"""Fixed-point synthesis driver (WP-F1): the loop over the one-shot flow.

RECON_HARVEST §5 (fixed-point control structure with an assisted second pass)
and §6 (typed ResolutionRequest / weighted-claim seam), bound by SELECTION §8's
repeatability contract. This module is the *driver*: it OWNS the loop and
ORCHESTRATES the existing stage functions (``lint_path`` / ``compile_pragmas``
→ :func:`infersynth.match.match` → :func:`infersynth.decide.engine.decide`, and
:func:`infersynth.synthesize.synthesize` as the one-shot materializer). It never
restructures those stages — :mod:`infersynth.synthesize` stays the one-shot
materializer it is (its only concession to this module is an additive
``exclude_cells`` filter argument).

Loop semantics (SELECTION §8 — REPEATABILITY BINDS)
---------------------------------------------------
A **round** is one pass of the deterministic decision flow: ``match`` (scoped by
the round's allocations, with excluded cells filtered out) → ``decide``. Between
rounds the driver applies only **deterministic, in-run refinements** — never
LLM/human/web input (those happen BETWEEN *runs*, via the spec artifacts this
module writes). Two honest refinements are implemented:

(a) **gate-failure constraint feedback.** After a round, every decided winner's
    cells are run through :func:`infersynth.gates.run.run_cell_gates` (the cell's
    own gates — harness ERC, golden-netlist equivalence, sim). A cell whose gates
    FAIL is a revocable claim beaten by contrary evidence (RECON_HARVEST §4/§6):
    it is EXCLUDED, and the affected requirement re-matches/re-decides next round
    (its runner-up cover wins). The exclusion + reason lands in the round ledger.

(b) **assisted second pass (the hint rule).** A requirement left
    ``undecided-no-candidates`` in round N *because an allocation scoped it to
    libraries that hold no matching cell* is helped in round N+1 by ADDING to its
    allocation ``allow`` list the libraries that produced decided winners for
    sibling requirements in round N. This is purely additive (it only extends an
    allow-list — it can never remove a candidate) and derived deterministically
    from round-N decided winners, so it can only *widen* the undecided
    requirement's scope and can never silently change an already-decided winner
    (enforced: hints touch only requirements that had no winner).

**Convergence** is a stable fixed point: the loop stops when a round's decided
winners *and* diagnostics are byte-identical to the previous round's (nothing
new to exclude, nothing new to hint), or when ``max_rounds`` is hit. Every
round's inputs/outputs are recorded in :attr:`PipelineResult.rounds`.

Resolution artifact round-trip (the between-runs escalation seam)
-----------------------------------------------------------------
:attr:`PipelineResult.pending_resolutions` collects every typed request the
deterministic rungs could not resolve — match :class:`ResolutionRequest`\\ s
(underspecified ensembles), :class:`WiringResolutionRequest`\\ s (ambiguous
nets), and undecided-requirement reasons — into one machine-readable
``resolutions_needed.json`` written beside the design. Each entry is::

    {
      "id":       "<stable id, e.g. 'match:R-3' / 'wiring:R-A->R-B' / 'undecided:R-7'>",
      "kind":     "underspecified-ensemble" | "no-candidates" |
                  "resolution-request" | "feeds-ambiguous" | "converge-ambiguous",
      "subject":  "<requirement id, or the net/edge the wiring ambiguity is on>",
      "options":  ["<competing option rendered as a string>", ...],
      "spec_edit":"<the durable SPEC EDIT that resolves it next run>"
    }

The whole file is::

    {
      "schema":      "infersynth.pipeline.resolutions/v0",
      "converged":   <bool>,
      "rounds":      <int>,
      "all_decided": <bool>,
      "resolutions": [ <entry>, ... ]
    }

A human or LLM edits the spec per ``spec_edit`` and re-runs; the next run consumes
the durable artifact deterministically (SELECTION §8). This is the ONLY
escalation channel — the loop itself never asks a model or a human anything.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from infersynth.catalog import Catalog
from infersynth.catalog.loader import CellPackage
from infersynth.decide.engine import (
    UNDECIDED_NO_CANDIDATES,
    Decision,
    decide,
)
from infersynth.decide.lockfile import Lockfile
from infersynth.decide.profiles import WeightProfile, load_profile
from infersynth.gates.run import run_cell_gates
from infersynth.gates.runner import GateReport
from infersynth.lint import lint_path
from infersynth.lint.pragmas import compile_pragmas
from infersynth.match import MatchResult, match
from infersynth.match.allocation import NONE_LIBRARY, Allocation, AllocationTable
from infersynth.match.knobs import MatchKnobs
from infersynth.match.propagate import EndpointSpec
from infersynth.spec import FeedEdge, Spec
from infersynth.synthesize import SynthesisResult, synthesize

__all__ = [
    "filter_match_excluding",
    "GateExclusion",
    "HintApplication",
    "RoundLedger",
    "PendingResolution",
    "PipelineResult",
    "run_pipeline",
]


# --------------------------------------------------------------------------
# match-result filtering (shared with synthesize's additive exclude_cells)
# --------------------------------------------------------------------------
def filter_match_excluding(mres: MatchResult, excluded: frozenset[str]) -> MatchResult:
    """Return a copy of *mres* with every chain/candidate naming an excluded cell
    dropped. Deterministic (preserves order); the identity when *excluded* empty.

    This is the mechanism behind gate-failure feedback: an excluded cell can no
    longer surface as (part of) a winning cover, so ``decide`` re-picks the
    requirement's runner-up. Shared with :func:`infersynth.synthesize.synthesize`
    so the final materialization reproduces the loop's converged decision.
    """
    if not excluded:
        return mres
    chains = {
        rid: tuple(c for c in chs if not (set(c.cells) & excluded))
        for rid, chs in mres.chains.items()
    }
    candidates = {
        rid: tuple(c for c in cands if c.cell_key not in excluded)
        for rid, cands in mres.candidates.items()
    }
    return MatchResult(
        candidates=candidates,
        chains=chains,
        diagnostics=mres.diagnostics,
        resolution_requests=mres.resolution_requests,
    )


# --------------------------------------------------------------------------
# ledger + result value types
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class GateExclusion:
    """A decided winner's cell that FAILED its own gates and was revoked."""

    cell_key: str
    requirement_id: str
    reason: str


@dataclass(frozen=True)
class HintApplication:
    """An assisted-second-pass hint applied to an undecided requirement."""

    requirement_id: str
    added_libraries: tuple[str, ...]
    rule: str
    spec_edit: str


@dataclass(frozen=True)
class RoundLedger:
    """The inputs and outputs of one fixed-point round."""

    index: int  # 1-based
    #: cells excluded ENTERING this round (accumulated gate-failure feedback)
    excluded_before: tuple[str, ...]
    #: requirement id -> winning chain's cell keys
    winners: dict[str, tuple[str, ...]]
    #: requirement id -> status, for every requirement without a winner
    undecided: dict[str, str]
    #: match diagnostics (code + message), in emission order
    diagnostics: tuple[str, ...]
    #: gate-failure exclusions DISCOVERED this round (applied next round)
    gate_exclusions: tuple[GateExclusion, ...] = ()
    #: hints DERIVED this round (applied next round)
    hints_applied: tuple[HintApplication, ...] = ()

    def signature(self) -> tuple:
        """The convergence signature: decided winners + undecided + diagnostics.

        Deliberately excludes the round index and the between-round refinements
        (exclusions/hints) — a fixed point is *stable output*, not stable
        bookkeeping (SELECTION §8: convergence = stable decided winners).
        """
        return (
            tuple(sorted(self.winners.items())),
            tuple(sorted(self.undecided.items())),
            self.diagnostics,
        )


@dataclass(frozen=True)
class PendingResolution:
    """One typed request the deterministic rungs could not resolve in-run."""

    id: str
    kind: str
    subject: str
    options: tuple[str, ...]
    spec_edit: str

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "kind": self.kind,
            "subject": self.subject,
            "options": list(self.options),
            "spec_edit": self.spec_edit,
        }


@dataclass(frozen=True)
class PipelineResult:
    """The fixed-point driver's output over a whole run."""

    rounds: tuple[RoundLedger, ...]
    pending_resolutions: tuple[PendingResolution, ...]
    synthesis: SynthesisResult | None
    converged: bool
    resolutions_path: Path | None = None
    excluded_cells: tuple[str, ...] = ()
    hint_allocations: AllocationTable | None = None

    @property
    def all_decided(self) -> bool:
        return self.synthesis is not None and self.synthesis.all_decided

    @property
    def final_round(self) -> RoundLedger | None:
        return self.rounds[-1] if self.rounds else None

    def resolutions_document(self) -> dict[str, object]:
        """The machine-readable ``resolutions_needed.json`` payload."""
        return {
            "schema": "infersynth.pipeline.resolutions/v0",
            "converged": self.converged,
            "rounds": len(self.rounds),
            "all_decided": self.all_decided,
            "resolutions": [r.to_dict() for r in self.pending_resolutions],
        }


# --------------------------------------------------------------------------
# one round: match (scoped, excluded) -> decide
# --------------------------------------------------------------------------
def _match_decide(
    reqset,
    catalog: Catalog,
    profile: WeightProfile,
    *,
    lockfile: Lockfile | None,
    allocations: AllocationTable,
    knobs: MatchKnobs | None,
    endpoints: dict[str, EndpointSpec] | None,
    pins: dict[str, str] | None,
    excluded: frozenset[str],
) -> tuple[MatchResult, Decision]:
    mres = match(
        reqset,
        catalog,
        allocations=allocations,
        knobs=knobs,
        endpoints=endpoints,
        pins=pins,
    )
    mres = filter_match_excluding(mres, excluded)
    decision = decide(mres, catalog, profile, lockfile=lockfile)
    return mres, decision


def _winners_of(decision: Decision) -> dict[str, tuple[str, ...]]:
    return {
        rid: outcome.winner.chain.cells
        for rid, outcome in decision.outcomes.items()
        if outcome.winner is not None
    }


def _diag_lines(mres: MatchResult) -> tuple[str, ...]:
    return tuple(f"{d.code}: {d.message}" for d in mres.diagnostics)


# --------------------------------------------------------------------------
# (a) gate-failure feedback
# --------------------------------------------------------------------------
def _verify_winners(
    decision: Decision,
    catalog: Catalog,
    already_excluded: frozenset[str],
    gate_cache: dict[str, GateReport],
    cell_gate_runner: Callable[[CellPackage], GateReport],
) -> tuple[GateExclusion, ...]:
    """Run each decided winner cell's own gates; return cells to EXCLUDE.

    Deterministic (SELECTION §8: ``run_cell_gates`` reads no clock/RNG). Results
    are memoized per cell key across rounds — a cell's gates depend only on its
    own package, which never changes during a run, so this is both a speed-up and
    a correctness guarantee (identical verdict every round).
    """
    exclusions: list[GateExclusion] = []
    seen: set[str] = set()
    for rid in sorted(decision.outcomes):
        outcome = decision.outcomes[rid]
        if outcome.winner is None:
            continue
        for cell_key in outcome.winner.chain.cells:
            if cell_key in already_excluded or cell_key in seen:
                continue
            seen.add(cell_key)
            cell = catalog.cells.get(cell_key)
            if cell is None:
                continue
            report = gate_cache.get(cell_key)
            if report is None:
                try:
                    report = cell_gate_runner(cell)
                except Exception as exc:  # noqa: BLE001 — a crashing gate is a failure
                    report = None  # type: ignore[assignment]
                    exclusions.append(
                        GateExclusion(cell_key, rid, f"cell gates crashed: {exc}")
                    )
                    continue
                gate_cache[cell_key] = report
            if not report.ok:
                exclusions.append(
                    GateExclusion(cell_key, rid, _first_gate_failure(report))
                )
    return tuple(exclusions)


def _first_gate_failure(report: GateReport) -> str:
    for r in report.results:
        if r.status.value == "fail":
            detail = "; ".join(r.diagnostics) if r.diagnostics else "(no detail)"
            return f"{r.gate}: {detail}"
    return "cell gates failed"  # pragma: no cover - report.ok already False


# --------------------------------------------------------------------------
# (b) assisted second pass: the hint rule
# --------------------------------------------------------------------------
def _winner_libraries(decision: Decision, catalog: Catalog) -> frozenset[str]:
    libs: set[str] = set()
    for outcome in decision.outcomes.values():
        if outcome.winner is None:
            continue
        for cell_key in outcome.winner.chain.cells:
            cell = catalog.cells.get(cell_key)
            if cell is not None:
                libs.add(cell.library if cell.library else NONE_LIBRARY)
    return frozenset(libs)


def _derive_hints(
    decision: Decision,
    catalog: Catalog,
    allocations: AllocationTable,
) -> tuple[AllocationTable, tuple[HintApplication, ...]]:
    """Assisted-second-pass hint (documented in the module docstring).

    For a requirement left ``undecided-no-candidates`` whose allocation ``allow``
    list excludes a sibling winner's library, ADD those libraries to the allow
    list. Purely additive; only touches requirements that had NO winner, so a
    decided winner can never change. Returns the augmented table + the records.
    """
    winner_libs = _winner_libraries(decision, catalog)
    if not winner_libs:
        return allocations, ()

    new_by_id = dict(allocations.by_id)
    records: list[HintApplication] = []
    for rid in sorted(decision.outcomes):
        outcome = decision.outcomes[rid]
        if outcome.status != UNDECIDED_NO_CANDIDATES:
            continue
        alloc = allocations.get(rid)
        # Only a *restrictive* allocation (an explicit allow list) is wideable —
        # no allocation already means "all libraries compete", so a no-candidates
        # there is a genuine catalog gap the hint cannot fill.
        if alloc is None or alloc.allow is None:
            continue
        current = set(alloc.allow)
        added = tuple(sorted(winner_libs - current))
        if not added:
            continue
        new_allow = tuple(alloc.allow) + added
        new_by_id[rid] = Allocation(at=rid, allow=new_allow, deny=alloc.deny)
        records.append(
            HintApplication(
                requirement_id=rid,
                added_libraries=added,
                rule="sibling-winner-libraries",
                spec_edit=(
                    f"allocations: extend `allow` of `{rid}` with "
                    f"{list(added)} (or remove the allocation to let all "
                    f"libraries compete)"
                ),
            )
        )
    if not records:
        return allocations, ()
    return AllocationTable(by_id=new_by_id), tuple(records)


# --------------------------------------------------------------------------
# pending resolutions (the between-runs escalation seam)
# --------------------------------------------------------------------------
def _collect_pending(
    mres: MatchResult,
    decision: Decision,
    synthesis: SynthesisResult | None,
) -> tuple[PendingResolution, ...]:
    """Fold every unresolved typed request into one ordered, deduped list."""
    pending: list[PendingResolution] = []
    covered: set[str] = set()

    # 1. match ResolutionRequests — underspecified ensembles (weighted claims).
    for rr in mres.resolution_requests:
        opts = tuple(
            f"[{'->'.join(o.cells)}] score={o.score:.3f}" for o in rr.options
        )
        pending.append(
            PendingResolution(
                id=f"match:{rr.requirement_id}",
                kind=rr.kind,
                subject=rr.requirement_id,
                options=opts,
                spec_edit=(
                    f"tighten `{rr.requirement_id}` (add an allocation "
                    f"`[at: {rr.requirement_id}, allow: [...]]` or a `[use: <cell>]` "
                    f"pin) so its ensemble variance {rr.variance:.4f} drops below "
                    f"the threshold"
                ),
            )
        )
        covered.add(rr.requirement_id)

    # 2. undecided requirements not already covered by a match request.
    for rid, status in sorted(decision.undecided().items()):
        if rid in covered:
            continue
        if status == UNDECIDED_NO_CANDIDATES:
            spec_edit = (
                f"add a cell/template covering `{rid}` (catalog gap), widen or "
                f"remove its allocation, or pin `[use: <cell>]`"
            )
        else:  # undecided-resolution-request handled above; defensive fallback
            spec_edit = f"resolve `{rid}` between runs (see selection_trace.json)"
        pending.append(
            PendingResolution(
                id=f"undecided:{rid}",
                kind="no-candidates" if status == UNDECIDED_NO_CANDIDATES else status,
                subject=rid,
                options=(),
                spec_edit=spec_edit,
            )
        )
        covered.add(rid)

    # 3. wiring ResolutionRequests — ambiguous nets (ask, never guess).
    if synthesis is not None and synthesis.wiring_plan is not None:
        for wr in synthesis.wiring_plan.resolution_requests:
            opts = tuple(f"{o.source} -> {o.sink}" for o in wr.options)
            pending.append(
                PendingResolution(
                    id=f"wiring:{wr.edge}",
                    kind=wr.kind,
                    subject=wr.edge,
                    options=opts,
                    spec_edit=(
                        f"declare the intended net for `{wr.edge}` with a "
                        f"`feeds: {{src, dst}}` entry in the spec"
                    ),
                )
            )
    return tuple(pending)


# --------------------------------------------------------------------------
# the driver
# --------------------------------------------------------------------------
def run_pipeline(
    frd: str | Path,
    catalog_dir: str | Path,
    out_dir: str | Path,
    *,
    spec: Spec | None = None,
    profile: str | dict | WeightProfile | None = None,
    max_rounds: int = 3,
    name: str | None = None,
    lockfile: Lockfile | None = None,
    allocations: AllocationTable | None = None,
    knobs: MatchKnobs | None = None,
    endpoints: dict[str, EndpointSpec] | None = None,
    pins: dict[str, str] | None = None,
    feeds: tuple[FeedEdge, ...] = (),
    rail_aliases: dict[str, str] | None = None,
    write_trace: bool = True,
    write_resolutions: bool = True,
    cell_gate_runner: Callable[[CellPackage], GateReport] | None = None,
) -> PipelineResult:
    """Run the fixed-point loop and materialize the converged decision.

    ``spec`` (a loaded :class:`~infersynth.spec.Spec`) seeds allocations / knobs /
    endpoints / profile / pins / feeds / rail_aliases; any explicit keyword
    overrides the spec's value (mirroring the CLI's ``--frd``/``--profile``
    precedence). The loop runs ``match`` → ``decide`` per round (deterministic),
    applies gate-failure feedback + the assisted-second-pass hint between rounds,
    stops at a stable fixed point or ``max_rounds``, then calls ``synthesize``
    ONCE (the one-shot materializer) with the converged excluded-cells + hint
    allocations so the emitted design is exactly the converged decision.
    """
    if max_rounds < 1:
        raise ValueError("max_rounds must be >= 1")
    if cell_gate_runner is None:
        # resolved at call time (module global) so tests can monkeypatch
        # infersynth.pipeline.run_cell_gates for speed.
        cell_gate_runner = run_cell_gates
    frd = Path(frd)
    out_dir = Path(out_dir)
    catalog = Catalog.load(catalog_dir)

    # spec-vs-explicit resolution (explicit wins).
    if profile is None:
        profile = (spec.profile if spec is not None and spec.profile is not None else "production")
    prof = load_profile(profile)
    base_allocations = allocations if allocations is not None else (
        spec.allocations if spec is not None else AllocationTable()
    )
    if knobs is None and spec is not None:
        knobs = spec.knobs
    if endpoints is None and spec is not None:
        endpoints = spec.endpoints
    if pins is None and spec is not None:
        pins = spec.pins
    if not feeds and spec is not None:
        feeds = spec.feeds
    if rail_aliases is None and spec is not None and spec.rail_aliases:
        rail_aliases = dict(spec.rail_aliases)

    # reqset + FRD [feeds:] pragmas are constant across rounds — compute once.
    reqset, _lint_diags = lint_path(frd, catalog_dir=catalog_dir)
    pragma_feeds = compile_pragmas(reqset).spec.feeds
    feeds = tuple(dict.fromkeys((*feeds, *pragma_feeds)))

    excluded: set[str] = set()
    round_allocations = base_allocations
    gate_cache: dict[str, GateReport] = {}
    rounds: list[RoundLedger] = []
    prev_sig: tuple | None = None
    converged = False
    last_mres: MatchResult | None = None
    last_decision: Decision | None = None

    for n in range(1, max_rounds + 1):
        excluded_frozen = frozenset(excluded)
        mres, decision = _match_decide(
            reqset,
            catalog,
            prof,
            lockfile=lockfile,
            allocations=round_allocations,
            knobs=knobs,
            endpoints=endpoints,
            pins=pins,
            excluded=excluded_frozen,
        )
        last_mres, last_decision = mres, decision

        gate_exclusions = _verify_winners(
            decision, catalog, excluded_frozen, gate_cache, cell_gate_runner
        )
        next_allocations, hints = _derive_hints(decision, catalog, round_allocations)

        ledger = RoundLedger(
            index=n,
            excluded_before=tuple(sorted(excluded_frozen)),
            winners=_winners_of(decision),
            undecided=decision.undecided(),
            diagnostics=_diag_lines(mres),
            gate_exclusions=gate_exclusions,
            hints_applied=hints,
        )
        rounds.append(ledger)

        sig = ledger.signature()
        if prev_sig is not None and sig == prev_sig:
            converged = True
            break
        prev_sig = sig

        # apply the deterministic between-round refinements for the next round.
        excluded.update(ge.cell_key for ge in gate_exclusions)
        round_allocations = next_allocations

    # Materialize the converged decision ONCE (the one-shot flow), honoring the
    # loop's excluded cells + hint allocations so the emitted design == the
    # converged decision. synthesize re-derives match/decide from these same
    # inputs (deterministic), so its SynthesisResult mirrors the final round.
    synthesis = synthesize(
        frd,
        catalog_dir,
        out_dir,
        profile=profile,
        name=name,
        lockfile=lockfile,
        write_trace=write_trace,
        allocations=round_allocations,
        knobs=knobs,
        endpoints=endpoints,
        feeds=feeds,
        pins=pins,
        rail_aliases=rail_aliases,
        wiring=True,
        verify=True,
        exclude_cells=frozenset(excluded),
    )

    assert last_mres is not None and last_decision is not None
    pending = _collect_pending(last_mres, last_decision, synthesis)

    resolutions_path: Path | None = None
    result = PipelineResult(
        rounds=tuple(rounds),
        pending_resolutions=pending,
        synthesis=synthesis,
        converged=converged,
        excluded_cells=tuple(sorted(excluded)),
        hint_allocations=round_allocations if round_allocations is not base_allocations else None,
    )
    if write_resolutions:
        resolutions_path = out_dir / "resolutions_needed.json"
        resolutions_path.write_text(
            json.dumps(result.resolutions_document(), indent=2) + "\n", encoding="utf-8"
        )
        result = PipelineResult(
            rounds=result.rounds,
            pending_resolutions=result.pending_resolutions,
            synthesis=result.synthesis,
            converged=result.converged,
            resolutions_path=resolutions_path,
            excluded_cells=result.excluded_cells,
            hint_allocations=result.hint_allocations,
        )
    return result
