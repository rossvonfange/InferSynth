# InferSynth UX — surfaces, and where authoring actually lives

> Converged 2026-07-06. Companion to DESIGN.md; this document owns the user-facing
> architecture. Guiding reframe: in the FPGA world engineers live in editor +
> terminal and the vendor GUI is a *viewer* (waveforms, floorplan, reports).
> Same split here: **KiCad is our waveform viewer** — eyes, hand-edits, and
> inter-cell routing live there; authoring lives where text lives. LLM/MCP
> replaces Tcl; markdown replaces the Tcl console.

## The four surfaces (one core, thin adapters)

| Surface | Who lives there | What it does |
|---|---|---|
| **Editor (LSP)** | requirements author | FRD.md with live lint: squiggles, catalog-vocabulary completions, idiom hovers, disambiguation code actions, "no primitive available" quick-fix that files a catalog-gap stub |
| **Conversation (MCP + skill)** | everyone; esp. manager/hobbyist-profile FRDs | elicitation ("what did I forget"), lint dialogue, gate-failure triage, driving runs |
| **Sidecar web panel** | reviewer | read-mostly: catalog browser (rendered fragments), gate dashboard, spec↔schematic cross-reference |
| **KiCad (viewer + craft)** | PCB designer | eyeballing renders/sheets, hand-finishing, inter-cell routing (the user's job by design) |

All four are adapters over the same library — the lint engine, catalog, and gate
runner are imported code; no surface owns logic.

## FRD language: EARS grammar + catalog semantics

Steal precisely from existing requirements practice, don't adopt wholesale:

- **EARS** sentence templates are the *grammar layer* of FRD lint ("While ⟨state⟩,
  when ⟨trigger⟩, the ⟨system⟩ shall ⟨response⟩" and its four siblings). EARS
  says the sentence is well-formed; the **catalog-derived vocabulary**
  (DESIGN §6) says it is synthesizable. The layers compose.
- **Permanent requirement IDs + hierarchy + traceability** from DOORS culture
  (see examples/frds/06). Every synthesized artifact traces back to requirement
  IDs; gate reports cite them.
- **ReqIF import** = v3 wishlist (enterprise interchange). SysML v2 = watch,
  don't adopt.

The LSP's completion dictionary is generated from the installed catalog, so the
editor can never suggest what synthesis can't do.

## Sidecar launch: PCM launcher plugin (wish-list, fragility contained)

A KiCad **Plugin and Content Manager** package installs a thin Action Plugin
(pcbnew toolbar button) whose sole job: find-or-start the local InferSynth
service, open the browser panel. The sidecar never touches KiCad internals —
the fragile part is ~50 lines of launcher that can break without taking
anything real down. Looks official; costs nothing architecturally.
(eeschema has no plugin surface; the button living in pcbnew is cosmetic.)

## KiCad-live bridge (IPC): small foothold, not a shoehorn

Via the IPC API against a live session (coverage expands by KiCad release —
probe empirically per version; the MCP server's IPC_CAPABLE_COMMANDS is the map):

- highlight-in-KiCad from the sidecar/conversation ("show me FB2's net"),
- **"explain this sheet"**: read the stamped `IS.Cell` / `IS.Param.*` /
  provenance properties and present cell docs, bindings, gate status. The
  DESIGN §5 storage model doubles as the in-CAD explainability hook.

No InferSynth function may *require* the live bridge; files + reload is always
sufficient.

## Write path: direct emission; MCP/kicad-cli is the oracle, never the emitter

The compiler writes `.kicad_sch` directly — instantiation = copy fragment →
substitute `${IS.*}` slots → stamp `IS.` sheet properties → splice the sheet
reference into the parent. Deterministic text operations on a format we control
(we authored the fragments). Then the verification layer runs the
**lint → correct → check cycle** with tools that share no code with the writer:

```
lint_offgrid → repair_flat_symbols (vendor symbols only) →
full-hierarchy ERC (kicad-cli) → netlist partition equivalence → render + review
```

Writer/checker independence means an emission bug cannot hide itself in
verification; KiCad format drift across versions is caught by the oracle.
MCP tooling remains the *interactive* surface — authoring new fragments,
inspection, and all oracle calls.

## Anti-goals

- No owned GUI (the cynth lesson): editor + CLI + web-viewer + host-CAD is the
  shape that survives; a full PyQt/wx app is a second product that starves the
  first.
- No KiCad-plugin dependency for any core function.
- No authoring UI inside KiCad — KiCad edits are the *user's* edits.
