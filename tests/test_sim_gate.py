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
UNITY = CATALOG / "unity-buffer"
INVERTING = CATALOG / "opamp-gain-inverting"
VREF = CATALOG / "vref-shunt"
CLAMP = CATALOG / "output-clamp"
SUMMING = CATALOG / "summing-offset-stage"
ADC_RC = CATALOG / "adc-driver-rc"
BJT_CS = CATALOG / "current-source-bjt"
PWR_COND = CATALOG / "power-input-conditioning"
SALLEN_KEY = CATALOG / "sallen-key-lowpass-2"
MFB = CATALOG / "mfb-lowpass-2"
LINREG = CATALOG / "linear-reg-fixed"
IN_AMP = CATALOG / "instrumentation-amp-3opamp"
BRIDGE = CATALOG / "bridge-interface"
RAIL_SPLIT = CATALOG / "rail-splitter-virtual-gnd"
INPUT_PROT = CATALOG / "input-protection-rfi"
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

    def test_unity_buffer_passes(self):
        result = simulation_cell_gate(load_cell(UNITY))
        assert result.status == GateStatus.PASS
        assert any("amplitude_ratio" in d for d in result.diagnostics)
        assert any("overdrive/clipped_within" in d for d in result.diagnostics)

    def test_inverting_passes_and_is_inverted(self):
        result = simulation_cell_gate(load_cell(INVERTING))
        assert result.status == GateStatus.PASS
        assert any("linear/inverted" in d for d in result.diagnostics)
        assert any("linear/amplitude_ratio" in d for d in result.diagnostics)

    def test_vref_shunt_passes(self):
        result = simulation_cell_gate(load_cell(VREF))
        assert result.status == GateStatus.PASS
        assert any("regulated/settles_to" in d for d in result.diagnostics)
        assert any("low_vin/settles_to" in d for d in result.diagnostics)

    def test_output_clamp_passes(self):
        result = simulation_cell_gate(load_cell(CLAMP))
        assert result.status == GateStatus.PASS
        assert any("linear/amplitude_ratio" in d for d in result.diagnostics)
        assert any("overdrive/clipped_within" in d for d in result.diagnostics)

    def test_summing_offset_stage_passes(self):
        result = simulation_cell_gate(load_cell(SUMMING))
        assert result.status == GateStatus.PASS
        assert any("linear/amplitude_ratio" in d for d in result.diagnostics)
        assert any("linear/inverted" in d for d in result.diagnostics)
        assert any("overdrive/clipped_within" in d for d in result.diagnostics)

    def test_adc_driver_rc_passes(self):
        result = simulation_cell_gate(load_cell(ADC_RC))
        assert result.status == GateStatus.PASS
        assert any("step_1v/settles_to" in d for d in result.diagnostics)
        assert any("steady_2v/settles_to" in d for d in result.diagnostics)

    def test_current_source_bjt_passes(self):
        result = simulation_cell_gate(load_cell(BJT_CS))
        assert result.status == GateStatus.PASS
        assert any("on/settles_to" in d for d in result.diagnostics)
        assert any("off/settles_to" in d for d in result.diagnostics)

    def test_power_input_conditioning_passes(self):
        result = simulation_cell_gate(load_cell(PWR_COND))
        assert result.status == GateStatus.PASS
        assert any("forward/settles_to" in d for d in result.diagnostics)
        assert any("reverse/settles_to" in d for d in result.diagnostics)

    def test_sallen_key_lowpass_2_passes(self):
        result = simulation_cell_gate(load_cell(SALLEN_KEY))
        assert result.status == GateStatus.PASS
        assert any("passband/amplitude_ratio" in d for d in result.diagnostics)
        assert any("at_fc/amplitude_ratio" in d for d in result.diagnostics)
        assert any("dc_settle/settles_to" in d for d in result.diagnostics)

    def test_mfb_lowpass_2_passes_and_is_inverted(self):
        result = simulation_cell_gate(load_cell(MFB))
        assert result.status == GateStatus.PASS
        assert any("passband/inverted" in d for d in result.diagnostics)
        assert any("at_fc/amplitude_ratio" in d for d in result.diagnostics)
        assert any("dc_settle/settles_to" in d for d in result.diagnostics)

    def test_linear_reg_fixed_passes(self):
        result = simulation_cell_gate(load_cell(LINREG))
        assert result.status == GateStatus.PASS
        assert any("regulating/settles_to" in d for d in result.diagnostics)
        assert any("dropout/settles_to" in d for d in result.diagnostics)

    def test_instrumentation_amp_3opamp_passes(self):
        result = simulation_cell_gate(load_cell(IN_AMP))
        assert result.status == GateStatus.PASS
        assert any("linear/amplitude_ratio" in d for d in result.diagnostics)
        assert any("overdrive/clipped_within" in d for d in result.diagnostics)

    def test_bridge_interface_passes(self):
        result = simulation_cell_gate(load_cell(BRIDGE))
        assert result.status == GateStatus.PASS
        assert any("dc_passthrough/settles_to(out_p)" in d for d in result.diagnostics)
        assert any("dc_passthrough/settles_to(out_n)" in d for d in result.diagnostics)

    def test_rail_splitter_virtual_gnd_passes(self):
        result = simulation_cell_gate(load_cell(RAIL_SPLIT))
        assert result.status == GateStatus.PASS
        assert any("midpoint/settles_to" in d for d in result.diagnostics)

    def test_input_protection_rfi_passes(self):
        result = simulation_cell_gate(load_cell(INPUT_PROT))
        assert result.status == GateStatus.PASS
        assert any("linear/settles_to" in d for d in result.diagnostics)
        assert any("overdrive/clipped_within" in d for d in result.diagnostics)

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

    @pytest.mark.parametrize(
        "cell_dir",
        [
            UNITY,
            INVERTING,
            VREF,
            CLAMP,
            SUMMING,
            ADC_RC,
            BJT_CS,
            PWR_COND,
            SALLEN_KEY,
            MFB,
            LINREG,
            IN_AMP,
            BRIDGE,
            RAIL_SPLIT,
            INPUT_PROT,
        ],
    )
    def test_new_cells_are_bit_identical(self, cell_dir: Path):
        cell = load_cell(cell_dir)
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


@pytest.mark.parametrize(
    "cell_dir",
    [
        NONINV,
        X4,
        UNITY,
        INVERTING,
        VREF,
        CLAMP,
        SUMMING,
        ADC_RC,
        BJT_CS,
        PWR_COND,
        SALLEN_KEY,
        MFB,
        LINREG,
        IN_AMP,
        BRIDGE,
        RAIL_SPLIT,
        INPUT_PROT,
    ],
)
def test_cell_gates_all_pass(cell_dir: Path):
    report = run_cell_gates(cell_dir)
    assert report.ok
    assert {r.gate for r in report.results} >= {"erc", "simulation"}
