# Copyright 2025-2026 Facundo Batista
# Licensed under the Apache v2 License
# For further info, check https://github.com/facundobatista/nysor

import argparse
import logging

from nysor.main import main

# add trace level to logging
logging.addLevelName(5, "TRACE")
logging.TRACE = 5

# prepare argument parsing
parser = argparse.ArgumentParser()
loggroup = parser.add_mutually_exclusive_group()
loggroup.add_argument("-q", "--quiet", action="store_true", help="Set logging to be quiet.")
loggroup.add_argument("-v", "--verbose", action="store_true", help="Set logging to verbose.")
loggroup.add_argument(
    "-t", "--trace", action="store_true",
    help="Set logging for tracing (beware, there may be too many messages)."
)

parser.add_argument("--nvim", action="store", help="Path to the Neovim executable.")
parser.add_argument(
    "path", action="store", nargs="?", default=None,
    help="Path to the file to edit or directory to open (optional)"
)

args = parser.parse_args()

if args.quiet:
    loglevel = logging.WARNING
elif args.verbose:
    loglevel = logging.DEBUG
elif args.trace:
    loglevel = logging.TRACE
else:
    loglevel = logging.INFO

main(loglevel, args.nvim, args.path)
