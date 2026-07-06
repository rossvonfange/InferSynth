"""Tests for the v0 dataflow kernel: topo order, stable tiebreak, chains, errors."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from infersynth.sim import BoundBlock, Simulation, SimulationError, topological_order


class Gain:
    """y = k*x; ports x -> y (stateless)."""

    def __init__(self, name: str, k: float, x: str = "x", y: str = "y"):
        self.name = name
        self.k = k
        self.inputs = (x,)
        self.outputs = (y,)

    def step(self, t: float, dt: float, inputs: Mapping[str, float]) -> dict[str, float]:
        return {self.outputs[0]: self.k * inputs[self.inputs[0]]}


class Const:
    def __init__(self, name: str, value: float, out: str = "out"):
        self.name = name
        self.value = value
        self.inputs = ()
        self.outputs = (out,)

    def step(self, t: float, dt: float, inputs: Mapping[str, float]) -> dict[str, float]:
        return {self.outputs[0]: self.value}


class Counter:
    """Stateful: emits 0,1,2,... on its output. Proves state lives on self."""

    def __init__(self, name: str, out: str = "n"):
        self.name = name
        self.inputs = ()
        self.outputs = (out,)
        self._n = -1

    def step(self, t: float, dt: float, inputs: Mapping[str, float]) -> dict[str, float]:
        self._n += 1
        return {self.outputs[0]: float(self._n)}


def _names(order: list[BoundBlock]) -> list[str]:
    return [b.block.name for b in order]


def test_two_block_chain_orders_producer_first():
    src = BoundBlock(Const("src", 2.0, out="a"), {"a": "a"})
    amp = BoundBlock(Gain("amp", 3.0, x="a", y="b"), {"a": "a", "b": "b"})
    # pass consumer first: topo must still put producer first
    order = topological_order([amp, src])
    assert _names(order) == ["src", "amp"]


def test_stable_tiebreak_is_name_sorted():
    # three independent sources -> deterministic name order regardless of input order
    blocks = [
        BoundBlock(Const("zebra", 1.0, out="z"), {"z": "z"}),
        BoundBlock(Const("alpha", 1.0, out="al"), {"al": "al"}),
        BoundBlock(Const("mike", 1.0, out="m"), {"m": "m"}),
    ]
    assert _names(topological_order(blocks)) == ["alpha", "mike", "zebra"]
    assert _names(topological_order(list(reversed(blocks)))) == ["alpha", "mike", "zebra"]


def test_diamond_orders_deterministically():
    # s -> {l, r} -> j ; l and r tie, break by name
    s = BoundBlock(Const("s", 1.0, out="s"), {"s": "s"})
    left = BoundBlock(Gain("left", 1.0, x="s", y="l"), {"s": "s", "l": "l"})
    right = BoundBlock(Gain("right", 1.0, x="s", y="r"), {"s": "s", "r": "r"})

    class Sum:
        name = "join"
        inputs = ("l", "r")
        outputs = ("j",)

        def step(self, t, dt, inp):
            return {"j": inp["l"] + inp["r"]}

    join = BoundBlock(Sum(), {"l": "l", "r": "r", "j": "j"})
    assert _names(topological_order([join, right, left, s])) == ["s", "left", "right", "join"]


def test_double_driver_raises():
    a = BoundBlock(Const("a", 1.0, out="shared"), {"shared": "shared"})
    b = BoundBlock(Const("b", 2.0, out="shared"), {"shared": "shared"})
    with pytest.raises(SimulationError, match="driven by both"):
        topological_order([a, b])


def test_cycle_raises():
    f = BoundBlock(Gain("f", 1.0, x="q", y="p"), {"q": "q", "p": "p"})
    g = BoundBlock(Gain("g", 1.0, x="p", y="q"), {"p": "p", "q": "q"})
    with pytest.raises(SimulationError, match="cycle"):
        topological_order([f, g])


def test_run_records_traces_and_gain():
    sim = Simulation(dt=0.5, n_steps=3)
    sim.add(Const("src", 2.0, out="a"), {"a": "a"})
    sim.add(Gain("amp", 3.0, x="a", y="b"), {"a": "a", "b": "b"})
    traces = sim.run()
    assert traces["a"] == [2.0, 2.0, 2.0]
    assert traces["b"] == [6.0, 6.0, 6.0]


def test_run_is_stateful_when_block_keeps_state():
    sim = Simulation(dt=1.0, n_steps=4)
    sim.add(Counter("c", out="n"), {"n": "n"})
    assert sim.run()["n"] == [0.0, 1.0, 2.0, 3.0]


def test_undriven_signal_holds_zero():
    class ReadsMissing:
        name = "r"
        inputs = ("ghost",)
        outputs = ("o",)

        def step(self, t, dt, inp):
            return {"o": inp["ghost"] + 5.0}

    sim = Simulation(dt=1.0, n_steps=1)
    sim.add(ReadsMissing(), {"ghost": "ghost", "o": "o"})
    traces = sim.run()
    assert traces["ghost"] == [0.0]
    assert traces["o"] == [5.0]


def test_identity_binding_default():
    sim = Simulation(dt=1.0, n_steps=1)
    sim.add(Const("src", 7.0, out="a"))  # no bindings -> port name == signal name
    assert sim.run()["a"] == [7.0]


def test_unknown_binding_port_raises():
    sim = Simulation(dt=1.0, n_steps=1)
    with pytest.raises(SimulationError, match="unknown port"):
        sim.add(Const("src", 1.0, out="a"), {"nope": "x"})


def test_time_is_index_times_dt():
    seen: list[float] = []

    class Recorder:
        name = "rec"
        inputs = ()
        outputs = ("o",)

        def step(self, t, dt, inp):
            seen.append(t)
            return {"o": 0.0}

    sim = Simulation(dt=0.25, n_steps=4)
    sim.add(Recorder())
    sim.run()
    assert seen == [0.0, 0.25, 0.5, 0.75]
