"""Requirements model: flat FRD in, hierarchical requirement tree out.

DESIGN.md section 6: the FRD is flat markdown; hierarchy lives here, derived
by the lint stage. A :class:`Requirement` is one statement (or a grouping
heading, or a rationale note); a :class:`RequirementSet` is the tree plus a
flat id index with deterministic (document-order, pre-order) iteration.

IDs are explicit when the document provides them (``SYS.1.2``, ``PWR-01``),
otherwise generated ``R-<n>`` in document order.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

__all__ = [
    "SourceSpan",
    "Requirement",
    "RequirementSet",
    "RequirementModelError",
    "Pragma",
]

#: Tag placed on heading-derived grouping nodes — never lintable requirements.
GROUP_TAG = "group"
#: Tag placed on prose-paragraph requirements (no bullet/number/ID marker).
PROSE_TAG = "prose"

#: Recognized FRD pragma kinds (NETFLOW.md "FRD pragmas" closed vocabulary),
#: plus ``"unknown"`` for pragma-shaped brackets whose head is not recognized
#: (these carry only a ``raw`` payload and drive the ``frd.unknown-pragma`` WARN).
PRAGMA_KINDS = ("feeds", "use", "no-pack", "unknown")


@dataclass(frozen=True)
class Pragma:
    """One FRD pragma parsed off a requirement line (NETFLOW.md sugar).

    Bracket-tag sugar (same grammar family as ``[D:…]``) compiled by lint into
    formal-spec entries. A single small carrier for all three closed-vocabulary
    pragmas plus the ``unknown`` sentinel:

    * ``feeds`` — ``target`` is the destination requirement id, ``port`` the
      optional port/role qualifier (``None`` when unqualified).
    * ``use`` — ``cell_ref`` is the pinned ``library/cell[@version]`` reference.
    * ``no-pack`` — a bare flag; no payload fields.
    * ``unknown`` — a pragma-shaped bracket with an unrecognized head; ``raw``
      holds the original bracket text for the ``frd.unknown-pragma`` diagnostic.
    """

    kind: str
    target: str | None = None
    port: str | None = None
    cell_ref: str | None = None
    raw: str = ""


class RequirementModelError(ValueError):
    """Raised for structurally invalid requirement trees (e.g. duplicate ids)."""


@dataclass(frozen=True)
class SourceSpan:
    """Where a requirement came from. Zero-based, LSP-compatible positions."""

    file: str
    line: int
    col: int
    end_line: int
    end_col: int


@dataclass
class Requirement:
    """One requirement (or grouping/rationale node) in the derived tree."""

    id: str
    text: str
    level: int = 0
    parent: Requirement | None = field(default=None, repr=False, compare=False)
    children: list[Requirement] = field(default_factory=list, repr=False, compare=False)
    source: SourceSpan | None = None
    tags: set[str] = field(default_factory=set)
    #: Rationale notes ([D]-tagged / parenthetical) are never lintable.
    rationale: bool = False
    #: FRD pragmas parsed off this line (NETFLOW.md); compiled into the spec.
    pragmas: list[Pragma] = field(default_factory=list, compare=False)

    @property
    def is_group(self) -> bool:
        return GROUP_TAG in self.tags

    @property
    def explicit_id(self) -> bool:
        return not self.id.startswith("R-")

    def add_child(self, child: Requirement) -> None:
        child.parent = self
        self.children.append(child)


class RequirementSet:
    """A requirement tree plus flat index; iteration is document order."""

    def __init__(self, roots: list[Requirement] | None = None) -> None:
        self.roots: list[Requirement] = []
        self.by_id: dict[str, Requirement] = {}
        for root in roots or []:
            self.add_root(root)

    def add_root(self, root: Requirement) -> None:
        self.roots.append(root)
        for req in _walk(root):
            if req.id in self.by_id:
                raise RequirementModelError(f"duplicate requirement id {req.id!r}")
            self.by_id[req.id] = req

    def __iter__(self) -> Iterator[Requirement]:
        for root in self.roots:
            yield from _walk(root)

    def __len__(self) -> int:
        return len(self.by_id)

    def __getitem__(self, req_id: str) -> Requirement:
        return self.by_id[req_id]

    def requirements(self) -> list[Requirement]:
        """Lintable requirements: not groups, not rationale notes."""
        return [r for r in self if not r.is_group and not r.rationale]


def _walk(req: Requirement) -> Iterator[Requirement]:
    yield req
    for child in req.children:
        yield from _walk(child)
