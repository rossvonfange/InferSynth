"""``catalog/taxonomy.yaml`` loader (SELECTION.md sec 3: function ontology).

The taxonomy is a small, catalog-wide file: ``{version, functions: {tag:
{desc: ...}, ...}}``. It is loaded once by :meth:`Catalog.load` from the
catalog root and threaded through to every :func:`~infersynth.catalog.loader.
load_cell` call so ``idioms.functions`` claims validate against it. Only the
top-level ``functions`` keys are meaningful to the validator (they are the
legal root tags); everything else is descriptive metadata for humans.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

__all__ = ["TaxonomyError", "load_taxonomy"]


class TaxonomyError(ValueError):
    """Raised when ``taxonomy.yaml`` itself is malformed."""


def load_taxonomy(path: str | Path) -> dict[str, Any]:
    """Load and minimally validate a ``taxonomy.yaml`` file.

    Returns the ``functions`` mapping (tag -> spec) — the set of legal
    ``idioms.functions`` root tags. Raises :class:`TaxonomyError` if the file
    is not well-formed (this is the catalog's own file; malformed taxonomy is
    a loud authoring bug, not a per-cell diagnostic).
    """
    p = Path(path)
    try:
        data = yaml.safe_load(p.read_text())
    except yaml.YAMLError as exc:
        raise TaxonomyError(f"{p}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise TaxonomyError(f"{p}: must be a mapping")
    functions = data.get("functions")
    if not isinstance(functions, dict) or not functions:
        raise TaxonomyError(f"{p}: 'functions' must be a non-empty mapping of tag -> spec")
    for tag in functions:
        if not isinstance(tag, str) or not tag:
            raise TaxonomyError(f"{p}: function tag keys must be non-empty strings")
    return functions
