# FRD — "AirCell" Classroom Air-Quality Node (ESP32)
# Format: hierarchical requirements tree (requirements-tool export style).
# Child requirements refine their parent; leaves are the binding statements.
# [D] = derived/rationale note, not itself a requirement.

SYS AirCell wall-mounted air-quality node
├── SYS.1 The node SHALL measure classroom air quality and report it to the
│         district dashboard over the building network.
│   ├── SYS.1.1 Sensing
│   │   ├── SYS.1.1.1 CO2 SHALL be measured 400–5000 ppm, ±(50 ppm + 5 %),
│   │   │             NDIR type. [D: photoacoustic drifted in 2023 pilot]
│   │   ├── SYS.1.1.2 Particulates SHALL be measured as PM2.5 and PM10,
│   │   │             0–500 µg/m³, laser scattering module acceptable.
│   │   ├── SYS.1.1.3 Temperature ±0.5 °C and RH ±3 % SHALL be measured.
│   │   │   └── SYS.1.1.3.1 The T/RH element SHALL be thermally isolated
│   │   │                   from board self-heating (guard slot or remote
│   │   │                   placement acceptable).
│   │   └── SYS.1.1.4 All sensors SHALL be sampled at 1/min or faster.
│   ├── SYS.1.2 Processing & connectivity
│   │   ├── SYS.1.2.1 The controller SHALL be an ESP32-family module with
│   │   │             on-module antenna (customer standard; fleet tooling).
│   │   ├── SYS.1.2.2 Wi-Fi 2.4 GHz WPA2-Enterprise SHALL be supported.
│   │   ├── SYS.1.2.3 The node SHOULD buffer ≥72 h of readings through
│   │   │             network outages (flash on module acceptable).
│   │   └── SYS.1.2.4 Firmware SHALL be field-updatable over the network;
│   │                 a physically accessible UART SHALL exist for recovery.
│   └── SYS.1.3 Indication
│       ├── SYS.1.3.1 A three-state indicator (good/elevated/high CO2)
│       │             SHALL be visible across a classroom.
│       └── SYS.1.3.2 Indicator SHALL be schedulable dark (nap rooms).
├── SYS.2 Power
│   ├── SYS.2.1 The node SHALL be powered from 802.3af PoE.
│   │   ├── SYS.2.1.1 An isolated PD converter SHALL provide the internal
│   │   │             rail(s); class 2 signature (≤6.49 W).
│   │   └── SYS.2.1.2 USB-C 5 V SHALL be accepted as an alternative supply
│   │                 (bench/sites without PoE); auto-select, no jumper.
│   └── SYS.2.2 Total draw SHALL NOT exceed 5 W continuous including the
│               PM sensor fan duty cycle.
├── SYS.3 Physical & environment
│   ├── SYS.3.1 Enclosure is customer-supplied (existing injection tool);
│   │           PCB SHALL fit its 90 × 60 mm bosses, connectors on south edge.
│   ├── SYS.3.2 Operating 0–40 °C indoor, non-condensing.
│   └── SYS.3.3 Serviceability: sensors listed in SYS.1.1.1/.2 SHALL be
│               socketed or connectorized for field replacement.
└── SYS.4 Compliance & production
    ├── SYS.4.1 Modular-approval radio only; no intentional-radiator cert
    │           at board level.
    ├── SYS.4.2 Production test SHALL be supported by a bed-of-nails-able
    │           test-point set (power rails, I2C/UART buses, boot straps).
    └── SYS.4.3 Target BOM ≤ $38 @ 1k. [D: bid ceiling from district RFP]
