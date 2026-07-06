"""pygls transport for the InferSynth language server (BUILD_PLAN WP6).

This module is the *only* place that imports ``pygls``/``lsprotocol`` — it
is deliberately thin: every handler converts an LSP request into a call
into :mod:`infersynth.lsp.core` (which in turn calls straight into
:mod:`infersynth.lint`) and converts the plain-Python result back into a
wire type. No lint/synthesis logic lives here (UX.md: the LSP is a thin
adapter over the lint engine — squiggles, catalog-vocabulary completions,
idiom hovers, disambiguation code actions, no-primitive quick-fix).

``pygls`` is an optional dependency (extra ``lsp``); this module is only
imported from :func:`create_server`, so importing :mod:`infersynth.lsp`
itself never requires it to be installed.
"""

from __future__ import annotations

from pathlib import Path

from lsprotocol import types
from pygls.server import LanguageServer

from infersynth.lint.diagnostics import Diagnostic as IsDiagnostic
from infersynth.lint.vocab import Vocabulary
from infersynth.lsp import core

__all__ = ["create_server"]

_SEVERITY_MAP = {
    1: types.DiagnosticSeverity.Error,
    2: types.DiagnosticSeverity.Warning,
    3: types.DiagnosticSeverity.Information,
    4: types.DiagnosticSeverity.Hint,
}


def _to_lsp_diagnostic(diag: IsDiagnostic) -> types.Diagnostic:
    d = diag.to_lsp()
    rng = d["range"]
    return types.Diagnostic(
        range=types.Range(
            start=types.Position(**rng["start"]),
            end=types.Position(**rng["end"]),
        ),
        message=d["message"],
        severity=_SEVERITY_MAP[d["severity"]],
        code=d["code"],
        source=d["source"],
        data=d.get("data"),
    )


def _to_lsp_completion(item: core.CompletionItem) -> types.CompletionItem:
    kind = types.CompletionItemKind.Variable if item.is_param else types.CompletionItemKind.Keyword
    return types.CompletionItem(
        label=item.label,
        kind=kind,
        detail=item.detail,
        documentation=types.MarkupContent(kind=types.MarkupKind.Markdown, value=item.documentation),
    )


def _to_lsp_hover(hover: core.Hover) -> types.Hover:
    return types.Hover(
        contents=types.MarkupContent(kind=types.MarkupKind.Markdown, value=hover.contents)
    )


def _to_lsp_code_action(uri: str, action: core.CodeAction) -> types.CodeAction:
    text_edits = [
        types.TextEdit(
            range=types.Range(
                start=types.Position(edit.line, edit.character),
                end=types.Position(edit.line, edit.character),
            ),
            new_text=edit.new_text,
        )
        for edit in action.edits
    ]
    return types.CodeAction(
        title=action.title,
        kind=types.CodeActionKind.QuickFix,
        edit=types.WorkspaceEdit(changes={uri: text_edits}),
    )


_ALLOCATE_COMMAND = "infersynth.allocateSubtree"


def _to_lsp_allocation_action(action: core.AllocationCommand) -> types.CodeAction:
    """An :class:`~infersynth.lsp.core.AllocationCommand` as a *command*-shaped
    ``CodeAction`` (see ``core.py``'s "allocation code action" docstring for
    why this isn't a same-document ``workspace/applyEdit`` like the others)."""
    return types.CodeAction(
        title=action.title,
        kind=types.CodeActionKind.QuickFix,
        command=types.Command(
            title=action.title,
            command=_ALLOCATE_COMMAND,
            arguments=[action.spec_path, action.req_id, action.library],
        ),
    )


def _whole_document_range(text: str) -> types.Range:
    lines = text.splitlines()
    last = max(len(lines) - 1, 0)
    last_len = len(lines[-1]) if lines else 0
    return types.Range(start=types.Position(0, 0), end=types.Position(last, last_len))


def create_server(
    catalog_dir: str | Path | None = None, spec_path: str | Path | None = None
) -> LanguageServer:
    """Build the pygls server. *catalog_dir* enables catalog-vocabulary
    diagnostics, completions, hover, and disambiguation code actions;
    without it the server still runs (EARS-grammar diagnostics only).

    *spec_path* (WP-L1) enables the "Allocate subtree to library" code
    action: one per catalog library, on any requirement with an explicit id
    at the cursor. Requires *catalog_dir* too (the action's libraries come
    from the catalog the server was started with); without *spec_path* the
    action is absent.
    """
    server = LanguageServer("infersynth-lsp", "v1")
    vocab: Vocabulary | None = None
    libraries: tuple[str, ...] = ()
    if catalog_dir is not None:
        from infersynth.catalog import Catalog

        catalog = Catalog.load(catalog_dir)
        vocab = Vocabulary.from_catalog(catalog)
        libraries = core.catalog_libraries(catalog)

    spec_path_str = str(spec_path) if spec_path is not None else None

    def _publish(ls: LanguageServer, uri: str, text: str) -> None:
        _, diagnostics = core.diagnostics_for_text(text, uri, catalog_dir)
        ls.publish_diagnostics(uri, [_to_lsp_diagnostic(d) for d in diagnostics])

    @server.feature(types.TEXT_DOCUMENT_DID_OPEN)
    def did_open(ls: LanguageServer, params: types.DidOpenTextDocumentParams) -> None:
        _publish(ls, params.text_document.uri, params.text_document.text)

    @server.feature(types.TEXT_DOCUMENT_DID_CHANGE)
    def did_change(ls: LanguageServer, params: types.DidChangeTextDocumentParams) -> None:
        doc = ls.workspace.get_text_document(params.text_document.uri)
        _publish(ls, params.text_document.uri, doc.source)

    @server.feature(
        types.TEXT_DOCUMENT_COMPLETION,
        types.CompletionOptions(trigger_characters=list("abcdefghijklmnopqrstuvwxyz")),
    )
    def completions(ls: LanguageServer, params: types.CompletionParams) -> types.CompletionList:
        if vocab is None:
            return types.CompletionList(is_incomplete=False, items=[])
        items = [_to_lsp_completion(i) for i in core.completion_items(vocab)]
        return types.CompletionList(is_incomplete=False, items=items)

    @server.feature(types.TEXT_DOCUMENT_HOVER)
    def hover(ls: LanguageServer, params: types.HoverParams) -> types.Hover | None:
        if vocab is None:
            return None
        doc = ls.workspace.get_text_document(params.text_document.uri)
        word = core.word_at_position(doc.source, params.position.line, params.position.character)
        result = core.hover_for_word(word, vocab)
        return _to_lsp_hover(result) if result is not None else None

    @server.feature(types.TEXT_DOCUMENT_CODE_ACTION)
    def code_action(
        ls: LanguageServer, params: types.CodeActionParams
    ) -> list[types.CodeAction]:
        doc = ls.workspace.get_text_document(params.text_document.uri)
        uri = params.text_document.uri
        reqset, diagnostics = core.diagnostics_for_text(doc.source, uri, catalog_dir)
        actions = core.code_actions_for_diagnostics(diagnostics, vocab)
        lsp_actions = [_to_lsp_code_action(uri, a) for a in actions]

        if spec_path_str is not None and libraries:
            req = core.requirement_at_position(reqset, params.range.start.line)
            alloc_actions = core.allocation_actions_for_requirement(
                req, libraries, spec_path_str
            )
            lsp_actions += [_to_lsp_allocation_action(a) for a in alloc_actions]

        return lsp_actions

    @server.command(_ALLOCATE_COMMAND)
    def allocate_subtree(ls: LanguageServer, args: list) -> None:
        """Handler for the "Allocate subtree to library" code action.

        Pragmatic v0 choice (documented per the WP-L1 brief): if the spec
        file is currently an open document in this session, send a
        ``workspace/applyEdit`` (so the client's own undo stack / dirty-file
        UI applies, same as any other editor edit); otherwise write the file
        to disk directly (there is no open buffer to edit through the
        protocol, and the spec is not the document the request came from).
        """
        spec_file, req_id, library = args
        spec_uri = Path(spec_file).resolve().as_uri()
        # ``ls.workspace`` raises before ``initialize`` has run (no live
        # client in a unit test / a server just started with no session
        # yet) — that is "not open", same as any other unopened document.
        try:
            is_open = spec_uri in ls.workspace.text_documents
        except RuntimeError:
            is_open = False
        if is_open:
            doc = ls.workspace.get_text_document(spec_uri)
            new_text = core.apply_allocation(doc.source, req_id, library)
            edit = types.WorkspaceEdit(
                changes={
                    spec_uri: [
                        types.TextEdit(
                            range=_whole_document_range(doc.source), new_text=new_text
                        )
                    ]
                }
            )
            ls.apply_edit(edit, label="Allocate subtree to library")
        else:
            path = Path(spec_file)
            current = path.read_text(encoding="utf-8") if path.exists() else ""
            new_text = core.apply_allocation(current, req_id, library)
            path.write_text(new_text, encoding="utf-8")

    return server


def run_stdio(catalog_dir: str | Path | None = None, spec_path: str | Path | None = None) -> None:
    """Run the server over stdio (the ``infersynth lsp`` CLI transport)."""
    create_server(catalog_dir, spec_path).start_io()
