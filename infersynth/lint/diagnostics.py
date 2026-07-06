"""LSP-shaped diagnostics (UX.md: the future language server is a thin adapter).

The :class:`Diagnostic` mirrors the LSP ``Diagnostic`` structure: zero-based
positions, severity enum with LSP numeric values, string code, optional
quickfix hint (the seed of an LSP code action). ``to_lsp()`` returns the
wire-shape dict; ``format_cli()`` renders the one-based human form used by
``infersynth lint``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

__all__ = ["Severity", "Position", "Range", "Diagnostic"]


class Severity(IntEnum):
    """LSP DiagnosticSeverity values."""

    ERROR = 1
    WARNING = 2
    INFO = 3
    HINT = 4


_CLI_NAMES = {
    Severity.ERROR: "error",
    Severity.WARNING: "warning",
    Severity.INFO: "info",
    Severity.HINT: "hint",
}


@dataclass(frozen=True)
class Position:
    """Zero-based line/character, LSP-style."""

    line: int
    character: int


@dataclass(frozen=True)
class Range:
    start: Position
    end: Position

    @classmethod
    def on_line(cls, line: int, start_col: int = 0, end_col: int = 0) -> Range:
        return cls(Position(line, start_col), Position(line, end_col))


@dataclass(frozen=True)
class Diagnostic:
    """One lint finding, LSP-shaped.

    Diagnostic codes (BUILD_PLAN WP2 item 5):
    ``frd.ears`` (INFO, detected EARS pattern), ``frd.unclassifiable``,
    ``frd.ambiguous``, ``frd.no-primitive``, ``frd.param-out-of-range``,
    ``frd.rationale-ignored``.
    """

    file: str
    range: Range
    severity: Severity
    code: str
    message: str
    quickfix: str | None = None
    source: str = "infersynth-lint"

    def to_lsp(self) -> dict:
        """The LSP ``Diagnostic`` wire shape (positions stay zero-based)."""
        d = {
            "range": {
                "start": {
                    "line": self.range.start.line,
                    "character": self.range.start.character,
                },
                "end": {
                    "line": self.range.end.line,
                    "character": self.range.end.character,
                },
            },
            "severity": int(self.severity),
            "code": self.code,
            "source": self.source,
            "message": self.message,
        }
        if self.quickfix is not None:
            d["data"] = {"quickfix": self.quickfix}
        return d

    def format_cli(self) -> str:
        """``file:line:col severity code message`` with one-based line/col."""
        pos = self.range.start
        return (
            f"{self.file}:{pos.line + 1}:{pos.character + 1} "
            f"{_CLI_NAMES[self.severity]} {self.code} {self.message}"
        )
