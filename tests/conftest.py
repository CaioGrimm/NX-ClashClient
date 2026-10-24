import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


@pytest.fixture(autouse=True)
def _plain_adb(monkeypatch):
    """Tests expect the bare `adb` binary, not the LDPlayer one found on the host."""
    monkeypatch.setenv("ADB_PATH", "adb")
