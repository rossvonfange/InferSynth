"""Cost vectors (SELECTION §6): a typed model of a cell's ``costs:`` block and
the composition rule for a chain/cover.

SELECTION §6 gives every candidate mapping a *cost vector*; ``cell.yaml`` carries
it under ``costs:``::

    costs:
      bom: {qty1: 1.72, qty1k: 0.61}   # currency-normalized, per volume tier
      area_mm2: 180
      power_mw: 15
      part_count: 6
      dev_hours: 0        # firmware/config engineering (the code dimension)
      production_steps: [] # e.g. [programming, calibration]
      sourcing_risk: 1    # 0=jellybean … 3=single-source

This module loads that block into a :class:`CostVector` and composes vectors by
**vector sum** over a chain/cover. Per SELECTION §6 accounting rule 1
("covers compete against covers"), a chain's cost is the sum of its cells' costs
— never compared per-requirement. :func:`compose` is that sum.

Scored dimensions (7, per the WP-D1 spec):

    bom (at a profile-chosen qty tier) · area_mm2 · power_mw · part_count ·
    dev_hours · production_steps (its *count*) · sourcing_risk

**Unpriced cells (SELECTION §6 honesty rule).** A cell with *no* ``costs:``
block is ``unpriced`` — never silently free. An unpriced vector composes to an
unpriced chain (unpricedness is contagious across a cover), and the scorer
(:mod:`infersynth.decide.profiles`) charges an unpriced chain the worst value in
the candidate set on *every* dimension. A cell that *has* a ``costs:`` block but
omits an individual dimension is priced; the missing dimension defaults to 0
(it declared its costs; that dimension is genuinely nil).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "CostVector",
    "NUMERIC_DIMENSIONS",
    "SCORE_DIMENSIONS",
    "cost_vector_for_cell",
    "compose",
]

#: The plain additive numeric dimensions (bom + production_steps are handled
#: specially: bom is a per-qty dict, production_steps a list whose *count* scores).
NUMERIC_DIMENSIONS: tuple[str, ...] = (
    "area_mm2",
    "power_mw",
    "part_count",
    "dev_hours",
    "sourcing_risk",
)

#: The full ordered set of scored dimensions (SELECTION §6). ``bom`` resolves to
#: a scalar at score time via the profile's chosen qty tier; ``production_steps``
#: scores by its element count.
SCORE_DIMENSIONS: tuple[str, ...] = (
    "bom",
    "area_mm2",
    "power_mw",
    "part_count",
    "dev_hours",
    "production_steps",
    "sourcing_risk",
)


@dataclass(frozen=True)
class CostVector:
    """A typed, composable cost vector for a cell or a whole chain/cover.

    ``bom`` maps a qty tier (``qty1``, ``qty1k``, …) to a currency-normalized
    unit price; the scorer resolves it to a scalar with the profile's chosen
    tier. ``production_steps`` keeps the step labels (its *count* is the scored
    dimension). ``unpriced`` is True for a cell with no ``costs:`` block (or a
    composition that absorbed one) — see the module docstring.
    """

    bom: dict[str, float] = field(default_factory=dict)
    area_mm2: float = 0.0
    power_mw: float = 0.0
    part_count: float = 0.0
    dev_hours: float = 0.0
    production_steps: tuple[str, ...] = ()
    sourcing_risk: float = 0.0
    unpriced: bool = False

    def bom_at(self, qty: str) -> float:
        """Unit BOM price at qty tier *qty*.

        Falls back deterministically to the lexicographically-smallest tier
        present when *qty* is absent (so ``production`` still scores a cell that
        only quotes ``qty1``); ``0.0`` when the vector quotes no BOM at all.
        """
        if qty in self.bom:
            return self.bom[qty]
        if not self.bom:
            return 0.0
        return self.bom[min(self.bom)]

    def dimension(self, dim: str, qty: str) -> float:
        """The scalar value of scored dimension *dim* (bom resolved at *qty*)."""
        if dim == "bom":
            return self.bom_at(qty)
        if dim == "production_steps":
            return float(len(self.production_steps))
        return float(getattr(self, dim))


def cost_vector_for_cell(costs: dict[str, Any] | None) -> CostVector:
    """Build a :class:`CostVector` from a cell's (possibly lockfile-overridden)
    ``costs`` mapping. An empty/None mapping yields an ``unpriced`` vector."""
    if not costs:
        return CostVector(unpriced=True)
    bom_raw = costs.get("bom") or {}
    bom = {str(k): float(v) for k, v in bom_raw.items()}
    steps = costs.get("production_steps") or []
    return CostVector(
        bom=bom,
        area_mm2=float(costs.get("area_mm2", 0) or 0),
        power_mw=float(costs.get("power_mw", 0) or 0),
        part_count=float(costs.get("part_count", 0) or 0),
        dev_hours=float(costs.get("dev_hours", 0) or 0),
        production_steps=tuple(str(s) for s in steps),
        sourcing_risk=float(costs.get("sourcing_risk", 0) or 0),
        unpriced=False,
    )


def compose(vectors: list[CostVector]) -> CostVector:
    """Vector-sum a chain/cover's per-cell vectors (SELECTION §6 rule 1).

    BOM sums per qty tier over the union of tiers (a cell missing a tier
    contributes 0 to it). ``production_steps`` concatenates. If *any* cell is
    unpriced the whole cover is unpriced (and the summed values are meaningless,
    so they are zeroed) — unpricedness is contagious, never silently dropped.
    An empty cover is a zero vector (not unpriced).
    """
    if not vectors:
        return CostVector()
    if any(v.unpriced for v in vectors):
        return CostVector(unpriced=True)
    bom: dict[str, float] = {}
    for v in vectors:
        for tier, price in v.bom.items():
            bom[tier] = bom.get(tier, 0.0) + price
    steps: tuple[str, ...] = ()
    for v in vectors:
        steps += v.production_steps
    return CostVector(
        bom=bom,
        area_mm2=sum(v.area_mm2 for v in vectors),
        power_mw=sum(v.power_mw for v in vectors),
        part_count=sum(v.part_count for v in vectors),
        dev_hours=sum(v.dev_hours for v in vectors),
        production_steps=steps,
        sourcing_risk=sum(v.sourcing_risk for v in vectors),
        unpriced=False,
    )
