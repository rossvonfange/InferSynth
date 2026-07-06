"""IR construction + elaboration tests (happy paths and failure cases)."""

import pytest

from infersynth.ir import (
    BOUNDARY,
    Cell,
    Design,
    ElaborationError,
    IRError,
    Param,
    Port,
    PortDirection,
    PortKind,
    elaborate,
)


def opamp_cell() -> Cell:
    return Cell(
        "opamp-gain-noninverting",
        ports=[
            Port("in", PortDirection.IN),
            Port("out", PortDirection.OUT),
            Port("vcc", PortDirection.PASSIVE, PortKind.POWER),
            Port("gnd", PortDirection.PASSIVE, PortKind.POWER),
        ],
        params=[
            Param("gain", "float", range=(1.0, 1000.0)),
            Param("bandwidth_hz", "float", default=1e3, range=(1.0, 1e7)),
        ],
        idioms={"keywords": ["non-inverting amplifier"]},
    )


def reg_cell() -> Cell:
    return Cell(
        "linear-reg-fixed",
        ports=[
            Port("vin", PortDirection.IN, PortKind.POWER),
            Port("vout", PortDirection.OUT, PortKind.POWER),
            Port("gnd", PortDirection.PASSIVE, PortKind.POWER),
        ],
        params=[Param("vout_v", "float", default=5.0, allowed=(3.3, 5.0, 12.0))],
    )


def simple_design() -> Design:
    d = Design(
        "top",
        ports=[
            Port("VIN_12V", PortDirection.IN, PortKind.POWER),
            Port("OUT", PortDirection.OUT),
            Port("GND", PortDirection.PASSIVE, PortKind.POWER),
        ],
    )
    d.add_instance("reg1", reg_cell(), params={"vout_v": 5.0})
    d.add_instance("amp1", opamp_cell(), params={"gain": 100.0})
    d.connect("VIN_12V", (BOUNDARY, "VIN_12V"), ("reg1", "vin"))
    d.connect("V5", ("reg1", "vout"), ("amp1", "vcc"))
    d.connect("SIG_OUT", ("amp1", "out"), (BOUNDARY, "OUT"))
    d.connect("SIG_IN", ("amp1", "in"))  # single-pin net: legal
    d.connect("GND", (BOUNDARY, "GND"), ("reg1", "gnd"), ("amp1", "gnd"))
    return d


class TestConstruction:
    def test_duplicate_port_rejected(self):
        with pytest.raises(IRError, match="duplicate port"):
            Cell("c", ports=[Port("a"), Port("a")])

    def test_param_default_must_satisfy_range(self):
        with pytest.raises(IRError, match="default"):
            Param("gain", "float", default=0.5, range=(1.0, 1000.0))

    def test_range_and_allowed_exclusive(self):
        with pytest.raises(IRError, match="mutually exclusive"):
            Param("x", "int", range=(0, 1), allowed=(0, 1))

    def test_connect_unknown_port_rejected(self):
        d = Design("top")
        d.add_instance("u1", opamp_cell(), params={"gain": 2.0})
        with pytest.raises(IRError, match="no port"):
            d.connect("n1", ("u1", "nope"))

    def test_connect_unknown_instance_rejected(self):
        d = Design("top")
        with pytest.raises(IRError, match="unknown instance"):
            d.connect("n1", ("ghost", "out"))

    def test_instance_name_no_slash(self):
        d = Design("top")
        with pytest.raises(IRError, match="hierarchy separator"):
            d.add_instance("a/b", opamp_cell())

    def test_subdesign_instance_takes_no_params(self):
        d = Design("top")
        with pytest.raises(IRError, match="do not take parameter bindings"):
            d.add_instance("sub", Design("child"), params={"x": 1})


class TestElaborateFlat:
    def test_happy_path_partition_and_params(self):
        elab = elaborate(simple_design())
        assert list(elab.instances) == ["amp1", "reg1"]  # sorted
        assert elab.instances["amp1"].params == {"bandwidth_hz": 1e3, "gain": 100.0}
        assert elab.instances["reg1"].params == {"vout_v": 5.0}
        part = elab.partition()
        assert part["GND"] == frozenset({("", "GND"), ("reg1", "gnd"), ("amp1", "gnd")})
        assert part["V5"] == frozenset({("reg1", "vout"), ("amp1", "vcc")})
        assert part["VIN_12V"] == frozenset({("", "VIN_12V"), ("reg1", "vin")})
        assert part["SIG_IN"] == frozenset({("amp1", "in")})
        # net names deterministic + sorted
        assert list(elab.nets) == sorted(elab.nets)

    def test_deterministic(self):
        a, b = elaborate(simple_design()), elaborate(simple_design())
        assert a.partition() == b.partition()
        assert list(a.instances) == list(b.instances)

    def test_unconnected_required_port_fails(self):
        d = Design("top")
        d.add_instance("amp1", opamp_cell(), params={"gain": 2.0})
        d.connect("n1", ("amp1", "in"))
        d.connect("n2", ("amp1", "out"))
        d.connect("gnd", ("amp1", "gnd"))
        with pytest.raises(ElaborationError, match=r"unconnected required port amp1\.vcc"):
            elaborate(d)

    def test_optional_port_may_float(self):
        c = Cell("c", ports=[Port("a", PortDirection.IN), Port("nc", required=False)])
        d = Design("top")
        d.add_instance("u1", c)
        d.connect("n1", ("u1", "a"))
        elaborate(d)  # no error

    def test_param_out_of_range_fails(self):
        d = Design("top")
        d.add_instance("amp1", opamp_cell(), params={"gain": 5000.0})
        d.connect("i", ("amp1", "in"))
        d.connect("o", ("amp1", "out"))
        d.connect("p", ("amp1", "vcc"))
        d.connect("g", ("amp1", "gnd"))
        with pytest.raises(ElaborationError, match="above maximum"):
            elaborate(d)

    def test_param_missing_no_default_fails(self):
        d = Design("top")
        d.add_instance("amp1", opamp_cell())  # gain has no default
        d.connect("i", ("amp1", "in"))
        d.connect("o", ("amp1", "out"))
        d.connect("p", ("amp1", "vcc"))
        d.connect("g", ("amp1", "gnd"))
        with pytest.raises(ElaborationError, match="no binding and no default"):
            elaborate(d)

    def test_unknown_param_fails(self):
        d = Design("top")
        d.add_instance("amp1", opamp_cell(), params={"gain": 2.0, "wat": 1})
        d.connect("i", ("amp1", "in"))
        d.connect("o", ("amp1", "out"))
        d.connect("p", ("amp1", "vcc"))
        d.connect("g", ("amp1", "gnd"))
        with pytest.raises(ElaborationError, match="unknown parameter 'wat'"):
            elaborate(d)

    def test_two_outputs_on_net_is_direction_conflict(self):
        d = Design("top")
        d.add_instance("a", opamp_cell(), params={"gain": 2.0})
        d.add_instance("b", opamp_cell(), params={"gain": 3.0})
        d.connect("clash", ("a", "out"), ("b", "out"))
        d.connect("ia", ("a", "in"))
        d.connect("ib", ("b", "in"))
        d.connect("p", ("a", "vcc"), ("b", "vcc"))
        d.connect("g", ("a", "gnd"), ("b", "gnd"))
        with pytest.raises(ElaborationError, match="direction conflict"):
            elaborate(d)

    def test_port_on_two_nets_fails(self):
        d = Design("top")
        d.add_instance("amp1", opamp_cell(), params={"gain": 2.0})
        d.connect("n1", ("amp1", "out"))
        d.connect("n2", ("amp1", "out"))
        d.connect("i", ("amp1", "in"))
        d.connect("p", ("amp1", "vcc"))
        d.connect("g", ("amp1", "gnd"))
        with pytest.raises(ElaborationError, match="multiple nets"):
            elaborate(d)

    def test_unconnected_top_boundary_port_fails(self):
        d = Design("top", ports=[Port("VIN", PortDirection.IN)])
        with pytest.raises(ElaborationError, match="unconnected required boundary port"):
            elaborate(d)


class TestElaborateHierarchy:
    def make_child(self) -> Design:
        child = Design(
            "gain-block",
            ports=[
                Port("sig_in", PortDirection.IN),
                Port("sig_out", PortDirection.OUT),
                Port("pwr", PortDirection.PASSIVE, PortKind.POWER),
                Port("gnd", PortDirection.PASSIVE, PortKind.POWER),
            ],
        )
        child.add_instance("u1", opamp_cell(), params={"gain": 10.0})
        child.connect("in", (BOUNDARY, "sig_in"), ("u1", "in"))
        child.connect("out", ("u1", "out"), (BOUNDARY, "sig_out"))
        child.connect("pwr", (BOUNDARY, "pwr"), ("u1", "vcc"))
        child.connect("gnd", (BOUNDARY, "gnd"), ("u1", "gnd"))
        return child

    def make_top(self) -> Design:
        top = Design(
            "top",
            ports=[
                Port("IN", PortDirection.IN),
                Port("OUT", PortDirection.OUT),
                Port("V5", PortDirection.PASSIVE, PortKind.POWER),
                Port("GND", PortDirection.PASSIVE, PortKind.POWER),
            ],
        )
        child = self.make_child()
        top.add_instance("stage1", child)
        top.add_instance("stage2", child)
        top.connect("IN", (BOUNDARY, "IN"), ("stage1", "sig_in"))
        top.connect("MID", ("stage1", "sig_out"), ("stage2", "sig_in"))
        top.connect("OUT", ("stage2", "sig_out"), (BOUNDARY, "OUT"))
        top.connect("V5", (BOUNDARY, "V5"), ("stage1", "pwr"), ("stage2", "pwr"))
        top.connect("GND", (BOUNDARY, "GND"), ("stage1", "gnd"), ("stage2", "gnd"))
        return top

    def test_flattening_paths_and_net_merge(self):
        elab = elaborate(self.make_top())
        assert list(elab.instances) == ["stage1/u1", "stage2/u1"]
        part = elab.partition()
        # nets cross the hierarchy: the child's internal net merged with parent's
        assert part["IN"] == frozenset({("", "IN"), ("stage1/u1", "in")})
        assert part["MID"] == frozenset({("stage1/u1", "out"), ("stage2/u1", "in")})
        assert part["V5"] == frozenset(
            {("", "V5"), ("stage1/u1", "vcc"), ("stage2/u1", "vcc")}
        )
        # merged nets take the shallowest name: parent "V5", not "stage1/pwr"
        assert "stage1/pwr" not in part

    def test_unconnected_subdesign_boundary_port_fails(self):
        top = Design("top")
        top.add_instance("stage1", self.make_child())
        top.connect("a", ("stage1", "sig_in"))
        top.connect("b", ("stage1", "sig_out"))
        top.connect("p", ("stage1", "pwr"))
        # gnd left unconnected
        with pytest.raises(ElaborationError, match=r"stage1\.gnd"):
            elaborate(top)

    def test_recursive_instantiation_fails(self):
        d = Design("ouroboros")
        d.add_instance("me", d)
        with pytest.raises(ElaborationError, match="recursive instantiation"):
            elaborate(d)

    def test_domain_annotation_carried(self):
        top = self.make_top()
        top.nets["MID"].domain = "audio"
        elab = elaborate(top)
        assert elab.domains == {"MID": "audio"}
