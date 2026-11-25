# Copyright 2026 Facundo Batista
# Licensed under the Apache v2 License
# For further info, check https://github.com/facundobatista/nysor

"""Tools related to logging."""

import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import foffinf
import platformdirs

_TRACE_LEVEL = 5
LOG_LEVELS = {
    "verbose": (logging.INFO, "Show INFO and above in the terminal."),
    "debug": (logging.DEBUG, "Show DEBUG and above in the terminal."),
    "trace": (
        _TRACE_LEVEL,
        "Show TRACE and above in the terminal (beware, there may be too many messages).",
    ),
    None: (logging.WARNING, None),  # default no-option-set
}

# the log file level, only for the disk, switchable through this environment variable
_LOGFILE_ENVVAR = "NYSOR_LOGFILE_LEVEL"
_LOGS_DIR = Path(platformdirs.user_log_dir("nysor"))

_LOG_FORMAT = "%(asctime)s.%(msecs)03d %(levelname)-5s %(prefix)-7s %(message)s"
_LOG_DATEFMT = "%H:%M:%S"

_ORIG_GETLOGGER = logging.getLogger
_ORIG_MAKERECORD = logging.Logger.makeRecord


def _getlogger(name=None, prefix=None):
    """Return the logger for `name`, deciding its prefix at capture time.

    The prefix is either explicitly given or the module name (the last component of the
    logger's name); it is stored in the logger itself, to be copied into every record
    afterwards (see _makerecord).
    """
    logger = _ORIG_GETLOGGER(name)
    if prefix is None:
        prefix = logger.name.rsplit(".", 1)[-1]
    logger._nysor_prefix = prefix
    return logger


def _makerecord(self, *args, **kwargs):
    """Create the record and copy into it the logger's already decided prefix, bracketed."""
    record = _ORIG_MAKERECORD(self, *args, **kwargs)
    prefix = getattr(self, "_nysor_prefix", None) or self.name.rsplit(".", 1)[-1]
    record.prefix = f"[{prefix}]"
    return record


# the prefix is decided at capture time (getLogger) and carried by the records (makeRecord);
# patching instead of subclassing also covers loggers created before this module gets imported
logging.getLogger = _getlogger
logging.Logger.makeRecord = _makerecord

logger = logging.getLogger(__name__)


def _trace(self, message, *args):
    """Log the message with severity TRACE."""
    if self.isEnabledFor(_TRACE_LEVEL):
        # logging's findCaller only skips frames internal to logging itself, so explicitly
        # skip this helper's frame to report the real caller
        self._log(_TRACE_LEVEL, message, args, stacklevel=2)


# make `logger.trace(...)` available everywhere; patching the class also affects loggers
# created before this module gets imported
logging.Logger.trace = _trace


class _FileOnlyFilter(logging.Filter):
    """Drop the records marked as file-only, so those lines never reach the terminal."""

    def filter(self, record):
        return not getattr(record, "file_only", False)


def _decide_logfile_level():
    """Decide the log file level from the NYSOR_LOGFILE_LEVEL environment variable.

    Return (level, invalid_value, show_hint): the level for the disk handler, the invalid value
    found there (None if the variable is absent or correct; the caller will report it), and
    whether to hint the user about the variable (only when it is not set).
    """
    value = os.environ.get(_LOGFILE_ENVVAR)
    if value is None:
        return logging.INFO, None, True
    value = value.strip().lower()
    if value == "debug":
        return logging.DEBUG, None, False
    if value == "trace":
        return _TRACE_LEVEL, None, False
    return logging.INFO, value, False


def _logfile_path():
    """Return the path of this run's log file: 'nysor-<timestamp>-<pid>.log'."""
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    return _LOGS_DIR / f"nysor-{stamp}-{os.getpid()}.log"


def logsetup(selected_level):
    """Prepare all logging: a terminal handler and a log file, each one with its own level."""
    # add trace level to logging
    logging.addLevelName(_TRACE_LEVEL, "TRACE")

    terminal_level = LOG_LEVELS[selected_level][0]
    disk_level, invalid_value, show_hint = _decide_logfile_level()

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT)
    tag = object()  # marks the handlers of this run, so tests can tell them apart

    # terminal: everything from the selected level up, except the lines marked as file only
    root = logging.getLogger()
    terminal = logging.StreamHandler(sys.stdout)
    terminal.setFormatter(formatter)
    terminal.setLevel(terminal_level)
    terminal.addFilter(_FileOnlyFilter())
    terminal._nysor_tag = tag
    root.addHandler(terminal)

    # disk: always there, with its own (env var switchable) level
    _LOGS_DIR.mkdir(parents=True, exist_ok=True)
    disk = logging.FileHandler(_logfile_path(), encoding="utf-8", delay=True)
    disk.setFormatter(formatter)
    disk.setLevel(disk_level)
    disk._nysor_tag = tag
    root.addHandler(disk)

    # the "nysor" tree must let through whatever the most verbose destination needs; each
    # destination applies its own level on top
    logging.getLogger("nysor").setLevel(min(terminal_level, disk_level))

    # use new format styles
    foffinf.formatize("nysor", scatter=True)

    # report the log file situation, now that the handlers are in place
    if invalid_value is not None:
        logger.error(
            "Invalid value {!r} for NYSOR_LOGFILE_LEVEL; valid values: 'debug', 'trace' "
            "(the log file keeps the default INFO level)", invalid_value)
    elif show_hint:
        logger.info(
            "To log more information set NYSOR_LOGFILE_LEVEL to 'debug' or 'trace'",
            extra={"file_only": True})


def log_notdone(msg, **items):
    """Nicely log a not implemented error."""
    reprs = [f"{k}={{!r}}" for k in items.keys()]
    fullmsg = f"Not Implemented! {msg}: {' '.join(reprs)}"
    logger.error(fullmsg, *items.values())
