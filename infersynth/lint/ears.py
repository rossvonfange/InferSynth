"""EARS classification — the grammar layer of FRD lint (UX.md).

Classifies each requirement sentence against the five EARS patterns
(ubiquitous, event-driven "when", state-driven "while", unwanted "if…then",
optional "where") plus ``complex`` (multiple triggers) and ``unclassifiable``
(no shall/must modal). Regex/keyword based, deliberately shallow: EARS says
the sentence is well-formed; the catalog vocabulary (vocab.py) says it is
synthesizable. The layers compose.
"""

from __future__ import annotations

import re

from infersynth.lint.diagnostics import Diagnostic, Range, Severity
from infersynth.lint.model import Requirement

__all__ = ["classify", "lint_ears", "EARS_PATTERNS"]

EARS_PATTERNS = (
    "ubiquitous",
    "event-driven",
    "state-driven",
    "unwanted",
    "optional",
    "complex",
    "unclassifiable",
)

_MODAL_RE = re.compile(r"\b(shall|must)\b", re.IGNORECASE)
_WHEN_RE = re.compile(r"\bwhen(ever)?\b", re.IGNORECASE)
_WHILE_RE = re.compile(r"\bwhile\b", re.IGNORECASE)
_WHERE_RE = re.compile(r"\bwhere\b", re.IGNORECASE)
_IF_THEN_RE = re.compile(r"\bif\b.*\b(then|shall|must)\b", re.IGNORECASE | re.DOTALL)
# A sentence separator in the middle of the text ⇒ more than one sentence.
_MID_SENTENCE_RE = re.compile(r"[.!?;]\s")


def classify(text: str) -> str:
    """Return the EARS pattern name for one requirement sentence.

    ``unclassifiable``: no shall/must modal, or a modal statement spanning
    multiple sentences (an EARS requirement is exactly one sentence).
    """
    if not _MODAL_RE.search(text):
        return "unclassifiable"
    if _MID_SENTENCE_RE.search(text.strip()):
        return "unclassifiable"
    triggers = []
    if _WHEN_RE.search(text):
        triggers.append("event-driven")
    if _WHILE_RE.search(text):
        triggers.append("state-driven")
    if _IF_THEN_RE.search(text):
        triggers.append("unwanted")
    if _WHERE_RE.search(text):
        triggers.append("optional")
    if len(triggers) > 1:
        return "complex"
    if len(triggers) == 1:
        return triggers[0]
    return "ubiquitous"


def _range(req: Requirement) -> Range:
    if req.source is None:
        return Range.on_line(0)
    return Range.on_line(req.source.line, req.source.col, req.source.end_col)


def lint_ears(req: Requirement) -> list[Diagnostic]:
    """EARS diagnostics for one lintable requirement.

    INFO ``frd.ears`` with the detected pattern; additionally WARN
    ``frd.unclassifiable`` when an unclassifiable requirement contains
    shall/must (a binding statement that fits no EARS sentence). Modal-free
    prose stays INFO-only: prose profiles are legal input, DESIGN section 6.
    """
    file = req.source.file if req.source else "<frd>"
    pattern = classify(req.text)
    diags = [
        Diagnostic(
            file=file,
            range=_range(req),
            severity=Severity.INFO,
            code="frd.ears",
            message=f"{req.id}: EARS pattern: {pattern}",
        )
    ]
    if pattern == "unclassifiable" and _MODAL_RE.search(req.text):
        diags.append(
            Diagnostic(
                file=file,
                range=_range(req),
                severity=Severity.WARNING,
                code="frd.unclassifiable",
                message=(
                    f"{req.id}: contains shall/must but does not fit a single "
                    "EARS sentence; write one requirement per sentence as "
                    "'The <system> shall <response>' (optionally with a "
                    "when/while/if-then/where clause)"
                ),
            )
        )
    return diags
