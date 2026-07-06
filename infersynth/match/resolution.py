"""Chain scoring + ensemble-variance-as-underspecification (RECON_HARVEST §3).

RECON_HARVEST §3: once forward/backward propagation has enumerated the closed
candidate chains, **the variance of their scores is the underspecification
signal** — a repeatable measurement of the SPEC's looseness, not solver noise
(SELECTION §8). When that variance exceeds the configured threshold the matcher
emits a typed :class:`ResolutionRequest` rather than silently picking a winner;
winner-picking is WP-D1's job, not the matcher's.

Scoring seam (WP-S1 boundary):
    A :class:`ChainScorer` maps a closed chain to a scalar. Two are shipped:

    * :class:`StructuralScorer` (default) — a deterministic *structural proxy*
      (no simulation), so a WP-M1 run needs no toolchain. It is explicitly a
      proxy, not a physical measurement.
    * :class:`SimGateScorer` — runs each chain cell through WP-S1's testbench
      runner (:func:`infersynth.gates.simulation.run_cell_simulation`) and
      scores by the fraction of behavioral checks that pass. This is the real
      WP-S1-backed signal for cells that carry a model + testbench.

    What does NOT exist yet: scoring a *whole chain* against the *spec's*
    numeric tolerances (gain/bandwidth/clipping of the composed chain). That
    needs a spec-level / SystemC-AMS chain testbench, which is WP-S2 + WP-D1
    scope — no such artifact is in the repo. :class:`SimGateScorer` therefore
    scores per-cell (each cell against its own testbench), which is the
    strongest sim signal WP-S1 actually provides. The interface is built so
    swapping in a spec-tolerance chain scorer later is a scorer change, not a
    rewrite.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from infersynth.catalog import Catalog
from infersynth.lint.diagnostics import Diagnostic, Position, Range, Severity
from infersynth.match.provenance import CandidateChain

__all__ = [
    "ChainScorer",
    "StructuralScorer",
    "SimGateScorer",
    "ResolutionOption",
    "ResolutionRequest",
    "ensemble_variance",
    "score_chains",
]


class ChainScorer(Protocol):
    """Maps a closed candidate chain to a deterministic scalar score."""

    def score(self, chain: CandidateChain, catalog: Catalog) -> float: ...


@dataclass(frozen=True)
class StructuralScorer:
    """Deterministic structural proxy score — no simulation.

    Score = ``verifiable_fraction - 0.01 * (len - 1)``, where
    ``verifiable_fraction`` is the fraction of the chain's cells that ship a
    behavioral model + testbench (higher = more of the chain is sim-verifiable)
    and the small length penalty breaks ties toward fewer parts. Bounded to
    ``[0, 1]``. This is a *proxy*, not a physical measurement — see the module
    docstring.
    """

    def score(self, chain: CandidateChain, catalog: Catalog) -> float:
        if not chain.cells:
            return 0.0
        verifiable = 0
        for key in chain.cells:
            cell = catalog.cells.get(key)
            if cell is None:
                continue
            if (cell.path / "model" / "behavior.py").is_file() and (
                cell.path / "testbench" / "tb.py"
            ).is_file():
                verifiable += 1
        frac = verifiable / len(chain.cells)
        return max(0.0, min(1.0, frac - 0.01 * (len(chain.cells) - 1)))


@dataclass(frozen=True)
class SimGateScorer:
    """WP-S1-backed scorer: fraction of behavioral checks that pass.

    Runs every chain cell that carries ``model/behavior.py`` + ``testbench/
    tb.py`` through :func:`infersynth.gates.simulation.run_cell_simulation`
    (deterministic, SELECTION §8) and scores by passing-check fraction over the
    whole chain. Structural-only cells (no model) contribute no checks. A chain
    of entirely structural cells scores ``1.0`` (nothing to falsify — the
    honest neutral, matching the sim gate's "SKIPPED, not FAILED" convention).
    """

    def score(self, chain: CandidateChain, catalog: Catalog) -> float:
        from infersynth.gates.simulation import run_cell_simulation

        total = 0
        passed = 0
        for key in chain.cells:
            cell = catalog.cells.get(key)
            if cell is None:
                continue
            if not (
                (cell.path / "model" / "behavior.py").is_file()
                and (cell.path / "testbench" / "tb.py").is_file()
            ):
                continue
            for _label, ok, _detail in run_cell_simulation(cell):
                total += 1
                passed += 1 if ok else 0
        if total == 0:
            return 1.0
        return passed / total


def score_chains(
    chains: tuple[CandidateChain, ...],
    catalog: Catalog,
    scorer: ChainScorer,
) -> tuple[CandidateChain, ...]:
    """Attach a score to every chain; preserve deterministic ordering."""
    return tuple(sorted(chain.with_score(scorer.score(chain, catalog)) for chain in chains))


def ensemble_variance(chains: tuple[CandidateChain, ...]) -> float:
    """Population variance of chain scores (RECON_HARVEST §3 signal).

    Zero for 0 or 1 chain (a single closed chain is not underspecified). Unset
    scores are treated as 0.0 (they should always be set by ``score_chains``
    before this is called).
    """
    scores = [c.score if c.score is not None else 0.0 for c in chains]
    n = len(scores)
    if n <= 1:
        return 0.0
    mean = sum(scores) / n
    return sum((s - mean) ** 2 for s in scores) / n


@dataclass(frozen=True)
class ResolutionOption:
    """One competing chain in an underspecified ensemble (the weighted claim)."""

    cells: tuple[str, ...]
    score: float


@dataclass(frozen=True)
class ResolutionRequest:
    """A typed request the matcher emits instead of guessing (SELECTION §8).

    RECON_HARVEST §6 weighted-claim discipline: the request carries the
    competing options (each a ``{cells, score}`` claim) and the measured
    ``variance``; it is resolved *between* runs (human / LLM / instrument) and
    re-enters the next run as a durable input artifact (a spec edit, an
    allocation, a tighter tolerance). The matcher never resolves it itself.
    ``diagnostic`` carries it through the existing ``frd.ambiguous`` code
    (WP2), not a new ad hoc code.
    """

    kind: str  # "underspecified-ensemble"
    requirement_id: str
    variance: float
    options: tuple[ResolutionOption, ...]
    diagnostic: Diagnostic


def make_resolution_request(
    requirement_id: str,
    file: str,
    rng: Range | None,
    chains: tuple[CandidateChain, ...],
    variance: float,
) -> ResolutionRequest:
    """Build the typed ResolutionRequest + its ``frd.ambiguous`` diagnostic."""
    options = tuple(
        ResolutionOption(cells=c.cells, score=c.score if c.score is not None else 0.0)
        for c in chains
    )
    rendered = "; ".join(f"[{'->'.join(o.cells)}]={o.score:.3f}" for o in options)
    diag = Diagnostic(
        file=file,
        range=rng if rng is not None else Range(Position(0, 0), Position(0, 0)),
        severity=Severity.ERROR,
        code="frd.ambiguous",
        message=(
            f"{requirement_id}: {len(options)} closed candidate chains with score "
            f"variance {variance:.4f} exceeds threshold — requirement is "
            f"underspecified; resolve between runs (tighten spec / add allocation). "
            f"Options: {rendered}"
        ),
        source="infersynth-match",
    )
    return ResolutionRequest(
        kind="underspecified-ensemble",
        requirement_id=requirement_id,
        variance=variance,
        options=options,
        diagnostic=diag,
    )
