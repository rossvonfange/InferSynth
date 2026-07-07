"""End-to-end synthesis: lint → match → decide → instantiate → report.

Wires the existing stages into one pure pipeline (SELECTION §8 binds: no
clock, no randomness — everything is a function of the FRD, the catalog, the
profile, and the optional lockfile).

v0 scope (honest boundaries):
- Every DECIDED requirement's winning chain is instantiated into one KiCad
  design (one sheet per cell instance, ``IS.*``-stamped).
- Inter-cell nets are NOT drawn: connecting cell ports requires the formal
  spec's net intent (elaborate/WP-F1 territory) or the user's hand — the
  report lists every instantiated cell's ports as the wiring worklist.
- Parameters: extracted-from-FRD values win; else the cell's declared
  default; else the deterministic harness rule (first allowed / midpoint of
  range) with the assumption LOUDLY recorded per param in the report and
  result (disclosed, deterministic — never silent).
- Verification: the emitted hierarchy must survive a kicad-cli netlist
  export (parse proof). Full-design ERC is run and *reported*, not gated —
  an unwired multi-sheet design legitimately fails connectivity ERC until
  the user (or a later stage) wires it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import infersynth.decide.trace as decide_trace
from infersynth.bind import BindingError
from infersynth.catalog import Catalog, CellPackage
from infersynth.compile_kicad.emit import instantiate, new_design
from infersynth.compile_kicad.wiring import emit_wiring
from infersynth.decide.engine import Decision, decide
from infersynth.decide.lockfile import Lockfile
from infersynth.decide.profiles import WeightProfile, load_profile
from infersynth.gates.harness import HarnessError, harness_params
from infersynth.gates.runner import GateResult
from infersynth.lint import lint_path
from infersynth.match import MatchResult, match
from infersynth.match.allocation import AllocationTable
from infersynth.match.knobs import MatchKnobs
from infersynth.match.propagate import EndpointSpec
from infersynth.netflow.plan import WiringPlan, build_plan
from infersynth.spec import DesignTestbench, FeedEdge


@dataclass(frozen=True)
class InstantiatedCell:
    requirement_id: str
    cell_key: str
    instname: str
    sheet_path: str
    params: dict[str, object] = field(default_factory=dict)
    #: param name -> "extracted" | "default" | "assumed(<rule>)"
    param_sources: dict[str, str] = field(default_factory=dict)

    @property
    def assumed_params(self) -> list[str]:
        return sorted(
            p for p, src in self.param_sources.items() if src.startswith("assumed")
        )


@dataclass(frozen=True)
class SynthesisResult:
    root: Path
    out_dir: Path
    instantiated: tuple[InstantiatedCell, ...]
    skipped: tuple[tuple[str, str, str], ...]  # (requirement_id, cell_key, reason)
    decision: Decision
    match_result: MatchResult
    trace_path: Path | None
    report_path: Path
    #: NETFLOW declared dataflow edges, pass-through (rendered, not yet wired).
    feeds: tuple[FeedEdge, ...] = ()
    #: the inferred net set applied to the design (None when --no-wiring)
    wiring_plan: WiringPlan | None = None
    #: full-hierarchy ERC summary line when verify ran; None otherwise
    erc_summary: str | None = None
    #: design-simulation gate result when a testbench ran; None otherwise
    design_sim: GateResult | None = None

    @property
    def all_decided(self) -> bool:
        return self.decision.all_decided

    @property
    def assumed_param_count(self) -> int:
        return sum(len(i.assumed_params) for i in self.instantiated)


def _sanitize_instname(raw: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_]+", "_", raw).strip("_").lower()
    return name or "cell"


def _params_for(
    cell: CellPackage, extracted: dict[str, float]
) -> tuple[dict[str, object], dict[str, str]]:
    """Resolve one value per idiom param: extracted > default > harness rule."""
    schemas = cell.idioms.get("params") or {}
    values: dict[str, object] = {}
    sources: dict[str, str] = {}
    fallback: dict[str, object] | None = None
    for pname, schema in sorted(schemas.items()):
        if pname in extracted:
            values[pname] = extracted[pname]
            sources[pname] = "extracted"
        elif isinstance(schema, dict) and "default" in schema:
            values[pname] = schema["default"]
            sources[pname] = "default"
        else:
            if fallback is None:
                fallback = harness_params(cell)
            values[pname] = fallback[pname]
            has_allowed = isinstance(schema, dict) and schema.get("allowed")
            rule = "first-allowed" if has_allowed else "midpoint"
            sources[pname] = f"assumed({rule})"
    return values, sources


def _extracted_params(mres: MatchResult, requirement_id: str, cell_key: str) -> dict[str, float]:
    for cand in mres.candidates.get(requirement_id, ()):
        if cand.cell_key == cell_key:
            return {p.name: p.value for p in cand.params if p.problem is None}
    return {}


def synthesize(
    frd: str | Path,
    catalog_dir: str | Path,
    out_dir: str | Path,
    profile: str | dict | WeightProfile = "prototype",
    name: str | None = None,
    lockfile: Lockfile | None = None,
    write_trace: bool = True,
    allocations: AllocationTable | None = None,
    knobs: MatchKnobs | None = None,
    endpoints: dict[str, EndpointSpec] | None = None,
    pins: dict[str, str] | None = None,
    feeds: tuple[FeedEdge, ...] = (),
    wiring: bool = True,
    verify: bool = False,
    rail_aliases: dict[str, str] | None = None,
    rail_binds: dict[tuple[str, str], str] | None = None,
    testbench: DesignTestbench | None = None,
) -> SynthesisResult:
    """Run the full pipeline and materialize the decision as a KiCad design.

    ``allocations``/``knobs``/``endpoints`` (WP-L1) thread straight into
    :func:`infersynth.match.match` — typically built from a formal spec via
    :func:`infersynth.spec.load_spec`; ``None`` for any of them keeps
    ``match``'s own defaults (no allocation scoping, lenient/strict-idiom
    knobs, trivial single-cell chains).

    ``pins`` (NETFLOW ``[use:]``) thread into :func:`infersynth.match.match` so
    a pinned requirement's cell becomes its winner. ``feeds`` (NETFLOW declared
    dataflow) are consumed into the wiring plan: any FRD ``[feeds:]`` pragma is
    compiled and merged with the *feeds* argument, then resolved to port-level
    nets (:mod:`infersynth.netflow.feeds`) that join the emitted design.
    """
    frd = Path(frd)
    out_dir = Path(out_dir)
    catalog = Catalog.load(catalog_dir)
    prof = load_profile(profile)

    reqset, _lint_diags = lint_path(frd, catalog_dir=catalog_dir)
    # NETFLOW: FRD [feeds:] pragmas compile into declared feed edges, merged
    # (additively) with any feeds passed in explicitly (spec-file feeds).
    from infersynth.lint.pragmas import compile_pragmas

    pragma_feeds = compile_pragmas(reqset).spec.feeds
    feeds = tuple(dict.fromkeys((*feeds, *pragma_feeds)))
    mres = match(
        reqset, catalog, allocations=allocations, knobs=knobs, endpoints=endpoints, pins=pins
    )
    decision = decide(mres, catalog, prof, lockfile=lockfile)

    design_name = name or _sanitize_instname(frd.stem)
    root = new_design(out_dir, design_name)

    instantiated: list[InstantiatedCell] = []
    skipped: list[tuple[str, str, str]] = []
    used_names: set[str] = set()
    for rid, outcome in decision.outcomes.items():
        if outcome.winner is None:
            continue
        for cell_key in outcome.winner.chain.cells:
            cell = catalog.cells.get(cell_key)
            if cell is None:  # pragma: no cover - decision only names catalog cells
                skipped.append((rid, cell_key, "cell not found in catalog"))
                continue
            try:
                params, sources = _params_for(cell, _extracted_params(mres, rid, cell_key))
            except (HarnessError, BindingError) as exc:
                skipped.append((rid, cell_key, f"unresolvable params: {exc}"))
                continue
            base = _sanitize_instname(f"{rid}_{cell.name}")
            instname, n = base, 2
            while instname in used_names:
                instname, n = f"{base}_{n}", n + 1
            used_names.add(instname)
            try:
                sheet = instantiate(cell, params, instname, out_dir, root)
            except (BindingError, ValueError) as exc:
                skipped.append((rid, cell_key, f"instantiation failed: {exc}"))
                continue
            instantiated.append(
                InstantiatedCell(
                    requirement_id=rid,
                    cell_key=cell_key,
                    instname=instname,
                    sheet_path=str(sheet),
                    params=params,
                    param_sources=sources,
                )
            )

    # NETFLOW stage 1: infer the net set (rails always; intra-chain when a
    # winner chain has >1 cell) and emit it onto the design.
    wiring_plan: WiringPlan | None = None
    if wiring and instantiated:
        winner_chains = {
            rid: outcome.winner.chain.cells
            for rid, outcome in decision.outcomes.items()
            if outcome.winner is not None
        }
        wiring_plan = build_plan(
            instantiated,
            winner_chains,
            catalog,
            feeds=feeds,
            rail_aliases=rail_aliases,
            rail_binds=rail_binds,
        )
        emit_wiring(root, wiring_plan, instantiated, catalog)

    # Full-hierarchy ERC is REPORTED, never hard-gated here (NETFLOW build
    # order 5 / docs note): connectivity errors stop being expected only when
    # the plan is clean (no diagnostics, no unwired signal ports).
    erc_summary: str | None = None
    if verify and wiring_plan is not None:
        erc_summary = _erc_report(root, wiring_plan)

    # Design-simulation gate (SEED_PLAN §1 crit 3): compose + run the whole
    # design's behavioral chain when a testbench is provided. Loudly SKIPPED
    # (never a silent pass) when there is no testbench or the plan is not clean.
    design_sim: GateResult | None = None
    if verify and wiring_plan is not None:
        from infersynth.gates.design_simulation import design_simulation_gate

        interim = SynthesisResult(
            root=root,
            out_dir=out_dir,
            instantiated=tuple(instantiated),
            skipped=tuple(skipped),
            decision=decision,
            match_result=mres,
            trace_path=None,
            report_path=out_dir / "SYNTHESIS.md",
            feeds=tuple(feeds),
            wiring_plan=wiring_plan,
        )
        design_sim = design_simulation_gate(
            {"result": interim, "catalog": catalog, "testbench": testbench}
        )

    trace_path: Path | None = None
    if write_trace:
        trace = decide_trace.build(mres, catalog, decision)
        trace_path = out_dir / "selection_trace.json"
        decide_trace.write(trace, trace_path)

    result = SynthesisResult(
        root=root,
        out_dir=out_dir,
        instantiated=tuple(instantiated),
        skipped=tuple(skipped),
        decision=decision,
        match_result=mres,
        trace_path=trace_path,
        report_path=out_dir / "SYNTHESIS.md",
        feeds=tuple(feeds),
        wiring_plan=wiring_plan,
        erc_summary=erc_summary,
        design_sim=design_sim,
    )
    result.report_path.write_text(_render_report(result, catalog), encoding="utf-8")
    return result


def _erc_report(root: Path, plan: WiringPlan) -> str:
    """Run full-hierarchy ERC on *root* via the existing erc gate; summarize.

    Report-only (NETFLOW build order 5: do not hard-gate synthesize exit on ERC
    yet). When the plan is not clean (diagnostics / unwired signal ports), the
    residual connectivity errors are expected and loudly noted; a clean plan is
    where full-hierarchy ERC-zero becomes a real target.
    """
    from infersynth.gates.erc import erc_gate

    result = erc_gate({"schematic_path": root})
    header = result.diagnostics[0] if result.diagnostics else result.status.value
    verdict = (
        "clean plan (ERC-zero is a real target)"
        if plan.clean
        else "residual (unwired signal ports / diagnostics remain — errors expected)"
    )
    return f"[{result.status.value}] {header} — {verdict}"


def _render_report(result: SynthesisResult, catalog: Catalog) -> str:
    lines = [
        "# Synthesis report",
        "",
        f"Root schematic: `{result.root.name}` · profile: "
        f"`{result.decision.profile_name}` · lockfile: "
        f"`{result.decision.lockfile_identity or 'none'}`",
        "",
        "## Instantiated cells",
        "",
    ]
    if result.instantiated:
        lines += ["| requirement | cell | sheet | params |", "|---|---|---|---|"]
        for inst in result.instantiated:
            parts = []
            for pname in sorted(inst.params):
                src = inst.param_sources.get(pname, "?")
                mark = " ⚠ASSUMED" if src.startswith("assumed") else ""
                parts.append(f"{pname}={inst.params[pname]} ({src}{mark})")
            lines.append(
                f"| {inst.requirement_id} | {inst.cell_key} | {inst.instname} | "
                f"{'; '.join(parts) or '—'} |"
            )
    else:
        lines.append("(none — no requirement reached a decided winner)")
    if result.assumed_param_count:
        lines += [
            "",
            f"**⚠ {result.assumed_param_count} parameter value(s) were ASSUMED** "
            "(deterministic midpoint/first-allowed rule) because the FRD did not "
            "specify them and the cell declares no default. Refine the FRD (or a "
            "future formal spec) and re-run.",
        ]
    if result.skipped:
        lines += ["", "## Skipped", ""]
        lines += [f"- {rid} / {key}: {reason}" for rid, key, reason in result.skipped]
    und = result.decision.undecided()
    if und:
        lines += ["", "## Undecided requirements", ""]
        lines += [f"- **{rid}** — {status}" for rid, status in sorted(und.items())]
        lines += [
            "",
            "See `selection_trace.json` for diagnostics; unresolved gaps are "
            "catalog-gap / ambiguity signals to resolve between runs.",
        ]
    plan = result.wiring_plan
    if plan is None:
        lines += [
            "",
            "## Wiring worklist (wiring skipped: --no-wiring)",
            "",
            "Inter-cell nets were not drawn. Connect the instantiated sheets' "
            "ports (hierarchical labels) per your intent; full-hierarchy ERC "
            "will fail on connectivity until wired:",
            "",
        ]
        for inst in result.instantiated:
            cell = catalog.cells.get(inst.cell_key)
            ports = ", ".join(sorted(cell.ports)) if cell is not None and cell.ports else "?"
            lines.append(f"- `{inst.instname}` ({inst.cell_key}): {ports}")
        lines.append("")
        return "\n".join(lines)

    # --- NETFLOW stage 1: wired nets + diagnostics + residual worklist ---
    lines += ["", "## Wired nets (NETFLOW stage 1 — inferred)", ""]
    if plan.nets:
        lines += ["| net | kind | driven | members |", "|---|---|---|---|"]
        for net in plan.nets:
            members = ", ".join(f"{i}.{p}" for i, p in net.members)
            driven = "yes" if net.driven else "**NO (undriven)**"
            lines.append(f"| `{net.name}` | {net.kind} | {driven} | {members} |")
    else:
        lines.append("(none — no power ports and no multi-cell chains to wire)")

    if plan.diagnostics:
        lines += ["", "## Wiring diagnostics (ask-rather-than-guess)", ""]
        lines += [f"- {d}" for d in plan.diagnostics]

    lines += [
        "",
        "## Residual wiring worklist (unwired signal ports)",
        "",
    ]
    for inst in result.instantiated:
        cell = catalog.cells.get(inst.cell_key)
        ports = ", ".join(sorted(cell.ports)) if cell is not None and cell.ports else "?"
        lines.append(f"- `{inst.instname}` ({inst.cell_key}): {ports}")
    if result.feeds:
        resolved_set = set(plan.feeds_resolved)
        unresolved_map = {e: r for e, r in plan.feeds_unresolved}
        lines += [
            "",
            "## Declared feeds",
            "",
            "Requirement-level dataflow declared via NETFLOW `feeds` (spec or FRD "
            "`[feeds:]` pragma), resolved to port-level nets and drawn onto the "
            "design:",
            "",
            "| feed | status |",
            "|---|---|",
        ]
        for edge in result.feeds:
            dst = f"{edge.dst}:{edge.dst_port}" if edge.dst_port else edge.dst
            if edge in resolved_set:
                status = "**wired**"
            elif edge in unresolved_map:
                status = f"unresolved — {unresolved_map[edge]}"
            else:
                status = "unresolved"
            lines.append(f"| `{edge.src}` → `{dst}` | {status} |")

    if plan.resolution_requests:
        lines += [
            "",
            "## Wiring decisions needed (ask-rather-than-guess)",
            "",
            "The engine found more than one admissible assignment and refused to "
            "guess (NETFLOW.md). Resolve each between runs with a declared "
            "`feeds` entry:",
            "",
        ]
        for req in plan.resolution_requests:
            opts = "; ".join(f"{o.source} → {o.sink}" for o in req.options)
            lines.append(f"- **[{req.kind}] {req.edge}** — options: {opts}")

    if plan.unwired_signal_ports:
        lines += [
            "These signal ports were not uniquely inferable (independent "
            "single-cell winners, or ambiguous pairings) and remain for "
            "declared feeds / hand-wiring; their hierarchical labels stay "
            "orphaned in the child sheet until connected:",
            "",
        ]
        lines += [f"- `{inst}`.{port}" for inst, port in plan.unwired_signal_ports]
    else:
        lines.append("(none — every signal port is wired)")

    if result.erc_summary is not None:
        lines += [
            "",
            "## Full-hierarchy ERC (reported, not gated)",
            "",
            f"{result.erc_summary}",
        ]

    if result.design_sim is not None:
        ds = result.design_sim
        lines += [
            "",
            "## Design-level simulation (SEED_PLAN §1 crit 3)",
            "",
            f"[{ds.status.value.upper()}] {_GATE_DESIGN_SIM}: the whole design's "
            "behavioral chain, composed from each cell's model/behavior.py and "
            "wired by the plan's nets.",
            "",
        ]
        lines += [f"- {d}" for d in ds.diagnostics]
    lines.append("")
    return "\n".join(lines)


_GATE_DESIGN_SIM = "design-simulation"
