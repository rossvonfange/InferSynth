"""Fit engine (docs/FABRIC.md, deliverable 2): compile winners onto a fabric.

``fit`` is the packer constrained to the fabric's site capacities — the FPGA
place-and-route analogy gone structural: **fit = utilization**, doesn't-fit is
a first-class diagnostic (``"needs 1 more site of core/X"``), not a routing
failure. It performs NO placement and NO routing (the routing was done once,
by a human, at fabric-design time).

Assignment is deterministic (SELECTION §8): requirements in *sorted* id order,
sites in *declared* order — the first free site whose accepted-cell set
contains the winner wins. Guard-and-claim (mirroring
:mod:`infersynth.pack`): a claimed site is removed from the free pool; a
requirement with no compatible free site is unfittable (utilization
diagnostic). Leftover sites become the DNP set per their ``tie_off`` policy.

Params: a fitted requirement's resolved params must satisfy the site's
``params_fixed`` (mismatch = a diagnostic naming both values); its stuffable
params produce a value-stuffing list (board ref -> value) via the cell's
``bindings``, reusing :func:`infersynth.bind.bind_cell`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from infersynth.bind.expr import BindingError, bind_cell, free_names
from infersynth.catalog import Catalog
from infersynth.fabric.loader import Fabric, Site, derive_ref_map

__all__ = [
    "StuffValue",
    "StuffedSite",
    "FitResult",
    "fit",
    "extracted_params_for_winners",
]

_PARAM_EQ_TOL = 1e-9


@dataclass(frozen=True)
class StuffValue:
    """One value-stuffed ref: a stuffable-param-dependent binding, resolved."""

    site_id: str
    board_ref: str
    fragment_ref: str
    value: float
    fmt: str | None = None


@dataclass(frozen=True)
class StuffedSite:
    """A site populated by a winning requirement, with its resolved values."""

    requirement_id: str
    site_id: str
    cell_key: str
    #: fragment ref -> board ref (explicit ref_map or prefix-derived)
    ref_map: dict[str, str] = field(default_factory=dict)
    #: board ref -> resolved value for every *binding* ref (fixed + stuffable)
    bound_values: dict[str, float] = field(default_factory=dict)
    #: the value-stuffing entries (stuffable-param-dependent bindings only)
    stuffed: tuple[StuffValue, ...] = ()


@dataclass(frozen=True)
class FitResult:
    """The fit engine's output: assignment, DNP set, value-stuffing, diagnostics."""

    stuffed_sites: tuple[StuffedSite, ...]
    dnp_sites: tuple[str, ...]
    dnp_refs: tuple[str, ...]
    #: (requirement_id, cell_key) winners that found no compatible free site
    unfittable: tuple[tuple[str, str], ...]
    diagnostics: tuple[str, ...]
    total_sites: int

    @property
    def assignments(self) -> dict[str, str]:
        """requirement id -> site id, in requirement-sorted order."""
        return {s.requirement_id: s.site_id for s in self.stuffed_sites}

    @property
    def value_stuffing(self) -> tuple[StuffValue, ...]:
        return tuple(sv for s in self.stuffed_sites for sv in s.stuffed)

    @property
    def utilization(self) -> float:
        """Fraction of the fabric's sites populated by this design."""
        if self.total_sites == 0:
            return 0.0
        return len(self.stuffed_sites) / self.total_sites

    @property
    def fits(self) -> bool:
        return not self.unfittable


def extracted_params_for_winners(match_result, decision, catalog: Catalog) -> dict:
    """Resolved winner params per requirement, for :func:`fit`'s ``resolved_params``.

    Mirrors :mod:`infersynth.synthesize`'s param resolution but keeps only the
    values the matcher *extracted* from requirement text (range-checked, no
    ``problem``). Missing dims fall back to the cell's own defaults inside
    :func:`infersynth.bind.bind_cell`, so this returns only what the FRD
    actually pinned — exactly what ``params_fixed`` should be checked against.
    """
    out: dict[str, dict[str, float]] = {}
    for req_id, finalist in decision.winners().items():
        cells = finalist.chain.cells
        if len(cells) != 1:
            continue  # v0 fit handles single-cell winners
        cell_key = cells[0]
        for cand in match_result.candidates.get(req_id, ()):
            if cand.cell_key == cell_key:
                out[req_id] = {p.name: p.value for p in cand.params if p.problem is None}
                break
        out.setdefault(req_id, {})
    return out


def _cell_fragment_refs(cell) -> list[str]:
    """Every fragment ref the cell names: binding refs + candidate ``maps`` refs.

    Value stuffing needs only binding refs, but the ref map (and the BOM) needs
    the whole footprint set — e.g. an op-amp ``U1`` that carries an MPN
    candidate but no value binding. Sorted for determinism.
    """
    refs: set[str] = set(cell.bindings)
    for cand in (cell.selection or {}).get("candidates") or []:
        refs.update(cand.get("maps", {}))
    return sorted(refs)


def _binding_is_stuffable(cell, fragment_ref: str, stuffable: tuple[str, ...]) -> bool:
    """True when *fragment_ref*'s binding expression references a stuffable param."""
    expr = cell.bindings.get(fragment_ref)
    if expr is None:
        return False
    try:
        return bool(free_names(expr) & set(stuffable))
    except BindingError:
        return False


def _resolve_site(
    site: Site,
    req_id: str,
    cell_key: str,
    catalog: Catalog,
    resolved_params: dict | None,
    diagnostics: list[str],
) -> StuffedSite:
    """Bind a claimed site's values and check its ``params_fixed`` contract."""
    rp = (resolved_params or {}).get(req_id, {})

    # params_fixed contract: any FRD-pinned value for a baked dim must agree.
    for pname in sorted(site.params_fixed):
        fixed_val = site.params_fixed[pname]
        if pname in rp and abs(float(rp[pname]) - fixed_val) > _PARAM_EQ_TOL:
            diagnostics.append(
                f"params_fixed mismatch at site {site.id!r} (req {req_id}): the routed "
                f"topology bakes {pname}={fixed_val} but the winner resolved "
                f"{pname}={rp[pname]} — this fabric cannot honor that requirement"
            )

    if catalog is None or resolved_params is None:
        return StuffedSite(req_id, site.id, cell_key)

    cell = catalog.get(cell_key)
    # merged binding params: baked dims + FRD-resolved stuffable dims; the rest
    # fall to cell defaults inside bind_cell.
    merged: dict[str, float] = dict(site.params_fixed)
    for pname in site.params_stuffable:
        if pname in rp:
            merged[pname] = float(rp[pname])
    try:
        bound = bind_cell(cell, merged)
    except BindingError as exc:
        diagnostics.append(
            f"value-stuffing failed at site {site.id!r} (req {req_id}, {cell_key}): {exc}"
        )
        return StuffedSite(req_id, site.id, cell_key)

    try:
        ref_map = site.ref_map or derive_ref_map(_cell_fragment_refs(cell), list(site.refs))
    except ValueError as exc:
        diagnostics.append(f"ref mapping failed at site {site.id!r} (req {req_id}): {exc}")
        return StuffedSite(req_id, site.id, cell_key)

    bound_values: dict[str, float] = {}
    stuffed: list[StuffValue] = []
    for frag_ref in sorted(cell.bindings):
        board_ref = ref_map.get(frag_ref)
        if board_ref is None:
            diagnostics.append(
                f"site {site.id!r} (req {req_id}): fragment ref {frag_ref!r} has no "
                f"board ref in the site's refs {list(site.refs)}"
            )
            continue
        bound_values[board_ref] = bound[frag_ref]
        if _binding_is_stuffable(cell, frag_ref, site.params_stuffable):
            stuffed.append(
                StuffValue(
                    site_id=site.id,
                    board_ref=board_ref,
                    fragment_ref=frag_ref,
                    value=bound[frag_ref],
                    fmt=cell.binding_formats.get(frag_ref),
                )
            )
    return StuffedSite(
        requirement_id=req_id,
        site_id=site.id,
        cell_key=cell_key,
        ref_map=ref_map,
        bound_values=bound_values,
        stuffed=tuple(stuffed),
    )


def fit(
    decision_winners: dict[str, str],
    fabric: Fabric,
    *,
    catalog: Catalog | None = None,
    resolved_params: dict | None = None,
) -> FitResult:
    """Assign each decided winner to a compatible free site (deliverable 2).

    *decision_winners* maps requirement id -> a single winning cell key (v0
    fits single-cell winners). Assignment is deterministic: requirements in
    sorted id order claim the first free site (declared order) whose accepted
    ``cells`` set contains the winner. Unfittable winners become utilization
    diagnostics; leftover sites become the DNP set per ``tie_off`` policy.

    ``catalog`` + ``resolved_params`` are keyword-only extras (mirroring
    :func:`infersynth.pack.pack`): with both present, ``fit`` checks each site's
    ``params_fixed`` and computes value-stuffing; omitting them yields an
    assignment-only fit (no value stuffing). See
    :func:`extracted_params_for_winners`.
    """
    diagnostics: list[str] = []
    occupied: set[str] = set()
    stuffed_sites: list[StuffedSite] = []
    unfittable: list[tuple[str, str]] = []

    for req_id in sorted(decision_winners):
        cell_key = decision_winners[req_id]
        site = next(
            (s for s in fabric.sites if s.id not in occupied and cell_key in s.cells),
            None,
        )
        if site is None:
            unfittable.append((req_id, cell_key))
            total = sum(1 for s in fabric.sites if cell_key in s.cells)
            used = sum(1 for s in fabric.sites if s.id in occupied and cell_key in s.cells)
            diagnostics.append(
                f"unfittable: requirement {req_id} won {cell_key} but no free site "
                f"accepts it (needs 1 more site of {cell_key}; fabric has {total} "
                f"such site(s), {used} already claimed)"
            )
            continue
        occupied.add(site.id)
        stuffed_sites.append(
            _resolve_site(site, req_id, cell_key, catalog, resolved_params, diagnostics)
        )

    # leftover sites -> DNP per tie_off policy (unstuffed-sites-inert).
    dnp_sites: list[str] = []
    dnp_refs: list[str] = []
    for s in fabric.sites:
        if s.id in occupied:
            continue
        dnp_sites.append(s.id)
        dnp_refs.extend(s.refs)
        tie = fabric.tie_off.get(s.id)
        if tie is None:
            diagnostics.append(
                f"site {s.id!r} left unstuffed with no tie_off declaration — "
                f"defaulting to dnp-all (declare `tie_off: {{{s.id}: {{policy: dnp-all}}}}` "
                f"to certify its inertness, FABRIC.md)"
            )

    return FitResult(
        stuffed_sites=tuple(stuffed_sites),
        dnp_sites=tuple(dnp_sites),
        dnp_refs=tuple(dnp_refs),
        unfittable=tuple(unfittable),
        diagnostics=tuple(diagnostics),
        total_sites=len(fabric.sites),
    )
