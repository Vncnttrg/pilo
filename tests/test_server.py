from unittest.mock import patch
from datetime import datetime, timezone

import numpy as np
import pytest

import server as srv


@pytest.fixture
def client():
    srv.app.config["TESTING"] = True
    srv._availability_cache.clear()
    with patch.object(srv, "_listing_is_available", return_value=True), srv.app.test_client() as c:
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
    assert "capsules" in data
    assert 1 <= len(data["capsules"]) <= 3
    assert all(len(c["vector"]) == 512 for c in data["capsules"])
    assert data["global_constraints"]["gender"] == "men"


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
    if data:
        assert data[0]["capsule_id"] == "default"
        assert data[0]["capsule_label"] == "saved style"
        assert "recommendation_reason" in data[0]
        assert "freshness_score" in data[0]


def test_final_score_blends_personalized_freshness_at_configured_weight(client):
    vec = np.random.rand(512).astype(np.float32)
    vec /= np.linalg.norm(vec)

    with patch.object(srv, "_freshness_score_for_listing", return_value=1.0):
        ranked = srv._compute_rankings(vec)

    item = ranked[0]
    personalized_freshness = item["capsule_score"] * item["freshness_score"]
    deal_gate = max(0.0, min(1.0, item["capsule_score"] / srv.DEAL_STYLE_GATE))
    expected = (
        srv.CAPSULE_SCORE_WEIGHT * item["capsule_score"]
        + srv.DEAL_SCORE_WEIGHT * item["deal_score"] * deal_gate
        + srv.FRESHNESS_SCORE_WEIGHT * personalized_freshness
    )
    assert item["freshness_score"] == pytest.approx(1.0)
    assert item["final_score"] == pytest.approx(expected, abs=1e-3)


def test_personalized_freshness_rewards_style_match_not_just_recency(monkeypatch):
    vec_strong = np.ones(512, dtype=np.float32)
    vec_strong /= np.linalg.norm(vec_strong)
    vec_weak = -np.ones(512, dtype=np.float32)
    vec_weak /= np.linalg.norm(vec_weak)

    base_listing = {
        "price": 20.0,
        "favourites": 0,
        "gender": "men",
        "image_url": "",
        "image_urls": [],
        "url": "",
        "catalog_name": "Herren Jacken",
        "category": "",
        "brand": "",
        "title": "",
    }
    listings = {
        1: {**base_listing, "id": 1},
        2: {**base_listing, "id": 2},
    }

    monkeypatch.setattr(srv, "_emb_ids", [1, 2])
    monkeypatch.setattr(srv, "_emb_index", {1: vec_strong, 2: vec_weak})
    monkeypatch.setattr(srv, "_emb_matrix", np.stack([vec_strong, vec_weak]))
    monkeypatch.setattr(srv, "_listings", listings)
    monkeypatch.setattr(srv, "_freshness_score_for_listing", lambda _listing: 1.0)
    monkeypatch.setattr(srv, "_sold_demand", {})

    capsule = srv._new_capsule(
        "test",
        "test",
        vec_strong,
        price_min=0,
        price_max=1000,
        confidence=1.0,
    )
    ranked = srv._score_for_capsule(capsule, gender="men", price_min=0, price_max=1000)

    strong = next(r for r in ranked if r["id"] == 1)
    weak = next(r for r in ranked if r["id"] == 2)

    assert strong["final_score"] > weak["final_score"]


def test_fav_velocity_rewards_momentum_over_raw_count(monkeypatch):
    import time as _time

    now = _time.time()
    vec = np.ones(512, dtype=np.float32)
    vec /= np.linalg.norm(vec)

    base = {
        "price": 20.0,
        "gender": "men",
        "image_url": "",
        "image_urls": [],
        "url": "",
        "catalog_name": "Herren Jacken",
        "category": "",
        "brand": "",
        "title": "",
    }
    listings = {
        1: {
            **base,
            "id": 1,
            "favourites": 40,
            "created_at": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(now - 3 * 86_400)),
        },
        2: {
            **base,
            "id": 2,
            "favourites": 40,
            "created_at": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(now - 60 * 86_400)),
        },
    }

    monkeypatch.setattr(srv, "_emb_ids", [1, 2])
    monkeypatch.setattr(srv, "_emb_index", {1: vec, 2: vec})
    monkeypatch.setattr(srv, "_emb_matrix", np.stack([vec, vec]))
    monkeypatch.setattr(srv, "_listings", listings)
    monkeypatch.setattr(srv, "_sold_demand", {})

    capsule = srv._new_capsule(
        "test",
        "test",
        vec,
        price_min=0,
        price_max=1000,
        confidence=1.0,
    )
    ranked = srv._score_for_capsule(capsule, gender="men", price_min=0, price_max=1000)

    fresh_item = next(r for r in ranked if r["id"] == 1)
    stale_item = next(r for r in ranked if r["id"] == 2)
    assert fresh_item["final_score"] > stale_item["final_score"]


def test_sold_demand_affects_final_score(monkeypatch):
    vec = np.ones(512, dtype=np.float32)
    vec /= np.linalg.norm(vec)

    base = {
        "price": 20.0,
        "favourites": 0,
        "gender": "men",
        "image_url": "",
        "image_urls": [],
        "url": "",
        "catalog_name": "Herren Jacken",
        "category": "",
        "brand": "carhartt wip",
        "title": "",
    }
    listings = {1: {**base, "id": 1}}

    brand_key = srv._listing_brand_key(base)
    cat_key = srv._listing_category_key(base)
    bucket = f"{brand_key}+{cat_key}"

    monkeypatch.setattr(srv, "_emb_ids", [1])
    monkeypatch.setattr(srv, "_emb_index", {1: vec})
    monkeypatch.setattr(srv, "_emb_matrix", np.stack([vec]))
    monkeypatch.setattr(srv, "_listings", listings)

    capsule = srv._new_capsule(
        "test",
        "test",
        vec,
        price_min=0,
        price_max=1000,
        confidence=1.0,
    )

    monkeypatch.setattr(srv, "_sold_demand", {bucket: {"demand_score": 0.9}})
    ranked_high = srv._score_for_capsule(capsule, gender="men", price_min=0, price_max=1000)

    monkeypatch.setattr(srv, "_sold_demand", {bucket: {"demand_score": 0.1}})
    ranked_low = srv._score_for_capsule(capsule, gender="men", price_min=0, price_max=1000)

    assert ranked_high[0]["final_score"] > ranked_low[0]["final_score"]


def test_onboarding_style_keywords_boost_matching_listings(monkeypatch):
    vec = np.ones(512, dtype=np.float32)
    vec /= np.linalg.norm(vec)
    listings = {
        1: {
            "id": 1,
            "title": "Vintage Adidas Track Jacket",
            "brand": "adidas",
            "price": 20.0,
            "favourites": 0,
            "gender": "men",
            "image_url": "",
            "image_urls": [],
            "url": "",
            "catalog_name": "Herren Jacken",
        },
        2: {
            "id": 2,
            "title": "Plain Office Shirt",
            "brand": "office",
            "price": 20.0,
            "favourites": 0,
            "gender": "men",
            "image_url": "",
            "image_urls": [],
            "url": "",
            "catalog_name": "Herren Oberteile",
        },
    }

    monkeypatch.setattr(srv, "_emb_ids", [1, 2])
    monkeypatch.setattr(srv, "_emb_index", {1: vec, 2: vec})
    monkeypatch.setattr(srv, "_emb_matrix", np.stack([vec, vec]))
    monkeypatch.setattr(srv, "_listings", listings)
    monkeypatch.setattr(srv, "_freshness_score_for_listing", lambda _listing: 0.5)

    capsule = srv._new_capsule(
        "vintage",
        "vintage",
        vec,
        category_weights={"vintage": 1.0},
        price_min=0,
        price_max=1000,
        confidence=1.0,
    )
    ranked = srv._score_for_capsule(capsule, gender="men", price_min=0, price_max=1000)

    assert [item["id"] for item in ranked[:2]] == [1, 2]
    assert ranked[0]["capsule_score"] > ranked[1]["capsule_score"]


def test_feed_post_supports_pagination(client):
    vec = np.random.rand(512).astype(np.float32)
    vec /= np.linalg.norm(vec)

    first = client.post("/feed", json={
        "style_vector": vec.tolist(),
        "offset": 0,
        "limit": 10,
    }).get_json()
    second = client.post("/feed", json={
        "style_vector": vec.tolist(),
        "offset": 10,
        "limit": 10,
    }).get_json()

    assert len(first) <= 10
    assert len(second) <= 10
    if len(first) == 10 and len(second) == 10:
        assert {item["id"] for item in first}.isdisjoint(
            item["id"] for item in second
        )


def test_feed_filters_unavailable_items(client):
    vec = np.random.rand(512).astype(np.float32)
    vec /= np.linalg.norm(vec)

    def is_available(listing):
        return listing["id"] != srv._emb_ids[0]

    with patch.object(srv, "_compute_rankings") as mock_rankings:
        mock_rankings.return_value = [
            {"id": srv._emb_ids[0], "final_score": 1.0},
            {"id": srv._emb_ids[1], "final_score": 0.9},
        ]
        with patch.object(srv, "_listing_is_available", side_effect=is_available):
            rv = client.post("/feed", json={
                "style_vector": vec.tolist(),
                "offset": 0,
                "limit": 1,
            })

    assert rv.status_code == 200
    assert rv.get_json() == [{"id": srv._emb_ids[1], "final_score": 0.9}]


def test_listing_availability_parser_detects_sold_status():
    html = (
        r'\"name\":\"item_status\",\"type\":\"item_status\",\"data\":'
        r'{\"is_reserved\":false,\"is_hidden\":false,\"is_closed\":true,'
        r'\"transaction_permitted\":false},\"exposure\"'
    )

    assert srv._is_listing_available_from_html(html) is False


def test_listing_availability_parser_detects_buyable_status():
    html = (
        r'\"name\":\"item_status\",\"type\":\"item_status\",\"data\":'
        r'{\"is_reserved\":false,\"is_hidden\":false,\"is_closed\":false,'
        r'\"transaction_permitted\":true},\"exposure\"'
        r'\"name\":\"ask_seller\",\"type\":\"ask_seller\",\"data\":'
        r'{\"can_buy\":true,\"is_closed\":false,\"is_hidden\":false,'
        r'\"is_reserved\":false},\"exposure\"'
    )

    assert srv._is_listing_available_from_html(html) is True


def test_listing_ttl_rechecks_stale_listings_before_showing(tmp_path, monkeypatch):
    now = datetime(2026, 5, 12, tzinfo=timezone.utc).timestamp()
    stale_scrape = now - ((srv.LISTING_REFRESH_TTL_HOURS * 60 * 60) + 1)
    calls = []

    monkeypatch.setattr(srv, "DATA_DIR", tmp_path)
    monkeypatch.setattr(srv.time, "time", lambda: now)
    monkeypatch.setattr(srv, "_fetch_listing_available", lambda listing: calls.append(listing["id"]) or True)
    srv._availability_cache.clear()

    assert srv._listing_passes_availability_gate({"id": 101, "scraped_at": stale_scrape}) is True
    assert srv._listing_passes_availability_gate({"id": 202, "scraped_at": now}) is True
    assert calls == [101]


def test_unavailable_listing_is_marked_dead(tmp_path, monkeypatch):
    monkeypatch.setattr(srv, "DATA_DIR", tmp_path)
    monkeypatch.setattr(srv, "_fetch_listing_available", lambda _listing: False)
    srv._availability_cache.clear()

    assert srv._listing_is_available({"id": 303, "url": "https://example.com/303"}) is False
    assert 303 in srv._dead_listing_ids()


def test_feed_post_without_style_vector_falls_back_to_cache(client):
    rv = client.post("/feed", json={})
    assert rv.status_code == 200
    data = rv.get_json()
    assert isinstance(data, list)


# ── /daily-drop ───────────────────────────────────────────────────────────────

def _install_daily_user(tmp_path, monkeypatch):
    monkeypatch.setattr(srv, "DATA_DIR", tmp_path)
    monkeypatch.setattr(srv, "USERS_DIR", tmp_path / "users")
    srv.USERS_DIR.mkdir()
    email_hash = "daily-user"
    capsule = srv._new_capsule(
        "daily-capsule",
        "clean minimal",
        srv._emb_index[srv._emb_ids[0]],
        confidence=0.9,
    )
    user = {
        "email": "daily@example.com",
        "token": "daily-token",
        "gender": None,
        "size": None,
        "style_vector": capsule["vector"],
        "capsules": [capsule],
        "global_constraints": {"gender": None, "sizes": [], "price_min": None, "price_max": None},
        "completed_onboarding": True,
        "feedback_log": [],
        "created_at": int(srv.time.time()),
    }
    srv._save_user(email_hash, user)
    return email_hash


def test_onboard_invalidates_same_day_daily_drop(client, tmp_path, monkeypatch):
    email_hash = _install_daily_user(tmp_path, monkeypatch)
    date_key = srv._today_key()
    drop_key = srv._drop_key(email_hash, date_key)
    srv._save_daily_drops({
        drop_key: {
            "user_id": email_hash,
            "date": date_key,
            "listing_ids": [srv._emb_ids[0]],
            "generated_at": int(srv.time.time()),
            "items": {},
        }
    })

    keys = list(srv._onboarding_embs.keys())[:3]
    with patch.object(srv, "_rescore_and_save"), patch("numpy.save"):
        rv = client.post(
            "/onboard",
            json={"gender": "men", "size": "M", "selected_images": keys},
            headers={"Authorization": "Bearer daily-token"},
        )

    assert rv.status_code == 200
    assert drop_key not in srv._load_daily_drops()


def test_daily_drop_persists_same_items_for_user(client, tmp_path, monkeypatch):
    email_hash = _install_daily_user(tmp_path, monkeypatch)

    first = client.get("/daily-drop?limit=12", headers={"Authorization": "Bearer daily-token"})
    second = client.get("/daily-drop?limit=12", headers={"Authorization": "Bearer daily-token"})

    assert first.status_code == 200
    assert second.status_code == 200
    first_items = first.get_json()
    second_items = second.get_json()
    assert 10 <= len(first_items) <= 15
    assert [item["id"] for item in second_items] == [item["id"] for item in first_items]
    assert all(isinstance(item["new_since_last_visit"], bool) for item in first_items)
    assert {item["daily_drop_tag"] for item in first_items} <= set(srv.DAILY_DROP_TAGS.values())

    saved_user = srv._load_user(email_hash)
    assert len(saved_user["top_3_daily_items"]) == 3
    stored_drops = srv._load_daily_drops()
    stored = stored_drops[srv._drop_key(email_hash, srv._today_key())]
    assert stored["listing_ids"] == [item["id"] for item in first_items]


def test_daily_drop_excludes_seen_ids_across_sessions(client, tmp_path, monkeypatch):
    email_hash = _install_daily_user(tmp_path, monkeypatch)
    user = srv._load_user(email_hash)
    seen_id = srv._rankings_for_daily_drop(user)[0]["id"]
    srv._upsert_daily_impression(email_hash, seen_id, event="item_seen")

    rv = client.get("/daily-drop?limit=12", headers={"Authorization": "Bearer daily-token"})

    assert rv.status_code == 200
    assert seen_id not in {item["id"] for item in rv.get_json()}


def test_feed_excludes_user_seen_ids_across_sessions(client, tmp_path, monkeypatch):
    email_hash = _install_daily_user(tmp_path, monkeypatch)
    seen_id, fresh_id = srv._emb_ids[:2]
    user = srv._load_user(email_hash)
    srv._remember_seen_listing(user, seen_id)
    srv._save_user(email_hash, user)

    with patch.object(srv, "_compute_capsule_rankings") as rankings:
        rankings.return_value = [
            {"id": seen_id, "final_score": 1.0},
            {"id": fresh_id, "final_score": 0.9},
        ]
        rv = client.post(
            "/feed",
            json={"limit": 2},
            headers={"Authorization": "Bearer daily-token"},
        )

    assert rv.status_code == 200
    assert rv.get_json() == [{"id": fresh_id, "final_score": 0.9}]


def test_daily_drop_regenerates_stale_profile_cache(client, tmp_path, monkeypatch):
    email_hash = _install_daily_user(tmp_path, monkeypatch)
    date_key = srv._today_key()
    drop_key = srv._drop_key(email_hash, date_key)
    srv._save_daily_drops({
        drop_key: {
            "user_id": email_hash,
            "date": date_key,
            "listing_ids": [srv._emb_ids[-1]],
            "generated_at": 1,
            "items": {
                str(srv._emb_ids[-1]): {
                    "capsule_id": "legacy-default",
                    "capsule_label": "saved style",
                    "final_score": 0.1,
                }
            },
        }
    })

    rv = client.get("/daily-drop?limit=12", headers={"Authorization": "Bearer daily-token"})

    assert rv.status_code == 200
    stored = srv._load_daily_drops()[drop_key]
    assert stored["generated_at"] != 1
    assert {
        item["capsule_label"]
        for item in stored["items"].values()
        if item.get("capsule_label")
    } <= {"clean minimal"}


def test_daily_drop_mixes_close_adjacent_hidden_and_wildcard(monkeypatch):
    monkeypatch.setattr(srv, "_filter_available_page", lambda ranked, offset, limit: ranked[offset:offset + limit])
    monkeypatch.setattr(srv, "_impression_stats", lambda _user_id: {})
    monkeypatch.setattr(srv, "_freshness_score_for_listing", lambda _listing: 0.5)

    listings = {}
    ranked = []
    brands = ["Nike", "Nike", "Nike", "Adidas", "Adidas", "Ralph", "Carhartt", "Levi's", "Patagonia", "Lacoste", "Diesel", "Columbia", "Uniqlo"]
    titles = [
        "Vintage Nike Track Jacket",
        "Nike Graphic Tee",
        "Nike Baggy Jeans",
        "Adidas Windbreaker Jacket",
        "Adidas Track Pants",
        "Ralph Lauren Polo Shirt",
        "Carhartt Cargo Pants",
        "Levi's 501 Jeans",
        "Patagonia Fleece Jacket",
        "Lacoste Polo",
        "Diesel Bootcut Jeans",
        "Columbia Outdoor Jacket",
        "Uniqlo Plain Overshirt",
    ]
    for idx, (brand, title) in enumerate(zip(brands, titles), start=1):
        listings[idx] = {
            "id": idx,
            "title": title,
            "brand": brand,
            "price": 20 + idx,
            "favourites": max(0, 12 - idx),
            "gender": "men",
            "image_url": "",
            "image_urls": [],
            "url": "",
            "catalog_name": "Herren Oberteile",
        }
        score = 1.0 - idx * 0.02
        ranked.append({
            "id": idx,
            "title": title,
            "brand": brand,
            "style_score": score,
            "capsule_score": score,
            "price_score": 1.0,
            "fav_score": 0.2,
            "deal_score": 0.8,
            "freshness_score": 0.5,
            "final_score": score,
            "capsule_id": "taste",
            "capsule_label": "vintage",
            "recommendation_reason": "old reason",
        })

    monkeypatch.setattr(srv, "_listings", listings)
    items = srv._compose_daily_drop(ranked, "daily-user", {"feedback_log": []}, 12, None)

    assert {item["daily_drop_tag"] for item in items} == {
        "Close match",
        "Adjacent find",
        "Hidden gem",
        "Wildcard",
    }
    assert max(
        sum(1 for item in items if item["brand"] == brand)
        for brand in {item["brand"] for item in items}
    ) <= 2
    assert all(item["recommendation_reason"] != "old reason" for item in items)


def test_daily_drop_events_update_impression_memory(client, tmp_path, monkeypatch):
    _install_daily_user(tmp_path, monkeypatch)
    item = client.get(
        "/daily-drop?limit=12",
        headers={"Authorization": "Bearer daily-token"},
    ).get_json()[0]

    seen = client.post(
        "/daily-drop/events",
        json={
            "event": "item_seen",
            "listing_id": item["id"],
            "capsule_id": item["capsule_id"],
            "final_score": item["final_score"],
        },
        headers={"Authorization": "Bearer daily-token"},
    )
    clicked = client.post(
        "/daily-drop/events",
        json={"event": "item_clicked", "listing_id": item["id"]},
        headers={"Authorization": "Bearer daily-token"},
    )

    assert seen.status_code == 200
    assert clicked.status_code == 200
    impressions = srv._load_daily_impressions()
    record = next(r for r in impressions if r["listing_id"] == item["id"])
    assert record["shown_at"] is not None
    assert record["capsule_id"] == item["capsule_id"]
    assert record["clicked"] is True


def test_open_listing_rechecks_availability_and_returns_replacement(client, tmp_path, monkeypatch):
    _install_daily_user(tmp_path, monkeypatch)
    dead_id, replacement_id = srv._emb_ids[:2]
    ranked = [
        {"id": dead_id, "final_score": 1.0},
        {"id": replacement_id, "final_score": 0.9},
    ]

    def is_available(listing, force_network=False):
        return listing["id"] != dead_id

    with (
        patch.object(srv, "_rankings_for_daily_drop", return_value=ranked),
        patch.object(srv, "_listing_is_available", side_effect=is_available),
    ):
        rv = client.post(
            f"/listings/{dead_id}/open",
            json={"exclude_ids": [dead_id], "final_score": 1.0},
            headers={"Authorization": "Bearer daily-token"},
        )

    assert rv.status_code == 200
    data = rv.get_json()
    assert data["available"] is False
    assert data["replacement"]["id"] == replacement_id
    assert dead_id in srv._dead_listing_ids()


def test_repeated_seen_penalty_lowers_daily_candidate_score(client, tmp_path, monkeypatch):
    _install_daily_user(tmp_path, monkeypatch)
    item = srv._compute_rankings(srv._style_vec)[0]
    srv._upsert_daily_impression("daily-user", item["id"], event="item_seen")
    srv._upsert_daily_impression("daily-user", item["id"], event="item_seen")

    prepared = srv._prepare_daily_candidates([item], "daily-user", None)

    assert prepared[0]["_daily_adjusted_score"] < item["final_score"]


# ── recency scoring ──────────────────────────────────────────────────────────

def test_freshness_score_uses_created_at_with_thirty_day_decay(monkeypatch):
    now = datetime(2026, 5, 11, tzinfo=timezone.utc).timestamp()
    listed = datetime(2026, 4, 26, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    monkeypatch.setattr(srv.time, "time", lambda: now)

    assert srv._freshness_score_for_listing({"created_at": listed}) == pytest.approx(0.5)


def test_freshness_score_falls_back_to_scraped_at_or_neutral(monkeypatch):
    now = datetime(2026, 5, 11, tzinfo=timezone.utc).timestamp()
    scraped = now - (15 * 86_400)
    monkeypatch.setattr(srv.time, "time", lambda: now)

    assert srv._freshness_score_for_listing({"created_at": "", "scraped_at": scraped}) == pytest.approx(0.5)
    assert srv._freshness_score_for_listing({"created_at": ""}) == 0.5


def test_daily_drop_new_since_last_visit_uses_created_at_only():
    last_visit = int(datetime(2026, 5, 10, tzinfo=timezone.utc).timestamp())
    new_created = datetime(2026, 5, 11, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")

    assert srv._listing_is_new_since({"created_at": new_created}, last_visit) is True
    assert srv._listing_is_new_since({"created_at": "", "scraped_at": last_visit + 3600}, last_visit) is False


# -- sold demand ----------------------------------------------------------------

def test_sold_demand_score_returns_neutral_for_missing_bucket(monkeypatch):
    monkeypatch.setattr(srv, "_sold_demand", {})
    listing = {"brand": "Nike", "catalog_name": "Herren Schuhe", "category": ""}
    assert srv._sold_demand_score(listing) == 0.5


def test_sold_demand_score_returns_stored_value(monkeypatch):
    brand_key = srv._listing_brand_key({"brand": "carhartt wip"})
    cat_key = srv._listing_category_key({"catalog_name": "Herren Jacken", "category": ""})
    bucket = f"{brand_key}+{cat_key}"
    monkeypatch.setattr(srv, "_sold_demand", {bucket: {"demand_score": 0.82}})
    listing = {"brand": "carhartt wip", "catalog_name": "Herren Jacken", "category": ""}
    assert srv._sold_demand_score(listing) == pytest.approx(0.82)


def test_sold_demand_score_returns_neutral_for_bucket_missing_demand_score(monkeypatch):
    brand_key = srv._listing_brand_key({"brand": "nike"})
    cat_key = srv._listing_category_key({"catalog_name": "Herren Schuhe", "category": ""})
    bucket = f"{brand_key}+{cat_key}"
    monkeypatch.setattr(srv, "_sold_demand", {bucket: {"sold_count": 5}})
    listing = {"brand": "nike", "catalog_name": "Herren Schuhe", "category": ""}
    assert srv._sold_demand_score(listing) == 0.5


# ── _price_band_score ─────────────────────────────────────────────────────────

def test_price_band_score_within_range_returns_1():
    assert srv._price_band_score(50, 10, 100) == 1.0


def test_price_band_score_at_boundaries_returns_1():
    assert srv._price_band_score(10, 10, 100) == 1.0
    assert srv._price_band_score(100, 10, 100) == 1.0


def test_price_band_score_above_max_decays_strongly():
    score = srv._price_band_score(200, 10, 100)
    assert 0.05 <= score < 0.5


def test_price_band_score_above_max_respects_floor():
    assert srv._price_band_score(10_000, 10, 100) >= 0.05


def test_price_band_score_below_min_decays_mildly():
    score = srv._price_band_score(5, 20, 100)
    assert 0.4 <= score < 1.0


def test_price_band_score_no_max_above_min_returns_1():
    assert srv._price_band_score(100, 10, None) == 1.0


def test_price_band_score_no_max_below_min_decays():
    score = srv._price_band_score(5, 20, None)
    assert 0.2 <= score < 1.0


# ── _compute_rankings with price band ────────────────────────────────────────

def test_compute_rankings_price_band_within_range_scores_1():
    vec = np.ones(512, dtype=np.float32)
    vec /= np.linalg.norm(vec)

    ranked = srv._compute_rankings(vec, price_min=0, price_max=1000)

    assert len(ranked) > 0
    for item in ranked:
        assert item["price_score"] == 1.0, (
            f"Expected price_score=1.0 for price €{item['price']}, "
            f"got {item['price_score']}"
        )


def test_compute_rankings_price_band_above_max_penalizes():
    vec = np.ones(512, dtype=np.float32)
    vec /= np.linalg.norm(vec)

    ranked = srv._compute_rankings(vec, price_min=0, price_max=1)
    expensive = [r for r in ranked if r["price"] > 1]

    assert len(expensive) > 0
    assert all(r["price_score"] < 1.0 for r in expensive)


def test_compute_rankings_no_price_range_uses_median_formula():
    vec = np.ones(512, dtype=np.float32)
    vec /= np.linalg.norm(vec)

    ranked = srv._compute_rankings(vec)
    nonzero = [r for r in ranked if r["price"] > 0]

    assert all(r["price_score"] < 1.0 for r in nonzero)


# ── /feed with price range ────────────────────────────────────────────────────

def test_feed_with_price_range_applies_band_scoring(client):
    vec = np.ones(512, dtype=np.float32)
    vec /= np.linalg.norm(vec)

    rv = client.post("/feed", json={
        "style_vector": vec.tolist(),
        "price_min": 0,
        "price_max": 1000,
    })

    assert rv.status_code == 200
    data = rv.get_json()
    assert isinstance(data, list)
    assert len(data) > 0
    within = [r for r in data if 0 <= r["price"] <= 1000]
    assert len(within) > 0
    assert all(r["price_score"] == 1.0 for r in within)


def test_feed_without_price_range_uses_median_formula(client):
    vec = np.ones(512, dtype=np.float32)
    vec /= np.linalg.norm(vec)

    rv = client.post("/feed", json={"style_vector": vec.tolist()})

    assert rv.status_code == 200
    data = rv.get_json()
    nonzero = [r for r in data if r["price"] > 0]
    assert all(r["price_score"] < 1.0 for r in nonzero)


def test_compute_capsule_rankings_returns_capsule_metadata(client):
    if len(srv._emb_ids) < 2:
        pytest.skip("not enough embeddings loaded")

    capsule_a = srv._new_capsule("a", "clean minimal", srv._emb_index[srv._emb_ids[0]], confidence=0.9)
    capsule_b = srv._new_capsule("b", "vintage", srv._emb_index[srv._emb_ids[1]], confidence=0.6)

    ranked = srv._compute_capsule_rankings([capsule_a, capsule_b])

    assert ranked
    assert {"capsule_id", "capsule_label", "capsule_score", "recommendation_reason"} <= ranked[0].keys()
    labels = {item["capsule_label"] for item in ranked[:10]}
    assert labels <= {"clean minimal", "vintage"}


# ── /feedback ────────────────────────────────────────────────────────────────

def test_skip_feedback_marks_seen_without_changing_taste(client, tmp_path, monkeypatch):
    email_hash = _install_daily_user(tmp_path, monkeypatch)
    user_before = srv._load_user(email_hash)
    listing_id = srv._emb_ids[0]

    rv = client.post(
        "/feedback",
        json={"listing_id": listing_id, "action": "dislike"},
        headers={"Authorization": "Bearer daily-token"},
    )

    user_after = srv._load_user(email_hash)
    assert rv.status_code == 200
    assert np.allclose(user_after["style_vector"], user_before["style_vector"])
    assert len(user_after["capsules"]) == len(user_before["capsules"])
    for after_cap, before_cap in zip(user_after["capsules"], user_before["capsules"]):
        assert after_cap["id"] == before_cap["id"]
        assert after_cap["confidence"] == pytest.approx(before_cap["confidence"])
        assert np.allclose(np.array(after_cap["vector"]), np.array(before_cap["vector"]))
    assert listing_id in user_after["seen_listing_ids"]


def test_golden_feedback_applies_higher_weight(client):
    """Golden swipe should nudge the style vector more than a regular like."""
    if not srv._emb_index or srv._style_vec is None:
        pytest.skip("embeddings not loaded in test environment")

    listing_id = srv._emb_ids[0]
    vec_before = srv._style_vec.copy()
    like_count_before = srv._like_count

    try:
        with (
            patch.object(srv, "_append_feedback_log"),
            patch.object(srv, "_rescore_and_save"),
            patch("numpy.save"),
        ):
            client.post(
                "/feedback",
                json={"listing_id": listing_id, "action": "like", "golden": False},
            )
        vec_after_like = srv._style_vec.copy()
        like_delta = float(np.linalg.norm(vec_after_like - vec_before))

        srv._style_vec = vec_before.copy()
        srv._like_count = like_count_before

        with (
            patch.object(srv, "_append_feedback_log"),
            patch.object(srv, "_rescore_and_save"),
            patch("numpy.save"),
        ):
            client.post(
                "/feedback",
                json={"listing_id": listing_id, "action": "like", "golden": True},
            )
        vec_after_golden = srv._style_vec.copy()
        golden_delta = float(np.linalg.norm(vec_after_golden - vec_before))
    finally:
        srv._style_vec = vec_before
        srv._like_count = like_count_before

    assert golden_delta > like_delta, (
        f"golden delta ({golden_delta:.6f}) should exceed like delta ({like_delta:.6f})"
    )


# ── _brand_tier_adj ───────────────────────────────────────────────────────────

def test_brand_tier_adj_known_brand_returns_delta():
    listing = {"brand": "Arc'teryx", "gender": "men"}
    assert srv._brand_tier_adj(listing, "gorpcore") == pytest.approx(0.04)


def test_brand_tier_adj_unknown_brand_returns_zero():
    listing = {"brand": "Zara", "gender": "men"}
    assert srv._brand_tier_adj(listing, "gorpcore") == 0.0


def test_brand_tier_adj_empty_lane_returns_zero():
    listing = {"brand": "Nike", "gender": "men"}
    assert srv._brand_tier_adj(listing, "") == 0.0


def test_brand_tier_adj_nike_vintage_vs_streetwear():
    listing_men = {"brand": "Nike", "gender": "men"}
    assert srv._brand_tier_adj(listing_men, "vintage") == pytest.approx(0.04)
    assert srv._brand_tier_adj(listing_men, "streetwear") == 0.0


def test_brand_tier_adj_old_money_gender_split():
    men_listing = {"brand": "Ralph Lauren", "gender": "men"}
    women_listing = {"brand": "Ralph Lauren", "gender": "women"}
    assert srv._brand_tier_adj(men_listing, "old money") == pytest.approx(0.04)
    assert srv._brand_tier_adj(women_listing, "old money") == 0.0


def test_brand_tier_adj_old_money_women_brand():
    listing = {"brand": "Sandro", "gender": "women"}
    assert srv._brand_tier_adj(listing, "old money") == pytest.approx(0.03)


def test_brand_tier_adj_old_money_unknown_gender_returns_zero():
    listing = {"brand": "Burberry", "gender": ""}
    assert srv._brand_tier_adj(listing, "old money") == 0.0


def test_brand_tier_adj_affects_final_score(monkeypatch):
    vec = np.ones(512, dtype=np.float32)
    vec /= np.linalg.norm(vec)

    base_listing = {
        "id": 1,
        "price": 30.0,
        "favourites": 0,
        "gender": "men",
        "brand": "",
        "title": "vintage jacket",
        "image_url": "",
        "image_urls": [],
        "url": "",
        "catalog_name": "Herren Jacken",
        "category": "",
    }
    listing_no_brand = {**base_listing, "id": 1, "brand": ""}
    listing_nike = {**base_listing, "id": 2, "brand": "Nike"}

    monkeypatch.setattr(srv, "_emb_ids", [1, 2])
    monkeypatch.setattr(srv, "_emb_index", {1: vec, 2: vec})
    monkeypatch.setattr(srv, "_emb_matrix", np.stack([vec, vec]))
    monkeypatch.setattr(srv, "_listings", {1: listing_no_brand, 2: listing_nike})
    monkeypatch.setattr(srv, "_freshness_score_for_listing", lambda _: 1.0)
    monkeypatch.setattr(srv, "_sold_demand", {})
    monkeypatch.setattr(srv, "_quality_scores", {})

    capsule = srv._new_capsule("test", "vintage", vec, price_min=0, price_max=500, confidence=1.0)
    ranked = srv._score_for_capsule(capsule, gender="men", price_min=0, price_max=500)

    scores = {r["id"]: r["final_score"] for r in ranked}
    assert scores[2] > scores[1], "Nike listing should outscore no-brand listing in vintage lane"
    assert scores[2] - scores[1] == pytest.approx(0.04, abs=1e-4)


# ── _explicit_label_boost ─────────────────────────────────────────────────────

def test_explicit_label_boost_fires_on_exact_lane_word():
    listing = {"title": "gorpcore patagonia jacket"}
    assert srv._explicit_label_boost(listing, "gorpcore") == pytest.approx(0.05)


def test_explicit_label_boost_zero_for_inferred_only():
    listing = {"title": "patagonia fleece men"}
    assert srv._explicit_label_boost(listing, "gorpcore") == 0.0


def test_explicit_label_boost_zero_for_wrong_lane():
    listing = {"title": "vintage nike jacket"}
    assert srv._explicit_label_boost(listing, "streetwear") == 0.0


def test_explicit_label_boost_minimal_variant():
    listing = {"title": "minimal aesthetic cos shirt"}
    assert srv._explicit_label_boost(listing, "clean minimal") == pytest.approx(0.05)


def test_explicit_label_boost_old_money_explicit():
    listing = {"title": "old money ralph lauren polo"}
    assert srv._explicit_label_boost(listing, "old money") == pytest.approx(0.05)


def test_explicit_label_boost_separates_inferred_vs_explicit(monkeypatch):
    vec = np.ones(512, dtype=np.float32)
    vec /= np.linalg.norm(vec)

    base = {
        "price": 30.0,
        "favourites": 0,
        "gender": "men",
        "brand": "Patagonia",
        "image_url": "",
        "image_urls": [],
        "url": "",
        "catalog_name": "Herren Jacken",
        "category": "",
    }
    listing_inferred = {**base, "id": 1, "title": "patagonia fleece jacket"}
    listing_explicit = {**base, "id": 2, "title": "gorpcore patagonia fleece jacket"}

    monkeypatch.setattr(srv, "_emb_ids", [1, 2])
    monkeypatch.setattr(srv, "_emb_index", {1: vec, 2: vec})
    monkeypatch.setattr(srv, "_emb_matrix", np.stack([vec, vec]))
    monkeypatch.setattr(srv, "_listings", {1: listing_inferred, 2: listing_explicit})
    monkeypatch.setattr(srv, "_freshness_score_for_listing", lambda _: 1.0)
    monkeypatch.setattr(srv, "_sold_demand", {})
    monkeypatch.setattr(srv, "_quality_scores", {})

    capsule = srv._new_capsule("test", "gorpcore", vec, price_min=0, price_max=500, confidence=1.0)
    ranked = srv._score_for_capsule(capsule, gender="men", price_min=0, price_max=500)

    scores = {r["id"]: r["final_score"] for r in ranked}
    assert scores[2] > scores[1], "Explicit self-label should outscore inferred-only listing"
    assert scores[2] - scores[1] == pytest.approx(0.05, abs=1e-4)
