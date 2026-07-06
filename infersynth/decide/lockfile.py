"""Cost lockfile (SELECTION §8): the *only* source of external cost data.

SELECTION §8 repeatability contract: "any external data the decision layer reads
(BOM pricing, stock, sourcing) is snapshotted into a versioned lockfile;
synthesis never touches a live API; refreshing the lockfile is an explicit user
action." This module is that snapshot.

Format — ``costs.lock.json``::

    {
      "version": 1,
      "generated_at": "2026-07-06T00:00:00Z",   # supplied by the caller/CLI
      "source": "cell.yaml",                      # where the entries came from
      "entries": {
        "core/opamp-gain-noninverting@0.1.0": {   # library/cell@version key
          "bom": {"qty1": 0.88, "qty1k": 0.33}     # PARTIAL cost overrides
        }
      }
    }

A lockfile entry is a *partial* override: it is merged onto the cell's
``cell.yaml`` ``costs`` (top-level keys replace; unspecified keys keep their
``cell.yaml`` value). :func:`apply_overrides` does that merge.

**Determinism (SELECTION §8).** Synthesis reads the lockfile but *never* the
clock: ``generated_at`` comes from the caller (``--timestamp`` on the CLI, else
current time captured *at lock time* — locking is a user action). :func:`write`
is the only writer; the refresh-from-live-API path is future work and raises
:class:`NotImplementedError` rather than silently hitting a network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = ["Lockfile", "load", "write", "refresh_from_live"]

LOCKFILE_VERSION = 1


@dataclass(frozen=True)
class Lockfile:
    """A loaded cost lockfile."""

    generated_at: str
    source: str
    entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    version: int = LOCKFILE_VERSION
    #: filesystem path the lockfile was loaded from (for trace identity); ``""``
    #: for an in-memory lockfile.
    path: str = ""

    @property
    def identity(self) -> str:
        """Stable identity string recorded in the selection trace."""
        stem = Path(self.path).name if self.path else "<in-memory>"
        return f"{stem}@{self.generated_at}"

    def apply_overrides(self, cell_key: str, base_costs: dict[str, Any]) -> dict[str, Any]:
        """Merge this lockfile's partial override for *cell_key* onto *base_costs*.

        Top-level cost keys in the override replace the base; keys the override
        omits keep their ``cell.yaml`` value. Returns a new dict (base unchanged).
        """
        merged = dict(base_costs)
        override = self.entries.get(cell_key)
        if override:
            merged.update(override)
        return merged


def load(path: str | Path) -> Lockfile:
    """Load a ``costs.lock.json``. Raises ``FileNotFoundError`` if absent (the
    caller decides whether a missing lockfile is fatal — the CLI treats an
    explicitly-requested-but-missing lockfile as an error, never a live fetch)."""
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{p}: lockfile must be a JSON object")
    entries = data.get("entries") or {}
    if not isinstance(entries, dict):
        raise ValueError(f"{p}: lockfile 'entries' must be an object")
    return Lockfile(
        generated_at=str(data.get("generated_at", "")),
        source=str(data.get("source", "")),
        entries={str(k): dict(v) for k, v in entries.items()},
        version=int(data.get("version", LOCKFILE_VERSION)),
        path=str(p),
    )


def write(
    catalog: Any,
    path: str | Path,
    *,
    timestamp: str | None = None,
    source: str = "cell.yaml",
) -> Lockfile:
    """Snapshot the catalog's *current* ``cell.yaml`` costs into a lockfile.

    This is the ``infersynth costs lock`` implementation: it freezes today's
    authored costs so future synthesis runs are reproducible. ``timestamp`` is
    the caller-supplied ``generated_at`` (ISO 8601); when ``None`` the current
    time is captured *here* (lock time is a user action, not a synthesis step).
    Only cells that carry a ``costs:`` block get an entry; unpriced cells are
    left out (the decision layer flags them, it does not invent prices).
    """
    ts = timestamp if timestamp is not None else datetime.now(timezone.utc).isoformat()
    entries: dict[str, dict[str, Any]] = {}
    for key in sorted(catalog.cells):
        cell = catalog.cells[key]
        if cell.costs:
            entries[key] = json.loads(json.dumps(cell.costs))  # deep, plain copy
    lock = Lockfile(generated_at=ts, source=source, entries=entries, path=str(path))
    payload = {
        "version": LOCKFILE_VERSION,
        "generated_at": ts,
        "source": source,
        "entries": entries,
    }
    Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return lock


def refresh_from_live(*_args: Any, **_kwargs: Any) -> None:
    """The refresh-from-live-pricing-API path — explicitly future work.

    SELECTION §8 forbids synthesis from touching a live API; refreshing the
    lockfile from a distributor API (Octopart/Digi-Key/…) is a deliberate,
    out-of-band user action that does not yet exist. Stubbed loudly so no caller
    silently ships a fake or a network hit.
    """
    raise NotImplementedError(
        "refresh-from-live-pricing-API is future work: no distributor backend is "
        "wired. Use `infersynth costs lock` to snapshot current cell.yaml costs."
    )
