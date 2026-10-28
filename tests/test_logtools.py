# Copyright 2026 Facundo Batista
# Licensed under the Apache v2 License
# For further info, check https://github.com/facundobatista/nysor

"""Tests for logtools.py."""

import logging

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


class TestPrefixFormatter:
    """Log lines carry a curated prefix (see LOG_PREFIXES), or the module name when unmapped."""

    def _format(self, name, pathname):
        """Format a minimal record, the same way logsetup configures the root handler."""
        record = logging.LogRecord(name, logging.INFO, pathname, 1, "hello", None, None)
        formatter = logtools._PrefixFormatter(logtools._LOG_FORMAT, datefmt=logtools._LOG_DATEFMT)
        return formatter.format(record)

    def test_mapped_logger_gets_curated_prefix(self):
        """A logger in LOG_PREFIXES logs with its curated prefix, not its module name."""
        out = self._format("nysor.nvim_interface", "/somewhere/nvim_interface.py")
        assert "[nvim]" in out
        assert "nvim_interface" not in out

    def test_unmapped_logger_falls_back_to_module_name(self):
        """A logger not in LOG_PREFIXES logs with its module name."""
        out = self._format("nysor.text_display", "/somewhere/text_display.py")
        assert "[text_display]" in out
