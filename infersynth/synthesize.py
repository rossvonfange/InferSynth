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
from infersynth.decide.engine import Decision, decide
from infersynth.decide.lockfile import Lockfile
from infersynth.decide.profiles import WeightProfile, load_profile
from infersynth.gates.harness import HarnessError, harness_params
from infersynth.lint import lint_path
from infersynth.match import MatchResult, match
from infersynth.match.allocation import AllocationTable
from infersynth.match.knobs import MatchKnobs
from infersynth.match.propagate import EndpointSpec
from infersynth.spec import FeedEdge


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
) -> SynthesisResult:
    """Run the full pipeline and materialize the decision as a KiCad design.

    ``allocations``/``knobs``/``endpoints`` (WP-L1) thread straight into
    :func:`infersynth.match.match` — typically built from a formal spec via
    :func:`infersynth.spec.load_spec`; ``None`` for any of them keeps
    ``match``'s own defaults (no allocation scoping, lenient/strict-idiom
    knobs, trivial single-cell chains).

    ``pins`` (NETFLOW ``[use:]``) thread into :func:`infersynth.match.match` so
    a pinned requirement's cell becomes its winner. ``feeds`` (NETFLOW declared
    dataflow) are pass-through in this stage: carried onto the result and
    rendered under "Declared feeds (not yet wired)" — no wiring is emitted.
    """
    frd = Path(frd)
    out_dir = Path(out_dir)
    catalog = Catalog.load(catalog_dir)
    prof = load_profile(profile)

    reqset, _lint_diags = lint_path(frd, catalog_dir=catalog_dir)
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
    )
    result.report_path.write_text(_render_report(result, catalog), encoding="utf-8")
    return result


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
    lines += [
        "",
        "## Wiring worklist (inter-cell nets are not drawn by synthesis)",
        "",
        "Connect the instantiated sheets' ports (hierarchical labels) per your "
        "intent; full-hierarchy ERC will fail on connectivity until wired:",
        "",
    ]
    for inst in result.instantiated:
        cell = catalog.cells.get(inst.cell_key)
        ports = ", ".join(sorted(cell.ports)) if cell is not None and cell.ports else "?"
        lines.append(f"- `{inst.instname}` ({inst.cell_key}): {ports}")
    if result.feeds:
        lines += [
            "",
            "## Declared feeds (not yet wired)",
            "",
            "Requirement-level dataflow declared via NETFLOW `feeds` (spec or FRD "
            "`[feeds:]` pragma). Pass-through only at this stage — the wiring "
            "plan stage consumes these; synthesis does not draw them yet:",
            "",
        ]
        for edge in result.feeds:
            dst = f"{edge.dst}.{edge.dst_port}" if edge.dst_port else edge.dst
            lines.append(f"- `{edge.src}` → `{dst}`")
    lines.append("")
    return "\n".join(lines)
