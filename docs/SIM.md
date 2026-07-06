# SIM.md — the behavioral simulation tier (sim-gate v0)

> Status: v0, shipped. This is the **graceful-bootstrap simulation tier** below
> the SystemC-AMS-by-emission end-state (DESIGN.md §4). It makes the
> `simulation` verification gate (DESIGN.md §7 gate 1) *real* — deterministic,
> pure-Python, zero-C++-dependency — for any cell that carries a behavioral
> model. Cells without a model skip loudly; nothing is silently "verified".

## 1. Why a bootstrap tier

DESIGN.md §4 fixes the end state: the compiler *emits* a SystemC-AMS top and the
sim gate compiles + runs it against the spec's testbench. That toolchain is not
wired up. Rather than leave `simulation` a SKIPPED stub forever, v0 defines a
tiny **timed-dataflow (TDF-style)** kernel in `infersynth/sim/` and a cell-format
convention (`model/behavior.py` + `testbench/tb.py`) so behavioral cells are
verified *today*. The kernel documents its **swap seam** to SystemC-AMS
(`infersynth/sim/kernel.py` docstring): the block contract the models are written
against does not move when the real kernel lands — only its realization does.
This mirrors fai-recon's Python-kernel → Accellera-kernel swap table
(RECON_HARVEST §1); recon's kernel is discrete-event digital MMIO, ours is analog
dataflow, so we adopt the *style* and wrote our own engine.

## 2. The kernel (`infersynth/sim/`)

* **Signal** — a named `float` stream; one sample recorded per step.
* **Block** — declares `name`, `inputs`, `outputs` (port-name tuples) and a pure
  `step(t, dt, inputs: dict[str, float]) -> dict[str, float]`. Stateful blocks
  keep state on `self`.
* **Binding** — each block port maps to a signal name (identity by default);
  this is the netlist. A signal has at most one driver.
* **Simulation** — `add(block, bindings)`, then `run()` topologically orders
  blocks (Kahn, **name-sorted at every tie**), runs `n_steps` at fixed `dt`
  (step *i* is time `i*dt`), and returns `{signal: [samples]}`. The graph must
  be a DAG; feedback is folded into a block (the ideal op-amp folds its feedback
  into the closed-loop gain) or broken by a delay element — standard TDF.

`sources.py` provides `DCSource` and `SineSource` (both stateless, computed from
`t`). `checks.py` provides `amplitude_ratio`, `clipped_within`, `settles_to` —
each returns a `Check` whose `evaluate(traces) -> (ok, detail)` always reports
the measured numbers.

**Repeatability (SELECTION.md §8, BINDING):** no wall-clock, no randomness, every
ordering name-sorted. `infersynth/sim/` imports neither `random` nor `time`
(enforced by a test).

## 3. Behavioral model convention — `model/behavior.py`

A cell that wants behavioral simulation ships `model/behavior.py` exporting:

```python
def make_behavior(params: dict) -> Block: ...
```

* `params` are the **resolved idiom params** — the cell's `idioms.params` after
  `bind_cell` validation and default-merging (the same values the fragment
  bindings evaluate over). The gate computes these; the model just reads them.
* The returned block's ports (`inputs ∪ outputs`) **MUST equal** the cell.yaml
  `ports` names exactly. The gate fails the cell on any mismatch.

The two op-amp models implement ideal-with-rails math, referenced to `GND`:

```
OUT = clip(GND + gain*(IN - GND), VEE + margin, VCC - margin)
```

`opamp-gain-noninverting` is one channel (`gain` from params);
`opamp-gain-x4-noninverting` is four independent channels (`gain1..gain4`)
sharing one `VCC`/`VEE`/`GND` set, in a single block.

## 4. Testbench convention — `testbench/tb.py`

A cell's testbench ships `testbench/tb.py` exporting:

```python
PARAMS = { ... }                        # the operating point (idiom params)
def make_testbench(params: dict) -> Testbench: ...
```

* **`PARAMS`** is the operating point: the idiom-param values the scenario runs
  at (e.g. `{"gain": 4.0}`). The gate validates `PARAMS` through `bind_cell`
  (proving the cell binds), resolves the idiom params, and passes the resolved
  dict to **both** `make_behavior` and `make_testbench`. This split — `PARAMS`
  the data, `make_testbench` the builder — keeps the two model-facing entry
  points fed from one validated source.
* **`Testbench`** declares `dt`, `n_steps`, `dut_bindings` (DUT port -> signal,
  shared by all runs), and `runs`. The DUT block itself is inserted by the gate
  (from `make_behavior`), not by the testbench — the testbench only wires it.
* **`Run`** is one scenario: `name`, `stimulus` (a tuple of `Stimulus` =
  block + bindings), `checks` (a tuple of `Check`), and optional `dt`/`n_steps`
  overrides. The gate builds a **fresh DUT and fresh Simulation per run**, so a
  stateful DUT never leaks state between scenarios.

Both op-amp testbenches use a 1 kHz sine at `dt = 1 µs`, `n_steps = 1000` (one
cycle), ±12 V rails, and two runs:

* `linear` — 1 Vpk sine, asserts measured peak-to-peak gain ≈ bound gain ±1 %
  (and output within rails).
* `overdrive` — 5 Vpk sine, asserts the output stays clipped within the rails.

## 5. The gate (`infersynth/gates/simulation.py`)

`simulation_cell_gate(cell)`:

1. **SKIP loudly** if `model/behavior.py` or `testbench/tb.py` is absent — a
   structural-only cell (connector, decoupling) is not applicable, and an
   unverified cell must never read as verified (DESIGN.md §4).
2. Load both modules by **file path** (`importlib`, namespaced per cell as
   `infersynth._sim_cells.<key>.<leaf>`) — no `sys.path` pollution.
3. Validate `PARAMS` through `bind_cell`, resolve idiom params, build the DUT,
   check DUT ports == cell.yaml ports.
4. Run every scenario deterministically; **each check becomes a diagnostic**
   (`[PASS]/[FAIL] <run>/<check>: <measured…>`). Any failing check fails the
   gate; a crashing sim fails the gate.

It is wired into `run_cell_gates` after the ERC and netlist gates, so
`infersynth gates --cell catalog/core/opamp-gain-noninverting` reports
`erc PASS`, `netlist-partition-equivalence PASS`, `simulation PASS`. The
context-based `simulation_gate(context)` stub also delegates here when given a
`cell` in its context; with no cell it still SKIPs loudly (the SystemC-AMS
end-state tier remains unwired).

## 6. Conventions this tier chose (not dictated by DESIGN/SELECTION)

* **`DEFAULT_RAIL_MARGIN = 0.1 V`** — the headroom an ideal output stops short
  of each rail. Not a cell.yaml idiom param; fixed as a tier constant in each
  `behavior.py`. (Visible in overdrive traces clipping at ±11.9 V on ±12 V rails.)
* **`PARAMS` module attribute** as the testbench operating point (§4) — the
  chosen way to feed one validated param set to both model entry points.
* **DUT inserted by the gate, wired by the testbench** via `dut_bindings` —
  keeps `make_behavior` the single source of the model block and lets the gate
  rebuild it fresh per run.
* **Source output port defaults to `"out"`**, bound to a signal by the testbench.
* **Gain measurement = peak-to-peak ratio** over the recorded traces
  (`amplitude_ratio`); exact in the linear region because output is a linear
  scaling of the sampled input.
* **x4 channel gains are 1, 2, 3, 4** ("gains 1..4"), driven from one shared
  input signal so each channel's gain is independently measured.
