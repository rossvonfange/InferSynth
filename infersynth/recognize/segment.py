"""Netlist segmenter — recognition's pre-pass (docs/HIERARCHICAL_RECOGNITION.md).

Flat recognition finds nothing on a real vendor board: it matches small analog
cells against a 562-component graph as one blob (the PolarFire ``.brd`` imported
to 562 comps / 471 nets and recognized 0 cells, correctly). A board's functional
blocks — a DDR interface, an LCD header, an IO bank, a power tree — are larger,
*interface-bounded* clusters, not small cells. This module partitions the design
netlist along protocol/interface boundaries into cell-sized clusters so the
existing recognizer (:mod:`infersynth.recognize.recognizer`) can actually fire
on each clean segment (:mod:`infersynth.recognize.hierarchical`).

This is the catalog's ``interfaces.yaml`` run **in reverse**: the same
"catalog reversed" logic that put the cell recognizer here. Forward, a cell
groups its ports into interface bundles (NETFLOW.md); backward, a bundle of
nets matching an interface signature is a natural graph *cut-line* — dense
local connectivity lives *inside* a cluster, a labeled bus bundle crosses its
boundary. It is recon's spanning-tree/absorption idea run backwards: grow
clusters by local connectivity until an interface bundle is the frontier.

Algorithm (all constants documented below, all ties broken lexicographically —
two runs are byte-identical, SELECTION.md §8):

1. **Classify every net** (:func:`classify_nets`) into

   * ``rail`` — a power/ground net (name matches a power/voltage pattern) OR a
     very-high-degree net (>= :data:`RAIL_DEGREE`). Rails are NOT clustering
     edges: a power net touches half the board, so treating it as a binding
     edge would merge the whole board into one cluster. Rails are cut lines.
   * ``interface`` — a net belonging to a detected interface bundle: an indexed
     bus (>= :data:`BUS_MIN_WIDTH` nets sharing a meaningful prefix, e.g.
     ``MSS_DDR4_DQ0..DQ31``), a differential pair (``X_P``/``X_N``), or an
     ``interfaces.yaml`` role signature (i2c ``SDA``+``SCL``, spi
     ``SCLK``+``MOSI``+``MISO``, ...). Interface bundles are the CUT the whole
     segmenter exists to find.
   * ``local`` — everything else: an ordinary short signal net that binds the
     components on it into a cluster.

2. **Score locality.** A local net of degree ``d`` (distinct components) has
   weight ``1/(d-1)`` — a dedicated 2-pin net binds tightly (1.0); a net
   fanning to many parts binds weakly. The edge weight between two components is
   the sum of the weights of the local nets they share.

3. **Anchors.** A component whose *local* degree is >= :data:`ANCHOR_DEGREE` is
   a subsystem *core* (an IC, a connector, a hub). Two anchors are two distinct
   subsystems: they must never fall in one cluster (that is exactly the
   FPGA-plus-PHY-plus-connectors chaining single-linkage would produce).

4. **Agglomerate.** Process component-pair edges in ``(-weight, ref_a, ref_b)``
   order; union a pair iff its weight >= :data:`MERGE_THRESHOLD` **and** the
   merge would not place two anchors in one cluster. Density grows each cluster;
   the at-most-one-anchor rule and the excluded rail/interface nets stop the
   frontier. A cluster of >= 2 components (or one containing an anchor) is a
   :class:`Segment`; leftover singletons are the ``residual``.

5. **Boundary + kind.** A segment's boundary pins are its components' pins that
   sit on a rail or interface net (a pin leaving the cluster); an interface
   boundary pin carries the ``interface_kind`` of its bundle. The segment's
   ``interface_kind`` is the dominant kind over its interface boundary pins.

6. **Absorb rail-attached passives (post-clustering).** After agglomeration, a
   residual singleton that is a 2-pin passive (:data:`PASSIVE_CLASSES` — C, R,
   L, FB, by refdes prefix) with exactly one pin on a rail net and one pin on a
   non-rail net is folded into the segment that owns the non-rail net's other
   pins (its decoupling target), by pin-count-on-that-net then lowest segment
   id. This is pure enrichment: it only appends to a segment's
   ``component_refs`` and records an ``absorbed`` note in its ``provenance`` —
   it never creates or merges segments and never moves an anchor. A passive
   whose both pins sit on rails (a bulk cap strung between two power rails, no
   signal net) has no owner; it stays residual but is tagged ``rail-only`` in
   :attr:`SegmentationResult.residual_tags` so it reads as *explained*
   residual, not silently-dropped glue (the determinism/honesty invariants in
   docs/HIERARCHICAL_RECOGNITION.md still apply: two runs byte-identical,
   nothing hidden). Toggle with ``segment(..., absorb_passives=False)``.

``labels`` (Contract 1 :class:`LabelClaim`) are HINTS ONLY — connectivity is
truth. A ``block`` label adds a bonus to the *existing* local-net edges among
its refs (biasing a borderline passive toward the labeled cluster) but never
creates an edge across a rail/interface and never overrides the
at-most-one-anchor rule; a ``net_label``/``protocol`` label may mark a net as an
interface cut. With ``labels=None`` the segmenter is pure connectivity — the v0
baseline; label-guided is the refinement.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from infersynth.catalog import Catalog
from infersynth.recognize.netlist import DesignNetlist, ref_class

__all__ = [
    "SCHEMA",
    "RAIL_DEGREE",
    "ANCHOR_DEGREE",
    "MERGE_THRESHOLD",
    "BUS_MIN_WIDTH",
    "PASSIVE_CLASSES",
    "LabelClaim",
    "BoundaryPin",
    "Segment",
    "SegmentationResult",
    "NetClass",
    "classify_nets",
    "segment",
]

SCHEMA = "infersynth.recognize.segment/v0"

#: A net touching at least this many distinct components is a rail (a power /
#: ground / broadcast net), never a clustering edge. Calibrated on the PolarFire
#: board: its named rails (GND=361, 3P3V=75, ...) and every >=8-way net are
#: broadcast nets; real local signal nets on that board are <= 5-way.
RAIL_DEGREE = 8

#: A component with at least this many *local* nets is a subsystem core
#: (anchor). Two anchors never share a cluster. 4 keeps ICs/connectors as cores
#: while leaving 2-3-pin passive networks free to attach to one.
ANCHOR_DEGREE = 4

#: Minimum edge weight (summed local-net locality) to merge two components. A
#: dedicated 2-pin net is 1.0; a single 3-way node (a small analog cell's
#: feedback/summing node, e.g. an opamp gain leg's R-R-U junction) is 0.5. 0.5
#: therefore keeps those cells intact while a lone >=4-way fan-out net (weight
#: <= 0.33) still does not merge on its own — it needs corroboration, so the
#: frontier stops at wide signal nets and chaining is resisted.
MERGE_THRESHOLD = 0.5

#: Minimum member count for an indexed group of nets to be treated as one bus
#: interface bundle (e.g. a >=4-bit address/data bus).
BUS_MIN_WIDTH = 4

#: Refdes-prefix classes eligible for the post-clustering rail-attached-passive
#: absorption pass: simple 2-pin passives (capacitor, resistor, inductor,
#: ferrite bead). Footprint/size agnostic — refdes-prefix based, same
#: convention :func:`infersynth.recognize.netlist.ref_class` already uses.
PASSIVE_CLASSES = frozenset({"C", "R", "L", "FB"})

#: Extra edge weight added between two components that share a ``block`` label
#: and already share a local net — a hint that biases a borderline assignment
#: toward the labeled cluster without creating connectivity that isn't there.
LABEL_BONUS = 0.5

# A power/ground net by name (GND, VDDxx, VSS, VTT, VREF, ...) — anchored at a
# token boundary so a signal like ``SD_VDD_EN`` isn't misread as a bare rail.
_POWER_RE = re.compile(
    r"(^|[_/])(GND|VSS|VCC|VDD|VEE|VBUS|VTT|VREF|VCCB|AGND|DGND|PGND)([_/]|\d|$)", re.I
)
# A voltage-named rail: 3P3V, 1P8V, 0P6V_VTT_DDR4, +5V0, 1V2, ...
_VOLT_RE = re.compile(r"(^|[_/])[+-]?\d+P\d+V|\d+V\d+", re.I)
# base + numeric index (1-3 digits) with the base ending in a letter — an indexed
# bus member. Excludes Allegro auto-names like ``N17891139`` (base would be "N").
_BUS_RE = re.compile(r"^(.*[A-Za-z])(\d{1,3})$")
# base + P/N differential suffix.
_DIFF_RE = re.compile(r"^(.*?)[_]?([PN])$")
_TOKEN_SPLIT = re.compile(r"[_/\-]")

# Interface bundle kind derived from a bus prefix. First substring that hits
# wins; falls back to "bus". Small, extend as boards demand.
_BUS_KIND_KEYWORDS = (
    ("DDR", "ddr"),
    ("GPIO", "gpio"),
    ("SDIO", "sdio"),
    ("SD_", "sdio"),
    ("SEG", "display"),
    ("DSP", "display"),
    ("LCD", "display"),
    ("LED", "led"),
    ("DIP", "gpio"),
    ("SW", "gpio"),
    ("ADDR", "ddr"),
)


@dataclass(frozen=True)
class LabelClaim:
    """Contract 1 — a PDF/schematic label the segmenter consumes as a *hint*.

    ``kind`` is ``"net_label"``, ``"block"``, or ``"protocol"``. ``value`` is
    the human string (``"DDR4_DQ0"``, ``"LCD Header"``, ``"I2C"``). ``refs`` are
    the component refs the label scopes (empty for a bare net label). ``net`` is
    the net name for a ``net_label``. Labels never override connectivity.
    """

    kind: str
    value: str
    refs: tuple[str, ...] = ()
    net: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BoundaryPin:
    """One pin of a segment that sits on a rail or interface net (leaves the
    cluster). ``interface_kind`` is set when the net is an interface bundle."""

    ref: str
    pin: str
    net: str
    interface_kind: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"ref": self.ref, "pin": self.pin, "net": self.net}
        if self.interface_kind is not None:
            d["interface_kind"] = self.interface_kind
        return d


@dataclass(frozen=True)
class Segment:
    """Contract 2 — one interface-bounded cluster."""

    id: str
    component_refs: tuple[str, ...]
    internal_nets: tuple[str, ...]
    boundary: tuple[BoundaryPin, ...]
    interface_kind: str | None = None
    #: The ecosystem connector this segment IS, when it contains a recognized
    #: standard connector (``rpi40``/``mikrobus``/``mipi_csi``…). This classifies
    #: the CONNECTOR itself (recognized by pinout fingerprint), independently of
    #: ``interface_kind`` (what is wired behind its pins). See
    #: infersynth/recognize/connectors.py.
    connector_kind: str | None = None
    label: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "component_refs": list(self.component_refs),
            "internal_nets": list(self.internal_nets),
            "boundary": [b.to_dict() for b in self.boundary],
            "interface_kind": self.interface_kind,
            "connector_kind": self.connector_kind,
            "label": self.label,
            "provenance": self.provenance,
        }


@dataclass(frozen=True)
class SegmentationResult:
    """Contract 2 — the segmenter's output.

    ``residual_tags`` maps a residual ref to an explanatory tag (currently only
    ``"rail-only"``, for a 2-pin passive strung between two power rails with no
    signal net — no clear absorption owner). A residual ref absent from
    ``residual_tags`` is genuine, unexplained glue: never hidden, never
    reclassified away. ``absorbed_count`` is how many rail-attached passives
    the post-clustering absorption pass (see module docstring) folded into an
    existing segment's ``component_refs``.
    """

    segments: tuple[Segment, ...] = ()
    residual: tuple[str, ...] = ()
    residual_tags: dict[str, str] = field(default_factory=dict)
    absorbed_count: int = 0

    @property
    def rail_only_residual(self) -> tuple[str, ...]:
        """Residual refs tagged ``rail-only`` — explained, not hidden."""
        return tuple(sorted(r for r, tag in self.residual_tags.items() if tag == "rail-only"))

    def to_dict(self) -> dict[str, Any]:
        sizes = sorted((len(s.component_refs) for s in self.segments), reverse=True)
        return {
            "schema": SCHEMA,
            "summary": {
                "segments": len(self.segments),
                "residual_components": len(self.residual),
                "largest_segment": sizes[0] if sizes else 0,
                "segment_sizes": sizes,
                "absorbed_passives": self.absorbed_count,
                "rail_only_residual": len(self.rail_only_residual),
            },
            "segments": [s.to_dict() for s in self.segments],
            "residual": list(self.residual),
            "residual_tags": dict(sorted(self.residual_tags.items())),
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=False)

    def to_markdown(self) -> str:
        sizes = sorted((len(s.component_refs) for s in self.segments), reverse=True)
        rail_only = self.rail_only_residual
        lines = [
            "# Segmentation report",
            "",
            f"- schema: `{SCHEMA}`",
            f"- segments: **{len(self.segments)}** "
            f"(largest {sizes[0] if sizes else 0} components)",
            f"- residual (unclustered) components: **{len(self.residual)}**",
            f"- rail-attached passives absorbed into segments: **{self.absorbed_count}**",
            f"- rail-only residual passives (tagged, no clear owner): "
            f"**{len(rail_only)}** {list(rail_only)[:8]}{' ...' if len(rail_only) > 8 else ''}",
            "",
            "## Segments",
            "",
        ]
        for s in self.segments:
            kind = f" [{s.interface_kind}]" if s.interface_kind else ""
            label = f" — {s.label}" if s.label else ""
            lines.append(
                f"- **{s.id}**{kind}{label}: {len(s.component_refs)} comps "
                f"{list(s.component_refs)[:8]}{' ...' if len(s.component_refs) > 8 else ''}"
            )
        lines.append("")
        return "\n".join(lines)


@dataclass(frozen=True)
class NetClass:
    """The classification of one net: ``kind`` in {rail, interface, local} and,
    for an interface, its ``interface_kind`` (e.g. ``i2c``, ``ddr``).

    ``confidence`` and ``basis`` describe HOW an interface kind was reached —
    ``"topology"`` (structure alone), ``"name_corroborated"`` (structure + net
    name role hints), ``"name_hint"`` (a wide vendor bus keyed on a bounded
    net-name keyword, e.g. ``ddr``/``led``), ``"label_hint"`` (a demoted PDF net
    label — marks a cut but never names the kind), or ``"generic"`` (an honest
    ``bus``/``signal``/``diff_pair`` fallback). This is what keeps
    ``interface_kind`` structural and label-independent."""

    kind: str
    interface_kind: str | None = None
    confidence: float = 0.0
    basis: str | None = None


def _net_degree(pins: list[tuple[str, str]]) -> int:
    return len({r for r, _ in pins})


def _bus_kind(prefix: str) -> str:
    up = prefix.upper()
    for needle, kind in _BUS_KIND_KEYWORDS:
        if needle in up:
            return kind
    return "bus"


def _detect_bus_bundles(names: list[str]) -> dict[str, str]:
    """Indexed-bus detection: net -> interface_kind for members of any group of
    >= :data:`BUS_MIN_WIDTH` nets sharing a letter-terminated prefix."""
    groups: dict[str, list[str]] = {}
    for name in names:
        m = _BUS_RE.match(name)
        if m:
            groups.setdefault(m.group(1), []).append(name)
    out: dict[str, str] = {}
    for prefix, members in groups.items():
        if len(members) >= BUS_MIN_WIDTH:
            kind = _bus_kind(prefix)
            for name in members:
                out[name] = kind
    return out


def _diff_pair_groups(names: list[str]) -> list[list[str]]:
    """Differential-pair GROUPING: return each ``X_P``/``X_N`` (or ``XP``/``XN``)
    pair (>=2-char base) as a 2-net bundle, sorted deterministically. The KIND is
    NOT decided here — :func:`classify_interface` classifies each bundle
    structurally (usb2/can/pcie/sgmii or a generic ``diff_pair``)."""
    halves: dict[str, dict[str, str]] = {}
    for name in names:
        m = _DIFF_RE.match(name)
        if m and len(m.group(1)) >= 2:
            halves.setdefault(m.group(1), {})[m.group(2)] = name
    groups: list[list[str]] = []
    for base in sorted(halves):
        d = halves[base]
        if "P" in d and "N" in d:
            groups.append(sorted((d["P"], d["N"])))
    return groups


def _protocol_role_groups(names: list[str], catalog: Catalog) -> list[list[str]]:
    """``interfaces.yaml`` role-signature GROUPING: return each net bundle whose
    present role tokens cover an interface's *required* roles, as a sorted list
    of nets. The KIND is NOT taken from the interface name here —
    :func:`classify_interface` classifies each bundle STRUCTURALLY (the role
    tokens only served to find the bundle, and later corroborate). The generic
    single-wire ``analog`` interface is skipped; multi-role interfaces only, so a
    stray token can't forge a bundle. Bundles are de-duplicated (a set of nets is
    emitted once) and returned in sorted order for determinism."""
    tokens_by_net = {n: set(_TOKEN_SPLIT.split(n.upper())) for n in names}
    seen: set[frozenset[str]] = set()
    result: list[list[str]] = []
    for _iface_name, idef in sorted(catalog.interfaces.items()):
        roles = idef.roles
        if len(roles) < 2:
            continue  # skip degenerate single-wire interfaces (analog)
        required = {r for r, rd in roles.items() if not (rd.optional or rd.optional_many)}
        if not required:
            continue
        role_tokens = {r.upper() for r in roles}
        groups: dict[str, dict[str, str]] = {}
        for name in names:
            hit = role_tokens & tokens_by_net[name]
            if not hit:
                continue
            for role_tok in hit:
                base = _TOKEN_SPLIT.sub("_", name.upper()).replace(role_tok, "\x00")
                groups.setdefault(base, {})[role_tok] = name
        for _base, present in groups.items():
            if {r.upper() for r in required} <= set(present):
                bundle = frozenset(present.values())
                if bundle not in seen:
                    seen.add(bundle)
                    result.append(sorted(bundle))
    result.sort()
    return result


def classify_nets(
    design: DesignNetlist, catalog: Catalog, labels: list[LabelClaim] | None = None
) -> dict[str, NetClass]:
    """Classify every net of *design* as rail / interface / local.

    Rails first (a power net that is also part of a bus name stays a rail — a
    rail never binds a cluster). Then interface bundles, whose ``interface_kind``
    comes from STRUCTURE, never from a raw net label:

      * **differential-pair** and **protocol-role** bundles are handed to
        :func:`~infersynth.recognize.interface_signatures.classify_interface`,
        which fingerprints the bundle topologically (cardinality, diff-pair
        count, multidrop vs point-to-point, pull-ups) and emits a standard
        protocol kind (spi/i2c/usb2/pcie/…) or an honest generic
        (``diff_pair``/``bus``/``signal``) — with net-name role tokens only
        CORROBORATING. When the catalog ships no ``interface_signatures.yaml``
        this degrades to a generic ``diff_pair``/``bus`` (never a guess).
      * **indexed vendor buses** keep the bounded net-name keyword kind
        (``ddr``/``sdio``/``led``/…, :func:`_bus_kind`) — these are wide,
        board-specific buses whose standard name legitimately rides on a
        corroborating net-name token, and the vocabulary is small and fixed.

    ``net_label`` / ``protocol`` LABELS are DEMOTED: a label marks its net as an
    interface CUT (so it stops a cluster), but its VALUE never becomes the kind —
    the kind is ``signal`` (generic) unless the net is already in a structurally
    classified bundle. This is the root fix for net-label fragmentation.

    All other nets are local clustering edges.
    """
    from infersynth.recognize.interface_signatures import (
        GENERIC_CONFIDENCE,
        classify_interface,
    )

    names = sorted(design.nets)
    rails = {
        n
        for n in names
        if _POWER_RE.search(n) or _VOLT_RE.search(n) or _net_degree(design.nets[n]) >= RAIL_DEGREE
    }
    non_rail = [n for n in names if n not in rails]
    rail_fs = frozenset(rails)
    sigs = catalog.interface_signatures

    # net -> (kind, confidence, basis); first assignment wins (setdefault),
    # ordered most-specific-first: diff pairs, protocol-role bundles, then buses.
    iface: dict[str, tuple[str, float, str]] = {}

    def _assign(bundle: list[str], kind: str, conf: float, basis: str) -> None:
        for n in bundle:
            iface.setdefault(n, (kind, conf, basis))

    def _classify(bundle: list[str], fallback: str) -> None:
        if sigs:
            m = classify_interface(bundle, design, sigs, rails=rail_fs)
            _assign(bundle, m.kind, m.confidence, m.basis)
        else:
            _assign(bundle, fallback, GENERIC_CONFIDENCE, "generic")

    for group in _diff_pair_groups(non_rail):
        _classify(group, "diff_pair")
    for group in _protocol_role_groups(non_rail, catalog):
        _classify(group, "bus")
    # indexed vendor buses: bounded net-name keyword kind (a corroborating hint,
    # small fixed vocabulary — never a raw net label).
    for name, kind in _detect_bus_bundles(non_rail).items():
        iface.setdefault(name, (kind, GENERIC_CONFIDENCE, "name_hint"))
    # DEMOTED label cut: mark the net as an interface boundary, but the kind is
    # generic ``signal`` — the label VALUE is never promoted to interface_kind.
    for lc in labels or ():
        if lc.kind in ("net_label", "protocol") and lc.net and lc.net not in rails:
            iface.setdefault(lc.net, ("signal", GENERIC_CONFIDENCE, "label_hint"))

    out: dict[str, NetClass] = {}
    for n in names:
        if n in rails:
            out[n] = NetClass("rail")
        elif n in iface:
            kind, conf, basis = iface[n]
            out[n] = NetClass("interface", kind, confidence=conf, basis=basis)
        else:
            out[n] = NetClass("local")
    return out


class _Union:
    """Deterministic union-find keyed by comparable refs (smaller ref = root),
    tracking an anchor count per root so two anchors never merge."""

    def __init__(self, refs: list[str], anchors: set[str]) -> None:
        self.parent = {r: r for r in refs}
        self.anchor_count = {r: (1 if r in anchors else 0) for r in refs}

    def find(self, x: str) -> str:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def anchors_of(self, x: str) -> int:
        return self.anchor_count[self.find(x)]

    def union(self, a: str, b: str) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        if self.anchor_count[ra] + self.anchor_count[rb] >= 2:
            return False  # two subsystem cores — this is a boundary, not a merge
        root, other = (ra, rb) if ra < rb else (rb, ra)
        self.parent[other] = root
        self.anchor_count[root] = self.anchor_count[ra] + self.anchor_count[rb]
        return True


def _component_pins(design: DesignNetlist) -> dict[str, list[tuple[str, str]]]:
    """ref -> sorted ``(pin, net)`` list, built deterministically from
    ``design.nets`` (net name order, then pin order) — mirrors the traversal
    :func:`_build_segment` already uses."""
    out: dict[str, list[tuple[str, str]]] = {}
    for name, pins in sorted(design.nets.items()):
        for ref, pin in sorted(pins):
            out.setdefault(ref, []).append((pin, name))
    return out


def _absorb_rail_passives(
    segments: list[Segment],
    residual: tuple[str, ...],
    design: DesignNetlist,
    netcls: dict[str, NetClass],
) -> tuple[list[Segment], tuple[str, ...], dict[str, str], int]:
    """Post-clustering absorption pass (module docstring, step 6).

    A residual 2-pin passive (:data:`PASSIVE_CLASSES`) with one pin on a rail
    net and one pin on a non-rail net is folded into the segment that owns the
    most pins on that non-rail net (ties broken by lowest segment id/index —
    segment ids are assigned in that same order). A passive with both pins on
    rails is left residual but tagged ``"rail-only"``. Everything else (a
    non-passive, a >2-pin part, or a passive with zero or two non-rail pins) is
    left as ordinary, unexplained residual. Pure + deterministic: only reads
    ``residual``'s membership at entry, never re-derives ownership from
    already-absorbed refs.
    """
    comp_pins = _component_pins(design)
    seg_of: dict[str, int] = {
        r: idx for idx, seg in enumerate(segments) for r in seg.component_refs
    }

    absorbed_by_seg: dict[int, list[str]] = {}
    tags: dict[str, str] = {}
    still_residual: list[str] = []

    for ref in residual:  # residual is already sorted
        pins = comp_pins.get(ref, [])
        if ref_class(ref) not in PASSIVE_CLASSES or len(pins) != 2:
            still_residual.append(ref)
            continue
        nets = [n for _pin, n in pins]
        is_rail = [netcls[n].kind == "rail" for n in nets]
        if all(is_rail):
            tags[ref] = "rail-only"
            still_residual.append(ref)
            continue
        if not any(is_rail):
            still_residual.append(ref)  # no rail pin at all: not this pass's target
            continue
        local_net = nets[1] if is_rail[0] else nets[0]
        pin_counts: dict[int, int] = {}
        for other_ref, _pin in design.nets.get(local_net, ()):
            if other_ref == ref:
                continue
            sidx = seg_of.get(other_ref)
            if sidx is not None:
                pin_counts[sidx] = pin_counts.get(sidx, 0) + 1
        if not pin_counts:
            still_residual.append(ref)  # no owner on the non-rail net: no clear target
            continue
        owner = min(pin_counts, key=lambda i: (-pin_counts[i], i))
        absorbed_by_seg.setdefault(owner, []).append(ref)

    if not absorbed_by_seg:
        return segments, tuple(sorted(still_residual)), tags, 0

    new_segments: list[Segment] = []
    absorbed_count = 0
    for idx, seg in enumerate(segments):
        extra = absorbed_by_seg.get(idx)
        if not extra:
            new_segments.append(seg)
            continue
        extra_sorted = sorted(extra)
        absorbed_count += len(extra_sorted)
        new_prov = dict(seg.provenance)
        new_prov["absorbed"] = extra_sorted
        new_segments.append(
            Segment(
                id=seg.id,
                component_refs=tuple(sorted((*seg.component_refs, *extra_sorted))),
                internal_nets=seg.internal_nets,
                boundary=seg.boundary,
                interface_kind=seg.interface_kind,
                label=seg.label,
                provenance=new_prov,
            )
        )
    return new_segments, tuple(sorted(still_residual)), tags, absorbed_count


def segment(
    design: DesignNetlist,
    catalog: Catalog,
    *,
    labels: list[LabelClaim] | None = None,
    absorb_passives: bool = True,
) -> SegmentationResult:
    """Partition *design* into interface-bounded segments. Pure + deterministic.

    See the module docstring for the algorithm. ``labels`` are hints only; with
    ``labels=None`` the result is pure connectivity (the v0 baseline).
    ``absorb_passives`` (default ``True``) runs the post-clustering
    rail-attached-passive absorption pass (module docstring, step 6); pass
    ``False`` to get the pre-absorption behavior back.
    """
    netcls = classify_nets(design, catalog, labels)
    refs = sorted(design.components)

    # local-net incidence + per-component local degree
    local_nets = {
        n: sorted({r for r, _ in design.nets[n]})
        for n in design.nets
        if netcls[n].kind == "local"
    }
    local_degree: dict[str, int] = dict.fromkeys(refs, 0)
    for members in local_nets.values():
        for r in members:
            local_degree[r] += 1
    anchors = {r for r in refs if local_degree[r] >= ANCHOR_DEGREE}

    # component-pair edge weights over shared local nets (locality-weighted)
    edge: dict[tuple[str, str], float] = {}
    for members in local_nets.values():
        d = len(members)
        if d < 2:
            continue
        w = 1.0 / (d - 1)
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                key = (members[i], members[j])
                edge[key] = edge.get(key, 0.0) + w

    # label bonus: strengthen existing local-net edges inside a block label
    block_refs: list[set[str]] = [
        set(lc.refs) for lc in (labels or ()) if lc.kind == "block" and len(lc.refs) >= 2
    ]
    if block_refs:
        for key in list(edge):
            a, b = key
            if any(a in s and b in s for s in block_refs):
                edge[key] += LABEL_BONUS

    uf = _Union(refs, anchors)
    for (a, b), w in sorted(edge.items(), key=lambda kv: (-kv[1], kv[0])):
        if w < MERGE_THRESHOLD:
            break
        uf.union(a, b)

    # gather clusters
    clusters: dict[str, list[str]] = {}
    for r in refs:
        clusters.setdefault(uf.find(r), []).append(r)

    block_label_of = _block_label_index(labels)
    seg_members = [
        sorted(members)
        for members in clusters.values()
        if len(members) >= 2 or any(m in anchors for m in members)
    ]
    seg_members.sort(key=lambda members: members)

    segments: list[Segment] = []
    clustered: set[str] = set()
    for idx, members in enumerate(seg_members):
        clustered.update(members)
        segments.append(
            _build_segment(f"seg-{idx:03d}", members, design, netcls, anchors, block_label_of)
        )
    residual = tuple(sorted(r for r in refs if r not in clustered))

    residual_tags: dict[str, str] = {}
    absorbed_count = 0
    if absorb_passives:
        segments, residual, residual_tags, absorbed_count = _absorb_rail_passives(
            segments, residual, design, netcls
        )

    if catalog.connectors:
        segments = _apply_connectors(segments, design, catalog)

    return SegmentationResult(
        segments=tuple(segments),
        residual=residual,
        residual_tags=residual_tags,
        absorbed_count=absorbed_count,
    )


#: interface_kind values a connector match is allowed to FILL (nothing more
#: specific is known). A protocol/vendor-bus kind already present describes what
#: is wired behind the connector and is kept; ``connector_kind`` is recorded
#: alongside either way (see :func:`_apply_connectors`).
_GENERIC_FILLABLE_KINDS = frozenset({None, "bus", "signal"})


def _apply_connectors(
    segments: list[Segment], design: DesignNetlist, catalog: Catalog
) -> list[Segment]:
    """Recognize standard ecosystem connectors in each segment and stamp
    ``connector_kind`` (rpi40/mikrobus/mipi_csi/…), recognized by PINOUT
    fingerprint — pin count + footprint + standardized pin->function net-name map
    (:func:`infersynth.recognize.connectors.classify_connector`).

    A connector classifies the CONNECTOR itself, a distinct axis from
    ``interface_kind`` (what is wired behind its pins). When a segment carries no
    more-specific interface kind (``None``/``bus``/``signal``), the connector
    kind FILLS ``interface_kind`` so the connector surfaces there too; when a
    protocol/vendor-bus kind is already present, that is kept (it describes the
    wiring) and the connector is still recorded in ``connector_kind``.

    Pure + deterministic: connector-sized components (>= 4 connected pins) are
    considered in sorted-ref order; the best match per segment wins by
    (confidence desc, kind asc). Never changes membership or any other field.
    """
    from infersynth.recognize.connectors import classify_connector

    # one pass over nets -> per-ref connected pin count, so we only run the
    # (rescanning) classifier on connector-sized components.
    pin_count: dict[str, set[str]] = {}
    for _name, pins in design.nets.items():
        for ref, pin in pins:
            pin_count.setdefault(ref, set()).add(pin)
    connector_sized = {ref for ref, ps in pin_count.items() if len(ps) >= 4}

    out: list[Segment] = []
    for seg in segments:
        best: tuple[float, str, str] | None = None  # (confidence, kind, ref)
        for ref in sorted(seg.component_refs):
            if ref not in connector_sized:
                continue
            m = classify_connector(ref, design, catalog.connectors)
            if m.kind is None:
                continue
            cand = (m.confidence, m.kind, ref)
            if best is None or (-cand[0], cand[1], cand[2]) < (-best[0], best[1], best[2]):
                best = cand
        if best is None:
            out.append(seg)
            continue
        conf, ckind, cref = best
        new_iface = (
            ckind if seg.interface_kind in _GENERIC_FILLABLE_KINDS else seg.interface_kind
        )
        new_prov = dict(seg.provenance)
        new_prov["connector"] = {
            "kind": ckind,
            "ref": cref,
            "confidence": conf,
            "filled_interface_kind": new_iface != seg.interface_kind,
        }
        out.append(
            Segment(
                id=seg.id,
                component_refs=seg.component_refs,
                internal_nets=seg.internal_nets,
                boundary=seg.boundary,
                interface_kind=new_iface,
                connector_kind=ckind,
                label=seg.label,
                provenance=new_prov,
            )
        )
    return out


def _block_label_index(labels: list[LabelClaim] | None) -> dict[frozenset[str], str]:
    out: dict[frozenset[str], str] = {}
    for lc in labels or ():
        if lc.kind == "block" and lc.refs:
            out[frozenset(lc.refs)] = lc.value
    return out


def _build_segment(
    seg_id: str,
    members: list[str],
    design: DesignNetlist,
    netcls: dict[str, NetClass],
    anchors: set[str],
    block_label_of: dict[frozenset[str], str],
) -> Segment:
    member_set = set(members)
    # internal nets: local nets wholly inside the cluster
    internal: list[str] = []
    boundary: list[BoundaryPin] = []
    kind_counts: dict[str, int] = {}
    for name, pins in sorted(design.nets.items()):
        pin_refs = {r for r, _ in pins}
        touch = pin_refs & member_set
        if not touch:
            continue
        nc = netcls[name]
        wholly_inside = pin_refs <= member_set and nc.kind == "local"
        if wholly_inside:
            internal.append(name)
            continue
        # a boundary net: every member pin on it is a boundary pin
        for ref, pin in sorted(pins):
            if ref in member_set:
                ik = nc.interface_kind if nc.kind == "interface" else None
                boundary.append(BoundaryPin(ref=ref, pin=pin, net=name, interface_kind=ik))
                if ik is not None:
                    kind_counts[ik] = kind_counts.get(ik, 0) + 1
    interface_kind = None
    if kind_counts:
        interface_kind = min(kind_counts, key=lambda k: (-kind_counts[k], k))
    label = None
    for refset, value in block_label_of.items():
        if refset & member_set:
            label = value
            break
    anchor_refs = sorted(m for m in members if m in anchors)
    provenance = {
        "reason": "local-connectivity-density cluster; frontier cut at rail/interface nets",
        "anchors": anchor_refs,
        "interface_boundary_kinds": {k: kind_counts[k] for k in sorted(kind_counts)},
        "n_boundary_pins": len(boundary),
    }
    if label is not None:
        provenance["label_hint"] = label
    return Segment(
        id=seg_id,
        component_refs=tuple(members),
        internal_nets=tuple(sorted(internal)),
        boundary=tuple(boundary),
        interface_kind=interface_kind,
        label=label,
        provenance=provenance,
    )
