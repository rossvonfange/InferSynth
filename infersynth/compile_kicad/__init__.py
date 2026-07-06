"""IR -> KiCad compiler, a KiCAD-MCP-Server client (DESIGN.md sections 8 and 9).

Speaks the per-sheet build recipe: create_schematic -> batch_add_and_connect ->
no-connects -> ERC -> render. Empty stub in this pass (no KiCad/MCP integration).

The direct emitter (writer) lives in :mod:`infersynth.compile_kicad.emit`
(BUILD_PLAN WP3): ``emit.new_design`` and ``emit.instantiate``.
"""

from infersynth.compile_kicad import emit

__all__ = ["emit"]
