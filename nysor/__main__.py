# Copyright 2025-2026 Facundo Batista
# Licensed under the Apache v2 License
# For further info, check https://github.com/facundobatista/nysor

import argparse

from nysor.logtools import logsetup, LOG_LEVELS
from nysor.main import main

# mutually exclusive verbosity levels
parser = argparse.ArgumentParser()
loggroup = parser.add_mutually_exclusive_group()
for option, (_, helpmsg) in LOG_LEVELS.items():
    if option:
        loggroup.add_argument(
            f"-{option[0]}",
            f"--{option}",
            action="store_const",
            const=option,
            dest="loglevel",
            help=helpmsg
        )

# the rest of argument parsing
parser.add_argument("--nvim", action="store", help="Path to the Neovim executable.")
parser.add_argument(
    "path", action="store", nargs="?", default=None,
    help="Path to the file to edit or directory to open (optional)"
)

args = parser.parse_args()

logsetup(args.loglevel)
main(args.nvim, args.path)
