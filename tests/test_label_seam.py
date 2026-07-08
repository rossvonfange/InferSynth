"""Tests for the ``net_role`` label seam — externally-supplied functional pin
roles corroborating the protocol + connector classifiers
(infersynth/recognize/interface_signatures.py, connectors.py, segment.py, and
the reverse CLI ``--labels``).

WHY: Allegro ``.brd`` import strips net-name roles, so on real vendor boards the
structural fingerprints starve (no ``MOSI``/``SDA``/``MDIO`` tokens to
corroborate) and every bundle falls to a generic ``signal``/``bus``/``diff_pair``
and every stripped-footprint connector goes unmatched. A downstream provider
recovers each part's KiCad-symbol pin functions and emits them as ``net_role``
LabelClaims; those tokens must be able to NAME an otherwise-generic bundle /
connector WITHOUT weakening the honesty gate (structure still gates).

Coverage: net_role token union; classify_interface + classify_connector label
corroboration (positive: names when labels present; negative: structure still
gates); ``labels`` folding is byte-identical to the pure net-name path when
absent; determinism; connectors matched via label tokens when the footprint is
absent; the CLI ``--labels`` parse (valid + malformed); and the synthetic
before/after PROVE (generic -> structural histogram flip, basis
``label_corroborated``, plus the honesty negative).
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from infersynth.recognize.connectors import classify_connector, load_connectors
from infersynth.recognize.interface_signatures import (
    classify_interface,
    load_interface_signatures,
)
from infersynth.recognize.netlist import Component, DesignNetlist
from infersynth.recognize.segment import LabelClaim, _net_role_tokens

CATALOG = Path(__file__).resolve().parents[1] / "catalog"
SIG_PATH = CATALOG / "interface_signatures.yaml"
CONN_PATH = CATALOG / "connectors.yaml"


@pytest.fixture(scope="module")
def sigs():
    return load_interface_signatures(SIG_PATH)


@pytest.fixture(scope="module")
def conns():
    return load_connectors(CONN_PATH)


def _design(nets: dict[str, list[tuple[str, str]]], footprints=None) -> DesignNetlist:
    footprints = footprints or {}
    comps: dict[str, Component] = {}
    for pins in nets.values():
        for r, _p in pins:
            comps.setdefault(r, Component.build(r, "", footprints.get(r, ""), ""))
    pin_net = {(r, p): name for name, pl in nets.items() for r, p in pl}
    return DesignNetlist(components=comps, nets=dict(nets), pin_net=pin_net)


# structurally-valid but GENERICALLY-named bundles (the bare-.brd degraded case).
def _spi_bundle() -> tuple[DesignNetlist, list[str]]:
    # 4 single-ended nets, point-to-point (degree 2) — structurally spi/jtag/sdio/i2s.
    nets = {f"N${i}": [("U1", str(i)), ("U2", str(i))] for i in range(1, 5)}
    return _design(nets), list(nets)


def _mdio_bundle() -> tuple[DesignNetlist, list[str]]:
    # 2 single-ended nets — structurally i2c/uart/mdio (the exact shape MDIO shares).
    nets = {"N$7": [("U1", "7"), ("U2", "7")], "N$8": [("U1", "8"), ("U2", "8")]}
    return _design(nets), list(nets)


# --------------------------------------------------------------------------- #
# net_role token union (Build step 1).                                        #
# --------------------------------------------------------------------------- #
def test_net_role_token_union_multiple_labels_per_net() -> None:
    labels = [
        LabelClaim("net_role", "SPI0_MOSI", net="N$1"),
        LabelClaim("net_role", "RPI_GPIO2_SDA", net="N$1"),  # same net, more pins
        LabelClaim("net_role", "MIPI_CSI0_D0_P", net="N$2"),
        LabelClaim("net_label", "ignore_me", net="N$1"),  # non-net_role: excluded
        LabelClaim("block", "PMIC", refs=("U4",)),  # no net: excluded
    ]
    tok = _net_role_tokens(labels)
    assert tok["N$1"] == {"SPI0", "MOSI", "RPI", "GPIO2", "SDA"}
    assert tok["N$2"] == {"MIPI", "CSI0", "D0", "P"}


def test_net_role_tokens_empty_without_labels() -> None:
    assert _net_role_tokens(None) == {}
    assert _net_role_tokens([LabelClaim("block", "X", refs=("U1",))]) == {}


# --------------------------------------------------------------------------- #
# classify_interface label corroboration — positive.                          #
# --------------------------------------------------------------------------- #
def test_interface_generic_without_labels(sigs) -> None:
    d, bundle = _spi_bundle()
    m = classify_interface(bundle, d, sigs)
    assert m.basis == "generic"
    assert m.kind in ("bus", "signal")  # NOT a forced protocol


def test_interface_named_by_labels(sigs) -> None:
    d, bundle = _spi_bundle()
    lt = {
        "N$1": {"SPI0", "MOSI"},
        "N$2": {"SPI0", "MISO"},
        "N$3": {"SPI0", "SCLK"},
        "N$4": {"SPI0", "CS"},
    }
    m = classify_interface(bundle, d, sigs, label_tokens=lt)
    assert m.kind == "spi"
    assert m.basis == "label_corroborated"
    assert m.confidence > 0.2


def test_interface_mdio_named_by_labels(sigs) -> None:
    d, bundle = _mdio_bundle()
    assert classify_interface(bundle, d, sigs).basis == "generic"
    m = classify_interface(bundle, d, sigs, label_tokens={"N$7": {"MDC"}, "N$8": {"MDIO"}})
    assert m.kind == "mdio"
    assert m.basis == "label_corroborated"


def test_interface_basis_is_name_corroborated_when_names_suffice(sigs) -> None:
    # real net names carry the roles: label tokens present but redundant -> the
    # match is attributed to the NAMES (name_corroborated), not the labels.
    nets = {"SPI_MOSI": [("U1", "1"), ("U2", "1")], "SPI_MISO": [("U1", "2"), ("U2", "2")],
            "SPI_SCLK": [("U1", "3"), ("U2", "3")], "SPI_CS": [("U1", "4"), ("U2", "4")]}
    d = _design(nets)
    m = classify_interface(list(nets), d, sigs, label_tokens={"SPI_MOSI": {"MOSI"}})
    assert m.kind == "spi"
    assert m.basis == "name_corroborated"


# --------------------------------------------------------------------------- #
# classify_interface — honesty gate (structure still gates).                  #
# --------------------------------------------------------------------------- #
def test_interface_labels_never_bypass_structure(sigs) -> None:
    # only 2 nets, labeled as if SPI (net_count [3,6]) -> structure contradicts,
    # spi is NOT even a structural candidate -> stays generic, never a false spi.
    d, bundle = _mdio_bundle()
    lt = {"N$7": {"MOSI", "SPI"}, "N$8": {"SCLK", "SPI"}}
    m = classify_interface(bundle, d, sigs, label_tokens=lt)
    assert m.kind != "spi"
    assert m.basis == "generic"


def test_interface_label_no_signature_stays_generic(sigs) -> None:
    # labels supply tokens that match NO signature's role hints -> generic.
    d, bundle = _spi_bundle()
    lt = {n: {"WOMBAT", "FROOD"} for n in bundle}
    m = classify_interface(bundle, d, sigs, label_tokens=lt)
    assert m.basis == "generic"


# --------------------------------------------------------------------------- #
# labels=None byte-identical + determinism.                                   #
# --------------------------------------------------------------------------- #
def test_interface_label_tokens_none_byte_identical(sigs) -> None:
    d, bundle = _spi_bundle()
    a = classify_interface(bundle, d, sigs)
    b = classify_interface(bundle, d, sigs, label_tokens=None)
    c = classify_interface(bundle, d, sigs, label_tokens={})
    for m in (b, c):
        assert (m.kind, m.confidence, m.basis, m.candidates) == (
            a.kind, a.confidence, a.basis, a.candidates
        )


def test_interface_label_determinism(sigs) -> None:
    d, bundle = _spi_bundle()
    lt = {"N$1": {"MOSI"}, "N$2": {"MISO"}, "N$3": {"SCLK"}, "N$4": {"CS"}}
    a = classify_interface(bundle, d, sigs, label_tokens=lt)
    b = classify_interface(bundle, d, sigs, label_tokens=lt)
    assert (a.kind, a.confidence, a.basis, a.candidates) == (
        b.kind, b.confidence, b.basis, b.candidates
    )


# --------------------------------------------------------------------------- #
# classify_connector label corroboration (stripped-footprint path).           #
# --------------------------------------------------------------------------- #
def _rpi40_stripped() -> DesignNetlist:
    # 40-pin connector, footprint STRIPPED, generically-named nets.
    nets = {f"N$c{i}": [("J1", str(i))] for i in range(40)}
    return _design(nets, footprints={"J1": ""})


def test_connector_unmatched_without_labels(conns) -> None:
    d = _rpi40_stripped()
    m = classify_connector("J1", d, conns)
    assert m.kind is None
    assert m.basis == "none"


def test_connector_matched_by_label_tokens(conns) -> None:
    d = _rpi40_stripped()
    # 3 rpi40 function roles supplied by the provider -> meets min_corroboration.
    lt = {"N$c0": {"SDA"}, "N$c1": {"SCL"}, "N$c2": {"MOSI"}, "N$c3": {"GPIO2"}}
    m = classify_connector("J1", d, conns, label_tokens=lt)
    assert m.kind == "rpi40"
    assert m.basis == "label_corroborated"


def test_connector_labels_never_bypass_min_corroboration(conns) -> None:
    d = _rpi40_stripped()
    # only ONE function token < rpi40 min_corroboration (3) -> still no match.
    m = classify_connector("J1", d, conns, label_tokens={"N$c0": {"SDA"}})
    assert m.kind is None


def test_connector_label_tokens_none_byte_identical(conns) -> None:
    # a matched connector via NET NAMES: labels absent must be byte-identical.
    nets = {}
    roles = ["SDA", "SCL", "MOSI", "MISO", "SCLK"] + [f"GPIO{i}" for i in range(35)]
    for i, r in enumerate(roles):
        nets[f"RPI_{r}"] = [("J1", str(i))]
    d = _design(nets, footprints={"J1": ""})
    a = classify_connector("J1", d, conns)
    b = classify_connector("J1", d, conns, label_tokens=None)
    assert (a.kind, a.basis, a.confidence, a.candidates) == (
        b.kind, b.basis, b.confidence, b.candidates
    )
    assert a.kind == "rpi40" and a.basis == "pinout"


# --------------------------------------------------------------------------- #
# CLI --labels parse (valid + malformed).                                     #
# --------------------------------------------------------------------------- #
def test_cli_load_labels_valid(tmp_path: Path) -> None:
    from infersynth.cli import _load_labels

    p = tmp_path / "labels.json"
    p.write_text(json.dumps([
        {"kind": "net_role", "value": "SPI0_MOSI", "net": "N$1"},
        {"kind": "net_role", "value": "MDIO", "net": "N$7"},
        {"kind": "net_label", "value": "DDR4_DQ0", "net": "N$5"},
        {"kind": "block", "value": "PMIC", "refs": ["U4", "L1"]},
    ]))
    labels = _load_labels(str(p))
    assert labels is not None and len(labels) == 4
    assert labels[0].kind == "net_role" and labels[0].value == "SPI0_MOSI"
    assert labels[3].kind == "block" and labels[3].refs == ("U4", "L1")


def test_cli_load_labels_none_is_none() -> None:
    from infersynth.cli import _load_labels

    assert _load_labels(None) is None


@pytest.mark.parametrize("bad", [
    "not json at all {",
    json.dumps({"kind": "net_role"}),          # top level not a list
    json.dumps([{"value": "X", "net": "N$1"}]),  # missing kind
    json.dumps([{"kind": "bogus", "value": "X"}]),  # unknown kind
    json.dumps([{"kind": "net_role", "net": "N$1"}]),  # missing value
    json.dumps([{"kind": "net_role", "value": "X", "net": 5}]),  # net not str
    json.dumps([{"kind": "block", "value": "X", "refs": "U1"}]),  # refs not list
])
def test_cli_load_labels_malformed_clear_error(tmp_path: Path, bad: str) -> None:
    from infersynth.cli import _LabelsError, _load_labels

    p = tmp_path / "bad.json"
    p.write_text(bad)
    with pytest.raises(_LabelsError):  # a clear error, never a raw traceback
        _load_labels(str(p))


_MIN_XML = """<?xml version="1.0"?>
<export version="E">
 <components>
  <comp ref="U1"><value>MCU</value><footprint>QFN</footprint></comp>
  <comp ref="U2"><value>PHY</value><footprint>QFN</footprint></comp>
 </components>
 <nets>
  <net name="N$1"><node ref="U1" pin="1"/><node ref="U2" pin="1"/></net>
  <net name="N$2"><node ref="U1" pin="2"/><node ref="U2" pin="2"/></net>
 </nets>
</export>
"""


def test_cli_segment_labels_end_to_end(tmp_path: Path) -> None:
    from infersynth.cli import main

    xml = tmp_path / "net.xml"
    xml.write_text(_MIN_XML)
    good = tmp_path / "labels.json"
    good.write_text(json.dumps([{"kind": "net_role", "value": "MDIO", "net": "N$2"}]))
    bad = tmp_path / "bad.json"
    bad.write_text("BROKEN{")

    base = ["segment", "--netlist", str(xml), "--catalog", str(CATALOG)]
    assert main(base) == 0  # no flag -> pure connectivity
    assert main([*base, "--labels", str(good)]) == 0
    assert main([*base, "--labels", str(bad)]) == 2  # clear error, not a crash


# --------------------------------------------------------------------------- #
# PROVE — synthetic before/after histogram flip + honesty.                    #
# --------------------------------------------------------------------------- #
def test_prove_histogram_flips_generic_to_structural(sigs, conns) -> None:
    d_spi, spi = _spi_bundle()
    d_mdio, mdio = _mdio_bundle()
    conn = _rpi40_stripped()

    # WITHOUT labels: every bundle generic, connector unmatched (bare-.brd case).
    before = Counter([
        classify_interface(spi, d_spi, sigs).kind,
        classify_interface(mdio, d_mdio, sigs).kind,
    ])
    assert set(before) <= {"signal", "bus", "diff_pair"}
    assert classify_connector("J1", conn, conns).kind is None

    # WITH net_role labels supplying the recovered pin functions.
    spi_lt = {"N$1": {"MOSI"}, "N$2": {"MISO"}, "N$3": {"SCLK"}, "N$4": {"CS"}}
    mdio_lt = {"N$7": {"MDC"}, "N$8": {"MDIO"}}
    conn_lt = {"N$c0": {"SDA"}, "N$c1": {"SCL"}, "N$c2": {"MOSI"}}

    mi_spi = classify_interface(spi, d_spi, sigs, label_tokens=spi_lt)
    mi_mdio = classify_interface(mdio, d_mdio, sigs, label_tokens=mdio_lt)
    mc = classify_connector("J1", conn, conns, label_tokens=conn_lt)

    after = Counter([mi_spi.kind, mi_mdio.kind])
    assert after == Counter({"spi": 1, "mdio": 1})  # flipped generic -> structural
    assert mi_spi.basis == mi_mdio.basis == "label_corroborated"
    assert mc.kind == "rpi40" and mc.basis == "label_corroborated"

    # Honesty: net_role labels that VIOLATE structure (2 nets labeled as SPI) do
    # NOT forge a false SPI — structure gates.
    false_spi = classify_interface(mdio, d_mdio, sigs,
                                   label_tokens={"N$7": {"MOSI"}, "N$8": {"SCLK"}})
    assert false_spi.kind != "spi"
