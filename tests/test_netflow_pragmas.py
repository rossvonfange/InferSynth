"""NETFLOW build step 2: FRD pragma parsing, spec compilation, and threading.

Covers the closed-vocabulary pragmas ``[feeds:]`` / ``[use:]`` / ``[no-pack]``:
parsing + stripping + the unknown-pragma WARN rule (positive and prose-bracket
false-positive-avoidance), compilation into ``Spec`` (merge, conflict-wins-spec
+ WARN, feeds-unknown-target ERROR), the pinned-use end-to-end primacy-defeat
"killer test", no-pack threading into the packer, and spec YAML round-trip.
"""

from __future__ import annotations

from pathlib import Path

from infersynth.catalog import Catalog
from infersynth.decide.engine import decide
from infersynth.lint import lint_text, parse_frd_text
from infersynth.lint.diagnostics import Severity
from infersynth.lint.model import RequirementSet
from infersynth.lint.parse_md import extract_pragmas
from infersynth.lint.pragmas import compile_pragmas
from infersynth.match import match
from infersynth.pack import PackKnobs, pack
from infersynth.spec import FeedEdge, Spec, load_spec

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "catalog"
INVERTING = "core/opamp-gain-inverting@0.1.0"
NONINVERTING = "core/opamp-gain-noninverting@0.1.0"


# --------------------------------------------------------------------------
# 1. parsing
# --------------------------------------------------------------------------


class TestParsing:
    def test_feeds_pragma(self):
        text, pragmas = extract_pragmas("do a thing [feeds: SYS.2]")
        assert text == "do a thing"
        assert len(pragmas) == 1
        assert pragmas[0].kind == "feeds"
        assert pragmas[0].target == "SYS.2"
        assert pragmas[0].port is None

    def test_feeds_qualified_port(self):
        _, pragmas = extract_pragmas("x [feeds: SYS.2:IN2]")
        assert pragmas[0].target == "SYS.2"
        assert pragmas[0].port == "IN2"

    def test_use_pragma(self):
        text, pragmas = extract_pragmas("amplifier [use: core/opamp-gain-inverting]")
        assert text == "amplifier"
        assert pragmas[0].kind == "use"
        assert pragmas[0].cell_ref == "core/opamp-gain-inverting"

    def test_use_pragma_with_version(self):
        _, pragmas = extract_pragmas("x [use: core/opamp-gain-inverting@0.1.0]")
        assert pragmas[0].cell_ref == "core/opamp-gain-inverting@0.1.0"

    def test_no_pack_flag(self):
        text, pragmas = extract_pragmas("independent stage [no-pack]")
        assert text == "independent stage"
        assert pragmas[0].kind == "no-pack"

    def test_multiple_pragmas_on_one_line(self):
        text, pragmas = extract_pragmas(
            "amp [use: core/opamp-gain-inverting] [feeds: SYS.2] [no-pack]"
        )
        assert text == "amp"
        assert [p.kind for p in pragmas] == ["use", "feeds", "no-pack"]

    def test_pragmas_stripped_from_requirement_text(self):
        rs = parse_frd_text("- SYS.1 The amp SHALL do X. [feeds: SYS.2] [no-pack]")
        req = rs["SYS.1"]
        assert req.text == "The amp SHALL do X."
        assert {p.kind for p in req.pragmas} == {"feeds", "no-pack"}

    def test_d_tag_still_stripped_and_pragmas_coexist(self):
        # [D:…] rationale handling must survive unchanged, alongside a pragma.
        rs = parse_frd_text("- SYS.1 Amp gain 10. [D: author note] [no-pack]")
        req = rs["SYS.1"]
        assert req.text == "Amp gain 10."
        assert [c.text for c in req.children] == ["author note"]  # D-note child
        assert [p.kind for p in req.pragmas] == ["no-pack"]


class TestUnknownPragmaRule:
    def test_colon_bearing_unknown_head_warns(self):
        _, pragmas = extract_pragmas("x [frobnicate: yes]")
        assert pragmas[0].kind == "unknown"
        assert pragmas[0].raw == "[frobnicate: yes]"

    def test_known_head_wrong_arity_warns(self):
        # no-pack with an argument, and feeds/use bare = pragma-shaped but wrong.
        assert extract_pragmas("x [no-pack: y]")[1][0].kind == "unknown"
        assert extract_pragmas("x [feeds]")[1][0].kind == "unknown"

    def test_prose_bracket_is_not_a_pragma(self):
        # spaces => not pragma-shaped; must NOT false-positive.
        _, pragmas = extract_pragmas("the signal [see note above] must settle")
        assert pragmas == []

    def test_bare_single_word_prose_bracket_is_not_a_pragma(self):
        _, pragmas = extract_pragmas("gain is [optional] here")
        assert pragmas == []

    def test_unknown_pragma_emits_warn_diagnostic(self):
        _, diags = lint_text("- SYS.1 amp [frob: x]")
        warns = [d for d in diags if d.code == "frd.unknown-pragma"]
        assert len(warns) == 1
        assert warns[0].severity is Severity.WARNING

    def test_prose_bracket_emits_no_unknown_pragma(self):
        _, diags = lint_text("- SYS.1 amp [see note above]")
        assert [d for d in diags if d.code == "frd.unknown-pragma"] == []


# --------------------------------------------------------------------------
# 2. compilation
# --------------------------------------------------------------------------


class TestCompilation:
    def _rs(self) -> RequirementSet:
        return parse_frd_text(
            "- SYS.1 amp. [feeds: SYS.2] [use: core/opamp-gain-inverting]\n"
            "- SYS.2 filter. [no-pack]"
        )

    def test_compiles_all_three_pragma_kinds(self):
        cr = compile_pragmas(self._rs())
        assert cr.spec.feeds == (FeedEdge(src="SYS.1", dst="SYS.2"),)
        assert cr.spec.pins == {"SYS.1": "core/opamp-gain-inverting"}
        assert cr.spec.forbid_pack == frozenset({"SYS.2"})
        assert cr.diagnostics == []

    def test_merge_spec_file_and_pragma_entries(self):
        base = Spec(path=Path("s.yaml"), forbid_pack=frozenset({"SYS.1"}))
        cr = compile_pragmas(self._rs(), base)
        # spec-file SYS.1 unions with pragma SYS.2
        assert cr.spec.forbid_pack == frozenset({"SYS.1", "SYS.2"})
        assert cr.spec.pins == {"SYS.1": "core/opamp-gain-inverting"}

    def test_conflict_spec_file_wins_and_warns(self):
        base = Spec(path=Path("s.yaml"), pins={"SYS.1": "core/unity-buffer"})
        cr = compile_pragmas(self._rs(), base)
        assert cr.spec.pins["SYS.1"] == "core/unity-buffer"  # spec-file wins
        conflicts = [d for d in cr.diagnostics if d.code == "frd.pragma-conflict"]
        assert len(conflicts) == 1
        assert conflicts[0].severity is Severity.WARNING

    def test_feeds_unknown_target_is_error(self):
        rs = parse_frd_text("- SYS.1 amp. [feeds: NOPE]")
        cr = compile_pragmas(rs)
        errs = [d for d in cr.diagnostics if d.code == "frd.feeds-unknown-target"]
        assert len(errs) == 1
        assert errs[0].severity is Severity.ERROR

    def test_feeds_known_target_no_error(self):
        cr = compile_pragmas(self._rs())
        assert [d for d in cr.diagnostics if d.code == "frd.feeds-unknown-target"] == []


# --------------------------------------------------------------------------
# 3. pinned-use end-to-end "killer test" (primacy defeat)
# --------------------------------------------------------------------------


class TestPinnedUse:
    FRD = (
        "- SYS.1 The system SHALL provide an amplifier gain stage, gain 10.\n"
        "- SYS.2 The system SHALL provide an amplifier gain stage, gain 5."
    )

    def test_control_without_pin_picks_noninverting(self):
        # Proves the pin defeat is non-vacuous: disambiguation primacy normally
        # excludes the inverting cell (it carries a disambiguation rule), so the
        # unqualified "amplifier gain stage" resolves to NON-inverting.
        rs = parse_frd_text(self.FRD)
        cat = Catalog.load(CORE)
        d = decide(match(rs, cat), cat, "prototype")
        assert d.winners()["SYS.1"].chain.cells == (NONINVERTING,)

    def test_pin_forces_inverting_despite_primacy(self):
        frd = self.FRD.replace(
            "gain 10.", "gain 10. [use: core/opamp-gain-inverting]"
        )
        rs = parse_frd_text(frd)
        cat = Catalog.load(CORE)
        cr = compile_pragmas(rs)
        mres = match(rs, cat, pins=cr.spec.pins)
        # pinned candidate is surfaced_by="pinned" and is the SOLE candidate
        assert [c.cell_key for c in mres.candidates["SYS.1"]] == [INVERTING]
        assert mres.candidates["SYS.1"][0].surfaced_by == "pinned"
        d = decide(mres, cat, "prototype")
        assert d.winners()["SYS.1"].chain.cells == (INVERTING,)  # pin beat primacy
        # the un-pinned neighbour still resolves normally to non-inverting
        assert d.winners()["SYS.2"].chain.cells == (NONINVERTING,)

    def test_unresolvable_pin_is_error(self):
        rs = parse_frd_text("- SYS.1 amp gain 10.")
        cat = Catalog.load(CORE)
        mres = match(rs, cat, pins={"SYS.1": "core/does-not-exist"})
        errs = [d for d in mres.diagnostics if d.code == "frd.pin-unresolved"]
        assert len(errs) == 1
        assert errs[0].severity is Severity.ERROR
        assert mres.chains["SYS.1"] == ()


# --------------------------------------------------------------------------
# 4. no-pack threading into the packer
# --------------------------------------------------------------------------


class TestNoPackThreading:
    def _packable_pair(self) -> RequirementSet:
        # two non-inverting gain reqs — without a forbid they pack 2-into-quad.
        return parse_frd_text(
            "- R-1 non-inverting amplifier gain stage, gain 10.\n"
            "- R-2 non-inverting amplifier gain stage, gain 20."
        )

    def test_pair_packs_without_forbid(self):
        rs = self._packable_pair()
        cat = Catalog.load(CORE)
        pr = pack(match(rs, cat), cat, PackKnobs("aggressive"), reqset=rs)
        assert len(pr.claims) == 1
        assert pr.claims[0].absorbed == ("R-1", "R-2")

    def test_forbid_member_prevents_merge(self):
        rs = self._packable_pair()
        cat = Catalog.load(CORE)
        pr = pack(
            match(rs, cat), cat, PackKnobs("aggressive"),
            reqset=rs, forbid_pack={"R-2"},
        )
        # R-2 excluded -> only R-1 remains, which cannot pack alone -> no claim.
        assert pr.claims == ()


# --------------------------------------------------------------------------
# 5. spec YAML round-trip for the three NETFLOW keys
# --------------------------------------------------------------------------


class TestSpecYamlRoundTrip:
    def test_load_and_round_trip(self, tmp_path):
        import yaml

        spec_file = tmp_path / "spec.yaml"
        payload = {
            "feeds": [
                {"src": "SYS.1", "dst": "SYS.2"},
                {"src": "SYS.1", "dst": "SYS.3", "dst_port": "IN2"},
            ],
            "pins": {"SYS.1": "core/opamp-gain-inverting"},
            "forbid_pack": ["SYS.2", "SYS.4"],
        }
        spec_file.write_text(yaml.safe_dump(payload), encoding="utf-8")
        spec = load_spec(spec_file)
        assert spec.feeds == (
            FeedEdge("SYS.1", "SYS.2"),
            FeedEdge("SYS.1", "SYS.3", "IN2"),
        )
        assert spec.pins == {"SYS.1": "core/opamp-gain-inverting"}
        assert spec.forbid_pack == frozenset({"SYS.2", "SYS.4"})

        # round-trip: dump the netflow mapping back out, reload, compare fields.
        again = tmp_path / "again.yaml"
        again.write_text(yaml.safe_dump(spec.netflow_mapping()), encoding="utf-8")
        spec2 = load_spec(again)
        assert spec2.feeds == spec.feeds
        assert spec2.pins == spec.pins
        assert spec2.forbid_pack == spec.forbid_pack

    def test_empty_spec_has_empty_netflow_fields(self, tmp_path):
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text("profile: prototype\n", encoding="utf-8")
        spec = load_spec(spec_file)
        assert spec.feeds == ()
        assert spec.pins == {}
        assert spec.forbid_pack == frozenset()
        assert spec.netflow_mapping() == {}
