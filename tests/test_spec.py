"""``infersynth.spec`` loader tests (BUILD_PLAN WP-L1 item 1)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from infersynth.match.allocation import AllocationTable
from infersynth.match.knobs import MatchKnobs
from infersynth.spec import Spec, SpecError, load_spec


def _write(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "spec.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


class TestLoadSpecValid:
    def test_minimal_empty_spec(self, tmp_path):
        p = _write(tmp_path, {})
        spec = load_spec(p)
        assert isinstance(spec, Spec)
        assert spec.frd is None
        assert not spec.allocations
        assert spec.profile is None
        assert spec.endpoints == {}
        assert spec.knobs == MatchKnobs()
        assert spec.absorption is None

    def test_frd_resolved_relative_to_spec_dir(self, tmp_path):
        (tmp_path / "board.md").write_text("- The board shall include decoupling.\n")
        p = _write(tmp_path, {"frd": "board.md"})
        spec = load_spec(p)
        assert spec.frd == tmp_path / "board.md"

    def test_allocations_reuse_existing_parser(self, tmp_path):
        p = _write(
            tmp_path,
            {"allocations": [{"at": "SYS.1", "allow": ["core"], "deny": ["other"]}]},
        )
        spec = load_spec(p)
        assert isinstance(spec.allocations, AllocationTable)
        alloc = spec.allocations.get("SYS.1")
        assert alloc is not None
        assert alloc.allow == ("core",)
        assert alloc.deny == ("other",)

    def test_profile_named(self, tmp_path):
        p = _write(tmp_path, {"profile": "prototype"})
        assert load_spec(p).profile == "prototype"

    def test_profile_inline_weights(self, tmp_path):
        p = _write(tmp_path, {"profile": {"bom": 5, "area_mm2": 2}})
        assert load_spec(p).profile == {"bom": 5, "area_mm2": 2}

    def test_endpoints_parsed(self, tmp_path):
        p = _write(
            tmp_path,
            {"endpoints": {"SYS.1": {"inputs": ["electrical"], "outputs": ["digital"]}}},
        )
        spec = load_spec(p)
        ep = spec.endpoints["SYS.1"]
        assert ep.inputs == ("electrical",)
        assert ep.outputs == ("digital",)

    def test_knobs_recall_allocation(self, tmp_path):
        p = _write(tmp_path, {"knobs": {"recall": "strict", "allocation": "strict"}})
        spec = load_spec(p)
        assert spec.knobs.recall == "strict"
        assert spec.knobs.allocation == "strict"

    def test_knobs_absorption_accepted_but_not_wired(self, tmp_path):
        p = _write(tmp_path, {"knobs": {"absorption": "conservative"}})
        spec = load_spec(p)
        assert spec.absorption == "conservative"
        assert spec.knobs == MatchKnobs()  # absorption doesn't touch MatchKnobs


class TestLoadSpecInvalid:
    def test_missing_file(self, tmp_path):
        with pytest.raises(SpecError, match="not found"):
            load_spec(tmp_path / "nope.yaml")

    def test_unknown_top_level_key(self, tmp_path):
        p = _write(tmp_path, {"bogus": 1})
        with pytest.raises(SpecError, match="unknown top-level"):
            load_spec(p)

    def test_not_a_mapping(self, tmp_path):
        p = tmp_path / "spec.yaml"
        p.write_text("- just\n- a\n- list\n", encoding="utf-8")
        with pytest.raises(SpecError, match="mapping"):
            load_spec(p)

    def test_invalid_yaml(self, tmp_path):
        p = tmp_path / "spec.yaml"
        p.write_text("frd: [unterminated\n", encoding="utf-8")
        with pytest.raises(SpecError, match="invalid YAML"):
            load_spec(p)

    def test_bad_profile_type(self, tmp_path):
        p = _write(tmp_path, {"profile": 5})
        with pytest.raises(SpecError, match="profile"):
            load_spec(p)

    def test_bad_allocations_propagates_allocation_error(self, tmp_path):
        p = _write(tmp_path, {"allocations": [{"at": "SYS.1"}, {"at": "SYS.1"}]})
        with pytest.raises(SpecError, match="duplicate"):
            load_spec(p)

    def test_unknown_endpoint_key(self, tmp_path):
        p = _write(tmp_path, {"endpoints": {"SYS.1": {"bogus": []}}})
        with pytest.raises(SpecError, match="unknown key"):
            load_spec(p)

    def test_bad_knob_value(self, tmp_path):
        p = _write(tmp_path, {"knobs": {"recall": "bogus"}})
        with pytest.raises(SpecError):
            load_spec(p)

    def test_bad_absorption_value(self, tmp_path):
        p = _write(tmp_path, {"knobs": {"absorption": "bogus"}})
        with pytest.raises(SpecError, match="absorption"):
            load_spec(p)

    def test_unknown_knob_key(self, tmp_path):
        p = _write(tmp_path, {"knobs": {"bogus": 1}})
        with pytest.raises(SpecError, match="unknown key"):
            load_spec(p)
