"""infersynth-mcp: the pipeline exposed as MCP tools (DESIGN.md section 9).

A thin stdio MCP server over the library — it owns no logic (UX.md: "all
four surfaces are adapters over the same library; no surface owns logic").

* :mod:`infersynth.mcp_server.tools` — the handler functions (JSON in/out);
  import and call these directly to drive the pipeline without an MCP
  transport (what the test suite does).
* :mod:`infersynth.mcp_server.server` — wires those handlers to the official
  ``mcp`` Python SDK's stdio server. Requires the ``mcp`` extra
  (``pip install infersynth[mcp]``); imported lazily so the base package
  never needs it.

Named ``mcp_server``, not ``mcp``, so this package never shadows the ``mcp``
SDK package it depends on.

Tools (DESIGN.md section 9): ``lint_frd``, ``catalog_search``,
``catalog_validate``, ``bind_cell``, ``instantiate_cell``, ``run_gates`` are
implemented; ``elaborate_spec``, ``match_catalog``, ``synthesize``,
``catalog_submit_check`` are registered but raise
:class:`infersynth.mcp_server.tools.NotImplementedStageError` naming the
BUILD_PLAN/DESIGN.md stage that delivers them.
"""

from infersynth.mcp_server.tools import TOOL_NAMES, NotImplementedStageError

__all__ = ["TOOL_NAMES", "NotImplementedStageError"]
