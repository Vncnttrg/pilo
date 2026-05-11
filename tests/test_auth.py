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
    yield srv.app.test_client()
    srv.DATA_DIR = original
    srv.USERS_DIR = original / "users"


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
