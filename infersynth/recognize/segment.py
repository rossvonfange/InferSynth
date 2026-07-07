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
from infersynth.recognize.netlist import DesignNetlist

__all__ = [
    "SCHEMA",
    "RAIL_DEGREE",
    "ANCHOR_DEGREE",
    "MERGE_THRESHOLD",
    "BUS_MIN_WIDTH",
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
    label: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "component_refs": list(self.component_refs),
            "internal_nets": list(self.internal_nets),
            "boundary": [b.to_dict() for b in self.boundary],
            "interface_kind": self.interface_kind,
            "label": self.label,
            "provenance": self.provenance,
        }


@dataclass(frozen=True)
class SegmentationResult:
    """Contract 2 — the segmenter's output."""

    segments: tuple[Segment, ...] = ()
    residual: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        sizes = sorted((len(s.component_refs) for s in self.segments), reverse=True)
        return {
            "schema": SCHEMA,
            "summary": {
                "segments": len(self.segments),
                "residual_components": len(self.residual),
                "largest_segment": sizes[0] if sizes else 0,
                "segment_sizes": sizes,
            },
            "segments": [s.to_dict() for s in self.segments],
            "residual": list(self.residual),
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=False)

    def to_markdown(self) -> str:
        sizes = sorted((len(s.component_refs) for s in self.segments), reverse=True)
        lines = [
            "# Segmentation report",
            "",
            f"- schema: `{SCHEMA}`",
            f"- segments: **{len(self.segments)}** "
            f"(largest {sizes[0] if sizes else 0} components)",
            f"- residual (unclustered) components: **{len(self.residual)}**",
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
    for an interface, its ``interface_kind`` (e.g. ``i2c``, ``ddr``)."""

    kind: str
    interface_kind: str | None = None


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


def _detect_diff_pairs(names: list[str]) -> dict[str, str]:
    """Differential-pair detection: net -> "diff_pair" for ``X_P``/``X_N`` (and
    ``XP``/``XN``) pairs sharing a >=2-char base."""
    halves: dict[str, dict[str, str]] = {}
    for name in names:
        m = _DIFF_RE.match(name)
        if m and len(m.group(1)) >= 2:
            halves.setdefault(m.group(1), {})[m.group(2)] = name
    out: dict[str, str] = {}
    for _base, d in halves.items():
        if "P" in d and "N" in d:
            out[d["P"]] = "diff_pair"
            out[d["N"]] = "diff_pair"
    return out


def _detect_protocol_bundles(names: list[str], catalog: Catalog) -> dict[str, str]:
    """``interfaces.yaml`` role-signature detection: net -> interface name.

    For every interface def, group nets that carry one of its role tokens by the
    base name (the net name with that role token removed). A base whose present
    role tokens cover all the interface's *required* roles is a bundle; its nets
    take the interface name as ``interface_kind``. The generic single-wire
    ``analog`` interface is skipped (its lone ``SIG`` role would match far too
    much). Multi-role interfaces (>=2 roles) only, so a stray token can't forge
    a bundle.
    """
    out: dict[str, str] = {}
    tokens_by_net = {n: set(_TOKEN_SPLIT.split(n.upper())) for n in names}
    for iface_name, idef in sorted(catalog.interfaces.items()):
        roles = idef.roles
        if len(roles) < 2:
            continue  # skip degenerate single-wire interfaces (analog)
        required = {r for r, rd in roles.items() if not (rd.optional or rd.optional_many)}
        if not required:
            continue
        role_tokens = {r.upper() for r in roles}
        # group nets by base (name minus the matched role token) -> {role: net}
        groups: dict[str, dict[str, str]] = {}
        for name in names:
            toks = tokens_by_net[name]
            hit = role_tokens & toks
            if not hit:
                continue
            for role_tok in hit:
                base = _TOKEN_SPLIT.sub("_", name.upper()).replace(role_tok, "\x00")
                groups.setdefault(base, {})[role_tok] = name
        for _base, present in groups.items():
            if {r.upper() for r in required} <= set(present):
                for name in present.values():
                    out.setdefault(name, iface_name)
    return out


def classify_nets(
    design: DesignNetlist, catalog: Catalog, labels: list[LabelClaim] | None = None
) -> dict[str, NetClass]:
    """Classify every net of *design* as rail / interface / local.

    Rails first (a power net that is also part of a bus name stays a rail — a
    rail never binds a cluster). Interface bundles (bus, diff pair, protocol
    signature, and any net a ``net_label``/``protocol`` label marks) next. All
    other nets are local clustering edges.
    """
    names = sorted(design.nets)
    rails = {
        n
        for n in names
        if _POWER_RE.search(n) or _VOLT_RE.search(n) or _net_degree(design.nets[n]) >= RAIL_DEGREE
    }
    non_rail = [n for n in names if n not in rails]
    iface: dict[str, str] = {}
    for detector in (_detect_bus_bundles(non_rail), _detect_diff_pairs(non_rail)):
        for n, kind in detector.items():
            iface.setdefault(n, kind)
    for n, kind in _detect_protocol_bundles(non_rail, catalog).items():
        iface.setdefault(n, kind)
    # label hints: a net_label / protocol label marks its net as an interface cut
    for lc in labels or ():
        if lc.kind in ("net_label", "protocol") and lc.net and lc.net not in rails:
            iface.setdefault(lc.net, _label_kind(lc))

    out: dict[str, NetClass] = {}
    for n in names:
        if n in rails:
            out[n] = NetClass("rail")
        elif n in iface:
            out[n] = NetClass("interface", iface[n])
        else:
            out[n] = NetClass("local")
    return out


def _label_kind(lc: LabelClaim) -> str:
    return re.sub(r"[^a-z0-9]+", "_", lc.value.lower()).strip("_") or "labeled"


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


def segment(
    design: DesignNetlist, catalog: Catalog, *, labels: list[LabelClaim] | None = None
) -> SegmentationResult:
    """Partition *design* into interface-bounded segments. Pure + deterministic.

    See the module docstring for the algorithm. ``labels`` are hints only; with
    ``labels=None`` the result is pure connectivity (the v0 baseline).
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
    return SegmentationResult(segments=tuple(segments), residual=residual)


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
