"""Protocol interface classification by TOPOLOGICAL fingerprint — the "known
not guessed" reverse of ``catalog/interfaces.yaml`` (docs/HIERARCHICAL_RECOGNITION.md).

``interfaces.yaml`` (+ :mod:`infersynth.catalog.interfaces`) is the FORWARD data
model: how a cell groups ports into bundles and how two bundles mate. This
module is the REVERSE: given an unlabeled bundle of nets pulled off a design,
which *standard* protocol is it? Standard protocols (SPI/I2C/UART/USB/PCIe/DDR…)
are SPECS, not things to learn — there are ~20 of them and their fingerprints
are fixed — so we encode them once in ``catalog/interface_signatures.yaml`` and
recognize STRUCTURALLY:

  * net cardinality (how many nets in the bundle),
  * differential-pair count (nets sharing a 2-component span / a P/N suffix),
  * point-to-point vs shared-multidrop topology (max net degree),
  * pull-up-to-rail presence (a resistor from a net to a power rail).

Net-name role tokens (SDA/SCL/MOSI…) are CORROBORATING ONLY: they disambiguate
two structurally-identical protocols (usb2 vs can — both one diff pair) and
raise confidence, but they are NEVER the emitted kind. A bundle that matches no
signature, or an ambiguous one names cannot disambiguate, gets an honest generic
kind (``diff_pair`` / ``bus`` / ``signal``) with LOW confidence — not a forced
guess. This is what makes ``interface_kind`` label-independent and robust, fixing
at the root the net-label fragmentation seen downstream.

Pure and deterministic (SELECTION.md §8): no clocks, no randomness, all ties
broken lexicographically — two runs are byte-identical.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from infersynth.recognize.netlist import DesignNetlist

__all__ = [
    "InterfaceSignature",
    "InterfaceSignaturesError",
    "BundleFingerprint",
    "InterfaceMatch",
    "load_interface_signatures",
    "fingerprint_bundle",
    "classify_interface",
    "rail_nets",
]

_TOP_KEYS = {"version", "signatures"}
_SIG_KEYS = {
    "net_count",
    "diff_pairs",
    "topology",
    "pullups",
    "role_hints",
    "base_confidence",
}
_TOPOLOGIES = ("point_to_point", "multidrop", "any")
_PULLUPS = ("expected", "optional", "none")

# Rail-name patterns — a deliberately small duplicate of the segmenter's (kept
# here so this module has NO dependency on segment.py, which imports us). Only
# used to decide "is a resistor's far pin on a rail?" for pull-up detection.
_POWER_RE = re.compile(
    r"(^|[_/])(GND|VSS|VCC|VDD|VEE|VBUS|VTT|VREF|VCCB|AGND|DGND|PGND)([_/]|\d|$)", re.I
)
_VOLT_RE = re.compile(r"(^|[_/])[+-]?\d+P\d+V|\d+V\d+", re.I)
#: A net touching at least this many distinct components reads as a broadcast
#: rail (mirrors segment.RAIL_DEGREE; duplicated to keep this module standalone).
_RAIL_DEGREE = 8

_TOKEN_SPLIT = re.compile(r"[^A-Z0-9]+")
# base + P/N differential suffix (X_P/X_N, XP/XN), base >= 2 chars.
_DIFF_RE = re.compile(r"^(.*?)[_]?([PN])$")

#: Confidence emitted for an honest generic fallback (no signature matched, or
#: an ambiguous match net names could not disambiguate). Low by design.
GENERIC_CONFIDENCE = 0.2
#: Per-role-hit confidence bump (capped) added to a structurally-matched
#: signature when its net names carry its role tokens — more distinct hits ->
#: higher confidence, so a specific match (qspi: QSPI+IO0..3) outranks a
#: shallow one (spi: just SCK+CS) on the same bundle.
CORROBORATION_STEP = 0.1
CORROBORATION_CAP = 0.3
#: Confidence ceiling — a structural + name-corroborated match never claims
#: certainty (a board can always wire a standard bundle unusually).
CONFIDENCE_CEILING = 0.95


class InterfaceSignaturesError(ValueError):
    """Raised when ``interface_signatures.yaml`` is malformed (an authoring bug,
    like :class:`infersynth.catalog.interfaces.InterfacesError`)."""


@dataclass(frozen=True)
class InterfaceSignature:
    """One loaded, validated entry from ``interface_signatures.yaml``."""

    name: str
    net_count: tuple[int, int]
    diff_pairs: tuple[int, int]
    topology: str
    pullups: str
    role_hints: tuple[str, ...]
    base_confidence: float


def _as_range(where: str, val: object, *, nonneg: bool = True) -> tuple[int, int]:
    if not (isinstance(val, list) and len(val) == 2):
        raise InterfaceSignaturesError(f"{where} must be a [lo, hi] list of two ints")
    lo, hi = val
    if not (isinstance(lo, int) and isinstance(hi, int)) or isinstance(lo, bool):
        raise InterfaceSignaturesError(f"{where} bounds must be ints, got {val!r}")
    if lo > hi or (nonneg and lo < 0):
        raise InterfaceSignaturesError(f"{where} must be a valid non-negative range, got {val!r}")
    return (lo, hi)


def load_interface_signatures(path: str | Path) -> dict[str, InterfaceSignature]:
    """Load + validate ``interface_signatures.yaml``. Returns name -> signature."""
    p = Path(path)
    try:
        data = yaml.safe_load(p.read_text())
    except yaml.YAMLError as exc:
        raise InterfaceSignaturesError(f"{p}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise InterfaceSignaturesError(f"{p}: must be a mapping")
    unknown = sorted(set(data) - _TOP_KEYS)
    if unknown:
        raise InterfaceSignaturesError(f"{p}: unknown top-level key(s) {unknown}")
    sigs = data.get("signatures")
    if not isinstance(sigs, dict) or not sigs:
        raise InterfaceSignaturesError(f"{p}: 'signatures' must be a non-empty mapping")

    out: dict[str, InterfaceSignature] = {}
    for name, spec in sigs.items():
        where = f"{p}: signatures.{name}"
        if not isinstance(name, str) or not name:
            raise InterfaceSignaturesError(f"{p}: signature names must be non-empty strings")
        if not isinstance(spec, dict):
            raise InterfaceSignaturesError(f"{where} must be a mapping")
        bad = sorted(set(spec) - _SIG_KEYS)
        if bad:
            raise InterfaceSignaturesError(f"{where}: unknown key(s) {bad}")
        net_count = _as_range(f"{where}.net_count", spec.get("net_count"))
        diff_pairs = _as_range(f"{where}.diff_pairs", spec.get("diff_pairs"))
        topology = spec.get("topology", "any")
        if topology not in _TOPOLOGIES:
            raise InterfaceSignaturesError(
                f"{where}.topology must be one of {_TOPOLOGIES}, got {topology!r}"
            )
        pullups = spec.get("pullups", "optional")
        if pullups not in _PULLUPS:
            raise InterfaceSignaturesError(
                f"{where}.pullups must be one of {_PULLUPS}, got {pullups!r}"
            )
        hints_raw = spec.get("role_hints", []) or []
        if not isinstance(hints_raw, list) or not all(isinstance(h, str) for h in hints_raw):
            raise InterfaceSignaturesError(f"{where}.role_hints must be a list of strings")
        role_hints = tuple(h.upper() for h in hints_raw)
        bc = spec.get("base_confidence", 0.4)
        if not isinstance(bc, (int, float)) or isinstance(bc, bool) or not 0.0 <= bc <= 1.0:
            raise InterfaceSignaturesError(f"{where}.base_confidence must be a float in [0, 1]")
        out[name] = InterfaceSignature(
            name=name,
            net_count=net_count,
            diff_pairs=diff_pairs,
            topology=topology,
            pullups=pullups,
            role_hints=role_hints,
            base_confidence=float(bc),
        )
    return out


def rail_nets(design: DesignNetlist) -> frozenset[str]:
    """The design's rail nets (power/ground name or very high degree) — the same
    definition the segmenter uses, recomputed here so this module stays
    self-contained. Used only for pull-up-to-rail detection."""
    out: set[str] = set()
    for name, pins in design.nets.items():
        if _POWER_RE.search(name) or _VOLT_RE.search(name):
            out.add(name)
            continue
        if len({r for r, _ in pins}) >= _RAIL_DEGREE:
            out.add(name)
    return frozenset(out)


@dataclass(frozen=True)
class BundleFingerprint:
    """The name-agnostic structural fingerprint of a net bundle."""

    net_count: int
    diff_pairs: int
    max_degree: int
    has_pullups: bool

    @property
    def is_multidrop(self) -> bool:
        return self.max_degree >= 3


def _tokens(net_name: str) -> set[str]:
    return {t for t in _TOKEN_SPLIT.split(net_name.upper()) if t}


def _hint_hits(hint: str, tokens: set[str]) -> bool:
    """A role hint matches a token that equals it, or is that hint followed by a
    pure-digit index (``DQ`` matches ``DQ0``, ``GPIO`` matches ``GPIO14``).
    Deliberately NOT a substring test, so ``SCL`` does not match ``SCLK``."""
    for t in tokens:
        if t == hint:
            return True
        if t.startswith(hint) and t[len(hint):].isdigit():
            return True
    return False


def fingerprint_bundle(
    bundle: list[str] | tuple[str, ...],
    design: DesignNetlist,
    *,
    rails: frozenset[str] | None = None,
) -> BundleFingerprint:
    """Compute the structural (name-agnostic) fingerprint of *bundle*.

    ``diff_pairs`` counts complementary ``X_P``/``X_N`` net pairs. Differential
    routing is a physical/intent property that a purely LOGICAL netlist cannot
    otherwise express (two single-ended nets between the same two pins are
    connectivity-identical to a diff pair), so the universal P/N naming
    convention is the honest structural signal for it — and it corroborates,
    never names, the emitted kind. ``max_degree`` (multidrop vs point-to-point)
    and pull-up presence remain fully name-agnostic. Pull-up: a net in the
    bundle carrying a 2-pin resistor (``R*``) whose other pin is on a rail.
    """
    from infersynth.recognize.netlist import ref_class

    if rails is None:
        rails = rail_nets(design)
    names = list(bundle)
    max_degree = 0
    for n in names:
        max_degree = max(max_degree, len({r for r, _ in design.nets.get(n, [])}))

    # differential pairs by the universal X_P/X_N naming convention.
    halves: dict[str, dict[str, str]] = {}
    for n in names:
        m = _DIFF_RE.match(n.upper())
        if m and len(m.group(1)) >= 2:
            halves.setdefault(m.group(1), {})[m.group(2)] = n
    diff_pairs = sum(1 for d in halves.values() if "P" in d and "N" in d)

    # pull-up: a resistor on a bundle net whose OTHER pin lands on a rail. Build
    # the per-resistor pin->net map once (only for resistors touching the bundle)
    # so this stays linear rather than scanning pin_net per pin.
    bundle_resistors = {
        ref
        for n in names
        for ref, _pin in design.nets.get(n, [])
        if ref_class(ref) == "R"
    }
    res_nets: dict[str, set[str]] = {ref: set() for ref in bundle_resistors}
    if bundle_resistors:
        for (ref, _pin), net in design.pin_net.items():
            if ref in res_nets:
                res_nets[ref].add(net)
    has_pullups = any(res_nets[ref] & rails for ref in bundle_resistors)

    return BundleFingerprint(
        net_count=len(names),
        diff_pairs=diff_pairs,
        max_degree=max_degree,
        has_pullups=has_pullups,
    )


@dataclass(frozen=True)
class InterfaceMatch:
    """Result of :func:`classify_interface`."""

    kind: str
    confidence: float
    #: how the kind was reached: ``"topology"`` (structure alone, unambiguous),
    #: ``"name_corroborated"`` (structure + net-name role hints),
    #: ``"label_corroborated"`` (structure + externally-supplied ``net_role``
    #: label tokens that net names alone did NOT supply — see ``label_tokens``),
    #: or ``"generic"`` (honest fallback — no/ambiguous signature match).
    basis: str
    fingerprint: BundleFingerprint = field(
        default_factory=lambda: BundleFingerprint(0, 0, 0, False)
    )
    candidates: tuple[str, ...] = ()


def _generic_kind(fp: BundleFingerprint) -> str:
    if fp.diff_pairs >= 1 and fp.net_count == 2 * fp.diff_pairs:
        return "diff_pair"
    if fp.net_count >= 4 or fp.is_multidrop:
        return "bus"
    return "signal"


def _structural_match(sig: InterfaceSignature, fp: BundleFingerprint) -> bool:
    lo, hi = sig.net_count
    if not lo <= fp.net_count <= hi:
        return False
    lo, hi = sig.diff_pairs
    return lo <= fp.diff_pairs <= hi


def _soft_score(sig: InterfaceSignature, fp: BundleFingerprint) -> float:
    """Base confidence nudged by the SOFT topology / pull-up hints (never a
    reject — a 2-device i2c bus is degree-2 and must still classify)."""
    score = sig.base_confidence
    if sig.topology == "multidrop":
        score += 0.05 if fp.is_multidrop else -0.05
    elif sig.topology == "point_to_point":
        score += 0.05 if not fp.is_multidrop else -0.05
    if sig.pullups == "expected":
        score += 0.05 if fp.has_pullups else -0.05
    return score


def classify_interface(
    bundle: list[str] | tuple[str, ...],
    design: DesignNetlist,
    signatures: dict[str, InterfaceSignature],
    *,
    rails: frozenset[str] | None = None,
    use_names: bool = True,
    label_tokens: dict[str, set[str]] | None = None,
) -> InterfaceMatch:
    """Classify a bundle of nets into a standard protocol kind + confidence.

    Structure first: a bundle is matched against every signature's net-count and
    diff-pair ranges. Among structural matches, the SOFT topology/pull-up hints
    set a base score; then (if *use_names*) net-name role tokens corroborate —
    raising the score and, critically, disambiguating two structurally-identical
    protocols. The emitted kind is always a SIGNATURE NAME (spi/i2c/usb2/…) or an
    honest generic (``diff_pair``/``bus``/``signal``) — NEVER a raw net-label.

    ``label_tokens`` is an optional ``net -> {role_token}`` map of
    externally-supplied functional role tokens (a downstream provider recovers
    them from each part's KiCad SYMBOL when the ``.brd`` import stripped the
    net-name roles). For every net in the bundle the corroboration token set is
    ``_tokens(net_name) ∪ label_tokens[net]`` — so labels can NAME an otherwise
    generically-named bundle. Labels only ADD corroboration tokens; they NEVER
    relax the structural gate (``_structural_match``): a label whose tokens
    corroborate a signature the STRUCTURE contradicts cannot force that match. A
    match won on tokens net names alone did not supply is reported with basis
    ``"label_corroborated"`` (vs ``"name_corroborated"``) so provenance shows
    WHERE the evidence came from. With ``label_tokens=None`` this is byte-
    identical to the pure net-name path.

    Determinism: signatures are considered in sorted-name order and every tie is
    broken lexicographically, so two runs are byte-identical.
    """
    fp = fingerprint_bundle(bundle, design, rails=rails)

    matched = [
        (name, signatures[name])
        for name in sorted(signatures)
        if _structural_match(signatures[name], fp)
    ]
    if not matched:
        return InterfaceMatch(_generic_kind(fp), GENERIC_CONFIDENCE, "generic", fp, ())

    # name_tokens come from the net names; tokens folds in the externally-supplied
    # net_role label tokens on top. name_tokens is kept so a corroborated winner
    # can be attributed: names-only -> "name_corroborated", label-supplied ->
    # "label_corroborated".
    name_tokens: set[str] = set()
    if use_names:
        for n in bundle:
            name_tokens |= _tokens(n)
    tokens = set(name_tokens)
    if label_tokens:
        for n in bundle:
            tokens |= label_tokens.get(n, set())

    scored: list[tuple[float, int, str]] = []  # (final_score, n_hits, kind)
    for name, sig in matched:
        base = _soft_score(sig, fp)
        n_hits = sum(1 for h in sig.role_hints if _hint_hits(h, tokens)) if tokens else 0
        final = base + min(n_hits * CORROBORATION_STEP, CORROBORATION_CAP)
        scored.append((final, n_hits, name))
    cand_names = tuple(name for _s, _h, name in sorted(scored, key=lambda t: t[2]))

    corroborated = [s for s in scored if s[1] > 0]
    if corroborated:
        # net names (∪ labels) pin down the protocol: MOST role hits wins (the
        # most specific match), then score, then lexical — so a deep, specific
        # corroboration (qspi's QSPI+IO0..3) beats a shallow overlap (spi's
        # SCK+CS).
        corroborated.sort(key=lambda t: (-t[1], -t[0], t[2]))
        best_score, best_hits, best_kind = corroborated[0]
        # attribute the evidence: if net names ALONE corroborate the winner as
        # strongly, it is a name match; otherwise the label tokens supplied the
        # deciding role(s) -> label_corroborated (honest provenance).
        name_hits = sum(1 for h in signatures[best_kind].role_hints if _hint_hits(h, name_tokens))
        basis = "name_corroborated" if name_hits >= best_hits else "label_corroborated"
        return InterfaceMatch(
            best_kind, round(min(best_score, CONFIDENCE_CEILING), 4),
            basis, fp, cand_names,
        )

    # No name corroboration. If EXACTLY ONE signature matches the structure, that
    # is an unambiguous topological match; emit it. If several standard protocols
    # share this fingerprint (e.g. usb2 vs can — both one diff pair; jtag vs sdio
    # — both 4 nets), we cannot honestly choose without names, so we emit a
    # GENERIC kind rather than force a guess. The soft topology/pull-up nudges
    # tune confidence but NEVER break a real protocol tie on their own.
    if len(scored) == 1:
        score, _h, kind = scored[0]
        return InterfaceMatch(
            kind, round(min(max(score, GENERIC_CONFIDENCE), CONFIDENCE_CEILING), 4),
            "topology", fp, cand_names,
        )
    return InterfaceMatch(_generic_kind(fp), GENERIC_CONFIDENCE, "generic", fp, cand_names)
