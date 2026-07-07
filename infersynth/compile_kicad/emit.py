"""Direct emitter — the InferSynth *writer* (BUILD_PLAN WP3, UX.md write path).

Instantiation is deterministic **byte-preserving text surgery** on a KiCad
schematic format we authored (the cell fragments). The steps, per the write
path in UX.md:

1. copy ``fragment.kicad_sch`` verbatim,
2. substitute the ``${IS.<ref>}`` value slots from :func:`bind_cell`,
3. re-key every ``(uuid ...)`` to a fresh uuid4 (structure/formatting otherwise
   untouched),
4. re-point the copied symbol ``(instances ...)`` at the target design and the
   new sheet path,
5. write the copy as ``<design>/<instname>.kicad_sch``,
6. splice a ``(sheet ...)`` block into the parent — stamping ``IS.Cell`` /
   ``IS.Param.*`` provenance properties (DESIGN.md section 5 storage model) —
   and add the matching ``(sheet_instances)`` page.

Hard rule (documented failure mode of this project): **never** parse-and-
re-serialize via ``sexpdata`` or any s-expression library — that destroys the
formatting. Every mutation here is a regex/index splice on the original text.

Writer/oracle independence (UX.md): this module imports nothing from
:mod:`infersynth.gates`; verification shares no code with emission so an
emission bug cannot hide itself in the checker.

Public API (consumed by Stage-2 WP4):

* :func:`instantiate` — copy a cell fragment into a design, return the child path
* :func:`new_design` — create a blank root ``.kicad_sch`` + minimal ``.kicad_pro``
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from infersynth.bind import bind_cell
from infersynth.compile_kicad.wiring_text import PIN_STRIDE

if TYPE_CHECKING:
    from infersynth.catalog.loader import CellPackage

__all__ = ["format_value", "instantiate", "new_design"]

# KiCad 10 file-format markers used by the blank-root template. Kept in sync
# with the committed fragments (version 20260101, eeschema 10.0).
_SCH_VERSION = "20260101"
_GENERATOR = "eeschema"
_GENERATOR_VERSION = "10.0"

# A uuid4 in canonical 8-4-4-4-12 hex form (KiCad writes them lowercase).
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

# Placement grid: KiCad's default 1.27 mm (50 mil). Every coordinate the emitter
# writes is an integer multiple of this so the sheet lands on-grid.
_GRID = 1.27
_SHEET_ORIGIN_X = 25.4  # 20 * 1.27
_SHEET_ORIGIN_Y = 25.4
_SHEET_W = 25.4  # floor width/height — grown per-instance, never shrunk below this
_SHEET_H = 25.4

# Grid layout (round-2 cosmetics): sheets wrap into rows instead of running off
# the A4 page in one endless line. ~250 mm is the usable width of an A4 sheet
# (297 mm landscape minus title-block/margins); row gap mirrors the old
# left-to-right stride's 12.7 mm (10 * 1.27) gap between boxes.
_PAGE_USABLE_W = 250.0
_ROW_GAP_X = 12.7
_ROW_GAP_Y = 12.7

# Rough per-character width for sizing a sheet box to its Sheetname/Sheetfile
# label (KiCad default 1.27 mm font): one grid step per character is generous
# enough that the label never overruns the box, and stays grid-aligned for free.
_CHAR_W = _GRID
_LABEL_PAD_CHARS = 2

_SHEET_RE = re.compile(r"\t\(sheet\n(.*?)\n\t\)\n", re.DOTALL)
_BOX_AT_RE = re.compile(r"\(at ([-\d.]+) ([-\d.]+)\)")
_BOX_SIZE_RE = re.compile(r"\(size ([-\d.]+) ([-\d.]+)\)")

# Refdes text surgery (round-2 cosmetics): a copied fragment's refs are
# rewritten per-instance so a multi-sheet design doesn't stack the same
# U1/R1/C1 across every child (kicad-cli's design-wide annotation check).
# #FLG / #PWR (and any other KiCad virtual ref) are left untouched.
_REF_PROP_RE = re.compile(r'(\(property "Reference" ")([^"]*)(")')
_REF_INST_RE = re.compile(r'(\(reference ")([^"]*)(")')
_REF_SPLIT_RE = re.compile(r"^([A-Za-z_]+)(\d+)$")
# Marker of the first per-instance component symbol: "(lib_id" appears ONLY
# in instance symbols (lib_symbols entries open as (symbol "Lib:Name" ...)),
# and every instance's Reference property / (reference ...) comes after its
# own (lib_id ...). Splitting at the first occurrence therefore protects the
# whole (lib_symbols ...) block — for both the pretty multi-line and the
# compact one-line fragment formats.
_FIRST_INSTANCE_SYMBOL = "(lib_id"


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _grid_snap(value: float) -> float:
    """Round *value* to the nearest on-grid coordinate."""
    return round(round(value / _GRID) * _GRID, 4)


def _sheet_size(instname: str, nports: int) -> tuple[float, float]:
    """Sheet box (w, h) sized for *instname*'s labels and *nports* pins.

    Width fits the longer of the two auto-placed labels (``Sheetfile`` is
    always the longer one: ``instname`` + ``.kicad_sch``). Height is sized for
    the FULL port count up front — before any wiring-stage pin splicing — so
    the post-wiring enlargement in :mod:`wiring`/:mod:`harness` is a no-op and
    a sheet never grows into the row below it.
    """
    label = f"{instname}.kicad_sch"
    w = max(_SHEET_W, _grid_snap((len(label) + _LABEL_PAD_CHARS) * _CHAR_W))
    h = max(_SHEET_H, _grid_snap((nports + 1) * PIN_STRIDE))
    return w, h


def _existing_sheet_boxes(parent_text: str) -> list[tuple[float, float, float, float]]:
    """Every already-placed child sheet's ``(x, y, w, h)`` box, in file order."""
    boxes = []
    for m in _SHEET_RE.finditer(parent_text):
        body = m.group(1)
        am = _BOX_AT_RE.search(body)
        sm = _BOX_SIZE_RE.search(body)
        if am and sm:
            boxes.append(
                (float(am.group(1)), float(am.group(2)), float(sm.group(1)), float(sm.group(2)))
            )
    return boxes


def _next_sheet_position(parent_text: str, w: float, h: float) -> tuple[float, float]:
    """Next on-grid ``(x, y)`` for a *w* x *h* sheet: wrap into a new row when

    it would not fit within ``_PAGE_USABLE_W`` of the current row. Row height
    is the tallest sheet placed in that row so far (sheets vary in height
    after port-count sizing).
    """
    boxes = _existing_sheet_boxes(parent_text)
    if not boxes:
        return _SHEET_ORIGIN_X, _SHEET_ORIGIN_Y

    max_y = max(b[1] for b in boxes)
    row = [b for b in boxes if b[1] == max_y]
    row_right = max(b[0] + b[2] for b in row)
    row_h = max(b[3] for b in row)

    cand_x = _grid_snap(row_right + _ROW_GAP_X)
    if cand_x + w <= _SHEET_ORIGIN_X + _PAGE_USABLE_W:
        return cand_x, max_y
    new_y = _grid_snap(max_y + row_h + _ROW_GAP_Y)
    return _SHEET_ORIGIN_X, new_y


def _bump_ref(ref: str, offset: int) -> str:
    """Offset a refdes's numeric suffix; virtual (``#``-prefixed) refs pass through."""
    if ref.startswith("#"):
        return ref
    m = _REF_SPLIT_RE.match(ref)
    if not m:
        return ref  # not a plain PREFIX+digits ref — leave it alone
    prefix, digits = m.groups()
    return f"{prefix}{int(digits) + offset}"


def _renumber_refs(text: str, offset: int) -> str:
    """Rewrite every non-virtual refdes in the copied fragment's instance

    symbols by *offset* — both the ``(property "Reference" ...)`` field and
    the matching ``(reference ...)`` inside its ``(instances ...)`` block.
    Never touches ``(lib_symbols ...)`` (everything before the first
    per-instance component symbol).
    """
    idx = text.find(_FIRST_INSTANCE_SYMBOL)
    if idx == -1:
        return text  # fragment has no discrete component symbols
    head, tail = text[:idx], text[idx:]
    tail = _REF_PROP_RE.sub(lambda m: m.group(1) + _bump_ref(m.group(2), offset) + m.group(3), tail)
    tail = _REF_INST_RE.sub(lambda m: m.group(1) + _bump_ref(m.group(2), offset) + m.group(3), tail)
    return head + tail


def format_value(ohms: float) -> str:
    """Render a resistance in ohms as a compact KiCad value string.

    ``1000.0 -> "1k"``, ``99000 -> "99k"``, ``470.0 -> "470"``,
    ``4700 -> "4.7k"``, ``1_000_000 -> "1M"``, ``2.2e6 -> "2.2M"``.
    Sub-kilohm values are emitted plain. Trailing zeros are trimmed.
    """
    v = float(ohms)
    neg = v < 0
    v = abs(v)
    if v >= 1e6:
        mantissa, suffix = v / 1e6, "M"
    elif v >= 1e3:
        mantissa, suffix = v / 1e3, "k"
    else:
        mantissa, suffix = v, ""
    # %g trims trailing zeros; 10 sig figs is well beyond E-series precision.
    text = f"{mantissa:.10g}"
    return f"{'-' if neg else ''}{text}{suffix}"


def _param_str(value: object) -> str:
    """Render a stamped IS.Param.* value; integral floats lose the ``.0``."""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _rekey_uuids(text: str) -> tuple[str, dict[str, str]]:
    """Replace every uuid in *text* with a fresh uuid4 (consistent per old id).

    Returns the rewritten text and the old->new mapping. Because the same old
    uuid maps to a single new value everywhere it occurs, internal references
    (e.g. a symbol ``(instances ... (path "/<root>" ...))`` pointing at the
    file's own root uuid) stay self-consistent.
    """
    mapping: dict[str, str] = {}

    def repl(m: re.Match[str]) -> str:
        old = m.group(0)
        new = mapping.get(old)
        if new is None:
            new = _new_uuid()
            mapping[old] = new
        return new

    return _UUID_RE.sub(repl, text), mapping


def _root_uuid(sch_text: str) -> str:
    """Return the root ``(uuid ...)`` of a schematic (first uuid in the file)."""
    m = _UUID_RE.search(sch_text)
    if m is None:  # pragma: no cover — every schematic has a root uuid
        raise ValueError("schematic has no root (uuid ...)")
    return m.group(0)


def _fragment_root_uuid(fragment_text: str) -> str:
    return _root_uuid(fragment_text)


def _property_block(name: str, value: str, x: float, y: float, hide: bool) -> str:
    """A KiCad ``(property ...)`` s-expression, tab-indented for a sheet block."""
    esc = value.replace("\\", "\\\\").replace('"', '\\"')
    hide_line = "\n\t\t\t\t(hide yes)" if hide else ""
    return (
        f'\t\t(property "{name}" "{esc}"\n'
        f"\t\t\t(at {x:g} {y:g} 0)\n"
        f"\t\t\t(show_name no)\n"
        f"\t\t\t(do_not_autoplace no)\n"
        f"\t\t\t(effects\n"
        f"\t\t\t\t(font\n"
        f"\t\t\t\t\t(size 1.27 1.27)\n"
        f"\t\t\t\t){hide_line}\n"
        f"\t\t\t)\n"
        f"\t\t)\n"
    )


def _resolved_params(cell: CellPackage, params: dict) -> dict[str, object]:
    """Idiom params merged with declared defaults — the provenance to stamp.

    ``bind_cell`` has already validated *params*; here we only surface the
    effective value of each declared idiom param (caller value, else default),
    skipping params with neither.
    """
    resolved: dict[str, object] = {}
    for pname, schema in cell.idiom_params.items():
        if pname in params:
            resolved[pname] = params[pname]
        elif schema.get("default") is not None:
            resolved[pname] = schema["default"]
    return resolved


def _sheet_block(
    *,
    instname: str,
    cell: CellPackage,
    resolved: dict[str, object],
    x: float,
    y: float,
    w: float,
    h: float,
    page: int,
    sheet_uuid: str,
    parent_root: str,
    design: str,
) -> str:
    """Build the ``(sheet ...)`` block spliced into the parent schematic.

    ``(x, y)`` / ``(w, h)`` are the sheet's on-grid position and size (grid
    wrapping + label/port sizing are the caller's job — see
    :func:`_next_sheet_position` / :func:`_sheet_size`); ``page`` is its
    ordinal in ``(sheet_instances)``. Carries Sheetname/Sheetfile plus the
    hidden ``IS.Cell`` / ``IS.Version`` / ``IS.Param.*`` provenance stamps.
    """
    name_y = y - 0.7116  # KiCad's Sheetname anchor: just above the box, bottom-justified
    file_y = y + h + 0.5846  # Sheetfile anchor: just below the box, top-justified

    props = [
        f'\t\t(property "Sheetname" "{instname}"\n'
        f"\t\t\t(at {x:g} {name_y:g} 0)\n"
        f"\t\t\t(show_name no)\n"
        f"\t\t\t(do_not_autoplace no)\n"
        f"\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n"
        f"\t\t\t\t(justify left bottom)\n\t\t\t)\n\t\t)\n",
        f'\t\t(property "Sheetfile" "{instname}.kicad_sch"\n'
        f"\t\t\t(at {x:g} {file_y:g} 0)\n"
        f"\t\t\t(show_name no)\n"
        f"\t\t\t(do_not_autoplace no)\n"
        f"\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n"
        f"\t\t\t\t(justify left top)\n\t\t\t)\n\t\t)\n",
        _property_block("IS.Cell", cell.key, x, y, hide=True),
        _property_block("IS.Version", cell.version, x, y, hide=True),
    ]
    for pname in sorted(resolved):
        props.append(
            _property_block(f"IS.Param.{pname}", _param_str(resolved[pname]), x, y, hide=True)
        )

    return (
        "\t(sheet\n"
        f"\t\t(at {x:g} {y:g})\n"
        f"\t\t(size {w:g} {h:g})\n"
        "\t\t(exclude_from_sim no)\n"
        "\t\t(in_bom yes)\n"
        "\t\t(on_board yes)\n"
        "\t\t(dnp no)\n"
        "\t\t(fields_autoplaced yes)\n"
        "\t\t(stroke\n\t\t\t(width 0.1524)\n\t\t\t(type solid)\n\t\t)\n"
        "\t\t(fill\n\t\t\t(color 0 0 0 0)\n\t\t)\n"
        f'\t\t(uuid "{sheet_uuid}")\n'
        + "".join(props)
        + "\t\t(instances\n"
        f'\t\t\t(project "{design}"\n'
        f'\t\t\t\t(path "/{parent_root}"\n'
        f'\t\t\t\t\t(page "{page}")\n'
        "\t\t\t\t)\n"
        "\t\t\t)\n"
        "\t\t)\n"
        "\t)\n"
    )


def new_design(design_dir: Path, name: str) -> Path:
    """Create a blank design: root ``<name>.kicad_sch`` + minimal ``<name>.kicad_pro``.

    Returns the path to the root schematic. The root is an empty hierarchical
    sheet (page 1); :func:`instantiate` appends child sheets to it.
    """
    design_dir = Path(design_dir)
    design_dir.mkdir(parents=True, exist_ok=True)
    root_uuid = _new_uuid()
    root = (
        "(kicad_sch\n"
        f"\t(version {_SCH_VERSION})\n"
        f'\t(generator "{_GENERATOR}")\n'
        f'\t(generator_version "{_GENERATOR_VERSION}")\n'
        f'\t(uuid "{root_uuid}")\n'
        '\t(paper "A4")\n'
        "\t(lib_symbols)\n"
        "\t(sheet_instances\n"
        '\t\t(path "/"\n'
        '\t\t\t(page "1")\n'
        "\t\t)\n"
        "\t)\n"
        "\t(embedded_fonts no)\n"
        ")\n"
    )
    sch_path = design_dir / f"{name}.kicad_sch"
    sch_path.write_text(root)

    pro = {
        "board": {"design_settings": {}, "layer_presets": [], "viewports": []},
        "boards": [],
        "cvpcb": {"equivalence_files": []},
        "libraries": {"pinned_footprint_libs": [], "pinned_symbol_libs": []},
        "meta": {"filename": f"{name}.kicad_pro", "version": 3},
        "net_settings": {
            "classes": [],
            "meta": {"version": 4},
            "net_colors": None,
            "netclass_assignments": None,
            "netclass_patterns": [],
        },
        "pcbnew": {"last_paths": {}, "page_layout_descr_file": ""},
        "schematic": {
            "legacy_lib_dir": "",
            "legacy_lib_list": [],
        },
        "sheets": [[root_uuid, "Root"]],
        "text_variables": {},
    }
    (design_dir / f"{name}.kicad_pro").write_text(json.dumps(pro, indent=2) + "\n")
    return sch_path


def instantiate(
    cell: CellPackage,
    params: dict,
    instname: str,
    design_dir: Path,
    parent_sch: Path,
    *,
    renumber_refs: bool = True,
) -> Path:
    """Instantiate *cell* (bound with *params*) as ``<instname>`` under *parent_sch*.

    Copies the cell fragment, substitutes its ``${IS.*}`` slots, re-keys all
    uuids, re-points the symbol instances at this design, writes
    ``<design_dir>/<instname>.kicad_sch``, and splices a provenance-stamped
    ``(sheet ...)`` reference + ``(sheet_instances)`` page into *parent_sch*.
    Returns the child schematic path.

    ``renumber_refs`` (default ``True``): offset the copied fragment's refdes
    by ``instance_index * 100`` (instance 1 -> +100, instance 2 -> +200, ...;
    ``#``-prefixed virtual refs untouched) so a multi-sheet design never
    stacks the same U1/R1/C1 across every child. Callers that need the
    fragment's own bare refs preserved (the WP4 cell-CI harness, checked
    against a golden netlist keyed on those bare refs) pass ``False``.
    """
    design_dir = Path(design_dir)
    parent_sch = Path(parent_sch)

    # 1. bind (validates params, yields the ${IS.<ref>} substitution values).
    values = bind_cell(cell, params)
    resolved = _resolved_params(cell, params)

    fragment_path = cell.path / "fragment.kicad_sch"
    text = fragment_path.read_text()

    # 2. substitute ${IS.<ref>} value slots. Every declared binding must have a
    #    matching slot; any leftover ${IS.*} is an authoring/bind mismatch.
    for ref, val in values.items():
        text = text.replace(f"${{IS.{ref}}}", format_value(val))
    leftover = re.findall(r"\$\{IS\.[^}]+\}", text)
    if leftover:
        raise ValueError(
            f"unsubstituted value slot(s) {sorted(set(leftover))} in "
            f"{fragment_path}: no binding produced them"
        )

    # 3. fresh uuids for the whole copy (consistent per old id).
    text, _mapping = _rekey_uuids(text)
    child_root = _root_uuid(text)  # the re-keyed root uuid of this child file

    # 4. re-point the copied symbol (instances ...) at the target design + sheet.
    #    In the fragment they read (project "fragment" (path "/<child_root>" ...));
    #    under the parent the KIID path is /<parent_root>/<sheet_uuid>.
    design = parent_sch.stem
    parent_text = parent_sch.read_text()
    parent_root = _root_uuid(parent_text)
    sheet_uuid = _new_uuid()

    text = re.sub(r'\(project "[^"]*"', f'(project "{design}"', text)
    text = text.replace(
        f'(path "/{child_root}"',
        f'(path "/{parent_root}/{sheet_uuid}"',
    )

    # 4b. per-instance refdes namespace: instance k (1-based, in
    #     instantiation order) offsets its refs by k*100.
    index = parent_text.count("\n\t(sheet\n")  # existing child sheets (root is not one)
    if renumber_refs:
        text = _renumber_refs(text, (index + 1) * 100)

    # 5. write the child schematic.
    design_dir.mkdir(parents=True, exist_ok=True)
    child_path = design_dir / f"{instname}.kicad_sch"
    child_path.write_text(text)

    # 6. splice the (sheet ...) block + page into the parent. Grid layout:
    #    wrap into a new row instead of running one endless row off the page;
    #    size the box up front for the instname label and the FULL port count
    #    (before wiring's pin splicing) so later enlargement is a no-op.
    page = index + 2  # root is page 1; child sheets are pages 2, 3, ...
    w, h = _sheet_size(instname, len(cell.ports))
    x, y = _next_sheet_position(parent_text, w, h)
    sheet_block = _sheet_block(
        instname=instname,
        cell=cell,
        resolved=resolved,
        x=x,
        y=y,
        w=w,
        h=h,
        page=page,
        sheet_uuid=sheet_uuid,
        parent_root=parent_root,
        design=design,
    )

    marker = "\t(sheet_instances\n"
    idx = parent_text.index(marker)
    parent_text = parent_text[:idx] + sheet_block + parent_text[idx:]

    # add the matching (sheet_instances) page (KIID path is just /<sheet_uuid>).
    # Insert before the single-tab ")" that closes (sheet_instances); "\n\t)\n"
    # cannot match the two-tab "\n\t\t)\n" that closes an inner (path ...).
    page_entry = f'\t\t(path "/{sheet_uuid}"\n\t\t\t(page "{page}")\n\t\t)\n'
    si_start = parent_text.index(marker)
    si_close = parent_text.index("\n\t)\n", si_start) + 1  # position of the "\t)\n"
    parent_text = parent_text[:si_close] + page_entry + parent_text[si_close:]

    parent_sch.write_text(parent_text)
    return child_path
