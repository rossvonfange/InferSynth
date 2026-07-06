"""``--spec`` threading tests (BUILD_PLAN WP-L1 item 2): ``infersynth
synthesize``/``infersynth decide`` gain a formal-spec file whose
``allocations:`` reach ``match()`` and change the outcome, and whose ``frd:``
acts as a default for ``--frd`` (``--frd`` overrides it)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from infersynth.cli import main

CORE = Path(__file__).resolve().parent.parent / "catalog"

DEMO_FRD = """# Demo sensor board

- The board shall include a non-inverting amplifier gain stage with gain of 4.
- The board shall include a voltage reference.
- The board shall include decoupling.
- The board shall include an adc driver.
- The board shall include a 4-wire sensor input connector.
"""

# The heading "Demo sensor board" (a group node, no explicit id in the FRD) is
# always generated as "R-1" in document order (deterministic — see
# infersynth/lint/model.py); it is the parent of every bullet below it, so an
# allocation at "R-1" denying the only library in this catalog ("core")
# scopes every requirement in the FRD out of every candidate.
DENY_CORE_SPEC = {"allocations": [{"at": "R-1", "deny": ["core"]}]}


@pytest.fixture()
def demo_frd(tmp_path: Path) -> Path:
    p = tmp_path / "demo_frd.md"
    p.write_text(DEMO_FRD, encoding="utf-8")
    return p


def _write_spec(tmp_path: Path, data: dict, name: str = "spec.yaml") -> Path:
    p = tmp_path / name
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


class TestSynthesizeSpecThreading:
    def test_no_spec_all_decided(self, demo_frd, tmp_path):
        rc = main(
            [
                "synthesize", "--frd", str(demo_frd), "--catalog", str(CORE),
                "--out", str(tmp_path / "nospec"), "--profile", "prototype",
            ]
        )
        assert rc == 0

    def test_spec_deny_core_makes_everything_undecided(self, demo_frd, tmp_path):
        spec = _write_spec(tmp_path, DENY_CORE_SPEC)
        rc = main(
            [
                "synthesize", "--frd", str(demo_frd), "--catalog", str(CORE),
                "--spec", str(spec), "--out", str(tmp_path / "denied"),
                "--profile", "prototype",
            ]
        )
        # skipped/undecided => exit 3, never a guessed winner
        assert rc == 3

    def test_spec_frd_is_default_and_flag_overrides(self, demo_frd, tmp_path):
        gap = tmp_path / "gap.md"
        gap.write_text("- The board shall include a flux capacitor.\n", encoding="utf-8")
        spec = _write_spec(tmp_path, {"frd": str(demo_frd)})

        # spec.frd used when --frd is omitted -> the demo FRD fully decides.
        rc = main(
            ["synthesize", "--spec", str(spec), "--catalog", str(CORE),
             "--out", str(tmp_path / "fromspec"), "--profile", "prototype"]
        )
        assert rc == 0

        # --frd overrides spec.frd -> the gap FRD, which never decides.
        rc = main(
            ["synthesize", "--spec", str(spec), "--frd", str(gap), "--catalog", str(CORE),
             "--out", str(tmp_path / "override"), "--profile", "prototype"]
        )
        assert rc == 3

    def test_neither_frd_nor_spec_frd_errors(self, tmp_path):
        spec = _write_spec(tmp_path, {})
        rc = main(
            ["synthesize", "--spec", str(spec), "--catalog", str(CORE),
             "--out", str(tmp_path / "out")]
        )
        assert rc == 2

    def test_spec_profile_used_as_default(self, demo_frd, tmp_path):
        spec = _write_spec(tmp_path, {"profile": "prototype"})
        rc = main(
            ["synthesize", "--frd", str(demo_frd), "--spec", str(spec),
             "--catalog", str(CORE), "--out", str(tmp_path / "prof")]
        )
        assert rc == 0

    def test_invalid_spec_errors_cleanly(self, demo_frd, tmp_path):
        spec = _write_spec(tmp_path, {"bogus": 1})
        rc = main(
            ["synthesize", "--frd", str(demo_frd), "--spec", str(spec),
             "--catalog", str(CORE), "--out", str(tmp_path / "bad")]
        )
        assert rc == 2


class TestDecideSpecThreading:
    def test_no_spec_decides(self, demo_frd, tmp_path):
        rc = main(["decide", "--frd", str(demo_frd), "--catalog", str(CORE)])
        assert rc == 0

    def test_spec_deny_core_undecided(self, demo_frd, tmp_path):
        spec = _write_spec(tmp_path, DENY_CORE_SPEC)
        rc = main(
            ["decide", "--frd", str(demo_frd), "--spec", str(spec), "--catalog", str(CORE)]
        )
        assert rc == 3

    def test_spec_frd_default(self, demo_frd, tmp_path):
        spec = _write_spec(tmp_path, {"frd": str(demo_frd)})
        rc = main(["decide", "--spec", str(spec), "--catalog", str(CORE)])
        assert rc == 0
