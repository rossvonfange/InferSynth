"""``infersynth`` CLI (DESIGN.md section 9, deliverable 1).

Subcommands: ``elaborate``, ``gates``, ``lint``, ``catalog validate``. The
first two are stubs pending the spec-loading and synthesis pipeline; ``lint``
runs the FRD lint engine; ``catalog validate`` runs the catalog validator.
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

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
