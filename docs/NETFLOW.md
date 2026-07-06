# Net intent — how inter-cell nets are resolved and drawn

> Converged 2026-07-06 over three design rounds. Governs the wiring stage that
> follows decide/pack in synthesis. SELECTION §8 (repeatability) binds: every
> inference below is deterministic; everything fuzzy resolves BETWEEN runs
> into spec entries. House rule throughout: infer only where unique — ask
> rather than guess.

## The DAG law (cells encapsulate feedback)

Catalog law: a cell's feedback loops live INSIDE its fragment (the gain cell's
FB net is internal); the cell's external face is feed-forward. Therefore
**inferred inter-cell signal flow is a DAG by construction** — during flow
inference, any expansion that returns to an already-claimed net is rejected
(the spanning-tree no-revisit discipline; fai-recon's pathway-tracing rule).
Genuine inter-cell feedback (control loops, AGC) is real and legal but must be
**declared, never inferred**: an explicit `feeds` (below) may close a cycle;
the inference engine never may. Inferred = spanning growth; declared = the
deliberate back-edges.

Under the single-driver rule (elaborator-enforced), every net is a star rooted
at its one driver — so fan-out is never ambiguous in *structure* (who drives
whom), only in *membership* (which sinks join), decomposing net inference into
independent per-driver questions.

## Three tiers of intent, cheapest first

1. **Inferred** (free, deterministic):
   - **Rail resolver**: power-kind ports bind to the design's rail nets by
     name/kind (the clock-tree analogy, DESIGN §2). An undriven rail is a
     diagnostic ("nothing sources +5V" — catalog/decision gap), never a guess.
   - **Intra-chain wiring**: a matcher chain that closed via propagation IS
     net intent — chain[i] out-ports wire to chain[i+1] in-ports. Already
     computed by infersynth/match/propagate.py; currently discarded at
     instantiation (fix = consume it).
   - **Design-scope convergence**: the propagation algorithm at whole-design
     scope — grow forward from input-side connector/IOB cells, backward from
     output-side ones, through decided cells' typed ports; fronts meeting
     UNIQUELY = inferred nets. Multiple meetings = ensemble variance =
     ResolutionRequest ("does the amp feed the filter or the driver?"),
     resolved between runs. No-revisit rule enforces the DAG law.
2. **Declared**: `feeds` entries in the spec (`nets:`/`feeds:` section), plus
   FRD pragma sugar (below) that lint compiles into spec entries. Qualified
   form `feeds: SYS.2.IN2` settles port-level ambiguity. Declared feeds may
   close cycles.
3. **Elicited**: prose that states dataflow ("amplify … and filter it before
   the ADC") — the LLM edge PROPOSES feeds entries during lint; human signs
   off; they land in the spec as durable inputs. The deterministic engine
   never reads prose for flow.

## FRD pragmas (sugar, closed vocabulary)

Bracket-tag syntax (the `[D:…]` convention already parsed): pragmas are
compiled by lint into spec entries — the FRD stays prose, the spec stays the
formal artifact.

- `[feeds: <req-id>[.<port-or-role>]]` — requirement-level dataflow.
- `[use: <library/cell>]` — pin an exact cell (the legitimate direct route to
  a disambiguation-carrying cell).
- `[no-pack]` — per-requirement absorption forbid (SELECTION §5).

Boundary kept deliberate: `feeds` is requirement-level dataflow, NOT a netlist
language. Finer than qualified-feeds belongs in the spec's nets section or the
user's hands in KiCad.

## Interfaces (bundles) — protocol vs transport

Wiring standardizes at the INTERFACE level even when the protocol riding it
varies. `catalog/interfaces.yaml` (versioned beside taxonomy.yaml) defines
each standard once: roles with directions from the initiator's perspective,
kinds, optionality (uart TX/RX; spi SCLK/MOSI/MISO/CS; i2c SDA/SCL; can_phy
CANH/CANL with `domain: differential`; …). Cells group ports:
`interfaces: {main: {type: uart, role: initiator, map: {TX: TXD, RX: RXD}}}`.

- An interface group is ONE typed node in flow analysis — bundles mate only
  with complementary-role bundles of the same type (massive ambiguity
  reduction; whole classes of ask-cases become unique inferences).
- Crossovers (TX→RX, MOSI→MOSI) are encoded once in the definition — the
  classic wiring bugs become unrepresentable.
- **Protocol is a constraint attribute, not a wiring concern**: uart mates
  with uart; whether both ends satisfy "CAN FD 5 Mbps" is a parameter-
  compatibility check surfaced as a decide-stage diagnostic.
- Interface definitions later carry the clock-domain/layout annotations
  (DESIGN §2): `domain: differential` is the future source of diff-pair
  netclass constraints — written once for wiring, reused for P&R.

## Emission

The wiring writer is WP4's harness generator generalized: sheet pins spliced
onto `(sheet)` blocks + coincident labels at parent level, per the resolved
net set instead of per test stimuli. Same text surgery, same oracle
verification after — and it unlocks full-hierarchy ERC-zero as a real
synthesize gate (connectivity errors stop being expected).

## Build order

1. Rail resolver + intra-chain wiring consumption (no new syntax; ERC-zero
   becomes reachable for single-chain designs).
2. Pragma parsing → spec compilation in lint (`feeds`/`use`/`no-pack`).
3. interfaces.yaml + cell `interfaces:` schema + bundle-aware matching.
4. Design-scope convergence with variance-triaged ResolutionRequests.
5. Wiring emission (generalized harness writer) + ERC-zero gate flip.
6. Elicited-feeds intake flow (LLM edge, sign-off, spec landing).
