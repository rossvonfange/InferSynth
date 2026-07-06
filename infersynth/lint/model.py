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

__all__ = ["SourceSpan", "Requirement", "RequirementSet", "RequirementModelError"]

#: Tag placed on heading-derived grouping nodes — never lintable requirements.
GROUP_TAG = "group"
#: Tag placed on prose-paragraph requirements (no bullet/number/ID marker).
PROSE_TAG = "prose"


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
