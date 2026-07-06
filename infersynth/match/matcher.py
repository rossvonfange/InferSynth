"""The WP-M1 matcher: a pure function of (requirements, catalog, allocations, knobs).

Pipeline (SELECTION §4 order, minus the WP-M2 semantic layer):

    idiom recall  ->  allocation scoping  ->  endpoint propagation
                  ->  chain scoring        ->  ensemble-variance triage

``match`` is deterministic: for a fixed ``(RequirementSet, Catalog, allocations,
knobs, endpoints, scorer)`` it returns byte-identical output (SELECTION §8) —
requirements iterate in document order, every candidate/chain list is sorted,
and nothing consults the wall clock or a random source. Unresolved residuals
never become guesses: they surface as typed diagnostics through the existing
lint ``Diagnostic`` codes (``frd.no-primitive`` / ``frd.allocation-empty`` /
``frd.unallocated`` / ``frd.ambiguous``) and, for ensemble underspecification,
a typed :class:`~infersynth.match.resolution.ResolutionRequest`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from infersynth.catalog import Catalog
from infersynth.lint.diagnostics import Diagnostic, Position, Range, Severity
from infersynth.lint.model import Requirement, RequirementSet
from infersynth.lint.vocab import Vocabulary
from infersynth.match.allocation import (
    NONE_LIBRARY,
    AllocationTable,
    resolve_scope,
)
from infersynth.match.knobs import MatchKnobs
from infersynth.match.propagate import EndpointSpec, propagate_chains
from infersynth.match.provenance import Candidate, CandidateChain
from infersynth.match.recall import idiom_recall
from infersynth.match.resolution import (
    ChainScorer,
    ResolutionRequest,
    StructuralScorer,
    ensemble_variance,
    make_resolution_request,
    score_chains,
)

__all__ = ["MatchResult", "match"]


@dataclass(frozen=True)
class MatchResult:
    """The matcher's output: candidates, chains, diagnostics, resolution requests.

    ``candidates`` / ``chains`` are keyed by requirement id (document order).
    ``diagnostics`` and ``resolution_requests`` are in emission order (which is
    itself document order), so the whole result is deterministic.
    """

    candidates: dict[str, tuple[Candidate, ...]] = field(default_factory=dict)
    chains: dict[str, tuple[CandidateChain, ...]] = field(default_factory=dict)
    diagnostics: tuple[Diagnostic, ...] = ()
    resolution_requests: tuple[ResolutionRequest, ...] = ()


def _req_range(req: Requirement) -> Range:
    if req.source is None:
        return Range(Position(0, 0), Position(0, 0))
    return Range(
        Position(req.source.line, req.source.col),
        Position(req.source.end_line, req.source.end_col),
    )


def _req_file(req: Requirement) -> str:
    return req.source.file if req.source else "<frd>"


def _diag(req: Requirement, severity: Severity, code: str, message: str) -> Diagnostic:
    return Diagnostic(
        file=_req_file(req),
        range=_req_range(req),
        severity=severity,
        code=code,
        message=f"{req.id}: {message}",
        source="infersynth-match",
    )


def _catalog_libraries(catalog: Catalog) -> frozenset[str]:
    return frozenset(
        (cell.library if cell.library else NONE_LIBRARY) for cell in catalog.cells.values()
    )


def match(
    reqset: RequirementSet,
    catalog: Catalog,
    allocations: AllocationTable | None = None,
    knobs: MatchKnobs | None = None,
    endpoints: dict[str, EndpointSpec] | None = None,
    scorer: ChainScorer | None = None,
) -> MatchResult:
    """Match a requirement set against a catalog (WP-M1).

    * ``allocations`` — an :class:`AllocationTable`
      (:func:`infersynth.match.allocation.allocations_from_spec` builds one
      from a spec's ``allocations:`` key). ``None`` ⇒ all libraries compete.
    * ``knobs`` — :class:`MatchKnobs`. ``recall="semantic"`` raises
      ``NotImplementedError`` (WP-M2). ``None`` ⇒ defaults.
    * ``endpoints`` — per-requirement :class:`EndpointSpec` for bidirectional
      propagation; a requirement absent from the mapping (or ``endpoints=None``)
      gets trivial single-cell chains.
    * ``scorer`` — a :class:`ChainScorer`; defaults to the structural proxy
      (no simulation). Pass ``SimGateScorer()`` for the WP-S1 sim-backed score.
    """
    knobs = knobs or MatchKnobs()
    if knobs.recall == "semantic":
        raise NotImplementedError(
            "recall='semantic' is the embeddings layer (WP-M2 — embeddings recall "
            "layer); WP-M1 ships idiom recall only. Use recall='strict'."
        )
    allocations = allocations or AllocationTable()
    endpoints = endpoints or {}
    scorer = scorer or StructuralScorer()

    vocab = Vocabulary.from_catalog(catalog)
    all_libraries = _catalog_libraries(catalog)

    candidates: dict[str, tuple[Candidate, ...]] = {}
    chains: dict[str, tuple[CandidateChain, ...]] = {}
    diagnostics: list[Diagnostic] = []
    requests: list[ResolutionRequest] = []

    for req in reqset.requirements():  # lintable reqs, document order
        scope = resolve_scope(req, allocations, all_libraries)
        in_scope, out_of_scope = idiom_recall(req, vocab, catalog, scope)
        candidates[req.id] = in_scope

        # --- residual diagnostics (never guesses) ---------------------------
        if not in_scope:
            if scope.empty:
                diagnostics.append(
                    _diag(
                        req,
                        Severity.WARNING,
                        "frd.allocation-empty",
                        "allocated library scope is empty — deny removed every "
                        "admissible library (SELECTION §2)",
                    )
                )
            elif out_of_scope:
                excluded = ", ".join(sorted(c.cell_key for c in out_of_scope))
                diagnostics.append(
                    _diag(
                        req,
                        Severity.WARNING,
                        "frd.allocation-empty",
                        "matching cells exist but all fall outside the allocated "
                        f"scope: {excluded} (SELECTION §2 scoped catalog-gap)",
                    )
                )
            else:
                diagnostics.append(
                    _diag(
                        req,
                        Severity.WARNING,
                        "frd.no-primitive",
                        "no catalog primitive matches this requirement (catalog-gap signal)",
                    )
                )
            chains[req.id] = ()
            continue

        # in-scope candidates exist but the requirement never got an allocation
        if knobs.allocation == "strict" and not scope.has_allocation:
            diagnostics.append(
                _diag(
                    req,
                    Severity.WARNING,
                    "frd.unallocated",
                    "requirement has candidates but no allocation in its ancestry "
                    "(allocation: strict profile — SELECTION §2)",
                )
            )

        # --- disambiguation primacy (SELECTION §4/§5) -----------------------
        # Cells declaring an ``idioms.disambiguation`` rule are never inferred
        # directly: when at least one rule-free candidate exists, rule-carrying
        # candidates are excluded from chain construction (they remain visible
        # in ``candidates`` as considered-but-not-inferable). Mirrors the lint
        # resolver's winner rule; without this, a downstream tie-break can
        # silently pick e.g. the inverting amplifier for a "non-inverting"
        # requirement — the exact silent-mis-inference class DESIGN §2 forbids.
        def _has_rule(c):
            cell = catalog.cells.get(c.cell_key)
            return bool(cell is not None and cell.disambiguation)

        rule_free = tuple(c for c in in_scope if not _has_rule(c))
        chainable = rule_free if rule_free else in_scope

        # --- bidirectional propagation -> closed chains ---------------------
        req_endpoints = endpoints.get(req.id)
        closed = propagate_chains(
            req.id, chainable, catalog, req_endpoints, knobs.expansion_budget
        )
        if not closed:
            # candidates surfaced but none bridge the declared endpoints
            diagnostics.append(
                _diag(
                    req,
                    Severity.WARNING,
                    "frd.no-primitive",
                    "in-scope candidates do not close a chain between the declared "
                    "input and output endpoints (RECON_HARVEST §3)",
                )
            )
            chains[req.id] = ()
            continue

        scored = score_chains(closed, catalog, scorer)
        chains[req.id] = scored

        # --- ensemble variance -> typed ResolutionRequest -------------------
        variance = ensemble_variance(scored)
        if len(scored) > 1 and variance > knobs.variance_threshold:
            request = make_resolution_request(
                req.id, _req_file(req), _req_range(req), scored, variance
            )
            requests.append(request)
            diagnostics.append(request.diagnostic)

    return MatchResult(
        candidates=candidates,
        chains=chains,
        diagnostics=tuple(diagnostics),
        resolution_requests=tuple(requests),
    )
