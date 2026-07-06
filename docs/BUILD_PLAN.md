# Build plan — v2 (Infer) work packages

> Companion to DESIGN.md / UX.md / SEED_PLAN.md / SELECTION.md / RECON_HARVEST.md,
> which own all design judgment. This document decomposes the v2 engine into
> work packages (WP) precise enough for budget-model agents to execute a first
> pass. Rule for every WP: follow the referenced docs; where they are silent,
> choose conventionally, note the choice in the final report, and DO NOT invent
> architecture.
>
> Refreshed 2026-07-06 against repo tip `b5787a9` (branch `v2-rewrite`, worktree
> `InferSynth-plan` on branch `plan`). v1 (Stages 1/2/4 below) is DONE; this
> refresh compresses those to a delivered ledger and adds the v2 WPs decomposed
> from SELECTION.md + RECON_HARVEST.md.

## Conventions for all WPs

- Python ≥3.10, repo `/home/cycix/Desktop/fai-tuner/InferSynth`, branch
  `v2-rewrite`. Package name `infersynth`, version `2.0.0a0` (`pyproject.toml`).
- venv setup: `python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'`
  (add other extras — `panel`, `reqif`, `mcp`, `lsp` — per the WP's owner area).
- Before every commit: `.venv/bin/pytest` (all green) and `.venv/bin/ruff check .`.
- Commit trailer: `Co-Authored-By: Claude <noreply@anthropic.com>`. Never push.
  New runtime deps require a `pyproject.toml` optional-dependency extra unless
  already listed.
- Test baseline moves and grows with every WP. As of this refresh the suite
  collects 269 tests across 12 files (`tests/test_bind.py` 43,
  `tests/test_catalog.py` 75, `tests/test_cli.py` 8, `tests/test_emit.py` 9,
  `tests/test_gates.py` 14, `tests/test_gates_wp4.py` 21, `tests/test_ir.py` 21,
  `tests/test_lint.py` 21, `tests/test_lint_frds.py` 12,
  `tests/test_lsp_core.py` 11, `tests/test_mcp_server.py` 22,
  `tests/test_parse_md.py` 12); do not trust this number going forward —
  re-verify with `.venv/bin/pytest --collect-only -q` before starting a WP,
  since parallel WPs will have landed more tests by the time you read this.
- Dispatch pattern: one dedicated git worktree + branch per WP (this refresh
  itself was dispatched as worktree `InferSynth-plan` / branch `plan`; sibling
  worktrees `InferSynth-sim` / branch `sim` and `InferSynth-cells2` / branch
  `cells2` exist for the seed-catalog work below). WPs with no dependency
  edge between them run as parallel agents; merge and review before starting
  a WP that depends on them. WPs tagged `dispatchable: budget-model` are
  written to executable precision — a cheap model should not need design
  judgment to complete them. WPs tagged `dispatchable: main-session (judgment
  required)` need an MCP session and/or binding-math/topology judgment and
  should not be handed to an unsupervised budget-model agent.

---

## Delivered — Stage 1 (core engine, pure Python) — DONE

- **WP1 — cell.yaml schema v1 + binding evaluator.**
  Commit `1be0dfc` ("catalog+bind: cell.yaml schema v1 (ports/bindings/verification,
  strict mode) + binding evaluator (WP1)"). Lives in `infersynth/catalog/` +
  `infersynth/bind/expr.py`. Restricted-AST expression evaluator, strict-mode
  validator, golden-cell round-trip tests all present (`tests/test_bind.py`,
  `tests/test_catalog.py`).
- **WP2 — requirements model + FRD lint core + ReqIF import.**
  Commits `dca7383` (model + parse_md + LSP-shaped diagnostics), `e1fae55`
  (EARS grammar + generated vocabulary + lint engine), `496b9b7` (CLI
  subcommand, `reqif` extra, end-to-end FRD snapshots). Lives in
  `infersynth/lint/`. Six example FRDs lint cleanly per
  `tests/test_lint_frds.py`, `tests/test_parse_md.py`, `tests/test_lint.py`.

## Delivered — Stage 2 (emitter + oracle, KiCad-facing) — DONE

- **WP3 — direct emitter (writer).** Commits `48b67c8` ("emit: direct KiCad
  writer — instantiate cell fragments into a design (WP3)") + `b83885a`
  (golden test + format_value/surgery units). Lives in
  `infersynth/compile_kicad/emit.py`. Text-surgery instantiation, `${IS.*}`
  slot substitution, sheet splicing — verified by `tests/test_emit.py`.
- **WP4 — oracle gates.** Commits `96b7b19` (ERC + netlist oracle gates,
  triage policy, ERC harness) + `30b12d2` (`infersynth gates --cell/--design`
  CLI wiring). Lives in `infersynth/gates/`. Both committed cells pass gates
  end-to-end against `kicad-cli` (`tests/test_gates.py`,
  `tests/test_gates_wp4.py`).

## Delivered — Stage 4 (surfaces) — DONE

- **WP6 — LSP server.** Commit `a518a79` ("lsp: add infersynth.lsp — pygls
  server over the lint engine (WP6)"), extra `lsp` (pygls). Verified by
  `tests/test_lsp_core.py`.
- **WP7 — infersynth-mcp server.** Commit `f0d8125` ("mcp: add infersynth-mcp
  stdio server (WP7)"), extra `mcp`. Tools lint_frd/elaborate/synthesize/
  run_gates/catalog_search. Verified by `tests/test_mcp_server.py`.
- **WP8 — sidecar panel v0.** Commit `53dd54d` ("panel: WP8 sidecar panel v0
  (FastAPI catalog browser + gate report viewer)"), extra `panel`. Catalog
  browser + gate report viewer over the same core library.

## Delivered — taxonomy/library migration (SELECTION.md v1 adoption) — DONE

Between Stage 4 and this refresh, the catalog adopted the SELECTION.md v1
artifacts (docs `dab4102`, `0cf2c23`, then code `934b4e5` "catalog: adopt
SELECTION.md v1 artifacts — library layout, taxonomy, cost/reserved schemas"
and `b1bb007` "catalog: surface library identity in CLI/panel/mcp_server;
update tests for the migration"): cells now live under `catalog/<library>/<cell>/`
(currently one library, `catalog/core/`, per `catalog/core/library.yaml`),
`catalog/taxonomy.yaml` defines the 10 function tags, and `cell.yaml` gains a
`costs:` block (validator-checked, unused by scoring — see
`catalog/core/opamp-gain-noninverting/cell.yaml`). `capacity:`/`absorbs:`/
`embedding.json` are reserved (schema-accepted, not yet emitted by any cell).
Doc `b5787a9` ("docs: SELECTION §8 repeatability contract...") is the current
HEAD and this refresh's baseline.

---

## Stage 3 — seed catalog (needs Stage 2; partially in-flight)

`catalog/core/` currently holds 6 cells: `conn-output-header`,
`conn-power-2pin`, `conn-sensor-4wire`, `decoupling`, `opamp-gain-noninverting`,
`opamp-gain-x4-noninverting` (verified via `ls catalog/core/`). Every one of
these ships a `model/` and `testbench/` directory, but **all six currently
contain only a `.gitkeep`** — no behavioral model or testbench code has
landed for any cell yet (verified by inspection; this includes the two
op-amp cells). The old doc's WP5 (author `sallen-key-lowpass-2` and
`linear-reg-fixed` as the second/third golden cells) was never executed —
neither directory exists in `catalog/core/`.

**Dispatch note / discrepancy found:** three sibling worktrees exist at the
same repo tip — `InferSynth-cells2` (branch `cells2`), `InferSynth-sim`
(branch `sim`), and `InferSynth` itself (branch `v2-rewrite`). This refresh's
task brief described a parallel WP authoring `unity-buffer`,
`opamp-gain-inverting`, `vref-shunt`, `output-clamp` (presumably dispatched
in `cells2`) and a sim WP adding behavioral models + testbenches for the two
existing op-amp cells plus a model/testbench convention (presumably
dispatched in `sim`). **As verified**, both worktrees are clean and sit at
the identical commit as `plan` (`b5787a9`) — no commits, stashes, or
uncommitted changes exist on either branch beyond the shared baseline. There
is no evidence in the repo that either WP has actually started; they read as
*dispatched slots* (a worktree + branch reserved for the work), not
in-progress work. Treat the two items below as **planned/dispatched, not
in-flight**, until their branches show real commits.

1. **(planned, branch `cells2`) Seed-cell authoring WP** — `unity-buffer`,
   `opamp-gain-inverting`, `vref-shunt` (SEED_PLAN.md names it
   `vref-shunt (TL431-class)`), `output-clamp`. All four are Phase-1 (not
   golden-cell) entries in SEED_PLAN.md §2, frozen-format hand-authoring, no
   MCP-format judgment required beyond following `opamp-gain-noninverting`'s
   layout.
2. **(planned, branch `sim`) Sim v0 convention WP** — see WP-S1 below; this
   is the WP that would populate the two op-amp cells' empty `model/` and
   `testbench/` directories and write down the convention every other cell
   (including item 1's four cells) must follow afterward.

**Remaining judgment cells** (SEED_PLAN.md §2, not yet allocated to any
worktree) — names below are SEED_PLAN.md's exact identifiers, which differ
slightly from shorthand used in prior task framing (`instrumentation-amp` →
`instrumentation-amp-3opamp`; `rail-splitter` → `rail-splitter-virtual-gnd`;
`summing-offset` → `summing-offset-stage`; `adc-driver` → `adc-driver-rc`;
`bridge-interface` and `input-protection-rfi` and `power-input-conditioning`
and `current-source-bjt` match verbatim):

- `sallen-key-lowpass-2`, `mfb-lowpass-2` — deliberately claim the *same*
  idiom with a disambiguation rule (seeds the collision-detection gate).
  **`sallen-key-lowpass-2` is main-session work**: it is one of the three
  original Phase-0 golden cells (SEED_PLAN.md §3) that froze the authoring
  format together with `opamp-gain-noninverting`, and per the old BUILD_PLAN
  needs MCP-driven fragment authoring + binding-math judgment (Sallen-Key
  equal-R/equal-C Butterworth derivation) — not budget-model-dispatchable.
- `linear-reg-fixed` — the third Phase-0 golden cell. **Main-session work**
  for the same reason (dropout/load-regulation binding math + MCP authoring
  judgment).
- `instrumentation-amp-3opamp`, `bridge-interface`, `input-protection-rfi`,
  `summing-offset-stage`, `adc-driver-rc`, `current-source-bjt`,
  `rail-splitter-virtual-gnd`, `power-input-conditioning` — Phase-1 cells,
  hand-built to the frozen format once it exists; budget-model-dispatchable
  once the sim/model convention (WP-S1) and the two golden cells above have
  landed, since they follow the same recipe.

`dispatchable: main-session (judgment required)` — `sallen-key-lowpass-2`,
`linear-reg-fixed` (topology/binding-math judgment, MCP fragment authoring).
`dispatchable: budget-model` — the remaining Phase-1 cells listed above, and
the two `cells2`-scoped cells' authoring mechanics (once WP-S1's convention
exists to follow), all format-following work with no open design judgment.

---

## Stage 5 — v2 engine (needs Stage 3 sim convention where noted; decomposed
from SELECTION.md + RECON_HARVEST.md)

Principle driving the sequence below: **the sim gate is the search driver**
(RECON_HARVEST §2 — testbench divergence is what steers lazy candidate
expansion; SELECTION §8 pins the sim solver into the repeatability contract).
Sim work is therefore the dependency root for the matcher/packer/decision
chain, not an afterthought bolted on after selection logic exists.

### WP-S1 — sim v0: Python behavioral tier
`dispatchable: budget-model` (mechanical once the two-golden-cell convention
below is nailed down; escalate to main-session only if the op-amp SPICE-level
behavior needs judgment calls beyond ideal-op-amp equations).
Depends on: none (Stage 2 gates already exist to run against).
Owner area: `infersynth/sim/` (new), `catalog/core/*/model/`,
`catalog/core/*/testbench/`.

**Scope, cross-checked against repo state**: this WP is the one that turns
the currently-empty `model/`/`testbench/` `.gitkeep` stubs in
`catalog/core/opamp-gain-noninverting/` and
`catalog/core/opamp-gain-x4-noninverting/` into real artifacts, and is the
**first** WP to define the model/testbench convention at all — nothing in
the repo currently specifies model file format, testbench harness shape, or
how a model is invoked. Do not assume any prior convention exists; author it
here.

1. `infersynth/sim/model.py`: adopt the RECON_HARVEST §1 DeviceModel shape
   (`fai_recon/emu/devices.py`) — a plain-Python behavioral model class:
   declarative parameters (bound from the cell's `bindings:` output),
   named behavior callables (e.g. `dc_transfer`, `ac_response`,
   `step_response`), tolerant of a `ctx=None` (no event-kernel dependency for
   v0 — pure function evaluation, not a scheduled simulation). Store as
   `catalog/core/<cell>/model/model.py` exposing `build_model(params: dict) ->
   Model`.
2. `infersynth/sim/testbench.py`: a testbench = a Python file
   `catalog/core/<cell>/testbench/testbench.py` exposing `run(model, **kwargs)
   -> TestbenchResult` (`{passed: bool, measurements: dict, tolerance_report:
   dict}`). For the two op-amp cells: DC transfer (measure gain vs. expected
   `1 + Rf/Rg`, tolerance from the cell's `gain` param range), −3 dB bandwidth
   estimate (ideal single-pole GBW assumption — document the assumed GBW
   constant in a comment since no real op-amp part is bound yet), rail
   clipping (output saturates at VCC/VEE minus headroom).
3. `infersynth/sim/runner.py`: `run_cell_testbench(cell_dir: Path) ->
   TestbenchResult` — loads `model.py` + `testbench.py` by path, binds
   default params the same way WP4's harness generator does (reuse that
   logic, don't fork it), runs, returns result. CLI: `infersynth sim --cell
   DIR` printing pass/fail + measurements, exit 1 on failure.
4. Write the convention down in `docs/SIM_MODEL_CONVENTION.md` (or a new
   `docs/` file — pick one, note the choice): file names, `Model`/
   `TestbenchResult` shapes, the ideal-component-level assumptions for v0
   (no parasitics, no noise, no temperature dependence — that's what WP-S2's
   AMS tier is for), and the migration note WP-S2 depends on (see below).
5. Populate `model/model.py` + `testbench/testbench.py` for
   `opamp-gain-noninverting` and `opamp-gain-x4-noninverting`.
6. Tests: `tests/test_sim.py` — model construction from bound params, each
   testbench passes against its own cell at 2-3 param points (e.g. gain=10,
   gain=100), a deliberately-wrong-binding fixture fails the testbench
   (divergence must be observable, per RECON_HARVEST §2 "wrong binding =
   observable failure, never silent").
7. Acceptance: `infersynth sim --cell catalog/core/opamp-gain-noninverting`
   exits 0; running it against a mutated fragment with a wrong resistor value
   exits 1 with a measurement showing the gain mismatch.

### WP-S2 — SystemC-AMS emitter + real AMS kernel seam
`dispatchable: main-session (judgment required)` — this is flagged in
DESIGN.md §10 and RECON_HARVEST's "Corrections to expectations" as
InferSynth's genuinely novel work; nothing in fai-recon or elsewhere in the
corpus addresses SystemC-AMS continuous-time (ELN/TDF) semantics, so there is
no prior art to mechanically follow.
Depends on: WP-S1 (needs the v0 model/testbench convention to migrate from).

1. Structural emission: given an elaborated `Design` (the existing
   `infersynth/ir/` structural DSL — already used by WP3's emitter), emit a
   SystemC-AMS ELN/TDF netlist description — components as ELN elements
   (resistor/capacitor/opamp macromodel primitives) wired per the same net
   partition the netlist gate already computes (reuse WP4's netlist-partition
   code as the source of truth for connectivity, don't re-derive it).
2. Follow RECON_HARVEST §1's emit-don't-bind decision explicitly: do NOT
   attempt a Python↔C++ SystemC-AMS binding layer (PySysC/cppyy were
   evaluated and rejected upstream in `pysysc_eval.md`,
   `/home/cycix/fai-recon/pysysc_eval.md` — do not re-run that evaluation,
   just cite it). Emit a standalone `.cpp`/`.h` SystemC-AMS testbench source
   file per cell instead, compiled and run out-of-process; InferSynth reads
   back a results file (e.g. CSV/JSON) the emitted testbench writes.
3. Migration path from WP-S1: the v0 Python testbench's `measurements`/
   `tolerance_report` shape becomes the target shape the AMS-emitted
   testbench's results file is parsed into, so WP-M1/WP-D1 (below) can score
   AMS results and Python-model results through one interface
   (`TestbenchResult`). Cells without an AMS model yet fall back to the v0
   Python tier (graceful degradation, same pattern as the structural-only
   cells' "sim stub" per SEED_PLAN.md notes).
4. Real AMS kernel seam: an actual SystemC-AMS kernel is a heavy, licensed-
   toolchain dependency (Accellera reference impl / commercial simulators).
   Define the seam (an abstract `AmsKernel` interface: compile, run, parse
   results) and ship one working backend against whichever SystemC-AMS
   toolchain is actually available in this environment — if none is
   installed, this WP must say so explicitly in its final report rather than
   fake a passing gate; mark the kicad-cli-style `@pytest.mark.ams` skip
   pattern (skip cleanly when the toolchain is absent, same convention WP4
   used for `@pytest.mark.kicad`).
5. Tests: emission golden test (structural netlist → expected `.cpp` for one
   op-amp cell, text-diffed); `@pytest.mark.ams` end-to-end test that
   compiles + runs + parses results IF the toolchain is present.
6. Acceptance: one op-amp cell's AMS testbench, if a toolchain is available,
   produces a `TestbenchResult` whose `measurements` match the v0 Python
   tier's within a documented tolerance (cross-validation of the two tiers,
   not just internal self-consistency).

### WP-M1 — matcher v0: idiom recall + allocation enforcement + bidirectional propagation
`dispatchable: budget-model`.
Depends on: WP-S1 (candidate scoring needs a testbench to score against, per
RECON_HARVEST §2/§3 — matching without a sim signal degrades to the existing
v1 keyword resolver, which is not what this WP is).
Owner area: new `infersynth/match/`.

1. Idiom recall (Layer 1, SELECTION §4): reuse `infersynth/lint/vocab.py`'s
   catalog-generated vocabulary to surface candidate cells per requirement;
   attach `surfaced_by: idiom` provenance to every candidate (SELECTION §7).
2. Allocation enforcement (SELECTION §2): parse `allocations:` from the
   formal spec (new top-level key alongside whatever the spec's existing
   schema is — check `infersynth/ir/` or spec-loading code for where the
   formal spec is parsed and extend it there, don't create a parallel
   loader); apply `allow`/`deny` per requirement subtree with inheritance +
   `deny`-beats-`allow`; emit `frd.unallocated` (WARN under
   `allocation: strict` profile) and `frd.allocation-empty` diagnostics
   through the existing `infersynth/lint/diagnostics.py` `Diagnostic` shape
   (reuse it, this is the seam UX.md/WP2 already built for this purpose).
3. Bidirectional endpoint propagation (RECON_HARVEST §3): given a
   requirement's declared inputs and required outputs, propagate forward
   from inputs and backward from outputs through candidate cell chains
   (candidates from step 1, filtered by step 2) until the chain closes on
   both ends or a fixed candidate-expansion budget (count-based, per
   SELECTION §8 — never wall-clock) is exhausted; enumerate the resulting
   constrained candidate chains.
4. Score each closed chain by running it through WP-S1's testbench runner
   (or WP-S2's, when a cell has an AMS model) against the spec's stated
   tolerances; **ensemble variance across scored candidates is the
   underspecification signal** (RECON_HARVEST §3) — when variance exceeds a
   configured threshold, emit an allocation-prompt-shaped
   `ResolutionRequest` (typed per SELECTION §8/RECON_HARVEST §6) rather than
   silently picking a winner (winner-picking is WP-D1's job, not the
   matcher's).
5. Must be deterministic per SELECTION §8: fixed expansion order, fixed
   tie-breaks, count-based budgets, no seeded/implicit randomness — two runs
   of the same `(spec, catalog@version)` produce byte-identical candidate
   chain enumerations.
6. Tests: `tests/test_match.py` — idiom recall against the 2-cell (then
   6-cell) catalog; allocation allow/deny inheritance with a synthetic spec
   fixture; propagation closes a 2-hop chain (e.g. bridge input → gain stage
   → ADC-driver output) on a fixture catalog; determinism test (same input
   twice, assert identical output including order); variance-threshold
   ResolutionRequest emission on a deliberately underspecified fixture.
7. Acceptance: BridgeSense-1's gain-stage requirement (SEED_PLAN.md §1)
   resolves to a single closed chain candidate against the eventual 20-cell
   catalog subset available at WP-M1 completion time; unresolved gaps emit
   `frd.no-primitive`/`frd.absorbable-no-template`/`frd.ambiguous` through
   the existing diagnostic codes (WP2), not new ad hoc codes.

### WP-M2 — embeddings recall layer
`dispatchable: budget-model`.
Depends on: WP-M1 (extends the same candidate-surfacing interface;
`surfaced_by` provenance discipline must already exist).

1. Pin an embedding model id in `catalog/taxonomy.yaml` or a new
   `catalog/embedding.yaml` manifest (pick one, note the choice) —
   SELECTION §4: "the catalog manifest pins the embedding model id."
2. Each cell (and later, absorbable template) gets a capability text (short
   free-text description — derive from existing `manifest.description` +
   `idioms.keywords` + `idioms.functions` for v0, no new authored field
   required) and a cached `embedding.json` beside it:
   `{model_id, dim, vector}`.
3. `infersynth/match/embed.py`: `embed_text(text) -> vector` (pluggable
   backend — if no local embedding model is available in this environment,
   implement a deterministic placeholder backend — e.g. a hashed bag-of-words
   vector — clearly labeled as a placeholder, and wire the real interface so
   swapping backends is a config change, not a rewrite; note this choice
   explicitly, don't silently ship a fake embedding as if it were real).
4. `recall(requirement_text, catalog) -> candidates` — cosine similarity
   above a catalog-configured floor surfaces a candidate with
   `surfaced_by: semantic(<score>)`; wire behind the `recall: strict |
   semantic` profile knob (SELECTION §4) — `strict` skips this layer
   entirely (idioms-only, unchanged from WP-M1).
5. Gap detection: high similarity to a programmable cell with no matching
   `absorbs:` template emits `frd.absorbable-no-template` (new diagnostic
   code, same `Diagnostic` shape as WP2/WP-M1).
6. A model-id change (re-embed) must be diffable/versioned: regenerating all
   `embedding.json` files is a single CLI command
   (`infersynth catalog reembed`), not a hand process.
7. Tests: `tests/test_embed.py` — placeholder backend determinism (same text
   twice → identical vector); recall floor threshold behavior; profile knob
   `strict` suppresses semantic candidates; gap diagnostic fires on a
   fixture with a programmable cell lacking a template.
8. Acceptance: `strict` mode candidate set for any existing fixture spec is
   byte-identical to WP-M1's output alone (semantic layer is strictly
   additive, never removes or reorders idiom candidates — SELECTION §4/§8).

### WP-P1 — packer: homogeneous packing (covers-vs-covers) + guard-and-claim
`dispatchable: budget-model`.
Depends on: WP-M1 (needs closed candidate chains to pack), WP-S1 (packing
decisions get validated against sim results, not just structural fit).

1. Homogeneous-first packing: start with the simplest real case in the seed
   catalog — 4x single op-amp gain stage → `opamp-gain-x4-noninverting` quad
   rewrite (this exact pair already exists in `catalog/core/`, authored
   specifically as "the LUT-4/LUT-6 packing pair" per its commit message
   `ea16cbc` — use it as the packer's first fixture, don't invent a new one).
   `infersynth/pack/homogeneous.py`: given N candidate instances of the same
   cell, check whether a quad/dual variant of that cell exists in the same
   library and covers all N without exceeding its port/capacity budget;
   if so, propose the rewrite as a candidate mapping.
2. Covers-vs-covers costing (SELECTION §6 rule 1): a packed (composite)
   mapping is compared against the best *composition* of the unpacked cells
   covering the same requirements — never against a single unpacked cell in
   isolation. Build both cost vectors (packed vs. best-composition) per
   SELECTION §6's `costs:` schema and hand both to WP-D1 as competing
   candidates; the packer does not itself decide the winner.
3. Guard-and-claim mechanics (RECON_HARVEST §4, adapted from recon's
   net_merge): each proposed pack is a **weighted, falsifiable claim** keyed
   by the set of instances it absorbs — deterministic, idempotent,
   order-independent when applied in one fixed-point pass; a **terminal
   guard** (port/capacity check, same role as recon's hub guard) blocks a
   pack from claiming instances that don't fit; a later contrary claim
   (e.g. a bigger pack covering an overlapping set) can beat an earlier one
   by cost, never by application order.
4. Absorption profile knobs (SELECTION §5): `absorption: off | conservative
   | aggressive` — conservative packs only within one requirement group;
   aggressive packs across the design but never across an allocation
   boundary (read the `allocations:` scoping WP-M1 already parses).
5. Tests: `tests/test_pack.py` — 4x `opamp-gain-noninverting` → 1x
   `opamp-gain-x4-noninverting` proposal with correct covers-vs-covers cost
   vectors on both sides; order-independence (shuffle input instance order,
   assert identical claim result); allocation-boundary block; capacity-guard
   rejection when N exceeds the quad variant's macrocell count.
6. Acceptance: given a synthetic 4-instance-of-the-same-gain-stage spec
   fixture, the packer proposes both the quad-pack and the 4x-discrete
   composition as competing candidates with fully interior-audited cost
   vectors (SELECTION §6 rule 2 — every BOM line/area/dev-hour inside the
   pack counted, not just the pack's own header cost).

### WP-D1 — decision engine: cost vectors x weight profiles
`dispatchable: budget-model`.
Depends on: WP-M1, WP-P1 (needs both unpacked and packed candidates to score).

1. `infersynth/decide/weights.py`: load a weight profile — named
   (`prototype`, `production`, `hobbyist` per SELECTION §6) or inline
   `{bom: 5, area: 2, dev_hours: 0.5, …}` from the spec.
2. `infersynth/decide/score.py`: `score(cost_vector, profile) -> float` —
   weighted sum over the `costs:` schema fields (SELECTION §6); apply
   covers-vs-covers grouping so a composite candidate only ever competes
   against equivalent-coverage compositions (consumes WP-P1's grouped
   candidate sets directly, don't regroup).
3. `selection_trace.json` (SELECTION §7): for every requirement/subtree,
   record every candidate considered with its `surfaced_by` provenance, the
   ones rejected and by which hard check (capacity/ports/allocation/
   constraint — cite the specific check that rejected it), the finalists'
   cost vectors and weighted scores, and the winner's justification in
   deterministic terms only (never a bare similarity score as the stated
   reason — semantic `surfaced_by` scores may appear in the provenance
   field but never in the justification field).
4. Lockfile format for external cost data (SELECTION §8): `costs.lock.json`
   (or similar — note the choice) snapshotting any BOM pricing/stock/sourcing
   data the decision layer reads; synthesis reads only the lockfile, never a
   live API; a `infersynth catalog refresh-lockfile` command is the only way
   to update it (explicit user action, not automatic).
5. Determinism per SELECTION §8: given `(spec, catalog@version, taxonomy,
   weight profile, lockfiles)`, output is identical on every re-run — no
   wall-clock-based tie-breaks; ties broken by a fixed rule (e.g. cell name
   lexical order) and documented.
6. Tests: `tests/test_decide.py` — named profile flips the winner on the
   same candidate set (prototype vs. production, per SELECTION §6's PIC vs.
   555 example shape, adapted to whatever cells exist); `selection_trace.json`
   schema round-trip; lockfile-absent behavior (must fail closed with a
   clear error, never silently hit a live API); determinism test (two runs,
   identical trace).
7. Acceptance: running the decision engine twice on the same frozen spec +
   catalog + lockfile produces byte-identical `selection_trace.json`.

### WP-F1 — fixed-point pipeline + typed ResolutionRequest seam
`dispatchable: budget-model`.
Depends on: WP-M1, WP-D1 (needs a first-pass matched+scored result to
re-enter as a constraint).
Owner area: new `infersynth/pipeline/`.

1. RECON_HARVEST §5 fixed-point control structure: stages run blind first
   (current DAG: lint → match → pack → decide → emit → gate), then a second,
   assisted pass runs with `known=<facts from the first pass + gate
   results>`; later-stage facts (gate failures: ERC, netlist-partition
   mismatch, sim-testbench divergence) feed back as earlier-stage
   constraints (e.g. "this binding failed ERC" narrows the matcher's
   candidate set on the next iteration); iterate until no stage produces new
   facts (fixed point) or a count-based iteration budget is hit.
2. RECON_HARVEST §6 typed ResolutionRequest / weighted-claim arbiter: every
   fact in the pipeline is `{value, source, confidence}`; conflicting facts
   (e.g. two gates disagreeing about the same net) resolve by weighted vote,
   keeping dissent in the trace rather than discarding it. A judgment gap
   that no deterministic rung resolves becomes a typed `ResolutionRequest`
   (reuses/extends the `frd.no-primitive` / `frd.ambiguous` /
   `frd.absorbable-no-template` / allocation-prompt diagnostic codes already
   defined — do not invent a parallel notification channel) that halts the
   in-run pipeline and is answered BETWEEN runs (SELECTION §8 — never inside
   a synthesis run); the answer lands as a durable input artifact (spec
   edit, allocation, new cell/template, waiver) the next run consumes.
3. `infersynth/pipeline/fixedpoint.py`: `run(spec, catalog, known=None) ->
   PipelineResult`; `PipelineResult.resolution_requests: list[ResolutionRequest]`
   is empty iff the run reached a clean fixed point.
4. Tests: `tests/test_pipeline.py` — a synthetic gate-failure fixture that
   only resolves on the second (assisted) pass; a genuinely irresolvable
   fixture that emits a `ResolutionRequest` and halts (does not loop
   forever — bounded iteration count enforced and tested); weighted-vote
   conflict resolution with kept dissent, round-tripped through the trace.
5. Acceptance: a fixture where WP-S1/WP-S2 testbench divergence on the first
   pass causes the matcher to pick a different candidate on the second pass,
   converging to a fixed point within the documented iteration budget.

### WP-L1 — spec-side allocation schema + LSP allocation code action + frd.unallocated
`dispatchable: budget-model`.
Depends on: WP-M1 (allocation parsing/enforcement must exist to have
something for the code action to write into and the diagnostic to check
against).
Owner area: `infersynth/lint/` (extend), `infersynth/lsp/` (extend WP6).

1. Formal-spec schema addition: `allocations:` (SELECTION §2 exact shape —
   `at`, `allow`, `deny`) — extend whatever the spec loader already uses
   (same loader WP-M1 step 2 extended; don't add a second parser).
2. `frd.unallocated` diagnostic (WARN under `allocation: strict` profile,
   absent otherwise — SELECTION §2) wired through the existing lint engine
   (WP2) so it shows up in both the CLI (`infersynth lint`) and the LSP
   (WP6) without new plumbing.
3. LSP code action (extends WP6's `infersynth.lsp`): on a requirement
   flagged `frd.unallocated`, offer a quick-fix that writes a starter
   `allocations:` entry (`at: <requirement id>, allow: [], deny: []`) into
   the formal spec file — this is UX.md's "disambiguation code actions"
   pattern, reused for allocation rather than idiom disambiguation.
4. Tests: `tests/test_lsp_allocation.py` (or extend `tests/test_lsp_core.py`)
   — code action produces valid spec YAML; `frd.unallocated` fires/doesn't
   fire correctly under `strict`/default profile; round-trip through
   WP-M1's allocation enforcement (write the code action's output, confirm
   the matcher now scopes correctly).
5. Acceptance: a requirement with no allocation shows `frd.unallocated` under
   `allocation: strict`; invoking the code action and re-running lint clears
   the diagnostic.

## Dependency graph

```
WP-S1 (sim v0, Python behavioral tier)
  -> WP-S2 (SystemC-AMS emitter + kernel seam)          [main-session]
  -> WP-M1 (matcher v0: idiom recall + allocation + propagation)
       -> WP-M2 (embeddings recall layer)
       -> WP-P1 (packer: homogeneous + guard-and-claim)
       -> WP-L1 (spec allocation schema + LSP code action)
            (WP-L1 also needs WP-M1 directly)
       WP-M1, WP-P1 -> WP-D1 (decision engine: cost vectors x weight profiles)
       WP-M1, WP-D1 -> WP-F1 (fixed-point pipeline + ResolutionRequest seam)
```

Stage 3 (seed catalog) relationship to Stage 5: WP-S1 is the convention that
the `cells2`-branch authoring work (unity-buffer, opamp-gain-inverting,
vref-shunt, output-clamp) and the remaining Phase-1 judgment cells will
eventually populate `model/`/`testbench/` against, but Stage 3 cell-authoring
is not gated on Stage 5 landing first — it can proceed in parallel using
whatever convention WP-S1 documents, same as `cells2`/`sim` were already
dispatched as parallel worktrees at repo tip.

## Dispatch notes

Stage 5 WPs with no edge between them (e.g. WP-M2 and WP-P1, both depending
only on WP-M1) run as parallel agents in separate worktrees, merged after
review, same pattern as this refresh itself (`InferSynth-plan`/`plan`
alongside `InferSynth-sim`/`sim` and `InferSynth-cells2`/`cells2`). WP-S2 and
WP-F1 involve either genuinely novel design (WP-S2) or cross-stage judgment
about conflict resolution (WP-F1's weighted-vote policy) — treat both as
needing a main-session review pass even where a budget-model agent produces
the first draft. Stage 3's two main-session cells (`sallen-key-lowpass-2`,
`linear-reg-fixed`) need an MCP session and should be scheduled as
tightly-scripted main-loop work, not unsupervised budget-model dispatch.
