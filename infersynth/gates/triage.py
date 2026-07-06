"""ERC triage policy (BUILD_PLAN WP4, KICAD_MCP_PLAYBOOK severity handling).

A :class:`TriagePolicy` is *pure data* — a severity rule plus an allowlist of
ERC codes that pass with a note regardless of severity. It is constructable
from a plain dict (so a project can carry one in JSON/YAML) and decides, for a
list of :class:`ErcViolation`, which violations FAIL the gate and which pass
with an explanatory note.

Default policy: error-severity violations fail; warnings (and exclusions) pass
with a note; the allowlist is empty. Allowlisted codes always pass with a note
even when their severity would otherwise fail — the note records *why* the code
is tolerated, so a silenced error is never silent.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = ["ErcViolation", "TriagePolicy", "TriageOutcome"]


@dataclass(frozen=True)
class ErcViolation:
    """One ERC violation parsed from ``kicad-cli sch erc --format json``."""

    code: str  # the machine key, e.g. "power_pin_not_driven" (json "type")
    severity: str  # "error" | "warning" | "exclusion"
    description: str  # human text, e.g. "Input Power pin not driven by any Output..."
    items: tuple[str, ...] = ()  # affected item descriptions
    sheet: str = ""  # sheet path the violation was reported on

    def one_line(self) -> str:
        where = f" [{'; '.join(self.items)}]" if self.items else ""
        loc = f" @ {self.sheet}" if self.sheet else ""
        return f"{self.severity}:{self.code}: {self.description}{where}{loc}"


@dataclass(frozen=True)
class TriageOutcome:
    """Result of triaging a violation list against a policy."""

    failures: tuple[ErcViolation, ...]
    notes: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.failures


@dataclass(frozen=True)
class TriagePolicy:
    """Data-only ERC severity policy.

    * ``fail_severities``: severities that fail the gate (default ``{"error"}``).
    * ``allow_codes``: ERC codes that always pass with a note, overriding
      severity — each maps to an optional human reason recorded in the note.
    """

    fail_severities: frozenset[str] = frozenset({"error"})
    allow_codes: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def default(cls) -> TriagePolicy:
        """Errors fail, warnings pass-with-note, empty allowlist."""
        return cls()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TriagePolicy:
        """Build a policy from a plain mapping.

        Shape::

            {"fail_severities": ["error"],
             "allow_codes": {"code": "reason", ...}  # or ["code", ...]
            }

        ``allow_codes`` may be a list (reasons default to "allowlisted") or a
        mapping code -> reason.
        """
        fail = data.get("fail_severities")
        fail_set = frozenset(str(s) for s in fail) if fail is not None else frozenset({"error"})
        raw = data.get("allow_codes", {})
        if isinstance(raw, Mapping):
            allow = {str(k): str(v) for k, v in raw.items()}
        else:
            allow = {str(c): "allowlisted" for c in raw}
        return cls(fail_severities=fail_set, allow_codes=allow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fail_severities": sorted(self.fail_severities),
            "allow_codes": dict(self.allow_codes),
        }

    def triage(self, violations: Iterable[ErcViolation]) -> TriageOutcome:
        """Partition *violations* into failures and pass-with-note strings."""
        failures: list[ErcViolation] = []
        notes: list[str] = []
        for v in violations:
            if v.code in self.allow_codes:
                notes.append(f"allowlisted {v.code} ({self.allow_codes[v.code]}): {v.one_line()}")
            elif v.severity in self.fail_severities:
                failures.append(v)
            else:
                notes.append(f"note ({v.severity}): {v.one_line()}")
        return TriageOutcome(failures=tuple(failures), notes=tuple(notes))
