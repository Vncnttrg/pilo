import hashlib
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest
import server as srv


@pytest.fixture
def client(tmp_path):
    srv.app.config["TESTING"] = True
    # Redirect DATA_DIR to tmp so tests don't write real files
    original = srv.DATA_DIR
    srv.DATA_DIR = tmp_path
    srv.USERS_DIR = tmp_path / "users"
    srv.USERS_DIR.mkdir()
    if hasattr(srv, "_pending_codes"):
        srv._pending_codes.clear()
    yield srv.app.test_client()
    srv.DATA_DIR = original
    srv.USERS_DIR = original / "users"
    if hasattr(srv, "_pending_codes"):
        srv._pending_codes.clear()


def test_user_round_trip(tmp_path):
    srv.USERS_DIR = tmp_path / "users"
    srv.USERS_DIR.mkdir()

    email_hash = hashlib.sha256(b"test@example.com").hexdigest()
    user = {
        "email": "test@example.com",
        "token": "abc123",
        "gender": None,
        "size": None,
        "style_vector": None,
        "completed_onboarding": False,
        "feedback_log": [],
        "created_at": int(time.time()),
    }
    srv._save_user(email_hash, user)
    loaded = srv._load_user(email_hash)
    assert loaded == user


def test_load_user_missing_returns_none(tmp_path):
    srv.USERS_DIR = tmp_path / "users"
    srv.USERS_DIR.mkdir()
    assert srv._load_user("nonexistent") is None


def test_get_user_from_token_finds_user(tmp_path):
    srv.USERS_DIR = tmp_path / "users"
    srv.USERS_DIR.mkdir()

    email_hash = hashlib.sha256(b"tok@example.com").hexdigest()
    user = {"email": "tok@example.com", "token": "mytoken123"}
    srv._save_user(email_hash, user)

    found_hash, found_user = srv._get_user_from_token("mytoken123")
    assert found_hash == email_hash
    assert found_user["token"] == "mytoken123"


def test_get_user_from_token_unknown_returns_none(tmp_path):
    srv.USERS_DIR = tmp_path / "users"
    srv.USERS_DIR.mkdir()
    assert srv._get_user_from_token("ghost") == (None, None)


def test_register_valid_email_returns_success(client):
    with patch.object(srv, "_send_code_email") as mock_send:
        rv = client.post("/register", json={"email": "user@example.com"})
    assert rv.status_code == 200
    assert rv.get_json() == {"success": True}
    mock_send.assert_called_once()
    call_email, call_code = mock_send.call_args[0]
    assert call_email == "user@example.com"
    assert len(call_code) == 6 and call_code.isdigit()


def test_register_missing_email_returns_400(client):
    rv = client.post("/register", json={})
    assert rv.status_code == 400


def test_register_stores_code_in_pending(client):
    with patch.object(srv, "_send_code_email"):
        client.post("/register", json={"email": "store@example.com"})
    assert "store@example.com" in srv._pending_codes
    entry = srv._pending_codes["store@example.com"]
    assert len(entry["code"]) == 6
    assert entry["expires_at"] > time.time()


def _plant_code(email: str, code: str, expired: bool = False):
    delta = -1 if expired else 600
    srv._pending_codes[email] = {"code": code, "expires_at": time.time() + delta}


def test_verify_valid_code_returns_token_and_user(client):
    _plant_code("new@example.com", "123456")
    rv = client.post("/verify", json={"email": "new@example.com", "code": "123456"})
    assert rv.status_code == 200
    data = rv.get_json()
    assert "token" in data
    assert data["user"]["email"] == "new@example.com"
    assert data["user"]["completed_onboarding"] is False
    assert data["user"]["style_vector"] is None


def test_verify_creates_user_file(client):
    _plant_code("file@example.com", "654321")
    client.post("/verify", json={"email": "file@example.com", "code": "654321"})
    email_hash = hashlib.sha256(b"file@example.com").hexdigest()
    assert srv._load_user(email_hash) is not None


def test_verify_existing_user_returns_same_token(client):
    email_hash = hashlib.sha256(b"existing@example.com").hexdigest()
    user = {
        "email": "existing@example.com",
        "token": "stable-token",
        "gender": "men",
        "size": "M",
        "style_vector": None,
        "completed_onboarding": False,
        "feedback_log": [],
        "created_at": int(time.time()),
    }
    srv._save_user(email_hash, user)
    _plant_code("existing@example.com", "000000")
    rv = client.post("/verify", json={"email": "existing@example.com", "code": "000000"})
    assert rv.status_code == 200
    assert rv.get_json()["token"] == "stable-token"


def test_verify_wrong_code_returns_400(client):
    _plant_code("wrong@example.com", "111111")
    rv = client.post("/verify", json={"email": "wrong@example.com", "code": "999999"})
    assert rv.status_code == 400


def test_verify_expired_code_returns_400(client):
    _plant_code("expired@example.com", "222222", expired=True)
    rv = client.post("/verify", json={"email": "expired@example.com", "code": "222222"})
    assert rv.status_code == 400


def test_verify_consumes_code(client):
    _plant_code("once@example.com", "333333")
    client.post("/verify", json={"email": "once@example.com", "code": "333333"})
    rv = client.post("/verify", json={"email": "once@example.com", "code": "333333"})
    assert rv.status_code == 400


def test_onboard_with_token_updates_user_file(client):
    email_hash = hashlib.sha256(b"onboard@example.com").hexdigest()
    user = {
        "email": "onboard@example.com",
        "token": "onboard-token",
        "gender": None,
        "size": None,
        "style_vector": None,
        "completed_onboarding": False,
        "feedback_log": [],
        "created_at": int(time.time()),
    }
    srv._save_user(email_hash, user)

    keys = list(srv._onboarding_embs.keys())[:3]
    with patch.object(srv, "_rescore_and_save"), patch("numpy.save"):
        rv = client.post(
            "/onboard",
            json={"gender": "men", "size": "M", "selected_images": keys},
            headers={"Authorization": "Bearer onboard-token"},
        )
    assert rv.status_code == 200

    saved = srv._load_user(email_hash)
    assert saved["completed_onboarding"] is True
    assert saved["gender"] == "men"
    assert saved["size"] == "M"
    assert saved["style_vector"] is not None
    assert len(saved["style_vector"]) == 512


def test_feedback_with_token_appends_to_user_log(client):
    email_hash = hashlib.sha256(b"fb@example.com").hexdigest()
    user = {
        "email": "fb@example.com",
        "token": "fb-token",
        "gender": None,
        "size": None,
        "style_vector": None,
        "completed_onboarding": False,
        "feedback_log": [],
        "created_at": int(time.time()),
    }
    srv._save_user(email_hash, user)

    with patch("numpy.save"):
        rv = client.post(
            "/feedback",
            json={"listing_id": 1, "action": "dislike", "reason": "too_expensive"},
            headers={"Authorization": "Bearer fb-token"},
        )
    assert rv.status_code == 200

    saved = srv._load_user(email_hash)
    assert len(saved["feedback_log"]) == 1
    entry = saved["feedback_log"][0]
    assert entry["listing_id"] == 1
    assert entry["action"] == "skip"
    assert entry["reason"] == "too_expensive"
