"""``infersynth capture`` — promote a user's sheet into a catalog cell.

CATALOG_GROWTH.md sec C: "capture is the inverse of ``emit.instantiate``."
Where :mod:`infersynth.compile_kicad.emit` takes a catalog cell and stamps
hierarchical-label ports + ``${IS.*}`` value slots into a user's design,
:func:`capture_cell` takes a user's already-drawn hierarchical sheet and
reverse-engineers a cell scaffold from it:

1. **port inference** — parse hierarchical labels, map KiCad's label
   ``shape`` to a port ``direction`` and the port name to a ``kind`` heuristic
   (DESIGN.md sec 5 ``ports`` schema).
2. **fragment** — copy the sheet verbatim as ``fragment.kicad_sch`` (capture
   must never rewrite the user's geometry/layout — the emitter's own hard
   rule, mirrored here).
3. **golden netlist** — export + parse via :mod:`infersynth.gates.netlist`
   (reused, not reimplemented) and commit it as ``golden_netlist.txt``.
4. **parameter slots** — scan for ``${IS.<ref>}`` tokens (the emitter's own
   substitution grammar) and scaffold one unbounded, default-less float idiom
   param + identity binding per unique ref.
5. **cell.yaml** — manifest/ports/idioms/bindings/verification/depth, per
   DESIGN.md sec 5 and the ``catalog/core/opamp-gain-noninverting`` shape.
6. **library** — target an existing ``local``-tier library dir or create one
   (SELECTION.md sec 1).
7. **verification** — run the cell's gates in-process
   (:func:`infersynth.gates.run.run_cell_gates`) and report.

Text surgery only, no s-expression round-trip: port/slot inference reads the
raw schematic text with regexes, exactly the discipline
:mod:`infersynth.compile_kicad.emit` documents for the write path. Emission
and capture share no inference code (only the read-only netlist/gate
plumbing), so a capture bug cannot rubber-stamp its own output any more than
an emission bug can (UX.md writer/oracle independence, mirrored).
"""

from __future__ import annotations

import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from infersynth.catalog.taxonomy import TaxonomyError, load_taxonomy
from infersynth.gates.netlist import export_netlist
from infersynth.gates.run import run_cell_gates
from infersynth.gates.runner import GateReport, GateResult

__all__ = [
    "CaptureError",
    "CaptureResult",
    "capture_cell",
    "infer_ports",
    "discover_param_slots",
]

# --------------------------------------------------------------------------
# 1. Port inference
# --------------------------------------------------------------------------

# One hierarchical_label block: capture the label name, then lazily consume
# everything up to the following ``(at `` token (its position sub-form) so an
# optional ``(shape ...)`` in between is captured whether or not it is
# present. Mirrors the label text emit.py's harness/emit code writes (see
# infersynth/gates/harness.py's ``_SHAPE`` table and
# infersynth/compile_kicad/emit.py) without importing or re-parsing it.
_HIER_LABEL_RE = re.compile(
    r'\(hierarchical_label\s+"([^"]+)"(?P<body>.*?)\(at\s', re.DOTALL
)
_SHAPE_RE = re.compile(r"\(shape\s+(\w+)\)")

# KiCad hierarchical/global label shapes -> DESIGN.md sec 5 port direction.
# input/output map onto the obvious in/out. `passive` is already undirected.
# `bidirectional` and `tri_state` are both "no single fixed direction from the
# label alone" shapes in KiCad's own vocabulary, so both fold onto the IR's
# undirected `passive` direction (infersynth/ir/core.py: PortDirection.PASSIVE
# is documented as "undirected electrical terminal") rather than the writer's
# `inout`, which BUILD_PLAN never uses for a captured (as opposed to
# authored) port. This is a deliberate capture-side simplification, not a
# faithfulness claim about the user's intended signal direction — the printed
# port table calls it out so a human can tighten it by hand.
_SHAPE_TO_DIRECTION = {
    "input": "in",
    "output": "out",
    "bidirectional": "passive",
    "tri_state": "passive",
    "passive": "passive",
}

# Power-name kind heuristic: VCC/VDD/VEE/VSS/GND verbatim, or any name that
# looks like a rail (`V` followed by only uppercase letters/digits/underscore
# — VBATT, V3V3, V12, ...). Deliberately case-sensitive: hierarchical labels
# for power rails are conventionally all-caps in the wild, and case-sensitivity
# keeps a mixed-case signal name like "Vout" (lowercase "out") from being
# misread as a rail.
_POWER_NAME_RE = re.compile(r"^(?:GND|V[A-Z0-9_]*)$")


class CaptureError(ValueError):
    """Raised when a sheet cannot be captured (bad input, existing cell, ...)."""


def infer_ports(sheet_text: str) -> dict[str, dict[str, str]]:
    """Infer ``{port_name: {direction, kind}}`` from a sheet's hierarchical labels.

    Preserves first-seen order (matches the order a human reads the sheet in).
    A label repeated with an inconsistent shape raises :class:`CaptureError`;
    repeats with a consistent shape (KiCad fans the same net out to multiple
    coincident labels — see e.g. ``OUT`` in
    ``catalog/core/opamp-gain-noninverting/fragment.kicad_sch``) collapse to
    one port.
    """
    ports: dict[str, dict[str, str]] = {}
    for m in _HIER_LABEL_RE.finditer(sheet_text):
        name = m.group(1)
        shape_m = _SHAPE_RE.search(m.group("body"))
        if shape_m is None:
            print(
                f"infersynth capture: warning: hierarchical label {name!r} has no "
                "explicit (shape ...); assuming 'passive'",
                file=sys.stderr,
            )
            shape = "passive"
        else:
            shape = shape_m.group(1)
        direction = _SHAPE_TO_DIRECTION.get(shape)
        if direction is None:
            raise CaptureError(
                f"hierarchical label {name!r}: unrecognized KiCad label shape {shape!r}"
            )
        kind = "power" if _POWER_NAME_RE.match(name) else "electrical"
        if name in ports:
            if ports[name]["direction"] != direction:
                raise CaptureError(
                    f"hierarchical label {name!r} appears with conflicting shapes "
                    f"({ports[name]['direction']!r} vs {direction!r} inferred)"
                )
            continue
        ports[name] = {"direction": direction, "kind": kind}
    return ports


def _print_port_table(ports: dict[str, dict[str, str]]) -> None:
    print("inferred ports:")
    if not ports:
        print("  (none found — no hierarchical labels in this sheet)")
        return
    width = max(len(n) for n in ports)
    print(f"  {'NAME':<{width}}  DIRECTION  KIND")
    for name, spec in ports.items():
        print(f"  {name:<{width}}  {spec['direction']:<9}  {spec['kind']}")


# --------------------------------------------------------------------------
# 4. Parameter slot discovery
# --------------------------------------------------------------------------

# Mirrors compile_kicad/emit.py's own substitution + leftover-detection regex
# (``re.findall(r"\$\{IS\.[^}]+\}", text)``), grouped to capture the ref name.
_SLOT_RE = re.compile(r"\$\{IS\.([^}]+)\}")


def discover_param_slots(sheet_text: str) -> list[str]:
    """Unique ``${IS.<ref>}`` refs found in *sheet_text*, first-seen order."""
    seen: list[str] = []
    for m in _SLOT_RE.finditer(sheet_text):
        ref = m.group(1)
        if ref not in seen:
            seen.append(ref)
    return seen


# --------------------------------------------------------------------------
# Golden netlist
# --------------------------------------------------------------------------


def _write_golden_netlist(fragment_path: Path, out_path: Path) -> None:
    """Export *fragment_path*'s netlist (gates/netlist.py) and commit it in
    the same ``NET: ref/pin, ...`` format the hand-authored golden cells use
    (see e.g. ``catalog/core/opamp-gain-noninverting/golden_netlist.txt``)."""
    partition = export_netlist(fragment_path)
    lines = [
        "# Golden netlist partition for fragment.kicad_sch (net -> {ref/pin}), "
        "captured via `infersynth capture`."
    ]
    for net in sorted(partition):
        pins = ", ".join(f"{ref}/{pin}" for ref, pin in sorted(partition[net]))
        lines.append(f"{net}: {pins}")
    out_path.write_text("\n".join(lines) + "\n")


# --------------------------------------------------------------------------
# 6. Library handling
# --------------------------------------------------------------------------


def _ensure_library(library_dir: Path) -> None:
    """Create *library_dir*'s ``library.yaml`` (SELECTION.md sec 1, ``local``
    tier) if it doesn't already have one; no-op for an existing library."""
    lib_yaml = library_dir / "library.yaml"
    if lib_yaml.is_file():
        return
    library_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "name": library_dir.name,
        "description": (
            f"Local capture library ({library_dir.name}) — cells promoted from "
            "user sheets via `infersynth capture` (CATALOG_GROWTH.md sec C)."
        ),
        "tier": "local",
        "maintainer": "infersynth capture (local user)",
    }
    lib_yaml.write_text(
        "# Library manifest (SELECTION.md sec 1). Auto-created by "
        "`infersynth capture` the first time a cell targeted this directory.\n"
        + yaml.safe_dump(data, sort_keys=False, default_flow_style=False, allow_unicode=True)
    )


def _discover_taxonomy(start_dir: Path) -> dict[str, Any] | None:
    """Best-effort ``taxonomy.yaml`` discovery, walking *start_dir* and its
    ancestors (mirrors ``infersynth.catalog.loader._discover_taxonomy``'s
    walk, independently, since capture runs before the cell is loadable).
    ``None`` when no taxonomy.yaml is found anywhere above the library —
    the caller omits ``idioms.functions`` in that case rather than guessing."""
    candidates = [start_dir.resolve(), *start_dir.resolve().parents]
    for ancestor in candidates[:6]:
        candidate = ancestor / "taxonomy.yaml"
        if candidate.is_file():
            try:
                return load_taxonomy(candidate)
            except TaxonomyError:
                return None
    return None


def _default_keyword(name: str) -> str:
    return re.sub(r"[-_]+", " ", name).strip()


# --------------------------------------------------------------------------
# capture_cell
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CaptureResult:
    """The outcome of one :func:`capture_cell` call."""

    cell_dir: Path
    ports: dict[str, dict[str, str]]
    param_slots: list[str]
    gate_report: GateReport


def capture_cell(
    sheet: str | Path,
    *,
    name: str,
    library: str | Path,
    version: str = "0.1.0",
    keywords: list[str] | None = None,
    function: str | None = None,
    license: str = "GPL-3.0-or-later",
    force: bool = False,
) -> CaptureResult:
    """Promote *sheet* into a cell named *name* under library dir *library*.

    Raises :class:`CaptureError` for a missing sheet, an unreadable label
    shape, an existing cell directory without ``force=True``, or an
    ``--function`` tag that a discovered taxonomy rejects. Verification
    (step 7) never raises: a cell whose freshly-scaffolded ``${IS.*}`` slots
    have no default/range yet (by design — see the ``bindings`` docstring
    note below) cannot be harnessed for ERC, and that surfaces as a FAILED
    gate result, not a crash.
    """
    sheet_path = Path(sheet)
    if not sheet_path.is_file():
        raise CaptureError(f"sheet not found: {sheet_path}")

    library_dir = Path(library)
    cell_dir = library_dir / name
    if cell_dir.exists() and not force:
        raise CaptureError(
            f"cell directory {cell_dir} already exists (pass --force to overwrite)"
        )

    text = sheet_path.read_text()
    ports = infer_ports(text)
    slots = discover_param_slots(text)

    taxonomy = _discover_taxonomy(library_dir)
    functions: list[str] | None = None
    if function is not None:
        if taxonomy is None:
            print(
                f"infersynth capture: no taxonomy.yaml discoverable near {library_dir}; "
                f"omitting idioms.functions (requested {function!r})",
                file=sys.stderr,
            )
        else:
            root = function.split(".")[0]
            if root not in taxonomy:
                raise CaptureError(
                    f"--function {function!r}: root tag {root!r} is not in the "
                    f"discovered taxonomy.yaml (known: {sorted(taxonomy)})"
                )
            functions = [function]

    _print_port_table(ports)

    # --- everything above is read-only; only now do we touch the filesystem ---
    _ensure_library(library_dir)
    if cell_dir.exists():
        shutil.rmtree(cell_dir)
    cell_dir.mkdir(parents=True)
    (cell_dir / "model").mkdir()
    (cell_dir / "testbench").mkdir()
    (cell_dir / "model" / ".gitkeep").write_text("")
    (cell_dir / "testbench" / ".gitkeep").write_text("")

    fragment_dst = cell_dir / "fragment.kicad_sch"
    shutil.copyfile(sheet_path, fragment_dst)  # byte-identical, never rewritten

    _write_golden_netlist(fragment_dst, cell_dir / "golden_netlist.txt")

    idioms: dict[str, Any] = {"keywords": list(keywords) if keywords else [_default_keyword(name)]}
    if functions is not None:
        idioms["functions"] = functions

    bindings: dict[str, str] = {}
    if slots:
        # DESIGN.md sec 5 bindings grammar: ref -> expression over idiom
        # params. Capture has no way to know the *semantic* parameter a
        # slot represents, so it scaffolds the honest minimum: one
        # same-named float param per ref, unbounded and default-less ("user
        # is expected to refine it later" — CATALOG_GROWTH.md sec C /
        # BUILD_PLAN capture WP), bound by the identity expression.
        idioms["params"] = {ref: {"type": "float", "range": [None, None]} for ref in slots}
        bindings = {ref: ref for ref in slots}

    data: dict[str, Any] = {
        "manifest": {
            "name": name,
            "version": version,
            "description": f"captured from {sheet_path.name} — TODO",
            "provenance": "user capture",
            "license": license,
        },
        "ports": ports,
        "idioms": idioms,
    }
    if bindings:
        data["bindings"] = bindings
    data["verification"] = {"golden_netlist": "golden_netlist.txt"}
    data["depth"] = {"level": "L0"}

    header = (
        f"# Captured from {sheet_path.name} via `infersynth capture`\n"
        "# (CATALOG_GROWTH.md sec C: capture is the inverse of emit.instantiate).\n"
        "# TODO — a human should review this scaffold before it graduates past\n"
        "# the local library: tighten idioms.params ranges/defaults for any\n"
        "# ${IS.*} slots, add idioms.functions/selection/costs, confirm license.\n"
    )
    (cell_dir / "cell.yaml").write_text(
        header + yaml.safe_dump(data, sort_keys=False, default_flow_style=False, allow_unicode=True)
    )

    try:
        report = run_cell_gates(cell_dir)
    except Exception as exc:  # a cell the gate infra can't even harness yet
        report = GateReport()
        report.results.append(
            GateResult.failed(
                "harness-generation",
                f"{type(exc).__name__}: {exc}",
            )
        )

    print(report.summary())
    return CaptureResult(cell_dir=cell_dir, ports=ports, param_slots=slots, gate_report=report)
