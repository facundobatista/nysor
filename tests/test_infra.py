# Copyright 2026 Facundo Batista
# Licensed under the Apache v2 License
# For further info, check https://github.com/facundobatista/nysor

"""Infrastructure tests."""

import subprocess

import pytest


def test_codespell():
    """Verify all words are correctly spelled."""
    cmd = ["codespell", "nysor", "tests", "docs"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    report = [x.strip() for x in proc.stdout.split("\n")]
    indented_issues = [f" - {issue}" for issue in report if issue]
    if indented_issues:
        msg = "Please fix the following codespell issues!\n" + "\n".join(indented_issues)
        pytest.fail(msg, pytrace=False)


def test_ruff(capsys):
    """Verify all files are nicely styled."""
    cmd = ["ruff", "check", "-q"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode:
        msg = "Please fix the following ruff issues!\n" + proc.stdout
        pytest.fail(msg, pytrace=False)
