"""pygls transport tests (BUILD_PLAN WP6).

No editor/e2e integration: handlers are invoked directly (pygls registers
the *undecorated* function in ``server.lsp.fm.features[method]`` bound to
the server instance via ``functools.partial``, so ``handler(params)`` runs
the real handler body with no socket/stdio transport needed).
``publish_diagnostics`` is monkeypatched to capture what would be sent,
since the server has no connected client in this test process.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pygls")

import lsprotocol.types as types  # noqa: E402
from pygls.workspace import Workspace  # noqa: E402

from infersynth.lsp.server import create_server  # noqa: E402

REPO_CATALOG = Path(__file__).parent.parent / "catalog"


def _handler(server, method):
    return server.lsp.fm.features[method]


def _open_document(server, uri: str, text: str) -> None:
    """Seed the (otherwise uninitialized-outside-a-real-session) workspace
    with one document, the way the real ``did_open``/``did_change`` handlers
    would via the client's notifications."""
    workspace = Workspace(root_uri=None)
    workspace.put_text_document(
        types.TextDocumentItem(uri=uri, language_id="markdown", version=1, text=text)
    )
    server.lsp._workspace = workspace


def test_did_open_publishes_diagnostics(monkeypatch):
    server = create_server(catalog_dir=REPO_CATALOG)
    published = {}

    def fake_publish(uri, diagnostics=None, **kwargs):
        published["uri"] = uri
        published["diagnostics"] = diagnostics

    monkeypatch.setattr(server, "publish_diagnostics", fake_publish)
    handler = _handler(server, types.TEXT_DOCUMENT_DID_OPEN)

    text = (
        "- The board shall use a non-inverting amplifier with gain of 2000\n"
        "- The unit shall report position at least hourly\n"
    )
    params = types.DidOpenTextDocumentParams(
        text_document=types.TextDocumentItem(
            uri="file:///buf.md", language_id="markdown", version=1, text=text
        )
    )
    handler(params)

    assert published["uri"] == "file:///buf.md"
    codes = {d.code for d in published["diagnostics"]}
    assert "frd.param-out-of-range" in codes
    assert "frd.no-primitive" in codes


def test_completion_handler_returns_catalog_vocabulary():
    server = create_server(catalog_dir=REPO_CATALOG)
    handler = _handler(server, types.TEXT_DOCUMENT_COMPLETION)
    params = types.CompletionParams(
        text_document=types.TextDocumentIdentifier(uri="file:///buf.md"),
        position=types.Position(0, 0),
    )
    result = handler(params)
    labels = {item.label for item in result.items}
    assert "non-inverting amplifier" in labels
    assert "gain" in labels


def test_hover_handler_includes_range():
    server = create_server(catalog_dir=REPO_CATALOG)
    doc_text = "a non-inverting amplifier with gain of 100\n"
    _open_document(server, "file:///buf.md", doc_text)
    handler = _handler(server, types.TEXT_DOCUMENT_HOVER)
    gain_col = doc_text.index("gain")
    params = types.HoverParams(
        text_document=types.TextDocumentIdentifier(uri="file:///buf.md"),
        position=types.Position(0, gain_col + 1),
    )
    result = handler(params)
    assert result is not None
    assert "1.0..1000.0" in result.contents.value


def test_code_action_handler_nonempty_for_ambiguous_case(tmp_path):
    from tests.test_lsp_core import write_cell

    write_cell(tmp_path, "can-transceiver", ["can transceiver"])
    write_cell(tmp_path, "isolated-can-transceiver", ["isolated can transceiver"])
    server = create_server(catalog_dir=tmp_path)
    doc_text = "- The board shall provide an isolated can transceiver\n"
    _open_document(server, "file:///buf.md", doc_text)
    handler = _handler(server, types.TEXT_DOCUMENT_CODE_ACTION)
    params = types.CodeActionParams(
        text_document=types.TextDocumentIdentifier(uri="file:///buf.md"),
        range=types.Range(types.Position(0, 0), types.Position(0, 10)),
        context=types.CodeActionContext(diagnostics=[]),
    )
    actions = handler(params)
    assert actions
    assert {a.title for a in actions} >= {
        "Specify can-transceiver",
        "Specify isolated-can-transceiver",
    }
