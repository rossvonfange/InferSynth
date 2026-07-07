# FRD — BridgeSense-1, bridge-sensor signal conditioning chain

(The SEED_PLAN.md §1 acceptance target, written in the seed catalog's own
vocabulary with feeds pragmas declaring the signal path — NETFLOW tier 2.
Power rails wire by name, tier 1; the feeds chain wires the signal path.)

## Signal chain

- SNS-01 The system shall provide a 4-wire sensor input connector. [feeds: CND-01]
- CND-01 The system shall provide a bridge interface. [feeds: AMP-01]
- AMP-01 The system shall provide an instrumentation amplifier with gain of 100. [feeds: FLT-01]
- FLT-01 The system shall provide an active low-pass filter at 1000 Hz. [feeds: BUF-01]
- BUF-01 The system shall provide a unity buffer. [feeds: DRV-01]
- DRV-01 The system shall provide an adc driver. [feeds: OUT-01]
- OUT-01 The system shall provide an output header.

## Power and references

- PWR-01 The system shall provide a power input connector. [feeds: PRT-01]
- PRT-01 The system shall provide reverse polarity protection. [feeds: REG-01]
- REG-01 The system shall provide a linear regulator.
- REF-01 The system shall provide a voltage reference.
- MID-01 The system shall provide a rail splitter for a virtual ground.
- DEC-01 The system shall provide a bypass capacitor.
