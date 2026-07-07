"""Design-level simulation tests (SEED_PLAN.md §1 acceptance criterion 3).

Covers: the spec ``testbench:`` loader (valid / bad kinds / missing keys), the
design-sim builder unit behavior (two synthetic behaviors chained by one net →
composed gain; rail-voltage-missing error; float-port loudness; determinism),
and the BridgeSense-1 end-to-end chain-gain check (~×100) run from a
SELF-CONTAINED tmp spec (FRD copy + testbench) so the test is immune to
parallel edits of the shared examples spec.yaml.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from infersynth.catalog import Catalog
from infersynth.gates.design_simulation import design_simulation_gate
from infersynth.gates.runner import GateStatus
from infersynth.sim.design import DesignSimError, build_design_sim
from infersynth.spec import DesignTestbench, SpecError, load_spec

REPO = Path(__file__).resolve().parents[1]
CATALOG_DIR = REPO / "catalog"
FRD = REPO / "examples" / "frds" / "07_bridgesense_1.md"


# ---------------------------------------------------------------------------
# spec `testbench:` loader
# ---------------------------------------------------------------------------


def _write_spec(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "spec.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


def _tb(**over) -> dict:
    base = {
        "rails": {"VOUT": 5.0, "GND": 0.0},
        "dt": 1.0e-6,
        "n_steps": 100,
        "stimuli": [
            {"signal": "n_a", "kind": "sine", "amplitude": 0.001, "freq_hz": 100.0},
            {"signal": "n_b", "kind": "dc", "value": 2.5},
        ],
        "checks": [
            {
                "kind": "amplitude_ratio",
                "input": "n_a",
                "output": "n_c",
                "expected": 100.0,
                "tol_pct": 8.0,
            },
            {"kind": "settles_to", "signal": "n_b", "value": 2.5, "tol": 0.05},
        ],
    }
    base.update(over)
    return base


class TestTestbenchLoader:
    def test_valid_testbench_loads(self, tmp_path):
        spec = load_spec(_write_spec(tmp_path, {"testbench": _tb()}))
        tb = spec.testbench
        assert isinstance(tb, DesignTestbench)
        assert tb.rails == {"VOUT": 5.0, "GND": 0.0}
        assert tb.dt == pytest.approx(1.0e-6)
        assert tb.n_steps == 100
        assert [s.kind for s in tb.stimuli] == ["sine", "dc"]
        assert tb.stimuli[0].signal == "n_a"
        assert tb.stimuli[0].params["amplitude"] == pytest.approx(0.001)
        assert [c.kind for c in tb.checks] == ["amplitude_ratio", "settles_to"]
        assert tb.checks[0].params["input"] == "n_a"

    def test_no_testbench_is_none(self, tmp_path):
        assert load_spec(_write_spec(tmp_path, {})).testbench is None

    def test_unknown_stimulus_kind_rejected(self, tmp_path):
        bad = _tb(stimuli=[{"signal": "n_a", "kind": "square", "value": 1.0}])
        with pytest.raises(SpecError, match="kind must be one of"):
            load_spec(_write_spec(tmp_path, {"testbench": bad}))

    def test_unknown_check_kind_rejected(self, tmp_path):
        bad = _tb(checks=[{"kind": "thd", "signal": "n_a"}])
        with pytest.raises(SpecError, match="kind must be one of"):
            load_spec(_write_spec(tmp_path, {"testbench": bad}))

    def test_unknown_stimulus_key_rejected(self, tmp_path):
        bad = _tb(stimuli=[{"signal": "n_a", "kind": "dc", "value": 1.0, "ramp": 2}])
        with pytest.raises(SpecError, match="unknown key"):
            load_spec(_write_spec(tmp_path, {"testbench": bad}))

    def test_missing_required_stimulus_key_rejected(self, tmp_path):
        bad = _tb(stimuli=[{"signal": "n_a", "kind": "sine", "amplitude": 1.0}])
        with pytest.raises(SpecError, match="missing key.*freq_hz"):
            load_spec(_write_spec(tmp_path, {"testbench": bad}))

    def test_missing_rails_rejected(self, tmp_path):
        bad = _tb()
        del bad["rails"]
        with pytest.raises(SpecError, match="'rails' is missing"):
            load_spec(_write_spec(tmp_path, {"testbench": bad}))

    def test_non_numeric_rail_rejected(self, tmp_path):
        bad = _tb(rails={"VOUT": "five"})
        with pytest.raises(SpecError, match="must be a number"):
            load_spec(_write_spec(tmp_path, {"testbench": bad}))

    def test_bad_dt_and_n_steps_rejected(self, tmp_path):
        with pytest.raises(SpecError, match="dt must be > 0"):
            load_spec(_write_spec(tmp_path, {"testbench": _tb(dt=0.0)}))
        with pytest.raises(SpecError, match="n_steps must be a positive integer"):
            load_spec(_write_spec(tmp_path, {"testbench": _tb(n_steps=-1)}))

    def test_unknown_testbench_key_rejected(self, tmp_path):
        bad = _tb()
        bad["seed"] = 42
        with pytest.raises(SpecError, match="unknown key"):
            load_spec(_write_spec(tmp_path, {"testbench": bad}))


# ---------------------------------------------------------------------------
# design-sim builder unit tests (synthetic cells; no real synthesis)
# ---------------------------------------------------------------------------

_CELL_YAML = """\
manifest:
  name: {name}
  version: "0.1.0"
  description: synthetic design-sim test cell
  provenance: test
  license: GPL-3.0-or-later
  technology: {technology}
ports:
{ports}
idioms:
  keywords: [{name}]
"""

_GAIN_BEHAVIOR = """\
from collections.abc import Mapping


class Gain:
    inputs = ("IN", "VCC", "GND")
    outputs = ("OUT",)

    def __init__(self, gain: float, name: str = "gain"):
        self.name = name
        self.gain = float(gain)

    def step(self, t, dt, inputs: Mapping[str, float]):
        gnd = inputs["GND"]
        return {"OUT": gnd + self.gain * (inputs["IN"] - gnd)}


def make_behavior(params):
    return Gain(gain=float(params.get("gain", 1.0)))
"""


def _make_cell(
    root: Path,
    name: str,
    ports: dict[str, tuple[str, str]],
    behavior: str | None,
    technology: str = "analog-ic",
) -> None:
    d = root / "core" / name
    d.mkdir(parents=True)
    lib_yaml = root / "core" / "library.yaml"
    if not lib_yaml.is_file():
        lib_yaml.write_text(
            "name: core\ndescription: design-sim test library\n"
            "tier: official\nmaintainer: tests\n",
            encoding="utf-8",
        )
    port_lines = "\n".join(
        f"  {p}: {{direction: {dr}, kind: {k}}}" for p, (dr, k) in ports.items()
    )
    (d / "cell.yaml").write_text(
        _CELL_YAML.format(name=name, technology=technology, ports=port_lines),
        encoding="utf-8",
    )
    (d / "fragment.kicad_sch").write_text("(kicad_sch)\n", encoding="utf-8")
    (d / "testbench").mkdir()
    (d / "model").mkdir()
    if behavior is not None:
        (d / "model" / "behavior.py").write_text(behavior, encoding="utf-8")


class _Inst:
    """Minimal InstantiatedCell stand-in for build_design_sim."""

    def __init__(self, instname: str, cell_key: str, params: dict | None = None):
        self.instname = instname
        self.cell_key = cell_key
        self.requirement_id = instname.upper()
        self.params = dict(params or {})


class _Net:
    def __init__(self, kind: str, name: str, members):
        self.kind = kind
        self.name = name
        self.members = tuple(members)
        self.driven = True


class _Plan:
    def __init__(self, nets):
        self.nets = tuple(nets)
        self.diagnostics = ()
        self.unwired_signal_ports = ()
        self.clean = True


class _Result:
    def __init__(self, instantiated, plan):
        self.instantiated = tuple(instantiated)
        self.wiring_plan = plan


@pytest.fixture()
def two_stage(tmp_path):
    """Two synthetic ×4 / ×5 gain cells chained by one signal net."""
    cat_dir = tmp_path / "catalog"
    ports = {
        "IN": ("in", "electrical"),
        "OUT": ("out", "electrical"),
        "VCC": ("passive", "power"),
        "GND": ("passive", "power"),
    }
    _make_cell(cat_dir, "stage-a", ports, _GAIN_BEHAVIOR)
    _make_cell(cat_dir, "stage-b", ports, _GAIN_BEHAVIOR)
    catalog = Catalog.load(cat_dir)
    (key_a,) = [k for k in catalog.cells if "stage-a" in k]
    (key_b,) = [k for k in catalog.cells if "stage-b" in k]
    insts = [
        _Inst("a1", key_a, {"gain": 4.0}),
        _Inst("b1", key_b, {"gain": 5.0}),
    ]
    nets = [
        _Net("rail", "VCC", [("a1", "VCC"), ("b1", "VCC")]),
        _Net("rail", "GND", [("a1", "GND"), ("b1", "GND")]),
        _Net("signal", "f_A_B", [("a1", "OUT"), ("b1", "IN")]),
        _Net("signal", "n_in", [("a1", "IN")]),
        _Net("signal", "n_out", [("b1", "OUT")]),
    ]
    return catalog, _Result(insts, _Plan(nets))


class TestBuildDesignSim:
    RAILS = {"VCC": 5.0, "GND": 0.0}

    def test_two_behaviors_chained_compose_gain(self, two_stage):
        from infersynth.sim.sources import SineSource

        catalog, result = two_stage
        build = build_design_sim(result, catalog, self.RAILS, dt=1e-6, n_steps=1000)
        assert build.behaviors == ("a1", "b1")
        assert build.passthrough == ()
        assert build.missing_behavior == ()
        build.simulation.add(
            SineSource(name="stim", amplitude=1.0, freq_hz=1000.0),
            {"out": build.resolve_net("n_in")},
        )
        traces = build.simulation.run()
        out = traces[build.resolve_net("n_out")]
        vin = traces[build.resolve_net("n_in")]
        ratio = (max(out) - min(out)) / (max(vin) - min(vin))
        assert ratio == pytest.approx(20.0, rel=1e-9)  # 4 × 5 composed

    def test_rail_voltage_missing_is_error_naming_it(self, two_stage):
        catalog, result = two_stage
        with pytest.raises(DesignSimError, match=r"\['VCC'\].*no declared voltage"):
            build_design_sim(result, catalog, {"GND": 0.0}, dt=1e-6, n_steps=10)

    def test_undeclared_extra_rail_is_error(self, two_stage):
        catalog, result = two_stage
        rails = dict(self.RAILS, VBAT=3.3)
        with pytest.raises(DesignSimError, match=r"\['VBAT'\].*not rail nets"):
            build_design_sim(result, catalog, rails, dt=1e-6, n_steps=10)

    def test_rail_nets_hold_declared_dc(self, two_stage):
        catalog, result = two_stage
        build = build_design_sim(result, catalog, self.RAILS, dt=1e-6, n_steps=5)
        traces = build.simulation.run()
        assert set(traces["VCC"]) == {5.0}
        assert set(traces["GND"]) == {0.0}

    def test_floating_input_port_is_loud(self, tmp_path):
        cat_dir = tmp_path / "catalog"
        ports = {
            "IN": ("in", "electrical"),
            "OUT": ("out", "electrical"),
            "VCC": ("passive", "power"),
            "GND": ("passive", "power"),
        }
        _make_cell(cat_dir, "stage-a", ports, _GAIN_BEHAVIOR)
        catalog = Catalog.load(cat_dir)
        (key,) = list(catalog.cells)
        insts = [_Inst("a1", key, {"gain": 2.0})]
        # IN is in NO net -> floats
        nets = [
            _Net("rail", "VCC", [("a1", "VCC")]),
            _Net("rail", "GND", [("a1", "GND")]),
            _Net("signal", "n_out", [("a1", "OUT")]),
        ]
        build = build_design_sim(
            _Result(insts, _Plan(nets)), catalog, self.RAILS, dt=1e-6, n_steps=5
        )
        assert build.floating_ports == (("a1", "IN"),)
        traces = build.simulation.run()  # floating input holds 0.0; still runs
        assert set(traces[build.resolve_net("n_out")]) == {0.0}

    def test_structural_cell_is_passthrough_not_missing(self, tmp_path):
        cat_dir = tmp_path / "catalog"
        _make_cell(
            cat_dir,
            "conn-x",
            {"P1": ("passive", "electrical"), "P2": ("passive", "electrical")},
            behavior=None,
            technology="electromechanical",
        )
        _make_cell(
            cat_dir,
            "amp-x",
            {
                "IN": ("in", "electrical"),
                "OUT": ("out", "electrical"),
                "VCC": ("passive", "power"),
                "GND": ("passive", "power"),
            },
            behavior=None,  # active cell WITHOUT a behavior -> missing
            technology="analog-ic",
        )
        catalog = Catalog.load(cat_dir)
        key_conn = next(k for k in catalog.cells if "conn-x" in k)
        key_amp = next(k for k in catalog.cells if "amp-x" in k)
        insts = [_Inst("c1", key_conn), _Inst("a1", key_amp)]
        nets = [
            _Net("rail", "GND", [("a1", "GND")]),
            _Net("rail", "VCC", [("a1", "VCC")]),
            _Net("signal", "n_1", [("c1", "P1"), ("a1", "IN")]),
        ]
        build = build_design_sim(
            _Result(insts, _Plan(nets)), catalog, self.RAILS, dt=1e-6, n_steps=5
        )
        assert build.passthrough == ("c1",)
        assert build.missing_behavior == ("a1",)

    def test_shared_endpoint_nets_collapse_to_one_signal(self, two_stage):
        catalog, result = two_stage
        # add a second net sharing a1.OUT: both must collapse to one signal
        nets = list(result.wiring_plan.nets) + [
            _Net("signal", "f_A_TAP", [("a1", "OUT"), ("b1", "VCC")])
        ]
        build = build_design_sim(
            _Result(result.instantiated, _Plan(nets)),
            catalog,
            self.RAILS,
            dt=1e-6,
            n_steps=5,
        )
        assert build.resolve_net("f_A_B") == build.resolve_net("f_A_TAP")

    def test_determinism_two_builds_identical_traces(self, two_stage):
        from infersynth.sim.sources import SineSource

        catalog, result = two_stage
        runs = []
        for _ in range(2):
            build = build_design_sim(result, catalog, self.RAILS, dt=1e-6, n_steps=500)
            build.simulation.add(
                SineSource(name="stim", amplitude=0.5, freq_hz=2000.0),
                {"out": build.resolve_net("n_in")},
            )
            runs.append(build.simulation.run())
        assert runs[0] == runs[1]  # byte-identical traces (SELECTION §8)


# ---------------------------------------------------------------------------
# the design-simulation gate (skip paths)
# ---------------------------------------------------------------------------


class TestDesignSimulationGate:
    def test_no_testbench_skips_loudly(self):
        res = design_simulation_gate({"testbench": None})
        assert res.status == GateStatus.SKIPPED
        assert "no 'testbench:'" in res.diagnostics[0]

    def test_unclean_plan_skips_loudly(self, two_stage):
        catalog, result = two_stage
        result.wiring_plan.clean = False
        tb = DesignTestbench(rails={"VCC": 5.0, "GND": 0.0}, dt=1e-6, n_steps=10)
        res = design_simulation_gate(
            {"result": result, "catalog": catalog, "testbench": tb}
        )
        assert res.status == GateStatus.SKIPPED
        assert "not clean" in res.diagnostics[0]

    def test_missing_behavior_skips_naming_instances(self, tmp_path):
        cat_dir = tmp_path / "catalog"
        _make_cell(
            cat_dir,
            "amp-x",
            {
                "IN": ("in", "electrical"),
                "OUT": ("out", "electrical"),
                "GND": ("passive", "power"),
            },
            behavior=None,
            technology="analog-ic",
        )
        catalog = Catalog.load(cat_dir)
        (key,) = list(catalog.cells)
        result = _Result(
            [_Inst("a1", key)], _Plan([_Net("rail", "GND", [("a1", "GND")])])
        )
        tb = DesignTestbench(rails={"GND": 0.0}, dt=1e-6, n_steps=10)
        res = design_simulation_gate(
            {"result": result, "catalog": catalog, "testbench": tb}
        )
        assert res.status == GateStatus.SKIPPED
        assert "a1" in res.diagnostics[0]
        assert "no model/behavior.py" in res.diagnostics[0]


# ---------------------------------------------------------------------------
# BridgeSense-1 end-to-end: the whole synthesized chain measures ~×100
# ---------------------------------------------------------------------------

# Self-contained testbench (mirrors the shipped examples spec but constructed
# here so the test is immune to parallel edits of the shared spec.yaml).
_BRIDGESENSE_TB = {
    "rails": {"VOUT": 5.0, "GND": 0.0, "VIN": 12.0},
    # dt near the ADC-driver RC's tau (~50 ns) so the fastest pole is stable;
    # 120000 steps = 6 ms spans filter settling + one full 200 Hz cycle.
    "dt": 5.0e-8,
    "n_steps": 120000,
    "stimuli": [
        {
            "signal": "f_SNS_01_CND_01_P",
            "kind": "sine",
            "amplitude": 1.0e-3,
            "freq_hz": 200.0,
            "offset": 0.0,
        },
        {"signal": "f_SNS_01_CND_01_N", "kind": "dc", "value": 0.0},
    ],
    "checks": [
        {
            "kind": "amplitude_ratio",
            "input": "f_SNS_01_CND_01_P",
            "output": "f_DRV_01_OUT_01",
            "expected": 100.0,
            "tol_pct": 8.0,
        }
    ],
}


@pytest.mark.slow
class TestBridgeSenseEndToEnd:
    def test_chain_gain_is_about_100(self, tmp_path):
        """The SEED_PLAN §1 crit-3 check: synthesize BridgeSense-1 and simulate
        the WHOLE behavioral chain (bridge → in-amp ×100 → sallen-key LP →
        buffer → RC driver); the end-to-end amplitude ratio measures ~×100."""
        from infersynth.synthesize import synthesize

        # self-contained spec: FRD copy + our own testbench (immune to
        # parallel edits of examples/frds/07_bridgesense_1.spec.yaml)
        frd_copy = tmp_path / "07_bridgesense_1.md"
        frd_copy.write_text(FRD.read_text(encoding="utf-8"), encoding="utf-8")
        spec_path = _write_spec(
            tmp_path,
            {
                "frd": frd_copy.name,
                "profile": "prototype",
                "rail_aliases": {
                    "VCC": "VOUT",
                    "VEE": "GND",
                    "EXC_N": "GND",
                    "SHIELD": "GND",
                },
                "testbench": _BRIDGESENSE_TB,
            },
        )
        spec = load_spec(spec_path)
        result = synthesize(
            frd=spec.frd,
            catalog_dir=CATALOG_DIR,
            out_dir=tmp_path / "out",
            profile=spec.profile or "prototype",
            allocations=spec.allocations,
            knobs=spec.knobs,
            endpoints=spec.endpoints,
            pins=spec.pins,
            feeds=spec.feeds,
            rail_aliases=spec.rail_aliases,
            write_trace=False,
            testbench=spec.testbench,
            verify=False,  # skip kicad ERC; run the design sim gate directly
        )
        assert result.wiring_plan is not None and result.wiring_plan.clean

        catalog = Catalog.load(CATALOG_DIR)
        res = design_simulation_gate(
            {"result": result, "catalog": catalog, "testbench": spec.testbench}
        )
        assert res.status == GateStatus.PASS, res.diagnostics
        gain_line = next(d for d in res.diagnostics if "amplitude_ratio" in d)
        assert "[PASS]" in gain_line
        # measured gain ~99.9 (in-amp ×100; bridge/filter/buffer/driver ≈ unity
        # at 200 Hz), well inside the ±8% window
        measured = float(gain_line.split("measured gain ")[1].split(" ")[0])
        assert measured == pytest.approx(100.0, rel=0.08)
        # structural connectors are passthrough, and that is loudly noted
        assert any("structural passthrough" in d for d in res.diagnostics)
        assert any("sns_01" in d for d in res.diagnostics)
