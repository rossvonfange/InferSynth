"""WP-M2 embeddings recall layer tests: hashing backend, the ``embed`` CLI,
staleness detection, semantic recall surfacing/provenance/disclosure, the
``strict`` knob shutting the layer off, the threshold knob, and the
model-mismatch hard error."""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from infersynth.catalog import Catalog
from infersynth.lint.model import Requirement, RequirementSet, SourceSpan
from infersynth.match import MatchKnobs, match
from infersynth.match.embed import (
    DEFAULT_ST_MODEL_ID,
    HashingBackend,
    SentenceTransformersBackend,
    backend_from_name,
    build_semantic_index,
    capability_text,
    cosine_similarity,
    embed_cell,
    text_hash,
)

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "catalog"


# --------------------------------------------------------------------------
# fixture helpers (mirrors tests/test_match.py's conventions)
# --------------------------------------------------------------------------


def write_cell(
    root: Path,
    name: str,
    *,
    keywords,
    description=None,
    functions=None,
    library=None,
    version="0.1.0",
) -> Path:
    base = root / library if library else root
    cell_dir = base / name
    (cell_dir / "model").mkdir(parents=True)
    (cell_dir / "testbench").mkdir()
    (cell_dir / "fragment.kicad_sch").write_text("(kicad_sch)\n")
    idioms = {"keywords": list(keywords)}
    if functions is not None:
        idioms["functions"] = list(functions)
    data = {
        "manifest": {
            "name": name,
            "version": version,
            "description": description or f"test cell {name}",
            "provenance": "synthetic test fixture",
            "license": "GPL-3.0-or-later",
        },
        "idioms": idioms,
        "selection": {},
        "depth": {"level": "L0"},
    }
    (cell_dir / "cell.yaml").write_text(yaml.safe_dump(data))
    return cell_dir


def write_taxonomy(root: Path, tags=("amplification", "regulation")) -> None:
    (root / "taxonomy.yaml").write_text(
        yaml.safe_dump({"version": 1, "functions": {t: {"desc": t} for t in tags}})
    )


def one_req(text: str, req_id: str = "R-1") -> RequirementSet:
    req = Requirement(id=req_id, text=text, source=SourceSpan("frd.md", 0, 0, 0, len(text)))
    return RequirementSet([req])


# --------------------------------------------------------------------------
# 1. HashingBackend: determinism + L2 normalization
# --------------------------------------------------------------------------


class TestHashingBackend:
    def test_deterministic_same_input_same_output(self):
        backend = HashingBackend()
        v1 = backend.embed(["a voltage follower buffer stage"])[0]
        v2 = backend.embed(["a voltage follower buffer stage"])[0]
        assert v1 == v2

    def test_deterministic_across_instances(self):
        v1 = HashingBackend().embed(["shunt regulator reference"])[0]
        v2 = HashingBackend().embed(["shunt regulator reference"])[0]
        assert v1 == v2

    def test_l2_normalized(self):
        backend = HashingBackend()
        for text in ("", "x", "a longer piece of capability text about op-amps"):
            vec = backend.embed([text])[0]
            norm = math.sqrt(sum(v * v for v in vec))
            assert norm == 0.0 or abs(norm - 1.0) < 1e-9

    def test_different_text_different_vector(self):
        backend = HashingBackend()
        v1 = backend.embed(["voltage follower buffer"])[0]
        v2 = backend.embed(["shunt bandgap reference"])[0]
        assert v1 != v2

    def test_model_id_reflects_dim_and_ngram(self):
        assert HashingBackend().model_id == "hashing-ngram3-256"
        assert HashingBackend(dim=128, ngram=4).model_id == "hashing-ngram4-128"


# --------------------------------------------------------------------------
# 2. capability text recipe + embed_cell
# --------------------------------------------------------------------------


class TestCapabilityText:
    def test_recipe_order_and_sorting(self, tmp_path):
        write_taxonomy(tmp_path)
        write_cell(
            tmp_path,
            "buf",
            keywords=["zeta keyword", "alpha keyword"],
            functions=["amplification"],
            description="a test description",
        )
        cat = Catalog.load(tmp_path)
        cell = cat.cells["buf@0.1.0"]
        text = capability_text(cell)
        assert text == (
            "a test description\n"
            "keywords: alpha keyword, zeta keyword\n"
            "functions: amplification"
        )

    def test_embed_cell_shape(self, tmp_path):
        write_cell(tmp_path, "buf", keywords=["voltage follower"])
        cat = Catalog.load(tmp_path)
        cell = cat.cells["buf@0.1.0"]
        backend = HashingBackend()
        data = embed_cell(cell, backend)
        assert set(data) == {"model_id", "dim", "vector", "text_hash"}
        assert data["model_id"] == backend.model_id
        assert data["dim"] == len(data["vector"])
        assert data["text_hash"] == text_hash(capability_text(cell))


# --------------------------------------------------------------------------
# 3. `infersynth embed` CLI
# --------------------------------------------------------------------------


class TestEmbedCli:
    def _tmp_catalog(self, tmp_path) -> Path:
        """Copy a small slice of the real catalog into tmp_path so the
        committed fixtures under repo `catalog/` are never mutated by tests."""
        dst = tmp_path / "catalog"
        shutil.copytree(CORE / "core" / "decoupling", dst / "core" / "decoupling")
        shutil.copytree(
            CORE / "core" / "opamp-gain-noninverting", dst / "core" / "opamp-gain-noninverting"
        )
        shutil.copy(CORE / "core" / "library.yaml", dst / "core" / "library.yaml")
        shutil.copy(CORE / "taxonomy.yaml", dst / "taxonomy.yaml")
        return dst

    def test_embed_cli_generates_embedding_json(self, tmp_path):
        cat_dir = self._tmp_catalog(tmp_path)
        assert not (cat_dir / "core" / "decoupling" / "embedding.json").exists()

        result = subprocess.run(
            [sys.executable, "-m", "infersynth.cli", "embed", "--catalog", str(cat_dir)],
            cwd=REPO,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr

        emb_path = cat_dir / "core" / "decoupling" / "embedding.json"
        assert emb_path.is_file()
        data = json.loads(emb_path.read_text())
        assert data["model_id"] == "hashing-ngram3-256"
        assert len(data["vector"]) == data["dim"]
        assert "text_hash" in data

        # repo fixtures are untouched
        assert not (CORE / "core" / "decoupling" / "embedding.json").exists()

    def test_embed_cli_regenerates_existing(self, tmp_path):
        cat_dir = self._tmp_catalog(tmp_path)
        emb_path = cat_dir / "core" / "decoupling" / "embedding.json"
        emb_path.write_text(
            json.dumps({"model_id": "stale-model", "dim": 2, "vector": [0.0, 1.0]})
        )
        result = subprocess.run(
            [sys.executable, "-m", "infersynth.cli", "embed", "--catalog", str(cat_dir)],
            cwd=REPO,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "stale: core/decoupling@0.1.0 (model_id changed)" in result.stderr
        data = json.loads(emb_path.read_text())
        assert data["model_id"] == "hashing-ngram3-256"

    def test_embed_cli_unknown_backend_rejected(self, tmp_path):
        cat_dir = self._tmp_catalog(tmp_path)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "infersynth.cli",
                "embed",
                "--catalog",
                str(cat_dir),
                "--backend",
                "bogus",
            ],
            cwd=REPO,
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0


# --------------------------------------------------------------------------
# shared fixture: a catalog with one cell embedded, for staleness/semantic
# recall/threshold/mismatch tests below
# --------------------------------------------------------------------------

_REQ_TEXT = "board needs a voltage buffer follower thing to isolate the sensor"
_RELEVANT_DESCRIPTION = "unity gain voltage follower buffer stage for sensor isolation"
_RELEVANT_KEYWORDS = ["unity gain buffer", "voltage follower"]
_UNRELATED_DESCRIPTION = "shunt voltage reference for precision bandgap regulation"
_UNRELATED_KEYWORDS = ["voltage reference", "shunt regulator"]


def _embedded_catalog(tmp_path: Path, backend=None) -> Catalog:
    """A 2-cell catalog: one cell semantically close to `_REQ_TEXT` (but not
    idiom-matchable, see below), one clearly unrelated. Both pre-embedded."""
    backend = backend or HashingBackend()
    write_taxonomy(tmp_path)
    write_cell(
        tmp_path,
        "voltage-follower-buf",
        keywords=["unity buffer widget"],  # deliberately NOT a substring of _REQ_TEXT
        description=_RELEVANT_DESCRIPTION,
        functions=["amplification"],
    )
    write_cell(
        tmp_path,
        "shunt-vref",
        keywords=["shunt widget"],
        description=_UNRELATED_DESCRIPTION,
        functions=["regulation"],
    )
    cat = Catalog.load(tmp_path)
    for key in cat.cells:
        cell = cat.cells[key]
        data = embed_cell(cell, backend)
        (cell.path / "embedding.json").write_text(json.dumps(data))
    return Catalog.load(tmp_path)


def _relevant_score(cat: Catalog) -> float:
    """Cosine score between `_REQ_TEXT` and the pre-embedded relevant cell's
    stored vector — read back from the catalog rather than reconstructed, so
    it can never drift from what `_embedded_catalog` actually wrote."""
    backend = HashingBackend()
    req_v = backend.embed([_REQ_TEXT])[0]
    cap_v = cat.cells["voltage-follower-buf@0.1.0"].embedding["vector"]
    return cosine_similarity(req_v, cap_v)


class TestSemanticRecallSurfacesCandidates:
    """Idiom recall misses (synonym/reordered wording), hashing-backend
    cosine similarity over the capability text hits."""

    def test_idiom_recall_misses_but_semantic_recall_hits(self, tmp_path):
        cat = _embedded_catalog(tmp_path)
        knobs = MatchKnobs(recall="semantic", semantic_threshold=0.3)
        result = match(one_req(_REQ_TEXT), cat, knobs=knobs)

        idiom_only = match(one_req(_REQ_TEXT), cat, knobs=MatchKnobs(recall="strict"))
        assert idiom_only.candidates["R-1"] == ()  # idiom recall genuinely misses

        keys = {c.cell_key for c in result.candidates["R-1"]}
        assert "voltage-follower-buf@0.1.0" in keys
        surfaced = result.candidates["R-1"]
        assert all(c.surfaced_by.startswith("semantic(") for c in surfaced)
        # the unrelated cell must not clear the threshold
        assert not any("shunt-vref" in c.cell_key for c in surfaced)


class TestStrictKnobDisablesSemantic:
    def test_strict_has_no_semantic_candidates_even_when_idiom_empty(self, tmp_path):
        cat = _embedded_catalog(tmp_path)
        result = match(one_req(_REQ_TEXT), cat, knobs=MatchKnobs(recall="strict"))
        assert result.candidates["R-1"] == ()
        assert not any(c.surfaced_by.startswith("semantic(") for c in result.candidates["R-1"])
        codes = [d.code for d in result.diagnostics]
        assert "frd.semantic-recall" not in codes


class TestProvenanceAndDisclosureDiagnostic:
    def test_provenance_format_and_diagnostic_content(self, tmp_path):
        cat = _embedded_catalog(tmp_path)
        knobs = MatchKnobs(recall="semantic", semantic_threshold=0.3)
        result = match(one_req(_REQ_TEXT), cat, knobs=knobs)

        surfaced = result.candidates["R-1"]
        assert surfaced, "expected at least one semantic candidate"
        for cand in surfaced:
            assert cand.surfaced_by.startswith("semantic(")
            assert cand.surfaced_by.endswith(")")
            score_str = cand.surfaced_by[len("semantic(") : -1]
            score = float(score_str)
            assert 0.3 <= score <= 1.0

        semantic_diags = [d for d in result.diagnostics if d.code == "frd.semantic-recall"]
        assert len(semantic_diags) == 1
        diag = semantic_diags[0]
        assert diag.severity.name == "INFO"
        assert "voltage-follower-buf" in diag.message
        for cand in surfaced:
            assert cand.cell_key in diag.message
            assert cand.surfaced_by in diag.message


class TestThresholdKnob:
    def test_raising_threshold_drops_candidates_lowering_keeps_them(self, tmp_path):
        cat = _embedded_catalog(tmp_path)
        score = _relevant_score(cat)

        low = match(
            one_req(_REQ_TEXT),
            cat,
            knobs=MatchKnobs(recall="semantic", semantic_threshold=max(score - 0.05, 0.0)),
        )
        high = match(
            one_req(_REQ_TEXT),
            cat,
            knobs=MatchKnobs(recall="semantic", semantic_threshold=min(score + 0.05, 1.0)),
        )
        assert low.candidates["R-1"] != ()
        assert high.candidates["R-1"] == ()


class TestStalenessDetection:
    def test_text_hash_mismatch_excludes_cell_and_emits_diagnostic(self, tmp_path):
        cat = _embedded_catalog(tmp_path)
        cell = cat.cells["voltage-follower-buf@0.1.0"]
        emb_path = cell.path / "embedding.json"
        data = json.loads(emb_path.read_text())
        data["text_hash"] = "deadbeef" * 8  # doesn't match current capability text
        emb_path.write_text(json.dumps(data))
        cat = Catalog.load(tmp_path)  # reload with the mutated sidecar

        result = match(
            one_req(_REQ_TEXT), cat, knobs=MatchKnobs(recall="semantic", semantic_threshold=0.3)
        )
        keys = {c.cell_key for c in result.candidates["R-1"]}
        assert "voltage-follower-buf@0.1.0" not in keys  # treated as un-embedded

        stale_diags = [d for d in result.diagnostics if d.code == "frd.embedding-stale"]
        assert len(stale_diags) == 1
        assert "voltage-follower-buf@0.1.0" in stale_diags[0].message
        assert stale_diags[0].severity.name == "WARNING"

    def test_model_id_change_is_treated_as_stale_not_silently_used(self, tmp_path):
        # A model_id mismatch is the hard-error path when it's the *run's*
        # backend that changed relative to the whole catalog; see
        # TestModelMismatchError below for that. Mutating just the recorded
        # model_id string (independent of dim/text) to something the current
        # backend never produces should also never be silently trusted.
        cat = _embedded_catalog(tmp_path)
        cell = cat.cells["voltage-follower-buf@0.1.0"]
        emb_path = cell.path / "embedding.json"
        data = json.loads(emb_path.read_text())
        data["model_id"] = "some-other-model-v2"
        emb_path.write_text(json.dumps(data))
        cat = Catalog.load(tmp_path)

        try:
            match(
                one_req(_REQ_TEXT),
                cat,
                knobs=MatchKnobs(recall="semantic", semantic_threshold=0.3),
            )
            raised = False
        except ValueError:
            raised = True
        assert raised


class TestModelMismatchError:
    def test_catalog_embedded_with_different_model_raises(self, tmp_path):
        cat = _embedded_catalog(tmp_path, backend=HashingBackend(dim=64, ngram=3))
        # this run is configured for the default (dim=256) hashing backend
        knobs = MatchKnobs(recall="semantic")
        try:
            match(one_req(_REQ_TEXT), cat, knobs=knobs, embedding_backend=HashingBackend())
        except ValueError as exc:
            assert "hashing-ngram3-64" in str(exc)
            assert "hashing-ngram3-256" in str(exc)
        else:
            raise AssertionError("expected a ValueError on embedding model mismatch")


# --------------------------------------------------------------------------
# backend_from_name / SentenceTransformersBackend lazy import
# --------------------------------------------------------------------------


class TestBackendFromName:
    def test_hashing(self):
        assert isinstance(backend_from_name("hashing"), HashingBackend)

    def test_st(self):
        backend = backend_from_name("st")
        assert isinstance(backend, SentenceTransformersBackend)
        assert backend.model_id == DEFAULT_ST_MODEL_ID

    def test_unknown_raises(self):
        try:
            backend_from_name("bogus")
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError")


class TestSentenceTransformersLazyImport:
    def test_missing_dependency_raises_clear_error(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "sentence_transformers":
                raise ImportError("no module named sentence_transformers")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        backend = SentenceTransformersBackend()
        try:
            backend.embed(["hello"])
        except Exception as exc:  # noqa: BLE001 - asserting the message, not the type below
            assert "infersynth[embeddings]" in str(exc)
        else:
            raise AssertionError("expected an error when sentence-transformers is absent")


class TestBuildSemanticIndex:
    def test_index_excludes_stale_reports_it(self, tmp_path):
        cat = _embedded_catalog(tmp_path)
        cell = cat.cells["voltage-follower-buf@0.1.0"]
        emb_path = cell.path / "embedding.json"
        data = json.loads(emb_path.read_text())
        data["text_hash"] = "0" * 64
        emb_path.write_text(json.dumps(data))
        cat = Catalog.load(tmp_path)

        index = build_semantic_index(cat, HashingBackend())
        assert "voltage-follower-buf@0.1.0" not in index.vectors
        assert any(s.cell_key == "voltage-follower-buf@0.1.0" for s in index.stale)
