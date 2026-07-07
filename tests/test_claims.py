"""Tests for the ``infersynth.claims`` evidence-fusion kernel.

Covers weighted-resolution correctness, dissent preservation, deterministic
tie-breaks, injectable weight tables, empty/single-claim edges, and a small
multi-source vendor-import scenario (the Loom Pillar 2 use case). Ported from
recon's ``tests/test_arbitrate.py`` semantics; PURE (no clock, no randomness).
"""

from __future__ import annotations

import pytest

from infersynth.claims import (
    CONFIDENCE_WEIGHT,
    SOURCE_WEIGHT,
    Arbitration,
    Claim,
    Provenance,
    arbitrate,
    confidence_weight,
    source_weight,
    weigh,
)


# --------------------------------------------------------------------------
# the weighting
# --------------------------------------------------------------------------
class TestWeighting:
    def test_confidence_ladder_high_gt_med_gt_low(self) -> None:
        assert confidence_weight("high") > confidence_weight("med") > confidence_weight("low")
        assert confidence_weight("garbage") == confidence_weight("low")  # unknown -> floor

    def test_source_weight_longest_prefix_wins(self) -> None:
        # a more specific family out-ranks the generic one (longest-prefix match)
        assert source_weight("ipc-d-356.net", {"ipc-d-356": 4.0, "ipc-d-356.net": 5.0}) == 5.0
        assert source_weight("ipc-d-356") > source_weight("gerber")
        assert source_weight("gerber") > source_weight("semantic")
        # unknown family -> heuristic floor (never zero, never infinite)
        assert source_weight("totally-unknown") == source_weight("heuristic")

    def test_no_source_is_absolute_high_conf_weak_beats_low_conf_strong(self) -> None:
        # additive total: a high-confidence gerber can outrank a low-confidence ipc-d-356
        strong_low = weigh(Provenance("ipc-d-356", "low"))
        weak_high = weigh(Provenance("gerber", "high"))
        assert weak_high > strong_low

    def test_native_surfaced_by_families_are_weighted(self) -> None:
        # InferSynth-native provenance tags (surfaced_by) resolve, not just Loom's
        assert weigh(Provenance("idiom", "high")) > weigh(Provenance("semantic", "low"))


# --------------------------------------------------------------------------
# the arbiter
# --------------------------------------------------------------------------
class TestArbitrate:
    def test_picks_weighted_winner_and_keeps_dissent(self) -> None:
        claims = [
            Claim("gerber_val", Provenance("gerber", "med")),
            Claim("netlist_val", Provenance("ipc-d-356", "high")),
        ]
        arb = arbitrate(claims)
        assert isinstance(arb, Arbitration)
        assert arb.winner.value == "netlist_val"
        assert arb.contested
        # the loser is NOT discarded
        assert [c.value for c in arb.dissent] == ["gerber_val"]
        assert [c.provenance.source for c in arb.dissenters] == ["gerber"]

    def test_corroboration_is_not_contested(self) -> None:
        # two sources that AGREE on the value are not a conflict (loser corroborates)
        claims = [
            Claim("VCC", Provenance("bom", "high")),
            Claim("VCC", Provenance("ipc-d-356", "high")),
        ]
        arb = arbitrate(claims)
        assert not arb.contested
        assert arb.dissenters == ()
        # but the corroborating claim is still kept in dissent (nothing dropped)
        assert len(arb.dissent) == 1

    def test_overridable_weighting_flips_the_winner(self) -> None:
        claims = [
            Claim("gerber_val", Provenance("gerber", "med")),
            Claim("netlist_val", Provenance("ipc-d-356", "high")),
        ]
        assert arbitrate(claims).winner.value == "netlist_val"      # default: netlist wins
        flipped = {**SOURCE_WEIGHT, "ipc-d-356": 0.0}               # demote the netlist source
        assert arbitrate(claims, source_table=flipped).winner.value == "gerber_val"
        # confidence table is injectable too
        conf = {**CONFIDENCE_WEIGHT, "med": 99.0}
        assert arbitrate(claims, confidence_table=conf).winner.value == "gerber_val"

    def test_deterministic_tiebreak_is_first_seen(self) -> None:
        # equal weight -> stable: the first-seen claim wins, never arbitrary ordering
        a = Claim("a", Provenance("gerber", "med"))
        b = Claim("b", Provenance("bom", "med"))  # gerber & bom share base weight + confidence
        assert weigh(a.provenance) == weigh(b.provenance)
        assert arbitrate([a, b]).winner.value == "a"
        assert arbitrate([b, a]).winner.value == "b"

    def test_determinism_two_runs_byte_identical(self) -> None:
        claims = [
            Claim("x", Provenance("gerber", "med")),
            Claim("y", Provenance("ipc-d-356", "high")),
            Claim("z", Provenance("bom", "low")),
        ]
        r1, r2 = arbitrate(claims), arbitrate(claims)
        assert r1.winner == r2.winner
        assert r1.dissent == r2.dissent  # order-stable

    def test_key_normalizes_contested_test(self) -> None:
        claims = [
            Claim("CAN_H", Provenance("ipc-d-356", "high")),
            Claim("can_h", Provenance("bom", "high")),
        ]
        assert arbitrate(claims).contested                     # raw: different strings
        assert not arbitrate(claims, key=str.lower).contested  # normalized: agree

    def test_single_claim_wins_uncontested(self) -> None:
        arb = arbitrate([Claim("only", Provenance("gerber", "low"))])
        assert arb.winner.value == "only"
        assert arb.dissent == ()
        assert not arb.contested

    def test_empty_fails_fast(self) -> None:
        with pytest.raises(ValueError):
            arbitrate([])

    def test_provenance_requires_non_empty_source(self) -> None:
        with pytest.raises(ValueError):
            Provenance("")


# --------------------------------------------------------------------------
# the Loom Pillar 2 use case (Gerber vs IPC-D-356 vs BOM disagree on a pad value)
# --------------------------------------------------------------------------
class TestVendorImportScenario:
    def test_three_source_pad_value_fusion_keeps_minority(self) -> None:
        # three vendor artifacts disagree on one pad's net; the netlist test data
        # (ipc-d-356, high) wins, but the gerber + bom evidence is preserved.
        claims = [
            Claim("GND", Provenance("gerber", "med")),      # copper says GND
            Claim("SGND", Provenance("ipc-d-356", "high")),  # netlist says SGND
            Claim("GND", Provenance("bom", "low")),          # BOM corroborates gerber
        ]
        arb = arbitrate(claims)
        assert arb.winner.value == "SGND"
        assert arb.contested
        # every losing claim is kept, in descending-weight order
        assert [c.value for c in arb.dissent] == ["GND", "GND"]
        assert [c.provenance.source for c in arb.dissent] == ["gerber", "bom"]
        # the two dissenters that disagree with the winner are surfaced for re-query
        assert {c.provenance.source for c in arb.dissenters} == {"gerber", "bom"}
