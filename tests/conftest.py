import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import pytest


@pytest.fixture(autouse=True)
def _sandbox_cache(tmp_path, monkeypatch):
    """Never let a test read or write the user's real explanation cache."""
    monkeypatch.setenv("EXPLAIN_ALOUD_CACHE", str(tmp_path / "_cache"))
