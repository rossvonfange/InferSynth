"""ReqIF import tests (optional extra). Core lint must work without ``reqif``."""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from infersynth.lint import reqif_io

REPO = Path(__file__).parent.parent

reqif = pytest.importorskip("reqif", reason="optional extra: pip install 'infersynth[reqif]'")


def make_minimal_reqif(path: Path) -> None:
    """Build a minimal .reqif fixture via the reqif library itself."""
    from reqif.models.reqif_core_content import ReqIFCoreContent
    from reqif.models.reqif_namespace_info import ReqIFNamespaceInfo
    from reqif.models.reqif_req_if_content import ReqIFReqIFContent
    from reqif.models.reqif_reqif_header import ReqIFReqIFHeader
    from reqif.models.reqif_spec_hierarchy import ReqIFSpecHierarchy
    from reqif.models.reqif_spec_object import ReqIFSpecObject
    from reqif.models.reqif_specification import ReqIFSpecification
    from reqif.object_lookup import ReqIFObjectLookup
    from reqif.reqif_bundle import ReqIFBundle
    from reqif.unparser import ReqIFUnparser

    def spec_object(ident: str, text: str) -> ReqIFSpecObject:
        return ReqIFSpecObject(
            identifier=ident, attributes=[], spec_object_type="T-req", long_name=text
        )

    objects = [
        spec_object("SYS.1", "The system shall measure air quality."),
        spec_object("SYS.1.1", "CO2 shall be measured with an NDIR sensor."),
        spec_object("SYS.2", "The system shall be powered from 802.3af PoE."),
    ]
    h11 = ReqIFSpecHierarchy(identifier="H-1-1", spec_object="SYS.1.1", level=2)
    h1 = ReqIFSpecHierarchy(identifier="H-1", spec_object="SYS.1", level=1, children=[h11])
    h2 = ReqIFSpecHierarchy(identifier="H-2", spec_object="SYS.2", level=1)
    specification = ReqIFSpecification(
        identifier="SPEC-1", long_name="AirCell", children=[h1, h2]
    )
    bundle = ReqIFBundle(
        namespace_info=ReqIFNamespaceInfo.create_default(),
        req_if_header=ReqIFReqIFHeader(identifier="HDR-1"),
        core_content=ReqIFCoreContent(
            ReqIFReqIFContent(spec_objects=objects, specifications=[specification])
        ),
        tool_extensions_tag_exists=False,
        lookup=ReqIFObjectLookup.empty(),
        exceptions=[],
    )
    path.write_text(ReqIFUnparser.unparse(bundle))


def test_reqif_roundtrip_to_requirement_set(tmp_path):
    fixture = tmp_path / "mini.reqif"
    make_minimal_reqif(fixture)
    rs = reqif_io.load_reqif(fixture)
    assert [(r.id, r.level) for r in rs] == [
        ("SPEC-1", 0),
        ("SYS.1", 1),
        ("SYS.1.1", 2),
        ("SYS.2", 1),
    ]
    assert rs["SPEC-1"].is_group  # the specification node is structural
    assert rs["SYS.1.1"].parent is rs["SYS.1"]
    assert rs["SYS.1.1"].text == "CO2 shall be measured with an NDIR sensor."
    assert rs["SYS.1"].source is not None and rs["SYS.1"].source.file == str(fixture)


def test_lint_path_over_reqif(tmp_path):
    from infersynth.lint import Severity, lint_path

    fixture = tmp_path / "mini.reqif"
    make_minimal_reqif(fixture)
    rs, diags = lint_path(fixture, catalog_dir=REPO / "catalog")
    assert len(rs.requirements()) == 3
    codes = {d.code for d in diags}
    assert codes == {"frd.ears", "frd.no-primitive"}
    assert not any(d.severity == Severity.ERROR for d in diags)


def test_load_reqif_without_extra_raises(monkeypatch):
    monkeypatch.setattr(reqif_io, "_HAVE_REQIF", False)
    assert not reqif_io.have_reqif()
    with pytest.raises(reqif_io.ReqIFImportError, match=r"infersynth\[reqif\]"):
        reqif_io.load_reqif("whatever.reqif")


def test_core_lint_runs_without_reqif_installed(tmp_path):
    """Import and run the lint engine in a subprocess where 'reqif' is blocked."""
    frd = tmp_path / "f.md"
    frd.write_text("- When armed, the unit shall alert.\n")
    script = textwrap.dedent(
        f"""
        import sys

        class Block:
            def find_spec(self, name, *a, **k):
                if name == "reqif" or name.startswith("reqif."):
                    raise ImportError("blocked for test")
                return None

        sys.meta_path.insert(0, Block())
        from infersynth.lint import lint_path
        _, diags = lint_path({str(frd)!r}, catalog_dir={str(REPO / "catalog")!r})
        assert any(d.code == "frd.ears" for d in diags), diags
        print("OK")
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout
