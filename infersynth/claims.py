"""Evidence-fusion kernel: confidence-tagged claims → weighted resolution, dissent preserved.

This is InferSynth's standalone, deterministic primitive for **fusing competing
claims from multiple sources** about one fact. Each source emits a
provenance-and-confidence-tagged claim; :func:`arbitrate` resolves them by a
weighted vote and **keeps every losing claim** (the dissent is recorded, never
silently discarded, so it can be re-queried or shown to a human).

Why it lives here (docs/LOOM.md — dependency architecture)
----------------------------------------------------------
Nothing in InferSynth consumes this yet; it is built ahead of Loom's **Pillar 2**
multi-source vendor-import ladder, whose import problem *is* an evidence-fusion
problem: a Gerber, an IPC-D-356 netlist, and a BOM can each claim a different
pad/net/value, and the importer must resolve the conflict without dropping the
minority evidence. LOOM.md decided the arbiter kernel **migrates into InferSynth**
(``infersynth.claims``) rather than Loom depending on fai-recon for it — ported
once from recon's ``fai_recon/arbitrate.py`` with recon's implementation as the
reference, because InferSynth "half-contains it already" (see below).

Alignment with InferSynth's existing provenance/resolution vocabulary
---------------------------------------------------------------------
This kernel is deliberately native to InferSynth, not a foreign transplant:

* :class:`Provenance` uses the same *non-empty ``source`` tag* discipline as the
  matcher's ``surfaced_by`` provenance (:mod:`infersynth.match.provenance`,
  SELECTION §7: "disclosure is non-negotiable"). The default
  :data:`SOURCE_WEIGHT` table therefore weights InferSynth-native ``surfaced_by``
  families (``idiom``, ``semantic``, ``allocation``, ``pack``) alongside Loom's
  vendor-import families (``ipc-d-356``, ``gerber``, ``bom``, ``odb``).
* A losing claim beaten by higher-weighted contrary evidence mirrors the
  **revocable-claim** semantics already in :mod:`infersynth.pipeline` /
  :mod:`infersynth.pack` (a decided winner whose gates fail is a *revocable claim
  beaten by contrary evidence* — RECON_HARVEST §4/§6); here the beaten claim is
  kept as recorded dissent rather than revoked-and-forgotten.
* The confidence ladder + the injectable weight tables are ported fresh from
  recon (InferSynth has no confidence vocabulary of its own).

Determinism (house law — SELECTION §8)
--------------------------------------
Pure and deterministic: no clocks, no randomness. Ranking is a **stable** sort by
``(-weight, original_index)``, so equal-weight claims resolve to the first-seen
claim (never to arbitrary dict/set iteration order) and two runs over identical
inputs are byte-identical. Both weight tables are injectable/overridable per call
— no source is hardwired-absolute.

Usage
-----
>>> from infersynth.claims import Claim, Provenance, arbitrate
>>> claims = [
...     Claim("NET_A", Provenance("gerber", "med")),
...     Claim("NET_B", Provenance("ipc-d-356", "high")),  # netlist test data
... ]
>>> res = arbitrate(claims)
>>> res.winner.value
'NET_B'
>>> res.contested                         # the gerber claim disagrees
True
>>> [c.value for c in res.dissent]        # losers kept, never dropped
['NET_A']
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "Provenance",
    "Claim",
    "Arbitration",
    "arbitrate",
    "weigh",
    "source_weight",
    "confidence_weight",
    "SOURCE_WEIGHT",
    "CONFIDENCE_WEIGHT",
]

# --------------------------------------------------------------------------- #
# the weighting (defaults — every caller may override; NO source is absolute)
# --------------------------------------------------------------------------- #
#: Confidence ladder ``high > med > low`` (recon parity). An unknown label falls
#: to the floor (treated as ``low``).
CONFIDENCE_WEIGHT: dict[str, float] = {"high": 3.0, "med": 2.0, "low": 1.0}
_DEFAULT_CONFIDENCE = 1.0

#: Source-FAMILY base weights, matched by **longest prefix** against a
#: provenance ``source`` tag. Deliberately a *spread*, not a hierarchy of
#: absolutes: authoritative netlist test data (``ipc-d-356``) ranks high but a
#: corroborated/high-confidence claim from a weaker family can still win on the
#: additive total. Loom vendor-import families and InferSynth-native
#: ``surfaced_by`` families both appear; override the whole table per project.
SOURCE_WEIGHT: dict[str, float] = {
    # Loom Pillar 2 vendor-import families (docs/LOOM.md):
    "ipc-d-356": 4.0,   # netlist test data — authoritative connectivity/nets
    "odb": 3.5,         # ODB++ — rich, structured fab data
    "gerber": 3.0,      # copper geometry — strong for pad/geometry facts
    "bom": 3.0,         # bill of materials — strong for values / part identity
    # InferSynth-native surfaced_by provenance families (SELECTION §4/§7):
    "allocation": 2.0,  # allocation-forced candidate
    "idiom": 2.0,       # Layer-1 idiom recall
    "pack": 2.0,        # a pack-target proposal
    "semantic": 1.5,    # Layer-2 embeddings — only ever surfaces, never decides
    "heuristic": 1.0,   # any heuristic guess
}
_DEFAULT_SOURCE = 1.0   # an unrecognized source family = a weak heuristic (never 0, never ∞)


def source_weight(source: str, table: dict[str, float] | None = None) -> float:
    """Base weight for a provenance ``source`` by **longest-prefix** match in *table*.

    Longest-prefix so a specific family (``ipc-d-356.net``) beats a generic one
    (``ipc-d-356``). An unknown family falls to :data:`_DEFAULT_SOURCE` — never
    zero, never infinite. *table* defaults to :data:`SOURCE_WEIGHT`.
    """
    table = SOURCE_WEIGHT if table is None else table
    src = source or ""
    best_key, best_len = None, -1
    for key in table:
        if (src == key or src.startswith(key)) and len(key) > best_len:
            best_key, best_len = key, len(key)
    return table[best_key] if best_key is not None else _DEFAULT_SOURCE


def confidence_weight(confidence: str, table: dict[str, float] | None = None) -> float:
    """Numeric weight for a confidence label; unknown labels fall to the floor (low)."""
    table = CONFIDENCE_WEIGHT if table is None else table
    return table.get(confidence or "", _DEFAULT_CONFIDENCE)


@dataclass(frozen=True)
class Provenance:
    """Where a claim came from + how sure that source is.

    ``source`` is a non-empty provenance tag in the ``surfaced_by`` discipline of
    :mod:`infersynth.match.provenance` (SELECTION §7 — disclosure is
    non-negotiable); constructing one without it is an error. ``confidence`` is a
    label on the :data:`CONFIDENCE_WEIGHT` ladder (unknown labels score as the
    floor).
    """

    source: str
    confidence: str = "low"

    def __post_init__(self) -> None:
        if not self.source:
            raise ValueError(
                "Provenance requires a non-empty source tag "
                "(SELECTION §7: provenance disclosure is non-negotiable)"
            )


@dataclass(frozen=True)
class Claim:
    """One source's ``(value, provenance)`` assertion about a fact.

    ``value`` is arbitrary (a net role/name, a pad value, a param — the kernel is
    agnostic to *what* it fuses; callers map their facts to claims).
    """

    value: Any
    provenance: Provenance


def weigh(
    prov: Provenance,
    *,
    source_table: dict[str, float] | None = None,
    confidence_table: dict[str, float] | None = None,
) -> float:
    """The scalar weight of one provenance = source-family base + confidence (additive).

    Additive so a high-confidence weaker-family claim can outrank a low-confidence
    stronger-family one (no source is absolute) — recon's ADR-007 crux, preserved.
    """
    return source_weight(prov.source, source_table) + confidence_weight(
        prov.confidence, confidence_table
    )


@dataclass(frozen=True)
class Arbitration:
    """The outcome of one arbitration: the winning claim + every dissenting claim (kept).

    ``dissent`` is every non-winning claim in descending-weight order (the losing
    evidence, preserved for conflict recording / re-query — never discarded).
    ``key`` (set by :func:`arbitrate`) is the value-equivalence normalizer the
    ``contested`` / ``dissenters`` views use, so claims that *agree* under a
    normalization (e.g. case-insensitive net role) read as corroboration, not
    conflict.
    """

    winner: Claim
    dissent: tuple[Claim, ...] = ()
    key: Callable[[Any], Any] | None = field(default=None, compare=False)

    @property
    def contested(self) -> bool:
        """True iff any dissenting claim disagrees with the winner on (normalized) *value*.

        Losers that merely corroborate the winning value (same value, weaker
        source) are NOT contested.
        """
        norm = self.key or (lambda v: v)
        wv = norm(self.winner.value)
        return any(norm(c.value) != wv for c in self.dissent)

    @property
    def dissenters(self) -> tuple[Claim, ...]:
        """The dissenting claims that actually disagree on value (corroborators dropped)."""
        norm = self.key or (lambda v: v)
        wv = norm(self.winner.value)
        return tuple(c for c in self.dissent if norm(c.value) != wv)


def arbitrate(
    claims: list[Claim] | tuple[Claim, ...],
    *,
    source_table: dict[str, float] | None = None,
    confidence_table: dict[str, float] | None = None,
    key: Callable[[Any], Any] | None = None,
) -> Arbitration:
    """Resolve competing claims by weighted vote; KEEP the losers as recorded dissent.

    The winner is the highest-weighted claim. Deterministic tie-break (SELECTION
    §8): higher weight first, then the claim's **original position** (stable) — so
    equal-weight claims resolve to the first-seen claim, never to arbitrary
    ordering. Two runs over identical inputs are byte-identical.

    ``source_table`` / ``confidence_table`` override the default weight tables
    (no source is hardwired-absolute). ``key`` (optional) is a value-normalizer
    used only by the returned :attr:`Arbitration.contested` / ``dissenters``
    views to tell genuine disagreement from corroboration; ranking still scores
    every individual claim.

    FAIL-FAST: raises :class:`ValueError` on an empty claim set (a programming
    error — there is nothing to resolve).
    """
    if not claims:
        raise ValueError("arbitrate() requires at least one claim")
    scored = [
        (
            weigh(c.provenance, source_table=source_table, confidence_table=confidence_table),
            idx,
            c,
        )
        for idx, c in enumerate(claims)
    ]
    # sort: weight desc, then original index asc (stable, deterministic tie-break)
    scored.sort(key=lambda t: (-t[0], t[1]))
    winner = scored[0][2]
    dissent = tuple(c for _w, _idx, c in scored[1:])
    return Arbitration(winner=winner, dissent=dissent, key=key)
