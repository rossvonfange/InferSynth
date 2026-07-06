# Build plan — v1 (Synth) in staged work packages

> Companion to DESIGN.md / UX.md / SEED_PLAN.md, which own all design judgment.
> This document decomposes v1 into work packages (WP) precise enough for
> budget-model agents to execute a first pass. Rule for every WP: follow the
> referenced docs; where they are silent, choose conventionally, note the
> choice in the final report, and DO NOT invent architecture.
>
> Conventions for all WPs: Python ≥3.10, repo `/home/cycix/Desktop/fai-tuner/InferSynth`
> branch `v2-rewrite`; run `.venv/bin/pytest` (all green) and `.venv/bin/ruff check .`
> before each commit; commit trailer `Co-Authored-By: Claude <noreply@anthropic.com>`;
> never push; new runtime deps require a pyproject extra unless listed.

## Stage 1 — core engine (pure Python, no KiCad, no network)

### WP1 — cell.yaml schema v1 + binding evaluator
Owner area: `infersynth/catalog/`, new `infersynth/bind/expr.py`.
1. Teach the validator the sections golden cells already use (see
   `catalog/opamp-gain-noninverting/cell.yaml` and `-x4`): `ports`
   (name → {direction: in|out|inout|passive, kind: electrical|power|digital}),
   `bindings` (ref → arithmetic expression string), `verification`
   (`golden_netlist` file must exist), optional `idioms.disambiguation` (string).
   Unknown top-level sections become validation ERRORS now (strictness switch:
   `load_cell(..., strict=True)` default True, False keeps old tolerance).
2. `infersynth/bind/expr.py`: `evaluate(expr: str, params: Mapping[str, float|int]) -> float`
   — restricted arithmetic evaluator over +,-,*,/,**,//,%, parentheses, numeric
   literals, parameter names, and functions min/max/abs/round. Implement via
   `ast.parse` + whitelist walker; NO eval/exec; unknown names/nodes raise
   `BindingError` with the offending token. `bind_cell(cell, params) -> dict[ref, float]`
   validates params against idiom param constraints first (reuse ir.Param logic).
3. Validator cross-checks: every binding expression parses; every free name in it
   is a declared idiom param; every declared port direction is legal.
4. Tests: golden-cell yaml round-trip; strict-mode rejection of a typo'd section;
   expression evaluator (incl. attack strings: `__import__`, attribute access,
   lambda — must raise); binding of gain=100 → R1=99000, R2=1000 for cell #1.

### WP2 — requirements model + FRD lint core + ReqIF import
Owner area: new `infersynth/lint/` (package exists as stub), optional extra `reqif`.
1. `model.py`: `Requirement` (id, text, level, parent, children, source:
   file/line/col span, tags, rationale flag) and `RequirementSet` (tree +
   flat index; deterministic iteration). IDs: explicit (e.g. `SYS.1.2`) when
   present, else generated `R-<n>` in document order.
2. `parse_md.py`: extract requirements from FRD markdown per UX.md/DESIGN §6 —
   flat bullets/numbered items = requirements; headings = grouping hints
   (become parents); explicit ID prefixes (`SYS.1.1.1`, `PWR-01`) recognized;
   `[D]`-tagged and parenthetical rationale lines flagged `rationale=True`
   (never lintable requirements). Must parse all six `examples/frds/*.md`
   without crashing; add per-file snapshot tests of extracted (id, text) pairs.
3. `ears.py`: classify each requirement sentence against the five EARS patterns
   (ubiquitous, event-driven "when", state-driven "while", unwanted "if…then",
   optional "where") + `complex`/`unclassifiable`; regex/keyword based; emit
   INFO diagnostic with the detected pattern and WARN for unclassifiable ones
   that contain shall/must.
4. `vocab.py`: build the controlled vocabulary from a `Catalog` — for each cell,
   idiom keywords + param names/ranges (DESIGN §6: vocabulary is generated,
   never hand-written). `resolve(requirement, vocab)` → matched cells with
   in-range param extraction (simple keyword + number-with-unit extraction;
   units: V, A, mA, Hz, kHz, MHz, ppm, °C, %, ohm/Ω/k/M suffixes).
5. `diagnostics.py`: LSP-shaped `Diagnostic` (file, span, severity, code,
   message, quickfix hint) so the future LSP is an adapter (UX.md). Codes:
   `frd.unclassifiable`, `frd.ambiguous` (≥2 cells match, no disambiguation
   winner), `frd.no-primitive` (0 matches; quickfix = catalog-gap stub text),
   `frd.param-out-of-range`, `frd.rationale-ignored` (INFO).
6. `reqif_io.py` (extra dep `reqif` from strictdoc-project, add
   `[project.optional-dependencies] reqif = ["reqif"]`): import a .reqif file →
   `RequirementSet` (spec hierarchy → tree; long-name/description → text).
   Guard import so core works without the extra. One test with a minimal
   hand-written .reqif fixture (create via the lib itself in the test).
7. CLI: `infersynth lint <frd.md|.reqif> [--catalog DIR]` printing diagnostics
   `file:line:col severity code message`; exit 1 on any ERROR-severity.
8. Tests: run lint over all six example FRDs with the 2-cell catalog and
   snapshot the diagnostic codes per file (e.g. FRD 1 hobbyist → mostly
   no-primitive; FRD 3 engineer PWR-03 → matches nothing yet but must
   classify EARS cleanly). Keep snapshots small (counts + first N codes).

## Stage 2 — emitter + oracle (KiCad-facing; needs WP1)

### WP3 — direct emitter (writer)
Owner: new `infersynth/compile_kicad/emit.py`. UX.md write-path section governs.
Instantiate a cell fragment into a design: copy `fragment.kicad_sch` →
substitute `${IS.<ref>}` value slots from `bind_cell` output (format ohms:
1000.0 → `1k`, 99000 → `99k`, plain if <1k; helper `format_value`) → new UUIDs
for all symbols (preserve structure/formatting otherwise — text surgery, no
sexpdata round-trip) → write as `<design>/<instname>.kicad_sch` → splice a
`(sheet ...)` block into the parent (template from the smoke-tested pattern:
Sheet name = instname, Sheet file, plus properties `IS.Cell=<name>@<ver>`,
`IS.Param.<p>=<value>` per bound param) → maintain `(sheet_instances)` pages.
Also: root generator (blank root .kicad_sch + minimal .kicad_pro). Golden test:
instantiate opamp-gain-noninverting(gain=100) and the x4(all gains 10) into one
root; assert file set exists and `kicad-cli sch erc` PARSES it (erc allowed to
report errors, must not crash) — kicad-cli path: `kicad-cli` on PATH.

### WP4 — oracle gates (checker; shares no code with WP3)
Owner: `infersynth/gates/` (fill the stubs). All checks shell to `kicad-cli`:
- ERC gate: `kicad-cli sch erc --format json` on the ROOT; triage per playbook
  (severity policy object with allowlist codes; default policy: errors fail,
  allowlisted codes pass with note).
- Netlist gate: `kicad-cli sch export netlist --format kicadxml` → parse net→
  {ref/pin} partition → feed the existing partition-equivalence gate against
  (a) the elaborated IR and (b) a cell's golden_netlist.txt (add a tiny parser
  for that format).
- Harness generator: given a cell, emit a parent that instantiates the fragment
  (reuse WP3 emitter), adds a `power:PWR_FLAG` + label per power port, a driver
  label per `in` electrical port, binds default params (error if any param
  lacks default — golden cells: gain defaults absent, so harness binds
  midpoint of range; note this rule). `infersynth gates --cell DIR` runs:
  validate → harness ERC → golden-netlist equivalence; wire into CLI.
Tests: both committed cells pass gates end-to-end (requires kicad-cli; mark
`@pytest.mark.kicad` and skip cleanly when absent — but RUN them here, machine
has kicad-cli).

## Stage 3 — Phase 0 completion (needs Stage 2)
WP5: author sallen-key-lowpass-2 and linear-reg-fixed fragments + cell.yaml per
SEED_PLAN (MCP-driven authoring, per-sheet recipe; format frozen by cells 1–2).
Binding math: Sallen-Key equal-R equal-C Butterworth (C fixed E12, R from fc);
document chosen convention in cell.yaml comments.

## Stage 4 — surfaces (needs Stage 1)
WP6: LSP server (`pygls`, extra `lsp`) over lint/ diagnostics+completions+hover.
WP7: infersynth-mcp server (stdio MCP; tools lint_frd/elaborate/synthesize/
run_gates/catalog_search) — thin over the library per UX.md.
WP8: sidecar panel v0 (FastAPI+static, extra `panel`): catalog browser
(render fragment SVGs via kicad-cli at startup), gate report viewer (JSON in).

## Dispatch notes
Stage 1 WPs are independent → parallel agents in git worktrees, merged after
review. Stage 2 WP3/WP4 must NOT share helpers (writer/oracle independence);
run as separate agents. Stage 3 needs MCP session (main-loop work or tightly
scripted agent). Stage 4 after Stage 1 review.
