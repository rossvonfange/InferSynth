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


def create_server(catalog_dir: str | Path | None = None) -> LanguageServer:
    """Build the pygls server. *catalog_dir* enables catalog-vocabulary
    diagnostics, completions, hover, and disambiguation code actions;
    without it the server still runs (EARS-grammar diagnostics only)."""
    server = LanguageServer("infersynth-lsp", "v1")
    vocab: Vocabulary | None = None
    if catalog_dir is not None:
        from infersynth.catalog import Catalog

        vocab = Vocabulary.from_catalog(Catalog.load(catalog_dir))

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
        _, diagnostics = core.diagnostics_for_text(doc.source, uri, catalog_dir)
        actions = core.code_actions_for_diagnostics(diagnostics, vocab)
        return [_to_lsp_code_action(params.text_document.uri, a) for a in actions]

    return server


def run_stdio(catalog_dir: str | Path | None = None) -> None:
    """Run the server over stdio (the ``infersynth lsp`` CLI transport)."""
    create_server(catalog_dir).start_io()
