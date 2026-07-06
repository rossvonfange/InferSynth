"""``infersynth`` CLI (DESIGN.md section 9, deliverable 1).

Subcommands: ``elaborate``, ``gates``, ``catalog validate``. The first two are
stubs pending the spec-loading and synthesis pipeline; ``catalog validate``
runs the real catalog validator.
"""

from __future__ import annotations

import argparse
import sys

from infersynth import __version__


def _cmd_elaborate(args: argparse.Namespace) -> int:
    print(
        f"infersynth elaborate: not implemented yet — spec loading for {args.spec!r} "
        "lands with the synthesis pipeline (DESIGN.md section 10, v1).",
        file=sys.stderr,
    )
    return 2


def _cmd_gates(args: argparse.Namespace) -> int:
    print(
        f"infersynth gates: not implemented yet — running gates against {args.design!r} "
        "requires the synthesis pipeline (DESIGN.md section 7).",
        file=sys.stderr,
    )
    return 2


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

    p_gates = sub.add_parser("gates", help="run verification gates on a design (stub)")
    p_gates.add_argument("design", help="path to the synthesized design")
    p_gates.set_defaults(func=_cmd_gates)

    p_cat = sub.add_parser("catalog", help="catalog operations")
    cat_sub = p_cat.add_subparsers(dest="catalog_command", required=True)
    p_val = cat_sub.add_parser("validate", help="validate a catalog directory")
    p_val.add_argument("catalog_dir", help="directory of cell packages")
    p_val.set_defaults(func=_cmd_catalog_validate)

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

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
