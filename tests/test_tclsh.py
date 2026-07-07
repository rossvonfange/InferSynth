"""infersynth tcl shell tests (surface #5: thin Tcl adapter over the tool
handlers). Skips cleanly on systems without the ``tkinter``/``python3-tk``
system package (see docs/TCL.md)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

tkinter = pytest.importorskip("tkinter")

try:
    tkinter.Tcl()
except tkinter.TclError:  # pragma: no cover - environment dependent
    pytest.skip("tkinter present but Tcl() construction failed", allow_module_level=True)

from infersynth.cli import main  # noqa: E402
from infersynth.mcp_server import tools as _tools  # noqa: E402
from infersynth.tclsh import build_interp  # noqa: E402

CATALOG = Path(__file__).parent.parent / "catalog"
FRD = Path(__file__).parent.parent / "examples" / "frds" / "01_hobbyist_garden_monitor.md"


def test_build_interp_registers_every_tool_name():
    interp = build_interp(catalog_dir=str(CATALOG))
    for name in _tools.TOOL_NAMES:
        # a registered Tcl command errors with a usage/handler error, not
        # "invalid command name" -- assert the command exists.
        assert interp.eval(f"info commands {name}") == name
    assert interp.eval("info commands is_version") == "is_version"
    assert interp.eval("info commands is_help") == "is_help"


def test_bom_and_pipeline_tools_are_registered():
    # round-3 "surface catch-up": the two new mcp_server.tools handlers
    # (bom/pipeline) come through automatically via TOOL_NAMES -- explicit
    # spot-check alongside the generic test above.
    interp = build_interp(catalog_dir=str(CATALOG))
    assert interp.eval("info commands bom") == "bom"
    assert interp.eval("info commands pipeline") == "pipeline"


def test_catalog_search_returns_json_with_both_opamp_cells():
    interp = build_interp(catalog_dir=str(CATALOG))
    result = json.loads(interp.eval("catalog_search -query amplifier"))
    cells = {r["cell"] for r in result["results"]}
    assert "core/opamp-gain-noninverting@0.1.0" in cells
    assert "core/opamp-gain-x4-noninverting@0.1.0" in cells


def test_params_flag_json_decodes_a_number():
    interp = build_interp(catalog_dir=str(CATALOG))
    cell_dir = CATALOG / "core" / "opamp-gain-noninverting"
    result = json.loads(
        interp.eval(f'bind_cell -cell_dir {cell_dir} -params {{"gain": 4}}')
    )
    assert result["cell"] == "opamp-gain-noninverting@0.1.0"
    assert set(result["bindings"]) == {"R1", "R2"}


def test_unknown_flag_is_a_tcl_error_listing_valid_flags():
    interp = build_interp(catalog_dir=str(CATALOG))
    with pytest.raises(tkinter.TclError) as exc_info:
        interp.eval("catalog_search -bogus 1")
    message = str(exc_info.value)
    assert "unknown flag -bogus" in message
    assert "-query" in message


def test_not_implemented_tool_surfaces_stage_naming_error():
    interp = build_interp(catalog_dir=str(CATALOG))
    with pytest.raises(tkinter.TclError) as exc_info:
        interp.eval("elaborate_spec")
    message = str(exc_info.value)
    assert "not implemented yet" in message
    assert "BUILD_PLAN" in message


def test_default_catalog_dir_is_injected_when_flag_omitted():
    interp = build_interp(catalog_dir=str(CATALOG))
    with_default = json.loads(interp.eval("catalog_search -query amplifier"))
    explicit = json.loads(interp.eval(f"catalog_search -query amplifier -catalog_dir {CATALOG}"))
    assert with_default == explicit


def test_c_mode_exit_codes_via_cli_main(capsys):
    assert main(["tcl", "--catalog", str(CATALOG), "-c", "is_version"]) == 0
    out = capsys.readouterr().out
    from infersynth import __version__

    assert __version__ in out

    assert main(["tcl", "--catalog", str(CATALOG), "-c", "elaborate_spec"]) == 1
    err = capsys.readouterr().err
    assert "not implemented yet" in err


def test_script_file_mode_runs_a_two_command_script(tmp_path, capfd):
    # NB: Tcl's `puts` writes through the C-level stdout channel, which
    # bypasses Python's sys.stdout object -- capfd (fd-level capture) sees
    # it; capsys (sys.stdout monkeypatch) would not.
    script = tmp_path / "run.tcl"
    script.write_text(
        "\n".join(
            [
                f'set r [lint_frd -path {FRD} -catalog_dir {CATALOG}]',
                "set v [is_version]",
                "puts $v",
            ]
        )
    )
    assert main(["tcl", "--catalog", str(CATALOG), str(script)]) == 0
    from infersynth import __version__

    assert __version__ in capfd.readouterr().out


def test_script_file_mode_error_exit_code(tmp_path, capsys):
    script = tmp_path / "bad.tcl"
    script.write_text("elaborate_spec\n")
    assert main(["tcl", "--catalog", str(CATALOG), str(script)]) == 1
    assert "not implemented yet" in capsys.readouterr().err
