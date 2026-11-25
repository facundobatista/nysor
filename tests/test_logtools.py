# Copyright 2026 Facundo Batista
# Licensed under the Apache v2 License
# For further info, check https://github.com/facundobatista/nysor

"""Tests for logtools.py."""

import logging
import os
import re

import pytest

from nysor import logtools

logger = logging.getLogger()


class TestTrace:
    """The custom ``logger.trace()`` method logs at the TRACE level."""

    def test_message_is_logged_at_trace_level(self, logs):
        """A trace() call emits a TRACE record, with the braces already formatted."""
        logger.trace("hello world")
        assert "hello world" in logs.trace
        assert "hello world" not in logs.debug
        assert "hello world" not in logs.info


@pytest.fixture
def fresh_logging(mocker, tmp_path):
    """Isolate logsetup: log file in tmp_path, and a way to find the handlers it added.

    Yields a function `added(predicate)` answering which handlers matching the predicate were
    added by this test's logsetup call -- identified by the tag that logsetup sets on its
    handlers (conftest already ran logsetup, so its handlers and pytest's own must not be
    confused with these, and pytest shuffles root.handlers between test phases anyway).
    Handlers added by the test are removed afterwards, and the "nysor" level restored.
    """
    mocker.patch.object(logtools, "_LOGS_DIR", tmp_path)
    root = logging.getLogger()
    nysor_logger = logging.getLogger("nysor")
    old_level = nysor_logger.level
    before_tags = {getattr(h, "_nysor_tag", None) for h in root.handlers}

    def added(predicate):
        """Return the handlers matching `predicate` that this test's logsetup added."""
        return [
            h for h in root.handlers
            if predicate(h)
            and getattr(h, "_nysor_tag", None) not in before_tags | {None}
        ]

    yield added
    nysor_logger.setLevel(old_level)
    for handler in list(root.handlers):
        if getattr(handler, "_nysor_tag", None) not in before_tags | {None}:
            root.removeHandler(handler)


def _is_terminal(handler):
    """Whether the handler is the terminal one (a stream handler that is not the log file)."""
    return isinstance(handler, logging.StreamHandler) and not isinstance(
        handler, logging.FileHandler)


class TestTerminalLevels:
    """The terminal level ladder: quiet default, then -v, -d and -t to increase it."""

    def test_quiet_is_gone_and_default_is_warning(self):
        """The -q option no longer exists; the (quiet-ish) default shows WARNING and above."""
        assert "quiet" not in logtools.LOG_LEVELS
        assert logtools.LOG_LEVELS[None][0] == logging.WARNING

    @pytest.mark.parametrize("option, level", [
        ("verbose", logging.INFO),
        ("debug", logging.DEBUG),
        ("trace", 5),
    ])
    def test_options_levels(self, option, level):
        """Each CLI option shows its level and above in the terminal."""
        assert logtools.LOG_LEVELS[option][0] == level


class TestLogSetup:
    """logsetup configures two destinations, each one with its own level."""

    def test_logfile_is_created_with_default_info_level(self, fresh_logging):
        """Without the environment variable, the log file records INFO and above."""
        logtools.logsetup(None)
        disk, = fresh_logging(lambda h: isinstance(h, logging.FileHandler))
        assert disk.level == logging.INFO

    def test_logfile_name_has_timestamp_and_pid(self, fresh_logging):
        """The log file is named 'nysor-<timestamp>-<pid>.log'."""
        logtools.logsetup(None)
        disk, = fresh_logging(lambda h: isinstance(h, logging.FileHandler))
        name = os.path.basename(disk.baseFilename)
        assert re.fullmatch(rf"nysor-\d{{8}}T\d{{6}}-{os.getpid()}\.log", name)

    def test_terminal_default_level_is_warning(self, fresh_logging):
        """Without options, the terminal only shows WARNING and above."""
        logtools.logsetup(None)
        terminal, = fresh_logging(_is_terminal)
        assert terminal.level == logging.WARNING

    def test_nysor_level_is_the_most_verbose_destination(self, fresh_logging, mocker):
        """The 'nysor' tree lets through whatever the most verbose destination needs."""
        mocker.patch.dict(os.environ, {"NYSOR_LOGFILE_LEVEL": "trace"})
        logtools.logsetup(None)
        assert logging.getLogger("nysor").level == 5  # min(WARNING from terminal, TRACE disk)

    def test_hint_is_logged_only_for_disk(self, fresh_logging, caplog):
        """In the default setup, a line hints the user about the env var; only in the file."""
        logtools.logsetup(None)
        disk, = fresh_logging(lambda h: isinstance(h, logging.FileHandler))
        terminal, = fresh_logging(_is_terminal)
        hints = [r for r in caplog.records if "NYSOR_LOGFILE_LEVEL" in r.getMessage()]
        assert len(hints) == 1
        assert hints[0].levelno == logging.INFO
        assert hints[0].file_only is True
        assert not terminal.filter(hints[0])
        assert disk.filter(hints[0])

    def test_envvar_debug_switches_only_the_disk(self, fresh_logging, caplog, mocker):
        """NYSOR_LOGFILE_LEVEL=debug raises the file level, leaves the terminal alone, no hint."""
        mocker.patch.dict(os.environ, {"NYSOR_LOGFILE_LEVEL": "debug"})
        logtools.logsetup(None)
        disk, = fresh_logging(lambda h: isinstance(h, logging.FileHandler))
        terminal, = fresh_logging(_is_terminal)
        assert disk.level == logging.DEBUG
        assert terminal.level == logging.WARNING
        assert not [r for r in caplog.records if "NYSOR_LOGFILE_LEVEL" in r.getMessage()]

    def test_envvar_trace_switches_only_the_disk(self, fresh_logging, mocker):
        """NYSOR_LOGFILE_LEVEL=trace puts the file in TRACE."""
        mocker.patch.dict(os.environ, {"NYSOR_LOGFILE_LEVEL": "trace"})
        logtools.logsetup(None)
        disk, = fresh_logging(lambda h: isinstance(h, logging.FileHandler))
        assert disk.level == 5

    def test_envvar_invalid_value_logs_error_and_keeps_default(self, fresh_logging, caplog,
                                                               mocker):
        """A wrong env var value logs an ERROR (visible everywhere) and keeps INFO in the file."""
        mocker.patch.dict(os.environ, {"NYSOR_LOGFILE_LEVEL": "banana"})
        logtools.logsetup(None)
        disk, = fresh_logging(lambda h: isinstance(h, logging.FileHandler))
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1
        assert "banana" in errors[0].getMessage()
        assert "NYSOR_LOGFILE_LEVEL" in errors[0].getMessage()
        assert disk.level == logging.INFO


class TestPrefix:
    """The prefix is decided when the logger is captured, then copied into every record."""

    def test_explicit_prefix_decided_at_capture(self, caplog):
        """getLogger(prefix=...) stores the prefix at capture; records carry it bracketed."""
        logger = logging.getLogger("nysor.tree.fruit", prefix="fruta")
        assert logger._nysor_prefix == "fruta"
        with caplog.at_level(logging.INFO):
            logger.info("hola")
        assert caplog.records[-1].prefix == "[fruta]"

    def test_default_prefix_is_the_module_name(self, caplog):
        """Without an explicit prefix, the decided one is the module name (last component)."""
        logger = logging.getLogger("nysor.tree.some_module")
        assert logger._nysor_prefix == "some_module"
        with caplog.at_level(logging.INFO):
            logger.info("hola")
        assert caplog.records[-1].prefix == "[some_module]"

    def test_logger_missing_prefix_falls_back_to_empty(self, caplog):
        """Loggers created before logtools was imported still get a sane prefix."""
        logger = logging.getLogger("nysor.tree.other_module")
        del logger._nysor_prefix  # as such a logger would come
        with caplog.at_level(logging.INFO):
            logger.info("hola")
        assert caplog.records[-1].prefix == ""
