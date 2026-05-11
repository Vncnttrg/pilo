from unittest.mock import patch

import numpy as np
import pytest

import server as srv


@pytest.fixture
def client():
    srv.app.config["TESTING"] = True
    with srv.app.test_client() as c:
        yield c


# ── /onboard ──────────────────────────────────────────────────────────────────

def test_onboard_valid_images_returns_512_dim_vector(client):
    keys = list(srv._onboarding_embs.keys())[:3]
    assert len(keys) == 3, "onboarding_embeddings.json must exist (run embed_onboarding.py)"

    with patch.object(srv, "_rescore_and_save"), patch("numpy.save"):
        rv = client.post("/onboard", json={
            "gender": "men",
            "size": "M",
            "selected_images": keys,
        })

    assert rv.status_code == 200
    data = rv.get_json()
    assert "style_vector" in data
    assert len(data["style_vector"]) == 512


def test_onboard_vector_is_l2_normalized(client):
    keys = list(srv._onboarding_embs.keys())[:3]

    with patch.object(srv, "_rescore_and_save"), patch("numpy.save"):
        rv = client.post("/onboard", json={
            "gender": "men",
            "size": "M",
            "selected_images": keys,
        })

    vec = np.array(rv.get_json()["style_vector"], dtype=np.float32)
    assert abs(np.linalg.norm(vec) - 1.0) < 1e-5


def test_onboard_empty_images_returns_400(client):
    rv = client.post("/onboard", json={
        "gender": "men",
        "size": "M",
        "selected_images": [],
    })
    assert rv.status_code == 400


def test_onboard_all_invalid_keys_returns_400(client):
    rv = client.post("/onboard", json={
        "gender": "men",
        "size": "M",
        "selected_images": ["nonexistent/image.jpg", "also/missing.jpg"],
    })
    assert rv.status_code == 400


# ── /feed ─────────────────────────────────────────────────────────────────────

def test_feed_get_returns_list(client):
    rv = client.get("/feed")
    assert rv.status_code == 200
    data = rv.get_json()
    assert isinstance(data, list)
    assert len(data) <= 50


def test_feed_post_with_style_vector_returns_ranked_list(client):
    vec = np.random.rand(512).astype(np.float32)
    vec /= np.linalg.norm(vec)

    rv = client.post("/feed", json={"style_vector": vec.tolist()})
    assert rv.status_code == 200
    data = rv.get_json()
    assert isinstance(data, list)
    assert len(data) <= 50

    scores = [r["final_score"] for r in data]
    assert scores == sorted(scores, reverse=True)


def test_feed_post_without_style_vector_falls_back_to_cache(client):
    rv = client.post("/feed", json={})
    assert rv.status_code == 200
    data = rv.get_json()
    assert isinstance(data, list)
