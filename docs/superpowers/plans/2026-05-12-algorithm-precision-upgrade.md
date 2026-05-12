# Algorithm Precision Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace flat freshness with personalized freshness, replace raw fav count with velocity, add sold-listing demand score as a collaborative proxy signal, and wire a daily sold-listings scrape into the update pipeline.

**Architecture:** Three scoring changes land in `_score_for_capsule` and `_recompute_cached_listing_score` in `server.py`. Two new standalone scripts (`scrape_sold.py`, `build_demand.py`) produce `sold_demand.json`, which the server loads at startup alongside `quality_scores.json`. `daily_update.py` calls both scripts after the existing scrape cycle.

**Tech Stack:** Python 3.12+, NumPy, Flask (existing), httpx, rich (existing), pytest

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `server.py` | Modify | Add constants, `_sold_demand` global, helpers, updated scoring formula |
| `vinted_scraper.py` | Modify | Add `status_ids` param to `VintedClient.search_items` |
| `scrape_sold.py` | Create | Scrape Vinted sold listings → `sold_listings.json` |
| `build_demand.py` | Create | Aggregate `sold_listings.json` → `sold_demand.json` |
| `daily_update.py` | Modify | Call `scrape_sold` + `build_demand` in daily cycle |
| `tests/test_server.py` | Modify | Update broken freshness test, add new scoring tests |
| `tests/test_demand.py` | Create | Unit tests for `build_demand` aggregation logic |

---

## Task 1: Sold demand data layer in server.py

**Files:**
- Modify: `server.py`
- Modify: `tests/test_server.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_server.py`:

```python
# ── sold demand ───────────────────────────────────────────────────────────────

def test_sold_demand_score_returns_neutral_for_missing_bucket(monkeypatch):
    monkeypatch.setattr(srv, "_sold_demand", {})
    listing = {"brand": "Nike", "catalog_name": "Herren Schuhe", "category": ""}
    assert srv._sold_demand_score(listing) == 0.5


def test_sold_demand_score_returns_stored_value(monkeypatch):
    brand_key = srv._listing_brand_key({"brand": "carhartt wip"})
    cat_key   = srv._listing_category_key({"catalog_name": "Herren Jacken", "category": ""})
    bucket    = f"{brand_key}+{cat_key}"
    monkeypatch.setattr(srv, "_sold_demand", {bucket: {"demand_score": 0.82}})
    listing = {"brand": "carhartt wip", "catalog_name": "Herren Jacken", "category": ""}
    assert srv._sold_demand_score(listing) == pytest.approx(0.82)


def test_sold_demand_score_returns_neutral_for_bucket_missing_demand_score(monkeypatch):
    brand_key = srv._listing_brand_key({"brand": "nike"})
    cat_key   = srv._listing_category_key({"catalog_name": "Herren Schuhe", "category": ""})
    bucket    = f"{brand_key}+{cat_key}"
    # bucket exists but no demand_score key
    monkeypatch.setattr(srv, "_sold_demand", {bucket: {"sold_count": 5}})
    listing = {"brand": "nike", "catalog_name": "Herren Schuhe", "category": ""}
    assert srv._sold_demand_score(listing) == 0.5
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/vincenttroger/pilo && python -m pytest tests/test_server.py::test_sold_demand_score_returns_neutral_for_missing_bucket tests/test_server.py::test_sold_demand_score_returns_stored_value tests/test_server.py::test_sold_demand_score_returns_neutral_for_bucket_missing_demand_score -v
```

Expected: `AttributeError: module 'server' has no attribute '_sold_demand'`

- [ ] **Step 3: Add `SOLD_DEMAND_FILE`, `_sold_demand` global, `DEAL_SOLD_DEMAND_WEIGHT` constant, and `_sold_demand_score` helper to server.py**

In `server.py`, after `QUALITY_SCORES_FILE` path constant (around line 41):
```python
SOLD_DEMAND_FILE     = APP_DIR  / "sold_demand.json"
```

In `server.py`, after `FRESHNESS_SCORE_WEIGHT` constant (around line 83):
```python
DEAL_SOLD_DEMAND_WEIGHT = _env_float("PILO_DEAL_SOLD_DEMAND_WEIGHT", 0.20)
```

In `server.py`, after `_quality_scores: dict[int, dict] = {}` global (around line 741):
```python
_sold_demand: dict = {}
```

Add helper function after `_quality_payload` (around line 905):
```python
def _sold_demand_score(listing: dict) -> float:
    key = f"{_listing_brand_key(listing)}+{_listing_category_key(listing)}"
    return float(_sold_demand.get(key, {}).get("demand_score", 0.5) or 0.5)
```

- [ ] **Step 4: Add sold_demand loading in `_load_all`**

In `_load_all`, after the quality scores block (after line 794):
```python
    if SOLD_DEMAND_FILE.exists():
        print("Loading sold demand scores…", flush=True)
        raw_demand = json.loads(SOLD_DEMAND_FILE.read_text(encoding="utf-8"))
        _sold_demand.update(raw_demand)
        print(f"  {len(_sold_demand)} demand buckets", flush=True)
    else:
        print("sold_demand.json not found — sold demand scoring disabled", flush=True)
```

Also add `_sold_demand` to the `global` declaration in `_load_all`:
```python
global _emb_index, _emb_matrix, _emb_ids, _listings, _style_vec, _quality_scores, _sold_demand
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /Users/vincenttroger/pilo && python -m pytest tests/test_server.py::test_sold_demand_score_returns_neutral_for_missing_bucket tests/test_server.py::test_sold_demand_score_returns_stored_value tests/test_server.py::test_sold_demand_score_returns_neutral_for_bucket_missing_demand_score -v
```

Expected: all 3 PASS

- [ ] **Step 6: Run the full test suite to check for regressions**

```bash
cd /Users/vincenttroger/pilo && python -m pytest tests/test_server.py -v
```

Expected: all existing tests still pass

- [ ] **Step 7: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat(scoring): add sold demand data layer — _sold_demand_score helper + startup loading"
```

---

## Task 2: Personalized freshness — update formula and fix existing test

**Files:**
- Modify: `server.py`
- Modify: `tests/test_server.py`

- [ ] **Step 1: Update the existing freshness test that will break**

The test `test_final_score_blends_freshness_at_configured_weight` in `tests/test_server.py` currently expects `FRESHNESS_SCORE_WEIGHT * freshness_score`. After our change, the formula uses `FRESHNESS_SCORE_WEIGHT * capsule_score * freshness_score`. Update it:

Replace the existing `test_final_score_blends_freshness_at_configured_weight` test with:

```python
def test_final_score_blends_personalized_freshness_at_configured_weight(client):
    vec = np.random.rand(512).astype(np.float32)
    vec /= np.linalg.norm(vec)

    with patch.object(srv, "_freshness_score_for_listing", return_value=1.0):
        ranked = srv._compute_rankings(vec)

    item = ranked[0]
    personalized_freshness = item["capsule_score"] * item["freshness_score"]
    expected = (
        srv.CAPSULE_SCORE_WEIGHT * item["capsule_score"]
        + srv.DEAL_SCORE_WEIGHT  * item["deal_score"]
        + srv.FRESHNESS_SCORE_WEIGHT * personalized_freshness
    )
    assert item["freshness_score"] == pytest.approx(1.0)
    assert item["final_score"] == pytest.approx(expected, abs=1e-3)
```

- [ ] **Step 2: Add a test that confirms weak-style items get less freshness boost than strong-style items**

Add to `tests/test_server.py`:

```python
def test_personalized_freshness_rewards_style_match_not_just_recency(monkeypatch):
    vec_strong = np.ones(512, dtype=np.float32)
    vec_strong /= np.linalg.norm(vec_strong)
    vec_weak = -np.ones(512, dtype=np.float32)
    vec_weak /= np.linalg.norm(vec_weak)

    base_listing = {
        "price": 20.0, "favourites": 0, "gender": "men",
        "image_url": "", "image_urls": [], "url": "",
        "catalog_name": "Herren Jacken", "category": "",
        "brand": "", "title": "",
    }
    listings = {
        1: {**base_listing, "id": 1},  # strong style match
        2: {**base_listing, "id": 2},  # weak style match
    }

    monkeypatch.setattr(srv, "_emb_ids",    [1, 2])
    monkeypatch.setattr(srv, "_emb_index",  {1: vec_strong, 2: vec_weak})
    monkeypatch.setattr(srv, "_emb_matrix", np.stack([vec_strong, vec_weak]))
    monkeypatch.setattr(srv, "_listings",   listings)
    monkeypatch.setattr(srv, "_freshness_score_for_listing", lambda _: 1.0)
    monkeypatch.setattr(srv, "_sold_demand", {})

    capsule = srv._new_capsule(
        "test", "test", vec_strong,
        price_min=0, price_max=1000, confidence=1.0,
    )
    ranked = srv._score_for_capsule(capsule, gender="men", price_min=0, price_max=1000)

    strong = next(r for r in ranked if r["id"] == 1)
    weak   = next(r for r in ranked if r["id"] == 2)

    # Both have freshness=1.0, but strong match should get bigger personalized boost
    assert strong["final_score"] > weak["final_score"]
```

- [ ] **Step 3: Run tests to verify the freshness test now fails (pre-implementation)**

```bash
cd /Users/vincenttroger/pilo && python -m pytest tests/test_server.py::test_final_score_blends_personalized_freshness_at_configured_weight tests/test_server.py::test_personalized_freshness_rewards_style_match_not_just_recency -v
```

Expected: FAIL (formula not yet updated)

- [ ] **Step 4: Update weight constants in server.py**

Replace the existing weight constants block (around lines 81–86):
```python
CAPSULE_SCORE_WEIGHT        = _env_float("PILO_CAPSULE_SCORE_WEIGHT",    0.58)
DEAL_SCORE_WEIGHT           = _env_float("PILO_DEAL_SCORE_WEIGHT",       0.27)
FRESHNESS_SCORE_WEIGHT      = _env_float("PILO_FRESHNESS_SCORE_WEIGHT",  0.15)
DEAL_PRICE_SCORE_WEIGHT     = _env_float("PILO_DEAL_PRICE_SCORE_WEIGHT", 0.50)
DEAL_FAV_SCORE_WEIGHT       = _env_float("PILO_DEAL_FAV_SCORE_WEIGHT",   0.30)
DEAL_SOLD_DEMAND_WEIGHT     = _env_float("PILO_DEAL_SOLD_DEMAND_WEIGHT", 0.20)
```

- [ ] **Step 5: Update `_score_for_capsule` to use personalized freshness**

In `_score_for_capsule` (around line 1010–1026), replace:
```python
        freshness_score = _freshness_score_for_listing(listing)
        ...
        final_score = (
            CAPSULE_SCORE_WEIGHT * capsule_score
            + DEAL_SCORE_WEIGHT * deal_score
            + FRESHNESS_SCORE_WEIGHT * freshness_score
            + quality_adj
        )
```

With:
```python
        freshness_score = _freshness_score_for_listing(listing)
        personalized_freshness = capsule_score * freshness_score
        ...
        final_score = (
            CAPSULE_SCORE_WEIGHT * capsule_score
            + DEAL_SCORE_WEIGHT * deal_score
            + FRESHNESS_SCORE_WEIGHT * personalized_freshness
            + quality_adj
        )
```

Note: `freshness_score` is still passed to `_listing_payload` unchanged — the payload field keeps its value for display purposes.

- [ ] **Step 6: Update `_recompute_cached_listing_score` to use personalized freshness**

Replace the `final_score` computation in `_recompute_cached_listing_score` (around line 957–962):
```python
    capsule_score = float(enriched.get("capsule_score", enriched.get("style_score", 0.0)) or 0.0)
    freshness_score = round(_freshness_score_for_listing(listing or {}), 4)
    personalized_freshness = capsule_score * freshness_score
    enriched["freshness_score"] = freshness_score
    enriched["final_score"] = round(
        CAPSULE_SCORE_WEIGHT * capsule_score
        + DEAL_SCORE_WEIGHT * float(enriched.get("deal_score", 0.0) or 0.0)
        + FRESHNESS_SCORE_WEIGHT * personalized_freshness,
        4,
    )
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
cd /Users/vincenttroger/pilo && python -m pytest tests/test_server.py::test_final_score_blends_personalized_freshness_at_configured_weight tests/test_server.py::test_personalized_freshness_rewards_style_match_not_just_recency -v
```

Expected: both PASS

- [ ] **Step 8: Run full test suite**

```bash
cd /Users/vincenttroger/pilo && python -m pytest tests/test_server.py -v
```

Expected: all pass

- [ ] **Step 9: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat(scoring): replace flat freshness with personalized freshness (capsule × freshness)"
```

---

## Task 3: Fav velocity — replace raw fav count with momentum signal

**Files:**
- Modify: `server.py`
- Modify: `tests/test_server.py`

- [ ] **Step 1: Write a failing test for velocity behavior**

Add to `tests/test_server.py`:

```python
def test_fav_velocity_rewards_momentum_over_raw_count(monkeypatch):
    """A listing with 40 favs in 3 days should outscore 40 favs in 60 days when style is equal."""
    import time as _time

    now = _time.time()
    vec = np.ones(512, dtype=np.float32)
    vec /= np.linalg.norm(vec)

    base = {
        "price": 20.0, "gender": "men",
        "image_url": "", "image_urls": [], "url": "",
        "catalog_name": "Herren Jacken", "category": "", "brand": "", "title": "",
    }
    listings = {
        1: {**base, "id": 1, "favourites": 40,
            "created_at": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(now - 3 * 86_400))},
        2: {**base, "id": 2, "favourites": 40,
            "created_at": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(now - 60 * 86_400))},
    }

    monkeypatch.setattr(srv, "_emb_ids",    [1, 2])
    monkeypatch.setattr(srv, "_emb_index",  {1: vec, 2: vec})
    monkeypatch.setattr(srv, "_emb_matrix", np.stack([vec, vec]))
    monkeypatch.setattr(srv, "_listings",   listings)
    monkeypatch.setattr(srv, "_sold_demand", {})

    capsule = srv._new_capsule(
        "test", "test", vec,
        price_min=0, price_max=1000, confidence=1.0,
    )
    ranked = srv._score_for_capsule(capsule, gender="men", price_min=0, price_max=1000)

    fresh_item = next(r for r in ranked if r["id"] == 1)
    stale_item = next(r for r in ranked if r["id"] == 2)
    assert fresh_item["final_score"] > stale_item["final_score"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/vincenttroger/pilo && python -m pytest tests/test_server.py::test_fav_velocity_rewards_momentum_over_raw_count -v
```

Expected: FAIL (both items score identically with raw fav count)

- [ ] **Step 3: Add fav velocity pre-pass and replace fav_score computation in `_score_for_capsule`**

In `_score_for_capsule`, after `max_favs` is computed (around line 979), add the velocity pre-pass:

```python
    # Pre-pass: compute max raw velocity for batch normalization
    _velocities = []
    for _lid in _emb_ids:
        _l = _listings.get(_lid)
        if not _l:
            continue
        _ts = _listing_freshness_timestamp(_l)
        _days = max((time.time() - _ts) / 86_400, 1.0) if _ts else 30.0
        _velocities.append(_l.get("favourites", 0) / _days)
    max_velocity = max(_velocities) if _velocities else 1.0
    max_velocity = max_velocity or 1.0  # zero-division guard
```

Then inside the per-listing loop, replace:
```python
        fav_score = favs / max_favs
```

With:
```python
        ts_listing = _listing_freshness_timestamp(listing)
        days_old = max((time.time() - ts_listing) / 86_400, 1.0) if ts_listing else 30.0
        fav_score = (favs / days_old) / max_velocity
```

`fav_score` keeps its name for payload compatibility. The variable is now velocity-normalized.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/vincenttroger/pilo && python -m pytest tests/test_server.py::test_fav_velocity_rewards_momentum_over_raw_count -v
```

Expected: PASS

- [ ] **Step 5: Run full test suite**

```bash
cd /Users/vincenttroger/pilo && python -m pytest tests/test_server.py -v
```

Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat(scoring): replace raw fav count with velocity (favs/days_old) for momentum signal"
```

---

## Task 4: Wire sold demand into deal score in `_score_for_capsule`

**Files:**
- Modify: `server.py`
- Modify: `tests/test_server.py`

- [ ] **Step 1: Write a failing test**

Add to `tests/test_server.py`:

```python
def test_sold_demand_affects_final_score(monkeypatch):
    vec = np.ones(512, dtype=np.float32)
    vec /= np.linalg.norm(vec)

    base = {
        "price": 20.0, "favourites": 0, "gender": "men",
        "image_url": "", "image_urls": [], "url": "",
        "catalog_name": "Herren Jacken", "category": "", "brand": "carhartt wip", "title": "",
    }
    listings = {1: {**base, "id": 1}}

    brand_key = srv._listing_brand_key(base)
    cat_key   = srv._listing_category_key(base)
    bucket    = f"{brand_key}+{cat_key}"

    monkeypatch.setattr(srv, "_emb_ids",    [1])
    monkeypatch.setattr(srv, "_emb_index",  {1: vec})
    monkeypatch.setattr(srv, "_emb_matrix", np.stack([vec]))
    monkeypatch.setattr(srv, "_listings",   listings)

    capsule = srv._new_capsule(
        "test", "test", vec,
        price_min=0, price_max=1000, confidence=1.0,
    )

    monkeypatch.setattr(srv, "_sold_demand", {bucket: {"demand_score": 0.9}})
    ranked_high = srv._score_for_capsule(capsule, gender="men", price_min=0, price_max=1000)

    monkeypatch.setattr(srv, "_sold_demand", {bucket: {"demand_score": 0.1}})
    ranked_low = srv._score_for_capsule(capsule, gender="men", price_min=0, price_max=1000)

    assert ranked_high[0]["final_score"] > ranked_low[0]["final_score"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/vincenttroger/pilo && python -m pytest tests/test_server.py::test_sold_demand_affects_final_score -v
```

Expected: FAIL (sold demand not yet in formula)

- [ ] **Step 3: Update deal_score formula in `_score_for_capsule`**

Replace:
```python
        deal_score = DEAL_PRICE_SCORE_WEIGHT * price_score + DEAL_FAV_SCORE_WEIGHT * fav_score
```

With:
```python
        sold_demand = _sold_demand_score(listing)
        deal_score = (DEAL_PRICE_SCORE_WEIGHT * price_score
                    + DEAL_FAV_SCORE_WEIGHT   * fav_score
                    + DEAL_SOLD_DEMAND_WEIGHT  * sold_demand)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/vincenttroger/pilo && python -m pytest tests/test_server.py::test_sold_demand_affects_final_score -v
```

Expected: PASS

- [ ] **Step 5: Run full test suite**

```bash
cd /Users/vincenttroger/pilo && python -m pytest tests/test_server.py -v
```

Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat(scoring): add sold demand to deal score formula (20% weight)"
```

---

## Task 5: `build_demand.py` — aggregate sold listings into demand scores

**Files:**
- Create: `build_demand.py`
- Create: `tests/test_demand.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_demand.py`:

```python
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import build_demand as bd


def _make_sold_listings(entries: list[dict]) -> dict:
    return {"meta": {"total": len(entries)}, "listings": entries}


def test_bucket_with_fewer_than_10_items_is_excluded():
    raw = _make_sold_listings([
        {"brand": "nike", "catalog_name": "Herren Schuhe", "category": "",
         "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-04T00:00:00Z"}
        for _ in range(9)
    ])
    result = bd.compute_demand(raw["listings"])
    assert len(result) == 0


def test_bucket_with_10_or_more_items_is_included():
    raw = _make_sold_listings([
        {"brand": "nike", "catalog_name": "Herren Schuhe", "category": "",
         "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-04T00:00:00Z"}
        for _ in range(10)
    ])
    result = bd.compute_demand(raw["listings"])
    assert len(result) == 1


def test_faster_selling_bucket_scores_higher():
    fast = [
        {"brand": "carhartt wip", "catalog_name": "Herren Jacken", "category": "",
         "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-02T00:00:00Z"}
        for _ in range(20)
    ]
    slow = [
        {"brand": "h&m", "catalog_name": "Herren Jacken", "category": "",
         "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-02-01T00:00:00Z"}
        for _ in range(20)
    ]
    result = bd.compute_demand(fast + slow)
    assert result["carhartt wip+herren jacken"]["demand_score"] > result["h&m+herren jacken"]["demand_score"]


def test_higher_sold_count_scores_higher_when_speed_equal():
    many = [
        {"brand": "adidas", "catalog_name": "Herren Schuhe", "category": "",
         "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-03T00:00:00Z"}
        for _ in range(30)
    ]
    few = [
        {"brand": "puma", "catalog_name": "Herren Schuhe", "category": "",
         "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-03T00:00:00Z"}
        for _ in range(10)
    ]
    result = bd.compute_demand(many + few)
    assert result["adidas+herren schuhe"]["demand_score"] > result["puma+herren schuhe"]["demand_score"]


def test_demand_score_is_between_0_and_1():
    listings = [
        {"brand": "zara", "catalog_name": "Herren Oberteil", "category": "",
         "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-05T00:00:00Z"}
        for _ in range(15)
    ]
    result = bd.compute_demand(listings)
    for bucket in result.values():
        assert 0.0 <= bucket["demand_score"] <= 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/vincenttroger/pilo && python -m pytest tests/test_demand.py -v
```

Expected: `ModuleNotFoundError: No module named 'build_demand'`

- [ ] **Step 3: Create `build_demand.py`**

```python
"""
Build Demand Scores
===================
Reads sold_listings.json, groups by brand+category bucket, and computes
a demand_score per bucket: 0.6 × sold_count_norm + 0.4 × speed_norm.

Buckets with fewer than MIN_SOLD items are excluded.
Writes sold_demand.json.

Run:
    python3 build_demand.py
"""

import json
import re
from pathlib import Path

SOLD_LISTINGS_FILE = Path("sold_listings.json")
SOLD_DEMAND_FILE   = Path("sold_demand.json")
MIN_SOLD           = 10


def _norm_key(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _brand_key(listing: dict) -> str:
    return _norm_key(listing.get("brand"))


def _category_key(listing: dict) -> str:
    category = listing.get("category") or listing.get("catalog_name") or ""
    return _norm_key(category)


def _days_to_sell(listing: dict) -> float | None:
    created = listing.get("created_at") or listing.get("scraped_at")
    sold    = listing.get("updated_at") or listing.get("sold_at")
    if not created or not sold:
        return None
    try:
        from datetime import datetime, timezone
        def _parse(v) -> float:
            if isinstance(v, (int, float)):
                return float(v)
            s = str(v).replace("Z", "+00:00")
            return datetime.fromisoformat(s).timestamp()
        created_ts = _parse(created)
        sold_ts    = _parse(sold)
        days = (sold_ts - created_ts) / 86_400
        return max(days, 0.5)
    except Exception:
        return None


def compute_demand(listings: list[dict]) -> dict:
    """
    Returns a dict keyed by 'brand+category' with demand metadata.
    Buckets with fewer than MIN_SOLD entries are excluded.
    """
    buckets: dict[str, list[float]] = {}

    for listing in listings:
        brand = _brand_key(listing)
        cat   = _category_key(listing)
        if not brand and not cat:
            continue
        key  = f"{brand}+{cat}"
        days = _days_to_sell(listing)
        if days is None:
            continue
        buckets.setdefault(key, []).append(days)

    # Filter buckets below threshold
    valid = {k: v for k, v in buckets.items() if len(v) >= MIN_SOLD}
    if not valid:
        return {}

    # Per-bucket stats
    stats: dict[str, dict] = {}
    for key, days_list in valid.items():
        stats[key] = {
            "sold_count":        len(days_list),
            "avg_days_to_sell":  round(sum(days_list) / len(days_list), 2),
        }

    # Normalize across buckets
    max_count = max(s["sold_count"]       for s in stats.values()) or 1
    # Speed: invert avg_days — shorter = faster = higher speed value
    speeds    = {k: 1.0 / s["avg_days_to_sell"] for k, s in stats.items()}
    max_speed = max(speeds.values()) or 1.0

    result: dict[str, dict] = {}
    for key, s in stats.items():
        count_norm = s["sold_count"] / max_count
        speed_norm = speeds[key]    / max_speed
        demand     = round(0.6 * count_norm + 0.4 * speed_norm, 4)
        result[key] = {
            "demand_score":     demand,
            "sold_count":       s["sold_count"],
            "avg_days_to_sell": s["avg_days_to_sell"],
        }

    return result


def run() -> None:
    if not SOLD_LISTINGS_FILE.exists():
        print(f"✗ {SOLD_LISTINGS_FILE} not found — run scrape_sold.py first")
        return

    raw = json.loads(SOLD_LISTINGS_FILE.read_text(encoding="utf-8"))
    listings = raw.get("listings", [])
    print(f"Loaded {len(listings)} sold listings")

    result = compute_demand(listings)
    print(f"Computed demand for {len(result)} buckets (≥{MIN_SOLD} sold each)")

    SOLD_DEMAND_FILE.write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"✅ Saved → {SOLD_DEMAND_FILE}")


if __name__ == "__main__":
    run()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/vincenttroger/pilo && python -m pytest tests/test_demand.py -v
```

Expected: all 5 PASS

- [ ] **Step 5: Commit**

```bash
git add build_demand.py tests/test_demand.py
git commit -m "feat(demand): add build_demand.py — aggregate sold listings into demand scores per brand+category"
```

---

## Task 6: `scrape_sold.py` — scrape Vinted sold listings

**Files:**
- Modify: `vinted_scraper.py`
- Create: `scrape_sold.py`

- [ ] **Step 1: Add `status_ids` parameter to `VintedClient.search_items` in `vinted_scraper.py`**

In `VintedClient.search_items`, add the parameter and append to params:

```python
def search_items(
    self,
    catalog_id: int,
    page: int = 1,
    size_ids: list[int] = None,
    search_text: str | None = None,
    status_ids: list[int] | None = None,   # ← new
) -> dict:
    params = [
        ("search_text", search_text or CONFIG["search_text"]),
        ("country_id",  CONFIG["country_id"]),
        ("currency",    CONFIG["currency"]),
        ("order",       CONFIG["order"]),
        ("page",        page),
        ("per_page",    CONFIG["per_page"]),
        ("with_photo",  "true"),
        ("catalog_ids[]", catalog_id),
    ]
    for sid in (size_ids or []):
        params.append(("size_ids[]", sid))
    for st in (status_ids or []):      # ← new
        params.append(("status_ids[]", st))
    # ... rest of method unchanged
```

Vinted sold status ID is `3`. Verify this by checking a sold listing response during a test run — if results come back empty, try `status_ids=[2, 3]`.

- [ ] **Step 2: Create `scrape_sold.py`**

```python
"""
Scrape Sold Listings
====================
Scrapes Vinted sold listings across the same catalog targets used by
daily_update.py. Only collects the fields needed for demand scoring:
brand, catalog_name, price, created_at, updated_at.

Appends to sold_listings.json (creates if absent). Safe to re-run —
deduplicates by listing ID.

Run:
    python3 scrape_sold.py
"""

import json
import random
import time
from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from vinted_scraper import VintedClient, iter_search_targets, CONFIG as SCRAPE_CONFIG

console = Console()

SOLD_LISTINGS_FILE = Path("sold_listings.json")
SOLD_STATUS_ID     = 3       # Vinted status_id for sold items
MAX_PAGES          = 3       # pages per catalog target (sold items are static — no stale streak needed)
TARGET_TOTAL       = 5_000   # stop early once we have this many sold items


def _load_existing() -> dict:
    if SOLD_LISTINGS_FILE.exists():
        try:
            return json.loads(SOLD_LISTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"meta": {"total": 0}, "listings": []}


def _save(data: dict) -> None:
    SOLD_LISTINGS_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def run() -> None:
    console.rule("[bold]Pilo Sold Listings Scraper[/bold]")

    existing_data = _load_existing()
    existing_ids  = {l["id"] for l in existing_data["listings"]}
    console.print(f"Existing sold listings: [cyan]{len(existing_ids)}[/cyan]")

    client = VintedClient(proxies=SCRAPE_CONFIG.get("proxies", []))
    client.bootstrap_session()
    time.sleep(random.uniform(2, 4))

    new_listings: list[dict] = []
    new_ids: set[int] = set()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        targets = list(iter_search_targets())
        task = progress.add_task("Scraping sold…", total=len(targets))

        for target in targets:
            if len(new_listings) + len(existing_ids) >= TARGET_TOTAL:
                progress.update(task, description="Target reached — stopping")
                break

            catalog_id   = target["catalog_id"]
            catalog_name = target["catalog_name"]
            size_ids     = target["size_ids"]

            for page in range(1, MAX_PAGES + 1):
                try:
                    data = client.search_items(
                        catalog_id=catalog_id,
                        page=page,
                        size_ids=size_ids,
                        status_ids=[SOLD_STATUS_ID],
                    )
                except Exception as e:
                    console.print(f"[red]  Request failed: {e}[/red]")
                    break

                items = data.get("items", [])
                if not items:
                    break

                for raw in items:
                    lid = raw.get("id")
                    if lid is None or lid in existing_ids or lid in new_ids:
                        continue
                    new_ids.add(lid)
                    price_field = raw.get("price", {})
                    try:
                        price = float(price_field.get("amount", 0)) if isinstance(price_field, dict) else float(str(price_field or 0).replace(",", "."))
                    except (ValueError, TypeError):
                        price = 0.0
                    catalog = raw.get("catalog") or raw.get("category") or {}
                    new_listings.append({
                        "id":           lid,
                        "brand":        raw.get("brand_title") or raw.get("brand", {}).get("title", ""),
                        "catalog_name": catalog_name,
                        "category":     catalog.get("title", ""),
                        "price":        price,
                        "created_at":   raw.get("created_at_ts") or raw.get("created_at", ""),
                        "updated_at":   raw.get("updated_at_ts") or raw.get("updated_at", ""),
                    })

                time.sleep(random.uniform(SCRAPE_CONFIG["delay_min"], SCRAPE_CONFIG["delay_max"]))

            progress.advance(task, description=f"{catalog_name}")

    existing_data["listings"].extend(new_listings)
    existing_data["meta"]["total"] = len(existing_data["listings"])
    _save(existing_data)

    console.print(f"\n[green]✅ {len(new_listings)} new sold listings → {SOLD_LISTINGS_FILE} ({len(existing_data['listings'])} total)[/green]")


if __name__ == "__main__":
    run()
```

- [ ] **Step 3: Verify scraper runs without crashing (dry smoke test)**

```bash
cd /Users/vincenttroger/pilo && python3 -c "import scrape_sold; print('import OK')"
```

Expected: `import OK`

- [ ] **Step 4: Commit**

```bash
git add vinted_scraper.py scrape_sold.py
git commit -m "feat(scraper): add scrape_sold.py and status_ids param to VintedClient.search_items"
```

---

## Task 7: Wire into `daily_update.py`

**Files:**
- Modify: `daily_update.py`

- [ ] **Step 1: Add imports and a new step function**

In `daily_update.py`, after the existing imports block, add:

```python
from scrape_sold   import run as scrape_sold_run
from build_demand  import run as build_demand_run
```

After `update_style()` function definition (around line 187), add:

```python
def update_demand():
    console.print("\n[bold]Step 4 — Scrape sold listings + rebuild demand scores[/bold]")
    scrape_sold_run()
    build_demand_run()
```

- [ ] **Step 2: Call `update_demand()` in `run()`**

In the `run()` function, after `update_style()` is called (around line 240), add:

```python
    # ── 4. sold listings + demand scores ──────────────────────────────────────
    update_demand()
```

- [ ] **Step 3: Verify the module imports cleanly**

```bash
cd /Users/vincenttroger/pilo && python3 -c "import daily_update; print('import OK')"
```

Expected: `import OK`

- [ ] **Step 4: Run the full test suite one final time**

```bash
cd /Users/vincenttroger/pilo && python -m pytest tests/ -v
```

Expected: all tests pass

- [ ] **Step 5: Commit**

```bash
git add daily_update.py
git commit -m "feat(pipeline): wire scrape_sold + build_demand into daily update cycle"
```
