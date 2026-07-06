"""Recall Layer 2 — embeddings backends + catalog cache generation (WP-M2).

SELECTION.md §4: "each cell ... carries a capability text; ``embedding.json``
beside it caches ``{model_id, dim, vector}``. Requirement text embeds against
these; similarity above a catalog-configured floor SURFACES the cell as a
candidate ... Text is source of truth; the vector is a derived cache." This
module owns:

* the pluggable :class:`EmbeddingBackend` protocol + two concrete backends
  (:class:`HashingBackend`, zero-dependency and always available;
  :class:`SentenceTransformersBackend`, behind the optional ``embeddings``
  extra);
* the **capability text recipe** (:func:`capability_text`) — the single
  deterministic definition of "the text a cell's vector represents", derived
  from ``manifest.description`` + ``idioms.keywords`` + ``idioms.functions``
  for v0 (no new authored cell.yaml field);
* :func:`embed_cell`, which turns one cell + one backend into the exact
  ``embedding.json`` payload (:mod:`infersynth.catalog.loader` validates its
  shape); and
* :func:`build_semantic_index`, which the matcher (:mod:`infersynth.match.matcher`)
  calls once per run to turn a catalog's cached vectors into a
  cosine-searchable index, loudly refusing a whole-catalog model mismatch and
  quietly excluding (never silently trusting) any single cell whose cache has
  gone stale relative to its own declared model/text.

Real semantic recall (:func:`infersynth.match.recall.semantic_recall`) is a
thin cosine-similarity layer on top of this module's vectors; embeddings only
ever *surface* candidates here — nothing in this module decides or resolves.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from infersynth.catalog import Catalog
    from infersynth.catalog.loader import CellPackage

__all__ = [
    "DEFAULT_HASHING_DIM",
    "DEFAULT_HASHING_NGRAM",
    "DEFAULT_ST_MODEL_ID",
    "EmbeddingBackend",
    "EmbeddingBackendError",
    "HashingBackend",
    "SentenceTransformersBackend",
    "StaleCell",
    "capability_text",
    "text_hash",
    "embed_cell",
    "cosine_similarity",
    "build_semantic_index",
    "backend_from_name",
]


class EmbeddingBackendError(RuntimeError):
    """Raised when a backend cannot embed text (e.g. a missing optional dep)."""


class EmbeddingBackend(Protocol):
    """The pluggable embeddings interface — swapping backends is a config
    change (which backend + which ``model_id``), never a rewrite of the
    recall layer."""

    model_id: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


# --- capability text recipe (documented here, the single source of truth) --


def capability_text(cell: CellPackage) -> str:
    """The exact, deterministic text a cell's embedding vector represents.

    Recipe (v0, no new authored field — SELECTION §4/BUILD_PLAN WP-M2 item 2):
    three newline-separated lines, in this fixed order —

        <manifest.description>
        keywords: <sorted idioms.keywords, comma+space-joined>
        functions: <sorted idioms.functions, comma+space-joined>

    Sorting the keyword/function lists (rather than using cell.yaml's authored
    order) is what makes the recipe — and therefore the cache key
    (:func:`text_hash`) — insensitive to a no-op cell.yaml reformatting that
    merely reorders a list; only an actual content change re-embeds.
    """
    description = str(cell.manifest.get("description", "")).strip()
    keywords = ", ".join(sorted(cell.keywords))
    functions = ", ".join(sorted(cell.functions))
    return f"{description}\nkeywords: {keywords}\nfunctions: {functions}"


def text_hash(text: str) -> str:
    """sha256 hex digest of *text* — the ``embedding.json`` cache key."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- backend 1: deterministic hashing placeholder ---------------------------

DEFAULT_HASHING_DIM = 256
DEFAULT_HASHING_NGRAM = 3


def _char_ngrams(text: str, n: int) -> list[str]:
    normalized = " ".join(text.lower().split())
    if len(normalized) < n:
        return [normalized] if normalized else []
    return [normalized[i : i + n] for i in range(len(normalized) - n + 1)]


@dataclass
class HashingBackend:
    """Deterministic char-ngram feature-hashing embedding — zero dependencies.

    **RECALL-QUALITY PLACEHOLDER, not a real semantic model.** It knows
    nothing about word meaning; it only measures character-ngram overlap
    (so "voltage follower" and "voltage buffer follower thing" score high
    together, but true synonyms with no shared substrings — "op-amp" vs
    "operational amplifier" — will not). It exists so the recall machinery
    (cosine search, staleness, provenance, determinism) can be built and
    tested without a heavy ML dependency in the core install; swap in
    :class:`SentenceTransformersBackend` for real semantic recall.

    Deterministic: every ngram is hashed with :mod:`hashlib` (sha256), never
    Python's salted ``hash()`` builtin, so the same text embeds to the exact
    same vector across processes and interpreter runs. The result is
    L2-normalized (unit norm) so cosine similarity reduces to a dot product.

    ``model_id`` encodes the two knobs that change the vector space
    (``dim``, ``ngram``) so changing either is — correctly — a "model
    change" for staleness purposes (:func:`build_semantic_index`).
    """

    dim: int = DEFAULT_HASHING_DIM
    ngram: int = DEFAULT_HASHING_NGRAM

    @property
    def model_id(self) -> str:
        return f"hashing-ngram{self.ngram}-{self.dim}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for gram in _char_ngrams(text, self.ngram):
            digest = hashlib.sha256(gram.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:8], "big") % self.dim
            sign = 1.0 if (digest[8] & 1) == 0 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


# --- backend 2: sentence-transformers (optional `embeddings` extra) --------

#: Pinned default model id (SELECTION §4/§8: "the catalog manifest pins the
#: embedding model id" / "pinned embedding model"). Chosen for being small,
#: CPU-friendly, and a common, well-understood default; any change to this
#: constant is a catalog-wide re-embed, never a silent drift.
DEFAULT_ST_MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"


@dataclass
class SentenceTransformersBackend:
    """Real semantic embeddings via the optional ``sentence-transformers``
    package (``pip install infersynth[embeddings]``).

    The import is lazy (inside :meth:`embed`, not at module import time) so
    the core install never requires the dependency; importing this class is
    always safe, only *calling* :meth:`embed` without the extra installed
    raises :class:`EmbeddingBackendError` with an install hint.

    Determinism note: unlike :class:`HashingBackend`'s pure arithmetic, this
    backend's determinism rests on the pinned model *version* being fixed —
    two runs against the same pinned ``model_id`` with the same
    sentence-transformers/torch versions reproduce the same vectors; an
    upstream model-weights update is exactly the "model upgrade -> catalog-
    wide re-embed" case SELECTION §4 describes, not silent drift.
    """

    model_id: str = DEFAULT_ST_MODEL_ID
    _model: object = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbeddingBackendError(
                "SentenceTransformersBackend requires the 'sentence-transformers' "
                "package; install with `pip install infersynth[embeddings]`"
            ) from exc
        if self._model is None:
            self._model = SentenceTransformer(self.model_id)
        vectors = self._model.encode(list(texts), normalize_embeddings=True)
        return [[float(x) for x in vector] for vector in vectors]


def backend_from_name(name: str) -> EmbeddingBackend:
    """``"hashing"`` -> :class:`HashingBackend`, ``"st"`` ->
    :class:`SentenceTransformersBackend` — the ``infersynth embed --backend``
    CLI knob's name -> instance mapping."""
    if name == "hashing":
        return HashingBackend()
    if name == "st":
        return SentenceTransformersBackend()
    raise ValueError(f"unknown embedding backend {name!r} (expected 'hashing' or 'st')")


# --- embedding.json generation ----------------------------------------------


def embed_cell(cell: CellPackage, backend: EmbeddingBackend) -> dict:
    """Build the ``embedding.json`` payload for *cell* under *backend*."""
    text = capability_text(cell)
    vector = backend.embed([text])[0]
    return {
        "model_id": backend.model_id,
        "dim": len(vector),
        "vector": vector,
        "text_hash": text_hash(text),
    }


# --- semantic index + staleness (matcher-facing) ----------------------------


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Plain cosine similarity; safe for zero vectors (returns 0.0)."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


@dataclass(frozen=True)
class StaleCell:
    """One cell whose ``embedding.json`` cache does not match what the
    current run would generate — excluded from semantic recall, never
    silently used (WP-M2 staleness contract)."""

    cell_key: str
    reason: str  # "text-changed" (model_id matches, capability text moved on)


@dataclass(frozen=True)
class SemanticIndex:
    """Cosine-searchable cache of a catalog's *usable* cell embeddings.

    ``vectors`` maps cell key -> (vector, library). ``stale`` lists every
    cell whose cache was excluded because it no longer matches what
    *backend* would currently generate for it (SELECTION §4: the vector is a
    derived cache, text is the source of truth).
    """

    vectors: dict[str, tuple[list[float], str | None]]
    stale: tuple[StaleCell, ...]


def build_semantic_index(catalog: Catalog, backend: EmbeddingBackend) -> SemanticIndex:
    """Build a cosine-searchable index of *catalog*'s cached embeddings.

    Every cell that ships an ``embedding.json`` is checked against *backend*:

    * ``model_id`` mismatch is a **hard, run-stopping error** — the catalog
      was embedded with a different model than this run is configured to use
      (SELECTION §4/§8: pinned model + pinned text is what makes candidate
      sets reproducible; silently mixing vector spaces would make cosine
      scores meaningless). The fix is a catalog-wide re-embed
      (``infersynth embed --catalog ... --backend ...``), never a silent
      fallback.
    * ``text_hash`` mismatch (capability text changed since the vector was
      cached, model otherwise matching) is **not** a hard error: the cell is
      excluded from the index (treated as un-embedded) and reported in
      ``stale`` so the caller can emit a loud diagnostic — this is the
      "regenerate, don't guess" staleness contract.
    * A cell with no ``embedding.json`` at all is simply absent from the
      index (nothing to disclose — it has never been embedded).
    """
    vectors: dict[str, tuple[list[float], str | None]] = {}
    stale: list[StaleCell] = []
    for key in sorted(catalog.cells):
        cell = catalog.cells[key]
        emb = cell.embedding
        if emb is None:
            continue
        cached_model_id = emb.get("model_id")
        if cached_model_id != backend.model_id:
            raise ValueError(
                f"embedding model mismatch for cell {key!r}: embedding.json was "
                f"generated with model {cached_model_id!r}, but this run is "
                f"configured for {backend.model_id!r}. Re-embed the catalog with "
                "`infersynth embed --catalog <dir> --backend <name>` before using "
                "recall='semantic' (SELECTION §4/§8: a model change is a "
                "catalog-wide re-embed, never a silent mix of vector spaces)."
            )
        current_hash = text_hash(capability_text(cell))
        if emb.get("text_hash") != current_hash:
            stale.append(StaleCell(cell_key=key, reason="text-changed"))
            continue
        vectors[key] = (list(emb["vector"]), cell.library)
    return SemanticIndex(vectors=vectors, stale=tuple(stale))
