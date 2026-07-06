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


def test_mcp_subcommand_parses(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["mcp", "--help"])
    assert exc.value.code == 0
    assert "--catalog" in capsys.readouterr().out


def test_mcp_missing_extra_prints_hint(monkeypatch, capsys):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "infersynth.mcp_server.server" or name.startswith("mcp"):
            raise ImportError("no module named 'mcp'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert main(["mcp"]) == 2
    assert "infersynth[mcp]" in capsys.readouterr().err
