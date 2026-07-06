# InferSynth

Inference and synthesis of PCBs from Functional Requirements Documents — the FPGA
toolchain workflow (source → simulate → infer → synthesize → place & route)
brought to PCB design.

The missing piece that makes FPGA-style *inference* impossible for PCBs is the
primitive library. InferSynth builds that **catalog** of verified circuit blocks
(SystemC-AMS model + KiCad fragment + testbench + selection metadata), derives a
controlled requirements language from it, and compiles signed-off specs into
verified KiCad schematics through a fully deterministic engine. LLMs assist at
the edges — linting requirements, drafting catalog entries, driving the flow —
but never make design decisions.

**Status:** v2 ground-up rewrite in design. Start with
[docs/DESIGN.md](docs/DESIGN.md).

License: GPL-3.0
