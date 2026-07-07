"""The design-simulation gate (SEED_PLAN.md §1 acceptance criterion 3).

Where ``simulation`` (``infersynth/gates/simulation.py``) verifies ONE cell's
behavioral model against its own testbench, this gate verifies the WHOLE
synthesized design's behavioral chain end-to-end: it composes every
instantiated cell's ``model/behavior.py`` block, wired together by the
:class:`~infersynth.netflow.plan.WiringPlan`'s nets
(:func:`infersynth.sim.design.build_design_sim`), drives the spec's
``testbench:`` stimuli, runs the composed graph deterministically, and turns
each declared check into a diagnostic.

Three loud outcomes (DESIGN.md §4 — never a silent partial pass):

1. **SKIPPED** — the spec declares no ``testbench:`` (nothing to verify), or the
   wiring plan is absent / not ``clean`` (an unwired design has floating nets;
   a whole-design sim needs the fully-wired plan);
2. **SKIPPED** — one or more NON-structural instances ship no behavioral model
   (naming them): the chain can't be simulated end-to-end without them, and a
   partial sim must never read as a full one. Structural passthroughs
   (connectors, decoupling) are exempt — they carry no transfer function;
3. **PASS/FAIL** — the composed sim ran; every ``testbench:`` check is a
   diagnostic, any failing check fails the gate, a crashing sim fails the gate.

Determinism (SELECTION.md §8): fixed ``dt``, name-sorted iteration, no
wall-clock, no randomness — two runs produce byte-identical traces.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from infersynth.gates.runner import GateResult
from infersynth.sim.checks import Check, amplitude_ratio, clipped_within, settles_to
from infersynth.sim.design import DesignSimBuild, DesignSimError, build_design_sim
from infersynth.sim.sources import DCSource, SineSource

__all__ = ["design_simulation_gate", "run_design_simulation"]

_GATE = "design-simulation"


class _MissingBehaviorError(Exception):
    """Internal: a non-structural instance had no behavioral model (→ SKIP)."""

    def __init__(self, instnames: tuple[str, ...]) -> None:
        self.instnames = instnames


def _stimulus_block(stim, signal: str, index: int):
    """Build a source block for one testbench stimulus, bound to *signal*."""
    name = f"__stim_{index}__{signal}"
    if stim.kind == "sine":
        return SineSource(
            name=name,
            amplitude=float(stim.params["amplitude"]),
            freq_hz=float(stim.params["freq_hz"]),
            offset=float(stim.params.get("offset", 0.0)),
            phase=float(stim.params.get("phase", 0.0)),
        )
    if stim.kind == "dc":
        return DCSource(name=name, value=float(stim.params["value"]))
    raise DesignSimError(f"unknown stimulus kind {stim.kind!r}")


def _make_check(check, build: DesignSimBuild) -> Check:
    """Map a testbench check to a :mod:`infersynth.sim.checks` helper.

    Net-name fields are resolved through the build's net→signal map so an author
    may reference ANY net name in a collapsed component (e.g. any of the three
    ``f_MID_*`` nets that share one virtual-ground node).
    """
    p = check.params
    if check.kind == "amplitude_ratio":
        return amplitude_ratio(
            build.resolve_net(p["input"]),
            build.resolve_net(p["output"]),
            float(p["expected"]),
            float(p["tol_pct"]),
        )
    if check.kind == "settles_to":
        return settles_to(
            build.resolve_net(p["signal"]),
            float(p["value"]),
            float(p["tol"]),
            int(p.get("after_step", 0)),
        )
    if check.kind == "clipped_within":
        return clipped_within(
            build.resolve_net(p["signal"]),
            float(p["lo"]),
            float(p["hi"]),
            float(p.get("eps", 1e-9)),
        )
    raise DesignSimError(f"unknown check kind {check.kind!r}")


def run_design_simulation(
    result_like, catalog, testbench
) -> tuple[list[tuple[str, bool, str]], DesignSimBuild]:
    """Compose + run the design sim; return ``(check rows, build)``.

    Raises :class:`_MissingBehaviorError` when a non-structural instance lacks a
    model (the gate turns that into a loud SKIP). Deterministic: a fresh build
    and :class:`~infersynth.sim.kernel.Simulation` are constructed each call.
    """
    build = build_design_sim(
        result_like, catalog, testbench.rails, testbench.dt, testbench.n_steps
    )
    if build.missing_behavior:
        raise _MissingBehaviorError(build.missing_behavior)

    for i, stim in enumerate(testbench.stimuli):
        sig = build.resolve_net(stim.signal)
        build.simulation.add(_stimulus_block(stim, sig, i), {"out": sig})

    traces = build.simulation.run()
    rows: list[tuple[str, bool, str]] = []
    for check in testbench.checks:
        made = _make_check(check, build)
        ok, detail = made.evaluate(traces)
        rows.append((made.name, ok, detail))
    return rows, build


def design_simulation_gate(context: Mapping[str, Any]) -> GateResult:
    """Design-level behavioral gate. Context keys: ``result``, ``catalog``,
    ``testbench`` (a :class:`infersynth.spec.DesignTestbench` or ``None``)."""
    testbench = context.get("testbench")
    if testbench is None:
        return GateResult.skipped(
            _GATE,
            "not verified end-to-end: the spec declares no 'testbench:' "
            "(add rails/dt/n_steps/stimuli/checks to simulate the whole chain)",
        )
    result = context.get("result")
    catalog = context.get("catalog")
    if result is None or catalog is None:
        return GateResult.skipped(
            _GATE, "no synthesis result / catalog in context (design sim needs both)"
        )
    plan = getattr(result, "wiring_plan", None)
    if plan is None:
        return GateResult.skipped(
            _GATE, "no wiring plan on the result (synthesize with wiring=True)"
        )
    if not plan.clean:
        return GateResult.skipped(
            _GATE,
            "wiring plan is not clean (unwired signal ports / diagnostics remain) "
            "— a whole-design sim needs a fully-wired plan; wire the residual "
            "ports (see SYNTHESIS.md) and re-run",
        )

    try:
        rows, build = run_design_simulation(result, catalog, testbench)
    except _MissingBehaviorError as exc:
        return GateResult.skipped(
            _GATE,
            "not verified end-to-end: instance(s) "
            f"{list(exc.instnames)} ship no model/behavior.py and are not "
            "structural passthroughs — the chain cannot be simulated without them",
        )
    except DesignSimError as exc:
        return GateResult.failed(_GATE, f"design sim malformed: {exc}")
    except Exception as exc:  # a crashing sim is a failing gate
        return GateResult.failed(_GATE, f"{type(exc).__name__}: {exc}")

    # Loud structural notes (informational; not pass/fail themselves).
    notes: list[str] = []
    if build.passthrough:
        notes.append(
            "[note] structural passthrough (no transfer function): "
            + ", ".join(build.passthrough)
        )
    if build.floating_ports:
        notes.append(
            "[note] FLOATING input port(s) wired to no net (held at 0.0): "
            + ", ".join(f"{i}.{p}" for i, p in build.floating_ports)
        )

    if not rows:
        return GateResult.skipped(
            _GATE, *(notes + ["testbench declared no checks"])
        )

    diagnostics = tuple(
        notes
        + [
            f"[{'PASS' if ok else 'FAIL'}] {label}: {detail}"
            for label, ok, detail in rows
        ]
    )
    if all(ok for _, ok, _ in rows):
        return GateResult.passed(_GATE, *diagnostics)
    return GateResult.failed(_GATE, *diagnostics)
