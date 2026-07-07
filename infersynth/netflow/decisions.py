"""Wiring-decision request type (NETFLOW.md house rule: ask, never guess).

Both the declared-feeds resolver (:mod:`infersynth.netflow.feeds`) and the
design-scope convergence pass (:mod:`infersynth.netflow.converge`) reach points
where more than one port-level assignment is admissible. NETFLOW.md is emphatic
that the engine must **never guess** there: it emits a typed request naming the
competing options, resolved *between* runs into a spec/pragma ``feeds`` entry.

Why a sibling to :class:`infersynth.match.resolution.ResolutionRequest` rather
than a reuse: that type's options are competing *cell chains* carrying a score
and a measured score *variance* (RECON_HARVEST §3 — underspecification of the
matcher's ensemble). A wiring ambiguity is a different question entirely — the
cells are already decided; what is undecided is which *port* on the destination
(or which *sink* for a driver) a net should join. There is no score and no
variance to report; forcing the chain-shaped fields would be dishonest. This
sibling carries exactly the wiring options and nothing it cannot substantiate.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["WiringOption", "WiringResolutionRequest"]


@dataclass(frozen=True, order=True)
class WiringOption:
    """One admissible port-level assignment in an ambiguous wiring decision."""

    #: the driver/source endpoint, ``"<instname>.<port>"``
    source: str
    #: the sink/destination endpoint, ``"<instname>.<port>"``
    sink: str


@dataclass(frozen=True)
class WiringResolutionRequest:
    """A wiring ambiguity the engine refuses to guess (NETFLOW.md).

    ``kind`` is ``"feeds-ambiguous"`` (a declared feed edge whose endpoints do
    not pair uniquely) or ``"converge-ambiguous"`` (a design-scope front that
    meets more than one way — the ensemble-variance signal). ``edge`` names the
    decision ("R-A -> R-B" for feeds, "kind electrical" for convergence),
    ``options`` are the competing assignments, and ``message`` is the rendered
    ask (also mirrored into the plan diagnostics so ``plan.clean`` accounts for
    it).
    """

    kind: str
    edge: str
    options: tuple[WiringOption, ...]
    message: str
