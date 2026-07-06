"""Packer (WP-P1): homogeneous covers-vs-covers packing + guard-and-claim.

Folds N single-cell winners-in-waiting — the same source cell surfaced by the
matcher (WP-M1) as length-1 closed chains across *different* requirements — into
one package-sharing pack-target cell, proposed as a revocable, deterministically
keyed :class:`PackClaim`. Claims are proposals in a :class:`PackResult`, never a
mutation of the :class:`~infersynth.match.matcher.MatchResult`; the decision layer
(WP-D1) arbitrates a packed cover against the discrete composition by cost, via
:func:`infersynth.pack.decide_integration.pack_outcome`.

Entry point: :func:`pack`. See :mod:`infersynth.pack.homogeneous`.
"""

from infersynth.pack.decide_integration import (
    pack_finalists,
    pack_outcome,
    synthetic_requirement_id,
)
from infersynth.pack.homogeneous import (
    ChannelMap,
    PackClaim,
    PackKnobs,
    PackResult,
    PackTarget,
    discover_pack_targets,
    pack,
)

__all__ = [
    "ChannelMap",
    "PackClaim",
    "PackKnobs",
    "PackResult",
    "PackTarget",
    "discover_pack_targets",
    "pack",
    "pack_finalists",
    "pack_outcome",
    "synthetic_requirement_id",
]
