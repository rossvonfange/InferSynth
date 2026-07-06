"""The matcher: a pure function of (requirements, catalog, allocations, knobs).

Pipeline (SELECTION §4 order):

    idiom recall  ->  semantic recall (residual only, WP-M2)
                  ->  allocation scoping  ->  endpoint propagation
                  ->  chain scoring        ->  ensemble-variance triage

``match`` is deterministic: for a fixed ``(RequirementSet, Catalog, allocations,
knobs, endpoints, scorer, embedding_backend)`` it returns byte-identical output
(SELECTION §8) — requirements iterate in document order, every candidate/chain
list is sorted, and nothing consults the wall clock or a random source.
Unresolved residuals never become guesses: they surface as typed diagnostics
through the existing lint ``Diagnostic`` codes (``frd.no-primitive`` /
``frd.allocation-empty`` / ``frd.unallocated`` / ``frd.ambiguous`` /
``frd.embedding-stale``) and, for ensemble underspecification, a typed
:class:`~infersynth.match.resolution.ResolutionRequest`; a requirement that
receives semantic candidates discloses it via ``frd.semantic-recall`` (INFO,
SELECTION §7 disclosure).
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
from infersynth.match.embed import EmbeddingBackend, HashingBackend, build_semantic_index
from infersynth.match.knobs import MatchKnobs
from infersynth.match.propagate import EndpointSpec, propagate_chains
from infersynth.match.provenance import Candidate, CandidateChain
from infersynth.match.recall import idiom_recall, semantic_recall
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


def resolve_pin(cell_ref: str, catalog: Catalog) -> str | None:
    """Resolve a NETFLOW ``[use:]`` cell reference to a catalog cell key.

    Accepts (most specific first) a full key ``library/cell@version``, a
    version-less ``library/cell`` (highest version wins), a bare ``cell@version``
    or bare ``cell`` name. Returns the resolved catalog key, or ``None`` when the
    reference matches no cell (the matcher then raises ``frd.pin-unresolved``).
    """
    if cell_ref in catalog.cells:
        return cell_ref
    matches: list[str] = []
    for key, cell in catalog.cells.items():
        no_ver = key.rsplit("@", 1)[0]  # library/name
        if cell_ref in (no_ver, cell.name, cell.bare_key):
            matches.append(key)
    if not matches:
        return None
    return sorted(matches)[-1]  # deterministic: highest version string


def match(
    reqset: RequirementSet,
    catalog: Catalog,
    allocations: AllocationTable | None = None,
    knobs: MatchKnobs | None = None,
    endpoints: dict[str, EndpointSpec] | None = None,
    scorer: ChainScorer | None = None,
    embedding_backend: EmbeddingBackend | None = None,
    pins: dict[str, str] | None = None,
) -> MatchResult:
    """Match a requirement set against a catalog.

    * ``allocations`` — an :class:`AllocationTable`
      (:func:`infersynth.match.allocation.allocations_from_spec` builds one
      from a spec's ``allocations:`` key). ``None`` ⇒ all libraries compete.
    * ``knobs`` — :class:`MatchKnobs`. ``recall="semantic"`` additionally runs
      Layer-2 embeddings recall (WP-M2) on each requirement's idiom-recall
      residual. ``None`` ⇒ defaults (``recall="strict"``, idioms only).
    * ``endpoints`` — per-requirement :class:`EndpointSpec` for bidirectional
      propagation; a requirement absent from the mapping (or ``endpoints=None``)
      gets trivial single-cell chains.
    * ``scorer`` — a :class:`ChainScorer`; defaults to the structural proxy
      (no simulation). Pass ``SimGateScorer()`` for the WP-S1 sim-backed score.
    * ``embedding_backend`` — the :class:`~infersynth.match.embed.EmbeddingBackend`
      used to embed requirement text for semantic recall; ignored when
      ``knobs.recall != "semantic"``. Defaults to
      :class:`~infersynth.match.embed.HashingBackend`. **Must be the same
      backend/model the catalog's cached ``embedding.json`` vectors were
      generated with** — a mismatch is a hard, run-stopping ``ValueError``
      (SELECTION §4/§8: pinned model + pinned text is what makes semantic
      candidate sets reproducible; see
      :func:`infersynth.match.embed.build_semantic_index`).
    """
    knobs = knobs or MatchKnobs()
    allocations = allocations or AllocationTable()
    endpoints = endpoints or {}
    scorer = scorer or StructuralScorer()
    pins = pins or {}

    vocab = Vocabulary.from_catalog(catalog)
    all_libraries = _catalog_libraries(catalog)

    candidates: dict[str, tuple[Candidate, ...]] = {}
    chains: dict[str, tuple[CandidateChain, ...]] = {}
    diagnostics: list[Diagnostic] = []
    requests: list[ResolutionRequest] = []

    # --- Layer 2 setup (semantic recall is opt-in, off in "strict") ---------
    semantic_index = None
    backend: EmbeddingBackend | None = None
    if knobs.recall == "semantic":
        backend = embedding_backend or HashingBackend()
        semantic_index = build_semantic_index(catalog, backend)  # raises on model mismatch
        if semantic_index.stale:
            stale_list = ", ".join(f"{s.cell_key} ({s.reason})" for s in semantic_index.stale)
            diagnostics.append(
                Diagnostic(
                    file="<catalog>",
                    range=Range.on_line(0),
                    severity=Severity.WARNING,
                    code="frd.embedding-stale",
                    message=(
                        "stale embedding.json cache excluded from semantic recall "
                        f"(capability text changed since last embed; re-run "
                        f"`infersynth embed`): {stale_list}"
                    ),
                    source="infersynth-match",
                )
            )

    for req in reqset.requirements():  # lintable reqs, document order
        # --- NETFLOW [use:] pin (authoritative) -----------------------------
        # A pinned requirement bypasses idiom/semantic recall AND disambiguation
        # primacy AND endpoint propagation: the pinned cell becomes THE sole
        # candidate/chain (surfaced_by="pinned"), so the decision layer picks it
        # as winner regardless of what recall/primacy would otherwise select. An
        # unresolvable pin is a hard ERROR (never silently ignored).
        if req.id in pins:
            resolved = resolve_pin(pins[req.id], catalog)
            if resolved is None:
                diagnostics.append(
                    _diag(
                        req,
                        Severity.ERROR,
                        "frd.pin-unresolved",
                        f"[use: {pins[req.id]}] does not resolve to any catalog cell",
                    )
                )
                candidates[req.id] = ()
                chains[req.id] = ()
                continue
            pinned = Candidate(
                requirement_id=req.id, cell_key=resolved, surfaced_by="pinned"
            )
            candidates[req.id] = (pinned,)
            pinned_chain = CandidateChain.make(
                req.id, (resolved,), closed=True, surfaced_by=("pinned",)
            )
            chains[req.id] = score_chains((pinned_chain,), catalog, scorer)
            continue

        scope = resolve_scope(req, allocations, all_libraries)
        in_scope, out_of_scope = idiom_recall(req, vocab, catalog, scope)

        # --- Layer 2: semantic recall on the idiom residual only -----------
        # RECON_HARVEST §2 cheapest-first ladder: semantic recall fires only
        # when idiom recall (Layer 1) surfaced zero in-scope candidates.
        semantic_candidates: tuple[Candidate, ...] = ()
        if not in_scope and semantic_index is not None and backend is not None:
            req_vector = backend.embed([req.text])[0]
            semantic_candidates = semantic_recall(
                req, req_vector, semantic_index, scope, knobs.semantic_threshold
            )
            if semantic_candidates:
                in_scope = semantic_candidates
                evidence = ", ".join(f"{c.cell_key}={c.surfaced_by}" for c in semantic_candidates)
                diagnostics.append(
                    _diag(
                        req,
                        Severity.INFO,
                        "frd.semantic-recall",
                        f"idiom recall found nothing in scope; semantic recall surfaced "
                        f"{len(semantic_candidates)} candidate(s): {evidence} "
                        "(SELECTION §4 layer-2 disclosure)",
                    )
                )

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
