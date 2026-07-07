"""Formal-spec loader (BUILD_PLAN WP-L1 item 1; SELECTION.md §2).

SELECTION §2 is explicit that an allocation "lives in the FORMAL SPEC, never
in FRD prose." Nothing in the repo parsed a whole formal-spec file before
this module — :mod:`infersynth.match.allocation` deliberately parses *only*
the ``allocations:`` key out of a bare mapping and says a real loader should
"call this same parser for that key" when one lands. This is that loader.

Spec file format v0 (``spec.yaml``), keys (strict — unknown top-level keys
are a :class:`SpecError`):

* ``frd`` — path to the FRD, resolved relative to the spec file's own
  directory (a spec and its FRD are expected to travel together). Optional;
  ``--frd`` on the CLI overrides it (see ``infersynth/cli.py``).
* ``allocations`` — the exact list schema
  :func:`infersynth.match.allocation.allocations_from_spec` already parses
  (``{at, allow, deny}`` entries). Reused verbatim, not reimplemented.
* ``profile`` — a named weight profile (``"prototype"`` / ``"production"`` /
  ``"hobbyist"``) or an inline ``{dim: weight, ...}`` mapping, i.e. exactly
  what :func:`infersynth.decide.profiles.load_profile` accepts. ``--profile``/
  ``--weights`` on the CLI override it.
* ``endpoints`` — optional ``{requirement_id: {inputs: [...], outputs:
  [...]}}``, matching :class:`infersynth.match.propagate.EndpointSpec`'s
  constructor fields exactly (signal-kind name lists).
* ``knobs`` — optional ``{recall, allocation, absorption}`` string knobs.
  ``recall``/``allocation`` map straight onto
  :class:`infersynth.match.knobs.MatchKnobs` (whose own validation runs, so an
  invalid value fails loudly). ``absorption`` is accepted and validated
  (``off`` / ``conservative`` / ``aggressive``, SELECTION §5) but not yet
  wired into a match/pack call — the packer (WP-P1) is what will consume it;
  it is parsed here now so a spec author doesn't have to touch the file again
  once that lands.

This is deliberately minimal (SELECTION §2: "keep it minimal, no requirement
re-statement") — it is a steering artifact, not a second FRD.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from infersynth.match.allocation import AllocationError, AllocationTable, allocations_from_spec
from infersynth.match.knobs import MatchKnobs
from infersynth.match.propagate import EndpointSpec

__all__ = [
    "Spec",
    "SpecError",
    "FeedEdge",
    "RailBind",
    "TbStimulus",
    "TbCheck",
    "DesignTestbench",
    "load_spec",
]

_TOP_KEYS = {
    "frd", "allocations", "profile", "endpoints", "knobs",
    "feeds", "pins", "forbid_pack", "rail_aliases", "rail_binds", "testbench",
}
_KNOB_KEYS = {"recall", "allocation", "absorption"}
_ABSORPTION_VALUES = ("off", "conservative", "aggressive")

# --- design-simulation testbench (SEED_PLAN §1 crit 3; docs/SIM.md design tier) ---
# Strict schema: each stimulus/check kind declares exactly its required and
# optional keys; any unknown kind or key is a SpecError. Net names refer to the
# synthesized WiringPlan's net names (rails by rail name; feed nets by their
# f_SRC_DST[_ROLE] name; the SYNTHESIS.md "Wired nets" table is how an author
# discovers them). Values are coerced to float/int and validated here so the
# design-simulation gate can trust the loaded testbench.
_STIMULUS_KEYS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    # kind: (required keys, optional keys)  -- "signal" + "kind" are implicit
    "sine": (frozenset({"amplitude", "freq_hz"}), frozenset({"offset", "phase"})),
    "dc": (frozenset({"value"}), frozenset()),
}
_CHECK_KEYS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "amplitude_ratio": (
        frozenset({"input", "output", "expected", "tol_pct"}),
        frozenset(),
    ),
    "settles_to": (
        frozenset({"signal", "value", "tol"}),
        frozenset({"after_step"}),
    ),
    "clipped_within": (
        frozenset({"signal", "lo", "hi"}),
        frozenset({"eps"}),
    ),
}


class SpecError(ValueError):
    """Raised for a structurally invalid or unreadable spec file."""


@dataclass(frozen=True)
class FeedEdge:
    """One declared inter-requirement dataflow edge (NETFLOW.md ``feeds``).

    ``src`` feeds ``dst`` (both requirement ids); ``dst_port`` is the optional
    port/role qualifier on the destination (``None`` when unqualified). Declared
    feeds are pass-through in this stage (NETFLOW build step 2) — they are
    surfaced in the report, never yet wired.
    """

    src: str
    dst: str
    dst_port: str | None = None


@dataclass(frozen=True)
class RailBind:
    """One per-requirement port→rail binding (NETFLOW ``rail_binds``).

    Ties requirement ``at``'s cell port ``port`` onto rail net ``rail``,
    OVERRIDING name-based rail grouping and ``rail_aliases`` for that exact
    (requirement, port). This is what makes a *series* power chain expressible:
    two ``VOUT`` ports on different requirements can be pinned to different
    rails (a protection stage's raw ``VOUT`` vs. a regulator's regulated
    ``VOUT``) even though name-based grouping would otherwise merge them.
    """

    at: str
    port: str
    rail: str


@dataclass(frozen=True)
class TbStimulus:
    """One design-sim stimulus: a source ``kind`` driving a WiringPlan net.

    ``params`` holds the kind's numeric fields (sine: ``amplitude``/``freq_hz``
    (+optional ``offset``/``phase``); dc: ``value``), already coerced to float.
    """

    signal: str
    kind: str
    params: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class TbCheck:
    """One design-sim assertion over the composed traces.

    ``kind`` is ``amplitude_ratio`` / ``settles_to`` / ``clipped_within``;
    ``params`` holds that kind's fields (net names as strings, thresholds as
    numbers). The gate maps each to an :mod:`infersynth.sim.checks` helper.
    """

    kind: str
    params: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class DesignTestbench:
    """A whole-design behavioral testbench (spec ``testbench:`` key, v0).

    ``rails`` maps every rail net name to a DC voltage (a rail net absent here
    is an error at build time). ``dt``/``n_steps`` set the composed run; a
    multi-rate chain must pick ``dt`` small enough for its fastest pole (see the
    BridgeSense testbench note). ``stimuli`` drive nets; ``checks`` assert over
    the recorded traces.
    """

    rails: dict[str, float]
    dt: float
    n_steps: int
    stimuli: tuple[TbStimulus, ...] = ()
    checks: tuple[TbCheck, ...] = ()


@dataclass(frozen=True)
class Spec:
    """A loaded ``spec.yaml`` (formal-spec artifact, WP-L1)."""

    path: Path
    frd: Path | None = None
    allocations: AllocationTable = field(default_factory=AllocationTable)
    profile: str | dict | None = None
    endpoints: dict[str, EndpointSpec] = field(default_factory=dict)
    knobs: MatchKnobs = field(default_factory=MatchKnobs)
    #: SELECTION §5 packer knob; parsed but not yet consumed (WP-P1 not landed).
    absorption: str | None = None
    #: NETFLOW declared dataflow edges (spec-file + compiled-pragma, merged).
    feeds: tuple[FeedEdge, ...] = ()
    #: NETFLOW pinned cells: requirement id -> ``library/cell[@version]`` ref.
    pins: dict[str, str] = field(default_factory=dict)
    #: NETFLOW per-requirement pack forbid (SELECTION §5); requirement ids.
    forbid_pack: frozenset[str] = frozenset()
    #: NETFLOW rail aliasing: alias rail name -> canonical rail name (e.g. VCC -> VOUT)
    rail_aliases: dict[str, str] = field(default_factory=dict)
    #: NETFLOW per-requirement rail bindings (override name/alias grouping).
    rail_binds: tuple[RailBind, ...] = ()

    def rail_binds_mapping(self) -> dict[tuple[str, str], str]:
        """The rail binds as the ``{(requirement_id, port): rail}`` mapping the
        rail resolver consumes (NETFLOW ``rail_binds`` threading)."""
        return {(b.at, b.port): b.rail for b in self.rail_binds}
    #: design-simulation testbench (SEED_PLAN §1 crit 3); None when unspecified.
    testbench: DesignTestbench | None = None

    def netflow_mapping(self) -> dict[str, object]:
        """The three NETFLOW keys as a YAML-round-trippable mapping (dump side).

        Emits only non-empty sections so a spec without netflow intent stays
        minimal. Inverse of the ``feeds``/``pins``/``forbid_pack`` loaders.
        """
        out: dict[str, object] = {}
        if self.feeds:
            out["feeds"] = [
                {"src": e.src, "dst": e.dst, **({"dst_port": e.dst_port} if e.dst_port else {})}
                for e in self.feeds
            ]
        if self.pins:
            out["pins"] = dict(self.pins)
        if self.rail_aliases:
            out["rail_aliases"] = dict(sorted(self.rail_aliases.items()))
        if self.rail_binds:
            out["rail_binds"] = [
                {"at": b.at, "port": b.port, "rail": b.rail} for b in self.rail_binds
            ]
        if self.forbid_pack:
            out["forbid_pack"] = sorted(self.forbid_pack)
        return out


def _require_mapping(value: Any, where: str) -> dict:
    if not isinstance(value, dict):
        raise SpecError(f"{where} must be a mapping")
    return value


def _load_endpoints(raw: Any, where: str) -> dict[str, EndpointSpec]:
    if raw is None:
        return {}
    mapping = _require_mapping(raw, where)
    endpoints: dict[str, EndpointSpec] = {}
    for req_id, entry in mapping.items():
        if not isinstance(req_id, str) or not req_id:
            raise SpecError(f"{where}: keys must be non-empty requirement id strings")
        entry = _require_mapping(entry, f"{where}[{req_id!r}]")
        unknown = sorted(set(entry) - {"inputs", "outputs"})
        if unknown:
            raise SpecError(f"{where}[{req_id!r}]: unknown key(s) {unknown}")
        kwargs: dict[str, tuple[str, ...]] = {}
        for key in ("inputs", "outputs"):
            if key not in entry:
                continue
            value = entry[key]
            if not (isinstance(value, list) and all(isinstance(v, str) and v for v in value)):
                raise SpecError(f"{where}[{req_id!r}].{key} must be a list of non-empty strings")
            kwargs[key] = tuple(value)
        endpoints[req_id] = EndpointSpec(**kwargs)
    return endpoints


def _load_knobs(raw: Any, where: str) -> tuple[MatchKnobs, str | None]:
    if raw is None:
        return MatchKnobs(), None
    mapping = _require_mapping(raw, where)
    unknown = sorted(set(mapping) - _KNOB_KEYS)
    if unknown:
        raise SpecError(f"{where}: unknown key(s) {unknown}")
    match_kwargs = {k: mapping[k] for k in ("recall", "allocation") if k in mapping}
    try:
        knobs = MatchKnobs(**match_kwargs)
    except ValueError as exc:
        raise SpecError(f"{where}: {exc}") from exc
    absorption = mapping.get("absorption")
    if absorption is not None and absorption not in _ABSORPTION_VALUES:
        raise SpecError(
            f"{where}.absorption must be one of {_ABSORPTION_VALUES}, got {absorption!r}"
        )
    return knobs, absorption


def _load_feeds(raw: Any, where: str) -> tuple[FeedEdge, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise SpecError(f"{where} must be a list of {{src, dst[, dst_port]}} mappings")
    edges: list[FeedEdge] = []
    for i, entry in enumerate(raw):
        entry = _require_mapping(entry, f"{where}[{i}]")
        unknown = sorted(set(entry) - {"src", "dst", "dst_port"})
        if unknown:
            raise SpecError(f"{where}[{i}]: unknown key(s) {unknown}")
        for key in ("src", "dst"):
            if not (isinstance(entry.get(key), str) and entry[key]):
                raise SpecError(f"{where}[{i}].{key} must be a non-empty string")
        port = entry.get("dst_port")
        if port is not None and not (isinstance(port, str) and port):
            raise SpecError(f"{where}[{i}].dst_port must be a non-empty string when present")
        edges.append(FeedEdge(src=entry["src"], dst=entry["dst"], dst_port=port))
    return tuple(edges)


def _load_pins(raw: Any, where: str) -> dict[str, str]:
    if raw is None:
        return {}
    mapping = _require_mapping(raw, where)
    pins: dict[str, str] = {}
    for req_id, ref in mapping.items():
        if not (isinstance(req_id, str) and req_id):
            raise SpecError(f"{where}: keys must be non-empty requirement id strings")
        if not (isinstance(ref, str) and ref):
            raise SpecError(f"{where}[{req_id!r}] must be a non-empty cell-ref string")
        pins[req_id] = ref
    return pins


def _load_forbid_pack(raw: Any, where: str) -> frozenset[str]:
    if raw is None:
        return frozenset()
    if not (isinstance(raw, list) and all(isinstance(v, str) and v for v in raw)):
        raise SpecError(f"{where} must be a list of non-empty requirement id strings")
    return frozenset(raw)


def _load_rail_aliases(raw: Any, where: str) -> dict[str, str]:
    if raw is None:
        return {}
    mapping = _require_mapping(raw, where)
    aliases: dict[str, str] = {}
    for alias, target in mapping.items():
        if not (isinstance(alias, str) and alias and isinstance(target, str) and target):
            raise SpecError(f"{where}: entries must be non-empty rail-name strings (alias: target)")
        if alias == target:
            raise SpecError(f"{where}[{alias!r}]: a rail cannot alias itself")
        aliases[alias] = target
    return aliases


def _load_rail_binds(raw: Any, where: str) -> tuple[RailBind, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise SpecError(f"{where} must be a list of {{at, port, rail}} mappings")
    binds: list[RailBind] = []
    seen: set[tuple[str, str]] = set()
    for i, entry in enumerate(raw):
        entry = _require_mapping(entry, f"{where}[{i}]")
        unknown = sorted(set(entry) - {"at", "port", "rail"})
        if unknown:
            raise SpecError(f"{where}[{i}]: unknown key(s) {unknown}")
        for key in ("at", "port", "rail"):
            if not (isinstance(entry.get(key), str) and entry[key]):
                raise SpecError(f"{where}[{i}].{key} must be a non-empty string")
        key = (entry["at"], entry["port"])
        if key in seen:
            raise SpecError(
                f"{where}[{i}]: duplicate binding for (at={key[0]!r}, port={key[1]!r})"
            )
        seen.add(key)
        binds.append(RailBind(at=entry["at"], port=entry["port"], rail=entry["rail"]))
    return tuple(binds)
def _as_number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SpecError(f"{where} must be a number, got {value!r}")
    return float(value)


def _load_tb_stimulus(entry: Any, where: str) -> TbStimulus:
    entry = _require_mapping(entry, where)
    if not (isinstance(entry.get("signal"), str) and entry["signal"]):
        raise SpecError(f"{where}.signal must be a non-empty net-name string")
    kind = entry.get("kind")
    if kind not in _STIMULUS_KEYS:
        raise SpecError(
            f"{where}.kind must be one of {sorted(_STIMULUS_KEYS)}, got {kind!r}"
        )
    required, optional = _STIMULUS_KEYS[kind]
    body = set(entry) - {"signal", "kind"}
    missing = sorted(required - body)
    if missing:
        raise SpecError(f"{where}: {kind} stimulus missing key(s) {missing}")
    unknown = sorted(body - required - optional)
    if unknown:
        raise SpecError(f"{where}: {kind} stimulus has unknown key(s) {unknown}")
    params = {k: _as_number(entry[k], f"{where}.{k}") for k in (required | optional) & body}
    return TbStimulus(signal=entry["signal"], kind=kind, params=params)


def _load_tb_check(entry: Any, where: str) -> TbCheck:
    entry = _require_mapping(entry, where)
    kind = entry.get("kind")
    if kind not in _CHECK_KEYS:
        raise SpecError(
            f"{where}.kind must be one of {sorted(_CHECK_KEYS)}, got {kind!r}"
        )
    required, optional = _CHECK_KEYS[kind]
    body = set(entry) - {"kind"}
    missing = sorted(required - body)
    if missing:
        raise SpecError(f"{where}: {kind} check missing key(s) {missing}")
    unknown = sorted(body - required - optional)
    if unknown:
        raise SpecError(f"{where}: {kind} check has unknown key(s) {unknown}")
    # net-name fields stay strings; everything else is numeric.
    string_fields = {"input", "output", "signal"}
    params: dict[str, object] = {}
    for k in (required | optional) & body:
        if k in string_fields:
            if not (isinstance(entry[k], str) and entry[k]):
                raise SpecError(f"{where}.{k} must be a non-empty net-name string")
            params[k] = entry[k]
        elif k == "after_step":
            if isinstance(entry[k], bool) or not isinstance(entry[k], int):
                raise SpecError(f"{where}.after_step must be an integer")
            params[k] = int(entry[k])
        else:
            params[k] = _as_number(entry[k], f"{where}.{k}")
    return TbCheck(kind=kind, params=params)


def _load_testbench(raw: Any, where: str) -> DesignTestbench | None:
    if raw is None:
        return None
    mapping = _require_mapping(raw, where)
    unknown = sorted(set(mapping) - {"rails", "dt", "n_steps", "stimuli", "checks"})
    if unknown:
        raise SpecError(f"{where}: unknown key(s) {unknown}")
    for key in ("rails", "dt", "n_steps"):
        if key not in mapping:
            raise SpecError(f"{where}: required key {key!r} is missing")
    rails_raw = _require_mapping(mapping["rails"], f"{where}.rails")
    rails: dict[str, float] = {}
    for name, volts in rails_raw.items():
        if not (isinstance(name, str) and name):
            raise SpecError(f"{where}.rails: keys must be non-empty rail-name strings")
        rails[name] = _as_number(volts, f"{where}.rails[{name!r}]")
    dt = _as_number(mapping["dt"], f"{where}.dt")
    if dt <= 0.0:
        raise SpecError(f"{where}.dt must be > 0")
    n_steps_raw = mapping["n_steps"]
    if isinstance(n_steps_raw, bool) or not isinstance(n_steps_raw, int) or n_steps_raw <= 0:
        raise SpecError(f"{where}.n_steps must be a positive integer")
    stimuli_raw = mapping.get("stimuli") or []
    if not isinstance(stimuli_raw, list):
        raise SpecError(f"{where}.stimuli must be a list")
    stimuli = tuple(
        _load_tb_stimulus(e, f"{where}.stimuli[{i}]") for i, e in enumerate(stimuli_raw)
    )
    checks_raw = mapping.get("checks") or []
    if not isinstance(checks_raw, list):
        raise SpecError(f"{where}.checks must be a list")
    checks = tuple(
        _load_tb_check(e, f"{where}.checks[{i}]") for i, e in enumerate(checks_raw)
    )
    return DesignTestbench(
        rails=rails, dt=dt, n_steps=int(n_steps_raw), stimuli=stimuli, checks=checks
    )


def load_spec(path: str | Path) -> Spec:
    """Load and validate a ``spec.yaml`` file. Raises :class:`SpecError`."""
    spec_path = Path(path)
    if not spec_path.is_file():
        raise SpecError(f"spec file not found: {spec_path}")
    try:
        raw = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SpecError(f"{spec_path}: invalid YAML: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise SpecError(f"{spec_path}: spec must be a mapping")
    unknown = sorted(set(raw) - _TOP_KEYS)
    if unknown:
        raise SpecError(f"{spec_path}: unknown top-level key(s) {unknown}")

    frd_value = raw.get("frd")
    frd_path: Path | None = None
    if frd_value is not None:
        if not isinstance(frd_value, str) or not frd_value:
            raise SpecError(f"{spec_path}: frd must be a non-empty string")
        candidate = Path(frd_value)
        frd_path = candidate if candidate.is_absolute() else (spec_path.parent / candidate)

    try:
        allocations = allocations_from_spec(raw)
    except AllocationError as exc:
        raise SpecError(f"{spec_path}: {exc}") from exc

    profile = raw.get("profile")
    if profile is not None and not isinstance(profile, (str, dict)):
        raise SpecError(f"{spec_path}: profile must be a string name or a weights mapping")

    endpoints = _load_endpoints(raw.get("endpoints"), f"{spec_path}: endpoints")
    knobs, absorption = _load_knobs(raw.get("knobs"), f"{spec_path}: knobs")
    feeds = _load_feeds(raw.get("feeds"), f"{spec_path}: feeds")
    pins = _load_pins(raw.get("pins"), f"{spec_path}: pins")
    forbid_pack = _load_forbid_pack(raw.get("forbid_pack"), f"{spec_path}: forbid_pack")
    rail_aliases = _load_rail_aliases(raw.get("rail_aliases"), f"{spec_path}: rail_aliases")
    rail_binds = _load_rail_binds(raw.get("rail_binds"), f"{spec_path}: rail_binds")
    testbench = _load_testbench(raw.get("testbench"), f"{spec_path}: testbench")

    return Spec(
        path=spec_path,
        frd=frd_path,
        allocations=allocations,
        profile=profile,
        endpoints=endpoints,
        knobs=knobs,
        absorption=absorption,
        feeds=feeds,
        pins=pins,
        forbid_pack=forbid_pack,
        rail_aliases=rail_aliases,
        rail_binds=rail_binds,
        testbench=testbench,
    )
