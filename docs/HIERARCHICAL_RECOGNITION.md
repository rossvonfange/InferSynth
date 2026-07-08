# Hierarchical Recognition — segment, then recognize

> Shared contract for the segmentation feature spanning three repos.
> Converged 2026-07-07. The primary code (segmenter) lives here in InferSynth;
> recon and Loom consume/feed it at the contracts below.

## The problem this fixes

Recognition today is **flat**: it matches small cells against a whole design as
one graph. On a real vendor board this finds nothing — the PolarFire `.brd`
imported to 562 components / 471 nets and recognized **0 cells**, correctly,
because (a) no MPN, and (b) a board's functional blocks (DDR interface, LCD
header, DIP/IO bank, power tree) are not small analog cells — they're larger,
**interface-bounded clusters**.

The fix is a **segmentation pre-pass**: partition the netlist along
protocol/interface boundaries into cell-sized clusters, then recognize (or
promote-to-new-cell) each clean segment. Interface boundaries are natural graph
cut-lines: dense local connectivity inside a cluster, a labeled bus bundle
crossing the boundary.

## Roles (decided — respect the seam)

- **InferSynth owns the structural segmenter + hierarchical recognition.**
  Partitioning by interface signature is the catalog's `interfaces.yaml` run in
  reverse — same "catalog reversed" logic that put the cell recognizer here. One
  segmentation core; recon, Loom, and the forward synthesizer all reach it.
- **recon supplies protocol knowledge + the PDF-schematic eyes.** Protocol
  signatures (bus pin-counts/topologies, standard connector pinouts) are
  *harvested* into InferSynth's interface catalog (RECON_HARVEST pattern — adopt
  knowledge, not code). At runtime recon's vision stack (`docrecon`/pdfplumber/
  tesseract) parses the vendor PDF schematic into label claims — entering Loom
  as an optional `loom[vision]` extra.
- **Loom orchestrates the conjunction.** Fuse `.brd` connectivity (exact, no
  intent) + PDF labels (intent: bus names, block groupings) through the existing
  `claims` kernel → a *labeled* netlist → InferSynth segments it along the
  labels → recover a fabric whose sites are clean subsystem segments.

## Contract 1 — the label claim (recon/PDF → Loom fusion → segmenter hint)

What the PDF-schematic source produces and the segmenter consumes as *hints*
(never as ground truth — connectivity is truth; labels guide the cut):
```
LabelClaim = {
  kind: "net_label" | "block" | "protocol" | "net_role",
  value: str,            # e.g. "DDR4_DQ0", "LCD Header", "I2C", "SPI0_MOSI"
  refs: list[str],       # component refs the label scopes (may be empty for a net_label)
  net: str | None,       # net name if kind is net_label / protocol / net_role
  provenance: {source: "pdf_schematic", confidence: "high|med|low", page: int},
}
```
These fuse via `infersynth.claims` alongside `.brd`/IPC/BOM claims. The
segmenter treats matching labels as strong same-cluster / cut-here signals.

**`net_role` — symbol-derived functional pin roles (the label seam).** Allegro
`.brd` import strips net-name roles, so on real vendor boards the structural
fingerprints starve — no `MOSI`/`SDA`/`MDIO` tokens to corroborate — and every
bundle falls to a generic `signal`/`bus`/`diff_pair`. A `net_role` claim
`{kind: "net_role", value: "<FUNCTIONAL_NAME>", net: "<net>"}` asserts that
`net` carries the functional role tokens in `value` (a pin-function name a
downstream provider recovers by looking up each part's KiCad **symbol** and
mapping its pin NUMBERS to functional pin NAMES). `value` is tokenized with the
same splitter `classify_interface`/`classify_connector` use; multiple `net_role`
labels per net union their tokens (a net touches several named pins). These
tokens fold into the corroboration set as `_tokens(net_name) ∪ label_tokens[net]`
in both the protocol classifier (`interface_signatures.classify_interface`) and
the connector classifier (`connectors.classify_connector`), so a generically
named bundle or a stripped-footprint connector gets NAMED. Honesty gate: labels
only ADD corroboration tokens — they never relax the structural gate
(net-count/diff-pair cardinality, topology, connector pin-count/`min_corroboration`).
A label-only-corroborated match reports basis `label_corroborated` (distinct
from `name_corroborated`) so provenance shows where the evidence came from. With
no `net_role` labels the result is byte-identical to the pure-connectivity
baseline.

## Contract 2 — the segment (segmenter output; recognizer/Loom input)

```
Segment = {
  id: str,                       # deterministic, stable
  component_refs: tuple[str],    # the cluster's components
  internal_nets: tuple[str],     # nets wholly inside the cluster
  boundary: tuple[BoundaryPin],  # (ref, pin, net, interface_kind?) crossing out
  interface_kind: str | None,    # e.g. "i2c", "ddr", "gpio", "power" — from interfaces.yaml + label hints
  label: str | None,             # human label if a PDF block named it
  provenance: {...},             # why this cut (density + which interface bundle + which labels)
}
SegmentationResult = { segments: tuple[Segment], residual: tuple[str] }  # residual = unclustered refs
```

## The pipeline

```
labeled netlist (fused .brd + PDF claims)
  → SEGMENT (infersynth.recognize.segment): cluster by local connectivity density;
    cut where an interface bundle (interfaces.yaml signature) or a PDF block label
    spans the boundary. Deterministic (SELECTION §8). This is recon's spanning-tree/
    absorption idea run in reverse: grow clusters until an interface bundle is the frontier.
  → per segment: RECOGNIZE against the catalog (existing cell recognizer, now scoped
    to a clean sub-netlist so it can actually fire) → a cell; OR if unrecognized,
    PROMOTE the segment to a candidate new cell (the capture/growth path — a DDR
    interface, an LCD header become new subsystem cells).
  → recover a fabric whose sites are the recognized/promoted segments; the true
    residual (unclustered glue) is loudly declared-not-verified.
```

## Subsystem-cell tier

Segments are a **new, larger class of cell** than today's analog cells: an
interface-bounded subsystem (DDR memory interface, LCD header, IO bank) with a
clean protocol boundary. They become fabric sites directly. This is the engine
for constructing a *broad* library of fabrics: segment many vendor boards →
harvest their protocol clusters as subsystem cells → recombine into new fabrics.
The vendor boards become a cell foundry, not one-off recoveries.

## Determinism & honesty

Segmenter is a pure function (stable cluster ids, documented tie-breaks, no
clocks/randomness). Labels are hints, never overrides of connectivity. Residual
and low-confidence segments are always surfaced, never silently dropped. A
promoted candidate cell is marked provisional until verified against its golden
partition (the recovered-fabric CI gate applies).
