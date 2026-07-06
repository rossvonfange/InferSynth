"""Recall — idiom (Layer 1) + semantic (Layer 2) candidate surfacing.

**Layer 1, idioms** (SELECTION.md §4, "idioms (strict)"): reuses the lint
engine's catalog-generated vocabulary (:class:`infersynth.lint.vocab.Vocabulary`)
and its ``resolve`` matcher: the matcher does not re-derive keyword matching,
it consumes lint's output. Every surfaced cell becomes a
:class:`~infersynth.match.provenance.Candidate` tagged ``surfaced_by: idiom``
(SELECTION §7) and is filtered by the requirement's resolved allocation scope
(SELECTION §2) — a candidate whose library is out of scope is never surfaced.

**Layer 2, semantic** (SELECTION §4, WP-M2): :func:`semantic_recall` cosine-
scores a requirement's embedding against a catalog's cached cell vectors
(:mod:`infersynth.match.embed`), surfacing candidates above a floor as
``surfaced_by: semantic(<score>)``. Per RECON_HARVEST §2's cheapest-first
ladder, the matcher only calls this for a requirement whose idiom recall came
up with zero in-scope candidates — it is never the first rung, only the
residual's. Embeddings SURFACE candidates; they never decide or resolve
anything (SELECTION §4).
"""

from __future__ import annotations

from infersynth.catalog import Catalog
from infersynth.lint.model import Requirement
from infersynth.lint.vocab import Vocabulary, resolve
from infersynth.match.allocation import ResolvedScope
from infersynth.match.embed import SemanticIndex, cosine_similarity
from infersynth.match.provenance import Candidate, surfaced_by_idiom, surfaced_by_semantic

__all__ = ["idiom_recall", "semantic_recall"]


def idiom_recall(
    req: Requirement,
    vocab: Vocabulary,
    catalog: Catalog,
    scope: ResolvedScope,
) -> tuple[tuple[Candidate, ...], tuple[Candidate, ...]]:
    """Surface idiom candidates for *req*, split by allocation scope.

    Returns ``(in_scope, out_of_scope)``: both are sorted candidate tuples.
    ``in_scope`` are the candidates the rest of the pipeline uses;
    ``out_of_scope`` are the ones idiom recall *would* have surfaced but the
    allocation scope excluded — retained so the matcher can tell an empty
    allocation scope (``frd.allocation-empty``) from a genuine catalog gap
    (``frd.no-primitive``).
    """
    in_scope: list[Candidate] = []
    out_of_scope: list[Candidate] = []
    for res in resolve(req, vocab):
        cell = catalog.cells.get(res.entry.cell_key)
        library = cell.library if cell is not None else None
        cand = Candidate(
            requirement_id=req.id,
            cell_key=res.entry.cell_key,
            surfaced_by=surfaced_by_idiom(),
            matched_keywords=res.matched_keywords,
            params=tuple(res.params),
        )
        if scope.admits(library):
            in_scope.append(cand)
        else:
            out_of_scope.append(cand)
    return tuple(sorted(in_scope)), tuple(sorted(out_of_scope))


def semantic_recall(
    req: Requirement,
    req_vector: list[float],
    index: SemanticIndex,
    scope: ResolvedScope,
    threshold: float,
) -> tuple[Candidate, ...]:
    """Surface semantic candidates for *req* from a prebuilt :class:`SemanticIndex`.

    Only cells within *scope* (SELECTION §2) are considered; a cosine score
    ``>= threshold`` surfaces the cell with ``surfaced_by: semantic(<score>)``
    (SELECTION §7 provenance). Callers (:mod:`infersynth.match.matcher`) are
    responsible for only invoking this on the idiom-recall residual (zero
    in-scope idiom candidates) per RECON_HARVEST §2's cheapest-first ladder —
    this function itself has no opinion on when it should run, only on what
    it surfaces once asked.
    """
    surfaced: list[Candidate] = []
    for cell_key, (vector, library) in index.vectors.items():
        if not scope.admits(library):
            continue
        score = cosine_similarity(req_vector, vector)
        if score >= threshold:
            surfaced.append(
                Candidate(
                    requirement_id=req.id,
                    cell_key=cell_key,
                    surfaced_by=surfaced_by_semantic(score),
                )
            )
    return tuple(sorted(surfaced))
