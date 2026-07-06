"""Netlist gate plumbing — export + parse, feed the partition-equivalence gate.

Two partition sources are supported for the comparison:

* the KiCad-exported netlist of the generated schematic
  (``kicad-cli sch export netlist --format kicadxml``), and
* a cell's ``golden_netlist.txt`` (the format the fragments were captured in).

Both are reduced to a ``net -> {(ref, pin)}`` partition and handed to the
already-implemented :func:`compare_partitions`, which compares modulo net
naming — so the sheet-scoped ``/instname/NET`` names ``kicad-cli`` emits under
a harness compare equal to the fragment's bare net names.

Driver pseudo-components the harness injects (``PWR_FLAG`` and power symbols)
carry KiCad's ``#``-prefixed references (``#FLG01``, ``#PWR01``); they are
filtered out so the exported partition contains exactly the cell's real pins.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from pathlib import Path

__all__ = [
    "NetlistError",
    "export_netlist",
    "parse_kicadxml",
    "parse_golden_netlist",
    "schematic_partition",
]

Partition = dict[str, list[tuple[str, str]]]


class NetlistError(RuntimeError):
    """Raised when netlist export or parsing fails."""


def _is_driver_ref(ref: str) -> bool:
    """KiCad virtual refs for power symbols / flags start with ``#``."""
    return ref.startswith("#")


def parse_kicadxml(xml_path: str | Path) -> Partition:
    """Parse a ``kicadxml`` netlist into ``net -> [(ref, pin), ...]``.

    ``#``-prefixed (power-flag / power-symbol) nodes are dropped; empty nets
    (all nodes filtered) are omitted.
    """
    try:
        root = ET.parse(Path(xml_path)).getroot()
    except (OSError, ET.ParseError) as exc:
        raise NetlistError(f"cannot parse netlist {xml_path}: {exc}") from exc
    partition: Partition = {}
    for net in root.iter("net"):
        name = net.get("name") or net.get("code") or ""
        pins: list[tuple[str, str]] = []
        for node in net.findall("node"):
            ref = node.get("ref") or ""
            pin = node.get("pin") or ""
            if _is_driver_ref(ref):
                continue
            pins.append((ref, pin))
        if pins:
            partition[name] = pins
    return partition


def parse_golden_netlist(path: str | Path) -> Partition:
    """Parse a ``golden_netlist.txt`` file.

    Format (one net per line)::

        NETNAME: REF/PIN, REF/PIN, ...
        # comment lines and blank lines are ignored

    Raises :class:`NetlistError` on a malformed line.
    """
    partition: Partition = {}
    text = Path(path).read_text()
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise NetlistError(f"{path}:{lineno}: missing ':' in net line: {raw!r}")
        name, rest = line.split(":", 1)
        name = name.strip()
        if not name:
            raise NetlistError(f"{path}:{lineno}: empty net name: {raw!r}")
        pins: list[tuple[str, str]] = []
        for token in rest.split(","):
            token = token.strip()
            if not token:
                continue
            if "/" not in token:
                raise NetlistError(
                    f"{path}:{lineno}: pin token {token!r} is not REF/PIN"
                )
            ref, pin = token.rsplit("/", 1)
            pins.append((ref.strip(), pin.strip()))
        partition[name] = pins
    return partition


def export_netlist(schematic_path: str | Path) -> Partition:
    """Export *schematic_path*'s netlist via ``kicad-cli`` and parse it.

    Raises :class:`NetlistError` if ``kicad-cli`` is missing or export fails.
    """
    root = Path(schematic_path)
    if shutil.which("kicad-cli") is None:
        raise NetlistError("kicad-cli not found on PATH")
    if not root.is_file():
        raise NetlistError(f"schematic not found: {root}")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "netlist.xml"
        proc = subprocess.run(
            [
                "kicad-cli",
                "sch",
                "export",
                "netlist",
                "--format",
                "kicadxml",
                "-o",
                str(out),
                str(root),
            ],
            capture_output=True,
            text=True,
        )
        if not out.is_file():
            raise NetlistError(
                f"kicad-cli netlist export produced no file (rc={proc.returncode}): "
                f"{proc.stderr.strip() or proc.stdout.strip()}"
            )
        return parse_kicadxml(out)


def schematic_partition(schematic_path: str | Path) -> Mapping[str, list[tuple[str, str]]]:
    """Convenience alias returning the exported schematic partition."""
    return export_netlist(schematic_path)
