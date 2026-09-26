# Copyright 2026 Facundo Batista
# Licensed under the Apache v2 License
# For further info, check https://github.com/facundobatista/nysor

"""Persist and retrieve the list of recently opened files."""

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import platformdirs

logger = logging.getLogger(__name__)

# how many paths we keep per scope
MAX_RECENT = 20


@dataclass
class _Scope:
    """A recent-files list and its on-disk backing file, with a cheap staleness check."""

    path: Path
    mtime_ns: int | None = None
    files: list[str] = field(default_factory=list)


# today there's only the "general" scope; a future per-project scope would just add another
# entry here, with its own file living alongside the project instead of the user config dir
_SCOPES = {
    "general": _Scope(path=Path(platformdirs.user_config_dir("nysor")) / "recent.json"),
}


def _load(scope):
    """Return the scope's recent-files list, reloading from disk only if it changed."""
    entry = _SCOPES[scope]
    try:
        mtime_ns = entry.path.stat().st_mtime_ns
    except OSError:
        entry.mtime_ns, entry.files = None, []
        return entry.files

    if mtime_ns != entry.mtime_ns:
        try:
            entry.files = json.loads(entry.path.read_text())
        except (OSError, ValueError):
            logger.warning("Could not read recent files from {!r}", entry.path)
            entry.files = []
        entry.mtime_ns = mtime_ns

    return entry.files


def get_recent(scope="general"):
    """Return the scope's recent-files list, most recent first."""
    return list(_load(scope))


def register(path, scope="general"):
    """Add `path` to the front of the scope's recent-files list, persisting it to disk."""
    entry = _SCOPES[scope]
    files = [path] + [f for f in _load(scope) if f != path]
    files = files[:MAX_RECENT]

    entry.path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=entry.path.parent)
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(files, fh)
        os.replace(tmp_name, entry.path)
    except OSError:
        os.unlink(tmp_name)
        raise

    entry.files = files
    entry.mtime_ns = entry.path.stat().st_mtime_ns
