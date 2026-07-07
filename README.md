# InferSynth

Inference and synthesis of PCBs from Functional Requirements Documents — the
FPGA toolchain workflow (source → simulate → infer → synthesize → place &
route) brought to PCB design.

```
idea → FRD (natural language) → formal spec (inference idioms)
     → simulate against a testbench → match the catalog → synthesize
     → verified multi-sheet KiCad schematic
```

What makes FPGA-style *inference* possible is the primitive library. PCB
design has no equivalent, so InferSynth builds one: a **catalog** of verified
circuit cells (behavioral model + KiCad fragment + testbench + selection
metadata), a controlled requirements vocabulary generated from it, and a
deterministic engine that compiles a signed-off spec into a gated KiCad
schematic. An LLM sits at the edges — linting requirements into canonical
idioms, drafting catalog entries, driving the flow — but it never picks a
part, a topology, or a connection. See [docs/DESIGN.md](docs/DESIGN.md) for
the full pitch and the FPGA analogy table.

## Status

**Pre-release**, developing on `v2-rewrite`, unpushed. What's true today,
verified against this repo:

- The pipeline runs end to end: `lint → match → decide → instantiate` produces
  a real multi-sheet `.kicad_sch` design plus a gate-checkable report.
- The catalog gates work: `erc`, `netlist-partition-equivalence`, and a
  Python-behavioral `simulation` gate all pass against real cells
  ([docs/SIM.md](docs/SIM.md)). The emitted-SystemC-AMS tier exists but skips
  loudly without a toolchain (this machine included) — nothing is ever
  reported verified when it wasn't run.
- The catalog is small — 21 cells, all leaf-level (L0: schematic fragment
  only, no floorplan/pre-routed tiers) — and none of the example FRDs in
  `examples/frds/` resolve cleanly against it yet (they target the eventual,
  larger catalog). The quickstart below uses an FRD sized to what exists now.
- Synthesis now wires power rails and unambiguous intra-chain signal nets
  (docs/NETFLOW.md stage 1); remaining inter-cell signal wiring is listed in
  `SYNTHESIS.md` as a worklist — drawn only where inference is unique or
  declared, never guessed.
- The v2 selection engine (packing, embeddings recall, cost-weighted decision,
  fixed-point resolution) is landing incrementally — see
  [docs/BUILD_PLAN.md](docs/BUILD_PLAN.md) for the commit-referenced ledger of
  what's shipped vs. still a work package.

Not stable. APIs, cell schema, and CLI flags may change without notice.

## Quickstart

```bash
git clone <repo> && cd infersynth
python3 -m venv .venv && .venv/bin/pip install -e .
```

Write a 5-line FRD that uses vocabulary the seed catalog already understands:

```markdown
# FRD-DEMO-1 -- Sensor Front End

- PWR-01 The system shall provide a power input connector.
- SIG-01 The system shall provide a non-inverting amplifier gain stage with gain 10.
- SIG-02 The system shall provide a unity buffer.
- OUT-01 The system shall provide an output header.
- DEC-01 The system shall provide a bypass capacitor.
```

```bash
.venv/bin/infersynth synthesize --frd demo.md --catalog catalog/ \
    --out build/ --profile prototype
```

```
root: build/demo.kicad_sch
  + pwr_01_conn_power_2pin  (core/conn-power-2pin@0.1.0)
  + sig_01_opamp_gain_noninverting  (core/opamp-gain-noninverting@0.1.0)
  + sig_02_unity_buffer  (core/unity-buffer@0.1.0)
  + out_01_conn_output_header  (core/conn-output-header@0.1.0)
  + dec_01_decoupling  (core/decoupling@0.1.0)
trace: build/selection_trace.json
report: build/SYNTHESIS.md
```

`build/SYNTHESIS.md` (excerpt — one row of the instantiation table, plus the
part that matters: synthesis never draws inter-cell wiring, on principle):

```
| SIG-01 | core/opamp-gain-noninverting@0.1.0 | sig_01_opamp_gain_noninverting | gain=10.0 (extracted); rg_ohms=1000.0 (default) |

## Wiring worklist (inter-cell nets are not drawn by synthesis)
- `sig_01_opamp_gain_noninverting`: GND, IN, OUT, VCC, VEE
  ...
```

Open `build/demo.kicad_sch` in KiCad. Requirements that don't match a catalog
cell lint as `frd.no-primitive` instead of failing silently — try
`infersynth lint` on any file in `examples/frds/` to see that diagnostic fire
against the current catalog.
See [examples/frds/README.md](examples/frds/README.md) for what each example FRD is for (01–06 are lint-diagnostic articles by design; 07 fully synthesizes).

## Surfaces

One core library; every surface below is a thin adapter over it
([docs/UX.md](docs/UX.md)):

| Surface | Command |
|---|---|
| Lint an FRD | `infersynth lint FRD.md --catalog catalog/` |
| Full pipeline | `infersynth synthesize --frd FRD.md --catalog catalog/ --out build/ --profile prototype` |
| Gate a cell or design | `infersynth gates --cell catalog/core/opamp-gain-noninverting` |
| Promote a hand-drawn sheet | `infersynth capture sheet.kicad_sch --name my-cell --library catalog/local` |
| Sidecar review panel | `infersynth panel --catalog catalog/` |
| Editor (LSP: squiggles, completions, hover) | `infersynth lsp --catalog catalog/` |
| Agent-facing MCP server | `infersynth mcp --catalog catalog/` |
| EDA-native Tcl shell | `infersynth tcl --catalog catalog/` |

## Principles

From [docs/DESIGN.md](docs/DESIGN.md) §3:

1. **Deterministic middle.** From signed-off formal spec to schematic, every
   step is code — reproducible, explainable ("selected X because: meets 3 A,
   AEC-Q100, lowest BOM cost of 4 candidates").
2. **LLM at the edges only.** Intake clerk, catalog factory, flow driver. It
   proposes; it never picks.
3. **Verification gates everything.** No artifact is trusted because of who
   or what authored it. It passes the gates or it does not exist.
4. **The catalog is the dataset is the product.** The language users write,
   the cells synthesis instantiates, and the benchmark corpus are all views
   of the same versioned dataset.

## Catalog snapshot

21 cells, one library (`catalog/core/`, tier `official`). Function tags
(`catalog/taxonomy.yaml`), by count of claiming cells: amplification (7),
filtering (5), regulation (4), protection (3), connectivity (3). All entries
are depth `L0` (schematic fragment only — no floorplan or pre-routed tiers
yet). Growing the catalog is most of the near-term roadmap; see
[docs/CATALOG_GROWTH.md](docs/CATALOG_GROWTH.md) and `infersynth capture` to
contribute a cell from a sheet you've already drawn.

## Docs map

Full annotated map and suggested reading order in
[docs/INDEX.md](docs/INDEX.md). Quick reference: DESIGN (vision, FPGA
analogy, catalog format), UX (the surfaces), SELECTION (requirement → cell
choice), NETFLOW (inter-cell net resolution), SIM (the two simulation
tiers), TCL (the Tcl shell), CATALOG_GROWTH (how cells get added), FABRIC
(a later idea: pre-routed platforms), IR (the structural DSL), LSP (editor
setup), RECON_HARVEST (what's adopted from sibling project `fai-recon`),
SEED_PLAN (target FRD + seed cell list), BUILD_PLAN (the delivered ledger —
the honest feature list, cited by commit).

## Contributing cells

The catalog grows from three places — hand-authored reference designs, mined
open-source-hardware corpora, and cells you capture from your own work — all
gated identically. Already drew a sheet in KiCad? `infersynth capture
sheet.kicad_sch --name my-cell --library catalog/local` scaffolds it into a
cell and runs the gates. See [docs/CATALOG_GROWTH.md](docs/CATALOG_GROWTH.md).

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
