"""Recovered-fabric verification gate — Loom Pillar 2's honesty piece.

Loom recovers fabrics from vendor reference designs by RECOGNIZING them as
catalog cells (:mod:`infersynth.recognize`): the vendor's routing is kept
verbatim, and each recognized cell instance becomes a fabric *site* with
recovered ``params_fixed``. Components no recognized instance claims are the
**glue** — real, but not a catalog cell.

The Loom Verification stance (``docs/LOOM.md``): *"Recovered fabrics (Pillar 2)
get partial fabric-CI: recognized cells verify against their golden partitions;
glue sites are declared-not-verified and loudly marked (the InferSynth honesty
discipline applies unchanged)."* This module is that partial fabric-CI. It does
NOT trust the recognizer's own output — it INDEPENDENTLY re-verifies each
recognized site against the source netlist, two ways:

* **Structural** — re-run the anchored subgraph match
  (:func:`infersynth.recognize.match.match_cell`) of the site's cell golden
  partition against the source netlist, anchored at the site's own anchor ref.
  The site PASSES structurally iff the match still succeeds AND claims exactly
  the refs the recovered site declares. This is the recognizer's STEP 2 rerun
  as a check: a site that no longer matches (a ref was swapped, a part removed)
  fails loudly with the mismatching refs named.
* **Parametric** — push the recovered ``params_fixed`` FORWARD through the
  cell's binding math (:mod:`infersynth.bind.expr`, the same evaluator
  :mod:`infersynth.recognize.invert` inverts) and check they reproduce the
  observed component values within tolerance. The site PASSES parametrically iff
  the max relative residual is ``<= tol`` and no param was left unresolved. A
  perturbed recovered value pushes the residual past ``tol`` and fails the site.

Glue (residual, unrecognized) components are **declared-not-verified**: counted
and listed by ref, never hidden and never silently passed — but their presence
does NOT fail the gate (glue is a legitimate, expected part of a recovered
fabric; it is honestly marked unverified, not treated as an error). The overall
verdict is PASS iff every recognized site verifies.

Pure and deterministic (SELECTION.md §8): the match rerun and the push-forward
are both deterministic; two runs are byte-identical.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from infersynth.bind.expr import BindingError, evaluate
from infersynth.catalog import Catalog
from infersynth.gates.runner import GateResult
from infersynth.recognize.match import CellMatch, load_golden_graph, match_cell
from infersynth.recognize.netlist import DesignNetlist, load_design_netlist
from infersynth.recognize.recognizer import RecognitionResult

__all__ = [
    "SiteVerification",
    "GlueSite",
    "RecoveredFabricReport",
    "verify_recovered_fabric",
    "recovered_fabric_gate",
]

SCHEMA = "infersynth.gates.recovered-fabric/v0"

#: Default max relative residual tolerated on the parametric push-forward. A
#: cleanly synthesized/recovered board reproduces its values exactly (residual
#: ~1e-16); the margin absorbs only float round-trip noise, not a real drift.
DEFAULT_TOL = 1e-6


@dataclass(frozen=True)
class SiteVerification:
    """The verification outcome of one recovered SITE (a recognized instance).

    ``structural_ok``: the anchored subgraph match still holds against the
    source netlist and claims exactly ``design_refs``. ``parametric_ok``: the
    recovered params push forward to the observed values within ``tolerance``
    and no param was unresolved. ``residual`` is the max relative push-forward
    error; ``detail`` carries loud human diagnostics for any failure or caveat.
    """

    site_id: str
    cell_key: str
    anchor_ref: str
    design_refs: tuple[str, ...]
    structural_ok: bool
    parametric_ok: bool
    residual: float
    tolerance: float
    detail: tuple[str, ...] = ()

    @property
    def verified(self) -> bool:
        return self.structural_ok and self.parametric_ok

    def to_dict(self) -> dict[str, Any]:
        return {
            "site_id": self.site_id,
            "cell_key": self.cell_key,
            "anchor_ref": self.anchor_ref,
            "design_refs": list(self.design_refs),
            "structural_ok": self.structural_ok,
            "parametric_ok": self.parametric_ok,
            "verified": self.verified,
            "residual": self.residual,
            "tolerance": self.tolerance,
            "detail": list(self.detail),
        }


@dataclass(frozen=True)
class GlueSite:
    """A declared-not-verified glue component (a recognizer residual)."""

    ref: str
    cls: str
    value: str
    mpn: str

    def to_dict(self) -> dict[str, Any]:
        return {"ref": self.ref, "class": self.cls, "value": self.value, "mpn": self.mpn}


@dataclass(frozen=True)
class RecoveredFabricReport:
    """Partial fabric-CI over a recovered fabric: verified sites + glue.

    The verdict (:pyattr:`verified`) is PASS iff every recognized site verified.
    Glue presence never fails the verdict but is always surfaced
    (``glue`` + :pyattr:`glue_count`) — the honesty requirement. Cells the
    catalog cannot recognize (no golden partition) are carried through as a
    reported gap, not a failure.
    """

    sites: tuple[SiteVerification, ...] = ()
    glue: tuple[GlueSite, ...] = ()
    unrecognizable_cells: tuple[str, ...] = ()
    tolerance: float = DEFAULT_TOL

    @property
    def verified_sites(self) -> tuple[SiteVerification, ...]:
        return tuple(s for s in self.sites if s.verified)

    @property
    def failed_sites(self) -> tuple[SiteVerification, ...]:
        return tuple(s for s in self.sites if not s.verified)

    @property
    def glue_count(self) -> int:
        return len(self.glue)

    @property
    def verified(self) -> bool:
        """PASS iff every recognized site verified. Glue never fails this."""
        return all(s.verified for s in self.sites)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "verdict": "pass" if self.verified else "fail",
            "tolerance": self.tolerance,
            "summary": {
                "sites_total": len(self.sites),
                "sites_verified": len(self.verified_sites),
                "sites_failed": len(self.failed_sites),
                "glue_declared_not_verified": self.glue_count,
                "unrecognizable_cells": len(self.unrecognizable_cells),
            },
            "sites": [s.to_dict() for s in self.sites],
            "glue": [g.to_dict() for g in self.glue],
            "unrecognizable_cells": list(self.unrecognizable_cells),
        }

    def to_gate_result(self, *, gate: str = "recovered-fabric") -> GateResult:
        """Fold this report into the house :class:`GateResult` shape.

        A FAIL itemizes each failed site; a PASS still reports the glue count
        loudly in its diagnostics (glue is unverified, never hidden).
        """
        glue_note = (
            f"{self.glue_count} glue component(s) DECLARED-NOT-VERIFIED: "
            f"{[g.ref for g in self.glue]}"
            if self.glue
            else "no glue (all components claimed by a recognized site)"
        )
        if not self.verified:
            diags = [
                f"site {s.site_id!r} ({s.cell_key}) FAILED: " + "; ".join(s.detail)
                for s in self.failed_sites
            ]
            diags.append(glue_note)
            return GateResult.failed(gate, *diags)
        return GateResult.passed(
            gate,
            f"{len(self.verified_sites)} recovered site(s) verified "
            f"(structural + parametric, tol={self.tolerance:g})",
            glue_note,
        )

    def to_markdown(self) -> str:
        v = "PASS" if self.verified else "FAIL"
        lines = [
            "# Recovered-fabric verification report",
            "",
            f"- schema: `{SCHEMA}`",
            f"- **VERDICT: {v}**",
            f"- sites verified: **{len(self.verified_sites)}/{len(self.sites)}** "
            f"(tol={self.tolerance:g})",
            f"- glue (declared-not-verified): **{self.glue_count}**",
        ]
        if self.unrecognizable_cells:
            lines.append(
                f"- catalog cells with no golden partition (unrecognizable): "
                f"**{len(self.unrecognizable_cells)}**"
            )
        lines.append("")
        if self.sites:
            lines += ["## Recovered sites", ""]
            for s in self.sites:
                mark = "OK" if s.verified else "FAIL"
                lines.append(
                    f"- [{mark}] **{s.site_id}** — {s.cell_key} @ `{s.anchor_ref}` "
                    f"→ refs {list(s.design_refs)}"
                )
                lines.append(
                    f"  - structural={'pass' if s.structural_ok else 'FAIL'}, "
                    f"parametric={'pass' if s.parametric_ok else 'FAIL'} "
                    f"(residual={s.residual:.3g}, tol={s.tolerance:g})"
                )
                for d in s.detail:
                    lines.append(f"  - {d}")
            lines.append("")
        # Glue is ALWAYS listed loudly — never hidden, never silently passed.
        lines += ["## Glue — DECLARED-NOT-VERIFIED", ""]
        if self.glue:
            lines.append(
                f"!! {self.glue_count} component(s) are glue: recognized as no catalog "
                "cell. They are part of the recovered fabric but are NOT verified. !!"
            )
            for g in self.glue:
                mpn = f" mpn={g.mpn}" if g.mpn else ""
                lines.append(f"- `{g.ref}` ({g.cls}) value={g.value}{mpn}")
        else:
            lines.append("- none — every component is claimed by a recognized site.")
        lines.append("")
        if self.unrecognizable_cells:
            lines += ["## Catalog gap (cells with no golden partition)", ""]
            for k in self.unrecognizable_cells:
                lines.append(f"- `{k}` — cannot be recognized or verified (no golden_netlist)")
            lines.append("")
        return "\n".join(lines)


def _observed_from_match(match: CellMatch, design: DesignNetlist) -> dict[str, float]:
    """Golden ref -> observed numeric value, read from *design* through *match*.

    Mirrors the recognizer's own STEP-3 observation extraction
    (``recognizer._observed``): for each golden ref the match binds to a design
    ref, take that design component's invertible value (``None`` values — e.g. an
    op-amp's symbolic value — are skipped, exactly as inversion skips them).
    """
    out: dict[str, float] = {}
    for gref, dref in match.phi.items():
        comp = design.components.get(dref)
        if comp is not None and comp.value is not None:
            out[gref] = comp.value
    return out


def _push_forward_residual(
    cell: Any, observed: dict[str, float], params: dict[str, float]
) -> float:
    """Max relative error pushing *params* FORWARD through the cell bindings.

    This is the parametric check: the recovered params, evaluated through the
    same binding expressions :mod:`infersynth.recognize.invert` inverted, must
    reproduce the observed component values. ``inf`` on any un-evaluable binding.
    """
    worst = 0.0
    for ref, expr in cell.bindings.items():
        if ref not in observed:
            continue
        try:
            pred = evaluate(expr, params)
        except (BindingError, ZeroDivisionError, ValueError, OverflowError):
            return float("inf")
        obs = observed[ref]
        denom = max(abs(obs), 1e-30)
        worst = max(worst, abs(pred - obs) / denom)
    return worst


def verify_recovered_fabric(
    recognition: RecognitionResult,
    source_netlist: DesignNetlist | str | Path,
    catalog: Catalog,
    *,
    tol: float = DEFAULT_TOL,
) -> RecoveredFabricReport:
    """Independently re-verify a recovered fabric against its source netlist.

    *recognition* is the recovered-fabric-shaped input: a
    :class:`~infersynth.recognize.recognizer.RecognitionResult` (recognized cell
    instances = fabric sites + residual = glue). *source_netlist* is the design
    netlist the fabric was recovered from — a
    :class:`~infersynth.recognize.netlist.DesignNetlist` or a path to a KiCad
    ``kicadxml`` (loaded here). *catalog* resolves each site's cell + golden
    partition.

    Each recognized site is re-verified STRUCTURALLY (anchored subgraph match
    rerun; PASS iff it still matches and claims the same refs) and PARAMETRICALLY
    (recovered params pushed forward reproduce the observed values within *tol*).
    Residual (glue) components are declared-not-verified and listed. Returns a
    :class:`RecoveredFabricReport`; verdict PASS iff every site verifies.

    Pure and deterministic. Never raises on a verification failure — a failure is
    a recorded FAIL in the report, not an exception (a crashing gate is a failing
    gate handled by the runner; here we itemize instead).
    """
    if isinstance(source_netlist, (str, Path)):
        design = load_design_netlist(source_netlist)
    else:
        design = source_netlist

    sites: list[SiteVerification] = []
    for inst in recognition.instances:
        detail: list[str] = []
        cell = catalog.cells.get(inst.cell_key)
        claimed = tuple(sorted(inst.design_refs))

        # --- structural: re-run the anchored subgraph match on the source ---
        structural_ok = False
        if cell is None:
            detail.append(f"cell {inst.cell_key!r} not present in catalog")
        else:
            golden = load_golden_graph(cell)
            if golden is None:
                detail.append(
                    f"cell {inst.cell_key!r} declares no golden partition — cannot verify"
                )
            else:
                # the golden ref the site is anchored on (maps to anchor_ref)
                seed_golden = next(
                    (g for g, d in inst.match.phi.items() if d == inst.anchor_ref), None
                )
                if seed_golden is None:
                    detail.append(
                        f"anchor ref {inst.anchor_ref!r} is not in the recovered match map"
                    )
                else:
                    rematch = match_cell(
                        cell, golden, design, seed_golden, inst.anchor_ref
                    )
                    if rematch is None:
                        detail.append(
                            f"STRUCTURAL: subgraph match no longer holds at anchor "
                            f"{inst.anchor_ref!r} against the source netlist"
                        )
                    elif tuple(sorted(rematch.design_refs)) != claimed:
                        detail.append(
                            f"STRUCTURAL: rematch claims refs "
                            f"{list(rematch.design_refs)} != recovered site refs "
                            f"{list(claimed)}"
                        )
                    else:
                        structural_ok = True

        # --- parametric: push recovered params forward vs observed values ---
        parametric_ok = False
        residual = float("inf")
        inv = inst.inversion
        if cell is None:
            pass  # already noted; parametric cannot run
        elif inv.unresolved:
            detail.append(
                f"PARAMETRIC: param(s) {list(inv.unresolved)} were never resolved "
                "by inversion — the recovered params are incomplete"
            )
        else:
            observed = _observed_from_match(inst.match, design)
            residual = _push_forward_residual(cell, observed, inv.params)
            if residual <= tol:
                parametric_ok = True
            else:
                detail.append(
                    f"PARAMETRIC: recovered params do not reproduce observed values — "
                    f"max relative residual {residual:.3g} > tol {tol:g}"
                )
            if inv.assumed_default:
                # not a failure, but honestly flagged: these fell back to default
                detail.append(
                    f"note: param(s) {list(inv.assumed_default)} assumed cell default "
                    "(no observation constrained them)"
                )

        sites.append(
            SiteVerification(
                site_id=inst.anchor_ref,
                cell_key=inst.cell_key,
                anchor_ref=inst.anchor_ref,
                design_refs=claimed,
                structural_ok=structural_ok,
                parametric_ok=parametric_ok,
                residual=residual,
                tolerance=tol,
                detail=tuple(detail),
            )
        )

    sites.sort(key=lambda s: (s.anchor_ref, s.cell_key))

    glue: list[GlueSite] = []
    for ref in recognition.residual:
        comp = design.components.get(ref)
        if comp is not None:
            glue.append(GlueSite(ref=ref, cls=comp.cls, value=comp.value_str, mpn=comp.mpn))
        else:
            glue.append(GlueSite(ref=ref, cls="", value="", mpn=""))
    glue.sort(key=lambda g: g.ref)

    return RecoveredFabricReport(
        sites=tuple(sites),
        glue=tuple(glue),
        unrecognizable_cells=tuple(recognition.unrecognizable_cells),
        tolerance=tol,
    )


def recovered_fabric_gate(
    recognition: RecognitionResult,
    source_netlist: DesignNetlist | str | Path,
    catalog: Catalog,
    *,
    tol: float = DEFAULT_TOL,
) -> GateResult:
    """:func:`verify_recovered_fabric` folded into the house :class:`GateResult`.

    The library entry point returns the rich :class:`RecoveredFabricReport`; this
    is the thin gate-shaped adapter for callers that aggregate :class:`GateResult`
    alongside the other verification gates.
    """
    return verify_recovered_fabric(
        recognition, source_netlist, catalog, tol=tol
    ).to_gate_result()
