"""LSP allocation code-action tests (BUILD_PLAN WP-L1 item 3/4).

Pure ``lsp/core.py`` logic (no pygls) plus one pygls-transport code-action
listing test in the style of ``tests/test_lsp_server.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from infersynth.catalog import Catalog
from infersynth.lsp import core
from infersynth.match.allocation import allocations_from_spec, resolve_scope

REPO_CATALOG = Path(__file__).parent.parent / "catalog"

FRD_WITH_EXPLICIT_ID = (
    "- SYS.1 The board shall include a non-inverting amplifier gain stage with gain of 4.\n"
    "- The board shall include decoupling.\n"  # generated id, no explicit id
)


def _catalog():
    return Catalog.load(REPO_CATALOG)


# --- catalog_libraries --------------------------------------------------------


def test_catalog_libraries_sorted():
    libs = core.catalog_libraries(_catalog())
    assert libs == ("core",)


# --- requirement_at_position --------------------------------------------------


def test_requirement_at_position_finds_explicit_id():
    reqset, _ = core.diagnostics_for_text(FRD_WITH_EXPLICIT_ID, "file:///buf.md", REPO_CATALOG)
    req = core.requirement_at_position(reqset, 0)
    assert req is not None
    assert req.id == "SYS.1"


def test_requirement_at_position_none_for_generated_id():
    reqset, _ = core.diagnostics_for_text(FRD_WITH_EXPLICIT_ID, "file:///buf.md", REPO_CATALOG)
    req = core.requirement_at_position(reqset, 1)
    assert req is None


def test_requirement_at_position_out_of_range():
    reqset, _ = core.diagnostics_for_text(FRD_WITH_EXPLICIT_ID, "file:///buf.md", REPO_CATALOG)
    assert core.requirement_at_position(reqset, 99) is None


# --- allocation_actions_for_requirement ---------------------------------------


def test_allocation_actions_one_per_library():
    reqset, _ = core.diagnostics_for_text(FRD_WITH_EXPLICIT_ID, "file:///buf.md", REPO_CATALOG)
    req = core.requirement_at_position(reqset, 0)
    actions = core.allocation_actions_for_requirement(req, ("core", "sensing"), "spec.yaml")
    assert [a.title for a in actions] == [
        "Allocate subtree to library: core",
        "Allocate subtree to library: sensing",
    ]
    assert all(a.req_id == "SYS.1" for a in actions)
    assert all(a.spec_path == "spec.yaml" for a in actions)


def test_allocation_actions_empty_without_requirement():
    assert core.allocation_actions_for_requirement(None, ("core",), "spec.yaml") == []


def test_allocation_actions_empty_without_libraries():
    reqset, _ = core.diagnostics_for_text(FRD_WITH_EXPLICIT_ID, "file:///buf.md", REPO_CATALOG)
    req = core.requirement_at_position(reqset, 0)
    assert core.allocation_actions_for_requirement(req, (), "spec.yaml") == []


# --- apply_allocation ----------------------------------------------------------


class TestApplyAllocation:
    def test_creates_fresh_spec_text(self):
        text = core.apply_allocation("", "SYS.1", "core")
        data = yaml.safe_load(text)
        assert data == {"allocations": [{"at": "SYS.1", "allow": ["core"], "deny": []}]}

    def test_appends_new_entry_to_existing_allocations(self):
        existing = yaml.safe_dump({"allocations": [{"at": "SYS.2", "allow": ["core"]}]})
        text = core.apply_allocation(existing, "SYS.1", "sensing")
        data = yaml.safe_load(text)
        by_at = {e["at"]: e for e in data["allocations"]}
        assert set(by_at) == {"SYS.1", "SYS.2"}
        assert by_at["SYS.1"]["allow"] == ["sensing"]

    def test_merges_into_existing_entry_same_req_id(self):
        existing = yaml.safe_dump({"allocations": [{"at": "SYS.1", "allow": ["core"]}]})
        text = core.apply_allocation(existing, "SYS.1", "sensing")
        data = yaml.safe_load(text)
        assert len(data["allocations"]) == 1  # merged, not duplicated
        assert data["allocations"][0]["allow"] == ["core", "sensing"]

    def test_idempotent_on_duplicate_apply(self):
        once = core.apply_allocation("", "SYS.1", "core")
        twice = core.apply_allocation(once, "SYS.1", "core")
        assert yaml.safe_load(once) == yaml.safe_load(twice)

    def test_result_valid_against_allocations_from_spec_no_duplicate_at_error(self):
        text = core.apply_allocation("", "SYS.1", "core")
        text = core.apply_allocation(text, "SYS.1", "sensing")  # same req id, different lib
        data = yaml.safe_load(text)
        table = allocations_from_spec(data)  # must not raise (no duplicate `at` entries)
        assert table.get("SYS.1").allow == ("core", "sensing")

    def test_preserves_other_top_level_keys(self):
        existing = yaml.safe_dump({"frd": "board.md", "profile": "prototype"})
        text = core.apply_allocation(existing, "SYS.1", "core")
        data = yaml.safe_load(text)
        assert data["frd"] == "board.md"
        assert data["profile"] == "prototype"
        assert data["allocations"] == [{"at": "SYS.1", "allow": ["core"], "deny": []}]

    def test_round_trips_through_matcher_scoping(self):
        text = core.apply_allocation("", "SYS.1", "core")
        table = allocations_from_spec(yaml.safe_load(text))

        from infersynth.lint.model import Requirement

        req = Requirement(id="SYS.1", text="stub")
        scope = resolve_scope(req, table, frozenset({"core", "sensing"}))
        assert scope.has_allocation
        assert scope.admits("core")
        assert not scope.admits("sensing")

    def test_not_a_mapping_raises(self):
        with pytest.raises(ValueError):
            core.apply_allocation("- just\n- a\n- list\n", "SYS.1", "core")


# --- pygls transport: code-action listing (style of test_lsp_server.py) -------

pytest.importorskip("pygls")

import lsprotocol.types as types  # noqa: E402
from pygls.workspace import Workspace  # noqa: E402

from infersynth.lsp.server import create_server  # noqa: E402


def _handler(server, method):
    return server.lsp.fm.features[method]


def _open_document(server, uri: str, text: str) -> None:
    workspace = Workspace(root_uri=None)
    workspace.put_text_document(
        types.TextDocumentItem(uri=uri, language_id="markdown", version=1, text=text)
    )
    server.lsp._workspace = workspace


def test_code_action_lists_one_allocation_command_per_library(tmp_path):
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text("", encoding="utf-8")
    server = create_server(catalog_dir=REPO_CATALOG, spec_path=spec_path)
    _open_document(server, "file:///buf.md", FRD_WITH_EXPLICIT_ID)
    handler = _handler(server, types.TEXT_DOCUMENT_CODE_ACTION)
    params = types.CodeActionParams(
        text_document=types.TextDocumentIdentifier(uri="file:///buf.md"),
        range=types.Range(types.Position(0, 0), types.Position(0, 10)),
        context=types.CodeActionContext(diagnostics=[]),
    )
    actions = handler(params)
    titles = {a.title for a in actions}
    assert "Allocate subtree to library: core" in titles
    alloc_action = next(a for a in actions if a.title == "Allocate subtree to library: core")
    assert alloc_action.command.command == "infersynth.allocateSubtree"
    assert alloc_action.command.arguments == [str(spec_path), "SYS.1", "core"]


def test_code_action_absent_without_spec_path():
    server = create_server(catalog_dir=REPO_CATALOG, spec_path=None)
    _open_document(server, "file:///buf.md", FRD_WITH_EXPLICIT_ID)
    handler = _handler(server, types.TEXT_DOCUMENT_CODE_ACTION)
    params = types.CodeActionParams(
        text_document=types.TextDocumentIdentifier(uri="file:///buf.md"),
        range=types.Range(types.Position(0, 0), types.Position(0, 10)),
        context=types.CodeActionContext(diagnostics=[]),
    )
    actions = handler(params)
    assert not any("Allocate subtree" in a.title for a in actions)


def test_command_handler_writes_spec_file_directly_when_not_open(tmp_path):
    spec_path = tmp_path / "spec.yaml"
    server = create_server(catalog_dir=REPO_CATALOG, spec_path=spec_path)
    cmd_handler = server.lsp.fm.commands["infersynth.allocateSubtree"]
    cmd_handler([str(spec_path), "SYS.1", "core"])
    data = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    assert data["allocations"] == [{"at": "SYS.1", "allow": ["core"], "deny": []}]


def test_command_handler_applies_workspace_edit_when_spec_open(tmp_path, monkeypatch):
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text("", encoding="utf-8")
    server = create_server(catalog_dir=REPO_CATALOG, spec_path=spec_path)
    spec_uri = spec_path.resolve().as_uri()
    _open_document(server, spec_uri, "")

    applied = {}

    def fake_apply_edit(edit, label=None):
        applied["edit"] = edit
        applied["label"] = label

    monkeypatch.setattr(server, "apply_edit", fake_apply_edit)
    cmd_handler = server.lsp.fm.commands["infersynth.allocateSubtree"]
    cmd_handler([str(spec_path), "SYS.1", "core"])

    assert not spec_path.read_text(encoding="utf-8")  # direct write NOT used
    assert spec_uri in applied["edit"].changes
    new_text = applied["edit"].changes[spec_uri][0].new_text
    data = yaml.safe_load(new_text)
    assert data["allocations"] == [{"at": "SYS.1", "allow": ["core"], "deny": []}]
