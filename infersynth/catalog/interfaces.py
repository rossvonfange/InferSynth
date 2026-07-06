"""``catalog/interfaces.yaml`` loader + cell ``interfaces:`` validator +
pairwise mating resolver (NETFLOW.md "Interfaces (bundles)", build order
item 3).

Scope discipline (NETFLOW build order item 3 vs item 4): this module is the
DATA MODEL and its validation, plus a simple pairwise complementarity/wire-
pairing resolver (:func:`mates`). It is deliberately match-ADJACENT but
ENGINE-FREE — it does not do bundle-aware flow matching or design-scope
convergence across many cells; that is NETFLOW build order item 4
(design-scope convergence with variance-triaged ResolutionRequests).
:func:`mates` resolves exactly one candidate pair of interface groups; a
future engine decides *which* pairs to try and folds the result into the
wider net-inference DAG.

``interfaces.yaml`` is a small, catalog-wide file (mirrors
``infersynth.catalog.taxonomy``'s ``taxonomy.yaml`` handling exactly):
``{version, interfaces: {name: {roles: {ROLE: {dir, kind?, optional?,
optional_many?}, ...}, domain?, description?}, ...}}``. It is loaded once by
:meth:`Catalog.load` from the catalog root and threaded through to every
:func:`~infersynth.catalog.loader.load_cell` call so a cell's ``interfaces:``
claims validate against it — exactly the ``taxonomy``/``idioms.functions``
pattern, replicated for interface groups.

Directions (``dir``) in a role definition are always stated from the
INITIATOR's perspective. This is the crossover-encoded-once design NETFLOW.md
calls for: the peripheral side's direction is the mirror image and is never
re-derived at match time — see the :func:`mates` docstring for exactly how
pairing uses this.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from infersynth.ir import PortKind

__all__ = [
    "InterfacesError",
    "RoleDef",
    "InterfaceDef",
    "InterfaceGroup",
    "WirePair",
    "MateResult",
    "load_interfaces",
    "validate_cell_interfaces",
    "mates",
]

_DIRS = ("out", "in", "bidir")
_ROLE_KEYS = {"dir", "kind", "optional", "optional_many"}
_INTERFACE_KEYS = {"roles", "domain", "description"}
_TOP_KEYS = {"version", "interfaces"}
_GROUP_KEYS = {"type", "role", "map"}
_GROUP_ROLES = ("initiator", "peripheral")
#: The cell ``ports:`` section's own fixed kind vocabulary (electrical/power/
#: digital — infersynth.ir.PortKind). Interface-role ``kind`` is a *separate*,
#: free-form vocabulary (e.g. ``diff_p``/``diff_n`` domain tags for CAN_H/
#: CAN_L) that need not overlap with it at all. The compatibility check in
#: :func:`validate_cell_interfaces` only fires when a role's declared kind
#: falls inside this shared vocabulary; a role kind like ``diff_p`` is purely
#: a domain annotation for that role and is never checked against a port's
#: (always electrical/power/digital) kind — there is nothing to compare it
#: against, and requiring equality would make ``can_phy``/``diff_pair``
#: unmappable by construction (a port can never legally declare
#: ``kind: diff_p`` under the ``ports:`` schema).
_PORT_KIND_VALUES = frozenset(k.value for k in PortKind)


class InterfacesError(ValueError):
    """Raised when ``interfaces.yaml`` itself is malformed.

    Mirrors :class:`infersynth.catalog.taxonomy.TaxonomyError`: this is the
    catalog's own file, so a malformed one is a loud authoring bug, not a
    per-cell diagnostic.
    """


@dataclass(frozen=True)
class RoleDef:
    """One role within an interface definition (e.g. uart's ``TX``)."""

    dir: str  # "out" | "in" | "bidir", from the initiator's perspective
    kind: str | None = None
    optional: bool = False
    optional_many: bool = False


@dataclass(frozen=True)
class InterfaceDef:
    """A loaded, validated entry from ``interfaces.yaml``."""

    name: str
    roles: dict[str, RoleDef] = field(default_factory=dict)
    domain: str | None = None
    description: str | None = None


def load_interfaces(path: str | Path) -> dict[str, InterfaceDef]:
    """Load and validate an ``interfaces.yaml`` file.

    Returns a mapping of interface name -> :class:`InterfaceDef`. Raises
    :class:`InterfacesError` on any malformed structure (bad ``dir`` enum,
    unknown key anywhere in the schema, an interface with zero roles, ...).
    """
    p = Path(path)
    try:
        data = yaml.safe_load(p.read_text())
    except yaml.YAMLError as exc:
        raise InterfacesError(f"{p}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise InterfacesError(f"{p}: must be a mapping")
    unknown_top = sorted(set(data) - _TOP_KEYS)
    if unknown_top:
        raise InterfacesError(f"{p}: unknown top-level key(s) {unknown_top}")
    interfaces = data.get("interfaces")
    if not isinstance(interfaces, dict) or not interfaces:
        raise InterfacesError(f"{p}: 'interfaces' must be a non-empty mapping of name -> spec")

    result: dict[str, InterfaceDef] = {}
    for name, spec in interfaces.items():
        if not isinstance(name, str) or not name:
            raise InterfacesError(f"{p}: interface name keys must be non-empty strings")
        where = f"{p}: interfaces.{name}"
        if not isinstance(spec, dict):
            raise InterfacesError(f"{where} must be a mapping")
        unknown = sorted(set(spec) - _INTERFACE_KEYS)
        if unknown:
            raise InterfacesError(f"{where}: unknown key(s) {unknown}")

        roles_raw = spec.get("roles")
        if not isinstance(roles_raw, dict) or not roles_raw:
            raise InterfacesError(
                f"{where}.roles must be a non-empty mapping of ROLE -> {{dir, ...}} "
                "(every interface needs at least one role)"
            )
        roles: dict[str, RoleDef] = {}
        for role_name, role_spec in roles_raw.items():
            role_where = f"{where}.roles.{role_name}"
            if not isinstance(role_spec, dict):
                raise InterfacesError(f"{role_where} must be a mapping")
            unknown_role = sorted(set(role_spec) - _ROLE_KEYS)
            if unknown_role:
                raise InterfacesError(f"{role_where}: unknown key(s) {unknown_role}")
            d = role_spec.get("dir")
            if d not in _DIRS:
                raise InterfacesError(f"{role_where}.dir must be one of {_DIRS}, got {d!r}")
            kind = role_spec.get("kind")
            if kind is not None and not (isinstance(kind, str) and kind):
                raise InterfacesError(f"{role_where}.kind must be a non-empty string")
            optional = role_spec.get("optional", False)
            if not isinstance(optional, bool):
                raise InterfacesError(f"{role_where}.optional must be a bool")
            optional_many = role_spec.get("optional_many", False)
            if not isinstance(optional_many, bool):
                raise InterfacesError(f"{role_where}.optional_many must be a bool")
            roles[str(role_name)] = RoleDef(
                dir=d, kind=kind, optional=optional, optional_many=optional_many
            )

        domain = spec.get("domain")
        if domain is not None and not (isinstance(domain, str) and domain):
            raise InterfacesError(f"{where}.domain must be a non-empty string")
        description = spec.get("description")
        if description is not None and not isinstance(description, str):
            raise InterfacesError(f"{where}.description must be a string")

        result[str(name)] = InterfaceDef(
            name=str(name), roles=roles, domain=domain, description=description
        )
    return result


@dataclass(frozen=True)
class InterfaceGroup:
    """One validated ``interfaces:`` group claimed by a cell.yaml."""

    name: str
    type: str
    role: str  # "initiator" | "peripheral"
    map: dict[str, str] = field(default_factory=dict)  # ROLE -> port name


def validate_cell_interfaces(
    interfaces_claim: Any,
    defs: dict[str, InterfaceDef] | None,
    ports: dict[str, dict[str, str]],
    diags: list[str],
) -> dict[str, InterfaceGroup]:
    """Validate a cell.yaml ``interfaces:`` section; return group name -> :class:`InterfaceGroup`.

    Mirrors ``_validate_functions``'s "absent catalog file -> any claim is
    rejected" rule: when *defs* is ``None`` (no ``interfaces.yaml`` in the
    catalog at all), any ``interfaces:`` claim is an error.

    Validation rules enforced here (NETFLOW build order item 3):
      * ``type`` must name a known interface in *defs*.
      * ``role`` must be ``initiator`` or ``peripheral``.
      * every mapped ROLE key must exist on that interface type.
      * every REQUIRED role (not ``optional``/``optional_many``) must be
        mapped.
      * every mapped PORT_NAME must exist in this cell's own ``ports:``.
      * a port may belong to at most one interface group in this cell
        (cross-group check, tracked via *claimed_ports* below).
      * kind compatibility — see ``_PORT_KIND_VALUES`` above for exactly
        when this fires (only when the role's kind is drawn from the same
        vocabulary a port's kind uses; domain tags like ``diff_p`` are
        exempt since ports cannot declare that kind at all).
    """
    if interfaces_claim is None:
        return {}
    if defs is None:
        claimed = (
            sorted(interfaces_claim) if isinstance(interfaces_claim, dict) else interfaces_claim
        )
        diags.append(
            f"cell.yaml: interfaces claims {claimed!r} but there is no interfaces.yaml "
            "in catalog (add catalog/interfaces.yaml)"
        )
        return {}
    if not isinstance(interfaces_claim, dict) or not interfaces_claim:
        diags.append(
            "cell.yaml: interfaces must be a non-empty mapping of group name -> "
            "{type, role, map}"
        )
        return {}

    result: dict[str, InterfaceGroup] = {}
    claimed_ports: dict[str, str] = {}  # port name -> owning group name

    for group_name, spec in sorted(interfaces_claim.items()):
        where = f"cell.yaml: interfaces.{group_name}"
        if not isinstance(spec, dict):
            diags.append(f"{where} must be a mapping with 'type', 'role', 'map'")
            continue
        unknown = sorted(set(spec) - _GROUP_KEYS)
        if unknown:
            diags.append(f"{where}: unknown key(s) {unknown}")

        itype = spec.get("type")
        idef: InterfaceDef | None = None
        if not isinstance(itype, str) or not itype:
            diags.append(f"{where}.type is required and must be a non-empty string")
        elif itype not in defs:
            diags.append(
                f"{where}.type: unknown interface type {itype!r} "
                "(not declared in catalog interfaces.yaml)"
            )
        else:
            idef = defs[itype]

        role = spec.get("role")
        if role not in _GROUP_ROLES:
            diags.append(f"{where}.role must be one of {_GROUP_ROLES}, got {role!r}")

        raw_map = spec.get("map")
        if raw_map is None:
            raw_map = {}
        elif not isinstance(raw_map, dict):
            diags.append(f"{where}.map must be a mapping of ROLE -> port name")
            raw_map = {}

        resolved_map: dict[str, str] = {}
        for role_name, port_name in sorted(raw_map.items()):
            map_where = f"{where}.map.{role_name}"
            if not isinstance(port_name, str) or not port_name:
                diags.append(f"{map_where} must be a non-empty port name string")
                continue
            if idef is not None and role_name not in idef.roles:
                diags.append(
                    f"{map_where}: unknown role {role_name!r} for interface type {itype!r}"
                )
                continue
            if port_name not in ports:
                diags.append(
                    f"{map_where}: port {port_name!r} is not declared in this cell's ports"
                )
                continue
            if idef is not None:
                role_def = idef.roles[role_name]
                port_kind = ports[port_name].get("kind")
                if (
                    role_def.kind is not None
                    and port_kind is not None
                    and role_def.kind in _PORT_KIND_VALUES
                    and role_def.kind != port_kind
                ):
                    diags.append(
                        f"{map_where}: port {port_name!r} kind {port_kind!r} is "
                        f"incompatible with role {role_name!r}'s declared kind "
                        f"{role_def.kind!r}"
                    )
            owner = claimed_ports.get(port_name)
            if owner is not None and owner != group_name:
                diags.append(
                    f"{map_where}: port {port_name!r} is already claimed by "
                    f"interface group {owner!r} (a port may belong to at most one "
                    "interface group)"
                )
            else:
                claimed_ports[port_name] = group_name
            resolved_map[str(role_name)] = port_name

        if idef is not None:
            for role_name, role_def in idef.roles.items():
                required = not (role_def.optional or role_def.optional_many)
                if required and role_name not in resolved_map:
                    diags.append(
                        f"{where}: required role {role_name!r} of interface type "
                        f"{itype!r} is not mapped"
                    )

        result[str(group_name)] = InterfaceGroup(
            name=str(group_name),
            type=itype if isinstance(itype, str) else "",
            role=role if role in _GROUP_ROLES else "",
            map=resolved_map,
        )
    return result


@dataclass(frozen=True)
class WirePair:
    """One resolved wire pairing between an initiator's and a peripheral's
    port, for a single role of the mated interface type."""

    role: str
    initiator_port: str
    peripheral_port: str


@dataclass(frozen=True)
class MateResult:
    """Result of :func:`mates` for exactly one candidate pair of groups."""

    mates: bool
    type: str | None
    pairs: tuple[WirePair, ...] = ()
    reason: str | None = None


def mates(
    group_a: InterfaceGroup, group_b: InterfaceGroup, defs: dict[str, InterfaceDef]
) -> MateResult:
    """Pairwise complementarity + wire-pairing resolver for two interface groups.

    ENGINE-FREE, v0 scope (NETFLOW build order item 3, not item 4): this
    resolves exactly one candidate pair of groups; it does not search a
    catalog or a design for *which* pairs to try, and it does not fold the
    result into any wider net graph — that is a future stage's bundle-aware
    matching engine.

    v0 restrictions (documented, not lifted here):
      * Only ``initiator`` <-> ``peripheral`` pairing mates. Two
        ``initiator``s or two ``peripheral``s never mate in v0 — a future
        stage may add peripheral-peripheral passthrough or bidir-role
        nuances; that is explicitly out of scope for this resolver.
      * Both groups must share the same ``type``; a type mismatch is a
        non-mate with a reason, never an error raised.

    Crossover semantics (read this before touching pairing logic — the
    classic wiring bug this whole mechanism exists to make unrepresentable):
    an interface definition's role ``dir`` is stated from the INITIATOR's
    perspective, and the crossover to the peripheral side is encoded ONCE, in
    the definition — never re-derived here. Concretely: for uart, the
    initiator's role map has ``TX`` bound to its own TX-capable port, and the
    peripheral's role map *also* uses the key ``TX`` (not ``RX``) to name the
    port on the peripheral that receives it. Pairing is therefore BY ROLE
    NAME — ``initiator.map["TX"]`` pairs with ``peripheral.map["TX"]`` — never
    by matching an ``out``-direction role name against an ``in``-direction
    one. The interface definition already put the crossover where it belongs
    (uart's ``RX`` role is the initiator's own receive port, paired against
    the peripheral's ``RX``-mapped port the same way). A future engine must
    not re-derive crossover from ``dir`` at match time; it must keep using
    this same by-role-name pairing.

    Unmapped optional roles: if a role is not mapped on EITHER side (common
    for optional/optional_many roles, e.g. spi's CS), that role is simply
    skipped — no pair is emitted and it is not a mismatch. A role mapped on
    only one side is likewise skipped (there is nothing to pair it with);
    this is not flagged as an error here because cell-level "required roles
    must be mapped" was already enforced at cell.yaml load time
    (:func:`validate_cell_interfaces`) — by the time two groups reach
    `mates()`, both are already known-valid, so an unmapped role here can
    only be a legitimately optional one.
    """
    if group_a.type != group_b.type:
        return MateResult(
            mates=False,
            type=None,
            reason=f"type mismatch: {group_a.type!r} != {group_b.type!r}",
        )
    itype = group_a.type
    idef = defs.get(itype)
    if idef is None:
        return MateResult(mates=False, type=itype, reason=f"unknown interface type {itype!r}")

    roles_seen = {group_a.role, group_b.role}
    if roles_seen != set(_GROUP_ROLES):
        return MateResult(
            mates=False,
            type=itype,
            reason=(
                "v0 only mates initiator<->peripheral pairs "
                f"(got {group_a.role!r} and {group_b.role!r})"
            ),
        )

    initiator = group_a if group_a.role == "initiator" else group_b
    peripheral = group_b if initiator is group_a else group_a

    pairs: list[WirePair] = []
    for role_name in idef.roles:
        a_port = initiator.map.get(role_name)
        b_port = peripheral.map.get(role_name)
        if a_port is None or b_port is None:
            continue
        pairs.append(WirePair(role=role_name, initiator_port=a_port, peripheral_port=b_port))

    return MateResult(mates=True, type=itype, pairs=tuple(pairs))
