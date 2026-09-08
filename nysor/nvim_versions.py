# Copyright 2026 Facundo Batista
# Licensed under the Apache v2 License
# For further info, check https://github.com/facundobatista/nysor

"""Neovim versions nysor is verified to work well with.

Single source of truth for both the app's own startup check and the CI test matrix. Kept
dependency-free so that CI step can read it without installing the project first.
"""

TO_TEST = [
    "0.12.2",
    "0.12.5",
]
APPROVED = [
    (0, 12),
]
