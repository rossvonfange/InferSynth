# Harvest from fai-recon — adopted ideas and their sources

> 2026-07-06. fai-recon (`/home/cycix/fai-recon`, same author) is a discovery
> engine for reverse-engineering embedded controllers — the *inverse* flow of
> InferSynth (it discovers a spec from an artifact; we synthesize an artifact
> from a spec) — but the middle machinery overlaps heavily. This document
> records what we adopt, adapt, or explicitly rejected, with maturity flags.
> Statuses: ADOPT (do it), ADAPT (pattern yes, code no), REJECTED/N-A.

## Corrections to expectations (honesty ledger)

- recon's "absorption" is **net-merging** (0-ohm/ferrite pass-throughs), not
  function-packing. No packing algorithm exists to lift. What transfers is the
  mechanics, not the algorithm (see 4).
- Everything SystemC in recon is **discrete-event digital TLM-2.0**. Nothing
  addresses SystemC-AMS continuous-time (ELN/TDF) — the AMS engine remains
  InferSynth's genuinely novel work.

## 1. ADOPT — sim-gate digital bootstrap: `fai_recon/pysysc/`

Built + tested pure-Python TLM-2.0 platform and timed kernel (`kernel.py`:
min-heap ns Scheduler, CoSimContext quantum loop; `tlm.py`: GenericPayload +
the `TlmTarget.b_transport` bind-not-transpile seam; frozen `ctx` contract:
now/schedule/raise_irq/lower_irq; README documents the Python-kernel →
Accellera-kernel swap table). Use as the zero-C++-dependency bootstrap for the
digital/logic half of testbenches; swap seam preserved for a real kernel.
**`pysysc_eval.md` is citable prior art for our emit-don't-bind decision**:
PySysC rejected (unmaintained, no SC_METHOD), cppyy rejected (Cling/ABI), Qbox
PythonBinder named as the real crossing if ever needed. Do not re-run this
evaluation.

Also adopt the **DeviceModel shape** (`fai_recon/emu/devices.py`: declarative
registers + named behavior callables + ctx, tolerant of ctx=None) as the
skeleton for programmable-cell behavioral models, and `regmap/`
(SVD/C-header → model; "parse machine-readable specs, never vendor") as the
template for generating catalog vocabulary from part specs.

## 2. ADOPT — sim gate as search driver (execution-guided lazy discovery, dualized)

From `Execution-Guided-Lazy-Discovery.pdf` + ADR-017 (loop orchestration is
thesis-stage there; the *pattern* is what we take):
`run → detect gap → resolve → resume → confirm-by-divergence → persist`.
Dualized for synthesis: expand candidate cells **lazily** where a requirement
or interface is still unsatisfied; let **testbench divergence** steer which
candidates expand next (wrong binding = observable failure, never silent);
**persist** every gated result (the catalog is the discover-and-keep corpus —
marginal cost of recurring functions → 0). The **cheapest-first resolution
ladder** becomes recall policy discipline: idioms → catalog/structured →
embeddings → LLM edge → human, each rung firing only on the residual of the
previous. This operationalizes "deterministic middle, LLM at edges."
Evaluation stance to match: **capability, not throughput** — "one requirement
correctly synthesized + gated" is the unit of progress.

## 3. ADOPT — bidirectional endpoint propagation + variance-as-underspecification

From recon's two-ended signal-chain tracing (ADR-007/011,
`recover/passes_signalchain.py`, `ratsnest.py`): both endpoints known ⇒
"generate the middle" becomes *scoring constrained hypotheses against ground
truth*. For the v2 matcher: propagate forward from the FRD's declared inputs
and backward from required outputs until the cell chain closes; enumerate
constrained candidate chains and score against the spec testbench; use
**ensemble variance across candidates as the underspecification signal** —
the principled trigger for allocation prompts and user decisions
(SELECTION.md §2/§7). recon's signal-chain motif library (decoupling,
diff-pair→transceiver, oscillator+caps, pull/termination) seeds idiom recall.

## 4. ADAPT — absorption guard-and-claim mechanics (not an algorithm)

From net_merge (`recover/heuristics.py` net_merge_key,
`docs/research/loop_avoidance.md` terminal guards): merges keyed by the
absorbed entity ⇒ deterministic, idempotent, order-independent application in
one fixed-point pass; each merge is a **weighted, falsifiable claim** beatable
by contrary evidence; **terminal guards** stop pass-throughs from absorbing
hubs. Mapped to SELECTION.md §5: an absorption into a programmable cell is a
revocable capacity-checked claim; the capacity/template check is the terminal
guard. The packing algorithm itself we still write.

## 5. ADOPT (v2) — fixed-point pipeline with assisted second pass

From `pipeline.md`: stages run blind, then `run(..., known=spec)` assisted;
later-stage facts feed back as earlier-stage inputs; conflicts trigger
re-queries until fixed point. For InferSynth: gate failures (ERC, partition,
sim) re-enter matching/binding as constraints — gates become loop-closing
evidence, not terminal checks. Current v1 pipeline stays DAG; this is the v2
engine's control structure.

## 6. ADOPT — weighted-claim arbiter + typed resolution requests

`fai_recon/arbitrate.py`: no LLM in the engine; every fact is
{value, source, confidence}; arbiter resolves by weighted vote and keeps
dissent; judgment gaps become **typed ResolutionRequests** answered
interchangeably by human / LLM / instrument, re-entering as weighted revocable
claims. Use this seam for the intake loop (lint ambiguity resolution) and for
factory/catalog screening decisions.

## 7. Smaller lifts

- `netsynth/kicad.py` + ADR-006: netlist-as-deliverable analysis and a working
  S-expression emitter from an evolving artifact; their build-vs-borrow survey
  (SKiDL/nl2sch/netlistsvg) is directly reusable context for our emitter work.
- `footprint_matching.md`: .kicad_mod pad-geometry parsing + package→footprint
  family table — feeds part binding (footprint selection) in v2.
- `docs/kicad_backlog.md` item 1: KiCad hierarchy ↔ SystemC bridge design
  (adapt KiCadVerilog MIT; component→model binding via symbol field) — aligns
  with our IS.* property convention; design-only, no code.
- `commons_interface.md` §0/§3: provenance-tagged artifact schema + TLM-shaped
  transaction seam contract — reference for selection_trace.json provenance.

## Maturity ledger (from the survey; verify before lifting)

Built+tested in recon: `pysysc/`, `emu/devices.py`, `regmap/`,
`netsynth/kicad.py`, `recover/` net_merge foundation, `arbitrate.py`.
Design-only: KiCad↔SystemC bridge, footprint matching, the lazy-discovery
loop orchestration. Absent entirely: anything SystemC-AMS.
