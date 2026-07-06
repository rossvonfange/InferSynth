"""End-to-end tests for the sim-gate v0: both op-amp cells, structural skip,
determinism, and the no-random/no-time repeatability guard (SELECTION.md §8)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import infersynth.sim as sim_pkg
from infersynth.catalog.loader import load_cell
from infersynth.catalog.taxonomy import load_taxonomy
from infersynth.gates import simulation_gate
from infersynth.gates.run import run_cell_gates
from infersynth.gates.runner import GateStatus
from infersynth.gates.simulation import run_cell_simulation, simulation_cell_gate

CATALOG = Path(__file__).resolve().parents[1] / "catalog" / "core"
NONINV = CATALOG / "opamp-gain-noninverting"
X4 = CATALOG / "opamp-gain-x4-noninverting"
DECOUPLING = CATALOG / "decoupling"
_TAXONOMY = load_taxonomy(CATALOG.parent / "taxonomy.yaml")


def _load_isolated(dst: Path):
    """Load a cell copied outside the catalog (taxonomy passed explicitly)."""
    return load_cell(dst, taxonomy=_TAXONOMY)


class TestBehavioralCellGate:
    def test_noninverting_passes(self):
        result = simulation_cell_gate(load_cell(NONINV))
        assert result.status == GateStatus.PASS
        assert any("amplitude_ratio" in d for d in result.diagnostics)
        assert any("overdrive/clipped_within" in d for d in result.diagnostics)

    def test_x4_passes_all_four_channels(self):
        result = simulation_cell_gate(load_cell(X4))
        assert result.status == GateStatus.PASS
        for k in (1, 2, 3, 4):
            assert any(f"vout{k}" in d for d in result.diagnostics)

    def test_structural_cell_skips_loudly(self):
        result = simulation_cell_gate(load_cell(DECOUPLING))
        assert result.status == GateStatus.SKIPPED
        assert "structural-only" in result.diagnostics[0]

    def test_run_cell_gates_includes_passing_simulation(self):
        report = run_cell_gates(NONINV)
        sims = [r for r in report.results if r.gate == "simulation"]
        assert len(sims) == 1
        assert sims[0].status == GateStatus.PASS

    def test_stub_gate_delegates_on_cell_context(self):
        # the context-based stub now runs the v0 tier when given a cell
        result = simulation_gate({"cell": load_cell(NONINV)})
        assert result.status == GateStatus.PASS

    def test_stub_gate_skips_without_cell(self):
        assert simulation_gate({}).status == GateStatus.SKIPPED


class TestDeterminism:
    def test_same_cell_twice_is_bit_identical(self):
        cell = load_cell(NONINV)
        assert run_cell_simulation(cell) == run_cell_simulation(cell)

    def test_x4_twice_is_bit_identical(self):
        cell = load_cell(X4)
        assert run_cell_simulation(cell) == run_cell_simulation(cell)


class TestRepeatabilityGuard:
    """sim/ must import neither random nor time (SELECTION.md §8)."""

    def test_no_random_or_time_imports(self):
        sim_dir = Path(sim_pkg.__file__).parent
        offenders: list[str] = []
        for py in sorted(sim_dir.rglob("*.py")):
            tree = ast.parse(py.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [(node.module or "").split(".")[0]]
                else:
                    continue
                for n in names:
                    if n in {"random", "time"}:
                        offenders.append(f"{py.name}: imports {n}")
        assert not offenders, offenders


class TestFailureModes:
    def test_port_mismatch_fails(self, tmp_path: Path):
        # copy the noninverting cell but break the behavior's ports
        import shutil

        dst = tmp_path / "cell"
        shutil.copytree(NONINV, dst)
        beh = dst / "model" / "behavior.py"
        text = beh.read_text().replace('("IN", "VCC", "VEE", "GND")', '("IN", "VCC", "VEE")')
        beh.write_text(text)
        result = simulation_cell_gate(_load_isolated(dst))
        assert result.status == GateStatus.FAIL
        assert any("do not match cell.yaml ports" in d for d in result.diagnostics)

    def test_missing_testbench_skips(self, tmp_path: Path):
        import shutil

        dst = tmp_path / "cell"
        shutil.copytree(NONINV, dst)
        (dst / "testbench" / "tb.py").unlink()
        result = simulation_cell_gate(_load_isolated(dst))
        assert result.status == GateStatus.SKIPPED
        assert "testbench/tb.py" in result.diagnostics[0]

    def test_bad_params_fail(self, tmp_path: Path):
        import shutil

        dst = tmp_path / "cell"
        shutil.copytree(NONINV, dst)
        tb = dst / "testbench" / "tb.py"
        # gain out of the declared [1.0, 1000.0] range -> binder rejects
        tb.write_text(tb.read_text().replace('PARAMS = {"gain": 4.0}', 'PARAMS = {"gain": 5000.0}'))
        result = simulation_cell_gate(_load_isolated(dst))
        assert result.status == GateStatus.FAIL
        assert any("binder" in d for d in result.diagnostics)


@pytest.mark.parametrize("cell_dir", [NONINV, X4])
def test_cell_gates_all_pass(cell_dir: Path):
    report = run_cell_gates(cell_dir)
    assert report.ok
    assert {r.gate for r in report.results} >= {"erc", "simulation"}
