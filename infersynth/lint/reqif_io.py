"""ReqIF import (optional extra: ``pip install infersynth[reqif]``).

Imports a ``.reqif`` file into a :class:`RequirementSet`: the specification
hierarchy becomes the tree; each spec object's long-name (falling back to its
description, then its first string attribute value) becomes the requirement
text. Uses strictdoc-project's ``reqif`` library; the import is guarded so
the core lint engine works without the extra installed.
"""

from __future__ import annotations

from pathlib import Path

from infersynth.lint.model import Requirement, RequirementSet, SourceSpan

__all__ = ["ReqIFImportError", "load_reqif", "have_reqif"]

try:
    from reqif.parser import ReqIFParser

    _HAVE_REQIF = True
except ImportError:  # pragma: no cover - depends on optional extra
    ReqIFParser = None
    _HAVE_REQIF = False


class ReqIFImportError(RuntimeError):
    """Raised when ReqIF import is unavailable or the file cannot be read."""


def have_reqif() -> bool:
    """Is the optional ``reqif`` extra installed?"""
    return _HAVE_REQIF


def _spec_object_text(spec_object) -> str:
    if spec_object.long_name:
        return str(spec_object.long_name)
    if spec_object.description:
        return str(spec_object.description)
    for attr in spec_object.attributes:
        if isinstance(attr.value, str) and attr.value.strip():
            return attr.value.strip()
    return ""


def load_reqif(path: str | Path) -> RequirementSet:
    """Import *path* (.reqif) into a :class:`RequirementSet`.

    Requirement ids are the ReqIF spec-object identifiers; hierarchy follows
    the specification's spec-hierarchy tree. ReqIF carries no line/column
    information, so every source span points at line 0 of the file.
    """
    if not _HAVE_REQIF:
        raise ReqIFImportError(
            "ReqIF import requires the optional dependency: pip install 'infersynth[reqif]'"
        )
    p = Path(path)
    bundle = ReqIFParser.parse(str(p))
    core = bundle.core_content
    content = core.req_if_content if core is not None else None
    if content is None:
        raise ReqIFImportError(f"{p}: no REQ-IF-CONTENT")

    span = SourceSpan(str(p), 0, 0, 0, 0)
    roots: list[Requirement] = []

    def build(hierarchy) -> Requirement:
        spec_object = bundle.get_spec_object_by_ref(hierarchy.spec_object)
        node = Requirement(
            id=spec_object.identifier,
            text=_spec_object_text(spec_object),
            source=span,
        )
        for child in hierarchy.children or []:
            node.add_child(build(child))
        return node

    for specification in content.specifications or []:
        spec_root = Requirement(
            id=specification.identifier,
            text=str(specification.long_name or specification.identifier),
            source=span,
            tags={"group"},
        )
        for child in specification.children or []:
            spec_root.add_child(build(child))
        roots.append(spec_root)

    reqset = RequirementSet(roots)
    # levels were left at default; normalize from tree depth
    for req in reqset:
        req.level = 0 if req.parent is None else req.parent.level + 1
    return reqset
