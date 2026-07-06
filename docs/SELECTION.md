# Selection & Taxonomy — how a requirement becomes a cell choice

> Converged 2026-07-06. Governs the v2 inference/packing engine; v1 artifacts
> (cell.yaml, catalog layout) adopt the schemas now so the catalog doesn't need
> a migration later. Companion to DESIGN.md (§5 catalog, §6 FRD language) and
> UX.md. Principle 1 (deterministic middle) and principle 3 (verification gates
> everything) bind every stage below.

## The pipeline, named

```
libraries (scope)
  → allocation (human steering, per requirement subtree)
    → recall: idioms (strict) + embeddings (semantic)   [proposes candidates]
      → absorption legality: capacity + code templates  [what MAY be rewritten]
        → decision: cost vectors × weight profiles      [deterministic, scored]
          → explanation with provenance                 [audit trail]
```

A statistical stage may only ADD candidates for consideration; it can never
select a winner. Every winner is justified solely by the deterministic layers.

## 1. Libraries (catalog namespaces)

The catalog gains KiCad-style library grouping: `catalog/<library>/<cell>/`.
A `library.yaml` at each library root: `{name, description, tier, maintainer}`.
Tiers: `official` | `community` | `local` (curation levels, PCM-style; scoring
never reads tier — it exists for screening and user trust display only).
Existing flat cells migrate to `catalog/core/…`. Cell identity becomes
`library/name@version`; bare `name@version` resolves iff unambiguous.

## 2. Allocation (requirements → libraries)

An **allocation** binds a requirement subtree to catalog scope:

```yaml
# lives in the FORMAL SPEC, never in FRD prose
allocations:
  - at: SYS.1.2          # requirement id; applies to the subtree
    allow: [microcontrollers, timing]
    deny:  [dacs]        # deliberate exclusion — judgment enters via a
                         # declared, versioned channel (systems-engineering
                         # "requirements allocation")
```

Semantics: inherited down the tree; nearer node overrides; `deny` beats
`allow`; no allocation ⇒ all libraries compete (default). Allocation scopes
BOTH recall layers and bounds absorption (see §5). Applied interactively —
an LSP code action / conversation step during lint writes it into the spec.
New diagnostics: `frd.unallocated` (WARN under `allocation: strict` profiles,
absent otherwise), `frd.allocation-empty` (allocated scope contains no cell
matching the requirement — a scoped catalog-gap signal).

## 3. Taxonomy (function ontology)

`catalog/taxonomy.yaml`, versioned with the catalog. Categories are FUNCTION
tags — what an FRD asks for — never part families:

```yaml
version: 1
functions:
  timing:        {desc: oscillators, delays, PWM, sequencing}
  amplification: {desc: gain, buffering, drive}
  filtering:     {desc: frequency shaping}
  regulation:    {desc: rail generation and conditioning}
  conversion:    {desc: ADC/DAC, V-I, level translation}
  interface:     {desc: comms buses and transceivers}
  protection:    {desc: clamps, fusing, reverse polarity, ESD}
  connectivity:  {desc: connectors, headers, test points}
  indication:    {desc: human-visible/audible state}
  processing:    {desc: programmable computation}
```

Cells claim `functions: [tag, …]` in `idioms`; the validator rejects tags not
in taxonomy.yaml. Implementation technology is an attribute, not a category:
`manifest.technology: analog-discrete | analog-ic | passive | programmable |
electromechanical`. One function, many technologies — that is the degree of
freedom the decision layer optimizes over.

## 4. Recall — two layers, both scoped by allocation

**Layer 1, idioms (strict):** the existing catalog-generated vocabulary
(DESIGN §6). Fully auditable. Always on.

**Layer 2, embeddings (semantic):** each cell (and each absorbable template,
§5) carries a capability text; `embedding.json` beside it caches
`{model_id, dim, vector}`. Requirement text embeds against these; similarity
above a catalog-configured floor SURFACES the cell as a candidate.

- **Text is source of truth; the vector is a derived cache.** The catalog
  manifest pins the embedding model id; a model upgrade is a catalog-wide
  re-embed (diffable, versioned). Pinned model + pinned text ⇒ reproducible
  candidate sets — deterministic in the reproducibility sense, though not
  hand-auditable; that residue is confined here, where it cannot decide.
- **Profile knob:** `recall: strict | semantic`. `strict` = idioms only
  (audit/safety/reproduction mode). `semantic` widens the net.
- **Gap detection (the vector's best job):** high similarity to a programmable
  cell with NO matching template is never a candidate — it emits
  `frd.absorbable-no-template`, a catalog-gap diagnostic aimed at the factory
  ("write and gate a template for this") or the user.

Provenance is recorded per candidate: `surfaced_by: idiom | semantic(0.83) |
allocation-forced`, and appears in the explanation (§7). Disclosure is
non-negotiable.

## 5. Absorption — capacity + templates, never capability claims

A programmable/complex cell's functional space is generative and cannot be
enumerated in words. It doesn't have to be: **a cell may only absorb a
function it ships a verified code template for.** No template ⇒ nothing to
emit ⇒ nothing to gate ⇒ not synthesizable, regardless of what the silicon
could do. The enumerable list is therefore honest — it is a directory listing,
not a capability claim.

```yaml
# cell.yaml additions for programmable cells
capacity: {timers: 4, pwm: 2, adc_ch: 8, gpio: 12}
absorbs:
  - function: timing.astable          # taxonomy tag (dotted refinement ok)
    consumes: {timers: 1, gpio: 1}
    template: templates/astable/      # code artifact tier: template source +
                                      # params + its own testbench, gated in
                                      # cell CI like fragment/model are
```

Packing consumes capacity like CPLD macrocell fitting. Absorbing emits the
template instance into the design's firmware artifact set (v1 output:
generated FIRMWARE.md stub listing absorbed functions + bound params; later:
compiled/templated source as a first-class emitted artifact).

**Policy knobs:**
- Profile: `absorption: off | conservative | aggressive`. Conservative packs
  only within one requirement group; aggressive packs greedily across the
  design — but never across an allocation boundary (absorb freely *within* an
  allocated library scope, never through it).
- FRD-level per-function override: a requirement may forbid absorption
  ("indication SHALL be independent of the processor") — carried by lint into
  the packer as a hard constraint.

## 6. Decision — cost vectors × weight profiles

Every candidate mapping (single cell, pack, or absorption rewrite) gets a
**cost vector**; `cell.yaml` gains `costs:`:

```yaml
costs:
  bom: {qty1: 1.72, qty1k: 0.61}   # currency-normalized
  area_mm2: 180
  power_mw: 15
  part_count: 6
  dev_hours: 0        # firmware/config engineering (the code dimension)
  production_steps: [] # e.g. [programming, calibration]
  sourcing_risk: 1    # 0=jellybean … 3=single-source
```

A **weight profile** (in the spec, named or inline) scores vectors:

```yaml
profile: production   # or explicit {bom: 5, area: 2, dev_hours: 0.5, …}
```

Named defaults mirror the FRD personas: `prototype` (dev_hours and
production_steps weighted heavily — firmware is expensive; the 555s win),
`production` (BOM/area at quantity — the PIC wins), `hobbyist` (part_count,
solderability). The knob doing real work is the same requirement set flipping
winners as the profile changes.

**Honest-accounting rules (the super-block discipline):**
1. **Covers compete against covers.** A composite/reference-design cell
   covering N requirements is compared against the best *composition* of
   cells covering the same N — never against one small cell at a time.
   Bundling cannot win by splitting the ledger.
2. **Cost vectors enumerate their interior.** A composite cell's BOM lines,
   area, and dev costs count everything inside; audited at submission the way
   the netlist gate audits connectivity.
3. Right-sizing is screening, not law: cells claiming many unrelated function
   tags get flagged for human review in factory/community CI; library tiers
   (§1) carry the curation. A super-block that wins honestly is the tool
   working — integration is often the correct answer.

## 7. Explanation

Every selection emits a machine- and human-readable trace: candidates
considered (with `surfaced_by` provenance), the ones rejected and by which
hard check (capacity, ports, allocation, constraint), the cost vectors and
weighted scores of the finalists, and the winner's justification in
deterministic terms only — never a similarity score as a reason. Rendered in
the panel and the CLI; stored beside the design as `selection_trace.json`.

## 8. The repeatability contract (run boundary)

**Synthesis is a pure function**: `(spec, catalog@version, taxonomy, weight
profile, lockfiles) → design`, identical on every re-run. Laziness (§ RECON
HARVEST 2: sim-guided lazy candidate expansion) is an efficiency strategy
inside a FIXED, deterministic exploration policy — deterministic expansion
order and tie-breaks, count-based budgets (never wall-clock), order-insensitive
parallel reduction, pinned embedding model, pinned AMS solver version/config.
Effort knobs change how much work a run does, never which answer it reaches.

**The escalation ladder splits at the run boundary.** In-run, only the
deterministic rungs execute (idioms → structured catalog → pinned embeddings →
sim-scored expansion). The upper rungs — LLM assistance, web research, human
judgment — NEVER execute inside synthesis: an unresolved residual emits a
typed diagnostic (ResolutionRequest pattern: frd.no-primitive, frd.ambiguous,
frd.absorbable-no-template, allocation prompts), resolved BETWEEN runs, with
every resolution landing as a durable input artifact (spec edit, allocation,
new cell/template, waiver) the next run consumes deterministically. All
steering is input change — the user changing available libraries is the
canonical instance of the general rule.

**Lockfiles**: any external data the decision layer reads (BOM pricing, stock,
sourcing) is snapshotted into a versioned lockfile; synthesis never touches a
live API; refreshing the lockfile is an explicit user action. No randomized or
seeded search — or seeds pinned and recorded, never implicit.

Ensemble variance (§ RECON HARVEST 3) remains valid under this contract: the
candidate ensemble is deterministically enumerated, so its variance is a
repeatable measurement of the SPEC's looseness, not solver noise.

## Adoption order

- **Now (v1 artifacts):** taxonomy.yaml + `functions:` claims; library
  layout + library.yaml; `costs:` section (validator-checked, unused by v1);
  reserve `capacity`/`absorbs`/`embedding.json` in the validator (accepted,
  schema-checked, unused).
- **v2:** recall layers, allocation enforcement, decision engine, packer,
  covers-vs-covers comparison, selection_trace.json.
- **v3:** template emission as compiled firmware artifacts; embedding-driven
  factory prompts; community screening automation.
