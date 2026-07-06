"""Binding-expression evaluator + bind_cell tests (BUILD_PLAN WP1 item 4)."""

from pathlib import Path

import pytest

from infersynth.bind import BindingError, bind_cell, evaluate, free_names
from infersynth.catalog import load_cell

CATALOG = Path(__file__).parent.parent / "catalog" / "core"


class TestEvaluate:
    @pytest.mark.parametrize(
        ("expr", "params", "expected"),
        [
            ("1 + 2", {}, 3.0),
            ("2 * x", {"x": 21}, 42.0),
            ("(gain - 1) * rg_ohms", {"gain": 100, "rg_ohms": 1000}, 99000.0),
            ("10 / 4", {}, 2.5),
            ("7 // 2", {}, 3.0),
            ("7 % 3", {}, 1.0),
            ("2 ** 10", {}, 1024.0),
            ("-x + +y", {"x": 3, "y": 5}, 2.0),
            ("min(a, b)", {"a": 4, "b": 9}, 4.0),
            ("max(1, 2, 3)", {}, 3.0),
            ("abs(-x)", {"x": 7.5}, 7.5),
            ("round(2.678, 1)", {}, 2.7),
            ("((x))", {"x": 1.5}, 1.5),
        ],
    )
    def test_arithmetic(self, expr, params, expected):
        result = evaluate(expr, params)
        assert isinstance(result, float)
        assert result == pytest.approx(expected)

    def test_unknown_parameter_names_offender(self):
        with pytest.raises(BindingError, match="'nope'"):
            evaluate("nope + 1", {"gain": 2})

    def test_division_by_zero(self):
        with pytest.raises(BindingError, match="division by zero"):
            evaluate("1 / x", {"x": 0})

    def test_syntax_error(self):
        with pytest.raises(BindingError, match="invalid syntax"):
            evaluate("1 +", {})

    def test_empty_expression(self):
        with pytest.raises(BindingError, match="non-empty string"):
            evaluate("   ", {})

    def test_non_numeric_param_value_rejected(self):
        with pytest.raises(BindingError, match="'x' must be numeric"):
            evaluate("x", {"x": "1000"})
        with pytest.raises(BindingError, match="'x' must be numeric"):
            evaluate("x", {"x": True})

    @pytest.mark.parametrize(
        "expr",
        [
            "__import__('os').system('true')",
            "__import__",
            "().__class__.__bases__",
            "x.__class__",
            "gain.real",  # attribute access, even benign, is forbidden
            "(lambda: 1)()",
            "[1, 2][0]",  # subscript / list display
            "'a' + 'b'",  # string literals
            "1 if x else 2",  # conditional expression
            "x < 1",  # comparison
            "open('/etc/passwd')",  # non-whitelisted function
            "min(a, key=abs)",  # keyword arguments
            "x and 1",  # boolean operators
            "f'{x}'",  # f-strings
        ],
    )
    def test_attack_and_forbidden_strings_raise(self, expr):
        with pytest.raises(BindingError):
            evaluate(expr, {"x": 1, "a": 2, "gain": 3})

    def test_forbidden_error_names_offending_token(self):
        with pytest.raises(BindingError, match="__import__"):
            evaluate("__import__('os')", {})
        with pytest.raises(BindingError, match="'open'"):
            evaluate("open(x)", {"x": 1})

    def test_function_name_as_value_rejected(self):
        with pytest.raises(BindingError, match="'min' used as a value"):
            evaluate("min + 1", {})


class TestFreeNames:
    def test_params_are_free_functions_are_not(self):
        assert free_names("min((gain - 1) * rg_ohms, cap)") == {"gain", "rg_ohms", "cap"}

    def test_literal_only_expression_has_no_free_names(self):
        assert free_names("1 + 2 * 3") == set()


class TestBindCell:
    def test_golden_cell_gain_100(self):
        """BUILD_PLAN WP1: gain=100 -> R1=99000, R2=1000 for cell #1."""
        cell = load_cell(CATALOG / "opamp-gain-noninverting")
        assert bind_cell(cell, {"gain": 100.0}) == {"R1": 99000.0, "R2": 1000.0}

    def test_default_param_is_merged(self):
        cell = load_cell(CATALOG / "opamp-gain-noninverting")
        bound = bind_cell(cell, {"gain": 2.0, "rg_ohms": 470.0})
        assert bound == {"R1": 470.0, "R2": 470.0}

    def test_out_of_range_param_rejected(self):
        cell = load_cell(CATALOG / "opamp-gain-noninverting")
        with pytest.raises(BindingError, match="above maximum"):
            bind_cell(cell, {"gain": 5000.0})

    def test_unknown_param_rejected(self):
        cell = load_cell(CATALOG / "opamp-gain-noninverting")
        with pytest.raises(BindingError, match="unknown parameter 'bogus'"):
            bind_cell(cell, {"gain": 10.0, "bogus": 1.0})

    def test_missing_param_without_default_rejected(self):
        cell = load_cell(CATALOG / "opamp-gain-noninverting")
        with pytest.raises(BindingError, match="no binding and no default"):
            bind_cell(cell, {})

    def test_quad_cell_binds_all_channels(self):
        cell = load_cell(CATALOG / "opamp-gain-x4-noninverting")
        bound = bind_cell(
            cell,
            {"channels": 4, "gain1": 10.0, "gain2": 20.0, "gain3": 30.0, "gain4": 40.0},
        )
        assert bound["R1"] == 9000.0
        assert bound["R3"] == 19000.0
        assert bound["R5"] == 29000.0
        assert bound["R7"] == 39000.0
        assert {bound["R2"], bound["R4"], bound["R6"], bound["R8"]} == {1000.0}

    def test_allowed_set_enforced(self):
        cell = load_cell(CATALOG / "opamp-gain-x4-noninverting")
        with pytest.raises(BindingError, match="not in allowed set"):
            bind_cell(
                cell,
                {"channels": 2, "gain1": 1.0, "gain2": 1.0, "gain3": 1.0, "gain4": 1.0},
            )
