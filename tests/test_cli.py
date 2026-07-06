"""CLI smoke tests."""

import subprocess
import sys
import textwrap
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


def test_lsp_command_missing_extra_exits_2():
    """``infersynth lsp`` without the ``lsp`` extra installed exits 2 with a
    hint (mirrors the ``panel`` command's lazy-import pattern)."""
    script = textwrap.dedent(
        """
        import sys

        class Block:
            def find_spec(self, name, *a, **k):
                if name == "pygls" or name.startswith("pygls."):
                    raise ImportError("blocked for test")
                return None

        sys.meta_path.insert(0, Block())
        from infersynth.cli import main
        sys.exit(main(["lsp"]))
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True
    )
    assert result.returncode == 2
    assert "infersynth[lsp]" in result.stderr
