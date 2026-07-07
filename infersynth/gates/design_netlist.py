"""Design-level netlist partition-equivalence gate (round 2 / SEED_PLAN §2).

The per-cell gate (:mod:`infersynth.gates.netlist_equiv`) proves ONE cell's
harnessed netlist matches its ``golden_netlist.txt``. This module proves the
next layer up: that a fully synthesized, fully-wired design's exported
full-hierarchy netlist actually realizes every net a :class:`~infersynth.
netflow.plan.WiringPlan` says it should — every member's pins are mutually
connected — no missing connections, no dropped members.

Port -> pin correspondence rule (derived empirically against BridgeSense-1,
``examples/frds/07_bridgesense_1``): :func:`infersynth.compile_kicad.wiring.
emit_wiring` drops a parent-level ``global_label`` named exactly the plan
net's name at every member's sheet pin, and KiCad's netlist exporter then
names the WHOLE net after that global label (not the sheet-scoped local
name it uses for a cell's unwired-to-the-parent internal nets) — so a clean
plan net's own name is *usually* a key in :func:`infersynth.gates.netlist.
export_netlist`'s partition verbatim. But it is not reliably so: when two or
more plan nets happen to share a member port (a legitimate fan-out — e.g. a
single reference/virtual-ground port feeding several independent downstream
sinks, each inferred as its own pairwise net by NETFLOW's convergence
tier), KiCad's exporter merges them into ONE physical net at export time and
picks just one of the candidate global-label names to represent it. So this
gate checks CONNECTIVITY, not literal name equality: for each plan net, every
expected pin (see below) must land in some single actual exported net
together — regardless of which name the exporter chose, and regardless of
whatever unrelated extra pins (from a merged, legitimately fanned-out
sibling net) that actual net also carries.

Reference designators are only unique WITHIN a cell's own schematic (every
instantiated cell starts its own R1/U1/C1 numbering), so a plain ``(ref,
pin)`` pair cannot be resolved to a member without first knowing which cell
instance the member belongs to. That's exactly what a cell's own
``golden_netlist.txt`` already records for its hierarchical-label ports: the
entry keyed ``/<port>`` lists every ``(ref, pin)`` inside that cell (there
can be more than one — an ``OUT`` port that also feeds an internal feedback
resistor shows up on both pins) the port's hierarchical label sits on. So
one plan member ``(instname, port)`` maps to the ``(ref, pin)`` set at
``golden_netlist[f"/{port}"]`` for ``instname``'s own cell — and a plan
net's full expected pin set is the union of that lookup over all its
members.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from infersynth.catalog import Catalog, CellPackage
from infersynth.gates.netlist import NetlistError, export_netlist, parse_golden_netlist
from infersynth.gates.runner import GateResult
from infersynth.netflow.plan import Net, WiringPlan

__all__ = [
    "Pin",
    "expected_pins_for_member",
    "design_netlist_gate",
    "plan_to_dict",
    "plan_from_dict",
]

Pin = tuple[str, str]


def _cell_and_golden(
    instname: str, instance_cells: Mapping[str, str], catalog: Catalog
) -> tuple[CellPackage | None, Mapping[str, Sequence[Pin]] | None]:
    """Resolve *instname* to its cell and (if declared) parsed golden partition."""
    cell_key = instance_cells.get(instname)
    if cell_key is None:
        return None, None
    cell = catalog.cells.get(cell_key)
    if cell is None:
        return None, None
    golden_name = cell.verification.get("golden_netlist")
    if not golden_name:
        return cell, None
    golden = parse_golden_netlist(cell.path / golden_name)
    return cell, golden



_REF_RE = re.compile(r'\(property\s+"Reference"\s+"([A-Z]+)(\d+)"')


def _refdes_offset(root: Path, instname: str) -> int:
    """The per-instance refdes renumber offset (emit's k*100 scheme).

    The emitter renumbers each instantiated sheet's refs into a per-instance
    century block (U1 -> U101 for instance 1, etc.). Rather than re-deriving
    the instantiation order, read the instance's own child schematic and take
    the century of its first non-virtual reference; 0 when the child was
    emitted without renumbering (renumber_refs=False paths, older designs).
    """
    child = root.parent / f"{instname}.kicad_sch"
    try:
        text = child.read_text(encoding="utf-8")
    except OSError:
        return 0
    for m in _REF_RE.finditer(text):
        letters, num = m.group(1), int(m.group(2))
        if letters.startswith("#"):
            continue
        if num >= 100:
            return (num // 100) * 100
        return 0
    return 0


def _offset_pin(pin: Pin, offset: int) -> Pin:
    ref, pnum = pin
    m = re.match(r"([A-Z]+)(\d+)$", ref)
    if not m or offset == 0 or ref.startswith("#"):
        return pin
    return (f"{m.group(1)}{int(m.group(2)) + offset}", pnum)


def expected_pins_for_member(
    instname: str, port: str, instance_cells: Mapping[str, str], catalog: Catalog
) -> tuple[Pin, ...] | None:
    """The ``(ref, pin)`` set the exported netlist should carry for one plan
    member, per that instance's own cell ``golden_netlist.txt`` entry
    ``/<port>``. ``None`` when unresolvable (unknown instance/cell, cell
    declares no ``verification.golden_netlist``, or the golden partition has
    no ``/<port>`` entry) — callers report these distinctly.
    """
    cell, golden = _cell_and_golden(instname, instance_cells, catalog)
    if cell is None or golden is None:
        return None
    key = f"/{port}"
    if key not in golden:
        return None
    return tuple(golden[key])


def design_netlist_gate(
    root: str | Path,
    plan: WiringPlan,
    instance_cells: Mapping[str, str],
    catalog: Catalog,
) -> GateResult:
    """Verify every *plan* net's members are actually mutually connected in
    *root*'s exported full-hierarchy netlist.

    For each plan net, every member's expected ``(ref, pin)`` set (resolved
    via its own cell's ``golden_netlist.txt``, see the module docstring) must
    all appear together in ONE exported net — whatever that exported net is
    named, and regardless of any extra pins it also carries from a *different*
    plan net that legitimately shares a member port (a fan-out net; see the
    module docstring). A plan member that resolves to no pins anywhere in the
    export, or whose members split across more than one actual net, fails
    with the exact net name and mismatching member(s) named.

    Child-internal nets that appear in the export but name no plan net are
    ignored — those are the per-cell gate's territory
    (:mod:`infersynth.gates.netlist_equiv`), already proven per cell.
    """
    name = "design-netlist-partition-equivalence"
    root = Path(root)
    try:
        exported = export_netlist(root)
    except NetlistError as exc:
        return GateResult.failed(name, f"netlist export failed: {exc}")

    # pin -> {exported net names containing it}. Reference designators are
    # only unique within one cell instance (see module docstring), so a pin
    # can legitimately map to more than one exported net name here; connectivity
    # for a given plan net is proven by intersecting across all its members,
    # not by trusting any single pin's mapping alone.
    pin_index: dict[Pin, set[str]] = {}
    for net_name, pins in exported.items():
        for p in pins:
            pin_index.setdefault(tuple(p), set()).add(net_name)

    diagnostics: list[str] = []
    checked = 0
    offsets: dict[str, int] = {}
    for net in plan.nets:
        member_pins: dict[tuple[str, str], tuple[Pin, ...]] = {}
        unresolved: list[str] = []
        for instname, port in net.members:
            cell, golden = _cell_and_golden(instname, instance_cells, catalog)
            if cell is None:
                unresolved.append(f"{instname}.{port} (unknown instance or cell)")
                continue
            if golden is None:
                unresolved.append(
                    f"{instname}.{port} (cell declares no verification.golden_netlist)"
                )
                continue
            key = f"/{port}"
            if key not in golden:
                unresolved.append(
                    f"{instname}.{port} (no {key!r} entry in {cell.key}'s golden_netlist)"
                )
                continue
            offset = offsets.setdefault(instname, _refdes_offset(root, instname))
            member_pins[(instname, port)] = tuple(
                _offset_pin(p, offset) for p in golden[key]
            )

        if unresolved:
            diagnostics.append(
                f"net {net.name!r}: cannot resolve member(s) to golden-netlist pins: "
                + "; ".join(unresolved)
            )
            continue

        checked += 1
        expected = {p for pins in member_pins.values() for p in pins}
        if not expected:
            diagnostics.append(f"net {net.name!r}: resolved to zero expected pins")
            continue

        dangling = sorted(p for p in expected if p not in pin_index)
        if dangling:
            diagnostics.append(
                f"net {net.name!r}: pin(s) {dangling} not found in the exported netlist"
            )
            continue

        common: set[str] | None = None
        for p in expected:
            common = pin_index[p] if common is None else (common & pin_index[p])
        if not common:
            per_member = {
                f"{i}.{p}": sorted(
                    {n for pin in pins for n in pin_index.get(pin, set())}
                )
                for (i, p), pins in member_pins.items()
            }
            diagnostics.append(
                f"net {net.name!r}: member(s) are not mutually connected in the "
                f"exported netlist: {per_member}"
            )
            continue

        actual = exported[sorted(common)[0]]
        missing = sorted(expected - set(actual))
        if missing:  # pragma: no cover - implied unreachable by the intersection above
            diagnostics.append(f"net {net.name!r}: missing pin(s) {missing}")

    if diagnostics:
        return GateResult.failed(name, *diagnostics)
    return GateResult.passed(
        name, f"{checked} plan net(s) verified connected in the exported netlist"
    )


def plan_to_dict(plan: WiringPlan, instance_cells: Mapping[str, str]) -> dict[str, Any]:
    """Serialize just enough of *plan* (+ its instance -> cell_key map) for
    :func:`plan_from_dict` to reconstruct what :func:`design_netlist_gate`
    needs — used to persist ``wiring_plan.json`` alongside a synthesized
    design so the ``gates --design`` CLI path can check it without re-running
    ``synthesize``.
    """
    return {
        "instances": dict(instance_cells),
        "nets": [
            {"kind": net.kind, "name": net.name, "members": [list(m) for m in net.members]}
            for net in plan.nets
        ],
    }


def plan_from_dict(data: Mapping[str, Any]) -> tuple[WiringPlan, dict[str, str]]:
    """Inverse of :func:`plan_to_dict`. The reconstructed :class:`WiringPlan`
    carries only ``nets`` (``driven``/diagnostics/residual fields are not
    needed by :func:`design_netlist_gate` and are reset to their trivial
    defaults).
    """
    nets = tuple(
        Net(
            kind=n["kind"],
            name=n["name"],
            driven=True,
            members=tuple((m[0], m[1]) for m in n["members"]),
        )
        for n in data["nets"]
    )
    plan = WiringPlan(nets=nets, diagnostics=(), unwired_signal_ports=())
    return plan, dict(data["instances"])
