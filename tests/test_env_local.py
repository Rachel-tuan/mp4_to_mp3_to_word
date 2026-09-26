"""Covers the .env.local loading behavior added to app.py."""
import importlib
import os
import sys

import pytest


@pytest.fixture
def app_module():
    # app.py inserts the project root onto sys.path itself; import fresh
    # each time so module-level state doesn't leak between tests.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if "app" in sys.modules:
        del sys.modules["app"]
    import app
    return app


def test_loads_env_local_when_present(tmp_path, app_module, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    (tmp_path / ".env.local").write_text("DEEPSEEK_API_KEY=from_env_local\n")

    app_module._load_local_env(str(tmp_path))
    assert os.environ.get("DEEPSEEK_API_KEY") == "from_env_local"


def test_real_env_var_takes_priority(tmp_path, app_module, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "from_shell")
    (tmp_path / ".env.local").write_text("DEEPSEEK_API_KEY=from_env_local\n")

    app_module._load_local_env(str(tmp_path))
    assert os.environ.get("DEEPSEEK_API_KEY") == "from_shell"


def test_missing_env_local_is_a_noop(tmp_path, app_module, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    app_module._load_local_env(str(tmp_path))  # no .env.local in tmp_path
    assert os.environ.get("DEEPSEEK_API_KEY") is None
