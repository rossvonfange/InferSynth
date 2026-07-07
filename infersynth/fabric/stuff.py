"""Stuffing emission (docs/FABRIC.md, deliverable 3).

Copies the fabric board file and, by **text surgery** on the ``.kicad_pcb``
(never sexpdata round-trips — house rule):

* for every DNP'd site ref, adds the ``dnp`` + ``exclude_from_bom`` flags to
  the footprint's ``(attr ...)`` line;
* for every stuffed value-parametric ref, rewrites the footprint's ``Value``
  property text.

Every byte outside a targeted footprint block is preserved exactly — the
surgery splices only the blocks whose reference is in the DNP set or the
value-stuffing table. Then emits ``STUFFING.md`` (fit table, utilization %,
DNP list, value-stuffing table) and a stuffing BOM CSV, reusing
:mod:`infersynth.bind.bom`'s value types and renderer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from infersynth.bind.bom import Bom, BomLine, bom_to_csv
from infersynth.catalog import Catalog
from infersynth.fabric.fit import FitResult
from infersynth.fabric.loader import Fabric

__all__ = ["StuffResult", "stuff", "apply_pcb_surgery"]

_FOOTPRINT_TOKEN = '\t(footprint "'
_REF_RE = re.compile(r'\(property "Reference" "([^"]*)"')
_VALUE_RE = re.compile(r'(\(property "Value" ")[^"]*(")')
_ATTR_RE = re.compile(r"\(attr ([^)]*)\)")
#: DNP flags, in KiCad's canonical footprint-attr order.
_DNP_FLAGS = ("exclude_from_bom", "dnp")


@dataclass(frozen=True)
class StuffResult:
    """Paths + summary of an emitted stuffing variant."""

    board_path: Path
    report_path: Path
    bom_path: Path
    dnp_refs: tuple[str, ...]
    stuffed_refs: tuple[str, ...]
    bom: Bom


def _format_value(value: float) -> str:
    """Render a bound numeric value for a KiCad ``Value`` field / BOM line.

    Integral values print without a trailing ``.0`` (``3000.0`` -> ``3000``);
    everything else uses ``%g`` (compact, deterministic).
    """
    if value == int(value):
        return str(int(value))
    return f"{value:g}"


def _match_paren(text: str, start: int) -> int:
    """Return the index just past the ``)`` matching the ``(`` at *start*.

    Respects double-quoted strings (with backslash escaping) so parens inside a
    quoted property value never miscount.
    """
    depth = 0
    in_str = False
    esc = False
    for j in range(start, len(text)):
        c = text[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return j + 1
    raise ValueError("unbalanced parentheses in .kicad_pcb footprint block")


def _iter_footprint_blocks(text: str):
    """Yield ``(start, end, block_text)`` for each top-level footprint.

    Top-level footprints are one-tab-indented (``\\t(footprint "``); the search
    anchors on that token so a stray ``(footprint`` inside a quoted string can
    never be mistaken for a block start.
    """
    i = 0
    while True:
        tok = text.find(_FOOTPRINT_TOKEN, i)
        if tok == -1:
            return
        start = tok + 1  # skip the leading tab, land on '('
        end = _match_paren(text, start)
        yield start, end, text[start:end]
        i = end


def _set_dnp(block: str) -> str:
    """Add ``exclude_from_bom`` + ``dnp`` to the block's ``(attr ...)`` line.

    Existing tokens/order are preserved; missing flags are appended in KiCad's
    canonical order. When the footprint has no ``(attr ...)`` line at all, one
    is inserted immediately after the footprint's ``(at ...)`` placement line.
    """
    m = _ATTR_RE.search(block)
    if m:
        tokens = m.group(1).split()
        for flag in _DNP_FLAGS:
            if flag not in tokens:
                tokens.append(flag)
        return block[: m.start()] + f"(attr {' '.join(tokens)})" + block[m.end() :]
    # fallback: synthesize an attr line after the (at ...) placement line.
    at_m = re.search(r"\n(\t+)\(at [^\n]*\)\n", block)
    if at_m:
        indent = at_m.group(1)
        insert = f"{indent}(attr {' '.join(_DNP_FLAGS)})\n"
        return block[: at_m.end()] + insert + block[at_m.end() :]
    return block  # pragma: no cover - every KiCad footprint has an (at ...) line


def _set_value(block: str, value: float) -> str:
    """Rewrite the footprint's ``(property "Value" "...")`` text (first only)."""
    rendered = _format_value(value)
    return _VALUE_RE.sub(lambda mm: mm.group(1) + rendered + mm.group(2), block, count=1)


def apply_pcb_surgery(
    text: str, dnp_refs: set[str], value_by_ref: dict[str, float]
) -> str:
    """Return *text* with DNP attrs set and stuffed values rewritten.

    Only footprint blocks whose reference is in *dnp_refs* or *value_by_ref*
    are touched; every other byte is preserved exactly (byte-identical).
    """
    edits: list[tuple[int, int, str]] = []
    for start, end, block in _iter_footprint_blocks(text):
        ref_m = _REF_RE.search(block)
        if ref_m is None:
            continue
        ref = ref_m.group(1)
        new_block = block
        if ref in dnp_refs:
            new_block = _set_dnp(new_block)
        if ref in value_by_ref:
            new_block = _set_value(new_block, value_by_ref[ref])
        if new_block != block:
            edits.append((start, end, new_block))
    if not edits:
        return text
    out: list[str] = []
    cur = 0
    for start, end, nb in edits:
        out.append(text[cur:start])
        out.append(nb)
        cur = end
    out.append(text[cur:])
    return "".join(out)


def _build_bom(fabric: Fabric, fit_result: FitResult, catalog: Catalog) -> Bom:
    """Roll up the stuffing BOM from the stuffed sites' catalog candidates.

    Reuses :class:`infersynth.bind.bom.BomLine` / :class:`~infersynth.bind.bom.Bom`:
    each stuffed site's cell candidates give MPN / manufacturer / footprint per
    fragment ref; the value is the site's resolved bound value. DNP'd sites are,
    by definition, absent from the BOM. Grouping key is ``(mpn, value, footprint)``.
    """
    groups: dict[tuple[str, str, str], dict] = {}
    unbound: list[str] = []
    for site in fit_result.stuffed_sites:
        cell = catalog.get(site.cell_key)
        candidates = (cell.selection or {}).get("candidates") or []
        board_to_cand: dict[str, dict] = {}
        for cand in candidates:
            for frag in cand.get("maps", {}):
                board = site.ref_map.get(frag)
                if board is not None:
                    board_to_cand[board] = cand
        for board_ref in fabric.site_by_id[site.site_id].refs:
            cand = board_to_cand.get(board_ref)
            if cand is None:
                unbound.append(board_ref)
                continue
            value = (
                _format_value(site.bound_values[board_ref])
                if board_ref in site.bound_values
                else ""
            )
            mpn = str(cand.get("mpn", ""))
            footprint = str(cand.get("footprint", ""))
            manufacturer = str(cand.get("manufacturer", ""))
            key = (mpn, value, footprint)
            entry = groups.setdefault(key, {"refs": [], "manufacturer": manufacturer})
            entry["refs"].append(board_ref)
    lines = tuple(
        BomLine(
            refs=tuple(sorted(entry["refs"], key=_ref_sort_key)),
            value=value,
            mpn=mpn,
            manufacturer=entry["manufacturer"],
            footprint=footprint,
        )
        for (mpn, value, footprint), entry in sorted(groups.items())
    )
    return Bom(lines=lines, unbound=tuple(sorted(unbound, key=_ref_sort_key)))


def _ref_sort_key(ref: str):
    m = re.match(r"^([A-Za-z_#]+)(\d+)$", ref)
    if m:
        return (m.group(1), int(m.group(2)), "")
    return (ref, 0, ref)


def _report_md(fabric: Fabric, fit_result: FitResult) -> str:
    lines: list[str] = [
        f"# Stuffing report — fabric `{fabric.name}`",
        "",
        f"- board: `{fabric.board_path.name}`",
        f"- utilization: **{fit_result.utilization:.0%}** "
        f"({len(fit_result.stuffed_sites)}/{fit_result.total_sites} sites populated)",
        f"- DNP sites: {len(fit_result.dnp_sites)} "
        f"({len(fit_result.dnp_refs)} footprint(s))",
        f"- fits: {'yes' if fit_result.fits else 'NO — see diagnostics'}",
        "",
        "## Fit table",
        "",
        "| requirement | site | cell |",
        "|---|---|---|",
    ]
    for s in fit_result.stuffed_sites:
        lines.append(f"| {s.requirement_id} | {s.site_id} | `{s.cell_key}` |")
    if not fit_result.stuffed_sites:
        lines.append("| _(none)_ | | |")

    lines += ["", "## DNP set (unstuffed sites, per tie_off policy)", ""]
    if fit_result.dnp_sites:
        lines += ["| site | policy | refs | note |", "|---|---|---|---|"]
        for sid in fit_result.dnp_sites:
            site = fabric.site_by_id[sid]
            tie = fabric.tie_off.get(sid)
            policy = tie.policy if tie else "dnp-all (default)"
            note = tie.note if tie else ""
            lines.append(f"| {sid} | {policy} | {' '.join(site.refs)} | {note} |")
    else:
        lines.append("_(no DNP sites — every site populated)_")

    lines += ["", "## Value stuffing", ""]
    if fit_result.value_stuffing:
        lines += ["| site | ref | value | source |", "|---|---|---|---|"]
        for sv in fit_result.value_stuffing:
            lines.append(
                f"| {sv.site_id} | {sv.board_ref} | {_format_value(sv.value)} "
                f"| binding `{sv.fragment_ref}` |"
            )
    else:
        lines.append("_(no value-parametric stuffing)_")

    if fit_result.unfittable:
        lines += ["", "## Unfittable (does-not-fit — a first-class diagnostic)", ""]
        for req_id, cell_key in fit_result.unfittable:
            lines.append(f"- requirement `{req_id}` won `{cell_key}` — no free site")

    if fit_result.diagnostics:
        lines += ["", "## Diagnostics", ""]
        lines += [f"- {d}" for d in fit_result.diagnostics]

    return "\n".join(lines) + "\n"


def stuff(
    fabric: Fabric,
    fit_result: FitResult,
    out_dir: str | Path,
    catalog: Catalog,
) -> StuffResult:
    """Emit the stuffed variant into *out_dir* (deliverable 3).

    Copies the fabric board with DNP attrs + stuffed values applied by text
    surgery, and writes ``STUFFING.md`` and ``stuffing_bom.csv``.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    text = fabric.board_path.read_text(encoding="utf-8")
    dnp_refs = set(fit_result.dnp_refs)
    value_by_ref = {sv.board_ref: sv.value for sv in fit_result.value_stuffing}
    new_text = apply_pcb_surgery(text, dnp_refs, value_by_ref)

    board_path = out_dir / fabric.board_path.name
    board_path.write_text(new_text, encoding="utf-8")

    report_path = out_dir / "STUFFING.md"
    report_path.write_text(_report_md(fabric, fit_result), encoding="utf-8")

    bom = _build_bom(fabric, fit_result, catalog)
    bom_path = out_dir / "stuffing_bom.csv"
    bom_path.write_text(bom_to_csv(bom), encoding="utf-8")

    return StuffResult(
        board_path=board_path,
        report_path=report_path,
        bom_path=bom_path,
        dnp_refs=tuple(sorted(dnp_refs, key=_ref_sort_key)),
        stuffed_refs=tuple(
            sorted(
                (
                    r
                    for s in fit_result.stuffed_sites
                    for r in fabric.site_by_id[s.site_id].refs
                ),
                key=_ref_sort_key,
            )
        ),
        bom=bom,
    )
