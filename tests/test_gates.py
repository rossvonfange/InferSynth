"""Gate runner + netlist-partition-equivalence tests."""

from infersynth.gates import (
    GateResult,
    GateRunner,
    GateStatus,
    compare_partitions,
    default_runner,
    erc_gate,
    partition_equivalence_gate,
    render_review_gate,
    simulation_gate,
)

PART_A = {
    "VIN": {("", "VIN"), ("reg1", "vin")},
    "V5": {("reg1", "vout"), ("amp1", "vcc")},
    "GND": {("", "GND"), ("reg1", "gnd"), ("amp1", "gnd")},
}


class TestComparePartitions:
    def test_identical_equal(self):
        diff = compare_partitions(PART_A, PART_A)
        assert diff.equivalent
        assert diff.diagnostics == []

    def test_renamed_nets_equal(self):
        renamed = {
            "Net-(R1-Pad1)": PART_A["VIN"],
            "/sheet1/rail": PART_A["V5"],
            "GNDREF": PART_A["GND"],
        }
        assert compare_partitions(PART_A, renamed).equivalent

    def test_moved_pin_unequal(self):
        moved = {
            "VIN": {("", "VIN"), ("reg1", "vin"), ("amp1", "vcc")},  # vcc moved here
            "V5": {("reg1", "vout")},
            "GND": PART_A["GND"],
        }
        diff = compare_partitions(PART_A, moved)
        assert not diff.equivalent
        assert any("only in" in d for d in diff.diagnostics)

    def test_missing_pin_unequal(self):
        smaller = {
            "VIN": PART_A["VIN"],
            "V5": {("reg1", "vout")},
            "GND": PART_A["GND"],
        }
        diff = compare_partitions(PART_A, smaller)
        assert not diff.equivalent
        assert any("missing from netlist" in d for d in diff.diagnostics)

    def test_split_net_unequal(self):
        split = dict(PART_A)
        split["GND"] = {("", "GND"), ("reg1", "gnd")}
        split["GND2"] = {("amp1", "gnd")}
        assert not compare_partitions(PART_A, split).equivalent

    def test_duplicate_pin_not_a_partition(self):
        dup = dict(PART_A)
        dup["EXTRA"] = {("reg1", "vin")}  # vin already in VIN
        diff = compare_partitions(PART_A, dup)
        assert not diff.equivalent
        assert any("not a partition" in d for d in diff.diagnostics)

    def test_empty_nets_ignored(self):
        padded = {**PART_A, "UNUSED": set()}
        assert compare_partitions(PART_A, padded).equivalent


class TestPartitionGate:
    def test_pass(self):
        result = partition_equivalence_gate(
            {"ir_partition": PART_A, "netlist_partition": PART_A}
        )
        assert result.status == GateStatus.PASS

    def test_fail_has_diagnostics(self):
        bad = {**PART_A, "GND": {("", "GND")}}
        result = partition_equivalence_gate(
            {"ir_partition": PART_A, "netlist_partition": bad}
        )
        assert result.status == GateStatus.FAIL
        assert result.diagnostics

    def test_missing_context_skips(self):
        result = partition_equivalence_gate({"ir_partition": PART_A})
        assert result.status == GateStatus.SKIPPED


class TestRunnerAndStubs:
    def test_stub_gates_skip(self):
        for gate in (erc_gate, simulation_gate, render_review_gate):
            assert gate({}).status == GateStatus.SKIPPED

    def test_default_runner_report_is_loud_about_skips(self):
        report = default_runner().run(
            {"ir_partition": PART_A, "netlist_partition": PART_A}
        )
        assert report.ok  # skips do not fail the run...
        summary = report.summary()
        assert "!! GATE SKIPPED !!" in summary  # ...but they are loud
        assert "NOT fully verified" in summary
        assert "[PASS] netlist-partition-equivalence" in summary

    def test_failed_gate_fails_report(self):
        runner = GateRunner()
        runner.register("boom", lambda ctx: GateResult.failed("boom", "bad"))
        report = runner.run({})
        assert not report.ok
        assert "RESULT: FAILED" in report.summary()

    def test_crashing_gate_is_failure(self):
        runner = GateRunner()

        def crash(ctx):
            raise RuntimeError("kaput")

        runner.register("crash", crash)
        report = runner.run({})
        assert report.results[0].status == GateStatus.FAIL
        assert "kaput" in report.results[0].diagnostics[0]
