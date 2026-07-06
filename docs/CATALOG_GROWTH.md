# Catalog growth — three trust pipelines into the same gates

> Captured 2026-07-06. All three avenues feed identical cell CI (DESIGN §5
> gates); they differ in source and volume, never in standard (principle 3).

## A. Reference-derived (authored)
Known-good documented circuits (AoE, vendor app notes, textbooks) re-expressed
by hand or factory. High trust, low volume. This is SEED_PLAN. Provenance:
citation-only, topology re-expressed (§5.1 rule).

## B. OSH corpus mining (discovered)
Wide scrub of open-source-hardware KiCad projects (GitHub et al.):
frequent-subgraph mining over netlists to discover recurring subcircuits —
the recon signal-chain/motif machinery pointed at a corpus instead of one
board. The volume avenue.
- Candidates are EVIDENCE, not artwork: every mined motif is re-expressed
  through the factory and gated; provenance records source repos + licenses.
- **Cross-project frequency is a quality signal** no datasheet provides
  ("N independent projects decouple this exact way") — record it in
  `selection` metadata as a scoring attribute.
- Pipeline sketch (factory, v3): clone corpus → netlist extraction
  (kicad-cli) → motif mining (frequent subgraph, parameterized by ref-type
  pattern) → cluster + rank by frequency/diversity → draft cell → gates →
  human review.

## C. User capture (promoted)
The user draws a hierarchical sheet in KiCad and promotes it into their
`local` library; it then participates in synthesis like any cell.
- Kills three problems at once: "synthesis won't resolve the way I want"
  (draw it; your allocation makes it win), "I want this exact part", and —
  most valuable — **the tool as honest design critic**: the captured cell
  gets a cost vector and competes under the same scoring, so comparing your
  idea against the library's is just reading the selection trace.
- Mechanically cheap because the fragment conventions make a well-formed
  user sheet almost-a-cell already (hierarchical-label ports, ${IS.*} slots
  optional, sheet properties). **`infersynth capture <sheet.kicad_sch>`**
  (planned WP): infer ports from hierarchical labels, copy the sheet as
  fragment.kicad_sch, export + store the golden netlist, scaffold cell.yaml
  (manifest/ports/verification prefilled; idioms/params/costs prompted or
  LLM-drafted at the edge), place into the user's local library, run the
  gates. Capture is the inverse of `emit.instantiate`.
- Gates don't care who authored a cell. Local-tier capture needs no review;
  promotion local → community → official is the screening ladder (SELECTION
  §1 tiers).
