# Fabric boards — stuffing synthesis onto pre-routed platforms

> Captured 2026-07-06. The gate-array / structured-ASIC / FPGA-fabric model
> applied to PCBs: route once, by a human, with care; compile many designs
> onto it by POPULATION, not layout.

## The idea

A **fabric board** is a fully placed AND fully routed `.kicad_pcb` + schematic
whose components are organized as **sites**: an MCU site, banks of buffer/
driver/interface/power sites, connectors — every trace already routed. A
design is compiled onto a fabric by **stuffing**: synthesis selects which
sites are populated and emits
- a DNP set (KiCad's native `dnp` attribute on a copy of the fabric project),
- the BOM variant (populated sites + bound values for parameterized sites),
- firmware artifacts for absorbed functions (SELECTION §5),
- a fit report.

Synthesis performs NO placement and NO routing — which does not bend the
"inter-cell routing is the user's craft" principle (DESIGN §2/UX): it
fulfills it. The routing was done once, at fabric-design time, by a human,
and amortizes across every design compiled onto the fabric.

## Why

- **Closure speed**: no layout step at all — FRD to orderable variant in one
  run. The fastest possible development loop.
- **Reusable form factors**: shield/HAT/DIN-rail/mikroBUS fabrics as
  community artifacts; a fabric is to InferSynth what a devkit is to an MCU.
- **The FPGA analogy goes structural**: fabric = the routed fabric, sites =
  device resources, stuffing = configuration, fit report = utilization
  report ("needs 3 more driver sites" = "design doesn't fit in this part").

## Model sketch

- A fabric is a catalog artifact (its own tier beside cells): the routed
  KiCad project + `fabric.yaml` declaring sites — each site references a
  cell (or a small set of alternative cells sharing the footprint pattern),
  its refs on the board, and per-site parameter slots (value stuffing:
  a site's R/C values are chosen at stuff time within the routed topology).
- Fit = the packer constrained to the fabric's site capacities; absorption
  and allocation rules apply unchanged. Doesn't-fit is a first-class
  diagnostic, not a failure to route.
- Gates: fabric CI verifies the fabric itself once (full ERC/DRC + every
  site's cell equivalence); per-design gates verify the stuffed variant's
  netlist partition (populated subgraph) + simulation of the stuffed set.
  Unpopulated sites must be provably inert (no floating inputs enabling
  parasitic paths — tie-off/pull discipline is part of fabric CI).
- Costs are honest: unstuffed sites still buy area/board cost; fabric NRE
  amortizes only at volume — the existing weight profiles already reason
  about this trade (prototype profiles love fabrics; cost-optimized
  production may reject them).

## Status

Design-captured only. Depends on: packer (WP-P1), decision engine (WP-D1),
and a first hand-designed fabric (main-session layout craft — a good
candidate: an Arduino-shield-format instrumentation fabric exercising the
seed catalog's cells). Not scheduled before the v2 engine exists.
