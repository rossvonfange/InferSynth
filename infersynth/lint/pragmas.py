"""Compile FRD pragmas into formal-spec entries (NETFLOW.md build step 2).

The FRD stays prose; the spec stays the formal artifact. This module is the
lint-side bridge: it walks a :class:`~infersynth.lint.model.RequirementSet`,
reads the :class:`~infersynth.lint.model.Pragma` objects the parser stripped off
each requirement line, and folds them into a :class:`~infersynth.spec.Spec`:

* ``feeds`` pragma  -> ``Spec.feeds``    (``FeedEdge(src=req, dst, dst_port)``)
* ``use`` pragma    -> ``Spec.pins``     (``{req_id: cell_ref}``)
* ``no-pack`` pragma -> ``Spec.forbid_pack`` (``frozenset[req_id]``)

Merge policy (NETFLOW "sugar compiled into spec entries"): explicit spec-file
entries and pragma-compiled entries merge. On conflict for the same requirement
id, **the spec-file entry wins** and a WARN ``frd.pragma-conflict`` is emitted
(only ``pins`` is single-valued per requirement, so only ``pins`` can conflict;
``feeds`` edges and ``forbid_pack`` membership union with no conflict).

Validation: every ``feeds`` destination requirement id must exist in the
requirement set, else an ERROR ``frd.feeds-unknown-target`` is emitted.

The documented spec signature is ``compile_pragmas(reqset, spec) -> Spec``; it is
extended here to return a :class:`CompileResult` (spec **plus** diagnostics),
because the merge/validation step raises WARN/ERROR diagnostics that the caller
must surface (there is nowhere else for them to go, and the tests assert on
them). ``result.spec`` is the compiled spec.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from infersynth.lint.diagnostics import Diagnostic, Position, Range, Severity
from infersynth.lint.model import Requirement, RequirementSet
from infersynth.spec import FeedEdge, Spec

__all__ = ["CompileResult", "compile_pragmas"]

_EMPTY_PATH = Path("<pragmas>")


@dataclass(frozen=True)
class CompileResult:
    """Output of :func:`compile_pragmas`: the merged spec plus its diagnostics."""

    spec: Spec
    diagnostics: list[Diagnostic] = field(default_factory=list)


def _range(req: Requirement | None) -> Range:
    if req is None or req.source is None:
        return Range(Position(0, 0), Position(0, 0))
    return Range(
        Position(req.source.line, req.source.col),
        Position(req.source.end_line, req.source.end_col),
    )


def _file(req: Requirement | None) -> str:
    return req.source.file if (req is not None and req.source) else "<frd>"


def _diag(req: Requirement | None, severity: Severity, code: str, message: str) -> Diagnostic:
    return Diagnostic(
        file=_file(req),
        range=_range(req),
        severity=severity,
        code=code,
        message=message,
        source="infersynth-pragma",
    )


def compile_pragmas(reqset: RequirementSet, spec: Spec | None = None) -> CompileResult:
    """Compile requirement pragmas into a :class:`Spec` (NETFLOW build step 2).

    ``spec`` supplies any explicit spec-file ``feeds``/``pins``/``forbid_pack``
    entries; ``None`` starts from an empty in-memory spec. Returns the merged
    spec (spec-file entries win conflicts) plus the WARN/ERROR diagnostics.
    """
    base = spec if spec is not None else Spec(path=_EMPTY_PATH)
    diagnostics: list[Diagnostic] = []

    # --- collect pragma-derived entries (document order, deterministic) -------
    pragma_feeds: list[FeedEdge] = []
    pragma_pins: dict[str, str] = {}
    pragma_forbid: set[str] = set()
    for req in reqset.requirements():
        for p in req.pragmas:
            if p.kind == "feeds" and p.target:
                pragma_feeds.append(FeedEdge(src=req.id, dst=p.target, dst_port=p.port))
            elif p.kind == "use" and p.cell_ref:
                # last [use:] on a line wins for that requirement (deterministic)
                pragma_pins[req.id] = p.cell_ref
            elif p.kind == "no-pack":
                pragma_forbid.add(req.id)

    # --- merge feeds (union, spec-file first; dedupe preserving order) --------
    merged_feeds: list[FeedEdge] = []
    for edge in (*base.feeds, *pragma_feeds):
        if edge not in merged_feeds:
            merged_feeds.append(edge)

    # --- merge pins (spec-file wins conflicts, WARN) --------------------------
    merged_pins: dict[str, str] = dict(base.pins)
    for req_id, cell_ref in pragma_pins.items():
        if req_id in base.pins and base.pins[req_id] != cell_ref:
            diagnostics.append(
                _diag(
                    reqset.by_id.get(req_id),
                    Severity.WARNING,
                    "frd.pragma-conflict",
                    f"{req_id}: [use: {cell_ref}] pragma conflicts with spec-file pin "
                    f"{base.pins[req_id]!r}; spec-file entry wins (NETFLOW merge policy)",
                )
            )
            continue
        merged_pins[req_id] = cell_ref

    # --- merge forbid_pack (union, no conflict possible) ----------------------
    merged_forbid = frozenset(base.forbid_pack) | pragma_forbid

    # --- validate feeds targets ----------------------------------------------
    for edge in merged_feeds:
        if edge.dst not in reqset.by_id:
            diagnostics.append(
                _diag(
                    reqset.by_id.get(edge.src),
                    Severity.ERROR,
                    "frd.feeds-unknown-target",
                    f"{edge.src}: feeds target {edge.dst!r} is not a known requirement id",
                )
            )

    merged = replace(
        base,
        feeds=tuple(merged_feeds),
        pins=merged_pins,
        forbid_pack=merged_forbid,
    )
    return CompileResult(spec=merged, diagnostics=diagnostics)
