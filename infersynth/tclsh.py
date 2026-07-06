"""``infersynth tcl``: a Tcl shell as a thin adapter over the tool handlers.

The EDA-native fifth surface (UX.md's "four surfaces, one core" plus this
one): every command below dispatches straight to
:mod:`infersynth.mcp_server.tools`, which owns all the logic. This module
owns none — same law as :mod:`infersynth.mcp_server.server` and
:mod:`infersynth.panel.app`, just for a Tcl audience instead of MCP/HTTP.

Implementation: stdlib ``tkinter.Tcl()`` — a headless Tcl interpreter, no Tk
window, no new pip dependency. The ``tkinter`` import is lazy (see
:func:`build_interp`) because some distros split it into a separate system
package (``python3-tk`` on Debian/Ubuntu, ``python3-tkinter`` on Fedora)
that may not be installed; mirrors the ``panel``/``mcp``/``lsp`` lazy-import
+ install-hint pattern in :mod:`infersynth.cli`.

Argument convention
--------------------
Every registered command takes Tcl ``-flag value`` pairs, one pair per
handler keyword argument, e.g.::

    lint_frd -path frd.md -catalog_dir catalog/

Each value is JSON-decoded on a best-effort basis (:func:`json.loads`; falls
back to the raw string when it doesn't parse), so ``-params {"gain": 4}``
and bare numbers (``-channels 4``) come through as real Python objects
instead of always-strings. An unrecognized ``-flag`` is a Tcl error listing
the valid flags for that command. When a handler accepts a ``catalog_dir``
keyword and the caller omits ``-catalog_dir``, the interpreter's configured
default (``infersynth tcl --catalog DIR``) is injected automatically.

Return convention
------------------
Handlers return JSON-serializable ``dict`` objects; commands return them as
**compact JSON strings** (``json.dumps(..., separators=(",", ":"))``) — we
deliberately do not build a Tcl object model (dict/list surface). Tcl
scripts that want structured access should ``package require json`` and
parse the string themselves (see ``docs/TCL.md`` for a worked example).

Errors
------
Python exceptions raised by a handler become Tcl errors carrying *only* the
exception message (``str(exc)``) — never a Python traceback. This includes
:class:`infersynth.mcp_server.tools.NotImplementedStageError`, whose message
already names the BUILD_PLAN stage that will deliver the tool.
"""

from __future__ import annotations

import inspect
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn

from infersynth.mcp_server import tools as _tools

__all__ = ["build_interp", "main"]

_INSTALL_HINT = (
    "infersynth tcl: tkinter is not available in this Python install; "
    "install the system package that provides it (e.g. `apt install "
    "python3-tk` on Debian/Ubuntu, `dnf install python3-tkinter` on "
    "Fedora) and try again."
)


def _decode_value(raw: str) -> Any:
    """Best-effort JSON-decode a single ``-flag`` value; else the raw string.

    Tcl's own word-grouping strips a single enclosing ``{...}`` pair before
    the value ever reaches Python, which collides with JSON object/array
    literals: ``-params {"gain": 4}`` arrives here as ``"gain": 4``, not
    ``{"gain": 4}``. Recover by re-wrapping and retrying once — this is what
    lets the natural, single-braced Tcl spelling in the docs/examples work,
    without requiring the double-brace escape a Tcl/JSON purist would use.
    """
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        pass
    for wrapped in (f"{{{raw}}}", f"[{raw}]"):
        try:
            return json.loads(wrapped)
        except (json.JSONDecodeError, ValueError):
            continue
    return raw


def _handler_params(handler: Callable[..., Any]) -> dict[str, inspect.Parameter]:
    sig = inspect.signature(handler)
    return {
        name: p
        for name, p in sig.parameters.items()
        if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
    }


def _parse_flags(name: str, argv: tuple[str, ...], valid_flags: set[str]) -> dict[str, Any]:
    if len(argv) % 2 != 0:
        raise ValueError(
            f"{name}: arguments must be '-flag value' pairs, got {len(argv)} token(s)"
        )
    kwargs: dict[str, Any] = {}
    for i in range(0, len(argv), 2):
        flag, value = argv[i], argv[i + 1]
        if not flag.startswith("-"):
            raise ValueError(f"{name}: expected a -flag, got {flag!r}")
        flag_name = flag[1:]
        if flag_name not in valid_flags:
            raise ValueError(
                f"{name}: unknown flag -{flag_name}; valid flags: "
                + ", ".join(f"-{f}" for f in sorted(valid_flags))
            )
        kwargs[flag_name] = _decode_value(value)
    return kwargs


def _tcl_error(interp: Any, message: str) -> NoReturn:
    """Raise a Tcl error carrying *message* verbatim (no Python traceback).

    Routing the message through the Tcl-native ``error`` command (rather
    than raising the Python exception directly) works around a
    ``tkinter.Tcl().createcommand`` quirk where a Python exception raised
    inside a registered command loses its message text on the way back
    into the interpreter's error result.
    """
    interp.call("error", message)
    raise AssertionError("unreachable: Tcl 'error' command always raises")


def _make_command(
    interp: Any,
    name: str,
    handler: Callable[..., Any],
    default_catalog_dir: str | None,
) -> Callable[..., str]:
    params = _handler_params(handler)
    valid_flags = set(params)

    def command(*argv: str) -> str:
        try:
            kwargs = _parse_flags(name, argv, valid_flags)
            if (
                default_catalog_dir is not None
                and "catalog_dir" in params
                and "catalog_dir" not in kwargs
            ):
                kwargs["catalog_dir"] = default_catalog_dir
            result = handler(**kwargs)
        except Exception as exc:  # noqa: BLE001 - deliberately broad: any -> Tcl error
            _tcl_error(interp, str(exc))
        return json.dumps(result, default=str, separators=(",", ":"))

    return command


def _is_help(interp: Any, commands: dict[str, Callable[..., Any]]) -> Callable[[], str]:
    def is_help() -> str:
        lines = ["infersynth tcl commands:"]
        for cmd_name in sorted(commands):
            handler = getattr(_tools, cmd_name)
            flags = " ".join(f"-{p}" for p in _handler_params(handler))
            lines.append(f"  {cmd_name} {flags}".rstrip())
        lines.append("  is_version")
        lines.append("  is_help")
        return "\n".join(lines)

    return is_help


def build_interp(catalog_dir: str | None = None) -> Any:
    """Build a headless ``tkinter.Tcl()`` interpreter with one Tcl command per
    handler in :data:`infersynth.mcp_server.tools.TOOL_NAMES`, plus
    ``is_version`` and ``is_help``.

    *catalog_dir* is injected as the default ``catalog_dir`` keyword for any
    handler that accepts one, when the caller omits ``-catalog_dir``.
    """
    try:
        import tkinter
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise ImportError(_INSTALL_HINT) from exc

    interp = tkinter.Tcl()
    commands: dict[str, Callable[..., Any]] = {}
    for tool_name in _tools.TOOL_NAMES:
        handler = getattr(_tools, tool_name)
        cmd = _make_command(interp, tool_name, handler, catalog_dir)
        interp.createcommand(tool_name, cmd)
        commands[tool_name] = cmd

    from infersynth import __version__

    interp.createcommand("is_version", lambda: __version__)
    interp.createcommand("is_help", _is_help(interp, commands))
    return interp


def _run_script(interp: Any, path: str) -> int:
    try:
        interp.evalfile(path)
    except Exception as exc:  # noqa: BLE001 - surface as a plain error line
        print(f"infersynth tcl: {exc}", file=sys.stderr)
        return 1
    return 0


def _run_command(interp: Any, command: str) -> int:
    try:
        result = interp.eval(command)
    except Exception as exc:  # noqa: BLE001
        print(f"infersynth tcl: {exc}", file=sys.stderr)
        return 1
    if result:
        print(result)
    return 0


def _repl(interp: Any) -> int:
    try:
        import readline  # noqa: F401 - side-effect import enables line editing
    except ImportError:
        pass

    print(f"infersynth tcl (Tcl {interp.eval('info patchlevel')}) — 'exit' to quit")
    while True:
        try:
            line = input("is-tcl> ")
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print()
            continue
        stripped = line.strip()
        if stripped in ("exit", "quit"):
            return 0
        if not stripped:
            continue
        try:
            result = interp.eval(line)
        except Exception as exc:  # noqa: BLE001
            print(f"error: {exc}", file=sys.stderr)
            continue
        if result:
            print(result)


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``infersynth tcl``; see module docstring for modes."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="infersynth tcl",
        description="Tcl scripting shell over the infersynth tool handlers.",
    )
    parser.add_argument(
        "--catalog", default=None, metavar="DIR", help="default catalog dir injected into calls"
    )
    parser.add_argument("-c", dest="command", metavar="CMD", help="run one Tcl command and exit")
    parser.add_argument("script", nargs="?", help="Tcl script file to source and exit")
    args = parser.parse_args(argv)

    try:
        interp = build_interp(catalog_dir=args.catalog)
    except ImportError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.command is not None:
        return _run_command(interp, args.command)
    if args.script is not None:
        if not Path(args.script).exists():
            print(f"infersynth tcl: no such file: {args.script}", file=sys.stderr)
            return 1
        return _run_script(interp, args.script)
    return _repl(interp)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
