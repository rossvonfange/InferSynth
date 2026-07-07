"""Generate examples/fabric-demo/fabric.kicad_pcb via pcbnew scripting.

Two opamp-gain sites (SOT-23-5 + 2x R_0603) + one decoupling site (C_0603),
using the real KiCad footprint libraries. No routing: v0 fit/DNP semantics
don't care (a REAL fabric would be fully routed here — the routing amortizes
across every design stuffed onto it; see docs/FABRIC.md). Run with the
KiCAD-MCP-Server venv python (it has pcbnew):

    /home/cycix/Desktop/fai-tuner/KiCAD-MCP-Server/venv/bin/python \
        examples/fabric-demo/gen_board.py examples/fabric-demo/fabric.kicad_pcb
"""
import sys
import pcbnew

FP = "/usr/share/kicad/footprints"
OUT = sys.argv[1] if len(sys.argv) > 1 else "fabric.kicad_pcb"

board = pcbnew.NewBoard(OUT)


def add(lib, name, ref, value, x, y):
    fp = pcbnew.FootprintLoad(f"{FP}/{lib}.pretty", name)
    fp.SetReference(ref)
    fp.SetValue(value)
    fp.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)))
    board.Add(fp)


# amp0 site
add("Package_TO_SOT_SMD", "SOT-23-5", "U101", "OPA340", 40, 40)
add("Resistor_SMD", "R_0603_1608Metric", "R101", "10k", 45, 40)
add("Resistor_SMD", "R_0603_1608Metric", "R102", "1k", 45, 43)
# amp1 site
add("Package_TO_SOT_SMD", "SOT-23-5", "U201", "OPA340", 40, 55)
add("Resistor_SMD", "R_0603_1608Metric", "R201", "10k", 45, 55)
add("Resistor_SMD", "R_0603_1608Metric", "R202", "1k", 45, 58)
# decoupling site
add("Capacitor_SMD", "C_0603_1608Metric", "C101", "100n", 40, 70)

board.Save(OUT)
print(f"wrote {OUT}: {board.GetFootprints().GetCount() if hasattr(board.GetFootprints(),'GetCount') else 'ok'}")
