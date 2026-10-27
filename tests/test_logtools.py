# Copyright 2026 Facundo Batista
# Licensed under the Apache v2 License
# For further info, check https://github.com/facundobatista/nysor

"""Tests for logtools.py."""

import logging

logger = logging.getLogger()


class TestTrace:
    """The custom ``logger.trace()`` method logs at the TRACE level."""

    def test_message_is_logged_at_trace_level(self, logs):
        """A trace() call emits a TRACE record, with the braces already formatted."""
        logger.trace("hello world")
        assert "hello world" in logs.trace
        assert "hello world" not in logs.debug
        assert "hello world" not in logs.info
