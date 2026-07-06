"""InferSynth language server (BUILD_PLAN WP6, extra ``lsp``).

Stdio LSP transport (``pygls``) over :mod:`infersynth.lint`: squiggles,
catalog-vocabulary completions, idiom hovers, disambiguation code actions,
and a no-primitive quick-fix (UX.md "Editor (LSP)" surface). This package
owns no lint/synthesis logic — :mod:`infersynth.lsp.core` is pure
re-shaping of :mod:`infersynth.lint` results; :mod:`infersynth.lsp.server`
is the only module that imports the optional ``pygls`` dependency.

Importing this package never requires ``pygls`` to be installed; only
:func:`run` (and ``infersynth.lsp.server`` directly) do.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["run"]


def run(catalog_dir: str | Path | None = None) -> None:
    """Start the server on stdio. Raises :class:`ImportError` if the
    optional ``pygls`` dependency (extra ``lsp``) is not installed."""
    from infersynth.lsp.server import run_stdio

    run_stdio(catalog_dir)
