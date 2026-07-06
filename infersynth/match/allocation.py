"""Allocation scoping (SELECTION.md §2): requirement subtree -> library scope.

An **allocation** binds a requirement subtree to a set of catalog libraries
(SELECTION §1 namespaces). It is human steering entered through a declared,
versioned channel — it lives in the formal spec, never in FRD prose.

Semantics (SELECTION §2), implemented by :func:`resolve_scope`:

* **inherited down the tree** — an allocation at ``SYS.1`` governs every
  descendant of ``SYS.1``;
* **nearer node overrides** — the nearest ancestor (including the requirement
  itself) that declares an ``allow`` sets the scope; likewise for ``deny``;
* **deny beats allow** — a library named in the effective ``deny`` is out even
  if it is also in the effective ``allow``;
* **no allocation ⇒ all libraries compete** (the default).

Convention chosen where the docs are silent: ``allow``/``deny`` entries are
**library names** (SELECTION §1 catalog namespaces), not function tags — the
reading that makes "the user changing available libraries is the canonical
instance of the general rule" (SELECTION §8) literally true, and that matches
§2's framing ("binds a requirement subtree to catalog scope"). A cell whose
``library`` is ``None`` (flat fixture catalog with no ``library.yaml``) is
treated as belonging to the pseudo-library ``"<none>"`` for scoping: it passes
an unrestricted scope, and an ``allow`` list may name ``"<none>"`` to admit it.

There is no formal-spec loader in the repo yet (``infersynth elaborate`` is a
stub), so :func:`allocations_from_spec` parses *only* the ``allocations:`` key
out of a spec mapping — it does not attempt to become a parallel FRD/spec
loader (the WP-M1 brief forbids that). When a real spec loader lands it should
call this same parser for that key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from infersynth.lint.model import Requirement

__all__ = [
    "NONE_LIBRARY",
    "Allocation",
    "AllocationTable",
    "AllocationError",
    "resolve_scope",
    "allocations_from_spec",
]

#: Pseudo-library name standing in for a cell loaded outside any library.
NONE_LIBRARY = "<none>"


class AllocationError(ValueError):
    """Raised for a structurally invalid ``allocations:`` block."""


@dataclass(frozen=True)
class Allocation:
    """One ``{at, allow, deny}`` entry pinned to a requirement id.

    ``allow`` is ``None`` when the entry declares no ``allow`` (leaves the
    inherited scope unrestricted at this node); an empty tuple means "allow
    nothing here" (a deliberate, and diagnosable, empty scope). ``deny`` is a
    (possibly empty) tuple.
    """

    at: str
    allow: tuple[str, ...] | None = None
    deny: tuple[str, ...] = ()


@dataclass(frozen=True)
class AllocationTable:
    """All allocations for a spec, indexed by the requirement id they attach to."""

    by_id: dict[str, Allocation] = field(default_factory=dict)

    def get(self, req_id: str) -> Allocation | None:
        return self.by_id.get(req_id)

    def __bool__(self) -> bool:
        return bool(self.by_id)


def _lib_of(library: str | None) -> str:
    return library if library else NONE_LIBRARY


@dataclass(frozen=True)
class ResolvedScope:
    """The effective library scope for one requirement.

    ``allowed`` is ``None`` for an unrestricted scope ("all libraries
    compete"); otherwise the concrete set of admitted library names (already
    with ``deny`` subtracted). ``has_allocation`` records whether *any*
    allocation was found in the requirement's ancestry (drives
    ``frd.unallocated``). ``empty`` is True when the scope admits no library
    (drives ``frd.allocation-empty``).
    """

    allowed: frozenset[str] | None
    has_allocation: bool

    @property
    def empty(self) -> bool:
        return self.allowed is not None and len(self.allowed) == 0

    def admits(self, library: str | None) -> bool:
        if self.allowed is None:
            return True
        return _lib_of(library) in self.allowed


def _ancestry(req: Requirement) -> list[Requirement]:
    """Root-first path down to *req* (farther ancestors first, req last)."""
    chain: list[Requirement] = []
    node: Requirement | None = req
    while node is not None:
        chain.append(node)
        node = node.parent
    chain.reverse()
    return chain


def resolve_scope(
    req: Requirement,
    allocations: AllocationTable,
    all_libraries: frozenset[str],
) -> ResolvedScope:
    """Resolve *req*'s effective library scope (SELECTION §2 semantics).

    Walks the root-first ancestry: an ``allow`` at a nearer node *overrides*
    (replaces) a farther one; likewise ``deny``. The final admitted set is
    ``(allow or all_libraries) - deny`` — deny beats allow.
    """
    allow: set[str] | None = None
    deny: set[str] = set()
    seen_alloc = False
    for node in _ancestry(req):
        alloc = allocations.get(node.id)
        if alloc is None:
            continue
        seen_alloc = True
        if alloc.allow is not None:
            allow = set(alloc.allow)  # nearer allow overrides farther
        if alloc.deny:
            deny = set(alloc.deny)  # nearer deny overrides farther
    if not seen_alloc:
        return ResolvedScope(allowed=None, has_allocation=False)
    base = set(all_libraries) if allow is None else allow
    return ResolvedScope(allowed=frozenset(base - deny), has_allocation=True)


def allocations_from_spec(spec: Any) -> AllocationTable:
    """Parse the ``allocations:`` key of a formal-spec mapping.

    Accepts either a full spec mapping (reads its ``allocations`` key) or the
    allocations list directly. Each entry is ``{at, allow?, deny?}`` with
    ``at`` a requirement id string and ``allow``/``deny`` lists of library-name
    strings. Raises :class:`AllocationError` on a malformed block. This parses
    *only* this key — it is not a spec loader.
    """
    if spec is None:
        return AllocationTable()
    if isinstance(spec, dict):
        raw = spec.get("allocations")
    else:
        raw = spec
    if raw is None:
        return AllocationTable()
    if not isinstance(raw, list):
        raise AllocationError("allocations must be a list of {at, allow, deny} entries")

    def _strlist(value: Any, where: str) -> tuple[str, ...] | None:
        if value is None:
            return None
        if not (isinstance(value, list) and all(isinstance(v, str) and v for v in value)):
            raise AllocationError(f"{where} must be a list of non-empty library-name strings")
        return tuple(value)

    by_id: dict[str, Allocation] = {}
    for i, entry in enumerate(raw):
        where = f"allocations[{i}]"
        if not isinstance(entry, dict):
            raise AllocationError(f"{where} must be a mapping")
        unknown = sorted(set(entry) - {"at", "allow", "deny"})
        if unknown:
            raise AllocationError(f"{where}: unknown key(s) {unknown}")
        at = entry.get("at")
        if not isinstance(at, str) or not at:
            raise AllocationError(f"{where}.at must be a non-empty requirement id string")
        if at in by_id:
            raise AllocationError(f"{where}: duplicate allocation for requirement id {at!r}")
        allow = _strlist(entry.get("allow"), f"{where}.allow")
        deny = _strlist(entry.get("deny"), f"{where}.deny") or ()
        by_id[at] = Allocation(at=at, allow=allow, deny=deny)
    return AllocationTable(by_id=by_id)
