"""Shared test setup.

* The repository root goes on sys.path so `import scan` and `import tracker` work.
* SEARCHSHIP_FAKE_AI=1 is forced before tracker.ai is imported, so the
  provider registry only ever holds fakes and no test can reach a real
  Claude Code or Codex CLI (real calls spend the user's plan).
* Every test gets its own SEARCHSHIP_HOME, so nothing touches a real data folder.
* urllib's urlopen is blocked, so a test that forgets to pass a fake `get`
  fails loudly instead of using the network.
"""
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
for p in (str(REPO), str(TESTS)):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ["SEARCHSHIP_FAKE_AI"] = "1"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("searchship-home")
    monkeypatch.setenv("SEARCHSHIP_HOME", str(home))
    monkeypatch.setenv("SEARCHSHIP_FAKE_AI", "1")
    return home


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    import urllib.request

    def blocked(*args, **kwargs):
        raise AssertionError("tests must not use the network (pass a fake `get`)")

    monkeypatch.setattr(urllib.request, "urlopen", blocked)


@pytest.fixture
def store(tmp_path):
    from tracker.store import Store
    return Store(root=tmp_path / "data")
