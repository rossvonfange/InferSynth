"""Tests for the ecosystem connector pinout classifier
(infersynth/recognize/connectors.py) — the "known not guessed" structural
classification of standard connectors by (pin count + footprint + standardized
pin->function net-name map).

Coverage: load/validate the seeded connector catalog; a positive per-connector
match (right pin count + pinout -> right kind); a negative (a same-pin-count but
different connector, or a bare pin count with no corroboration -> no match, never
a forced guess); footprint-hint corroboration; and determinism.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from infersynth.recognize.connectors import (
    ConnectorsError,
    classify_connector,
    load_connectors,
)
from infersynth.recognize.netlist import Component, DesignNetlist

CONN_PATH = Path(__file__).resolve().parents[1] / "catalog" / "connectors.yaml"


@pytest.fixture(scope="module")
def conns():
    return load_connectors(CONN_PATH)


def _connector_design(ref: str, pin_nets: dict[str, str], footprint: str = "") -> DesignNetlist:
    """A design with one connector *ref* whose pin->net map is *pin_nets*, plus a
    far-side dummy so every net has 2 pins."""
    nets: dict[str, list[tuple[str, str]]] = {}
    for pin, net in pin_nets.items():
        nets.setdefault(net, []).append((ref, pin))
        nets[net].append(("U1", pin))
    comps = {
        ref: Component.build(ref, "", footprint, ""),
        "U1": Component.build("U1", "", "", ""),
    }
    pin_net = {(r, p): name for name, pl in nets.items() for r, p in pl}
    return DesignNetlist(components=comps, nets=nets, pin_net=pin_net)


def test_seeded_connectors_load(conns) -> None:
    for kind in ("rpi40", "rpi26", "mikrobus", "mipi_csi", "mipi_dsi",
                 "pmod_1x6", "pmod_2x6", "qwiic", "arduino_uno_shield", "fmc_lpc"):
        assert kind in conns, kind


def test_malformed_connector_rejected(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("version: 1\nconnectors:\n  x: {pins: [40]}\n")
    with pytest.raises(ConnectorsError):
        load_connectors(p)


# --------------------------------------------------------------------------- #
# Positive: real RPi-40 pinout (mirrors the PolarFire J10 header).            #
# --------------------------------------------------------------------------- #
_RPI40 = {
    "1": "3P3V", "2": "5P0V", "3": "GPIO2_SDA", "4": "5P0V", "5": "GPIO3_SCL",
    "6": "GND", "7": "GPIO4", "8": "GPIO14_TXD", "9": "GND", "10": "GPIO15_RXD",
    "11": "GPIO17", "12": "GPIO18", "13": "GPIO27", "14": "GND", "15": "GPIO22",
    "16": "GPIO23", "17": "3P3V", "18": "GPIO24", "19": "GPIO10_SPI_MOSI",
    "20": "GND", "21": "GPIO9_SPI_MISO", "22": "GPIO25", "23": "GPIO11_SPI_SCLK",
    "24": "GPIO8_SPI_CE0", "25": "GND", "26": "GPIO7", "27": "ID_SD", "28": "ID_SC",
    "29": "GPIO5", "30": "GND", "31": "GPIO6", "32": "GPIO12", "33": "GPIO13",
    "34": "GND", "35": "GPIO19", "36": "GPIO16", "37": "GPIO26", "38": "GPIO20",
    "39": "GND", "40": "GPIO21",
}


def test_positive_rpi40_by_pinout(conns) -> None:
    d = _connector_design("J10", _RPI40)
    m = classify_connector("J10", d, conns)
    assert m.kind == "rpi40"
    assert m.pin_count == 40
    assert m.basis == "pinout"
    assert m.confidence > 0.5


def test_positive_mikrobus_by_pinout(conns) -> None:
    pins = {str(i + 1): n for i, n in enumerate(
        ["AN", "RST", "CS", "SCK", "MISO", "MOSI", "P3V3", "GND",
         "PWM", "INT", "RX", "TX", "SCL", "SDA", "P5V", "GND2"])}
    d = _connector_design("J5", pins)
    m = classify_connector("J5", d, conns)
    assert m.kind == "mikrobus"
    assert m.pin_count == 16


def test_positive_mipi_csi_by_pinout(conns) -> None:
    # mirrors the PolarFire J11 camera header (15 signal + 2 shield pins).
    pins = {
        "1": "GND", "2": "MIPI_RX_N0", "3": "MIPI_RX_P0", "4": "GND",
        "5": "MIPI_RX_N1", "6": "MIPI_RX_P1", "7": "GND", "8": "MIPI_RX_CKN",
        "9": "MIPI_RX_CKP", "10": "GND", "11": "CAM_EN", "12": "CAM_GPIO",
        "13": "CAM_I2C_SCL", "14": "CAM_I2C_SDA", "15": "3P3V",
        "SH1": "GND", "SH2": "GND",
    }
    d = _connector_design("J11", pins)
    m = classify_connector("J11", d, conns)
    assert m.kind == "mipi_csi"


def test_positive_qwiic_by_pinout(conns) -> None:
    pins = {"1": "GND", "2": "P3V3", "3": "QWIIC_SDA", "4": "QWIIC_SCL"}
    d = _connector_design("J9", pins)
    m = classify_connector("J9", d, conns)
    assert m.kind == "qwiic"


# --------------------------------------------------------------------------- #
# Negatives + honesty: a same-pin-count different connector must NOT match.   #
# --------------------------------------------------------------------------- #
def test_negative_usbc_16pin_is_not_mikrobus(conns) -> None:
    # a 16-pin USB-C connector (mirrors PolarFire J4): same pin count as mikroBUS
    # but a totally different pinout -> no match (the honesty gate).
    pins = {
        "A1": "GND", "A4": "USB_5V", "A5": "CC1", "A6": "DP_USBC", "A7": "DM_USBC",
        "A8": "SBU1", "B1": "GND", "B4": "USB_5V", "B5": "CC2", "B6": "DP_USBC",
        "B7": "DM_USBC", "B8": "SBU2", "SH1": "SHLD", "SH2": "SHLD", "SH3": "SHLD",
        "SH4": "SHLD",
    }
    d = _connector_design("J4", pins)
    m = classify_connector("J4", d, conns)
    assert m.kind is None
    assert m.basis == "none"


def test_negative_bare_pincount_no_corroboration(conns) -> None:
    # 40 pins but a random pinout and no footprint -> not force-matched to rpi40.
    pins = {str(i): f"NET{i}" for i in range(1, 41)}
    d = _connector_design("J2", pins)
    m = classify_connector("J2", d, conns)
    assert m.kind is None


def test_negative_wrong_pincount(conns) -> None:
    pins = {"1": "A", "2": "B", "3": "C"}  # 3 pins matches no seeded connector
    d = _connector_design("J8", pins)
    m = classify_connector("J8", d, conns)
    assert m.kind is None


def test_footprint_hint_corroborates(conns) -> None:
    # even a sparse pinout matches when the footprint carries the connector name.
    pins = {str(i): f"N{i}" for i in range(1, 41)}
    pins["3"] = "SDA"
    d = _connector_design("J2", pins, footprint="Raspberry_Pi_2x20_2.54mm")
    m = classify_connector("J2", d, conns)
    assert m.kind == "rpi40"
    assert m.basis == "footprint"


def test_determinism(conns) -> None:
    d = _connector_design("J10", _RPI40)
    a = classify_connector("J10", d, conns)
    b = classify_connector("J10", d, conns)
    assert (a.kind, a.confidence, a.basis, a.candidates) == (
        b.kind, b.confidence, b.basis, b.candidates
    )
