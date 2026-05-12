# Algorithm Precision Upgrade

**Date:** 2026-05-12

## Overview

Three parallel signal improvements to the ranking formula, each replacing a weak component with a more precise one. FashionCLIP is already implemented. This spec covers the remaining three upgrades.

**Current formula:**
```
final = 0.612 × capsule_score + 0.288 × deal(price, fav_count) + 0.10 × freshness(flat) + quality_adj
```

**New formula:**
```
personalized_freshness = capsule_score × freshness_score
deal_score             = 0.50 × price + 0.30 × fav_velocity + 0.20 × sold_demand
final = 0.58 × capsule_score + 0.27 × deal_score + 0.15 × personalized_freshness + quality_adj
```

Style stays dominant. Weights sum to 1.0 (quality_adj is a small delta, not a weighted term).

---

## Signal 1: Personalized Freshness

### Problem
Current `freshness_score` is a flat additive term — the same bonus for every item regardless of style relevance. A 3-day-old item that doesn't match your taste gets the same freshness lift as one that does.

### Design
Multiply freshness by capsule score before it enters the formula:

```python
freshness_score = _freshness_score_for_listing(listing)   # unchanged helper
personalized_freshness = capsule_score * freshness_score
```

`_freshness_score_for_listing` remains untouched (30-day linear decay from `created_at` / `scraped_at`).

### Effect
| capsule_score | freshness_score | personalized_freshness |
|---|---|---|
| 0.85 (strong match) | 0.91 (3 days old) | 0.77 |
| 0.30 (weak match) | 0.91 (3 days old) | 0.27 |
| 0.85 (strong match) | 0.17 (25 days old) | 0.14 |

New items that don't match your style get almost no freshness boost. New items that do match get a strong boost. Old strong matches still carry on style score alone.

### Weight change
`FRESHNESS_SCORE_WEIGHT`: 0.10 → 0.15. Justified because the personalized product is a more discriminating signal than the flat version — higher weight is appropriate.

---

## Signal 2: Fav Velocity

### Problem
`fav_score = favourites / max_favourites` normalizes raw count across the batch. A listing with 40 favs in 3 days scores identically to one with 40 favs in 60 days. Momentum is invisible.

### Design
Replace raw fav count with velocity — favourites per day since listed:

```python
ts = _listing_freshness_timestamp(listing)
days_old = max((time.time() - ts) / 86_400, 1.0) if ts else 30.0
raw_velocity = listing.get("favourites", 0) / days_old
fav_velocity_score = raw_velocity / max_velocity   # batch-normalized
```

`max_velocity` is computed in a single pre-pass over all scored entries, same pattern as `max_favs` today.

**Fallback:** if `ts` is missing, `days_old = 30.0` (neutral assumption — no penalty, no reward).

### Weight change
`DEAL_FAV_SCORE_WEIGHT`: 0.40 → 0.30 of deal score. The 10% freed up goes to sold demand.

---

## Signal 3: Sold Demand Score

### Problem
There is no signal for whether items in a given brand+category combination actually sell. Collaborative filtering requires users; sold listings on Vinted are a proxy that requires none.

### Design

#### `scrape_sold.py` (new)
Scrapes Vinted sold listings using the same pattern as the existing listings scraper, filtered to sold status. Collects per listing: brand, category, price, condition, `created_at`, `sold_at` (or `updated_at` as proxy). Writes to `sold_listings.json`.

#### `build_demand.py` (new)
Reads `sold_listings.json`. Groups by bucket key `"{brand_key}+{category_key}"` using the same normalization helpers already in `server.py` (`_listing_brand_key`, `_listing_category_key`).

Per bucket computes:
```
demand_score = 0.6 × normalize(sold_count) + 0.4 × normalize(1 / avg_days_to_sell)
```

Faster-selling + more sold = higher demand. Buckets with fewer than 10 sold items are excluded — not enough signal to trust.

Writes `sold_demand.json`:
```json
{
  "carhartt+jacket": {"demand_score": 0.82, "sold_count": 145, "avg_days_to_sell": 2.3},
  "nike+shoes":      {"demand_score": 0.71, "sold_count": 89,  "avg_days_to_sell": 4.1}
}
```

#### `server.py` additions
`_sold_demand: dict = {}` loaded at startup alongside `_quality_scores` (same pattern).

New helper:
```python
def _sold_demand_score(listing: dict) -> float:
    key = f"{_listing_brand_key(listing)}+{_listing_category_key(listing)}"
    return _sold_demand.get(key, {}).get("demand_score", 0.5)
```

Missing bucket → `0.5` (neutral). No data never penalizes a listing.

#### `daily_update.py`
Add calls to run `scrape_sold` and `build_demand` in the daily update cycle, after the main listings scrape.

### Weight
`DEAL_SOLD_DEMAND_WEIGHT = 0.20` of deal score (new constant).

---

## Updated Constants

```python
CAPSULE_SCORE_WEIGHT        = 0.58   # was 0.612
DEAL_SCORE_WEIGHT           = 0.27   # was 0.288
FRESHNESS_SCORE_WEIGHT      = 0.15   # was 0.10
DEAL_PRICE_SCORE_WEIGHT     = 0.50   # was 0.60
DEAL_FAV_SCORE_WEIGHT       = 0.30   # was 0.40
DEAL_SOLD_DEMAND_WEIGHT     = 0.20   # new
```

---

## Full Scoring Formula

```python
# Fav velocity (pre-pass to find max_velocity across all entries)
# max_velocity = max(raw_velocity for all entries) or 1.0  ← zero-division guard
ts = _listing_freshness_timestamp(listing)
days_old = max((time.time() - ts) / 86_400, 1.0) if ts else 30.0
raw_velocity = favourites / days_old
fav_velocity_score = raw_velocity / max_velocity

# Sold demand
sold_demand = _sold_demand_score(listing)

# Personalized freshness
freshness_score = _freshness_score_for_listing(listing)
personalized_freshness = capsule_score * freshness_score

# Deal
deal_score = (DEAL_PRICE_SCORE_WEIGHT * price_score
            + DEAL_FAV_SCORE_WEIGHT   * fav_velocity_score
            + DEAL_SOLD_DEMAND_WEIGHT * sold_demand)

# Final
final_score = (CAPSULE_SCORE_WEIGHT   * capsule_score
             + DEAL_SCORE_WEIGHT      * deal_score
             + FRESHNESS_SCORE_WEIGHT * personalized_freshness
             + quality_adj)
```

---

## Files Changed

| File | Change |
|---|---|
| `server.py` | Update constants, `_score_for_capsule`, `_recompute_cached_listing_score`, `_load_all`, add `_sold_demand_score` + `_fav_velocity_score` helpers, add `_sold_demand` global |
| `scrape_sold.py` | New — scrapes Vinted sold listings into `sold_listings.json` |
| `build_demand.py` | New — aggregates sold data into `sold_demand.json` |
| `daily_update.py` | Add calls to `scrape_sold` and `build_demand` in daily cycle |

`style_scorer.py` and `feedback_loop.py` are not touched — offline tools that don't use the server scoring path.
