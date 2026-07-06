# Seed Plan — target FRD, seed cell library, definable outcome

> The v1 vertical slice needs all three legs or it proves nothing: a **target
> FRD** written in idioms, a **seed cell library** sufficient to synthesize it,
> and a **machine-checkable outcome**. This document defines all three.
>
> Seed corpus: *The Art of Electronics* (Horowitz & Hill, 3rd ed.) — "AoE"
> below. Topologies are re-expressed, never copied; manifests cite AoE
> section numbers as provenance *references only* (see DESIGN.md §5.1).
> Section numbers below are from memory and **must be verified against the
> book** during cell authoring.

## 1. Target FRD: `BridgeSense-1`

A bridge-sensor signal-conditioning chain. Chosen because the FRD reads
naturally in idioms, the cell slice it selects is coherent and pure-analog
(plays to SystemC-AMS strengths), and the end-to-end acceptance simulation
(gain / bandwidth / clipping) is a far more convincing verification demo than
netlist equivalence alone.

Draft FRD (to be run through intake lint once the vocabulary exists — this is
also the lint's first test article):

- Measure a 4-wire Wheatstone bridge sensor, 350 Ω nominal, ±10 mV full-scale
  differential output, ratiometric excitation preferred.
- Differential gain 100 V/V ±1 %.
- Bandwidth DC–1 kHz (−3 dB); 2nd-order Butterworth anti-alias low-pass at
  1 kHz.
- Output 0.25–4.75 V single-ended, mid-scale at 2.5 V, driving a SAR ADC input
  modeled as 10 kΩ ∥ 50 pF.
- Power: single 12 V ±10 % input, reverse-polarity protected; internally
  regulated rails as needed.
- Bridge excitation 5 V from a precision reference (ratiometric to ADC ref).
- Operating range 0–70 °C.
- Connectors: 4-wire sensor input, 2-pin power in, 4-pin output header
  (OUT, REF, GND, shield).

## 2. Seed cell library (~20 cells, all L0)

Grouped by role. Every cell ships the full artifact set (DESIGN.md §5):
model, fragment, idioms, selection, testbench, manifest.

### Signal path
| Cell | AoE ref (verify) | Testbench sketch |
|---|---|---|
| `bridge-interface` | Ch 5 (precision; bridge measurement) | excitation → known ΔR → differential output within tol |
| `instrumentation-amp-3opamp` (+ IC-INA binding alternative) | §5.15-ish | gain accuracy, CMRR > spec, input range |
| `opamp-gain-noninverting` | Ch 4 (op-amp golden rules) | DC transfer, gain ±tol, −3 dB BW, rail clipping |
| `opamp-gain-inverting` | Ch 4 | same as above, sign inverted |
| `summing-offset-stage` | Ch 4 | output = Σ(weights·inputs) + offset, ±tol |
| `sallen-key-lowpass-2` | Ch 6 (active filters) | freq sweep: fc ±10 %, Butterworth Q, stopband slope |
| `mfb-lowpass-2` | Ch 6 | same sweep; alternative topology for the same idiom |
| `unity-buffer` | Ch 4 | gain ≈ 1, output Z, load drive into ADC model |
| `adc-driver-rc` | Ch 4/§13 (driving converters) | settling into 10 kΩ∥50 pF within tol |

### Precision & protection
| Cell | AoE ref (verify) | Testbench sketch |
|---|---|---|
| `vref-shunt` (TL431-class) | Ch 9 (voltage ref) | line/load regulation, tempco bound (model-level) |
| `input-protection-rfi` (series R, clamps, RFI cap) | Ch 5 / interference | overvoltage clamp sim; passband unaffected |
| `output-clamp` | Ch 1/4 (diode clamps) | output never exceeds rails + V_diode |

### Power
| Cell | AoE ref (verify) | Testbench sketch |
|---|---|---|
| `linear-reg-fixed` (78xx/LDO class) | Ch 9 (regulators) | dropout, load regulation, current limit behavior |
| `rail-splitter-virtual-gnd` | Ch 4 (single-supply) | output Z vs freq, sink/source symmetry |
| `power-input-conditioning` (reverse-polarity, fuse, bulk C) | Ch 9 practical | reverse-input survives; inrush bounded |
| `current-source-bjt` | Ch 2 (transistor current sources) | compliance range, output Z (bridge-excitation option) |
| `decoupling` (per-IC pattern) | construction practice | structural-only (sim stub); ERC + fragment checks |

### IOB (connector cells — not AoE; generic)
| Cell | Testbench sketch |
|---|---|
| `conn-sensor-4wire` | structural-only |
| `conn-power-2pin` | structural-only |
| `conn-output-header` | structural-only |

Notes:
- `sallen-key-lowpass-2` vs `mfb-lowpass-2` deliberately claim the **same
  idiom** ("2nd-order active low-pass") with a disambiguation rule — this
  seeds the §5 gate-4 collision machinery and the v2 scoring engine with a
  real case from day one.
- `decoupling` and the connector cells are structural-only (sim stub):
  they exercise the graceful-degradation path of the sim gate.
- AoE gives topologies; `selection.yaml` binds modern orderable parts
  (no LF411-era bindings in the seed).

## 3. Phasing — golden cells first

1. **Phase 0 — three golden cells** (`opamp-gain-noninverting`,
   `sallen-key-lowpass-2`, `linear-reg-fixed`): hand-built end-to-end to
   *design* the cell format — directory schema, fragment parameterization,
   idiom schema, testbench harness, manifest. Format questions get settled
   here, on real artifacts, before volume.
2. **Phase 1 — the rest of the seed library**, hand-built to the frozen
   format. (The AI catalog factory stays out of the seed: hand-building is
   what validates the format the factory will later be held to. The full AoE
   sweep is the factory's benchmark, later.)
3. **Phase 2 — spec authoring**: `BridgeSense-1` FRD → formal spec (manual,
   AI-assisted outside the tool per the v1 roadmap).
4. **Phase 3 — the demo**: compile spec → schematic via KiCAD-MCP-Server,
   run all gates.

## 4. Definable outcome (v1 acceptance criteria)

The v1 demo passes when, from the signed-off `BridgeSense-1` spec, one
command produces:

1. A multi-sheet KiCad schematic (one sheet per cell instance group), with
   **ERC = 0 errors** (full hierarchy, documented benign-warning allowlist).
2. **IR↔netlist partition equivalence**: elaborated spec's net→{pin} map ==
   exported schematic netlist.
3. **End-to-end AMS simulation** of the emitted design passing the spec
   numbers: gain 100 ± 1 %, f₋₃dB = 1 kHz ± 10 %, Butterworth response
   shape, output clamped within 0.25–4.75 V, mid-scale offset at 2.5 V ±
   tol, correct behavior at 12 V ± 10 % supply corners.
4. **A bound BOM**: every part a real, orderable MPN from `selection.yaml`.
5. Every seed cell individually green in catalog CI (all four entry gates).

Anything less is a diagnostic, not a demo.
