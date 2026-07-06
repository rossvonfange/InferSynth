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

__all__ = ["Spec", "SpecError", "load_spec"]

_TOP_KEYS = {"frd", "allocations", "profile", "endpoints", "knobs"}
_KNOB_KEYS = {"recall", "allocation", "absorption"}
_ABSORPTION_VALUES = ("off", "conservative", "aggressive")


class SpecError(ValueError):
    """Raised for a structurally invalid or unreadable spec file."""


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

    return Spec(
        path=spec_path,
        frd=frd_path,
        allocations=allocations,
        profile=profile,
        endpoints=endpoints,
        knobs=knobs,
        absorption=absorption,
    )
