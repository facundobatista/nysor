# Copyright 2026 Facundo Batista
# Licensed under the Apache v2 License
# For further info, check https://github.com/facundobatista/nysor

"""Shared test configuration and fixtures."""

import os

# must be set before any PyQt import ever creates a QApplication (see the 'qapp' fixture below)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from nysor import logtools

logtools.logsetup(None)


@pytest.fixture(scope="session")
def qapp():
    """A single, offscreen QApplication for the whole test session (Qt allows only one).

    Needed by any test that constructs a real QWidget (e.g. TextDisplay) -- Qt aborts the
    process otherwise. Session-scoped and never quit: tearing a QApplication down mid-session
    and building another is unsupported by Qt itself.
    """
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
