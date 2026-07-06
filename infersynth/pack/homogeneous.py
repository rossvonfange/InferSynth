"""Homogeneous packing (WP-P1): fold N single-cell winners into one pack target.

This is the packer's first and simplest real case (BUILD_PLAN WP-P1): N instances
of the *same* single-channel cell — surfaced by the matcher (WP-M1) as length-1
closed chains across **different** requirements — jointly replaced by one
package-sharing "pack target" cell that carries them as independent channels. The
seed pair is ``opamp-gain-noninverting`` (the source C) and its quad variant
``opamp-gain-x4-noninverting`` (the pack target T), authored specifically as the
LUT-4/LUT-6 packing pair. The x4 cell carries an ``idioms.disambiguation`` rule,
so the matcher deliberately excludes it from direct chains (matcher.py's
"disambiguation primacy") — **the packer is the only legal route to it**, exactly
as the x4 cell's own disambiguation text says.

Discovery is **data-driven, never hard-coded to the opamp pair** (WP-P1 item 1):
a catalog cell T is a pack target for a source cell C when

    1. T declares package sharing — ``selection.package_sharing.units >= 2``;
    2. T shares C's idiom space — their ``idioms.keywords`` sets intersect, and
       (when both declare ``idioms.functions``) their function tags intersect;
    3. T is a different cell from C (different ``manifest.name``); and
    4. T exposes a per-channel parameter family (``gain1 … gainN``) whose size
       matches (or exceeds) its ``units`` — the channels the source's single
       parameter maps into.

See :func:`discover_pack_targets`.

**Guard-and-claim** (RECON_HARVEST §4, adapted from recon's ``net_merge``): each
proposed pack is a :class:`PackClaim` keyed by the *set* of requirement ids it
absorbs — a deterministic, idempotent, order-independent key. Two **terminal
guards** block an illegal claim from ever forming (never a silent bad pack):

    * **capacity** — ``N <= units`` (the quad's macrocell budget); ``N > units``
      is blocked (v0 forms one claim per group; chunking a too-big group across
      several packs is future work — see the module note below);
    * **rails compatibility** — approximated at v0 by *identical power-port sets*
      between C and T (both ``{VCC, VEE, GND}`` for the opamp pair).

Claims are **proposals** carried in :class:`PackResult`; they never mutate the
:class:`~infersynth.match.matcher.MatchResult`. Winner-picking (pack vs. the N
discrete singles) is the decision layer's job — see
:mod:`infersynth.pack.decide_integration`.

**Absorption knobs** (SELECTION §5): ``off`` yields no claims; ``conservative``
packs only requirements sharing a parent (same requirement-tree group);
``aggressive`` packs across the whole design — but, in *both* non-off modes,
**never across an allocation boundary** (two requirements pack together only when
they resolve to the *same* library scope, WP-M1's ``resolve_scope``).

Conventions chosen where the docs are silent (flagged in the WP report):

* ``pack(match_result, catalog, knobs)`` is the primary signature; the legality
  guards need more than the match result, so ``reqset`` (parent grouping) and
  ``allocations`` (scope boundary) are **keyword-only** extras — omitting them
  degrades gracefully (no parent ⇒ one group; no allocations ⇒ one unrestricted
  scope), it never packs across a boundary it cannot see.
* Per-requirement forbid: no lint-level absorption-forbid constraint exists in
  the repo yet (checked: ``infersynth/lint`` has no such tag), so ``forbid_pack``
  is accepted as an explicit set of requirement ids; wiring an FRD-level
  ``SHALL be independent of the processor`` forbid through lint is future work.
* A too-big group (``N > units``) is blocked wholesale rather than chunked into
  ``ceil(N/units)`` packs — matches the WP's "capacity blocks 5-into-4" test and
  keeps v0 claims disjoint; multi-pack chunking is a later refinement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from infersynth.catalog import Catalog
from infersynth.catalog.loader import CellPackage
from infersynth.lint.model import RequirementSet
from infersynth.match.allocation import NONE_LIBRARY, AllocationTable, resolve_scope
from infersynth.match.matcher import MatchResult

__all__ = [
    "PackKnobs",
    "PackTarget",
    "ChannelMap",
    "PackClaim",
    "PackResult",
    "discover_pack_targets",
    "pack",
]

_ABSORPTION_VALUES = ("off", "conservative", "aggressive")
_CHANNEL_RE = re.compile(r"^([a-z_]+?)(\d+)$")


@dataclass(frozen=True)
class PackKnobs:
    """Packer policy knob (SELECTION §5 ``absorption``).

    * ``off`` — never propose a pack (no claims);
    * ``conservative`` — pack only requirements sharing a parent (one
      requirement-tree group);
    * ``aggressive`` — pack across the whole design, but never across an
      allocation boundary.

    Default is ``conservative`` (the least-surprising non-trivial behavior; the
    WP does not pin a default — noted in the report).
    """

    absorption: str = "conservative"

    def __post_init__(self) -> None:
        if self.absorption not in _ABSORPTION_VALUES:
            raise ValueError(
                f"absorption must be one of {_ABSORPTION_VALUES}, got {self.absorption!r}"
            )


@dataclass(frozen=True)
class PackTarget:
    """A discovered (source cell C -> pack target T) pairing, with its budget.

    ``channel_base`` is the per-channel parameter family name on T (``"gain"``
    for the opamp quad, whose params are ``gain1 … gain4``); ``units`` is the
    channel capacity from ``selection.package_sharing.units``.
    """

    source_key: str
    target_key: str
    units: int
    channel_base: str


@dataclass(frozen=True)
class ChannelMap:
    """One absorbed requirement mapped onto a target channel (WP-P1 item 6).

    ``channel_param`` is the target parameter the requirement's extracted gain
    binds to (``gain1 … gainN`` in requirement-id-sorted order). ``gain`` is the
    value the matcher extracted for the source cell on this requirement, or
    ``None`` when the requirement text bound no gain (synthesize consumes this
    later; the packer only records it).
    """

    requirement_id: str
    channel_param: str
    gain: float | None = None


@dataclass(frozen=True)
class PackClaim:
    """A weighted, falsifiable pack proposal keyed by its absorbed requirement set.

    ``key`` — the sorted tuple of absorbed requirement ids — is the deterministic,
    idempotent claim key (RECON_HARVEST §4): re-running :func:`pack` on the same
    inputs yields byte-identical claims, and shuffling the input requirement order
    does not change it. A claim is a *proposal*; it is arbitrated (against the N
    discrete singles) by the decision layer, by cost, never by application order.
    """

    key: tuple[str, ...]
    source_cell: str
    target_cell: str
    units: int
    absorbed: tuple[str, ...]
    channels: tuple[ChannelMap, ...]
    unused_channels: tuple[str, ...]
    power_ports: tuple[str, ...]
    absorption: str

    @property
    def partial(self) -> bool:
        """True when fewer requirements are packed than the target has channels."""
        return bool(self.unused_channels)


@dataclass(frozen=True)
class PackResult:
    """The packer's output: proposed claims (never a mutation of the match result)."""

    claims: tuple[PackClaim, ...] = ()
    #: source_key -> PackTarget for every discovered pairing (audit / trace input).
    targets: dict[str, PackTarget] = field(default_factory=dict)


def _package_sharing_units(cell: CellPackage) -> int:
    sharing = cell.selection.get("package_sharing") if cell.selection else None
    if not isinstance(sharing, dict):
        return 0
    try:
        return int(sharing.get("units", 0))
    except (TypeError, ValueError):
        return 0


def _channel_family(cell: CellPackage) -> tuple[str | None, int]:
    """Largest ``<base><digit>`` parameter family on *cell*: ``(base, count)``.

    The opamp quad's ``gain1 … gain4`` yields ``("gain", 4)``; a cell with no such
    family yields ``(None, 0)``.
    """
    families: dict[str, set[int]] = {}
    for pname in cell.idiom_params:
        m = _CHANNEL_RE.match(pname)
        if m:
            families.setdefault(m.group(1), set()).add(int(m.group(2)))
    if not families:
        return None, 0
    base = min(families, key=lambda b: (-len(families[b]), b))
    return base, len(families[base])


def _power_ports(cell: CellPackage) -> tuple[str, ...]:
    return tuple(sorted(n for n, spec in cell.ports.items() if spec.get("kind") == "power"))


def discover_pack_targets(catalog: Catalog) -> dict[str, PackTarget]:
    """Discover, data-driven, each source cell's pack target (WP-P1 item 1).

    Returns ``source_key -> PackTarget``. A source cell maps to the
    lexicographically-smallest catalog cell that (1) declares
    ``package_sharing.units >= 2``, (2) shares an idiom keyword (and, when both
    declare functions, a function tag), (3) is a different cell, and (4) exposes a
    per-channel parameter family. Rule-carrying targets (the x4 quad's
    disambiguation) are *included* here — the packer is their only legal route.
    """
    targets = sorted(
        (k, c) for k, c in catalog.cells.items() if _package_sharing_units(c) >= 2
    )
    result: dict[str, PackTarget] = {}
    for src_key in sorted(catalog.cells):
        src = catalog.cells[src_key]
        src_keywords = set(src.keywords)
        if not src_keywords:
            continue
        for tkey, tcell in targets:
            if tcell.name == src.name:
                continue
            if not (set(tcell.keywords) & src_keywords):
                continue
            if src.functions and tcell.functions and not (
                set(tcell.functions) & set(src.functions)
            ):
                continue
            base, count = _channel_family(tcell)
            units = _package_sharing_units(tcell)
            if base is None or count < units:
                continue
            result[src_key] = PackTarget(
                source_key=src_key, target_key=tkey, units=units, channel_base=base
            )
            break
    return result


def _extracted_gain(
    match_result: MatchResult, req_id: str, source_key: str, base: str
) -> float | None:
    """The gain the matcher bound for *source_key* on *req_id* (``None`` if none)."""
    for cand in match_result.candidates.get(req_id, ()):  # sorted, deterministic
        if cand.cell_key != source_key:
            continue
        for binding in cand.params:
            if binding.name == base and binding.problem is None:
                return float(binding.value)
    return None


def _scope_key(
    req_id: str, reqset: RequirementSet | None, allocations: AllocationTable | None,
    all_libraries: frozenset[str],
) -> frozenset[str] | None:
    """The requirement's resolved library scope (allocation-boundary partition key)."""
    if reqset is None or allocations is None or not allocations:
        return None
    req = reqset.by_id.get(req_id)
    if req is None:
        return None
    return resolve_scope(req, allocations, all_libraries).allowed


def _parent_key(req_id: str, reqset: RequirementSet | None) -> str | None:
    if reqset is None:
        return None
    req = reqset.by_id.get(req_id)
    if req is None or req.parent is None:
        return None
    return req.parent.id


def pack(
    match_result: MatchResult,
    catalog: Catalog,
    knobs: PackKnobs | None = None,
    *,
    reqset: RequirementSet | None = None,
    allocations: AllocationTable | None = None,
    forbid_pack: frozenset[str] | set[str] | None = None,
) -> PackResult:
    """Propose homogeneous packs over a match result (WP-P1). Pure + deterministic.

    Identifies groups of requirements that surfaced the *same* source cell as a
    length-1 closed chain and proposes, per legal group, one :class:`PackClaim`
    folding them into that cell's discovered pack target. ``off`` ⇒ no claims;
    the group boundary is the parent (``conservative``) or the whole design
    (``aggressive``), always within one allocation scope.
    """
    knobs = knobs or PackKnobs()
    targets = discover_pack_targets(catalog)
    result = PackResult(claims=(), targets=targets)
    if knobs.absorption == "off" or not targets:
        return result

    forbid = frozenset(forbid_pack or ())
    all_libraries = frozenset(
        (c.library if c.library else NONE_LIBRARY) for c in catalog.cells.values()
    )

    # requirement -> the single source cell it is packable into (smallest key).
    packable: dict[str, str] = {}
    for req_id, chains in match_result.chains.items():
        if req_id in forbid:
            continue
        sources = sorted(
            ch.cells[0] for ch in chains if len(ch.cells) == 1 and ch.cells[0] in targets
        )
        if sources:
            packable[req_id] = sources[0]

    # group by (source, allocation scope [, parent]) — deterministic partitioning.
    groups: dict[tuple, list[str]] = {}
    for req_id in sorted(packable):
        source = packable[req_id]
        scope = _scope_key(req_id, reqset, allocations, all_libraries)
        gkey: tuple = (source, scope)
        if knobs.absorption == "conservative":
            gkey = (source, scope, _parent_key(req_id, reqset))
        groups.setdefault(gkey, []).append(req_id)

    claims: list[PackClaim] = []
    for gkey, members in groups.items():
        source_key = gkey[0]
        target = targets[source_key]
        absorbed = tuple(sorted(members))
        if len(absorbed) < 2:
            continue  # nothing to pack (a single instance stays discrete)
        if len(absorbed) > target.units:
            continue  # capacity terminal guard blocks an over-budget pack
        src_cell = catalog.cells[source_key]
        tgt_cell = catalog.cells[target.target_key]
        src_power = _power_ports(src_cell)
        if src_power != _power_ports(tgt_cell):
            continue  # rails terminal guard: power-port sets must be identical
        channels = tuple(
            ChannelMap(
                requirement_id=rid,
                channel_param=f"{target.channel_base}{k}",
                gain=_extracted_gain(match_result, rid, source_key, target.channel_base),
            )
            for k, rid in enumerate(absorbed, start=1)
        )
        unused = tuple(
            f"{target.channel_base}{k}" for k in range(len(absorbed) + 1, target.units + 1)
        )
        claims.append(
            PackClaim(
                key=absorbed,
                source_cell=source_key,
                target_cell=target.target_key,
                units=target.units,
                absorbed=absorbed,
                channels=channels,
                unused_channels=unused,
                power_ports=src_power,
                absorption=knobs.absorption,
            )
        )

    claims.sort(key=lambda c: c.key)
    return PackResult(claims=tuple(claims), targets=targets)
