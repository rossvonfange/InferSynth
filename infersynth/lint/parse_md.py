"""Extract requirements from FRD markdown (DESIGN.md section 6, UX.md).

Flat FRD, hierarchical spec: bullets / numbered items / table rows /
requirement-tree lines are requirements; headings are grouping *hints* that
become parent nodes; prose paragraphs are captured as requirements too (the
hobbyist/manager/scientist profiles write prose, and lint must see it).
Rationale material — ``[D]``-tagged lines and fully parenthetical lines —
is flagged ``rationale=True`` and is never a lintable requirement.

Explicit ID prefixes (``SYS.1.1.1``, ``PWR-01``) are recognized; dotted IDs
attach to their prefix parent. Everything else gets a generated ``R-<n>`` id
in document order. Pure stdlib/regex; no markdown-parser dependency.
"""

from __future__ import annotations

import re
from pathlib import Path

from infersynth.lint.model import (
    GROUP_TAG,
    PROSE_TAG,
    Requirement,
    RequirementSet,
    SourceSpan,
)

__all__ = ["parse_frd_markdown", "parse_frd_text"]

# Explicit requirement IDs: dotted (SYS.1.1.1) or dashed (PWR-01, COM-01).
_DOTTED_ID = r"[A-Z][A-Z0-9]*(?:\.\d+)+"
_DASHED_ID = r"[A-Z][A-Z0-9]*-\d+[A-Za-z0-9]*"
_ID_RE = re.compile(rf"^(?P<id>{_DOTTED_ID}|{_DASHED_ID})\s+(?P<rest>.+)$")
_ID_FULL_RE = re.compile(rf"^(?:{_DOTTED_ID}|{_DASHED_ID})$")
# Bare all-caps root id (e.g. "SYS AirCell ...") — only honored in tree-mode files.
_BARE_ROOT_RE = re.compile(r"^(?P<id>[A-Z][A-Z0-9]{1,7})\s+(?P<rest>.+)$")

_HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<text>.+?)\s*$")
_BULLET_RE = re.compile(r"^(?P<indent>\s*)[-*+]\s+(?P<text>.+)$")
_NUMBERED_RE = re.compile(r"^(?P<indent>\s*)\d+[.)]\s+(?P<text>.+)$")
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|[\s:|\-]+\|?\s*$")
_TREE_MARKER_RE = re.compile(r"[├└]──\s*")
_TREE_PREFIX_RE = re.compile(r"^[\s│├└─]+")
_PAREN_LINE_RE = re.compile(r"^\(.*\)$", re.DOTALL)
_INLINE_D_RE = re.compile(r"\s*\[D:?\s*(?P<note>[^\]]*)\]")


class _Parser:
    def __init__(self, text: str, file: str) -> None:
        self.lines = text.splitlines()
        self.file = file
        self.tree_mode = any(_TREE_MARKER_RE.search(ln) for ln in self.lines)
        self.roots: list[Requirement] = []
        self.by_explicit_id: dict[str, Requirement] = {}
        self._counter = 0
        # heading stack: (markdown level, node)
        self.headings: list[tuple[int, Requirement]] = []
        # list stack: (indent, node)
        self.list_stack: list[tuple[int, Requirement]] = []
        # node accepting plain-line continuations (list item or tree node)
        self.open_item: Requirement | None = None
        self.open_item_indent = 0
        # paragraph accumulator: (start_line, [lines])
        self.para: tuple[int, list[str]] | None = None

    # -- node plumbing ---------------------------------------------------

    def _gen_id(self) -> str:
        self._counter += 1
        return f"R-{self._counter}"

    def _attach(self, node: Requirement, parent: Requirement | None) -> None:
        if parent is not None:
            parent.add_child(node)
            node.level = parent.level + 1
        else:
            self.roots.append(node)
            node.level = 0
        if node.explicit_id:
            self.by_explicit_id[node.id] = node

    def _heading_parent(self) -> Requirement | None:
        return self.headings[-1][1] if self.headings else None

    def _parent_for_id(self, req_id: str) -> Requirement | None:
        """Dotted IDs attach to their longest existing prefix parent."""
        if "." in req_id:
            prefix = req_id
            while "." in prefix:
                prefix = prefix.rsplit(".", 1)[0]
                if prefix in self.by_explicit_id:
                    return self.by_explicit_id[prefix]
        return self._heading_parent()

    def _new_requirement(
        self,
        raw_text: str,
        line: int,
        col: int,
        *,
        tags: set[str] | None = None,
        allow_bare_root: bool = False,
    ) -> Requirement:
        req_id, text = None, raw_text
        m = _ID_RE.match(raw_text)
        if m:
            req_id, text = m.group("id"), m.group("rest")
        elif allow_bare_root and self.tree_mode:
            m = _BARE_ROOT_RE.match(raw_text)
            if m:
                req_id, text = m.group("id"), m.group("rest")
        node = Requirement(
            id=req_id or self._gen_id(),
            text=text.strip(),
            source=SourceSpan(self.file, line, col, line, len(self.lines[line])),
            tags=tags or set(),
        )
        parent = self._parent_for_id(node.id) if node.explicit_id else None
        if parent is None:
            parent = self._current_list_parent() or self._heading_parent()
        self._attach(node, parent)
        return node

    def _current_list_parent(self) -> Requirement | None:
        return self.list_stack[-1][1] if self.list_stack else None

    def _continue(self, node: Requirement, extra: str, line: int) -> None:
        node.text = f"{node.text} {extra.strip()}".strip()
        if node.source is not None:
            node.source = SourceSpan(
                node.source.file,
                node.source.line,
                node.source.col,
                line,
                len(self.lines[line]),
            )

    # -- paragraph handling ------------------------------------------------

    def _flush_para(self) -> None:
        if self.para is None:
            return
        start, lines = self.para
        self.para = None
        text = " ".join(ln.strip() for ln in lines).strip()
        if not text:
            return
        node = Requirement(
            id="",
            text=text,
            source=SourceSpan(
                self.file, start, 0, start + len(lines) - 1, len(lines[-1])
            ),
            tags={PROSE_TAG},
        )
        m = _ID_RE.match(text)
        if m is None and self.tree_mode:
            m = _BARE_ROOT_RE.match(text)
        if m:
            node.id, node.text = m.group("id"), m.group("rest").strip()
            node.tags.discard(PROSE_TAG)
        if not node.id:
            node.id = self._gen_id()
        parent = self._parent_for_id(node.id) if node.explicit_id else None
        if parent is None:
            parent = self._heading_parent()
        self._attach(node, parent)
        if node.explicit_id:
            # an ID'd plain line (tree root) accepts tree continuations
            self.open_item = node

    # -- line handlers -------------------------------------------------------

    def _handle_heading(self, m: re.Match, line_no: int) -> None:
        self._flush_para()
        self.list_stack.clear()
        self.open_item = None
        depth = len(m.group("hashes"))
        text = m.group("text")
        if _PAREN_LINE_RE.match(text) or text.startswith("[D"):
            # comment-style rationale heading (e.g. "# (author: layout ...)")
            node = Requirement(
                id=self._gen_id(),
                text=text,
                source=SourceSpan(self.file, line_no, 0, line_no, len(self.lines[line_no])),
                rationale=True,
            )
            self._attach(node, self._heading_parent())
            return
        while self.headings and self.headings[-1][0] >= depth:
            self.headings.pop()
        node = Requirement(
            id=self._gen_id(),
            text=text,
            source=SourceSpan(self.file, line_no, 0, line_no, len(self.lines[line_no])),
            tags={GROUP_TAG},
        )
        self._attach(node, self._heading_parent())
        self.headings.append((depth, node))

    def _handle_table_row(self, line: str, line_no: int) -> None:
        self._flush_para()
        self.open_item = None
        nxt = self.lines[line_no + 1] if line_no + 1 < len(self.lines) else ""
        if _TABLE_SEP_RE.match(line) or _TABLE_SEP_RE.match(nxt):
            return  # separator row, or header row (separator follows)
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and _ID_FULL_RE.match(cells[0]):
            text = cells[1] if len(cells) > 1 else ""
            if len(cells) > 2 and cells[2] not in ("", "—", "-", "–"):
                text = f"{text} — {cells[2]}"
            raw = f"{cells[0]} {text}"
        else:
            raw = " — ".join(c for c in cells if c)
        col = line.index("|")
        self._new_requirement(raw, line_no, col)

    def _handle_tree_line(self, line: str, line_no: int) -> None:
        self._flush_para()
        marker = _TREE_MARKER_RE.search(line)
        if marker:
            content = line[marker.end() :].strip()
            node = self._new_requirement(content, line_no, marker.end())
            self.open_item = node
        elif self.open_item is not None:
            content = _TREE_PREFIX_RE.sub("", line).strip()
            if content:
                self._continue(self.open_item, content, line_no)

    def _handle_list_item(self, m: re.Match, line_no: int) -> None:
        self._flush_para()
        indent = len(m.group("indent"))
        while self.list_stack and self.list_stack[-1][0] >= indent:
            self.list_stack.pop()
        node = self._new_requirement(m.group("text"), line_no, indent)
        self.list_stack.append((indent, node))
        self.open_item = node
        self.open_item_indent = indent

    def _handle_plain(self, line: str, line_no: int) -> None:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if (
            self.open_item is not None
            and self.para is None
            and indent > self.open_item_indent
        ):
            self._continue(self.open_item, stripped, line_no)
            return
        self.open_item = None
        self.list_stack.clear()
        if self.para is None:
            self.para = (line_no, [stripped])
        else:
            self.para[1].append(stripped)

    # -- main loop ----------------------------------------------------------

    def parse(self) -> RequirementSet:
        for line_no, line in enumerate(self.lines):
            if not line.strip():
                self._flush_para()
                self.open_item = None
                continue
            m = _HEADING_RE.match(line)
            if m:
                self._handle_heading(m, line_no)
                continue
            if _TABLE_ROW_RE.match(line):
                self._handle_table_row(line, line_no)
                continue
            if self.tree_mode and (
                _TREE_MARKER_RE.search(line) or line.lstrip().startswith("│")
            ):
                self._handle_tree_line(line, line_no)
                continue
            m = _BULLET_RE.match(line) or _NUMBERED_RE.match(line)
            if m:
                self._handle_list_item(m, line_no)
                continue
            self._handle_plain(line, line_no)
        self._flush_para()

        for root in self.roots:
            self._finalize(root)
        return RequirementSet(self.roots)

    # -- post-pass: rationale flags + levels -------------------------------

    def _finalize(self, node: Requirement) -> None:
        if not node.is_group:
            if _PAREN_LINE_RE.match(node.text) or node.text.startswith("[D"):
                node.rationale = True
            else:
                notes = [
                    mm.group("note").strip() for mm in _INLINE_D_RE.finditer(node.text)
                ]
                if notes:
                    node.text = _INLINE_D_RE.sub("", node.text).strip()
                    for note in notes:
                        child = Requirement(
                            id=self._gen_id(),
                            text=note,
                            source=node.source,
                            rationale=True,
                        )
                        node.add_child(child)
        node.level = 0 if node.parent is None else node.parent.level + 1
        for child in node.children:
            self._finalize(child)


def parse_frd_text(text: str, file: str = "<frd>") -> RequirementSet:
    """Parse FRD markdown *text* into a :class:`RequirementSet`."""
    return _Parser(text, file).parse()


def parse_frd_markdown(path: str | Path) -> RequirementSet:
    """Parse an FRD markdown file into a :class:`RequirementSet`."""
    p = Path(path)
    return parse_frd_text(p.read_text(encoding="utf-8"), file=str(p))
