"""Shared fixtures. Integration tests use the real Needle engine, real Whisper and
Piper models and real Windows APIs; nothing here mocks AI output.

File-system tests run in ``~/NEXUS_test_sandbox/<random>`` (not under AppData,
which NEXUS protects), removed afterwards. Power commands are intercepted.
"""

from __future__ import annotations

import os
import shutil
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("NEEDLE_TELEMETRY", "0")
os.environ.setdefault("DO_NOT_TRACK", "1")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "needle: needs the real Needle engine (downloads on first run)")
    config.addinivalue_line("markers", "models: needs the Whisper / Piper models")
    config.addinivalue_line("markers", "hardware: needs a microphone / speakers / display")


@pytest.fixture(autouse=True)
def english_by_default():
    """Tests assert exact messages; each test starts in English (Portuguese tests switch)."""
    from app.i18n import set_language

    set_language("en")
    yield
    set_language("en")


@pytest.fixture()
def sandbox() -> Path:
    base = Path.home() / "NEXUS_test_sandbox"
    path = base / uuid.uuid4().hex[:10]
    path.mkdir(parents=True)
    yield path
    shutil.rmtree(path, ignore_errors=True)
    try:
        base.rmdir()
    except OSError:
        pass


@pytest.fixture()
def services(sandbox: Path):
    """Tool services pointed at the sandbox."""
    from app.ai.memory import LocalMemory
    from app.config.settings import Settings
    from app.tools.app_resolver import AppResolver
    from app.tools.context import SessionContext, services as svc
    from app.tools.file_index import FileIndex

    settings = Settings()
    settings.files.default_create_folder = str(sandbox)
    memory = LocalMemory(sandbox / "memory.json")
    old = (svc.settings, svc.memory, svc.apps, svc.files, svc.session)
    svc.settings, svc.memory = settings, memory
    svc.apps = AppResolver(cache_file=sandbox / "apps.json", user_alias=memory.alias_for)
    svc.files = FileIndex(lambda: [sandbox])
    svc.session = SessionContext()
    yield svc
    svc.settings, svc.memory, svc.apps, svc.files, svc.session = old


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    from app.ui.nexus_core import configure_default_format

    configure_default_format()
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(scope="session")
def engine():
    """The real Needle 3 engine with NEXUS's tool registry (loaded once)."""
    from app import paths
    from app.ai.model_assets import AssetError, ModelAssets
    from app.ai.needle_engine import NeedleEngine
    from app.config.settings import Settings
    from app.tools import load_all

    eng = NeedleEngine(Settings(), load_all())
    try:
        eng.submit(eng.load, ModelAssets(paths.models_dir())).result()
    except AssetError as exc:
        pytest.skip(f"Needle assets unavailable: {exc}")
    yield eng
    eng.shutdown()


@pytest.fixture()
def power_calls(monkeypatch):
    from app.tools import system

    calls: list[list[str]] = []
    monkeypatch.setattr(system, "_run_power_command", lambda args: calls.append(list(args)))
    monkeypatch.setattr(system, "_lock_workstation", lambda: calls.append(["lock"]) or True)
    return calls
