# Floorplan — a placed starting point, not a layout tool

> Converged 2026-07-07 (Round 4). Governs the stage that follows synthesis:
> turning a decided, wired design into a **placed, grouped, UNROUTED**
> `.kicad_pcb` the user opens in pcbnew and starts routing immediately.

## Thesis: pcbnew IS the floorplanner

InferSynth writes the schematic (the *what*) and now emits a **placement
proposal** (a *where to start*). It does not, and will not, ship an interactive
placement/routing UI. That is a deliberate anti-goal — the same one DESIGN.md
and UX.md draw against the "cynth" trap: **do not rebuild the EDA tool**. The
craft of inter-cell layout — pushing footprints, tuning courtyards, routing
copper — is the user's, done in KiCad, which is already the best tool in the
world for it. The floorplanner's job is to remove the blank-canvas cold-start:
give the user 39 footprints already grouped by function, ordered by signal
flow, and biased to the right board edges, so the first thing they do in pcbnew
is *route*, not *hunt-and-arrange forty anonymous parts*.

This does not bend the "inter-cell routing is the user's craft" principle
(DESIGN §2 / UX) any more than the schematic writer bends "the schematic is the
user's" — it fulfills it, by handing over a good draft. The board is emitted
**unrouted and carrying no nets**: it is placement STRUCTURE, a scaffold, never
a finished layout.

## Three mechanisms

The floorplanner (`infersynth/floorplan/`) is **pure and deterministic**
(SELECTION §8): every position is a function of the design dir, the catalog, and
the optional spec — no clock, no randomness, two runs byte-identical. Its inputs
are exactly what synthesis already produced: the century-stamped netlist export
(refs + footprints), `wiring_plan.json` (nets + instance order), and the
catalog (port directions). It composes from three ideas, cheapest first:

1. **Groups — cluster = instance.** Every footprint whose refdes century maps
   to an instance (`U101` ⇒ instance 1; the emit.py century scheme makes
   footprint→instance mapping FREE — no extra bookkeeping) becomes one KiCad
   `(group)`. A group is the unit of placement: select it in pcbnew and the
   whole cell moves together, mirroring the schematic's one-sheet-per-cell
   structure onto the board. The pcbnew `PCB_GROUP` API is used natively (it is
   available in this KiCad; no text surgery on the saved file was needed —
   `SetName` + `AddItem` + `board.Add`).

2. **Dataflow auto-placement.** The signal nets induce a DAG over instances
   (the DAG law, NETFLOW.md): each net is oriented driver→sink from its
   members' port directions (an `out` port drives; a connector's `passive` port
   drives a downstream `in`). A longest-path layering gives every connected
   cluster a `flow_depth`, and clusters are placed left-to-right in that order —
   sensor → bridge → in-amp → filter → buffer → ADC-driver reads across the
   board the way the signal actually flows. Flow-disconnected clusters (a lone
   decoupling bank) sort by name AFTER the connected chain.

3. **Spec placement hints.** The spec `placement:` key (small, v0 vocabulary)
   lets the author override:
   ```yaml
   placement:
     board: {width_mm: 80, height_mm: 60}   # fixed outline (else auto-sized)
     edges: {SNS-01: west, OUT-01: east}    # pull a requirement to a board edge
   ```
   An edge hint pulls a cluster to a compass band: north/south bias its row,
   west/east bias its column. **Defaults** (documented, applied when no hint is
   given): power-rail-source instances — a regulator driving a rail, or a
   power-input connector — default **west**; output-side connectors (a
   connector that is a pure signal sink) default **east**. Everything else
   flows through the middle.

The board outline is either the spec's or computed from total courtyard area ×
a margin (aspect ~1.4); clusters are **row-packed** — side by side within a
row, rows stacked by the row's tallest cluster — so no two courtyards can
overlap. That is a structural invariant, verified programmatically
(`check_no_overlap`), not hoped for.

### Courtyard approximation

The pure planner reasons over rectangular **tiles**, never real copper, so it
stays importable and testable without pcbnew. A footprint's tile is a
keyword-estimated body size (`extents.py`: 0603 → 2.2×1.4 mm, SOT-23 → 3.2×3.2,
a 1×N header sized by pin count, …) **plus** a generous per-side pad. The
estimate deliberately over-bounds the real courtyard (part body + ~0.25 mm
clearance), so the real footprints the board emitter places land comfortably
inside their reserved tiles and the no-overlap guarantee carries over to the
actual board. A future refinement can feed pcbnew-measured courtyards into the
same planner (the `extents` seam) for tighter packing.

## What v0 does NOT do (loud boundaries)

- **No nets, no ratsnest.** The board carries footprints and groups only. The
  deliverable is PLACEMENT; ratsnest import from the schematic netlist (so
  pcbnew shows the airwires to route) is the first follow-on refinement.
- **No routing.** By thesis, forever the user's / a fabric's job.
- **Footprints within a cluster are a simple grid.** No intra-cell craft —
  that is the L1 hook below.

## Composition with fabric (placement-proposed vs placement-done)

FABRIC.md and this doc are the two ends of one spectrum — *where does placement
come from?*

- **Fabric (placement-done):** the board is placed AND routed once, by a human,
  at fabric-design time; synthesis only POPULATES sites (DNP + value stuffing).
  Zero per-design layout. The cost: a fixed form factor, area bought for
  unstuffed sites.
- **Floorplan (placement-proposed):** synthesis proposes a fresh placement per
  design; the user routes. Full flexibility, a real (but warm-started) layout
  step.

They share the same upstream (decide + wire) and the same grouping idea (a
fabric site ≈ a floorplan cluster). A natural future middle: floorplan a design,
then *promote* the routed result to a fabric — the auto-floorplan is the draft a
fabric is hand-perfected from.

## Future hook: L1 cell floorplans

The clear next refinement is **per-cell placement templates**. Today a cluster
is a uniform grid; a cell could instead carry an `L1` floorplan fragment — the
relative placement of its own footprints (the decoupling cap hard by the op-amp
pin, the gain resistors in a tight ladder), captured once with the cell like its
schematic fragment is. The design-level floorplanner would then place *cluster
boxes* (unchanged) and stamp each cluster's interior from its cell's L1 template
— composing cell-craft the same way the schematic composes cell fragments. The
`ClusterPlacement` boundary is already the seam for it.

## Build order / status

Landed (Round 4): the pure planner (`floorplan/plan.py`), the footprint-extent
estimator (`extents.py`), the pcbnew board emitter (`board.py` +
`_pcbnew_emit.py`), the spec `placement:` loader, and `infersynth board`.
Acceptance: BridgeSense → a 39-footprint / 13-group board, flow-ordered, zero
tile overlaps, kicad-cli-parseable. Next: ratsnest import; then L1 cell
floorplans; then fabric promotion.
