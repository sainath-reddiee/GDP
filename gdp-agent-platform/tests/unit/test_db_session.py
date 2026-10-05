import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api"))

from app.db import SnowflakeSessionError, _friendly, _patch_keyring


def test_friendly_maps_credwrite():
    message = _friendly(RuntimeError("(1783, 'CredWrite', 'The stub received bad data')"))
    assert "CredWrite 1783" in message
    assert "memory" in message


def test_keyring_store_survives_windows_failure(monkeypatch):
    import keyring

    def boom(service, username, password):
        raise RuntimeError("CredWrite")

    monkeypatch.setattr(keyring, "set_password", boom)
    monkeypatch.setattr(keyring, "get_password", lambda service, username: None)
    _patch_keyring()
    keyring.set_password("svc", "user", "token")
    assert keyring.get_password("svc", "user") == "token"


def test_session_error_is_runtime():
    assert issubclass(SnowflakeSessionError, RuntimeError)
