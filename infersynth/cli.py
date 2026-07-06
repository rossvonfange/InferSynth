"""``infersynth`` CLI (DESIGN.md section 9, deliverable 1).

Subcommands: ``elaborate``, ``gates``, ``lint``, ``catalog validate``. The
first two are stubs pending the spec-loading and synthesis pipeline; ``lint``
runs the FRD lint engine; ``catalog validate`` runs the catalog validator.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from infersynth import __version__


def _cmd_elaborate(args: argparse.Namespace) -> int:
    print(
        f"infersynth elaborate: not implemented yet — spec loading for {args.spec!r} "
        "lands with the synthesis pipeline (DESIGN.md section 10, v1).",
        file=sys.stderr,
    )
    return 2


def _cmd_gates(args: argparse.Namespace) -> int:
    import json

    from infersynth.catalog import CatalogError
    from infersynth.gates import GateReport, report_to_dict
    from infersynth.gates.run import run_cell_gates, run_design_gates

    if not args.cell and not args.design:
        print("infersynth gates: one of --cell or --design is required", file=sys.stderr)
        return 2
    if args.cell and args.design:
        print("infersynth gates: --cell and --design are mutually exclusive", file=sys.stderr)
        return 2

    try:
        if args.cell:
            report: GateReport = run_cell_gates(args.cell)
        else:
            if not args.root:
                print("infersynth gates: --design requires --root NAME.kicad_sch", file=sys.stderr)
                return 2
            root_path = Path(args.design) / args.root
            report = run_design_gates(root_path, golden=args.golden)
    except (CatalogError, OSError, ValueError) as exc:
        print(f"infersynth gates: {exc}", file=sys.stderr)
        return 1

    print(report.summary())
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(report_to_dict(report), fh, indent=2)
        print(f"wrote {args.json}", file=sys.stderr)
    return 0 if report.ok else 1


def _cmd_lint(args: argparse.Namespace) -> int:
    from infersynth.catalog import CatalogError
    from infersynth.lint import Severity, lint_path
    from infersynth.lint.reqif_io import ReqIFImportError

    try:
        _, diagnostics = lint_path(args.frd, catalog_dir=args.catalog)
    except (OSError, ReqIFImportError, CatalogError) as exc:
        print(f"infersynth lint: {exc}", file=sys.stderr)
        return 1
    for diag in diagnostics:
        print(diag.format_cli())
    if any(d.severity == Severity.ERROR for d in diagnostics):
        return 1
    return 0


def _cmd_catalog_validate(args: argparse.Namespace) -> int:
    from infersynth.catalog import Catalog, CatalogError

    try:
        catalog = Catalog.load(args.catalog_dir)
    except CatalogError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"OK: {len(catalog.cells)} cell(s) valid, no idiom collisions")
    for key in sorted(catalog.cells):
        print(f"  {key}")
    return 0


def _cmd_decide(args: argparse.Namespace) -> int:
    import json

    from infersynth.catalog import Catalog, CatalogError
    from infersynth.decide import build_trace, decide, render_text
    from infersynth.decide import write as write_trace
    from infersynth.decide.lockfile import load as load_lockfile
    from infersynth.lint import lint_path
    from infersynth.lint.reqif_io import ReqIFImportError
    from infersynth.match import match

    if args.profile and args.weights:
        print("infersynth decide: --profile and --weights are mutually exclusive", file=sys.stderr)
        return 2
    profile: str | dict
    if args.weights:
        try:
            profile = json.loads(args.weights)
        except json.JSONDecodeError as exc:
            print(f"infersynth decide: --weights is not valid JSON: {exc}", file=sys.stderr)
            return 1
        if not isinstance(profile, dict):
            print("infersynth decide: --weights must be a JSON object of dim->weight",
                  file=sys.stderr)
            return 1
    else:
        profile = args.profile or "production"

    try:
        catalog = Catalog.load(args.catalog)
        reqset, _diags = lint_path(args.frd, catalog_dir=args.catalog)
        lockfile = load_lockfile(args.lockfile) if args.lockfile else None
        match_result = match(reqset, catalog)
        decision = decide(match_result, catalog, profile, lockfile=lockfile)
    except (OSError, ReqIFImportError, CatalogError, ValueError) as exc:
        print(f"infersynth decide: {exc}", file=sys.stderr)
        return 1

    trace = build_trace(match_result, catalog, decision)
    print(render_text(trace))
    if args.trace:
        write_trace(trace, args.trace)
        print(f"wrote {args.trace}", file=sys.stderr)

    undecided = decision.undecided()
    if undecided:
        print("", file=sys.stderr)
        for rr in decision.resolution_requests:
            print(f"ResolutionRequest: {rr.diagnostic.message}", file=sys.stderr)
        for rid, status in undecided.items():
            print(f"undecided [{rid}]: {status}", file=sys.stderr)
        return 3
    return 0


def _cmd_costs_lock(args: argparse.Namespace) -> int:
    from infersynth.catalog import Catalog, CatalogError
    from infersynth.decide.lockfile import write as write_lockfile

    try:
        catalog = Catalog.load(args.catalog)
        lock = write_lockfile(
            catalog, args.output, timestamp=args.timestamp, source="cell.yaml"
        )
    except (OSError, CatalogError, ValueError) as exc:
        print(f"infersynth costs lock: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {args.output}: {len(lock.entries)} priced cell(s) @ {lock.generated_at}")
    return 0


def _cmd_panel(args: argparse.Namespace) -> int:
    try:
        import uvicorn

        from infersynth.panel.app import create_app
    except ImportError as exc:
        print(
            "infersynth panel: missing panel extra dependencies "
            f"({exc}); install with `pip install infersynth[panel]`",
            file=sys.stderr,
        )
        return 2

    app = create_app(catalog_dir=args.catalog, reports_dir=args.reports)
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


def _cmd_mcp(args: argparse.Namespace) -> int:
    import asyncio

    try:
        from infersynth.mcp_server.server import run_stdio
    except ImportError as exc:
        print(
            "infersynth mcp: missing mcp extra dependencies "
            f"({exc}); install with `pip install infersynth[mcp]`",
            file=sys.stderr,
        )
        return 2

    asyncio.run(run_stdio(catalog_dir=args.catalog))
def _cmd_lsp(args: argparse.Namespace) -> int:
    from infersynth.lsp import run

    try:
        run(catalog_dir=args.catalog)
    except ImportError as exc:
        print(
            f"infersynth lsp: missing lsp extra dependencies ({exc}); "
            "install with `pip install infersynth[lsp]`",
            file=sys.stderr,
        )
        return 2
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="infersynth",
        description="Inference and synthesis of PCBs from Functional Requirements Documents.",
    )
    parser.add_argument("--version", action="version", version=f"infersynth {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_elab = sub.add_parser("elaborate", help="elaborate a formal spec (stub)")
    p_elab.add_argument("spec", help="path to the formal spec")
    p_elab.set_defaults(func=_cmd_elaborate)

    p_gates = sub.add_parser(
        "gates", help="run verification gates on a catalog cell or a design"
    )
    p_gates.add_argument(
        "--cell", metavar="DIR", help="cell package dir: validate -> harness ERC -> golden netlist"
    )
    p_gates.add_argument(
        "--design", metavar="DIR", help="design dir (use with --root); runs ERC + optional --golden"
    )
    p_gates.add_argument(
        "--root", metavar="NAME.kicad_sch", help="root schematic filename within --design"
    )
    p_gates.add_argument(
        "--golden", metavar="FILE", help="golden_netlist.txt to compare against (design mode)"
    )
    p_gates.add_argument(
        "--json", metavar="OUT", help="write a panel-compatible GateReport JSON to OUT"
    )
    p_gates.set_defaults(func=_cmd_gates)

    p_lint = sub.add_parser("lint", help="lint an FRD (markdown or .reqif)")
    p_lint.add_argument("frd", help="path to the FRD (.md or .reqif)")
    p_lint.add_argument(
        "--catalog",
        default=None,
        metavar="DIR",
        help="catalog directory for vocabulary lint (grammar-only when omitted)",
    )
    p_lint.set_defaults(func=_cmd_lint)

    p_cat = sub.add_parser("catalog", help="catalog operations")
    cat_sub = p_cat.add_subparsers(dest="catalog_command", required=True)
    p_val = cat_sub.add_parser("validate", help="validate a catalog directory")
    p_val.add_argument("catalog_dir", help="directory of cell packages")
    p_val.set_defaults(func=_cmd_catalog_validate)

    p_decide = sub.add_parser(
        "decide", help="run lint -> match -> decide; pick winning covers (WP-D1)"
    )
    p_decide.add_argument("--frd", required=True, metavar="F.md", help="path to the FRD")
    p_decide.add_argument("--catalog", required=True, metavar="DIR", help="catalog directory")
    p_decide.add_argument(
        "--profile", metavar="P", help="named weight profile (prototype|production|hobbyist)"
    )
    p_decide.add_argument(
        "--weights", metavar="JSON", help='inline weights, e.g. \'{"bom": 8, "area_mm2": 5}\''
    )
    p_decide.add_argument("--lockfile", metavar="F", help="costs.lock.json to override cell costs")
    p_decide.add_argument("--trace", metavar="OUT.json", help="write selection_trace.json to OUT")
    p_decide.set_defaults(func=_cmd_decide)

    p_costs = sub.add_parser("costs", help="cost lockfile operations")
    costs_sub = p_costs.add_subparsers(dest="costs_command", required=True)
    p_lock = costs_sub.add_parser(
        "lock", help="snapshot current cell.yaml costs into a costs.lock.json"
    )
    p_lock.add_argument("--catalog", required=True, metavar="DIR", help="catalog directory")
    p_lock.add_argument(
        "--output", "-o", default="costs.lock.json", metavar="F", help="lockfile path to write"
    )
    p_lock.add_argument(
        "--timestamp", metavar="ISO8601", help="generated_at value (default: now, at lock time)"
    )
    p_lock.set_defaults(func=_cmd_costs_lock)

    p_panel = sub.add_parser(
        "panel", help="run the sidecar web panel (read-mostly catalog + gate viewer)"
    )
    p_panel.add_argument(
        "--catalog", default="catalog", help="catalog directory (default: ./catalog)"
    )
    p_panel.add_argument(
        "--reports", default=None, help="directory of *.json GateReport files to list under /gates"
    )
    p_panel.add_argument("--host", default="127.0.0.1", help="bind host (default: 127.0.0.1)")
    p_panel.add_argument("--port", type=int, default=8765, help="bind port (default: 8765)")
    p_panel.set_defaults(func=_cmd_panel)

    p_mcp = sub.add_parser(
        "mcp", help="run the infersynth-mcp stdio server (thin adapter over the library)"
    )
    p_mcp.add_argument(
        "--catalog", default=None, metavar="DIR", help="default catalog dir for catalog-aware tools"
    )
    p_mcp.set_defaults(func=_cmd_mcp)
    p_lsp = sub.add_parser(
        "lsp", help="run the language server over stdio (squiggles, completions, hover)"
    )
    p_lsp.add_argument(
        "--catalog",
        default=None,
        metavar="DIR",
        help="catalog directory for vocabulary features (grammar-only lint when omitted)",
    )
    p_lsp.set_defaults(func=_cmd_lsp)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
