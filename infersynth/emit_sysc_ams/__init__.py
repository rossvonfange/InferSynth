"""SystemC-AMS source emitter and real-kernel seam (DESIGN.md §4, WP-S2).

The end-state simulation tier of DESIGN.md §4: the compiler *emits* standalone
SystemC-AMS C++ (TDF modules + testbench) that a real toolchain compiles and
runs out-of-process — **emit, don't bind** (no cppyy/PySysC on the critical
path; RECON_HARVEST §1). This package is the emitter (:mod:`.emit`), the per-cell
model-tier discovery + operating-point resolution (:mod:`.spec`), and the
toolchain probe + kernel seam (:mod:`.toolchain`). Emission is a pure function of
(cell/design, params) with no timestamps, so generated sources are byte-stable
and golden-comparable in CI; the actual compile-and-run path is real but SKIPs
LOUDLY when the toolchain is absent (the ``ams-simulation`` gate).

The v0 pure-Python tier (:mod:`infersynth.sim`, docs/SIM.md) remains the
``simulation`` gate for cells without an AMS model; neither tier replaces the
other yet (docs/SIM.md §7 precedence).
"""

from __future__ import annotations

from infersynth.emit_sysc_ams.emit import (
    EmissionReport,
    EmittedCell,
    EmittedDesign,
    emit_cell,
    emit_design,
    emit_makefile,
)
from infersynth.emit_sysc_ams.spec import (
    AMS_RAIL_MARGIN,
    AmsModuleSpec,
    OperatingPoint,
    ams_header_path,
    operating_point,
    spec_from_cell,
    spec_from_ports,
)
from infersynth.emit_sysc_ams.toolchain import (
    AmsKernel,
    AmsKernelError,
    AmsToolchain,
    SystemCAmsKernel,
    detect_toolchain,
    parse_csv_trace,
)

__all__ = [
    "AMS_RAIL_MARGIN",
    "AmsKernel",
    "AmsKernelError",
    "AmsModuleSpec",
    "AmsToolchain",
    "EmissionReport",
    "EmittedCell",
    "EmittedDesign",
    "OperatingPoint",
    "SystemCAmsKernel",
    "ams_header_path",
    "detect_toolchain",
    "emit_cell",
    "emit_design",
    "emit_makefile",
    "operating_point",
    "parse_csv_trace",
    "spec_from_cell",
    "spec_from_ports",
]
