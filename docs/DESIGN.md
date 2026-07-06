# InferSynth v2 — Design

> Inference and synthesis of PCBs from Functional Requirements Documents.
>
> This document is the converged design from the 2026-07 planning discussions. It
> supersedes the 2016-era PyQt prototype (`cynth`), whose core ideas — a circuit
> catalog, a project spec, an AMS description at the heart of the design — survive
> here in modern form.

## 1. Vision

InferSynth brings the FPGA toolchain workflow to PCB design:

```
idea → source (RTL) → simulate → infer → synthesize → place & route
```

PCB design is nearly the same sequence of actions — what's missing is the
**catalog** (the primitive library) that makes *inference* possible. Given a
catalog, the *synthesis* stage is functionally similar to FPGA synthesis as well.
InferSynth builds that catalog, the controlled requirements language derived from
it, and the deterministic engine that compiles a formal spec into a verified KiCad
schematic.

An LLM assists at the edges — linting requirements into well-formed idioms,
generating and checking catalog entries, driving the flow — but **never makes
design decisions**. Inference and synthesis are known, not guessed.

## 2. The FPGA analogy (load-bearing, not decorative)

| FPGA flow | InferSynth flow |
|---|---|
| idea | FRD (natural language) |
| RTL / HLS source | formal spec written in **inference idioms** |
| behavioral simulation | SystemC-AMS simulation against the spec's testbench |
| **inference** — tool recognizes RTL idioms → primitives (BRAM, DSP) | **catalog matching** — spec idioms → catalog blocks |
| synthesis / technology mapping (LUT packing, cell binding) | **part binding** — real MPNs, component values, footprints |
| place & route | schematic sheet generation; later PCB placement & routing |
| primitive library + vendor IP catalog | **the catalog** (see §5) |
| XDC / constraints file | netclasses, power rails, stackup, mechanical envelope |
| timing closure | ERC/DRC/SI/power-budget closure |

Structural correspondences that fall out of the analogy:

- **A KiCad hierarchical sheet is an instantiated primitive.** Each catalog block
  synthesizes to one sheet (or a fragment of one). Connector/interface sheets play
  the role of IOBs.
- **Power is modeled with primitives, not by analogy.** Where digital flows bolt
  power intent on as a sidecar (UPF/IEEE 1801), SystemC-AMS ELN models are
  electrical networks — rails, regulators, and loads are ordinary nodes and
  elements. So power blocks are first-class *synthesizable* catalog primitives
  that simulate natively, and rail-integrity rules (exactly one driver per power
  net; the PWR_FLAG discipline: one flag per rail on its originating sheet,
  regulator `power_out` pins need none) are checked as primitive legality, the
  way clock-buffer rules are in an FPGA.
- **Clock domains stay clocks.** The IR carries clock/timing domains as a
  first-class annotation on nets and blocks (oscillators, RF chains, differential
  pairs, matched groups). In v1 they gate lint and simulation; later they are
  what *emits* layout constraints — netclasses, diff-pair rules, matched-length
  groups — exactly as timing constraints drive an FPGA's P&R.
- **Silent mis-inference is the failure mode to design against.** The FPGA
  equivalent of "meant BRAM, got LUTRAM" is "meant isolated CAN, got a bare
  transceiver." FPGA tools only reveal this in a synthesis report after the fact;
  InferSynth's intake lint rejects ambiguous idioms interactively, before
  synthesis runs (§6).

## 3. Principles

1. **Deterministic middle.** From signed-off formal spec to schematic, every step
   is code: constraint filtering and scored heuristics over the catalog,
   reproducible (same input → same output) and explainable ("selected X because:
   meets 3 A, AEC-Q100, lowest BOM cost of 4 candidates").
2. **LLM at the edges only.** Intake clerk (FRD → canonical idioms, with the human
   signing off the formal spec), catalog factory (draft entries from datasheets),
   flow driver (orchestrate runs, triage failures, explain results). It proposes;
   it never picks.
3. **Verification gates everything.** No artifact — catalog entry or synthesized
   design — is trusted because of who or what authored it. It passes the gates
   (§7) or it does not exist. This neutralizes the "is AI-generated content
   trustworthy" question by holding all authors to identical machine-checked
   standards.
4. **The catalog is the dataset is the product.** The language users write, the
   blocks synthesis instantiates, and the benchmark corpus are all views of the
   same versioned dataset (§5).

## 4. Intermediate representation

**Authoring/inference IR: native Python.** The IR is a Python-embedded structural
DSL (the pattern Amaranth/migen proved for the FPGA flow): a `Block` declares
ports, parameters, and idiom vocabulary; a `Design` is a tree of block instances
and nets. Elaboration walks the instance tree; the catalog matcher and the KiCad
compiler consume the elaborated structure directly. No external parser, no
fragile bindings on the critical path; the IR is importable, unit-testable code.

**Simulation target: SystemC-AMS, by emission.** Each catalog block carries a
behavioral SystemC-AMS model. The elaborated design *emits* a SystemC-AMS
top-level (structural instantiation of the blocks' models) plus the testbench
from the spec, which is compiled and run as the simulation gate. Emission — not
live binding — keeps the heavy toolchain off the critical path:

- Structural checks (elaboration, connectivity, catalog conformance, KiCad
  compilation) always run, everywhere.
- Behavioral simulation runs where the SystemC-AMS toolchain is installed
  (mandatory in catalog CI; graceful degradation with a loud "sim gate SKIPPED"
  on user machines without it).
- **PySysC** (Accellera, cppyy-based) is an optional bridge for interactive
  Python-driven runs of the emitted models — a convenience, never a dependency.

Users never write the IR (nor SystemC-AMS, nor JSON). They write the FRD; the
formal spec is IR constructed by the intake stage (§6).

## 5. The catalog

The primitive library. A **catalog entry** is a directory with a manifest,
containing:

| Artifact | Role |
|---|---|
| `model/` — SystemC-AMS behavioral + structural model | simulation gate; the block's meaning |
| `fragment/` — parameterized KiCad schematic fragment | what synthesis instantiates (via KiCAD-MCP-Server) |
| `idioms.yaml` — vocabulary this block contributes to the FRD language | keywords, parameters, ranges, ambiguity rules (§6) |
| `selection.yaml` — matching + binding metadata | electrical ratings, qualification (e.g. AEC-Q100), package/footprint, cost/sourcing hooks, scoring attributes |
| `testbench/` — stimulus + expected results | the entry's own acceptance test and its contribution to the benchmark corpus |
| `floorplan/` *(optional)* — relative placement of the block's parts on the PCB | hard-IP-style layout knowledge (see depth tiers below) |
| `routes/` *(optional)* — pre-routed critical intra-block traces (crystal loops, RF feeds, sense lines) | ditto |
| `manifest.yaml` — identity, version, provenance, license of referenced symbols/footprints | dataset hygiene |

**Library depth is graded, not uniform** — the FPGA soft-IP/hard-macro spectrum:

- **L0 — soft:** schematic fragment only. Parts land on the board as a ratsnest.
- **L1 — floorplanned:** L0 + relative placement of the block's own parts
  (cluster geometry, courtyard keep-outs).
- **L2 — hard-routed:** L1 + pre-routed traces for the block's *internal*
  critical nets.

Depth is per-entry and optional; a catalog is useful with nothing but L0
entries. Crucially, **inter-block interconnect is always the user's job** —
InferSynth never routes between blocks, even where it could. An "Arduino
Uno-class MCU" primitive may arrive with its crystal floorplan-placed and its
oscillator loop pre-routed (L2 internally), while every connection *to other
blocks* remains an unrouted ratsnest by design.

**Entry gates (CI, identical for all authors):**
1. Behavioral model simulates against its testbench and passes.
2. Fragment compiles to a sheet and is ERC-clean in isolation (with documented
   benign-warning allowlist).
3. **Model↔fragment equivalence:** the structural model's port/net topology and
   the fragment's netlist partition must correspond — the killer check that keeps
   simulation results meaningful about the schematic that ships.
4. Idiom vocabulary is well-formed and collision-checked against the installed
   catalog (no two entries may claim the same idiom with overlapping parameter
   ranges without an explicit disambiguation rule).

**The AI catalog factory:** an agent pipeline that drafts entries from datasheets
and reference designs — model skeleton, fragment, testbench, idioms — then runs
the gates. Humans review what passes; nothing that fails is ever surfaced as a
candidate. **Community submissions** are PRs run through the same CI. The factory
also assists submitters: reviews a draft entry, points at the failing gate,
proposes fixes.

## 6. The FRD language

The hurdle: users must write requirements that play the role of RTL without
learning the IR. The FPGA answer transfers directly — RTL authors never learn the
primitive library either; they write **inference idioms** (describe a memory as
an array with the right access pattern → BRAM is inferred). The idiom vocabulary
is implied by the primitive library.

Therefore: **the FRD's controlled vocabulary is generated from the catalog, never
hand-designed.** Each entry's `idioms.yaml` contributes keywords and parameter
schemas ("CAN FD", `bitrate ≤ 8 Mbps`, `isolated: bool`, `standby: bool`). The
language cannot drift from what synthesis can actually do, and it grows exactly
when the catalog grows.

**FRD lint** is then mechanical: every requirement must resolve to catalog
vocabulary with in-range parameters. The LLM's role is linter/normalizer — it
rewrites "needs to talk to the car bus reliably" into candidate canonical idioms
and *asks* which bus; it never picks. Ambiguity is rejected at intake, not
discovered in a report. An unresolvable requirement is not a failure but a
diagnostic — **"no primitive available"** — which is the catalog-gap signal that
feeds the submission pipeline (§5). The loop:

```
FRD ⇄ LLM lint ⇄ human sign-off → formal spec (IR)
        │
        └─ "no primitive available" → catalog factory / community submission
```

The signed-off formal spec is the determinism boundary: from here to schematic,
no LLM, no randomness.

## 7. Verification gates (per synthesized design)

1. **Simulation:** emitted SystemC-AMS top vs. the spec's testbench.
2. **ERC-zero** via `kicad-cli` (ground truth), full-hierarchy from the root
   sheet, with a triaged benign-warning policy.
3. **Netlist partition equivalence:** the elaborated IR's net→{pin} partition vs.
   the generated schematic's exported netlist — the compiler proves it did what
   the IR said.
4. **Rendered-output review:** SVG render eyeballed by a vision pass for overlap/
   legibility defects (cosmetic gate, non-blocking, reported).

A design that fails a gate re-enters the flow with the diagnostic; it is never
delivered.

## 8. Relationship to KiCAD-MCP-Server

InferSynth *consumes* the (improved) KiCAD-MCP-Server — MIT-licensed, driven over
MCP, no code linkage (GPL-3 here is compatible with talking to it). The synthesis
backend is a client that speaks the per-sheet build recipe proven on real
hardware: `create_schematic` → `batch_add_and_connect` (global-label convention)
→ no-connects → ERC → render. It depends on the upstream contribution set
(in flight on `feat/upstream-contribution`), notably: fail-loud schematic loading,
`repair_flat_symbols`, `lint_offgrid`, the completed netclass API, and the
`.kicad_pro` round-trip fix.

## 9. Form factor

Four deliverables in one repo:

1. **Python package** (`infersynth`): IR, elaborator, catalog matcher, part
   binder, KiCad compiler (MCP client), SystemC-AMS emitter, gate runners, FRD
   lint engine. Importable, testable, CLI entry points.
2. **MCP server** (`infersynth-mcp`): exposes the pipeline as tools
   (`lint_frd`, `elaborate_spec`, `match_catalog`, `synthesize`, `run_gates`,
   `catalog_search`, `catalog_submit_check`) so any agent can drive it.
3. **Thin skill**: the agent-side playbook — how to conduct the FRD lint
   conversation, when to invoke which tool, how to present diagnostics. No logic
   lives here.
4. **The dataset**: the catalog itself (versioned in-repo to start; splittable
   later), plus worked examples (FRD → spec → design) that double as integration
   tests and the public benchmark corpus.

## 10. Roadmap — Synth before Infer

- **v1 (Synth):** IR + elaborator; catalog format, gates, and CI; seed catalog
  (~10–15 blocks — candidate list: buck regulator, LDO, isolated DC-DC, CAN-FD
  transceiver ± isolation, LIN/K-line, RS-485, I²C isolator, level shifter,
  ESD/TVS group, connector/IOB, op-amp stage, analog mux, crystal/oscillator);
  IR→KiCad compiler; SystemC-AMS emitter + sim gate; gate runner. Spec authoring
  is manual (AI-assisted outside the tool). Demo: spec in → verified multi-sheet
  schematic out.
- **v2 (Infer):** the deterministic inference engine — spec idioms → catalog
  match → scored part binding; `selection.yaml` scoring; "no primitive available"
  diagnostics; catalog factory v1.
- **v3 (intake):** LLM FRD lint front-end + sign-off workflow; community
  submission pipeline; MCP server + skill polish.
- **Later (layout):** floorplan-driven placement of block clusters (riding
  `hierarchical_place`), instantiation of L1/L2 floorplans and pre-routed
  intra-block traces, constraint emission from clock domains (netclasses,
  diff pairs, matched groups), SI/power-budget closure gates. Inter-block
  routing remains the user's, always.

## 11. Non-goals

- No LLM-decided part selection, topology, or connectivity — ever (principle 1).
- No free-form "AI draws a schematic" mode.
- **No inter-block routing, ever** — not a v1 deferral but a design boundary.
  Catalog entries may carry floorplans and pre-routed *internal* traces (§5
  depth tiers); connecting blocks to each other is the user's craft.
- v1 does not attempt PCB layout or mechanical.
- No proprietary designs in catalog, examples, or benchmarks; the corpus is
  built from public reference designs and synthetic compositions.

## 12. Repo layout (proposed)

```
infersynth/            # the package: ir/, catalog/, match/, bind/, compile_kicad/,
                       #   emit_sysc_ams/, gates/, lint/, mcp/, cli.py
catalog/               # the dataset: one directory per entry (§5)
examples/              # worked FRD → spec → design flows (= integration tests)
skills/infersynth/     # the thin skill
docs/                  # this document, IR reference, catalog authoring guide
tests/
```

License: GPL-3.0 (unchanged from v1).
