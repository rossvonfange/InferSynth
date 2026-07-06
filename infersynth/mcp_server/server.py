"""infersynth-mcp: stdio MCP server (BUILD_PLAN WP7, DESIGN.md section 9).

Wire-protocol adapter only — every tool call dispatches straight to
:mod:`infersynth.mcp_server.tools`, which owns all the logic (UX.md: "all
four surfaces are adapters over the same library; no surface owns logic").

The ``mcp`` package (the official Python MCP SDK) is an optional dependency
(the ``mcp`` extra) so importing :mod:`infersynth` never requires it; only
this module and :func:`infersynth.cli._cmd_mcp` touch it, both via lazy
import.
"""

from __future__ import annotations

import json
from typing import Any

from infersynth.mcp_server import tools as _tools

__all__ = ["build_server", "run_stdio"]

# name -> (description, JSON Schema for inputSchema)
_TOOL_SCHEMAS: dict[str, tuple[str, dict[str, Any]]] = {
    "lint_frd": (
        "Lint an FRD (markdown or .reqif) for EARS grammar + catalog-vocabulary "
        "semantics. Give exactly one of 'path' or 'text'.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "path to the FRD file"},
                "text": {"type": "string", "description": "inline FRD markdown text"},
                "catalog_dir": {
                    "type": "string",
                    "description": "catalog dir for vocabulary lint (grammar-only when omitted)",
                },
            },
        },
    ),
    "catalog_search": (
        "Find catalog cells whose keywords/description/name match a query "
        "(substring match).",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "catalog_dir": {"type": "string", "default": "catalog"},
            },
            "required": ["query"],
        },
    ),
    "catalog_validate": (
        "Validate a catalog directory (schema + idiom-collision checks).",
        {
            "type": "object",
            "properties": {"catalog_dir": {"type": "string"}},
            "required": ["catalog_dir"],
        },
    ),
    "bind_cell": (
        "Resolve a cell's binding expressions against idiom parameters -> "
        "{fragment ref: value}.",
        {
            "type": "object",
            "properties": {
                "cell_dir": {"type": "string"},
                "params": {"type": "object"},
            },
            "required": ["cell_dir"],
        },
    ),
    "instantiate_cell": (
        "Instantiate a cell fragment into a design (WP3 direct emitter). "
        "Creates a new design when parent_sch is omitted.",
        {
            "type": "object",
            "properties": {
                "cell_dir": {"type": "string"},
                "params": {"type": "object"},
                "instname": {"type": "string"},
                "design_dir": {"type": "string"},
                "parent_sch": {"type": "string"},
            },
            "required": ["cell_dir", "instname", "design_dir"],
        },
    ),
    "run_gates": (
        "Run the verification gate runner (DESIGN.md section 7). Gates without "
        "a wired backend on this branch report SKIPPED, loudly.",
        {
            "type": "object",
            "properties": {
                "design_dir": {"type": "string"},
                "cell_dir": {"type": "string"},
                "ir_partition": {"type": "object"},
                "netlist_partition": {"type": "object"},
            },
        },
    ),
    "elaborate_spec": (
        "[not implemented yet] Elaborate a formal spec into IR.",
        {"type": "object", "properties": {}},
    ),
    "match_catalog": (
        "[not implemented yet] Match spec idioms to catalog cells (v2 inference).",
        {"type": "object", "properties": {}},
    ),
    "synthesize": (
        "[not implemented yet] End-to-end FRD -> verified schematic synthesis.",
        {"type": "object", "properties": {}},
    ),
    "catalog_submit_check": (
        "[not implemented yet] Pre-flight a community catalog submission (v3 intake).",
        {"type": "object", "properties": {}},
    ),
}

assert set(_TOOL_SCHEMAS) == set(_tools.TOOL_NAMES)


def _dispatch(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    handler = getattr(_tools, name)
    return handler(**arguments)


def build_server(catalog_dir: str | None = None) -> Any:
    """Build the low-level MCP ``Server`` with every DESIGN.md section 9 tool
    registered. *catalog_dir* is used as the default ``catalog_dir`` argument
    for tools that take one and don't get it explicitly in ``arguments``."""
    import mcp.types as types
    from mcp.server.lowlevel import Server

    server: Server = Server("infersynth-mcp")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(name=name, description=desc, inputSchema=schema)
            for name, (desc, schema) in _TOOL_SCHEMAS.items()
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
        if name not in _TOOL_SCHEMAS:
            raise ValueError(f"unknown tool {name!r}")
        args = dict(arguments or {})
        if catalog_dir is not None and "catalog_dir" not in args:
            if name in ("lint_frd", "catalog_search", "catalog_validate"):
                args["catalog_dir"] = catalog_dir
        try:
            result = _dispatch(name, args)
        except _tools.NotImplementedStageError as exc:
            result = {"error": "not_implemented", "tool": exc.tool, "stage": exc.stage}
        return [types.TextContent(type="text", text=json.dumps(result, default=str))]

    return server


async def run_stdio(catalog_dir: str | None = None) -> None:
    """Run the server over stdio until the client disconnects."""
    from mcp.server.stdio import stdio_server

    server = build_server(catalog_dir)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )
