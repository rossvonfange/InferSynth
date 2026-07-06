"""interfaces.yaml loader + cell ``interfaces:`` validation + ``mates()``
(NETFLOW.md "Interfaces (bundles)", build order item 3)."""

from pathlib import Path

import pytest
import yaml

from infersynth.catalog import Catalog, CatalogError, CellPackageError, load_cell
from infersynth.catalog.interfaces import (
    InterfaceDef,
    InterfaceGroup,
    InterfacesError,
    RoleDef,
    load_interfaces,
    mates,
)
from tests.test_catalog import write_cell

GOLDEN_CATALOG = Path(__file__).parent.parent / "catalog"

UART = {
    "version": 1,
    "interfaces": {
        "uart": {"roles": {"TX": {"dir": "out"}, "RX": {"dir": "in"}}},
    },
}

SPI = {
    "version": 1,
    "interfaces": {
        "spi": {
            "roles": {
                "SCLK": {"dir": "out"},
                "MOSI": {"dir": "out"},
                "MISO": {"dir": "in"},
                "CS": {"dir": "out", "optional_many": True},
            }
        },
    },
}


def _write_interfaces(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "interfaces.yaml"
    p.write_text(yaml.safe_dump(data))
    return p


class TestLoadInterfaces:
    def test_load_success(self, tmp_path):
        p = _write_interfaces(tmp_path, UART)
        defs = load_interfaces(p)
        assert set(defs) == {"uart"}
        assert defs["uart"].roles["TX"] == RoleDef(dir="out")
        assert defs["uart"].roles["RX"] == RoleDef(dir="in")

    def test_real_interfaces_yaml_loads(self):
        defs = load_interfaces(GOLDEN_CATALOG / "interfaces.yaml")
        assert set(defs) == {"uart", "spi", "i2c", "can_phy", "diff_pair", "analog"}
        assert defs["can_phy"].domain == "differential"
        assert defs["can_phy"].roles["CANH"].kind == "diff_p"
        assert defs["can_phy"].roles["CANL"].kind == "diff_n"
        assert defs["spi"].roles["CS"].optional_many is True

    def test_bad_dir_enum_rejected(self, tmp_path):
        bad = {
            "version": 1,
            "interfaces": {"uart": {"roles": {"TX": {"dir": "sideways"}}}},
        }
        p = _write_interfaces(tmp_path, bad)
        with pytest.raises(InterfacesError, match="dir must be one of"):
            load_interfaces(p)

    def test_unknown_top_level_key_rejected(self, tmp_path):
        bad = {**UART, "bogus": 1}
        p = _write_interfaces(tmp_path, bad)
        with pytest.raises(InterfacesError, match="unknown top-level key"):
            load_interfaces(p)

    def test_unknown_interface_key_rejected(self, tmp_path):
        bad = {
            "version": 1,
            "interfaces": {"uart": {"roles": {"TX": {"dir": "out"}}, "bogus": 1}},
        }
        p = _write_interfaces(tmp_path, bad)
        with pytest.raises(InterfacesError, match="unknown key"):
            load_interfaces(p)

    def test_unknown_role_key_rejected(self, tmp_path):
        bad = {
            "version": 1,
            "interfaces": {"uart": {"roles": {"TX": {"dir": "out", "bogus": 1}}}},
        }
        p = _write_interfaces(tmp_path, bad)
        with pytest.raises(InterfacesError, match="unknown key"):
            load_interfaces(p)

    def test_zero_roles_rejected(self, tmp_path):
        bad = {"version": 1, "interfaces": {"uart": {"roles": {}}}}
        p = _write_interfaces(tmp_path, bad)
        with pytest.raises(InterfacesError, match="non-empty mapping"):
            load_interfaces(p)

    def test_not_a_mapping_rejected(self, tmp_path):
        p = tmp_path / "interfaces.yaml"
        p.write_text(yaml.safe_dump(["not", "a", "mapping"]))
        with pytest.raises(InterfacesError, match="must be a mapping"):
            load_interfaces(p)


class TestCellInterfacesValidation:
    """Cell.yaml ``interfaces:`` section validation rules."""

    def _uart_cell(self, tmp_path, ports=None, interfaces=None, name="c1"):
        ports = ports or {
            "TXD": {"direction": "out", "kind": "digital"},
            "RXD": {"direction": "in", "kind": "digital"},
        }
        return write_cell(
            tmp_path,
            name,
            ports=ports,
            extra_sections={"interfaces": interfaces} if interfaces is not None else None,
        )

    def test_valid_uart_group_loads(self, tmp_path):
        _write_interfaces(tmp_path, UART)
        cell_dir = self._uart_cell(
            tmp_path,
            interfaces={
                "main": {"type": "uart", "role": "initiator", "map": {"TX": "TXD", "RX": "RXD"}}
            },
        )
        cell = load_cell(cell_dir)
        assert set(cell.interfaces) == {"main"}
        group = cell.interfaces["main"]
        assert isinstance(group, InterfaceGroup)
        assert group.type == "uart"
        assert group.role == "initiator"
        assert group.map == {"TX": "TXD", "RX": "RXD"}

    def test_unknown_type_rejected(self, tmp_path):
        _write_interfaces(tmp_path, UART)
        cell_dir = self._uart_cell(
            tmp_path,
            interfaces={
                "main": {"type": "bogus_bus", "role": "initiator", "map": {"TX": "TXD"}}
            },
        )
        with pytest.raises(CellPackageError, match="unknown interface type 'bogus_bus'"):
            load_cell(cell_dir)

    def test_bad_role_enum_rejected(self, tmp_path):
        _write_interfaces(tmp_path, UART)
        cell_dir = self._uart_cell(
            tmp_path,
            interfaces={
                "main": {
                    "type": "uart",
                    "role": "master",
                    "map": {"TX": "TXD", "RX": "RXD"},
                }
            },
        )
        with pytest.raises(CellPackageError, match="role must be one of"):
            load_cell(cell_dir)

    def test_unknown_mapped_role_rejected(self, tmp_path):
        _write_interfaces(tmp_path, UART)
        cell_dir = self._uart_cell(
            tmp_path,
            interfaces={
                "main": {
                    "type": "uart",
                    "role": "initiator",
                    "map": {"TX": "TXD", "RX": "RXD", "BOGUS": "TXD"},
                }
            },
        )
        with pytest.raises(CellPackageError, match="unknown role 'BOGUS'"):
            load_cell(cell_dir)

    def test_missing_required_role_rejected(self, tmp_path):
        _write_interfaces(tmp_path, UART)
        cell_dir = self._uart_cell(
            tmp_path,
            interfaces={"main": {"type": "uart", "role": "initiator", "map": {"TX": "TXD"}}},
        )
        with pytest.raises(CellPackageError, match="required role 'RX'.*is not mapped"):
            load_cell(cell_dir)

    def test_unmapped_optional_many_role_ok(self, tmp_path):
        _write_interfaces(tmp_path, SPI)
        cell_dir = write_cell(
            tmp_path,
            "c1",
            ports={
                "SCLK": {"direction": "out", "kind": "digital"},
                "MOSI": {"direction": "out", "kind": "digital"},
                "MISO": {"direction": "in", "kind": "digital"},
            },
            extra_sections={
                "interfaces": {
                    "main": {
                        "type": "spi",
                        "role": "initiator",
                        "map": {"SCLK": "SCLK", "MOSI": "MOSI", "MISO": "MISO"},
                    }
                }
            },
        )
        cell = load_cell(cell_dir)
        assert "CS" not in cell.interfaces["main"].map

    def test_mapped_port_not_in_cell_ports_rejected(self, tmp_path):
        _write_interfaces(tmp_path, UART)
        cell_dir = self._uart_cell(
            tmp_path,
            interfaces={
                "main": {
                    "type": "uart",
                    "role": "initiator",
                    "map": {"TX": "NOPE", "RX": "RXD"},
                }
            },
        )
        with pytest.raises(CellPackageError, match="port 'NOPE' is not declared"):
            load_cell(cell_dir)

    def test_double_port_membership_rejected(self, tmp_path):
        _write_interfaces(tmp_path, UART)
        cell_dir = self._uart_cell(
            tmp_path,
            ports={
                "TXD": {"direction": "out", "kind": "digital"},
                "RXD": {"direction": "in", "kind": "digital"},
            },
            interfaces={
                "a": {"type": "uart", "role": "initiator", "map": {"TX": "TXD", "RX": "RXD"}},
                "b": {"type": "uart", "role": "peripheral", "map": {"TX": "TXD", "RX": "RXD"}},
            },
        )
        with pytest.raises(CellPackageError, match="already claimed by interface group"):
            load_cell(cell_dir)

    def test_unknown_group_key_rejected(self, tmp_path):
        _write_interfaces(tmp_path, UART)
        cell_dir = self._uart_cell(
            tmp_path,
            interfaces={
                "main": {
                    "type": "uart",
                    "role": "initiator",
                    "map": {"TX": "TXD", "RX": "RXD"},
                    "bogus": 1,
                }
            },
        )
        with pytest.raises(CellPackageError, match="unknown key"):
            load_cell(cell_dir)

    def test_kind_incompatibility_rejected(self, tmp_path):
        # can_phy's CANH role declares kind "diff_p" which IS in the shared
        # port-kind vocabulary check only when it equals a real PortKind
        # value; here we exercise the compatibility path using a synthetic
        # interfaces.yaml whose role kind matches the port-kind vocabulary
        # ("power") so the mismatch is meaningfully checked end to end.
        defs = {
            "version": 1,
            "interfaces": {
                "sig": {"roles": {"A": {"dir": "out", "kind": "power"}}},
            },
        }
        _write_interfaces(tmp_path, defs)
        cell_dir = write_cell(
            tmp_path,
            "c1",
            ports={"P1": {"direction": "out", "kind": "digital"}},
            extra_sections={
                "interfaces": {"main": {"type": "sig", "role": "initiator", "map": {"A": "P1"}}}
            },
        )
        with pytest.raises(CellPackageError, match="incompatible with role 'A'"):
            load_cell(cell_dir)

    def test_domain_tag_kind_not_checked_against_port_kind(self, tmp_path):
        # can_phy's CANH/CANL kinds (diff_p/diff_n) are outside the port-kind
        # vocabulary (electrical/power/digital) entirely, so they are never
        # checked against a mapped port's kind -- this is what makes can_phy
        # mappable at all given ports: cannot legally declare kind: diff_p.
        real = yaml.safe_load((GOLDEN_CATALOG / "interfaces.yaml").read_text())
        _write_interfaces(tmp_path, real)
        cell_dir = write_cell(
            tmp_path,
            "c1",
            ports={
                "CAN_H": {"direction": "in", "kind": "electrical"},
                "CAN_L": {"direction": "in", "kind": "electrical"},
            },
            extra_sections={
                "interfaces": {
                    "main": {
                        "type": "can_phy",
                        "role": "peripheral",
                        "map": {"CANH": "CAN_H", "CANL": "CAN_L"},
                    }
                }
            },
        )
        cell = load_cell(cell_dir)
        assert cell.interfaces["main"].map == {"CANH": "CAN_H", "CANL": "CAN_L"}

    def test_no_interfaces_yaml_rejects_claim(self, tmp_path):
        # No interfaces.yaml written at all in tmp_path.
        cell_dir = self._uart_cell(
            tmp_path,
            interfaces={
                "main": {"type": "uart", "role": "initiator", "map": {"TX": "TXD", "RX": "RXD"}}
            },
        )
        with pytest.raises(CellPackageError, match="no interfaces.yaml in catalog"):
            load_cell(cell_dir)

    def test_no_interfaces_yaml_rejects_claim_via_catalog(self, tmp_path):
        # Mirrors TestFunctionsTaxonomy.test_no_taxonomy_in_catalog_rejects_functions_claim.
        self._uart_cell(
            tmp_path,
            interfaces={
                "main": {"type": "uart", "role": "initiator", "map": {"TX": "TXD", "RX": "RXD"}}
            },
        )
        with pytest.raises(CatalogError, match="no interfaces.yaml in catalog"):
            Catalog.load(tmp_path)

    def test_catalog_threads_interfaces_through(self, tmp_path):
        _write_interfaces(tmp_path, UART)
        self._uart_cell(
            tmp_path,
            interfaces={
                "main": {"type": "uart", "role": "initiator", "map": {"TX": "TXD", "RX": "RXD"}}
            },
        )
        catalog = Catalog.load(tmp_path)
        assert set(catalog.interfaces) == {"uart"}
        cell = next(iter(catalog.cells.values()))
        assert cell.interfaces["main"].type == "uart"

    def test_real_catalog_still_loads_with_interfaces_yaml_present(self):
        # The real catalog/interfaces.yaml exists but none of the 14 real
        # cells claim an interfaces: section (see report for rationale) --
        # loading must still be strict-clean.
        catalog = Catalog.load(GOLDEN_CATALOG)
        assert set(catalog.interfaces) == {
            "uart",
            "spi",
            "i2c",
            "can_phy",
            "diff_pair",
            "analog",
        }
        assert all(c.interfaces == {} for c in catalog.cells.values())


class TestMates:
    def test_uart_crossover_pairing(self):
        """The explicit crossover assertion NETFLOW.md's design calls out:
        the initiator's TX-mapped port pairs with the peripheral's TX-mapped
        port -- both keyed by the SAME role name "TX", never TX-to-RX."""
        defs = load_interfaces_from_dict(UART)
        initiator = InterfaceGroup(
            name="main", type="uart", role="initiator", map={"TX": "MCU_TXD", "RX": "MCU_RXD"}
        )
        peripheral = InterfaceGroup(
            name="main", type="uart", role="peripheral", map={"TX": "DEV_TXD", "RX": "DEV_RXD"}
        )
        result = mates(initiator, peripheral, defs)
        assert result.mates is True
        assert result.type == "uart"
        pairs = {(p.role, p.initiator_port, p.peripheral_port) for p in result.pairs}
        assert ("TX", "MCU_TXD", "DEV_TXD") in pairs
        assert ("RX", "MCU_RXD", "DEV_RXD") in pairs
        assert len(pairs) == 2

    def test_spi_pairing_with_optional_cs(self):
        defs = load_interfaces_from_dict(SPI)
        initiator = InterfaceGroup(
            name="main",
            type="spi",
            role="initiator",
            map={"SCLK": "SCLK", "MOSI": "MOSI", "MISO": "MISO", "CS": "CS0"},
        )
        peripheral = InterfaceGroup(
            name="main",
            type="spi",
            role="peripheral",
            map={"SCLK": "SCK_IN", "MOSI": "SI", "MISO": "SO", "CS": "CSB"},
        )
        result = mates(initiator, peripheral, defs)
        assert result.mates is True
        pairs = {(p.role, p.initiator_port, p.peripheral_port) for p in result.pairs}
        assert pairs == {
            ("SCLK", "SCLK", "SCK_IN"),
            ("MOSI", "MOSI", "SI"),
            ("MISO", "MISO", "SO"),
            ("CS", "CS0", "CSB"),
        }

    def test_spi_pairing_cs_unmapped_on_one_side_skipped(self):
        defs = load_interfaces_from_dict(SPI)
        initiator = InterfaceGroup(
            name="main",
            type="spi",
            role="initiator",
            map={"SCLK": "SCLK", "MOSI": "MOSI", "MISO": "MISO"},
        )
        peripheral = InterfaceGroup(
            name="main",
            type="spi",
            role="peripheral",
            map={"SCLK": "SCK_IN", "MOSI": "SI", "MISO": "SO", "CS": "CSB"},
        )
        result = mates(initiator, peripheral, defs)
        assert result.mates is True
        roles = {p.role for p in result.pairs}
        assert roles == {"SCLK", "MOSI", "MISO"}

    def test_type_mismatch_does_not_mate(self):
        defs = load_interfaces_from_dict(UART)
        a = InterfaceGroup(name="a", type="uart", role="initiator", map={"TX": "T", "RX": "R"})
        b = InterfaceGroup(name="b", type="spi", role="peripheral", map={})
        result = mates(a, b, defs)
        assert result.mates is False
        assert "type mismatch" in result.reason

    def test_initiator_initiator_does_not_mate(self):
        defs = load_interfaces_from_dict(UART)
        a = InterfaceGroup(name="a", type="uart", role="initiator", map={"TX": "T1", "RX": "R1"})
        b = InterfaceGroup(name="b", type="uart", role="initiator", map={"TX": "T2", "RX": "R2"})
        result = mates(a, b, defs)
        assert result.mates is False
        assert "initiator<->peripheral" in result.reason

    def test_peripheral_peripheral_does_not_mate(self):
        defs = load_interfaces_from_dict(UART)
        a = InterfaceGroup(name="a", type="uart", role="peripheral", map={"TX": "T1", "RX": "R1"})
        b = InterfaceGroup(name="b", type="uart", role="peripheral", map={"TX": "T2", "RX": "R2"})
        result = mates(a, b, defs)
        assert result.mates is False


def load_interfaces_from_dict(data: dict) -> dict[str, InterfaceDef]:
    """Test helper: build InterfaceDef map straight from a dict (no file I/O)."""
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.safe_dump(data, f)
        path = f.name
    return load_interfaces(path)
