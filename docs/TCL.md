# `infersynth tcl` — the EDA-native fifth surface

> Companion to UX.md's "four surfaces, one core": the editor (LSP), the
> conversation (MCP + skill), the sidecar panel, and KiCad-the-viewer are
> all thin adapters over the same library; no surface owns logic. This is a
> fifth thin adapter, for the audience that already lives in a Tcl console
> — most EDA tool consoles (ngspice, OpenROAD, vendor synthesis shells) are
> Tcl, and `infersynth tcl` gives that audience the same tool handlers
> without learning MCP or a web panel.

Implementation: stdlib `tkinter.Tcl()` — a headless Tcl 8.6 interpreter, no
Tk window, no new pip dependency. Every command dispatches straight to
`infersynth.mcp_server.tools`, the same handler set the MCP server wraps.

## Synopsis

```
infersynth tcl [--catalog DIR] [script.tcl]
infersynth tcl [--catalog DIR] -c 'command ...'
infersynth tcl [--catalog DIR]        # interactive REPL
```

- `--catalog DIR` sets a default `catalog_dir` that is auto-injected into
  any handler call that accepts a `catalog_dir` keyword and doesn't supply
  `-catalog_dir` explicitly.
- With a `script.tcl` argument: source the file, then exit — 0 if it runs
  clean, 1 if a Tcl error propagates out of the top level.
- With `-c 'command'`: evaluate exactly one command/script fragment and
  exit the same way; prints the command's return value (if non-empty).
- With neither: a plain read-eval-print loop (`is-tcl>` prompt, `exit` or
  `quit` to leave, Ctrl-D also exits). `readline` is enabled when
  importable but is not a hard dependency.

Registered commands are exactly `infersynth.mcp_server.tools.TOOL_NAMES`:
`lint_frd`, `catalog_search`, `catalog_validate`, `bind_cell`,
`instantiate_cell`, `run_gates`, `synthesize`, plus the tools still stubbed
upstream (`elaborate_spec`, `match_catalog`, `catalog_submit_check`) — those
surface their `NotImplementedStageError` message as a Tcl error naming the
BUILD_PLAN stage that lands them, same as every other surface. Two
shell-only conveniences are also registered: `is_version` (package version
string) and `is_help` (lists every command with its flag names,
introspected from each handler's signature).

## Argument convention

Every command takes Tcl `-flag value` pairs, one pair per handler keyword
argument — flag names match the Python keyword names exactly:

```tcl
lint_frd -path frd.md -catalog_dir catalog/
catalog_search -query amplifier
bind_cell -cell_dir catalog/core/opamp-gain-noninverting -params {"gain": 4}
```

Each value is JSON-decoded on a best-effort basis (`json.loads`; falls back
to the literal string when it doesn't parse), so plain numbers
(`-channels 4`) and JSON objects (`-params {"gain": 4}`) both come through
as real Python values rather than always-strings.

An unrecognized `-flag` is a Tcl error listing the valid flags for that
command. When a handler accepts `catalog_dir` and the caller omits
`-catalog_dir`, the interpreter's `--catalog` default is injected for you.

### The Tcl-brace / JSON-brace caveat

Tcl's own word-grouping strips a single enclosing `{...}` pair before a
value ever reaches Python — that's exactly the syntax JSON objects and
arrays use, so `-params {"gain": 4}` arrives in Python as `"gain": 4`
(braces already gone). `infersynth tcl` recovers from this automatically:
if a value doesn't parse as JSON as-is, it retries wrapped in `{...}` and
then `[...]` before giving up and treating it as a plain string. That
recovery is what makes the natural, single-braced spelling above work
without a Tcl/JSON purist's double-brace escape (`-params {{"gain": 4}}`,
which also works, since after Tcl's own stripping the inner braces survive
literally). If you hit a case the recovery can't parse, double-brace it
yourself and it will decode as with any other Tcl/JSON interop.

## Return convention

Handlers return JSON-serializable `dict`s; commands return them as
**compact JSON strings** — this shell deliberately does not build a Tcl
object model (list-of-dicts, associative arrays, etc.). Parse the string
yourself, e.g. with `tcllib`'s `json` package:

```tcl
package require json
set result [catalog_search -query amplifier]
set parsed [json::json2dict $result]
```

Python exceptions raised by a handler become Tcl errors carrying only the
exception's message (`str(exc)`) — never a Python traceback.

## Worked example: lint, synthesize, then parse the JSON

```tcl
package require json

set catalog catalog
set frd examples/frds/01_hobbyist_garden_monitor.md

# 1. Lint first -- counts tells you if there's anything worth synthesizing.
set lint_json [lint_frd -path $frd -catalog_dir $catalog]
set lint [json::json2dict $lint_json]
puts "lint counts: [dict get $lint counts]"

# 2. Full pipeline: lint -> match -> decide -> instantiate.
set syn_json [synthesize -frd $frd -catalog_dir $catalog -out_dir /tmp/garden \
                  -profile hobbyist]
set syn [json::json2dict $syn_json]
puts "wrote design root: [dict get $syn root]"
foreach inst [dict get $syn instantiated] {
    puts "  + [dict get $inst instname]  ([dict get $inst cell])"
}
```

Run it with `infersynth tcl --catalog catalog script.tcl`, or one command
at a time with `-c`.

## The `python3-tk` caveat

`infersynth tcl` uses the standard-library `tkinter` module purely for its
`Tcl()` interpreter object — no Tk window is ever created, no display is
required, and headless CI runs fine. Some distributions still split
`tkinter` into a separate system package that isn't installed by default:

- Debian/Ubuntu: `apt install python3-tk`
- Fedora/RHEL: `dnf install python3-tkinter`
- Arch: `pacman -S tk`
- macOS (python.org installer) / Windows: bundled, no extra step

If it's missing, `infersynth tcl` prints an install hint naming the right
package and exits 2 — it does not add `tkinter` as a pip dependency (it
can't be one; there's nothing to install from PyPI).
