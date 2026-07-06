"""Fragment SVG rendering via ``kicad-cli`` (BUILD_PLAN WP8, item 2).

Renders a cell's ``fragment.kicad_sch`` to SVG on demand, cached in a
directory keyed by the source file's mtime so edits invalidate the cache
automatically. If ``kicad-cli`` is not on PATH (or the render fails), callers
get ``None`` back and show a placeholder — never a crash.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from infersynth.catalog import CellPackage

__all__ = ["PLACEHOLDER_SVG", "default_cache_dir", "kicad_cli_available", "render_fragment_svg"]

#: Shown in place of a real render when kicad-cli is absent or a render fails.
#: Deliberately plain — this is a degrade path, not a design element.
PLACEHOLDER_SVG = """\
<svg xmlns="http://www.w3.org/2000/svg" width="480" height="200" viewBox="0 0 480 200">
  <rect width="480" height="200" fill="none" stroke="currentColor" stroke-dasharray="6 4"
        stroke-width="2" x="1" y="1" rx="8"/>
  <text x="240" y="95" text-anchor="middle" font-family="monospace" font-size="14">
    fragment preview unavailable
  </text>
  <text x="240" y="118" text-anchor="middle" font-family="monospace" font-size="12">
    (kicad-cli not found on PATH, or render failed)
  </text>
</svg>
"""


def kicad_cli_available() -> bool:
    return shutil.which("kicad-cli") is not None


def default_cache_dir() -> Path:
    d = Path(tempfile.gettempdir()) / "infersynth-panel-svg-cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def render_fragment_svg(cell: CellPackage, cache_dir: Path) -> Path | None:
    """Return a cached (rendering if needed) SVG path for *cell*'s fragment.

    Returns ``None`` when the fragment is missing, kicad-cli is unavailable,
    or the export fails — callers fall back to :data:`PLACEHOLDER_SVG`.
    """
    src = cell.path / "fragment.kicad_sch"
    if not src.is_file():
        return None

    cache_dir = Path(cache_dir)
    mtime = int(src.stat().st_mtime)
    cached = cache_dir / f"{cell.name}__{cell.version}__{mtime}.svg"
    if cached.is_file():
        return cached

    if not kicad_cli_available():
        return None

    cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            [
                "kicad-cli",
                "sch",
                "export",
                "svg",
                "--exclude-drawing-sheet",
                "--no-background-color",
                "--output",
                str(cache_dir),
                str(src),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None

    produced = cache_dir / f"{src.stem}.svg"
    if not produced.is_file():
        return None
    produced.replace(cached)
    return cached
