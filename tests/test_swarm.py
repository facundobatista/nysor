# Copyright 2026 Facundo Batista
# Licensed under the Apache v2 License
# For further info, check https://github.com/facundobatista/nysor

from unittest.mock import MagicMock

import psutil
import pytest

from nysor.swarm import get_nysor_pids


@pytest.fixture
def make_process():
    """Build a fake psutil.Process-like mock with the given pid/name/cmdline."""

    def _make_process(pid, name, cmdline=None, name_error=None, cmdline_error=None):
        p = MagicMock()
        p.pid = pid
        if name_error is not None:
            p.name.side_effect = name_error
        else:
            p.name.return_value = name
        if cmdline_error is not None:
            p.cmdline.side_effect = cmdline_error
        else:
            p.cmdline.return_value = cmdline or []
        return p

    return _make_process


class TestGetNysorPids:

    def test_no_processes(self, mocker):
        """No processes running at all."""
        mocker.patch("nysor.swarm.psutil.process_iter", return_value=[])
        assert get_nysor_pids() == set()

    def test_exact_name_match(self, mocker, make_process):
        """A process literally named 'nysor' is picked up."""
        proc = make_process(pid=100, name="nysor")
        mocker.patch("nysor.swarm.psutil.process_iter", return_value=[proc])
        assert get_nysor_pids() == {100}

    def test_python_process_running_nysor(self, mocker, make_process):
        """A 'py*' process with 'nysor' in its cmdline is picked up."""
        proc = make_process(pid=200, name="python3", cmdline=["python3", "-m", "nysor"])
        mocker.patch("nysor.swarm.psutil.process_iter", return_value=[proc])
        assert get_nysor_pids() == {200}

    def test_python_process_not_running_nysor(self, mocker, make_process):
        """A 'py*' process running something else is ignored."""
        proc = make_process(pid=201, name="python3", cmdline=["python3", "-m", "http.server"])
        mocker.patch("nysor.swarm.psutil.process_iter", return_value=[proc])
        assert get_nysor_pids() == set()

    def test_unrelated_process(self, mocker, make_process):
        """A process that's neither named 'nysor' nor a 'py*' interpreter is ignored."""
        proc = make_process(pid=300, name="bash", cmdline=["bash"])
        mocker.patch("nysor.swarm.psutil.process_iter", return_value=[proc])
        assert get_nysor_pids() == set()

    def test_process_vanishes_while_getting_name(self, mocker, make_process):
        """A process that disappears mid-iteration (name()) is skipped, not raised."""
        proc = make_process(pid=400, name=None, name_error=psutil.NoSuchProcess(400))
        mocker.patch("nysor.swarm.psutil.process_iter", return_value=[proc])
        assert get_nysor_pids() == set()

    def test_process_access_denied_on_cmdline(self, mocker, make_process):
        """A process whose cmdline() we can't read (e.g. other user/OS restriction) is skipped."""
        proc = make_process(
            pid=500, name="python3", cmdline_error=psutil.AccessDenied(500))
        mocker.patch("nysor.swarm.psutil.process_iter", return_value=[proc])
        assert get_nysor_pids() == set()

    def test_process_becomes_zombie(self, mocker, make_process):
        """A zombie process is skipped instead of raising."""
        proc = make_process(pid=600, name="python3", cmdline_error=psutil.ZombieProcess(600))
        mocker.patch("nysor.swarm.psutil.process_iter", return_value=[proc])
        assert get_nysor_pids() == set()

    def test_mixed_processes(self, mocker, make_process):
        """A realistic mix: matches, non-matches and broken processes all at once."""
        procs = [
            make_process(pid=1, name="nysor"),
            make_process(pid=2, name="python3", cmdline=["python3", "-m", "nysor"]),
            make_process(pid=3, name="bash", cmdline=["bash"]),
            make_process(pid=4, name=None, name_error=psutil.NoSuchProcess(4)),
            make_process(pid=5, name="python3", cmdline_error=psutil.AccessDenied(5)),
        ]
        mocker.patch("nysor.swarm.psutil.process_iter", return_value=procs)
        assert get_nysor_pids() == {1, 2}
