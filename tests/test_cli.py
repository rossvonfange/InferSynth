"""CLI smoke tests."""

from pathlib import Path

import pytest

from infersynth.cli import main

FIXTURES = Path(__file__).parent / "fixtures" / "catalog"


def test_catalog_validate_ok(capsys):
    assert main(["catalog", "validate", str(FIXTURES)]) == 0
    out = capsys.readouterr().out
    assert "opamp-gain-noninverting@0.1.0" in out


def test_catalog_validate_fail(tmp_path, capsys):
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "cell.yaml").write_text("manifest: {}\n")
    assert main(["catalog", "validate", str(tmp_path)]) == 1
    assert "FAIL" in capsys.readouterr().err


def test_stub_subcommands_exit_2():
    assert main(["elaborate", "spec.py"]) == 2
    assert main(["gates", "design.kicad_sch"]) == 2


def test_version():
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
