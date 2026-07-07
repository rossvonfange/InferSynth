"""Subsystem-cell tier — recognize interface-bounded segments (not just promote).

The small-cell recognizer (:mod:`infersynth.recognize.recognizer`) matches a
segment by **exact anchored subgraph isomorphism** against a golden fragment.
That is right for a fixed analog cell (an inverting-gain opamp is always the
same three components wired the same way) but wrong for a *subsystem*: a DDR
bus, an I2C bus, an LED bank have **variable width and instance count**. Two
LED banks with 4 vs 8 LEDs are the same *kind* of thing; no single golden
subgraph captures both.

A **subsystem cell** therefore recognizes by **interface + composition**, not by
subgraph shape (docs/HIERARCHICAL_RECOGNITION.md "Subsystem-cell tier"):

* an **interface signature** — the boundary bundle kind (``i2c``, ``led``,
  ``diff_pair`` …) and an allowed width range ``[min_width, max_width]``; this
  is exactly the :class:`~infersynth.recognize.segment.Segment`'s
  ``interface_kind`` + how many interface nets cross its boundary, and
* a **composition** — the member component-*classes* and their allowed counts
  (ranges), e.g. an I2C bus = 1-4 pull-up ``R`` + 1-8 device ``U``.

A segment matches a subsystem cell iff (1) its ``interface_kind`` equals the
cell's interface kind, (2) its boundary width is in ``[min_width, max_width]``,
and (3) for every composition rule the count of that component class among the
segment's members is in the rule's range. This is the segmenter's own output
"read forwards": the segment carries exactly the interface boundary and member
set the matcher tests, so a segment promoted on one board becomes a subsystem
cell recognized on the next — the foundry loop.

Params are recovered structurally: the interface width is the boundary width;
when the cell names a ``golden_ref_cell`` (an existing small cell that is the
subsystem's electrical core, e.g. ``i2c-pullups`` for an I2C bus) the member
resistor values are inverted through that cell's bindings via the EXISTING
:func:`infersynth.recognize.invert.invert_params` to recover, e.g., the pull-up
value. Everything here is pure + deterministic (SELECTION.md §8): no clocks, no
randomness, ties broken by the tightest-ranges-then-lexical-name rule.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from infersynth.recognize.netlist import ref_class

if TYPE_CHECKING:
    from infersynth.catalog import Catalog
    from infersynth.recognize.netlist import DesignNetlist
    from infersynth.recognize.segment import Segment

__all__ = [
    "SCHEMA",
    "InterfaceSig",
    "CompositionRule",
    "SubsystemCell",
    "SubsystemMatch",
    "SubsystemCellError",
    "load_subsystem_cell",
    "load_subsystem_cells",
    "match_subsystem",
    "match_subsystems",
]

SCHEMA = "infersynth.recognize.subsystem/v0"

_KNOWN_KEYS = {
    "kind",
    "name",
    "interface",
    "composition",
    "anchor",
    "params_stuffable",
    "verification",
    "description",
}
_INTERFACE_KEYS = {"kind", "min_width", "max_width"}
_RULE_KEYS = {"class", "role", "count"}


class SubsystemCellError(ValueError):
    """Raised when a subsystem-cell package fails validation."""

    def __init__(self, diagnostics: list[str]) -> None:
        self.diagnostics = list(diagnostics)
        super().__init__(
            "subsystem-cell validation failed with {} error(s):\n{}".format(
                len(diagnostics), "\n".join(f"  - {d}" for d in diagnostics)
            )
        )


@dataclass(frozen=True)
class InterfaceSig:
    """The boundary signature: interface *kind* + allowed width range."""

    kind: str
    min_width: int
    max_width: int


@dataclass(frozen=True)
class CompositionRule:
    """One member-class requirement: *cls* components, count in ``[lo, hi]``.

    ``role`` is a human hint (``pullup``, ``device``, ``series``) — it is NOT
    verified against the netlist (ref designators carry class, not role), it
    documents intent and drives param recovery (which class is the pull-up)."""

    cls: str
    role: str
    lo: int
    hi: int


@dataclass(frozen=True)
class SubsystemCell:
    """An interface+composition subsystem cell (kind: subsystem)."""

    name: str
    interface: InterfaceSig
    composition: tuple[CompositionRule, ...]
    anchor_mpn_patterns: tuple[str, ...] = ()
    params_stuffable: tuple[str, ...] = ()
    golden_ref_cell: str | None = None
    description: str = ""
    path: Path | None = None

    @property
    def specificity(self) -> int:
        """Total slack across all ranges (lower = more specific). The
        deterministic tie-break key when several cells match one segment:
        tightest ranges first, then lexical name."""
        span = self.interface.max_width - self.interface.min_width
        for rule in self.composition:
            span += rule.hi - rule.lo
        return span


@dataclass(frozen=True)
class SubsystemMatch:
    """One segment recognized as one subsystem cell."""

    cell_name: str
    segment_id: str
    interface_kind: str
    width: int
    member_counts: dict[str, int]
    params: dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "cell_name": self.cell_name,
            "segment_id": self.segment_id,
            "interface_kind": self.interface_kind,
            "width": self.width,
            "member_counts": {k: self.member_counts[k] for k in sorted(self.member_counts)},
            "params": {k: self.params[k] for k in sorted(self.params)},
            "confidence": self.confidence,
        }


# --------------------------------------------------------------------------- #
# Loading + validation                                                        #
# --------------------------------------------------------------------------- #
def _validate_range(value: Any, where: str, diags: list[str]) -> tuple[int, int] | None:
    if not (isinstance(value, (list, tuple)) and len(value) == 2):
        diags.append(f"{where} must be a two-element [lo, hi] list")
        return None
    lo, hi = value
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in (lo, hi)):
        diags.append(f"{where} bounds must be integers")
        return None
    if lo < 0 or hi < lo:
        diags.append(f"{where}: need 0 <= lo <= hi, got [{lo}, {hi}]")
        return None
    return int(lo), int(hi)


def load_subsystem_cell(cell_dir: str | Path) -> SubsystemCell:
    """Load + validate one ``<dir>/subsystem_cell.yaml`` package.

    Raises :class:`SubsystemCellError` with all collected diagnostics."""
    import yaml

    path = Path(cell_dir)
    diags: list[str] = []
    yaml_path = path / "subsystem_cell.yaml"
    if not yaml_path.is_file():
        raise SubsystemCellError([f"{path.name}: missing subsystem_cell.yaml"])
    try:
        data = yaml.safe_load(yaml_path.read_text())
    except yaml.YAMLError as exc:
        raise SubsystemCellError([f"{path.name}/subsystem_cell.yaml: invalid YAML: {exc}"]) from exc
    if not isinstance(data, dict):
        raise SubsystemCellError([f"{path.name}/subsystem_cell.yaml: must be a mapping"])

    pfx = f"{path.name}/subsystem_cell.yaml"
    unknown = sorted(set(data) - _KNOWN_KEYS)
    if unknown:
        diags.append(f"{pfx}: unknown key(s) {unknown} (known: {sorted(_KNOWN_KEYS)})")
    if data.get("kind") != "subsystem":
        diags.append(f"{pfx}: kind must be 'subsystem', got {data.get('kind')!r}")
    name = data.get("name")
    if not isinstance(name, str) or not name:
        diags.append(f"{pfx}: name is required and must be a non-empty string")

    iface_raw = data.get("interface")
    interface: InterfaceSig | None = None
    if not isinstance(iface_raw, dict):
        diags.append(f"{pfx}: interface is required and must be a mapping")
    else:
        iunknown = sorted(set(iface_raw) - _INTERFACE_KEYS)
        if iunknown:
            diags.append(f"{pfx}: interface: unknown key(s) {iunknown}")
        ikind = iface_raw.get("kind")
        if not isinstance(ikind, str) or not ikind:
            diags.append(f"{pfx}: interface.kind is required and must be a non-empty string")
        rng = _validate_range(
            [iface_raw.get("min_width"), iface_raw.get("max_width")],
            f"{pfx}: interface [min_width, max_width]",
            diags,
        )
        if isinstance(ikind, str) and ikind and rng is not None:
            interface = InterfaceSig(kind=ikind, min_width=rng[0], max_width=rng[1])

    comp_raw = data.get("composition")
    composition: list[CompositionRule] = []
    if comp_raw is None:
        comp_raw = []
    if not isinstance(comp_raw, list):
        diags.append(f"{pfx}: composition must be a list of rules")
        comp_raw = []
    for i, rule in enumerate(comp_raw):
        where = f"{pfx}: composition[{i}]"
        if not isinstance(rule, dict):
            diags.append(f"{where} must be a mapping")
            continue
        runknown = sorted(set(rule) - _RULE_KEYS)
        if runknown:
            diags.append(f"{where}: unknown key(s) {runknown}")
        cls = rule.get("class")
        if not isinstance(cls, str) or not cls:
            diags.append(f"{where}.class is required and must be a non-empty string")
            cls = None
        rng = _validate_range(rule.get("count"), f"{where}.count", diags)
        role = rule.get("role", "")
        if cls is not None and rng is not None:
            composition.append(CompositionRule(cls=cls, role=str(role), lo=rng[0], hi=rng[1]))

    anchor = data.get("anchor")
    mpn_patterns: tuple[str, ...] = ()
    if anchor is not None:
        if not isinstance(anchor, dict):
            diags.append(f"{pfx}: anchor must be a mapping or null")
        else:
            pats = anchor.get("mpn_patterns")
            if pats is not None:
                if not (isinstance(pats, list) and all(isinstance(p, str) for p in pats)):
                    diags.append(f"{pfx}: anchor.mpn_patterns must be a list of strings")
                else:
                    mpn_patterns = tuple(pats)

    stuffable = data.get("params_stuffable") or []
    if not (isinstance(stuffable, list) and all(isinstance(s, str) for s in stuffable)):
        diags.append(f"{pfx}: params_stuffable must be a list of strings")
        stuffable = []

    golden_ref: str | None = None
    verification = data.get("verification")
    if verification is not None:
        if not isinstance(verification, dict):
            diags.append(f"{pfx}: verification must be a mapping or null")
        else:
            gr = verification.get("golden_ref_cell")
            if gr is not None and (not isinstance(gr, str) or not gr):
                diags.append(f"{pfx}: verification.golden_ref_cell must be a non-empty string")
            elif isinstance(gr, str):
                golden_ref = gr

    if diags:
        raise SubsystemCellError(sorted(diags))

    assert interface is not None  # guaranteed: no diags means it was built
    return SubsystemCell(
        name=str(name),
        interface=interface,
        composition=tuple(composition),
        anchor_mpn_patterns=mpn_patterns,
        params_stuffable=tuple(stuffable),
        golden_ref_cell=golden_ref,
        description=str(data.get("description", "")),
        path=path,
    )


def load_subsystem_cells(subsystems_dir: str | Path) -> dict[str, SubsystemCell]:
    """Load every ``<subsystems_dir>/<cell>/subsystem_cell.yaml`` package.

    Returns ``name -> SubsystemCell`` in sorted-name order. Raises
    :class:`SubsystemCellError` collecting every package's diagnostics (plus a
    duplicate-name check)."""
    root = Path(subsystems_dir)
    if not root.is_dir():
        raise SubsystemCellError([f"{root}: not a directory"])
    diags: list[str] = []
    cells: dict[str, SubsystemCell] = {}
    for entry in sorted(p for p in root.iterdir() if p.is_dir()):
        try:
            cell = load_subsystem_cell(entry)
        except SubsystemCellError as exc:
            diags.extend(exc.diagnostics)
            continue
        if cell.name in cells:
            diags.append(f"duplicate subsystem cell name {cell.name!r}")
            continue
        cells[cell.name] = cell
    if diags:
        raise SubsystemCellError(sorted(diags))
    return dict(sorted(cells.items()))


# --------------------------------------------------------------------------- #
# Matching                                                                    #
# --------------------------------------------------------------------------- #
def _boundary_width(segment: Segment, kind: str) -> int:
    """Number of distinct interface nets of *kind* crossing the boundary."""
    return len({b.net for b in segment.boundary if b.interface_kind == kind})


def _member_counts(segment: Segment) -> dict[str, int]:
    return dict(Counter(ref_class(r) for r in segment.component_refs))


def _recover_params(
    cell: SubsystemCell,
    segment: Segment,
    design: DesignNetlist,
    width: int,
    catalog: Catalog | None,
) -> dict[str, float]:
    """Structural param recovery: interface width + (if a golden_ref_cell is
    named and the catalog is available) member-resistor values inverted through
    that cell's bindings via the existing :func:`invert_params`."""
    params: dict[str, float] = {"interface_width": float(width)}
    if cell.golden_ref_cell is None or catalog is None:
        return params
    try:
        golden = catalog.get(cell.golden_ref_cell)
    except Exception:  # noqa: BLE001 — an absent golden cell just skips recovery
        return params
    # observe the segment's resistor member values, mapped onto the golden
    # cell's resistor refs (all pull-ups bind the same param, so which R maps
    # to which golden R is immaterial — sorted-to-sorted is deterministic).
    r_values = sorted(
        v
        for r in segment.component_refs
        if ref_class(r) == "R" and (v := design.components[r].value) is not None
    )
    golden_r_refs = sorted(ref for ref in golden.bindings if ref_class(ref) == "R")
    observed = {gref: val for gref, val in zip(golden_r_refs, r_values, strict=False)}
    if not observed:
        return params
    from infersynth.recognize.invert import invert_params

    inv = invert_params(golden, observed)
    for pname in sorted(inv.params):
        if pname not in inv.unresolved:
            params[pname] = inv.params[pname]
    return params


def _confidence(cell: SubsystemCell, width: int, counts: dict[str, int]) -> float:
    """How tightly the segment fits the cell (1.0 = every dimension pinned to a
    single point). Mean over the width dimension + each composition rule of
    ``1/(1+slack)`` where slack is how far the observed value sits from a
    single-point range."""
    scores: list[float] = []
    scores.append(1.0 / (1.0 + (cell.interface.max_width - cell.interface.min_width)))
    for rule in cell.composition:
        scores.append(1.0 / (1.0 + (rule.hi - rule.lo)))
    return sum(scores) / len(scores) if scores else 1.0


def match_subsystem(
    segment: Segment,
    cell: SubsystemCell,
    design: DesignNetlist,
    *,
    catalog: Catalog | None = None,
) -> SubsystemMatch | None:
    """Does *segment* match subsystem *cell*? Returns a :class:`SubsystemMatch`
    or ``None``. Pure + deterministic.

    A match requires (1) ``segment.interface_kind == cell.interface.kind``,
    (2) boundary width in ``[min_width, max_width]``, (3) every composition
    rule's class count in range, and (4) — when the cell declares
    ``anchor.mpn_patterns`` — at least one member MPN containing one pattern."""
    if segment.interface_kind != cell.interface.kind:
        return None
    width = _boundary_width(segment, cell.interface.kind)
    if not (cell.interface.min_width <= width <= cell.interface.max_width):
        return None
    counts = _member_counts(segment)
    for rule in cell.composition:
        if not (rule.lo <= counts.get(rule.cls, 0) <= rule.hi):
            return None
    if cell.anchor_mpn_patterns:
        mpns = [design.components[r].mpn for r in segment.component_refs if r in design.components]
        if not any(pat in m for m in mpns for pat in cell.anchor_mpn_patterns if m):
            return None
    params = _recover_params(cell, segment, design, width, catalog)
    return SubsystemMatch(
        cell_name=cell.name,
        segment_id=segment.id,
        interface_kind=cell.interface.kind,
        width=width,
        member_counts=counts,
        params=params,
        confidence=_confidence(cell, width, counts),
    )


def match_subsystems(
    segment: Segment,
    cells: dict[str, SubsystemCell] | list[SubsystemCell],
    design: DesignNetlist,
    *,
    catalog: Catalog | None = None,
) -> SubsystemMatch | None:
    """Best subsystem match for *segment* among *cells*, or ``None``.

    Deterministic tie-break when several cells match: most-specific
    (tightest total range slack) first, then lexical cell name."""
    seq = list(cells.values()) if isinstance(cells, dict) else list(cells)
    matches: list[tuple[int, str, SubsystemMatch]] = []
    for cell in seq:
        m = match_subsystem(segment, cell, design, catalog=catalog)
        if m is not None:
            matches.append((cell.specificity, cell.name, m))
    if not matches:
        return None
    matches.sort(key=lambda t: (t[0], t[1]))
    return matches[0][2]
