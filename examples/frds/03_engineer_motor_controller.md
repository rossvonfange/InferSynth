# FRD-2031 rev B — BLDC Servo Drive, 48 V / 20 A

## 1. Scope
Single-axis sensored BLDC drive for AGV traction. This document specifies
electrical function only; firmware and mechanical are separate FRDs.

## 2. Requirements
IDs are permanent; SHALL = mandatory, SHOULD = target.

| ID | Requirement | Value | Verify by |
|----|-------------|-------|-----------|
| PWR-01 | SHALL accept battery input | 36–58 VDC | test |
| PWR-02 | SHALL survive reverse polarity | indefinite | test |
| PWR-03 | SHALL provide logic rails | 12 V ±5 % @ 1 A, 3V3 ±3 % @ 500 mA | test |
| DRV-01 | SHALL drive 3-phase bridge, continuous | 20 A RMS/phase | test |
| DRV-02 | SHALL drive peak (10 s) | 40 A | test |
| DRV-03 | Phase current sense, per-phase | ±50 A range, 1 % FS | analysis |
| SNS-01 | SHALL interface Hall sensors | 5 V, RC filtered | inspect |
| SNS-02 | SHOULD interface ABZ encoder | RS-422, 5 MHz | test |
| COM-01 | SHALL provide CAN 2.0B | 500 kbps, isolated | test |
| PRT-01 | Overcurrent trip | < 2 µs to safe state | test |
| PRT-02 | Bus overvoltage clamp (regen) | 60 V, 500 W transient | analysis |
| ENV-01 | Operating temperature | -20…+60 °C | analysis |
| ENV-02 | Conformal-coat compatible layout | — | inspect |

## 3. Interfaces
J1 battery (XT90), J2 phases (M6 lugs), J3 Hall+encoder (JST GH 10p),
J4 CAN (M12 5-pin), J5 debug UART (do not populate in production).

## 4. Explicitly out of scope
Charging, cell balancing, brake resistor (external), EMC filtering beyond
CISPR 25 class 3 pre-compliance.
