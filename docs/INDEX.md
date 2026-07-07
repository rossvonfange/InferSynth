# Docs index

An annotated map of `docs/`, for a new contributor deciding what to read and
in what order. Every file here was converged/captured 2026-07-06 unless noted;
treat them as living design records, not frozen specs — `BUILD_PLAN.md` is the
one document that tracks what has actually landed in code.

## Suggested reading order

**DESIGN → UX → SELECTION → NETFLOW → SIM → BUILD_PLAN.**

Why this order: DESIGN gives you the vision and the vocabulary (catalog,
idioms, gates, the FPGA analogy) that every other doc assumes without
re-explaining. UX tells you where a human or agent actually touches the
system, so the pipeline stages in the other docs land somewhere concrete.
SELECTION and NETFLOW are the two halves of "what does synthesis actually
decide" — cell choice, then wiring — and read naturally back to back since
NETFLOW's wiring stage runs immediately after SELECTION's decision stage.
SIM comes next because it underpins both: SELECTION's decision engine scores
candidates by running them through the sim gate (RECON_HARVEST §2 — "the sim
gate is the search driver"), so understanding the two simulation tiers
before the matcher/packer/decision chain avoids re-deriving that dependency
later. Finish with BUILD_PLAN to see which parts of the preceding five
documents are implemented today, in progress, or still a work package — it
is intentionally the reality check that comes last, not first, so you form
your own picture of the design before checking it against the ledger.

The remaining docs (FABRIC, IR, LSP, TCL, RECON_HARVEST, SEED_PLAN,
CATALOG_GROWTH) are reference material — read them when the task in front of
you touches that area, not up front.

Outside `docs/`: [../examples/frds/README.md](../examples/frds/README.md) explains the example-FRD corpus (01–06 are lint-diagnostic test articles by design; 07 BridgeSense is the one that fully synthesizes).

## Per-doc notes

### [DESIGN.md](DESIGN.md) — vision / spec
The foundational document. States the FPGA-flow analogy (§1-2), the four
governing principles (§3, quoted in the README), the IR (§4), the catalog
format and its L0/L1/L2 depth tiers (§5), the FRD controlled-vocabulary
language (§6), the per-design verification gates (§7), the relationship to
KiCAD-MCP-Server (§8), the four-deliverable form factor (§9), the roadmap
(§10), and explicit non-goals (§11) — read this first, everything else is a
refinement of some section here.

### [UX.md](UX.md) — spec
Owns the user-facing architecture: the four core surfaces (editor/LSP,
conversation/MCP, sidecar panel, KiCad-as-viewer) plus the Tcl shell
documented separately in TCL.md. Also states the write-path discipline
(direct emission, MCP/kicad-cli as oracle never emitter) and the anti-goals
(no owned GUI, no KiCad-plugin dependency, no authoring UI inside KiCad).
Read this to understand where a given piece of new work should live — it is
the fastest way to avoid building a fifth surface that duplicates logic.

### [SELECTION.md](SELECTION.md) — spec
Governs the v2 inference/packing engine: library namespaces and tiers,
requirement allocation (`allow`/`deny` scoping), the two-layer recall model
(idioms strict, embeddings semantic-additive-only), absorption legality
(capacity + verified templates, never bare capability claims), cost vectors
× named weight profiles, the honest-accounting rules for composite cells,
the explanation trace, and the repeatability contract (§8) that pins
synthesis as a pure function of `(spec, catalog@version, taxonomy, weights,
lockfiles)`. Its schemas are adopted into v1 cell.yaml now even though the
scoring engine itself is v2 work.

### [NETFLOW.md](NETFLOW.md) — spec
The wiring stage that follows decide/pack: how inter-cell nets get resolved
(rail resolver, intra-chain propagation, whole-design convergence) and drawn,
strictly as a DAG (cells encapsulate their own feedback; genuine inter-cell
feedback must be declared via `feeds`, never inferred). Covers the FRD
pragma sugar (`[feeds:]`, `[use:]`, `[no-pack]`) and the `interfaces.yaml`
bundle-typing model that turns whole classes of wiring ambiguity into unique
inferences. Read alongside SELECTION — one is "which cell," the other is
"how do the chosen cells connect."

### [SIM.md](SIM.md) — spec / reference (marked "shipped" for its v0 tier)
The behavioral simulation tier that makes DESIGN §7's `simulation` gate real:
a small deterministic timed-dataflow kernel (`infersynth/sim/`), the
`model/behavior.py` + `testbench/tb.py` cell convention, and the gate that
runs both. §7 documents the second, end-state tier — emitted SystemC-AMS,
compiled and run out-of-process, with a documented `AmsKernel` seam — which
exists in code but loudly skips without a toolchain (true on this
machine). Both tiers are cross-validated at one operating point where a cell
ships both.

### [TCL.md](TCL.md) — reference
Documents `infersynth tcl`, the fifth thin adapter surface (alongside UX.md's
four) aimed at engineers who already live in an EDA Tcl console. Pure
stdlib `tkinter.Tcl()`, no Tk window; every command dispatches to the same
handler set the MCP server wraps. Read when you need to script the pipeline
from a Tcl-native tool or CI step.

### [CATALOG_GROWTH.md](CATALOG_GROWTH.md) — plan / reference
Names the three pipelines that feed catalog growth — hand-authored reference
designs (A), mined open-source-hardware corpora (B, not yet built), and user
capture of hand-drawn KiCad sheets (C, `infersynth capture`) — and states
that all three funnel into identical cell CI. Short; read when deciding how
a new cell should enter the catalog.

### [FABRIC.md](FABRIC.md) — plan (design-captured, not scheduled)
A later idea: a fully placed-and-routed "fabric" board that synthesis
populates by selecting which pre-laid-out sites to stuff, rather than
laying out anything itself — the gate-array/structured-ASIC model applied to
PCBs. Depends on the packer and decision engine landing first; not scheduled
before the v2 engine exists. Read for context on where layout automation is
headed, not for anything buildable today.

### [IR.md](IR.md) — reference
Describes what `infersynth/ir/` actually implements today: `Cell`, `Design`,
`elaborate()`, and the `ElaboratedDesign` output (instances, nets, domains,
the `partition()` map the netlist-equivalence gate consumes). Where it
extends DESIGN.md §4, the choice is conventional, not authoritative — this
is the doc to check when writing code against the IR directly.

### [LSP.md](LSP.md) — reference
Documents `infersynth lsp` (WP6): what's implemented (diagnostics,
completions, hover, code actions) and what isn't (debouncing, go-to-def,
editor e2e tests), plus copy-pasteable VS Code and Neovim client
configuration. Read when wiring up an editor, not for pipeline internals.

### [RECON_HARVEST.md](RECON_HARVEST.md) — harvest / reference
An honesty ledger of what's adopted, adapted, or rejected from the sibling
`fai-recon` project (a reverse-engineering "discovery" engine — the inverse
flow of InferSynth's synthesis). Notes plainly what fai-recon does NOT cover
(anything SystemC-AMS continuous-time is InferSynth's own novel work) so
readers don't assume more prior art exists than actually does. Read once,
for context on why certain architectural choices (sim-as-search-driver,
bidirectional propagation, guard-and-claim absorption, fixed-point pipeline)
look the way they do.

### [SEED_PLAN.md](SEED_PLAN.md) — plan
Defines the v1 vertical-slice target: the `BridgeSense-1` bridge-sensor FRD,
the ~20-cell seed catalog it needs (grouped by role, each with an AoE
provenance reference and a testbench sketch), the golden-cells-first
phasing, and the machine-checkable v1 acceptance criteria. Read before
authoring a new seed cell — it is the spec for what the seed catalog is
supposed to become, distinct from what's landed so far (see BUILD_PLAN).

### [BUILD_PLAN.md](BUILD_PLAN.md) — plan (the delivered ledger)
The authoritative, commit-referenced record of what has actually shipped
(Stages 1/2/4, taxonomy/library migration, and an ever-growing "delivered
since the refresh" section) versus what remains an open work package (WP-S2,
WP-M1/M2, WP-P1, WP-D1, WP-F1, WP-L1, and the two main-session judgment
cells still unauthored). Explicitly the corrective to any other doc's
aspirational tone — when in doubt about whether something exists in code,
this is the doc that says so, and says so by citing the commit.
