# IR reference

The Python-embedded structural DSL of DESIGN.md §4, as implemented in
`infersynth/ir/`. This document describes what the code does today; where it
extends the design doc, the choice is conventional, not authoritative.

Users never write the IR; the intake stage constructs it. It is plain,
importable Python.

## Cell

`Cell(name, ports=(), params=(), idioms=None, timing=None)` — a catalog
primitive.

- **Ports** — `Port(name, direction, kind, required=True)`:
  - `direction`: `IN`, `OUT`, `INOUT`, or `PASSIVE` (undirected terminal —
    resistor legs, connector pins, power pins). Default `PASSIVE`.
  - `kind`: `ELECTRICAL` (default), `POWER`, or `DIGITAL`.
  - `required=True` ports must be connected at elaboration; optional ports may
    float.
- **Parameters** — `Param(name, type, default=None, range=None, allowed=None)`:
  - `type`: `"int" | "float" | "str" | "bool"` (an `int` value satisfies a
    `float` param; `bool` never satisfies `int`/`float`).
  - `range`: inclusive `(min, max)` for numeric types; either end may be
    `None` (unbounded). Mutually exclusive with `allowed` (an explicit value
    set). A non-`None` `default` must itself satisfy the constraint.
- **`idioms`** and **`timing`** are freeform-mapping *hooks*, carried opaquely:
  `idioms` for the catalog matcher (DESIGN §6), `timing` for the future
  clock/timing-domain constraint emitter (DESIGN §2).

## Design

`Design(name, ports=())` — a hierarchical design.

- **Boundary ports** (same `Port` type) are the IOB/connector boundary.
- `add_instance(name, target, params=None)` — `target` is a `Cell` or another
  `Design` (hierarchy). Instance names must not contain `/` (the hierarchy
  separator). Parameter bindings apply to Cell instances only; sub-Design
  instances take none.
- `connect(net_name, *endpoints, domain=None)` — attaches
  `(instance_name, port_name)` endpoints to a named net, creating it on first
  use. Use `BOUNDARY` (the empty string) as instance name for the Design's own
  boundary ports. `domain` sets the net's clock/timing-domain annotation.
- Endpoint references are checked eagerly at `connect()` time (unknown
  instance/port raises `IRError`); design-level rules are checked at
  elaboration.

## elaborate(design)

`elaborate(design) -> ElaboratedDesign`, raising `ElaborationError` with the
full sorted list of diagnostics if anything is wrong (all errors are collected,
not just the first).

### Flattening

- Walks the instance tree depth-first, instances in sorted-name order.
- Cell instances land at hierarchical paths joined with `/`
  (`"stage1/u1"`).
- A sub-Design's boundary ports are pass-through: the parent net and the
  child's internal net attached to that boundary port merge into one flat net
  (union-find). Boundary-port pins of *nested* designs do not appear in the
  output.
- The **top** design's boundary ports do appear: as pins with instance path
  `""` — the IOB pins of the flat model.
- Merged nets keep the shallowest hierarchical name (fewest `/`), ties broken
  lexicographically; a child-local net that never merges upward is named
  `<instance-path>/<net-name>`.
- Nets with no pins are dropped.

### Validation (each violation is one diagnostic)

- **Unconnected required ports** — on cell instances, on sub-Design boundary
  ports (checked in the parent), and on the top design's own boundary ports.
- **Double connection** — one `(instance, port)` endpoint (or boundary port)
  attached to more than one net in the same design.
- **Direction conflicts** — more than one *driver* on a flat net. Drivers are
  cell `OUT` pins and top-level boundary `IN` pins (external inputs drive
  inward). `INOUT`/`PASSIVE` pins never conflict; nets with zero drivers are
  legal (common for analog/passive nets).
- **Parameter violations** — unknown parameter name, type mismatch,
  range/allowed violation, or no binding for a parameter without a default.
- **Domain conflicts** — merged nets carrying different `domain` annotations.
- **Recursive instantiation** — a Design (transitively) instantiating itself.

### Output

`ElaboratedDesign` is frozen and fully deterministic (sorted everywhere; two
elaborations of the same design are equal structure):

- `instances`: `path -> ElaboratedInstance(path, cell, params)` in sorted path
  order, `params` fully resolved (bindings merged over defaults).
- `nets`: `net_name -> tuple[(instance_path, port_name), ...]` in sorted
  net-name order, pins sorted.
- `domains`: `net_name -> domain` for annotated nets only.
- `partition()`: the `net -> frozenset[pin]` map consumed by the
  netlist-partition-equivalence gate (DESIGN §7 gate 3, implemented in
  `infersynth/gates/netlist_equiv.py`).
