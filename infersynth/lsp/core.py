"""Pure LSP feature logic (BUILD_PLAN WP6; UX.md "thin adapter").

Every function here is plain Python in, plain Python out (no ``pygls`` /
``lsprotocol`` types) so it is trivially unit-testable and so the pygls
transport (``server.py``) is the *only* place that speaks the wire protocol.
This module owns no lint/synthesis logic of its own — it calls straight
into :mod:`infersynth.lint` and re-shapes the results for editor features:
diagnostics, completions, hover, and code actions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from infersynth.lint import Diagnostic, RequirementSet, Severity, lint_text
from infersynth.lint.model import Requirement
from infersynth.lint.vocab import Vocabulary
from infersynth.match.allocation import NONE_LIBRARY

__all__ = [
    "CompletionItem",
    "Hover",
    "CodeAction",
    "TextInsert",
    "AllocationCommand",
    "diagnostics_for_text",
    "word_at_position",
    "completion_items",
    "hover_for_word",
    "code_actions_for_diagnostics",
    "catalog_libraries",
    "requirement_at_position",
    "allocation_actions_for_requirement",
    "apply_allocation",
]


# --- diagnostics --------------------------------------------------------------


def diagnostics_for_text(
    text: str, uri: str, catalog_dir: str | Path | None = None
) -> tuple[RequirementSet, list[Diagnostic]]:
    """Lint an in-memory buffer. Thin wrapper over :func:`infersynth.lint.lint_text`
    — kept here so ``server.py`` never imports ``infersynth.lint`` directly."""
    return lint_text(text, file=uri, catalog_dir=catalog_dir)


# --- word-at-cursor -------------------------------------------------------------

_WORD_CHAR_RE = re.compile(r"[\w-]")


def word_at_position(text: str, line: int, character: int) -> str:
    """The identifier-ish word touching *character* on *line* (zero-based).

    Word characters are ``[A-Za-z0-9_-]`` — wide enough to catch idiom
    param names like ``rg_ohms`` and hyphenated keyword tokens.
    """
    lines = text.splitlines()
    if line < 0 or line >= len(lines):
        return ""
    src = lines[line]
    start = min(max(character, 0), len(src))
    end = start
    while start > 0 and _WORD_CHAR_RE.match(src[start - 1]):
        start -= 1
    while end < len(src) and _WORD_CHAR_RE.match(src[end]):
        end += 1
    return src[start:end]


# --- completions ----------------------------------------------------------------


@dataclass(frozen=True)
class CompletionItem:
    """A completion candidate: catalog vocabulary keyword or idiom param name."""

    label: str
    detail: str  # owning cell "name@version"
    documentation: str
    is_param: bool


def _param_table(params: dict) -> str:
    if not params:
        return ""
    lines = ["", "| param | type | range | default |", "| --- | --- | --- | --- |"]
    for name in sorted(params):
        schema = params[name]
        ptype = schema.get("type", "")
        prange = schema.get("range")
        allowed = schema.get("allowed")
        rng = f"{prange[0]}..{prange[1]}" if prange else (str(allowed) if allowed else "")
        default = schema.get("default", "")
        lines.append(f"| {name} | {ptype} | {rng} | {default} |")
    return "\n".join(lines)


def _entry_documentation(entry) -> str:
    doc = entry.description or entry.cell_name
    table = _param_table(entry.params)
    if table:
        doc = f"{doc}\n{table}"
    return doc


def completion_items(vocab: Vocabulary) -> list[CompletionItem]:
    """Catalog-vocabulary completions (UX.md): idiom keywords + param names.

    One item per keyword phrase and one per idiom param, generated straight
    from the installed catalog (never hand-written).
    """
    items: list[CompletionItem] = []
    seen: set[tuple[str, bool]] = set()
    for entry in vocab.entries:
        doc = _entry_documentation(entry)
        for kw in entry.keywords:
            key = (kw, False)
            if key in seen:
                continue
            seen.add(key)
            items.append(
                CompletionItem(label=kw, detail=entry.cell_key, documentation=doc, is_param=False)
            )
        for pname in sorted(entry.params):
            key = (pname, True)
            if key in seen:
                continue
            seen.add(key)
            items.append(
                CompletionItem(label=pname, detail=entry.cell_key, documentation=doc, is_param=True)
            )
    return items


# --- hover ------------------------------------------------------------------


@dataclass(frozen=True)
class Hover:
    contents: str  # markdown


def _keyword_matches(word: str, entry) -> bool:
    w = word.lower()
    for kw in entry.keywords:
        kwl = kw.lower()
        if w == kwl or w in kwl.split():
            return True
    return False


def hover_for_word(word: str, vocab: Vocabulary) -> Hover | None:
    """Idiom hover (UX.md): cell description, param table, depth level.

    Matches when *word* is an idiom keyword token or an idiom param name.
    """
    if not word:
        return None
    wl = word.lower()
    sections: list[str] = []
    seen_cells: set[str] = set()
    for entry in vocab.entries:
        hit = wl in {p.lower() for p in entry.params} or _keyword_matches(word, entry)
        if not hit or entry.cell_key in seen_cells:
            continue
        seen_cells.add(entry.cell_key)
        header = f"**{entry.cell_key}** — {entry.description or entry.cell_name}"
        table = _param_table(entry.params)
        depth = f"\n\nDepth: {entry.depth_level}"
        sections.append(f"{header}{table}{depth}")
    if not sections:
        return None
    return Hover(contents="\n\n---\n\n".join(sections))


# --- code actions -------------------------------------------------------------


@dataclass(frozen=True)
class TextInsert:
    """A single-point text insertion (line/character are zero-based LSP positions)."""

    line: int
    character: int
    new_text: str


@dataclass(frozen=True)
class CodeAction:
    title: str
    edits: list[TextInsert]


_AMBIGUOUS_CELLS_RE = re.compile(r"winner:\s*(?P<cells>[^;]+);")


def _disambiguating_text(entry) -> str:
    """A short param assignment that steers ``vocab.resolve`` to *entry*
    when one exists (a singleton ``allowed`` value, e.g. ``channels: [4]``
    on the quad-pack cell); otherwise the entry's own primary keyword."""
    for pname in sorted(entry.params):
        allowed = entry.params[pname].get("allowed")
        if allowed and len(allowed) == 1:
            return f"{pname}={allowed[0]}"
    return entry.keywords[0] if entry.keywords else entry.cell_name


def _ambiguous_actions(diag: Diagnostic, vocab: Vocabulary) -> list[CodeAction]:
    m = _AMBIGUOUS_CELLS_RE.search(diag.message)
    if not m:
        return []
    cell_keys = [c.strip() for c in m.group("cells").split(",") if c.strip()]
    by_key = {e.cell_key: e for e in vocab.entries}
    actions = []
    end = diag.range.end
    for key in cell_keys:
        entry = by_key.get(key)
        if entry is None:
            continue
        insert = f" ({_disambiguating_text(entry)})"
        actions.append(
            CodeAction(
                title=f"Specify {entry.cell_name}",
                edits=[TextInsert(end.line, end.character, insert)],
            )
        )
    return actions


def _no_primitive_action(diag: Diagnostic) -> list[CodeAction]:
    if not diag.quickfix:
        return []
    end = diag.range.end
    stub_lines = "\n".join(f"  {ln}" for ln in diag.quickfix.splitlines())
    return [
        CodeAction(
            title="Insert catalog-gap stub",
            edits=[TextInsert(end.line, end.character, f"\n{stub_lines}\n")],
        )
    ]


def code_actions_for_diagnostics(
    diagnostics: list[Diagnostic], vocab: Vocabulary | None
) -> list[CodeAction]:
    """Code actions (UX.md): one 'Specify <cell>' per ``frd.ambiguous``
    candidate; one 'Insert catalog-gap stub' per ``frd.no-primitive``."""
    actions: list[CodeAction] = []
    for diag in diagnostics:
        if diag.severity not in (Severity.ERROR, Severity.WARNING):
            continue
        if diag.code == "frd.ambiguous" and vocab is not None:
            actions.extend(_ambiguous_actions(diag, vocab))
        elif diag.code == "frd.no-primitive":
            actions.extend(_no_primitive_action(diag))
    return actions


# --- allocation code action (WP-L1; SELECTION.md §2) -----------------------
#
# Allocation lives in the formal spec, never in FRD prose (SELECTION §2), so
# unlike the actions above this one's edit does not touch the FRD buffer at
# all — it targets a *different* file, the workspace's ``spec.yaml``. LSP
# code actions are shaped around edits to the document the request was made
# on, so this is deliberately a *command*-shaped action
# (``infersynth.allocateSubtree``) rather than a ``workspace/applyEdit`` built
# here: ``server.py`` decides, at invocation time, whether the spec file is
# currently an open document (send ``workspace/applyEdit``) or not (write it
# to disk directly) — see that module's docstring for the choice. Everything
# that can be pure lives here: which requirement the cursor resolved to,
# which libraries to offer, and the actual YAML edit.


@dataclass(frozen=True)
class AllocationCommand:
    """One 'Allocate subtree to library: <library>' action.

    Not a :class:`CodeAction` (whose edits are always same-document
    ``TextInsert``s) because this action's edit targets the spec file, a
    document distinct from the one the code-action request was made on.
    ``server.py`` turns this into an LSP ``CodeAction`` whose ``command`` is
    ``infersynth.allocateSubtree`` with ``arguments=[spec_path, req_id,
    library]``.
    """

    title: str
    req_id: str
    library: str
    spec_path: str


def catalog_libraries(catalog: Any) -> tuple[str, ...]:
    """Sorted library names present in *catalog* (mirrors
    ``infersynth.match.matcher._catalog_libraries``; kept as a small local
    copy rather than importing a private helper). A cell loaded outside any
    ``library.yaml`` counts as :data:`infersynth.match.allocation.NONE_LIBRARY`."""
    libs = {(cell.library if cell.library else NONE_LIBRARY) for cell in catalog.cells.values()}
    return tuple(sorted(libs))


def requirement_at_position(reqset: RequirementSet, line: int) -> Requirement | None:
    """The lintable, explicit-id requirement whose source span covers *line*
    (zero-based), or ``None``. Requirements with a generated ``R-<n>`` id
    (no explicit id in the FRD) never resolve here — SELECTION §2's
    allocation ``at:`` key names a requirement id the author actually wrote,
    not a synthesized one the matcher assigned."""
    for req in reqset.requirements():
        if not req.explicit_id or req.source is None:
            continue
        if req.source.line <= line <= req.source.end_line:
            return req
    return None


def allocation_actions_for_requirement(
    req: Requirement | None, libraries: tuple[str, ...], spec_path: str
) -> list[AllocationCommand]:
    """One 'Allocate subtree to library: <library>' action per catalog
    library, for *req* (SELECTION §2: "applied interactively — an LSP code
    action... during lint writes it into the spec"). Empty when there is no
    requirement at the cursor, it has no explicit id, or the catalog has no
    libraries."""
    if req is None or not req.explicit_id:
        return []
    return [
        AllocationCommand(
            title=f"Allocate subtree to library: {library}",
            req_id=req.id,
            library=library,
            spec_path=spec_path,
        )
        for library in libraries
    ]


def apply_allocation(spec_text: str, req_id: str, library: str) -> str:
    """Pure spec-text edit for the allocation code action (WP-L1).

    Parses *spec_text* as YAML (empty/whitespace-only text is treated as an
    empty mapping — this is how a fresh ``spec.yaml`` gets created), appends
    *library* to the ``allow`` list of the ``allocations:`` entry whose
    ``at`` equals *req_id*, creating that entry (``{at, allow: [library],
    deny: []}``) if none exists yet.

    Convention chosen where WP-L1's brief is silent ("idempotence on
    duplicate ``at`` = merge or error per ``allocations_from_spec``'s
    duplicate rule — pick and document"): **merge**, not error.
    ``allocations_from_spec`` rejects two separate list entries sharing an
    ``at`` (SELECTION §2 gives one requirement one allocation), so this
    function never produces a second entry for the same requirement — it
    extends the existing one's ``allow`` list (deduplicated) instead. Running
    the same action twice for the same ``(req_id, library)`` is therefore a
    no-op after the first application (true idempotence), and repeated
    invocations for *different* libraries on the same requirement accumulate
    into one entry rather than erroring.

    Round-trips through ``yaml.safe_load``/``yaml.safe_dump`` — comments and
    key ordering in a hand-edited spec file are not preserved. Documented
    v0 limitation; a format-preserving editor (e.g. ``ruamel.yaml``) is a
    drop-in upgrade later if that turns out to matter.
    """
    if spec_text.strip():
        data = yaml.safe_load(spec_text)
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ValueError("spec.yaml must be a mapping")
    else:
        data = {}

    allocations = list(data.get("allocations") or [])
    for entry in allocations:
        if isinstance(entry, dict) and entry.get("at") == req_id:
            allow = list(entry.get("allow") or [])
            if library not in allow:
                allow.append(library)
            entry["allow"] = allow
            entry.setdefault("deny", [])
            break
    else:
        allocations.append({"at": req_id, "allow": [library], "deny": []})

    data["allocations"] = allocations
    return yaml.safe_dump(data, sort_keys=False)
