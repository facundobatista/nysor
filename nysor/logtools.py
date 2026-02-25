# Copyright 2026 Facundo Batista
# Licensed under the Apache v2 License
# For further info, check https://github.com/facundobatista/nysor

"""Tools related to logging."""

import logging
import sys

import foffinf

logger = logging.getLogger(__name__)

_TRACE_LEVEL = 5
LOG_LEVELS = {
    "quiet": (logging.WARNING, "Set logging to be quiet."),
    "verbose": (logging.DEBUG, "Set logging to verbose."),
    "trace": (_TRACE_LEVEL, "Set logging for tracing (beware, there may be too many messages)."),
    None: (logging.INFO, None),  # default no-option-set
}


def logsetup(selected_level):
    """Prepare all logging."""
    # add trace level to logging
    logging.addLevelName(_TRACE_LEVEL, "TRACE")
    logging.TRACE = _TRACE_LEVEL

    # config
    logging.basicConfig(
        format='%(asctime)s.%(msecs)03d %(levelname)-5s %(message)s',
        datefmt='%H:%M:%S', stream=sys.stdout)

    # set proper level to the whole app
    level, _ = LOG_LEVELS[selected_level]
    logging.getLogger("nysor").setLevel(level)

    # use new format styles
    foffinf.formatize("nysor", scatter=True)


def log_notdone(msg, **items):
    """Nicely log a not implemented error."""
    reprs = [f"{k}={{!r}}" for k in items.keys()]
    fullmsg = f"Not Implemented! {msg}: {' '.join(reprs)}"
    logger.error(fullmsg, *items.values())
