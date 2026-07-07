# What the files in this directory are (read this first)

This directory holds two very different kinds of FRD, and telling them apart
matters before you run anything against them.

## 01–06: lint-corpus articles, NOT synthesis demos

`01_hobbyist_garden_monitor.md` through `06_hierarchical_esp32_aircell.md` are
**test articles for the lint/diagnostic tooling**, written in deliberately
varied authentic author voices — a forum post from a hobbyist, an ops
director's memo, a formal engineering FRD, a layout designer's redline notes,
a scientist's email, a requirements-tool export. They intentionally target
the eventual, much larger component catalog described in
[docs/CATALOG_GROWTH.md](../../docs/CATALOG_GROWTH.md), not the 21-cell seed
catalog that exists today.

So when you run

```bash
infersynth lint examples/frds/01_hobbyist_garden_monitor.md --catalog catalog/
```

you get wall-to-wall `frd.no-primitive` diagnostics. **That is the point.**
The demonstration is that unmatched requirements surface as loud, precise,
per-requirement diagnostics instead of failing silently or being guessed at —
one of the project's governing principles (DESIGN.md §3). These six files are
exercised by `tests/test_lint_frds.py`; they are not expected to synthesize,
and "fixing" them to match the current catalog would destroy their purpose.

## 07: BridgeSense-1, the acceptance design that fully synthesizes

`07_bridgesense_1.md` + `07_bridgesense_1.spec.yaml` are the
[docs/SEED_PLAN.md](../../docs/SEED_PLAN.md) v1 acceptance pair — the one
example that runs the whole pipeline end to end against the seed catalog:
every requirement resolves to a cell, the `[feeds:]` pragmas plus the spec's
rail identities drive NETFLOW to a fully machine-wired hierarchical design,
and the verify gates (full-hierarchy ERC, design-level netlist
partition-equivalence) pass.

```bash
infersynth synthesize --spec examples/frds/07_bridgesense_1.spec.yaml \
    --catalog catalog/ --out build/bridgesense/
```

If you want a working example to study or to start your own FRD from, start
here — not with 01–06.
