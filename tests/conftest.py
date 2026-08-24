"""
Shared fixtures: redirect SQLite to a temp file per test so tests are isolated
and the real DB is never touched.
"""
import os
import tempfile

import pytest


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("config.settings.db_file", db_path)

    # Signal files in tmp so they don't clash with a running instance
    monkeypatch.setattr("config.settings.skip_signal_file", str(tmp_path / ".skip"))
    monkeypatch.setattr("config.settings.pause_signal_file", str(tmp_path / ".pause"))
    monkeypatch.setattr("config.settings.seek_signal_file", str(tmp_path / ".seek"))
    monkeypatch.setattr("config.settings.stop_signal_file", str(tmp_path / ".stop"))
    monkeypatch.setattr("config.settings.volume_file", str(tmp_path / ".volume"))
    monkeypatch.setattr("config.settings.queue_state_file", str(tmp_path / ".queue_state.json"))

    # Force db module to open a fresh connection to the test DB
    import db as db_mod
    import threading
    db_mod._local = threading.local()
    db_mod.init_db()

    yield
