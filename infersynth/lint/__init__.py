"""FRD lint engine (DESIGN.md section 6, UX.md).

Mechanical resolution of requirements to catalog vocabulary with in-range
parameters. Two composed layers: EARS grammar (ears.py) and catalog-derived
semantics (vocab.py). All findings are LSP-shaped diagnostics
(diagnostics.py) so every surface — CLI, LSP, MCP — is a thin adapter.

Entry points: :func:`lint_requirement_set` (model in, diagnostics out),
:func:`lint_text` (in-memory markdown in, model + diagnostics out — the LSP's
entry point, since editor buffers are not necessarily saved) and
:func:`lint_path` (markdown or .reqif file in, model + diagnostics out).
"""

from __future__ import annotations

from pathlib import Path

from infersynth.lint.diagnostics import Diagnostic, Position, Range, Severity
from infersynth.lint.ears import classify, lint_ears
from infersynth.lint.model import Requirement, RequirementSet, SourceSpan
from infersynth.lint.parse_md import parse_frd_markdown, parse_frd_text
from infersynth.lint.vocab import Vocabulary, pick_winner, resolve

__all__ = [
    "Diagnostic",
    "Position",
    "Range",
    "Requirement",
    "RequirementSet",
    "Severity",
    "SourceSpan",
    "Vocabulary",
    "classify",
    "lint_path",
    "lint_requirement_set",
    "lint_text",
    "parse_frd_markdown",
    "parse_frd_text",
]


def _req_range(req: Requirement) -> Range:
    if req.source is None:
        return Range.on_line(0)
    return Range(
        Position(req.source.line, req.source.col),
        Position(req.source.end_line, req.source.end_col),
    )


def _catalog_gap_stub(req: Requirement) -> str:
    """Quickfix payload for frd.no-primitive: a catalog-gap submission stub
    (DESIGN section 6: the 'no primitive available' diagnostic feeds the
    catalog factory / community submission pipeline)."""
    phrase = " ".join(req.text.split())[:60]
    return (
        "# catalog-gap stub — file as a new cell package\n"
        "manifest:\n"
        "  name: <new-cell-name>\n"
        '  version: "0.1.0"\n'
        f"  description: covers requirement {req.id}: {phrase}\n"
        "idioms:\n"
        f'  keywords: ["<canonical idiom for: {phrase}>"]\n'
        "  params: {}\n"
    )


def _lint_vocab(req: Requirement, vocab: Vocabulary) -> list[Diagnostic]:
    file = req.source.file if req.source else "<frd>"
    rng = _req_range(req)
    matches = resolve(req, vocab)
    if not matches:
        return [
            Diagnostic(
                file=file,
                range=rng,
                severity=Severity.WARNING,
                code="frd.no-primitive",
                message=(
                    f"{req.id}: no catalog primitive matches this requirement "
                    "(catalog-gap signal)"
                ),
                quickfix=_catalog_gap_stub(req),
            )
        ]
    winner = pick_winner(matches)
    if winner is None:
        cells = ", ".join(m.entry.cell_key for m in matches)
        return [
            Diagnostic(
                file=file,
                range=rng,
                severity=Severity.ERROR,
                code="frd.ambiguous",
                message=(
                    f"{req.id}: {len(matches)} catalog cells match with no "
                    f"disambiguation winner: {cells}; ambiguity is rejected "
                    "at intake — name the intended idiom"
                ),
            )
        ]
    diags: list[Diagnostic] = []
    for binding in winner.params:
        if binding.problem is not None:
            diags.append(
                Diagnostic(
                    file=file,
                    range=rng,
                    severity=Severity.ERROR,
                    code="frd.param-out-of-range",
                    message=(
                        f"{req.id}: {winner.entry.cell_key} parameter "
                        f"{binding.name!r}: {binding.problem}"
                    ),
                )
            )
    return diags


def _lint_unknown_pragmas(req: Requirement) -> list[Diagnostic]:
    """WARN ``frd.unknown-pragma`` for each pragma-shaped-but-unrecognized bracket
    (NETFLOW.md closed vocabulary — sugar the compiler cannot honor)."""
    file = req.source.file if req.source else "<frd>"
    return [
        Diagnostic(
            file=file,
            range=_req_range(req),
            severity=Severity.WARNING,
            code="frd.unknown-pragma",
            message=(
                f"{req.id}: unrecognized pragma {p.raw!r} — not in the closed "
                "vocabulary (feeds / use / no-pack); left as prose (NETFLOW.md)"
            ),
        )
        for p in req.pragmas
        if p.kind == "unknown"
    ]


def lint_requirement_set(
    reqset: RequirementSet, vocab: Vocabulary | None = None
) -> list[Diagnostic]:
    """Lint a requirement tree: EARS grammar always; catalog semantics when a
    vocabulary is supplied. Grouping nodes are structural and never linted;
    rationale nodes only get INFO ``frd.rationale-ignored``."""
    diags: list[Diagnostic] = []
    for req in reqset:
        if req.is_group:
            continue
        diags.extend(_lint_unknown_pragmas(req))
        if req.rationale:
            file = req.source.file if req.source else "<frd>"
            diags.append(
                Diagnostic(
                    file=file,
                    range=_req_range(req),
                    severity=Severity.INFO,
                    code="frd.rationale-ignored",
                    message=f"{req.id}: rationale note, not a lintable requirement",
                )
            )
            continue
        diags.extend(lint_ears(req))
        if vocab is not None:
            diags.extend(_lint_vocab(req, vocab))
    return diags


def _load_vocab(catalog_dir: str | Path | None) -> Vocabulary | None:
    if catalog_dir is None:
        return None
    from infersynth.catalog import Catalog

    return Vocabulary.from_catalog(Catalog.load(catalog_dir))


def lint_text(
    text: str, file: str = "<frd>", catalog_dir: str | Path | None = None
) -> tuple[RequirementSet, list[Diagnostic]]:
    """Lint in-memory FRD markdown *text* (no file need be saved to disk).

    This is the LSP's entry point (UX.md: the language server is a thin
    adapter over this engine and must not require a saved buffer). Catalog
    semantics run only when *catalog_dir* is given; grammar-only lint
    otherwise.
    """
    reqset = parse_frd_text(text, file=file)
    vocab = _load_vocab(catalog_dir)
    return reqset, lint_requirement_set(reqset, vocab)


def lint_path(
    path: str | Path, catalog_dir: str | Path | None = None
) -> tuple[RequirementSet, list[Diagnostic]]:
    """Lint an FRD file: markdown by default, ReqIF for ``.reqif``/``.reqifz``
    (requires the optional ``reqif`` extra). Catalog semantics run only when
    *catalog_dir* is given; grammar-only lint otherwise."""
    p = Path(path)
    if p.suffix.lower() in (".reqif", ".reqifz"):
        from infersynth.lint.reqif_io import load_reqif

        reqset = load_reqif(p)
        vocab = _load_vocab(catalog_dir)
        return reqset, lint_requirement_set(reqset, vocab)
    return lint_text(p.read_text(encoding="utf-8"), file=str(p), catalog_dir=catalog_dir)
