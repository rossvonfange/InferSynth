"""Tests for the protocol topological signature classifier
(infersynth/recognize/interface_signatures.py) — the "known not guessed"
structural classification of standard protocol bundles.

Coverage: load/validate the seeded signature catalog; a positive per-protocol
classification (a synthetic bundle with the right cardinality/topology/names ->
the correct kind); a negative (wrong cardinality -> a generic kind, never a
false protocol); the name-corroboration confidence bump; the honest-ambiguity
rule (two structurally-identical protocols with no names -> generic, not a
guess); and determinism.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from infersynth.recognize.interface_signatures import (
    GENERIC_CONFIDENCE,
    InterfaceSignaturesError,
    classify_interface,
    fingerprint_bundle,
    load_interface_signatures,
)
from infersynth.recognize.netlist import Component, DesignNetlist

SIG_PATH = Path(__file__).resolve().parents[1] / "catalog" / "interface_signatures.yaml"


@pytest.fixture(scope="module")
def sigs():
    return load_interface_signatures(SIG_PATH)


def _design(nets: dict[str, list[tuple[str, str]]]) -> DesignNetlist:
    comps: dict[str, Component] = {}
    for pins in nets.values():
        for r, _p in pins:
            comps.setdefault(r, Component.build(r, "", "fp", ""))
    pin_net = {(r, p): name for name, pl in nets.items() for r, p in pl}
    return DesignNetlist(components=comps, nets=dict(nets), pin_net=pin_net)


# --------------------------------------------------------------------------- #
# Catalog load / validation.                                                  #
# --------------------------------------------------------------------------- #
def test_seeded_signatures_load(sigs) -> None:
    for kind in ("i2c", "spi", "uart", "usb2", "pcie", "ddr", "sdio", "jtag",
                 "qspi", "i2s", "rgmii", "sgmii", "can"):
        assert kind in sigs, kind


def test_malformed_signature_rejected(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("version: 1\nsignatures:\n  x: {net_count: [2], diff_pairs: [0,0]}\n")
    with pytest.raises(InterfaceSignaturesError):
        load_interface_signatures(p)


# --------------------------------------------------------------------------- #
# Positive per-protocol classification (right structure + names -> right kind).#
# --------------------------------------------------------------------------- #
def _i2c() -> tuple[DesignNetlist, list[str]]:
    nets = {
        # each bus wire carries its two devices plus a pull-up resistor whose
        # other pin sits on the VCC rail (corroborates i2c's pull-up soft hint).
        "SDA": [("U1", "1"), ("U2", "1"), ("R1", "2")],
        "SCL": [("U1", "2"), ("U2", "2"), ("R2", "2")],
        "VCC": [("R1", "1"), ("R2", "1")],
    }
    return _design(nets), ["SDA", "SCL"]


@pytest.mark.parametrize(
    "kind, bundle_nets",
    [
        ("spi", {"SCLK": 2, "MOSI": 2, "MISO": 2}),
        ("uart", {"TXD": 2, "RXD": 2}),
        ("sdio", {"SD_CLK": 2, "SD_CMD": 2, "SD_DAT0": 2, "SD_DAT1": 2}),
        ("jtag", {"TCK": 2, "TMS": 2, "TDI": 2, "TDO": 2}),
        ("qspi", {"QSPI_SCK": 2, "QSPI_CS": 2, "QSPI_IO0": 2, "QSPI_IO1": 2,
                  "QSPI_IO2": 2, "QSPI_IO3": 2}),
        ("i2s", {"I2S_BCLK": 2, "I2S_LRCLK": 2, "I2S_SDIN": 2}),
        ("rgmii", {f"RGMII_{s}": 2 for s in
                   ["TXD0", "TXD1", "TXD2", "TXD3", "TX_CTL", "TXC",
                    "RXD0", "RXD1", "RXD2", "RXD3", "RX_CTL", "RXC"]}),
        ("ddr", {f"DDR4_DQ{i}": 2 for i in range(16)}),
    ],
)
def test_positive_protocol_with_names(sigs, kind: str, bundle_nets) -> None:
    nets = {name: [("U1", str(i)), ("U2", str(i))] for i, name in enumerate(bundle_nets)}
    d = _design(nets)
    m = classify_interface(list(nets), d, sigs)
    assert m.kind == kind, (m.kind, m.candidates)
    assert m.basis in ("name_corroborated", "topology")


def test_positive_i2c_with_pullups(sigs) -> None:
    d, bundle = _i2c()
    m = classify_interface(bundle, d, sigs)
    assert m.kind == "i2c"
    assert m.basis == "name_corroborated"
    assert m.fingerprint.has_pullups is True


def test_positive_usb2_diff_pair(sigs) -> None:
    # a P/N-named diff pair with USB naming -> usb2 (over can, the other 1-pair
    # candidate) by name corroboration.
    nets = {"USB_D_P": [("U1", "1"), ("J1", "1")], "USB_D_N": [("U1", "2"), ("J1", "2")]}
    d = _design(nets)
    m = classify_interface(list(nets), d, sigs)
    assert m.kind == "usb2"
    assert m.fingerprint.diff_pairs == 1


def test_positive_can_by_name(sigs) -> None:
    nets = {"CANH": [("U1", "1"), ("U2", "1")], "CANL": [("U1", "2"), ("U2", "2")]}
    d = _design(nets)
    m = classify_interface(list(nets), d, sigs)
    assert m.kind == "can"


def test_positive_pcie_topology_only(sigs) -> None:
    # 8 nets / 4 diff pairs is uniquely pcie-shaped (sgmii is exactly 4 nets) —
    # classifies from TOPOLOGY alone, no names needed.
    nets = {}
    for lane in range(2):
        for d_ in ("TX", "RX"):
            nets[f"L{lane}_{d_}_P"] = [("U1", f"{lane}{d_}1"), ("U2", f"{lane}{d_}1")]
            nets[f"L{lane}_{d_}_N"] = [("U1", f"{lane}{d_}2"), ("U2", f"{lane}{d_}2")]
    d = _design(nets)
    m = classify_interface(list(nets), d, sigs)
    assert m.kind == "pcie"
    assert m.basis == "topology"
    assert m.fingerprint.diff_pairs == 4


# --------------------------------------------------------------------------- #
# Negatives + honesty.                                                        #
# --------------------------------------------------------------------------- #
def test_negative_wrong_cardinality_is_generic(sigs) -> None:
    # 9 single-ended nets match no seeded signature -> generic bus, low conf.
    nets = {f"RANDOM{i}": [("U1", str(i)), ("U2", str(i))] for i in range(9)}
    d = _design(nets)
    m = classify_interface(list(nets), d, sigs)
    assert m.kind == "bus"
    assert m.basis == "generic"
    assert m.confidence == pytest.approx(GENERIC_CONFIDENCE)


def test_ambiguous_diff_pair_without_names_is_generic(sigs) -> None:
    # one bare diff pair fits usb2 AND can — with no corroborating names we must
    # NOT guess; emit the generic diff_pair kind.
    nets = {"LANE_P": [("U1", "1"), ("U2", "1")], "LANE_N": [("U1", "2"), ("U2", "2")]}
    d = _design(nets)
    m = classify_interface(list(nets), d, sigs)
    assert m.kind == "diff_pair"
    assert m.basis == "generic"


def test_ambiguous_two_wire_without_names_is_generic(sigs) -> None:
    nets = {"A": [("U1", "1"), ("U2", "1")], "B": [("U1", "2"), ("U2", "2")]}
    d = _design(nets)
    m = classify_interface(list(nets), d, sigs)
    assert m.basis == "generic"
    assert m.kind in ("signal", "bus")


def test_name_corroboration_raises_confidence(sigs) -> None:
    # same structure (12 nets, unique rgmii shape); names must not LOWER, and for
    # an ambiguous shape must RAISE, confidence + flip basis.
    named = {f"RGMII_TXD{i}": [("U1", str(i)), ("U2", str(i))] for i in range(12)}
    plain = {f"SIG{i}": [("U1", str(i)), ("U2", str(i))] for i in range(12)}
    dn, dp = _design(named), _design(plain)
    mn = classify_interface(list(named), dn, sigs)
    mp = classify_interface(list(plain), dp, sigs)
    assert mn.kind == "rgmii" and mp.kind == "rgmii"  # 12 nets is uniquely rgmii
    assert mn.basis == "name_corroborated" and mp.basis == "topology"
    assert mn.confidence > mp.confidence


def test_use_names_false_is_pure_structure(sigs) -> None:
    d, bundle = _i2c()
    m = classify_interface(bundle, d, sigs, use_names=False)
    # 2-wire non-diff bundle is structurally ambiguous (i2c/uart/can) -> generic.
    assert m.basis == "generic"


def test_determinism(sigs) -> None:
    d, bundle = _i2c()
    a = classify_interface(bundle, d, sigs)
    b = classify_interface(bundle, d, sigs)
    assert (a.kind, a.confidence, a.basis, a.candidates) == (
        b.kind, b.confidence, b.basis, b.candidates
    )


def test_fingerprint_multidrop_and_pullups(sigs) -> None:
    d, _bundle = _i2c()
    fp = fingerprint_bundle(["SDA", "SCL"], d)
    assert fp.net_count == 2
    # each wire = 2 devices + a pull-up resistor -> degree 3 -> reads multidrop.
    assert fp.is_multidrop is True
    assert fp.has_pullups is True
