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

## 7. The design tier — whole-design behavioral simulation (`infersynth/sim/design.py`)

SEED_PLAN §1 acceptance criterion 3: simulate the WHOLE synthesized design's
behavioral chain end-to-end, not just per-cell. Sits on the same v0 kernel —
a design sim is just MORE blocks wired by the WiringPlan's nets.

### Composition rules (`build_design_sim(result, catalog, rails, dt, n_steps)`)

* **Nets are electrical nodes.** WiringPlan nets sharing an ``(instname, port)``
  endpoint (e.g. the three ``f_MID_*`` nets all touching the rail splitter's
  ``VGND``) are unioned into ONE simulation signal, named by the
  lexicographically smallest net name in the component (rails keep their own
  names — rail names sort UPPERCASE-first, below the ``c_``/``f_``/``n_``
  signal-net prefixes).
* **One Block per behavioral instance:** ``make_behavior(resolved_params)`` with
  the instance's synthesize-resolved params; block ``name`` = instname; each
  port bound to its net's composed signal.
* **RAIL nets become DC sources** at the voltages of the required ``rails``
  mapping — a rail net with no declared voltage is an ERROR naming it, and a
  declared rail that is not a plan rail net is also an error. A behavior output
  landing on a rail net (the regulator's ``VOUT``) is redirected to a private
  sink signal: the declared rail is the single driver (one driver per signal).
* **Structural cells are PASSTHROUGH:** an instance with no ``model/behavior.py``
  whose technology is electromechanical/passive (or whose every port is
  ``direction: passive``) contributes no block — a connector doesn't transform;
  its ports are simply shared signals. (Each port lives in exactly one net
  under the plan model, so a structural cell cannot bridge two different nets —
  fine.) Recorded loudly as an informational note. A NON-structural instance
  missing a behavior instead triggers a loud gate SKIP naming it — a partial
  sim never reads as a full one.
* **Unwired ports float:** a port in no net binds to a private 0.0-holding
  signal and is listed loudly (``floating_ports``).

Determinism (SELECTION.md §8): the build is a pure function of
(result, catalog, rails, dt, n_steps); two runs give byte-identical traces.

### Spec `testbench:` key (v0, strict — `infersynth/spec.py`)

```yaml
testbench:
  rails: {VOUT: 5.0, GND: 0.0, VIN: 12.0}   # required; every rail net, by name
  dt: 5.0e-8                                 # required, > 0
  n_steps: 120000                            # required, positive int
  stimuli:                                   # kinds: sine | dc
    - {signal: <net>, kind: sine, amplitude: 0.001, freq_hz: 200.0, offset: 0.0}
    - {signal: <net>, kind: dc, value: 0.0}
  checks:                     # kinds: amplitude_ratio | settles_to | clipped_within
    - {kind: amplitude_ratio, input: <net>, output: <net>, expected: 100.0, tol_pct: 8.0}
    - {kind: settles_to, signal: <net>, value: 2.5, tol: 0.05}   # optional after_step
    - {kind: clipped_within, signal: <net>, lo: 0.0, hi: 5.0}    # optional eps
```

Unknown kinds/keys and missing required keys are ``SpecError``s. **Net names
are the WiringPlan's net names**: rails by rail name, feed nets
``f_SRC_DST[_ROLE]``, intra-chain ``n_<reqid>_<k>``, converged ``c_<k>`` — the
SYNTHESIS.md "Wired nets" table is how an author discovers them. Any net name
in a collapsed component resolves to the shared signal.

### The gate — `design-simulation` (`infersynth/gates/design_simulation.py`)

Runs inside ``synthesize(..., verify=True, testbench=...)``; the CLI passes the
spec's ``testbench:`` through ``infersynth synthesize --spec``, prints the gate
result, and renders it as a SYNTHESIS.md section. Loud outcomes:

1. no ``testbench:`` in the spec → **SKIPPED** loudly;
2. wiring plan absent or not ``clean`` → **SKIPPED** (a whole-design sim needs
   the fully-wired plan);
3. non-structural instance(s) with no ``model/behavior.py`` → **SKIPPED**
   naming them (structural passthroughs exempt, noted informationally);
4. otherwise compose → run deterministically → each check is a
   ``[PASS]/[FAIL]`` diagnostic; any failing check or crashing sim fails the
   gate.

``infersynth gates --design`` was left untouched (documented choice): that
path takes a bare schematic root and has no synthesis result/catalog to
compose from — the design sim's natural home is the synthesize verify path,
where the wiring plan and resolved params already exist.

### The BridgeSense-1 end-to-end check

``examples/frds/07_bridgesense_1.spec.yaml`` ships the reference testbench: a
1 mV 200 Hz sine on the bridge's positive sense net (``f_SNS_01_CND_01_P``),
negative leg DC 0 V, rails ``VOUT 5 / GND 0 / VIN 12``, asserting the
P-net → ``f_DRV_01_OUT_01`` amplitude ratio ≈ 100 ±8 % — the in-amp's ×100
with bridge RC / sallen-key (200 Hz = fc/5, |H|≈0.999) / buffer / RC driver all
≈ unity. Measured: **99.90**.

**Multi-rate caveat (the chose-dt convention):** the composed chain shares ONE
``dt``; forward-Euler needs ``dt`` at or below the FASTEST pole's time constant
(here the ADC driver RC, tau ≈ 50 ns), while the run must span the SLOWEST
dynamics (filter settling + one full stimulus cycle). BridgeSense uses
``dt = 50 ns``, ``n_steps = 120000`` (6 ms). A ``dt`` above a fast pole's tau
makes that stage's Euler integrator diverge — visible as an ``inf`` gain.

## 8. The AMS tier — emitted SystemC-AMS (`infersynth/emit_sysc_ams/`, WP-S2)

The end-state simulation tier of DESIGN.md §4, sitting *above* the v0 tier
documented in §§1–6: the compiler **emits** standalone SystemC-AMS C++ (TDF
modules + a testbench) that a real toolchain compiles and runs out-of-process.
This is **emit, don't bind** (RECON_HARVEST §1; `pysysc_eval.md` is the citable
prior art — PySysC/cppyy were evaluated and rejected upstream): the generated
C++ carries no cppyy/PySysC dependency, and InferSynth reads results back from a
CSV trace the emitted testbench writes. Emission is a **pure function of
(cell/design, params)** with no timestamps, so generated sources are byte-stable
and golden-comparable in CI (`tests/fixtures/ams_golden/`).

### AMS model artifact — `model/ams/<cell>.h`

A cell contributes an AMS model as `model/ams/<cell>.h`: a header-only
SystemC-AMS TDF module whose ports match `cell.yaml` `ports` exactly and whose
constructor takes the **gain-prefixed idiom params in sorted order** followed by
the rail-headroom `margin`. Example (opamp-gain-noninverting), mirroring the v0
`behavior.py` math (`OUT = clip(GND + gain·(IN−GND), VEE+margin, VCC−margin)`):

```cpp
SCA_TDF_MODULE(opamp_gain_noninverting) {
    sca_tdf::sca_in<double>  IN, VCC, VEE, GND;
    sca_tdf::sca_out<double> OUT;
    const double gain, margin;
    opamp_gain_noninverting(sc_core::sc_module_name nm, double gain_, double margin_ = 0.1)
        : IN("IN"), VCC("VCC"), VEE("VEE"), GND("GND"), OUT("OUT"),
          gain(gain_), margin(margin_) {}
    void processing() {
        const double gnd = GND.read();
        const double ideal = gnd + gain * (IN.read() - gnd);
        OUT.write(std::min(std::max(ideal, VEE.read()+margin), VCC.read()-margin));
    }
};
```

Header-only (no `.cpp`): the module is a pure function of its ports/params, so a
header both authors and bundles cleanly into the emitted build. `margin` is the
same tier constant as the v0 `DEFAULT_RAIL_MARGIN = 0.1 V` (§6), re-exposed as
`AMS_RAIL_MARGIN`. The two golden op-amp cells ship reference implementations;
`rg_ohms`/`channels` are structural and are *not* AMS constructor args.

### Precedence (this tier vs. the v0 tier)

Both tiers coexist; **neither replaces the other yet**:

* a cell with `model/ams/<cell>.h` is eligible for the `ams-simulation` gate;
* `model/behavior.py` remains the v0 `simulation` gate (§5);
* a cell may carry both (the two golden op-amps do) — the AMS gate cross-validates
  the v0 gate at one shared operating point, it does not supersede it;
* a cell with only `behavior.py` runs the v0 gate and loudly SKIPs `ams-simulation`
  ("no `model/ams/`"); a cell with neither loudly SKIPs both.

### The gate — `ams-simulation` (`infersynth/gates/ams_simulation.py`)

Wired into `run_cell_gates` after the v0 `simulation` gate. Three loud, distinct
outcomes (DESIGN.md §4 — never a silent pass):

1. cell ships no `model/ams/` → **SKIPPED** (falls back to the v0 tier);
2. SystemC-AMS toolchain absent → **SKIPPED** (distinct wording; the v0 gate
   still gives behavioral coverage);
3. toolchain present + AMS model → emit → compile → run → parse the CSV trace →
   reuse `infersynth/sim/checks.py` (`amplitude_ratio`, `clipped_within`) →
   PASS/FAIL.

The real compile/run path is implemented but its end-to-end test is
`@pytest.mark.sysc_ams`, skipped cleanly when the toolchain is absent — the same
convention WP4 used for `@pytest.mark.kicad`.

**A real toolchain is now installed on the dev box** (outside the repo, under
`/home/cycix/Desktop/fai-tuner/_toolchains/`, never committed):

* **SystemC 2.3.3** — Accellera reference implementation,
  `github.com/accellera-official/systemc` tag `2.3.3`, built via its CMake
  build (`-DCMAKE_CXX_STANDARD=17` to match the emitted Makefile) and installed
  to `_toolchains/systemc-2.3.3-install` (a `lib-linux64 -> lib` symlink was
  added so the emitted Makefile's `-L.../lib-linux64` resolves).
* **SystemC-AMS 2.3** (proof-of-concept) — the official release lives behind
  a registration form at coseda-tech.com/systemc-ams-proof-of-concept; the
  identical sources are mirrored unregistered at
  `github.com/sivertism/mirror-sysc-ams` (verified: matching `AUTHORS` /
  `NOTICE` / `RELEASENOTES` pointing at the same coseda-tech.com release,
  `src/scams/` present, Apache-2.0 `LICENSE`). Built with GNU autotools
  (`configure --with-systemc=... CXXFLAGS=-std=c++17`) against the SystemC
  2.3.3 install above, installed to `_toolchains/systemc-ams-2.3-install`.

Source `_toolchains/env.sh` to export `SYSTEMC_HOME` / `SYSTEMC_AMS_HOME` /
`LD_LIBRARY_PATH`, then `detect_toolchain()` reports available with **no
repo-side changes needed** — the existing probe already matches this install
layout. Run the real gate end-to-end with:

```
source /home/cycix/Desktop/fai-tuner/_toolchains/env.sh
pytest -m sysc_ams
infersynth gates --cell catalog/core/opamp-gain-noninverting
```

Both golden op-amp cells (`opamp-gain-noninverting`,
`opamp-gain-x4-noninverting`) compile, run, and PASS `ams-simulation` for real,
cross-validating the v0 `simulation` tier's gain/clipping numbers at the same
operating point — the emitter's golden-file tests turned out to already match
what a real SystemC-AMS kernel accepts; no emitter or Makefile fixes were
needed. On a machine without `_toolchains/env.sh` sourced, the gate still SKIPs
loudly as described above; nothing is ever faked.

### The emitter and the real-kernel seam

* **Cell tier** (`emit_cell`): a self-contained `main.cpp` that drives a
  deterministic 1 Vpk 1 kHz sine into each electrical input and a DC level onto
  each rail, instantiates the AMS module with bound params, and records
  `t` + inputs + outputs to CSV. The operating point (gain values, rails, timing)
  is read from the cell's v0 `testbench/tb.py` `PARAMS`/rails when present, so the
  AMS tier cross-validates the v0 `linear` scenario at the *same* point; it falls
  back to AMS-tier defaults (±12 V, 1000 × 1 µs) otherwise.
* **Design tier** (`emit_design`): from an `ElaboratedDesign`, a structural
  `SC_MODULE` top — one `sca_signal` per net, each instance's AMS module a member
  wired to its nets. Instances lacking an AMS model become a documented
  passthrough stub and are recorded **loudly** in the `EmissionReport` (the
  emitted top is flagged NOT a complete behavioral model).
* **Build**: a generated `Makefile` resolves SystemC + SystemC-AMS include/lib
  paths from `SYSTEMC_HOME` / `SYSTEMC_AMS_HOME` (the documented env seam).
* **`AmsKernel` seam** (`toolchain.py`): `detect_toolchain()` is a real probe
  (C++ compiler + SystemC/SystemC-AMS headers); `SystemCAmsKernel` compiles via
  the emitted Makefile, runs, and parses the CSV. Swapping in a commercial
  simulator is a new `AmsKernel` backend, not a rewrite — the same
  Python-kernel → real-kernel discipline the v0 `kernel.py` docstring documents.

### Conventions this tier chose (not dictated by DESIGN/SELECTION)

* **`model/ams/<cell>.h`, header-only**, module name = cell name with `-`→`_`.
* **Constructor args = gain-prefixed idiom params (sorted) + `margin`.**
* **Operating point borrowed from `testbench/tb.py`** for cross-tier validation
  at one shared point (WP-S2 acceptance §6); AMS-tier defaults when a cell has no
  v0 testbench.
* **CSV trace** (`t` + inputs + outputs) as the results file, parsed into the
  same trace shape the v0 checks consume — one check interface across both tiers.
* **Per-pair gain check**: the i-th sorted input is paired with the i-th sorted
  output and the i-th gain param (IN↔OUT; IN1↔OUT1…), so the same emitter serves
  the single- and quad-channel cells.
