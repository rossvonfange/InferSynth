"""Recall Layer 1 — idiom recall (SELECTION.md §4, "idioms (strict)").

Reuses the lint engine's catalog-generated vocabulary
(:class:`infersynth.lint.vocab.Vocabulary`) and its ``resolve`` matcher: the
matcher does not re-derive keyword matching, it consumes lint's output. Every
surfaced cell becomes a :class:`~infersynth.match.provenance.Candidate` tagged
``surfaced_by: idiom`` (SELECTION §7) and is filtered by the requirement's
resolved allocation scope (SELECTION §2) — a candidate whose library is out of
scope is never surfaced.

The semantic (embeddings) layer is WP-M2; it is intentionally absent here.
"""

from __future__ import annotations

from infersynth.catalog import Catalog
from infersynth.lint.model import Requirement
from infersynth.lint.vocab import Vocabulary, resolve
from infersynth.match.allocation import ResolvedScope
from infersynth.match.provenance import Candidate, surfaced_by_idiom

__all__ = ["idiom_recall"]


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
