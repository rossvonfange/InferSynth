"""pcbnew subprocess worker for placed-board emission (docs/FLOORPLAN.md).

Run under a python that has ``pcbnew`` (the KiCad-bundled interpreter), NOT the
project venv — :mod:`infersynth.floorplan.board` invokes it as a subprocess and
this file imports nothing from ``infersynth`` so it stands alone (the gen_board.py
precedent). It only PLACES a pre-computed floorplan; it makes no layout decision,
so determinism is owned by the pure planner upstream.

    <kicad-python> _pcbnew_emit.py payload.json

``payload.json``::

    {"out": "board.kicad_pcb", "fp_dir": "/usr/share/kicad/footprints",
     "board": {"w": <mm>, "h": <mm>},
     "footprints": [{"ref","value","libid","x","y"}, ...],
     "groups": {"<instance>": ["<ref>", ...], ...}}

Emits: each footprint loaded from stock libs and placed at (x, y) mm; one
``PCB_GROUP`` per instance (the native API — available in this pcbnew; no text
surgery needed); a rectangular board outline on Edge.Cuts. No nets/ratsnest
(v0 is placement structure only). Prints a one-line JSON stats object on success.
"""

import json
import sys

import pcbnew


def _load_footprint(fp_dir, libid):
    if ":" in libid:
        lib, name = libid.split(":", 1)
    else:
        lib, name = "", libid
    return pcbnew.FootprintLoad(f"{fp_dir}/{lib}.pretty", name)


def _rect_outline(board, w, h):
    """Draw a closed rectangle on Edge.Cuts from (0,0) to (w,h) mm."""
    corners = [(0, 0), (w, 0), (w, h), (0, h)]
    for i in range(4):
        x0, y0 = corners[i]
        x1, y1 = corners[(i + 1) % 4]
        seg = pcbnew.PCB_SHAPE(board)
        seg.SetShape(pcbnew.SHAPE_T_SEGMENT)
        seg.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(x0), pcbnew.FromMM(y0)))
        seg.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(x1), pcbnew.FromMM(y1)))
        seg.SetLayer(pcbnew.Edge_Cuts)
        seg.SetWidth(pcbnew.FromMM(0.15))
        board.Add(seg)


def main(payload_path):
    with open(payload_path) as fh:
        payload = json.load(fh)

    out = payload["out"]
    fp_dir = payload["fp_dir"]
    board = pcbnew.NewBoard(out)

    fp_by_ref = {}
    missing = []
    for entry in payload["footprints"]:
        fp = _load_footprint(fp_dir, entry["libid"])
        if fp is None:
            missing.append(entry["libid"])
            continue
        fp.SetReference(entry["ref"])
        fp.SetValue(str(entry["value"]))
        fp.SetPosition(
            pcbnew.VECTOR2I(pcbnew.FromMM(entry["x"]), pcbnew.FromMM(entry["y"]))
        )
        board.Add(fp)
        fp_by_ref[entry["ref"]] = fp

    if missing:
        sys.stderr.write(f"floorplan emit: could not load footprint(s): {missing}\n")
        return 1

    ngroups = 0
    for instance, refs in payload["groups"].items():
        members = [fp_by_ref[r] for r in refs if r in fp_by_ref]
        if not members:
            continue
        group = pcbnew.PCB_GROUP(board)
        group.SetName(instance)
        for fp in members:
            group.AddItem(fp)
        board.Add(group)
        ngroups += 1

    b = payload["board"]
    _rect_outline(board, b["w"], b["h"])

    board.Save(out)
    print(json.dumps({"footprints": len(fp_by_ref), "groups": ngroups}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
