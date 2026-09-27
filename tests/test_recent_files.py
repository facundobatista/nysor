# Copyright 2026 Facundo Batista
# Licensed under the Apache v2 License
# For further info, check https://github.com/facundobatista/nysor

import json
import time
from pathlib import Path

import pytest

from nysor import recent_files


@pytest.fixture
def scope(tmp_path, monkeypatch):
    """Redirect the 'general' scope to a throwaway file for this test."""
    entry = recent_files._Scope(path=tmp_path / "recent.json")
    monkeypatch.setitem(recent_files._SCOPES, "general", entry)
    return entry


class TestGetRecent:

    def test_missing_file(self, scope):
        """No file on disk yet -> empty list, no error."""
        assert recent_files.get_recent() == []

    def test_reads_existing_file(self, scope):
        """A pre-existing file is read and returned as Path objects."""
        scope.path.write_text(json.dumps(["/a", "/b"]))
        assert recent_files.get_recent() == [Path("/a"), Path("/b")]

    def test_corrupt_file_returns_empty(self, scope):
        """Corrupt JSON is treated as no data, not raised."""
        scope.path.write_text("not json at all")
        assert recent_files.get_recent() == []

    def test_cache_reused_when_mtime_unchanged(self, scope, mocker):
        """A second call with no file change does not re-parse the JSON."""
        scope.path.write_text(json.dumps(["/a"]))
        spy = mocker.spy(recent_files.json, "loads")
        recent_files.get_recent()
        recent_files.get_recent()
        assert spy.call_count == 1

    def test_reloads_when_file_changes(self, scope):
        """A later write is picked up on the next call."""
        scope.path.write_text(json.dumps(["/a"]))
        recent_files.get_recent()
        time.sleep(0.01)  # force the mtime to actually tick past the previous write
        scope.path.write_text(json.dumps(["/a", "/b"]))
        assert recent_files.get_recent() == [Path("/a"), Path("/b")]


class TestRegister:

    def test_creates_parent_dir_and_file(self, tmp_path, monkeypatch):
        """The config directory is created on demand."""
        entry = recent_files._Scope(path=tmp_path / "nested" / "recent.json")
        monkeypatch.setitem(recent_files._SCOPES, "general", entry)
        recent_files.register("/a")
        assert json.loads(entry.path.read_text()) == ["/a"]

    def test_new_path_goes_to_front(self, scope):
        """Registering adds new paths as the most recent one."""
        recent_files.register("/a")
        recent_files.register("/b")
        assert recent_files.get_recent() == [Path("/b"), Path("/a")]

    def test_existing_path_moves_to_front_without_duplicating(self, scope):
        """Re-registering an already-known path just re-ranks it."""
        recent_files.register("/a")
        recent_files.register("/b")
        recent_files.register("/a")
        assert recent_files.get_recent() == [Path("/a"), Path("/b")]

    def test_truncates_to_max_recent(self, scope, monkeypatch):
        """Only the most recent MAX_RECENT paths survive."""
        monkeypatch.setattr(recent_files, "MAX_RECENT", 2)
        recent_files.register("/a")
        recent_files.register("/b")
        recent_files.register("/c")
        assert recent_files.get_recent() == [Path("/c"), Path("/b")]

    def test_persisted_content_matches_cache(self, scope):
        """What lands on disk (raw strings) matches a fresh read (as Path objects)."""
        recent_files.register("/a")
        on_disk = json.loads(scope.path.read_text())
        assert on_disk == [str(p) for p in recent_files.get_recent()]
