"""
Pilo API Server
===============
Flask server on port 5001 that serves the ranked feed, accepts swipe
feedback to update the style vector, and persists saves.

Run:
    python3 server.py

Dependencies:
    pip install flask flask-cors numpy
"""

import hashlib
import json
import os
import random
import re
import threading
import time
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from flask import Flask, jsonify, request
from flask_cors import CORS

# ─── Config ──────────────────────────────────────────────────────────────────

# APP_DIR: git checkout — read-only source for large static files
# DATA_DIR: persistent volume — all mutable state is written here
APP_DIR  = Path(__file__).parent
DATA_DIR = Path(os.environ.get("DATA_DIR", APP_DIR))
DATA_DIR.mkdir(parents=True, exist_ok=True)

EMBEDDINGS_FILE      = APP_DIR  / "embeddings.json"
LISTINGS_FILE        = APP_DIR  / "listings.json"
QUALITY_SCORES_FILE  = APP_DIR  / "quality_scores.json"
SOLD_DEMAND_FILE     = APP_DIR  / "sold_demand.json"
STYLE_VECTOR_FILE = DATA_DIR / "style_vector.npy"   # updated by /feedback
ONBOARDING_EMBEDDINGS_FILE = APP_DIR / "onboarding_embeddings.json"
SAVED_FILE        = DATA_DIR / "saved.json"          # updated by /save
FEEDBACK_LOG_FILE = DATA_DIR / "feedback_log.json"   # append-only reason log
USERS_DIR         = DATA_DIR / "users"
USERS_DIR.mkdir(parents=True, exist_ok=True)


def _style_results_path() -> Path:
    """DATA_DIR version once it exists (post-rescore), else the git seed."""
    p = DATA_DIR / "style_results.json"
    return p if p.exists() else APP_DIR / "style_results.json"


def _daily_drops_file() -> Path:
    return DATA_DIR / "daily_drops.json"


def _daily_impressions_file() -> Path:
    return DATA_DIR / "daily_drop_impressions.json"


def _daily_events_file() -> Path:
    return DATA_DIR / "daily_drop_events.jsonl"


def _dead_listings_file() -> Path:
    return DATA_DIR / "dead_listings.json"


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


LIKE_WEIGHT    = 0.3    # how much each like nudges the style vector
GOLDEN_WEIGHT  = 0.55   # golden swipe nudges harder: confirmed style + price fit
SKIP_WEIGHT    = -0.04  # small negative nudge away from skipped items
LIKE_EMA_ALPHA = 0.18
GOLDEN_EMA_ALPHA = 0.32
SKIP_EMA_ALPHA = 0.08

# Skip reasons that carry zero style signal — don't touch vector or confidence
_SKIP_NO_SIGNAL: frozenset[str] = frozenset({
    "wrong_size", "size", "sizing", "too_small", "too_big",
    "already_seen", "seen", "duplicate",
    "bad_photo", "photo", "image",
    "not_now", "later", "save_for_later",
})
# Skip reasons that are explicit style rejections — apply full vector repulsion
_SKIP_STYLE_DISLIKE: frozenset[str] = frozenset({
    "not_my_style", "ugly", "dislike", "wrong_style",
    "dont_like", "not_my_taste", "style",
})
CAPSULE_SCORE_WEIGHT = _env_float("PILO_CAPSULE_SCORE_WEIGHT", 0.58)
DEAL_SCORE_WEIGHT = _env_float("PILO_DEAL_SCORE_WEIGHT", 0.27)
FRESHNESS_SCORE_WEIGHT = _env_float("PILO_FRESHNESS_SCORE_WEIGHT", 0.15)
DEAL_PRICE_SCORE_WEIGHT = _env_float("PILO_DEAL_PRICE_SCORE_WEIGHT", 0.50)
DEAL_FAV_SCORE_WEIGHT = _env_float("PILO_DEAL_FAV_SCORE_WEIGHT", 0.30)
DEAL_SOLD_DEMAND_WEIGHT = _env_float("PILO_DEAL_SOLD_DEMAND_WEIGHT", 0.20)
STEAL_FAV_THRESHOLD = _env_float("PILO_STEAL_FAV_THRESHOLD", 0.85)
# Minimum capsule_score for full deal bonus; below 0 → no deal boost, linearly ramps up to this threshold
DEAL_STYLE_GATE = _env_float("PILO_DEAL_STYLE_GATE", 0.10)
FRESHNESS_WINDOW_DAYS = _env_float("PILO_FRESHNESS_WINDOW_DAYS", 30.0)
LISTING_REFRESH_TTL_HOURS = _env_float("PILO_LISTING_REFRESH_TTL_HOURS", 6.0)
RESCORE_EVERY  = 5      # re-rank all listings every N likes
TOP_N          = 50     # entries returned by /feed and stored in style_results.json
PRICE_MEDIAN   = 30.0   # € — used in deal scoring formula
MAX_CAPSULES   = 5
DEFAULT_CAPSULE_CONFIDENCE = 0.45
MIN_CAPSULE_CONFIDENCE = 0.12
MAX_CAPSULE_CONFIDENCE = 1.0
DAILY_DROP_DEFAULT_SIZE = 12
DAILY_DROP_MIN_SIZE = 10
DAILY_DROP_MAX_SIZE = 15
DAILY_DROP_POOL_SIZE = 240
REPEATED_SEEN_PENALTY = 0.075
FRESHNESS_BOOST = 0.065
DAILY_DROP_TAGS = {
    "fresh": "Close match",
    "hidden": "Hidden gem",
    "seller": "Adjacent find",
    "explore": "Wildcard",
}
DAILY_DROP_EVENTS = {
    "daily_drop_opened",
    "item_seen",
    "item_clicked",
    "item_liked",
    "item_disliked",
    "drop_completed",
    "explore_more_clicked",
}
MAX_USER_SEEN_IDS = 5000

STYLE_LABELS = {
    "gorpcore": "gorpcore",
    "minimal": "clean minimal",
    "old money": "old money",
    "smart casual": "smart casual",
    "streetwear": "streetwear",
    "vintage": "vintage",
    "y2k": "y2k",
}

STYLE_KEYWORDS = {
    "gorpcore": (
        "gorpcore",
        "outdoor",
        "hiking",
        "mountain",
        "fleece",
        "polaire",
        "softshell",
        "windbreaker",
        "gore",
        "patagonia",
        "the north face",
        "columbia",
        "arcteryx",
        "arc'teryx",
        "salomon",
    ),
    "minimal": (
        "minimal",
        "plain",
        "basic",
        "clean",
        "blank",
        "essential",
        "essentials",
        "uniqlo",
        "cos",
        "arketype",
    ),
    "old money": (
        "old money",
        "ralph lauren",
        "polo ralph lauren",
        "lacoste",
        "fred perry",
        "tommy hilfiger",
        "polo",
        "chino",
        "blazer",
        "quarter zip",
        "strick",
        "knit",
        "hemd",
    ),
    "smart casual": (
        "smart casual",
        "blazer",
        "chino",
        "trouser",
        "shirt",
        "hemd",
        "polo",
        "knit",
        "tailored",
        "coat",
        "mantel",
    ),
    "streetwear": (
        "streetwear",
        "oversize",
        "oversized",
        "baggy",
        "hoodie",
        "sweatshirt",
        "graphic",
        "skate",
        "corteiz",
        "stussy",
        "carhartt",
        "cargo",
        "workwear",
    ),
    "vintage": (
        "vintage",
        "retro",
        "90s",
        "80s",
        "70s",
        "oldschool",
        "old school",
        "washed",
        "distressed",
    ),
    "y2k": (
        "y2k",
        "00s",
        "2000s",
        "baggy",
        "low waist",
        "lowwaist",
        "flare",
        "flared",
        "bootcut",
        "rave",
        "pasha",
    ),
}

ITEM_TYPE_KEYWORDS = {
    "outerwear": (
        "jacket",
        "jacke",
        "veste",
        "windbreaker",
        "fleece",
        "polaire",
        "puffer",
        "doudoune",
        "coat",
        "mantel",
        "parka",
        "blazer",
        "overshirt",
    ),
    "pants": (
        "jeans",
        "hose",
        "hosen",
        "pants",
        "trouser",
        "trousers",
        "cargo",
        "chino",
        "jogger",
        "trackpant",
        "track pant",
        "bootcut",
        "flare",
        "flared",
    ),
    "tops": (
        "t-shirt",
        "tee",
        "shirt",
        "hemd",
        "polo",
        "hoodie",
        "sweatshirt",
        "sweater",
        "sweat",
        "knit",
        "strick",
        "pullover",
        "half zip",
        "quarter zip",
    ),
}

# Terms a seller might explicitly write to self-label their listing's aesthetic.
# Distinct from STYLE_KEYWORDS (which detects style via brands, garment types, etc.)
# Used to give a small extra boost when a seller consciously positions an item.
LANE_EXPLICIT_TERMS: dict[str, tuple[str, ...]] = {
    "gorpcore": ("gorpcore",),
    "old money": ("old money", "oldmoney"),
    "streetwear": ("streetwear",),
    "vintage": ("vintage", "retro"),
    "y2k": ("y2k",),
    "clean minimal": ("minimal", "minimalist"),
    "smart casual": ("smart casual",),
}

# Lane-specific brand prestige boosts. Added directly to final_score.
# Max delta is +0.04 — nudges ranking, doesn't dominate.
# "old money" is gender-split: Ralph Lauren is a men's signal; women's cluster
# around French contemporary and heritage tailoring brands.
# Nike/Adidas are vintage signals only — mass market elsewhere, no prestige boost.
BRAND_TIERS: dict[str, dict[str, float]] = {
    "gorpcore": {
        "arc'teryx": 0.04,
        "arcteryx": 0.04,
        "patagonia": 0.03,
        "the north face": 0.03,
        "salomon": 0.03,
        "columbia": 0.02,
        "mammut": 0.02,
        "fjallraven": 0.02,
        "fjällräven": 0.02,
        "helly hansen": 0.02,
    },
    "old money:men": {
        "ralph lauren": 0.04,
        "polo ralph lauren": 0.04,
        "lacoste": 0.04,
        "fred perry": 0.03,
        "barbour": 0.03,
        "burberry": 0.03,
        "hackett": 0.02,
        "gant": 0.02,
    },
    "old money:women": {
        "burberry": 0.04,
        "max mara": 0.04,
        "toteme": 0.03,
        "totême": 0.03,
        "sandro": 0.03,
        "maje": 0.03,
        "ba&sh": 0.03,
        "a.p.c.": 0.03,
        "apc": 0.03,
        "claudie pierlot": 0.02,
        "gerard darel": 0.02,
    },
    "streetwear": {
        "stone island": 0.04,
        "carhartt": 0.04,
        "stussy": 0.04,
        "corteiz": 0.04,
        "palace": 0.03,
        "supreme": 0.03,
        "off-white": 0.03,
        "off white": 0.03,
        "bape": 0.03,
        "a bathing ape": 0.03,
        "dickies": 0.02,
        "new era": 0.02,
    },
    "vintage": {
        "nike": 0.04,
        "adidas": 0.04,
        "levi's": 0.03,
        "levis": 0.03,
        "champion": 0.03,
        "tommy hilfiger": 0.03,
        "ralph lauren": 0.03,
        "fila": 0.02,
        "kappa": 0.02,
        "ellesse": 0.02,
        "sergio tacchini": 0.02,
        "le coq sportif": 0.02,
        "wrangler": 0.02,
        "lee": 0.02,
    },
    "y2k": {
        "diesel": 0.04,
        "von dutch": 0.03,
        "ed hardy": 0.03,
        "true religion": 0.02,
        "miss sixty": 0.02,
        "g-star": 0.02,
        "g-star raw": 0.02,
    },
    "clean minimal": {
        "cos": 0.04,
        "lemaire": 0.04,
        "our legacy": 0.04,
        "arket": 0.03,
        "a.p.c.": 0.03,
        "apc": 0.03,
        "uniqlo": 0.03,
        "muji": 0.02,
        "weekday": 0.02,
    },
}


def _bounded_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(parsed, minimum), maximum)


def _decode_embedded_json(raw: str) -> dict:
    unescaped = raw.replace('\\"', '"').replace("\\/", "/")
    unescaped = unescaped.encode("utf-8").decode("unicode_escape")
    return json.loads(unescaped)


def _extract_plugin_data(html: str, plugin_name: str) -> dict | None:
    match = re.search(
        rf'\\"name\\":\\"{re.escape(plugin_name)}\\".*?\\"data\\":(\{{.*?\}}),\\"exposure\\"',
        html,
    )
    if not match:
        return None
    try:
        return _decode_embedded_json(match.group(1))
    except Exception:
        return None


def _is_listing_available_from_html(html: str) -> bool | None:
    item_status = _extract_plugin_data(html, "item_status")
    ask_seller = _extract_plugin_data(html, "ask_seller")

    status_blocks = [block for block in (item_status, ask_seller) if block]
    if not status_blocks:
        return None

    for block in status_blocks:
        if block.get("is_closed") or block.get("is_hidden") or block.get("is_reserved"):
            return False

    if item_status and item_status.get("transaction_permitted") is False:
        return False

    if ask_seller and ask_seller.get("can_buy") is False:
        return False

    return True


def _fetch_listing_available(listing: dict) -> bool:
    listing_id = listing.get("id")
    url = listing.get("url") or f"https://www.vinted.de/items/{listing_id}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=6) as resp:
        html = resp.read().decode("utf-8", "ignore")
    available = _is_listing_available_from_html(html)
    return True if available is None else available


def _load_dead_listing_records() -> dict:
    data = _read_json_file(_dead_listings_file(), {})
    return data if isinstance(data, dict) else {}


def _dead_listing_ids() -> set[int]:
    ids: set[int] = set()
    for raw_id in _load_dead_listing_records().keys():
        try:
            ids.add(int(raw_id))
        except (TypeError, ValueError):
            continue
    return ids


def _listing_is_marked_dead(listing_id: int | None) -> bool:
    return listing_id is not None and listing_id in _dead_listing_ids()


def _mark_listing_dead(listing_id: int | None, reason: str = "unavailable") -> None:
    if listing_id is None:
        return
    records = _load_dead_listing_records()
    records[str(listing_id)] = {
        "listing_id": listing_id,
        "marked_at": int(time.time()),
        "reason": reason,
    }
    _write_json_file(_dead_listings_file(), records)
    _availability_cache[listing_id] = (time.time() + 24 * 60 * 60, False)


def _listing_needs_availability_refresh(listing: dict) -> bool:
    scraped_ts = _parse_timestamp(listing.get("scraped_at"))
    if scraped_ts is None:
        return True
    max_age_seconds = max(LISTING_REFRESH_TTL_HOURS, 0.0) * 60 * 60
    return (time.time() - scraped_ts) >= max_age_seconds


def _listing_passes_availability_gate(listing: dict) -> bool:
    listing_id = listing.get("id")
    if listing_id is None:
        return True
    if _listing_is_marked_dead(listing_id):
        return False

    cached = _availability_cache.get(listing_id)
    if cached and cached[0] > time.time() and cached[1] is False:
        return False

    if not _listing_needs_availability_refresh(listing):
        return True

    return _listing_is_available(listing)


def _listing_is_available(listing: dict, force_network: bool = False) -> bool:
    listing_id = listing.get("id")
    if listing_id is None:
        return True
    if _listing_is_marked_dead(listing_id):
        return False

    now = time.time()
    cached = _availability_cache.get(listing_id)
    if cached and cached[0] > now and not force_network:
        return cached[1]

    try:
        available = _fetch_listing_available(listing)
    except Exception as e:
        print(f"Availability check failed for {listing_id}: {e}", flush=True)
        available = True

    if not available:
        _mark_listing_dead(listing_id, "availability_check_unavailable")

    ttl = 30 * 60 if not available else 5 * 60
    _availability_cache[listing_id] = (now + ttl, available)
    return available


def _filter_available_page(ranked: list[dict], offset: int, limit: int) -> list[dict]:
    page: list[dict] = []
    available_seen = 0
    batch_size = 16

    for start in range(0, len(ranked), batch_size):
        batch = ranked[start:start + batch_size]
        with ThreadPoolExecutor(max_workers=min(8, len(batch))) as pool:
            futures = {
                pool.submit(_listing_passes_availability_gate, listing): listing
                for listing in batch
            }
            availability = {}
            for future in as_completed(futures):
                listing = futures[future]
                try:
                    availability[listing["id"]] = future.result()
                except Exception:
                    availability[listing["id"]] = True

        for listing in batch:
            if not availability.get(listing["id"], True):
                continue
            if available_seen >= offset:
                page.append(listing)
                if len(page) >= limit:
                    return page
            available_seen += 1

    return page


def _l2(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(max(value, minimum), maximum)


def _as_float(value, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _norm_key(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _public_label(value: str) -> str:
    return STYLE_LABELS.get(_norm_key(value), value.strip().lower() or "style")


def _style_label_from_image_key(key: str, fallback_index: int = 1) -> str:
    parts = [part for part in key.split("/") if part]
    if len(parts) >= 2:
        return _public_label(parts[1])
    return f"Style {fallback_index}"


def _style_category_from_image_key(key: str) -> str:
    parts = [part for part in key.split("/") if part]
    return _norm_key(parts[1]) if len(parts) >= 2 else ""


def _listing_category_key(listing: dict) -> str:
    category = listing.get("category") or listing.get("catalog_name") or ""
    return _norm_key(category)


def _listing_brand_key(listing: dict) -> str:
    return _norm_key(listing.get("brand"))


def _listing_search_text(listing: dict) -> str:
    fields = (
        listing.get("title"),
        listing.get("brand"),
        listing.get("category"),
        listing.get("catalog_name"),
        listing.get("color"),
        listing.get("material"),
    )
    return _norm_key(" ".join(str(value) for value in fields if value))


def _text_has_keyword(text: str, keyword: str) -> bool:
    normalized = _norm_key(keyword)
    if not normalized:
        return False
    pattern = re.escape(normalized).replace(r"\ ", r"\s+")
    return bool(re.search(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])", text))


def _listing_style_tags(listing: dict) -> set[str]:
    text = _listing_search_text(listing)
    if not text:
        return set()
    return {
        style
        for style, keywords in STYLE_KEYWORDS.items()
        if any(_text_has_keyword(text, keyword) for keyword in keywords)
    }


def _listing_item_type(listing: dict) -> str:
    text = _listing_search_text(listing)
    for item_type, keywords in ITEM_TYPE_KEYWORDS.items():
        if any(_text_has_keyword(text, keyword) for keyword in keywords):
            return item_type

    catalog = _listing_category_key(listing)
    if "jack" in catalog or "kleidung" in catalog:
        return "outerwear"
    if "hose" in catalog or "hosen" in catalog:
        return "pants"
    if "oberteil" in catalog or "shirt" in catalog:
        return "tops"
    return "other"


def _condition_key(listing: dict) -> str:
    return _norm_key(listing.get("status") or listing.get("condition"))


def _is_bad_condition(listing: dict, reason: str | None = None) -> bool:
    reason_key = _norm_key(reason)
    condition = _condition_key(listing)
    return (
        reason_key in {"bad_condition", "condition", "poor_condition"}
        or condition in {"zufriedenstellend", "fair", "poor"}
    )


def _valid_vector(value) -> np.ndarray | None:
    if not isinstance(value, list):
        return None
    try:
        vec = np.array(value, dtype=np.float32)
    except Exception:
        return None
    if vec.ndim != 1 or vec.shape[0] != 512:
        return None
    return _l2(vec)


def _weighted_dict(weights: dict | None) -> dict[str, float]:
    if not isinstance(weights, dict):
        return {}
    cleaned: dict[str, float] = {}
    for key, value in weights.items():
        norm = _norm_key(str(key))
        parsed = _as_float(value)
        if norm and parsed is not None and parsed > 0:
            cleaned[norm] = round(float(parsed), 4)
    return cleaned


def _bump_weight(weights: dict, key: str, amount: float) -> None:
    norm = _norm_key(key)
    if not norm:
        return
    weights[norm] = round(_clamp(float(weights.get(norm, 0.0)) + amount, 0.0, 1.0), 4)


def _serializable_capsules(capsules: list[dict]) -> list[dict]:
    safe: list[dict] = []
    for capsule in capsules:
        vec = _valid_vector(capsule.get("vector"))
        if vec is None:
            continue
        safe.append({
            "id": str(capsule.get("id") or f"capsule-{len(safe) + 1}"),
            "label": str(capsule.get("label") or f"Style {len(safe) + 1}"),
            "vector": vec.tolist(),
            "category_weights": _weighted_dict(capsule.get("category_weights")),
            "brand_weights": _weighted_dict(capsule.get("brand_weights")),
            "price_min": _as_float(capsule.get("price_min")),
            "price_max": _as_float(capsule.get("price_max")),
            "negative_attributes": _weighted_dict(capsule.get("negative_attributes")),
            "confidence": round(_clamp(
                float(_as_float(capsule.get("confidence"), DEFAULT_CAPSULE_CONFIDENCE)),
                MIN_CAPSULE_CONFIDENCE,
                MAX_CAPSULE_CONFIDENCE,
            ), 4),
        })
    return safe[:MAX_CAPSULES]


def _profile_vector_from_capsules(capsules: list[dict]) -> np.ndarray | None:
    valid = []
    weights = []
    for capsule in capsules:
        vec = _valid_vector(capsule.get("vector"))
        if vec is None:
            continue
        valid.append(vec)
        weights.append(float(capsule.get("confidence", DEFAULT_CAPSULE_CONFIDENCE)))
    if not valid:
        return None
    matrix = np.stack(valid)
    weight_vec = np.array(weights, dtype=np.float32)
    return _l2((matrix * weight_vec[:, None]).sum(axis=0))


def _global_constraints_from_user(user: dict, body: dict | None = None) -> dict:
    body = body or {}
    existing = user.get("global_constraints") if isinstance(user.get("global_constraints"), dict) else {}
    size = body.get("size", user.get("size"))
    sizes = existing.get("sizes") if isinstance(existing.get("sizes"), list) else []
    if size and size not in sizes:
        sizes = [size]
    return {
        "gender": body.get("gender", existing.get("gender", user.get("gender"))),
        "sizes": sizes,
        "price_min": _as_float(body.get("price_min", existing.get("price_min"))),
        "price_max": _as_float(body.get("price_max", existing.get("price_max"))),
    }


def _new_capsule(
    capsule_id: str,
    label: str,
    vector: np.ndarray,
    category_weights: dict | None = None,
    brand_weights: dict | None = None,
    price_min: float | None = None,
    price_max: float | None = None,
    confidence: float = DEFAULT_CAPSULE_CONFIDENCE,
) -> dict:
    return {
        "id": capsule_id,
        "label": label,
        "vector": _l2(vector.astype(np.float32)).tolist(),
        "category_weights": _weighted_dict(category_weights),
        "brand_weights": _weighted_dict(brand_weights),
        "price_min": _as_float(price_min),
        "price_max": _as_float(price_max),
        "negative_attributes": {},
        "confidence": round(_clamp(confidence, MIN_CAPSULE_CONFIDENCE, MAX_CAPSULE_CONFIDENCE), 4),
    }


def _kmeans_clusters(vecs: list[np.ndarray], k: int, iterations: int = 8) -> list[list[int]]:
    if not vecs:
        return []
    matrix = np.stack([_l2(v.astype(np.float32)) for v in vecs])
    k = max(1, min(k, len(vecs)))

    chosen = [0]
    while len(chosen) < k:
        sims = matrix @ matrix[chosen].T
        nearest = sims.max(axis=1)
        for idx in np.argsort(nearest):
            if int(idx) not in chosen:
                chosen.append(int(idx))
                break

    centroids = matrix[chosen].copy()
    assignments = np.zeros(len(vecs), dtype=np.int64)
    for _ in range(iterations):
        assignments = np.argmax(matrix @ centroids.T, axis=1)
        for cluster_idx in range(k):
            members = matrix[assignments == cluster_idx]
            if len(members) == 0:
                continue
            centroids[cluster_idx] = _l2(members.mean(axis=0))

    clusters = [[idx for idx, assigned in enumerate(assignments) if assigned == cluster_idx] for cluster_idx in range(k)]
    clusters = [cluster for cluster in clusters if cluster]
    clusters.sort(key=lambda cluster: (-len(cluster), min(cluster)))
    return clusters


def _cluster_count(selected_count: int) -> int:
    if selected_count <= 1:
        return 1
    if selected_count <= 4:
        return 2
    return 3


def _build_onboarding_capsules(
    selected_images: list[str],
    vecs: list[np.ndarray],
    price_min: float | None,
    price_max: float | None,
) -> list[dict]:
    clusters = _kmeans_clusters(vecs, _cluster_count(len(vecs)))
    capsules: list[dict] = []
    for idx, cluster in enumerate(clusters, start=1):
        cluster_vecs = [vecs[i] for i in cluster]
        vec = _l2(np.stack(cluster_vecs).mean(axis=0))
        labels = [_style_label_from_image_key(selected_images[i], idx) for i in cluster]
        categories = [_style_category_from_image_key(selected_images[i]) for i in cluster]
        label = max(set(labels), key=lambda item: (labels.count(item), -labels.index(item))) if labels else f"Style {idx}"
        category_weights: dict[str, float] = {}
        for category in categories:
            if category:
                category_weights[category] = category_weights.get(category, 0.0) + 1.0 / max(len(categories), 1)
        confidence = DEFAULT_CAPSULE_CONFIDENCE + min(0.2, 0.04 * len(cluster))
        capsules.append(_new_capsule(
            f"capsule-{idx}",
            label,
            vec,
            category_weights=category_weights,
            price_min=price_min,
            price_max=price_max,
            confidence=confidence,
        ))
    return capsules or [_new_capsule("capsule-1", "Style 1", _l2(np.stack(vecs).mean(axis=0)), price_min=price_min, price_max=price_max)]


def _ensure_taste_profile(user: dict | None, email_hash: str | None = None) -> dict | None:
    if user is None:
        return None

    changed = False
    capsules = _serializable_capsules(user.get("capsules") if isinstance(user.get("capsules"), list) else [])
    if not capsules:
        legacy_vec = _valid_vector(user.get("style_vector"))
        if legacy_vec is not None:
            capsules = [_new_capsule(
                "legacy-default",
                "saved style",
                legacy_vec,
                price_min=_as_float(user.get("price_min")),
                price_max=_as_float(user.get("price_max")),
                confidence=0.6,
            )]
            changed = True

    if capsules != user.get("capsules"):
        user["capsules"] = capsules
        changed = True

    constraints = _global_constraints_from_user(user)
    if constraints != user.get("global_constraints"):
        user["global_constraints"] = constraints
        changed = True

    if changed and email_hash:
        _save_user(email_hash, user)
    return user


def _public_user(user: dict) -> dict:
    capsules = _serializable_capsules(user.get("capsules") if isinstance(user.get("capsules"), list) else [])
    profile_vec = _profile_vector_from_capsules(capsules)
    return {
        "email": user.get("email"),
        "gender": user.get("gender"),
        "size": user.get("size"),
        "style_vector": profile_vec.tolist() if profile_vec is not None else user.get("style_vector"),
        "capsules": capsules,
        "global_constraints": user.get("global_constraints") or _global_constraints_from_user(user),
        "completed_onboarding": user.get("completed_onboarding", False),
    }

# ─── App ─────────────────────────────────────────────────────────────────────

app = Flask(__name__)
CORS(app)

# ─── In-memory cache (loaded once at startup) ─────────────────────────────────

_emb_index:     dict[int, np.ndarray] = {}
_emb_matrix:    np.ndarray | None = None
_emb_ids:       list[int] = []
_listings:      dict[int, dict] = {}
_quality_scores: dict[int, dict] = {}    # id → {quality, price_verdict, red_flags, ...}
_sold_demand: dict = {}

_style_vec:  np.ndarray | None = None
_onboarding_embs: dict[str, np.ndarray] = {}
_style_results_cache: list[dict] = []
_like_count: int = 0
_pending_codes: dict[str, dict] = {}
_availability_cache: dict[int, tuple[float, bool]] = {}
_lock = threading.Lock()


def _load_all() -> None:
    global _emb_index, _emb_matrix, _emb_ids, _listings, _style_vec, _quality_scores, _sold_demand

    if EMBEDDINGS_FILE.exists():
        print("Loading embeddings…", flush=True)
        emb_data = json.loads(EMBEDDINGS_FILE.read_text(encoding="utf-8"))
        emb_list = emb_data["embeddings"]
        _emb_ids = [e["id"] for e in emb_list]
        _emb_index = {
            e["id"]: np.array(e["embedding"], dtype=np.float32)
            for e in emb_list
        }
        _emb_matrix = np.stack([_emb_index[i] for i in _emb_ids])
        print(f"  {len(_emb_ids)} embeddings ({_emb_matrix.shape[1]}-dim)", flush=True)
    else:
        print("embeddings.json not found — /feedback will be unavailable", flush=True)

    if LISTINGS_FILE.exists():
        print("Loading listings…", flush=True)
        listing_data = json.loads(LISTINGS_FILE.read_text(encoding="utf-8"))
        raw = {l["id"]: l for l in listing_data["listings"]}
        # Backfill gender from catalog_name for listings scraped before the field existed
        for listing in raw.values():
            if "gender" not in listing:
                catalog = listing.get("catalog_name", "").lower()
                if any(catalog.startswith(p) for p in ("herren", "men")):
                    listing["gender"] = "men"
                elif any(catalog.startswith(p) for p in ("damen", "frauen", "women")):
                    listing["gender"] = "women"
                else:
                    listing["gender"] = ""
        _listings = raw
        print(f"  {len(_listings)} listings", flush=True)
    else:
        print("listings.json not found — rescoring will be unavailable", flush=True)

    if QUALITY_SCORES_FILE.exists():
        print("Loading quality scores…", flush=True)
        raw_scores = json.loads(QUALITY_SCORES_FILE.read_text(encoding="utf-8"))
        _quality_scores = {int(k): v for k, v in raw_scores.items()}
        print(f"  {len(_quality_scores)} scored listings", flush=True)
    else:
        print("quality_scores.json not found — quality scoring disabled", flush=True)

    if SOLD_DEMAND_FILE.exists():
        print("Loading sold demand scores…", flush=True)
        raw_demand = json.loads(SOLD_DEMAND_FILE.read_text(encoding="utf-8"))
        _sold_demand.update(raw_demand)
        print(f"  {len(_sold_demand)} demand buckets", flush=True)
    else:
        print("sold_demand.json not found — sold demand scoring disabled", flush=True)

    if ONBOARDING_EMBEDDINGS_FILE.exists():
        print("Loading onboarding embeddings…", flush=True)
        onb_data = json.loads(ONBOARDING_EMBEDDINGS_FILE.read_text(encoding="utf-8"))
        _onboarding_embs.update({
            k: np.array(v, dtype=np.float32)
            for k, v in onb_data["embeddings"].items()
        })
        print(f"  {len(_onboarding_embs)} onboarding images", flush=True)
    else:
        print("onboarding_embeddings.json not found — /onboard will be unavailable", flush=True)

    p = _style_results_path()
    if p.exists():
        try:
            _style_results_cache[:] = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass

    print("Loading style vector…", flush=True)
    _style_vec = _load_style_vector()
    print("  done", flush=True)


def _load_style_vector() -> np.ndarray:
    """Load from disk, or bootstrap from the current top results if absent."""
    if STYLE_VECTOR_FILE.exists():
        return _l2(np.load(STYLE_VECTOR_FILE))

    print("  style_vector.npy not found — bootstrapping from style_results.json", flush=True)
    results = json.loads(_style_results_path().read_text(encoding="utf-8"))
    top_ids = [r["id"] for r in results[:10] if r["id"] in _emb_index]

    vecs = np.stack([_emb_index[i] for i in top_ids]) if top_ids else _emb_matrix
    vec = _l2(vecs.mean(axis=0))
    np.save(STYLE_VECTOR_FILE, vec)
    return vec


def _price_band_score(price: float, price_min: float, price_max: float | None) -> float:
    """Soft-band score: in range is full credit, over budget decays faster."""
    if price_max is None:
        if price >= price_min:
            return 1.0
        overshoot = (price_min - price) / max(price_min, 1)
        return max(0.2, 1.0 - 0.5 * overshoot)
    if price_min <= price <= price_max:
        return 1.0
    if price < price_min:
        overshoot = (price_min - price) / max(price_min, 1)
        return max(0.4, 1.0 - 0.4 * overshoot)
    overshoot = (price - price_max) / max(price_max, 1)
    return max(0.05, 1.0 - 0.9 * overshoot)


# ─── Scoring ──────────────────────────────────────────────────────────────────

def _price_score_for_capsule(listing_price: float, capsule: dict, price_min: float | None, price_max: float | None) -> float:
    active_min = _as_float(capsule.get("price_min"), price_min)
    active_max = _as_float(capsule.get("price_max"), price_max)
    if active_min is not None:
        return _price_band_score(listing_price, active_min, active_max)
    return PRICE_MEDIAN / (listing_price + PRICE_MEDIAN) if listing_price >= 0 else 0.5


def _quality_adjustment(listing_id: int) -> float:
    """Score delta from LLM quality scoring. Range roughly -0.30 to +0.13."""
    qs = _quality_scores.get(listing_id)
    if not qs:
        return 0.0
    delta = 0.0
    quality = int(qs.get("quality") or 3)
    if quality >= 4:
        delta += 0.05 * (quality - 3)   # +0.05 for q4, +0.10 for q5
    elif quality <= 2:
        delta -= 0.08 * (3 - quality)   # -0.08 for q2, -0.16 for q1
    if qs.get("price_verdict") == "steal":
        delta += 0.08
    elif qs.get("price_verdict") == "overpriced":
        delta -= 0.05
    red_flags = qs.get("red_flags") or []
    delta -= 0.08 * min(len(red_flags), 3)  # up to -0.24 for bad items
    return delta


def _brand_tier_adj(listing: dict, lane: str) -> float:
    """Score delta from lane-specific brand prestige tiers. Range 0.0 to +0.04."""
    brand = _listing_brand_key(listing)
    if not brand or not lane:
        return 0.0
    if lane == "old money":
        gender = (listing.get("gender") or "").lower()
        if gender not in ("men", "women"):
            return 0.0
        tier = BRAND_TIERS.get(f"old money:{gender}", {})
    else:
        tier = BRAND_TIERS.get(lane, {})
    return tier.get(brand, 0.0)


def _explicit_label_boost(listing: dict, lane: str) -> float:
    """Extra +0.05 when a seller explicitly self-labels their item with the capsule lane.
    Distinguishes 'gorpcore patagonia jacket' from 'patagonia jacket' (inferred gorpcore)."""
    terms = LANE_EXPLICIT_TERMS.get(lane)
    if not terms:
        return 0.0
    text = _norm_key(listing.get("title") or "")
    if not text:
        return 0.0
    return 0.05 if any(_text_has_keyword(text, t) for t in terms) else 0.0


def _negative_penalty(capsule: dict, listing: dict) -> float:
    attrs = _weighted_dict(capsule.get("negative_attributes"))
    penalty = 0.0
    condition = _condition_key(listing)
    if condition:
        penalty += attrs.get(f"condition:{condition}", 0.0) * 0.12
    category = _listing_category_key(listing)
    if category:
        penalty += attrs.get(f"category:{category}", 0.0) * 0.08
    brand = _listing_brand_key(listing)
    if brand:
        penalty += attrs.get(f"brand:{brand}", 0.0) * 0.08
    return min(penalty, 0.35)


def _quality_payload(listing_id: int) -> dict:
    qs = _quality_scores.get(listing_id)
    if not qs:
        return {}
    return {
        "quality_score": qs.get("quality"),
        "price_verdict": qs.get("price_verdict"),
        "red_flags": qs.get("red_flags") or [],
        "style_tags": qs.get("style_tags") or [],
        "one_liner": qs.get("one_liner") or "",
    }


def _sold_demand_score(listing: dict) -> float:
    key = f"{_listing_brand_key(listing)}+{_listing_category_key(listing)}"
    try:
        return float(_sold_demand.get(key, {}).get("demand_score", 0.5) or 0.5)
    except (TypeError, ValueError):
        return 0.5


def _listing_payload(
    listing_id: int,
    listing: dict,
    style_score: float,
    capsule_score: float,
    price_score: float,
    fav_score: float,
    deal_score: float,
    freshness_score: float,
    final_score: float,
    capsule: dict,
) -> dict:
    image_url = listing.get("image_url", "")
    image_urls = listing.get("image_urls") or ([image_url] if image_url else [])
    label = str(capsule.get("label") or "saved style")
    return {
        "id": listing_id,
        "style_score": round(style_score, 4),
        "capsule_score": round(capsule_score, 4),
        "title": listing.get("title", ""),
        "price": listing.get("price", 0.0),
        "currency": listing.get("currency", "EUR"),
        "brand": listing.get("brand", ""),
        "favourites": listing.get("favourites", 0),
        "image_url": image_url,
        "image_urls": image_urls,
        "url": listing.get("url", ""),
        "gender": listing.get("gender", ""),
        "price_score": round(price_score, 4),
        "fav_score": round(fav_score, 4),
        "deal_score": round(deal_score, 4),
        "freshness_score": round(freshness_score, 4),
        "final_score": round(final_score, 4),
        "capsule_id": str(capsule.get("id") or "capsule-1"),
        "capsule_label": label,
        "recommendation_reason": f"Because of your {label} picks",
        **_quality_payload(listing_id),
    }


def _recompute_cached_listing_score(item: dict) -> dict:
    listing_id = _coerce_listing_id(item.get("id"))
    listing = _listings.get(listing_id) if listing_id is not None else {}
    enriched = dict(item)
    enriched.setdefault("capsule_id", "default")
    enriched.setdefault("capsule_label", "saved style")
    enriched.setdefault("capsule_score", enriched.get("style_score", 0.0))
    enriched.setdefault("recommendation_reason", "Because of your saved style picks")
    capsule_score = float(enriched.get("capsule_score", enriched.get("style_score", 0.0)) or 0.0)
    freshness_score = round(_freshness_score_for_listing(listing or {}), 4)
    personalized_freshness = capsule_score * freshness_score
    lane = _norm_key(enriched.get("capsule_label", ""))
    brand_tier_adj = _brand_tier_adj(listing or {}, lane)
    explicit_adj = _explicit_label_boost(listing or {}, lane)
    deal_gate = max(0.0, min(1.0, capsule_score / DEAL_STYLE_GATE))
    enriched["freshness_score"] = freshness_score
    enriched["final_score"] = round(
        CAPSULE_SCORE_WEIGHT * capsule_score
        + DEAL_SCORE_WEIGHT * float(enriched.get("deal_score", 0.0) or 0.0) * deal_gate
        + FRESHNESS_SCORE_WEIGHT * personalized_freshness
        + brand_tier_adj
        + explicit_adj,
        4,
    )
    return enriched


def _score_for_capsule(
    capsule: dict,
    gender: str | None = None,
    price_min: float | None = None,
    price_max: float | None = None,
) -> list[dict]:
    if _emb_matrix is None:
        return []
    vec = _valid_vector(capsule.get("vector"))
    if vec is None:
        return []

    scores = (_emb_matrix @ vec).tolist()
    velocities = []
    now = time.time()
    for emb_id in _emb_ids:
        listing = _listings.get(emb_id)
        if not listing:
            continue
        listed_ts = _listing_freshness_timestamp(listing)
        days_old = max((now - listed_ts) / 86_400, 1.0) if listed_ts else 30.0
        velocities.append(float(listing.get("favourites", 0) or 0) / days_old)
    max_velocity = max(velocities, default=1.0) or 1.0
    category_weights = _weighted_dict(capsule.get("category_weights"))
    brand_weights = _weighted_dict(capsule.get("brand_weights"))
    style_weights = {
        key: value
        for key, value in category_weights.items()
        if key in STYLE_KEYWORDS
    }
    label_key = _norm_key(str(capsule.get("label") or ""))
    if label_key in STYLE_KEYWORDS:
        style_weights[label_key] = max(style_weights.get(label_key, 0.0), 1.0)

    ranked = []
    for listing_id, style_score in zip(_emb_ids, scores):
        listing = _listings.get(listing_id)
        if not listing:
            continue
        listing_gender = listing.get("gender", "")
        if gender and listing_gender and listing_gender != gender:
            continue

        price = listing.get("price", 0.0)
        favs = float(listing.get("favourites", 0) or 0)
        listed_ts = _listing_freshness_timestamp(listing)
        days_old = max((now - listed_ts) / 86_400, 1.0) if listed_ts else 30.0
        fav_score = (favs / days_old) / max_velocity
        active_max = _as_float(capsule.get("price_max"), price_max)
        if active_max is not None and price > active_max and fav_score < STEAL_FAV_THRESHOLD:
            continue
        price_score = _price_score_for_capsule(price, capsule, price_min, price_max)
        sold_demand = _sold_demand_score(listing)
        deal_score = (
            DEAL_PRICE_SCORE_WEIGHT * price_score
            + DEAL_FAV_SCORE_WEIGHT * fav_score
            + DEAL_SOLD_DEMAND_WEIGHT * sold_demand
        )
        freshness_score = _freshness_score_for_listing(listing)
        category_boost = category_weights.get(_listing_category_key(listing), 0.0) * 0.08
        style_tag_boost = sum(
            style_weights.get(tag, 0.0)
            for tag in _listing_style_tags(listing)
        ) * 0.12
        brand_boost = brand_weights.get(_listing_brand_key(listing), 0.0) * 0.10
        penalty = _negative_penalty(capsule, listing)
        capsule_score = style_score + category_boost + style_tag_boost + brand_boost - penalty
        quality_adj = _quality_adjustment(listing_id)
        brand_tier_adj = _brand_tier_adj(listing, label_key)
        explicit_adj = _explicit_label_boost(listing, label_key)
        personalized_freshness = capsule_score * freshness_score
        deal_gate = max(0.0, min(1.0, capsule_score / DEAL_STYLE_GATE))
        final_score = (
            CAPSULE_SCORE_WEIGHT * capsule_score
            + DEAL_SCORE_WEIGHT * deal_score * deal_gate
            + FRESHNESS_SCORE_WEIGHT * personalized_freshness
            + quality_adj
            + brand_tier_adj
            + explicit_adj
        )
        ranked.append(_listing_payload(
            listing_id,
            listing,
            style_score,
            capsule_score,
            price_score,
            fav_score,
            deal_score,
            freshness_score,
            final_score,
            capsule,
        ))

    ranked.sort(key=lambda x: x["final_score"], reverse=True)
    return ranked


def _capsule_weights(capsules: list[dict]) -> dict[str, float]:
    raw = {
        str(capsule.get("id")): max(float(capsule.get("confidence", DEFAULT_CAPSULE_CONFIDENCE)), MIN_CAPSULE_CONFIDENCE)
        for capsule in capsules
    }
    total = sum(raw.values()) or 1.0
    return {capsule_id: value / total for capsule_id, value in raw.items()}


def _interleave_capsule_rankings(ranked_by_capsule: dict[str, list[dict]], capsules: list[dict]) -> list[dict]:
    if len(capsules) <= 1:
        first = capsules[0].get("id") if capsules else None
        return ranked_by_capsule.get(str(first), [])

    weights = _capsule_weights(capsules)
    credits = {str(capsule.get("id")): 0.0 for capsule in capsules}
    cursors = {str(capsule.get("id")): 0 for capsule in capsules}
    seen: set[int] = set()
    result: list[dict] = []
    max_items = sum(len(items) for items in ranked_by_capsule.values())
    last_capsule_id = None
    streak = 0

    def next_unseen(capsule_id: str) -> dict | None:
        items = ranked_by_capsule.get(capsule_id, [])
        cursor = cursors[capsule_id]
        while cursor < len(items) and items[cursor]["id"] in seen:
            cursor += 1
        cursors[capsule_id] = cursor
        return items[cursor] if cursor < len(items) else None

    while len(result) < max_items:
        available = [cid for cid in credits if next_unseen(cid) is not None]
        if not available:
            break
        for cid in available:
            credits[cid] += weights.get(cid, 0.0)

        eligible = available
        if last_capsule_id is not None and streak >= 2:
            alternates = [cid for cid in available if cid != last_capsule_id]
            if alternates:
                eligible = alternates

        chosen = max(eligible, key=lambda cid: (credits[cid], weights.get(cid, 0.0)))
        item = next_unseen(chosen)
        if item is None:
            credits[chosen] = -1
            continue
        cursors[chosen] += 1
        credits[chosen] -= 1.0
        seen.add(item["id"])
        result.append(item)
        if chosen == last_capsule_id:
            streak += 1
        else:
            last_capsule_id = chosen
            streak = 1

    return result


def _compute_capsule_rankings(
    capsules: list[dict],
    gender: str | None = None,
    price_min: float | None = None,
    price_max: float | None = None,
) -> list[dict]:
    safe_capsules = _serializable_capsules(capsules)
    ranked_by_capsule = {
        capsule["id"]: _score_for_capsule(capsule, gender=gender, price_min=price_min, price_max=price_max)
        for capsule in safe_capsules
    }
    return _interleave_capsule_rankings(ranked_by_capsule, safe_capsules)


def _compute_rankings(
    style_vec: np.ndarray,
    gender: str | None = None,
    price_min: float | None = None,
    price_max: float | None = None,
) -> list[dict]:
    """Rank all listings by a single legacy vector. Filters by gender when provided."""
    capsule = _new_capsule(
        "default",
        "saved style",
        _l2(style_vec),
        price_min=price_min,
        price_max=price_max,
        confidence=1.0,
    )
    return _compute_capsule_rankings([capsule], gender=gender, price_min=price_min, price_max=price_max)


def _rescore_and_save(style_vec: np.ndarray) -> None:
    """Re-rank all listings and overwrite style_results.json (runs in ~10ms)."""
    global _style_results_cache
    ranked = _compute_rankings(style_vec)
    _style_results_cache = ranked[:TOP_N]
    (DATA_DIR / "style_results.json").write_text(
        json.dumps(_style_results_cache, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    if ranked:
        print(
            f"Re-scored {len(ranked)} listings — new #1: {ranked[0]['brand']} "
            f"(score {ranked[0]['final_score']:.4f})",
            flush=True,
        )


def _append_impression_logs(items: list[dict], email_hash: str | None = None) -> None:
    if not items:
        return
    if app.config.get("TESTING"):
        return
    path = DATA_DIR / "impression_log.jsonl"
    now = int(time.time())
    lines = []
    for item in items:
        lines.append(json.dumps({
            "timestamp": now,
            "user_hash": email_hash,
            "listing_id": item.get("id"),
            "capsule_id": item.get("capsule_id"),
            "capsule_score": item.get("capsule_score"),
            "final_score": item.get("final_score"),
            "recommendation_reason": item.get("recommendation_reason"),
        }, ensure_ascii=False))
    with path.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _read_json_file(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json_file(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _today_key(now_ts: int | None = None) -> str:
    return datetime.fromtimestamp(now_ts or time.time(), timezone.utc).date().isoformat()


def _parse_timestamp(value) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 10_000_000_000:
            ts = ts / 1000
        return int(ts) if ts > 0 else None
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            ts = float(raw)
            if ts > 10_000_000_000:
                ts = ts / 1000
            return int(ts) if ts > 0 else None
        except ValueError:
            pass
        try:
            normalized = raw.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return int(parsed.timestamp())
        except ValueError:
            return None
    return None


def _coerce_listing_id(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_listing_id_set(values) -> set[int]:
    if values is None:
        return set()
    if isinstance(values, str):
        values = values.split(",")
    ids: set[int] = set()
    for value in values:
        listing_id = _coerce_listing_id(value)
        if listing_id is not None:
            ids.add(listing_id)
    return ids


def _exclude_listing_ids(ranked: list[dict], excluded_ids: set[int]) -> list[dict]:
    if not excluded_ids:
        return ranked
    return [
        item
        for item in ranked
        if _coerce_listing_id(item.get("id")) not in excluded_ids
    ]


def _listing_created_timestamp(listing: dict) -> int | None:
    return _parse_timestamp(listing.get("created_at"))


def _listing_freshness_timestamp(listing: dict) -> int | None:
    return _parse_timestamp(listing.get("created_at"))


def _freshness_score_for_listing(listing: dict) -> float:
    ts = _listing_freshness_timestamp(listing)
    if ts is None:
        return 0.5
    days_since_listed = max(0.0, (time.time() - ts) / 86_400)
    return _clamp(1.0 - (days_since_listed / max(FRESHNESS_WINDOW_DAYS, 1.0)), 0.0, 1.0)


def _listing_is_new_since(listing: dict, last_visit_ts: int | None) -> bool:
    ts = _listing_created_timestamp(listing)
    return bool(last_visit_ts and ts and ts > last_visit_ts)


def _listing_recency_score(listing_id: int, listing: dict) -> float:
    ts = _listing_freshness_timestamp(listing)
    if ts is not None:
        age_days = max(0.0, (time.time() - ts) / 86_400)
        return _clamp(1.0 - (age_days / 14.0), 0.0, 1.0)

    ids = _emb_ids or list(_listings.keys())
    if not ids:
        return 0.0
    min_id = min(ids)
    max_id = max(ids)
    if max_id == min_id:
        return 0.0
    return _clamp((listing_id - min_id) / (max_id - min_id), 0.0, 1.0)


def _style_metric(item: dict) -> float:
    return float(item.get("capsule_score", item.get("style_score", item.get("final_score", 0.0))) or 0.0)


def _minimum_style_score(ranked: list[dict]) -> float:
    if not ranked:
        return -float("inf")
    scores = sorted((_style_metric(item) for item in ranked), reverse=True)
    idx = min(len(scores) - 1, max(DAILY_DROP_MAX_SIZE * 3, int(len(scores) * 0.35)))
    return scores[idx]


def _daily_drop_bucket_counts(limit: int) -> dict[str, int]:
    high = max(4, int(round(limit * 0.50)))
    seller = max(2, int(round(limit * 0.25)))
    hidden = max(1, int(round(limit * 0.17)))
    explore = limit - high - hidden - seller
    if explore < 1:
        high = max(1, high + explore - 1)
        explore = 1
    return {
        "fresh": high,
        "hidden": hidden,
        "seller": seller,
        "explore": max(1, explore),
    }


def _basic_listing_payload(listing_id: int, metadata: dict | None = None) -> dict | None:
    listing = _listings.get(listing_id)
    if not listing:
        return None
    metadata = metadata or {}
    image_url = listing.get("image_url", "")
    image_urls = listing.get("image_urls") or ([image_url] if image_url else [])
    final_score = float(metadata.get("final_score", 0.0) or 0.0)
    return {
        "id": listing_id,
        "style_score": float(metadata.get("style_score", 0.0) or 0.0),
        "capsule_score": float(metadata.get("capsule_score", 0.0) or 0.0),
        "title": listing.get("title", ""),
        "price": listing.get("price", 0.0),
        "currency": listing.get("currency", "EUR"),
        "brand": listing.get("brand", ""),
        "favourites": listing.get("favourites", 0),
        "image_url": image_url,
        "image_urls": image_urls,
        "url": listing.get("url", ""),
        "gender": listing.get("gender", ""),
        "price_score": float(metadata.get("price_score", 0.0) or 0.0),
        "fav_score": float(metadata.get("fav_score", 0.0) or 0.0),
        "deal_score": float(metadata.get("deal_score", 0.0) or 0.0),
        "freshness_score": float(metadata.get("freshness_score", _freshness_score_for_listing(listing)) or 0.0),
        "final_score": round(final_score, 4),
        "capsule_id": str(metadata.get("capsule_id") or "default"),
        "capsule_label": str(metadata.get("capsule_label") or "saved style"),
        "recommendation_reason": str(metadata.get("recommendation_reason") or "Because of your saved style picks"),
    }


def _drop_key(user_id: str, date_key: str) -> str:
    return f"{user_id}:{date_key}"


def _load_daily_drops() -> dict:
    data = _read_json_file(_daily_drops_file(), {})
    return data if isinstance(data, dict) else {}


def _save_daily_drops(drops: dict) -> None:
    _write_json_file(_daily_drops_file(), drops)


def _invalidate_daily_drop(user_id: str, date_key: str | None = None) -> None:
    drops = _load_daily_drops()
    key = _drop_key(user_id, date_key or _today_key())
    if key not in drops:
        return
    del drops[key]
    _save_daily_drops(drops)


def _load_daily_impressions() -> list[dict]:
    data = _read_json_file(_daily_impressions_file(), [])
    return data if isinstance(data, list) else []


def _save_daily_impressions(records: list[dict]) -> None:
    _write_json_file(_daily_impressions_file(), records[-5000:])


def _remember_seen_listing(user: dict | None, listing_id: int | None) -> None:
    if user is None or listing_id is None:
        return
    seen = [
        existing_id
        for existing_id in (_coerce_listing_id(value) for value in user.get("seen_listing_ids", []))
        if existing_id is not None
    ]
    if listing_id in seen:
        return
    seen.append(listing_id)
    user["seen_listing_ids"] = seen[-MAX_USER_SEEN_IDS:]


def _user_seen_listing_ids(user_id: str, user: dict | None = None) -> set[int]:
    seen: set[int] = set()
    if user is not None:
        seen.update(_coerce_listing_id_set(user.get("seen_listing_ids", [])))
        for entry in user.get("feedback_log", []):
            listing_id = _coerce_listing_id(entry.get("listing_id"))
            if listing_id is not None:
                seen.add(listing_id)

    for listing_id, memory in _impression_stats(user_id).items():
        if (
            int(memory.get("seen_count", 0) or 0) > 0
            or memory.get("clicked")
            or memory.get("liked")
            or memory.get("disliked")
        ):
            seen.add(listing_id)
    return seen


def _impression_stats(user_id: str) -> dict[int, dict]:
    stats: dict[int, dict] = {}
    for record in _load_daily_impressions():
        if record.get("user_id") != user_id:
            continue
        listing_id = _coerce_listing_id(record.get("listing_id"))
        if listing_id is None:
            continue
        stats[listing_id] = {
            "seen_count": int(record.get("seen_count") or (1 if record.get("shown_at") else 0)),
            "clicked": bool(record.get("clicked")),
            "liked": bool(record.get("liked")),
            "disliked": bool(record.get("disliked")),
        }
    return stats


def _upsert_daily_impression(
    user_id: str,
    listing_id: int,
    capsule_id: str | None = None,
    final_score: float | None = None,
    event: str = "item_seen",
) -> None:
    records = _load_daily_impressions()
    now = int(time.time())
    key = (user_id, str(listing_id))
    record = None
    for existing in records:
        if (existing.get("user_id"), str(existing.get("listing_id"))) == key:
            record = existing
            break

    if record is None:
        record = {
            "user_id": user_id,
            "listing_id": listing_id,
            "shown_at": None,
            "capsule_id": capsule_id,
            "final_score": final_score,
            "clicked": False,
            "liked": False,
            "disliked": False,
            "seen_count": 0,
        }
        records.append(record)

    if capsule_id is not None:
        record["capsule_id"] = capsule_id
    if final_score is not None:
        record["final_score"] = final_score
    if event == "item_seen":
        record["shown_at"] = now
        record["seen_count"] = int(record.get("seen_count") or 0) + 1
    elif event == "item_clicked":
        record["clicked"] = True
    elif event == "item_liked":
        record["liked"] = True
        record["disliked"] = False
    elif event == "item_disliked":
        record["disliked"] = True

    _save_daily_impressions(records)


def _append_daily_drop_event(
    user_id: str,
    event: str,
    listing_id: int | None = None,
    details: dict | None = None,
) -> None:
    if event not in DAILY_DROP_EVENTS:
        return
    payload = {
        "event": event,
        "user_id": user_id,
        "listing_id": listing_id,
        "timestamp": int(time.time()),
    }
    if details:
        payload.update(details)
    path = _daily_events_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _liked_seller_ids(user: dict | None) -> set[int]:
    if not user:
        return set()
    seller_ids: set[int] = set()
    for entry in user.get("feedback_log", []):
        if entry.get("action") not in {"like", "golden"}:
            continue
        listing_id = _coerce_listing_id(entry.get("listing_id"))
        listing = _listings.get(listing_id) if listing_id is not None else None
        seller_id = _coerce_listing_id((listing or {}).get("seller_id"))
        if seller_id:
            seller_ids.add(seller_id)
    return seller_ids


def _rankings_for_daily_drop(user: dict | None) -> list[dict]:
    constraints = user.get("global_constraints", {}) if user else {}
    gender = constraints.get("gender") if constraints else None
    price_min = _as_float(constraints.get("price_min") if constraints else None)
    price_max = _as_float(constraints.get("price_max") if constraints else None)
    capsules = _serializable_capsules(user.get("capsules")) if user else []
    if capsules and _emb_matrix is not None:
        return _compute_capsule_rankings(capsules, gender=gender, price_min=price_min, price_max=price_max)

    style_vec = _valid_vector(user.get("style_vector")) if user else None
    if style_vec is None:
        style_vec = _style_vec
    if style_vec is not None and _emb_matrix is not None:
        return _compute_rankings(style_vec, gender=gender, price_min=price_min, price_max=price_max)

    cache = _style_results_cache or json.loads(_style_results_path().read_text(encoding="utf-8"))
    return [r for r in cache if not gender or not r.get("gender") or r["gender"] == gender]


def _prepare_daily_candidates(
    ranked: list[dict],
    user_id: str,
    last_visit_ts: int | None,
) -> list[dict]:
    stats = _impression_stats(user_id)
    prepared: list[dict] = []
    for rank, item in enumerate(ranked):
        listing_id = _coerce_listing_id(item.get("id"))
        if listing_id is None:
            continue
        listing = _listings.get(listing_id, {})
        memory = stats.get(listing_id, {})
        seen_count = int(memory.get("seen_count", 0))
        repeated_penalty = REPEATED_SEEN_PENALTY * min(seen_count, 5)
        dislike_penalty = 0.22 if memory.get("disliked") else 0.0
        is_new = _listing_is_new_since(listing, last_visit_ts)
        recency = _listing_recency_score(listing_id, listing)
        adjusted = (
            float(item.get("final_score", 0.0) or 0.0)
            + (FRESHNESS_BOOST if is_new else 0.0)
            + 0.025 * recency
            - repeated_penalty
            - dislike_penalty
        )
        candidate = dict(item)
        candidate["_daily_rank"] = rank
        candidate["_daily_adjusted_score"] = adjusted
        candidate["_daily_recency_score"] = recency
        candidate["_daily_new_since_last_visit"] = is_new
        prepared.append(candidate)
    return prepared


def _candidate_hidden_score(item: dict) -> float:
    fav_score = _clamp(float(item.get("fav_score", 0.0) or 0.0), 0.0, 1.0)
    return (
        0.42 * float(item.get("price_score", 0.0) or 0.0)
        + 0.26 * (1.0 - fav_score)
        + 0.22 * float(item.get("_daily_recency_score", 0.0) or 0.0)
        + 0.10 * float(item.get("_daily_adjusted_score", 0.0) or 0.0)
    )


def _candidate_listing(item: dict) -> dict:
    listing_id = _coerce_listing_id(item.get("id"))
    return _listings.get(listing_id, {}) if listing_id is not None else {}


def _candidate_brand(item: dict) -> str:
    return _listing_brand_key(_candidate_listing(item)) or _norm_key(item.get("brand"))


def _candidate_item_type(item: dict) -> str:
    return _listing_item_type(_candidate_listing(item))


def _daily_reason(item: dict, tag_key: str) -> str:
    label = str(item.get("capsule_label") or "your style")
    item_type = _candidate_item_type(item)
    brand = str(item.get("brand") or _candidate_listing(item).get("brand") or "").strip()
    item_label = item_type if item_type != "other" else "find"
    if tag_key == "fresh":
        return f"Close match: {label} {item_label}"
    if tag_key == "seller":
        return f"Adjacent find: same {label} lane, different {item_label}"
    if tag_key == "hidden":
        return f"Hidden gem: strong taste match with a better price"
    if brand:
        return f"Wildcard: a small stretch from your {label} picks"
    return f"Wildcard: a small stretch from your {label} picks"


def _take_daily_items(
    source: list[dict],
    count: int,
    selected_ids: set[int],
    tag_key: str,
    min_style_score: float,
    brand_counts: dict[str, int] | None = None,
    type_counts: dict[str, int] | None = None,
    max_per_brand: int = 2,
    max_per_type: int = 5,
) -> list[dict]:
    picked: list[dict] = []
    brand_counts = brand_counts if brand_counts is not None else {}
    type_counts = type_counts if type_counts is not None else {}
    for item in source:
        listing_id = _coerce_listing_id(item.get("id"))
        if listing_id is None or listing_id in selected_ids:
            continue
        if _style_metric(item) < min_style_score:
            continue
        brand = _candidate_brand(item)
        item_type = _candidate_item_type(item)
        if brand and brand_counts.get(brand, 0) >= max_per_brand:
            continue
        if item_type and type_counts.get(item_type, 0) >= max_per_type:
            continue
        selected_ids.add(listing_id)
        if brand:
            brand_counts[brand] = brand_counts.get(brand, 0) + 1
        if item_type:
            type_counts[item_type] = type_counts.get(item_type, 0) + 1
        enriched = dict(item)
        enriched["daily_drop_tag"] = DAILY_DROP_TAGS[tag_key]
        enriched["daily_drop_bucket"] = tag_key
        enriched["recommendation_reason"] = _daily_reason(enriched, tag_key)
        picked.append(enriched)
        if len(picked) >= count:
            break
    return picked


def _strip_daily_internal_fields(item: dict, stored: dict | None = None) -> dict:
    clean = {k: v for k, v in item.items() if not k.startswith("_daily_")}
    stored = stored or {}
    tag = clean.get("daily_drop_tag") or stored.get("tag") or DAILY_DROP_TAGS["fresh"]
    clean["daily_drop_tag"] = tag
    clean["daily_drop_bucket"] = clean.get("daily_drop_bucket") or stored.get("bucket") or "fresh"
    clean["new_since_last_visit"] = bool(
        clean.get("new_since_last_visit", stored.get("new_since_last_visit", False))
    )
    clean["final_score"] = round(float(clean.get("final_score", stored.get("final_score", 0.0)) or 0.0), 4)
    return clean


def _compose_daily_drop(
    ranked: list[dict],
    user_id: str,
    user: dict | None,
    limit: int,
    last_visit_ts: int | None,
    date_key: str | None = None,
) -> list[dict]:
    available_pool = _filter_available_page(ranked, 0, min(DAILY_DROP_POOL_SIZE, max(limit * 8, limit)))
    # Shuffle the pool with a deterministic seed so the drop rotates daily
    # but stays stable within a day (reloading gives the same items).
    seed_str = f"{user_id}:{date_key or _today_key()}"
    seed = int(hashlib.md5(seed_str.encode()).hexdigest(), 16) % (2 ** 32)
    rng = random.Random(seed)
    rng.shuffle(available_pool)
    candidates = _prepare_daily_candidates(available_pool, user_id, last_visit_ts)
    # Apply a small seeded jitter so the sort order varies daily while
    # still respecting rough score quality.
    for item in candidates:
        item["_daily_adjusted_score"] += rng.uniform(-0.04, 0.04)
    min_style_score = _minimum_style_score(candidates)
    counts = _daily_drop_bucket_counts(limit)
    selected_ids: set[int] = set()
    brand_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    selected: list[dict] = []

    high_source = sorted(
        candidates,
        key=lambda item: (
            float(item.get("_daily_adjusted_score", 0.0)),
            bool(item.get("_daily_new_since_last_visit")),
            float(item.get("_daily_recency_score", 0.0)),
        ),
        reverse=True,
    )
    selected += _take_daily_items(
        high_source,
        counts["fresh"],
        selected_ids,
        "fresh",
        min_style_score,
        brand_counts,
        type_counts,
    )

    adjacent_source = sorted(
        candidates,
        key=lambda item: (
            type_counts.get(_candidate_item_type(item), 0) == 0,
            brand_counts.get(_candidate_brand(item), 0) == 0,
            float(item.get("_daily_adjusted_score", 0.0)),
            float(item.get("_daily_recency_score", 0.0)),
        ),
        reverse=True,
    )
    selected += _take_daily_items(
        adjacent_source,
        counts["seller"],
        selected_ids,
        "seller",
        min_style_score,
        brand_counts,
        type_counts,
    )

    hidden_source = sorted(candidates, key=_candidate_hidden_score, reverse=True)
    selected += _take_daily_items(
        hidden_source,
        counts["hidden"],
        selected_ids,
        "hidden",
        min_style_score,
        brand_counts,
        type_counts,
    )

    explore_source = sorted(
        candidates,
        key=lambda item: (
            float(item.get("_daily_recency_score", 0.0)),
            float(item.get("_daily_adjusted_score", 0.0)),
        ),
        reverse=True,
    )
    selected += _take_daily_items(
        explore_source,
        counts["explore"],
        selected_ids,
        "explore",
        min_style_score,
        brand_counts,
        type_counts,
        max_per_brand=2,
        max_per_type=6,
    )

    if len(selected) < limit:
        fallback = sorted(candidates, key=lambda item: float(item.get("_daily_adjusted_score", 0.0)), reverse=True)
        selected += _take_daily_items(
            fallback,
            limit - len(selected),
            selected_ids,
            "fresh",
            min_style_score,
            brand_counts,
            type_counts,
            max_per_brand=4,
            max_per_type=8,
        )

    result = []
    for item in selected[:limit]:
        listing_id = _coerce_listing_id(item.get("id"))
        listing = _listings.get(listing_id, {}) if listing_id is not None else {}
        item["new_since_last_visit"] = bool(item.get("_daily_new_since_last_visit")) or _listing_is_new_since(listing, last_visit_ts)
        result.append(_strip_daily_internal_fields(item))
    return result


def _hydrate_daily_drop(stored: dict, ranked: list[dict]) -> list[dict]:
    by_id = {_coerce_listing_id(item.get("id")): item for item in ranked}
    metadata = stored.get("items", {}) if isinstance(stored.get("items"), dict) else {}
    hydrated: list[dict] = []
    for raw_id in stored.get("listing_ids", []):
        listing_id = _coerce_listing_id(raw_id)
        if listing_id is None:
            continue
        stored_item = metadata.get(str(listing_id), {})
        base = by_id.get(listing_id)
        if base is None:
            base = _basic_listing_payload(listing_id, stored_item)
        if base is None:
            continue
        hydrated.append(_strip_daily_internal_fields(dict(base), stored_item))
    return hydrated


def _daily_drop_matches_profile(stored: dict, user: dict | None) -> bool:
    if user is None:
        return True
    capsules = _serializable_capsules(user.get("capsules") if isinstance(user.get("capsules"), list) else [])
    if not capsules:
        return True

    current_labels = {
        _norm_key(str(capsule.get("label") or ""))
        for capsule in capsules
        if capsule.get("label")
    }
    metadata = stored.get("items", {}) if isinstance(stored.get("items"), dict) else {}
    stored_labels = {
        _norm_key(str(item.get("capsule_label") or ""))
        for item in metadata.values()
        if item.get("capsule_label")
    }
    return bool(stored_labels) and stored_labels <= current_labels


def _store_daily_drop(
    user_id: str,
    date_key: str,
    items: list[dict],
    generated_at: int,
) -> dict:
    return {
        "user_id": user_id,
        "date": date_key,
        "listing_ids": [item["id"] for item in items],
        "generated_at": generated_at,
        "items": {
            str(item["id"]): {
                "tag": item.get("daily_drop_tag"),
                "bucket": item.get("daily_drop_bucket"),
                "new_since_last_visit": bool(item.get("new_since_last_visit")),
                "final_score": item.get("final_score"),
                "style_score": item.get("style_score"),
                "capsule_score": item.get("capsule_score"),
                "price_score": item.get("price_score"),
                "fav_score": item.get("fav_score"),
                "deal_score": item.get("deal_score"),
                "freshness_score": item.get("freshness_score"),
                "capsule_id": item.get("capsule_id"),
                "capsule_label": item.get("capsule_label"),
                "recommendation_reason": item.get("recommendation_reason"),
            }
            for item in items
        },
    }


def _store_top_daily_items(email_hash: str | None, user: dict | None, date_key: str, items: list[dict]) -> None:
    if not email_hash or user is None:
        return
    top_items = sorted(items, key=lambda item: float(item.get("final_score", 0.0) or 0.0), reverse=True)[:3]
    user["top_3_daily_items"] = [
        {
            "date": date_key,
            "listing_id": item.get("id"),
            "final_score": item.get("final_score"),
            "daily_drop_tag": item.get("daily_drop_tag"),
        }
        for item in top_items
    ]
    _save_user(email_hash, user)


def _replacement_items_for_user(
    user_id: str,
    user: dict | None,
    exclude_ids: set[int],
    limit: int = 1,
) -> list[dict]:
    excluded = exclude_ids | _user_seen_listing_ids(user_id, user) | _dead_listing_ids()
    ranked = _exclude_listing_ids(_rankings_for_daily_drop(user), excluded)
    return _filter_available_page(ranked, 0, limit)


# ─── Routes ──────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return jsonify({"ok": True})


@app.post("/onboard")
def onboard():
    global _style_vec

    body = request.get_json(silent=True) or {}
    selected_images = body.get("selected_images", [])
    price_min = _as_float(body.get("price_min"))
    price_max = _as_float(body.get("price_max"))

    if not isinstance(selected_images, list) or not selected_images:
        return jsonify({"error": "no images selected"}), 400

    valid_images = [k for k in selected_images if k in _onboarding_embs]
    vecs = [_onboarding_embs[k] for k in valid_images]
    if not vecs:
        return jsonify({"error": "no valid embeddings"}), 400

    capsules = _build_onboarding_capsules(valid_images, vecs, price_min, price_max)
    profile_vec = _profile_vector_from_capsules(capsules)
    if profile_vec is None:
        profile_vec = _l2(np.stack(vecs).mean(axis=0))

    auth_header = request.headers.get("Authorization", "")
    token = auth_header.removeprefix("Bearer ").strip() or None
    email_hash, user = _get_user_from_token(token)
    if user is not None:
        # Authenticated: save to the user's own profile, never touch the global vector
        user["gender"] = body.get("gender", user.get("gender"))
        user["size"] = body.get("size", user.get("size"))
        user["capsules"] = capsules
        user["global_constraints"] = _global_constraints_from_user(user, body)
        user["style_vector"] = profile_vec.tolist()
        user["completed_onboarding"] = True
        _save_user(email_hash, user)
    else:
        # Anonymous: update the shared global vector (single-user / dev usage)
        with _lock:
            _style_vec = profile_vec
            np.save(STYLE_VECTOR_FILE, _style_vec)
            if _emb_matrix is not None:
                _rescore_and_save(_style_vec)

    _invalidate_daily_drop(email_hash or "anonymous")

    return jsonify({
        "style_vector": profile_vec.tolist(),
        "capsules": capsules,
        "global_constraints": _global_constraints_from_user({
            "gender": body.get("gender"),
            "size": body.get("size"),
        }, body),
    })


@app.get("/daily-drop")
def daily_drop():
    limit = _bounded_int(
        request.args.get("limit"),
        DAILY_DROP_DEFAULT_SIZE,
        DAILY_DROP_MIN_SIZE,
        DAILY_DROP_MAX_SIZE,
    )
    force_refresh = request.args.get("force_refresh", "").lower() in ("1", "true")
    exclude_raw = request.args.get("exclude", "")
    exclude_ids = _coerce_listing_id_set(exclude_raw)

    auth_header = request.headers.get("Authorization", "")
    token = auth_header.removeprefix("Bearer ").strip() or None
    email_hash, auth_user = _get_user_from_token(token)
    auth_user = _ensure_taste_profile(auth_user, email_hash)
    user_id = email_hash or "anonymous"
    now = int(time.time())
    date_key = _today_key(now)
    last_visit_ts = _parse_timestamp((auth_user or {}).get("last_daily_drop_opened_at"))

    ranked = _rankings_for_daily_drop(auth_user)
    excluded_ids = (
        exclude_ids
        | _user_seen_listing_ids(user_id, auth_user)
        | _dead_listing_ids()
    )
    ranked = _exclude_listing_ids(ranked, excluded_ids)

    drops = _load_daily_drops()
    key = _drop_key(user_id, date_key)
    stored = drops.get(key)
    stored_ids = _coerce_listing_id_set((stored or {}).get("listing_ids", []))

    if (
        not force_refresh
        and stored
        and _daily_drop_matches_profile(stored, auth_user)
        and not (stored_ids & excluded_ids)
    ):
        items = _hydrate_daily_drop(stored, ranked)
    else:
        items = _compose_daily_drop(ranked, user_id, auth_user, limit, last_visit_ts, date_key)
        if not exclude_ids:
            stored = _store_daily_drop(user_id, date_key, items, now)
            drops[key] = stored
            _save_daily_drops(drops)

    _store_top_daily_items(email_hash, auth_user, date_key, items)
    if auth_user is not None and email_hash:
        auth_user["last_daily_drop_opened_at"] = now
        _save_user(email_hash, auth_user)

    _append_daily_drop_event(
        user_id,
        "daily_drop_opened",
        details={
            "date": date_key,
            "count": len(items),
            "generated_at": stored.get("generated_at") if isinstance(stored, dict) else None,
        },
    )
    return jsonify(items)


@app.post("/daily-drop/events")
def daily_drop_events():
    body = request.get_json(silent=True) or {}
    event = body.get("event")
    if event not in DAILY_DROP_EVENTS:
        return jsonify({"error": "invalid event"}), 400

    auth_header = request.headers.get("Authorization", "")
    token = auth_header.removeprefix("Bearer ").strip() or None
    email_hash, auth_user = _get_user_from_token(token)
    user_id = email_hash or "anonymous"
    listing_id = _coerce_listing_id(body.get("listing_id"))
    item_event = event in {"item_seen", "item_clicked", "item_liked", "item_disliked"}
    if item_event and listing_id is None:
        return jsonify({"error": "missing listing_id"}), 400

    capsule_id = body.get("capsule_id")
    final_score = _as_float(body.get("final_score"))
    if item_event and listing_id is not None:
        _upsert_daily_impression(
            user_id,
            listing_id,
            capsule_id=str(capsule_id) if capsule_id else None,
            final_score=final_score,
            event=event,
        )
        if event in {"item_seen", "item_clicked", "item_liked", "item_disliked"}:
            _remember_seen_listing(auth_user, listing_id)
            if email_hash and auth_user is not None:
                _save_user(email_hash, auth_user)

    _append_daily_drop_event(
        user_id,
        event,
        listing_id=listing_id,
        details={
            "capsule_id": capsule_id,
            "final_score": final_score,
            "date": body.get("date"),
        },
    )
    return jsonify({"ok": True})


@app.route("/feed", methods=["GET", "POST"])
def feed():
    body = request.get_json(silent=True) or {}
    client_style_vector = body.get("style_vector")
    offset = _bounded_int(body.get("offset", request.args.get("offset")), 0, 0, 10_000)
    limit = _bounded_int(body.get("limit", request.args.get("limit")), TOP_N, 1, 100)
    client_seen_ids = _coerce_listing_id_set(body.get("seen_ids") or [])

    # Prefer the server-stored style vector (updated by feedback) over the
    # client-sent onboarding vector, which is stale after the first like/skip.
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.removeprefix("Bearer ").strip() or None
    email_hash, auth_user = _get_user_from_token(token)
    auth_user = _ensure_taste_profile(auth_user, email_hash)
    user_id = email_hash or "anonymous"
    excluded_ids = (
        client_seen_ids
        | _user_seen_listing_ids(user_id, auth_user)
        | _dead_listing_ids()
    )

    def _exclude_seen(ranked: list[dict]) -> list[dict]:
        return _exclude_listing_ids(ranked, excluded_ids)

    constraints = auth_user.get("global_constraints", {}) if auth_user else {}
    gender = constraints.get("gender") if constraints else body.get("gender")
    active_price_min = _as_float(body.get("price_min"), _as_float(constraints.get("price_min") if constraints else None))
    active_price_max = _as_float(body.get("price_max"), _as_float(constraints.get("price_max") if constraints else None))
    capsules = _serializable_capsules(auth_user.get("capsules")) if auth_user else []

    if capsules and _emb_matrix is not None:
        ranked = _compute_capsule_rankings(
            capsules,
            gender=gender,
            price_min=active_price_min,
            price_max=active_price_max,
        )
        page = _filter_available_page(_exclude_seen(ranked), offset, limit)
        _append_impression_logs(page, email_hash)
        return jsonify(page)

    if client_style_vector and _emb_matrix is not None:
        try:
            vec = np.array(client_style_vector, dtype=np.float32)
            if vec.ndim == 1 and vec.shape[0] == _emb_matrix.shape[1]:
                ranked = _compute_rankings(
                    _l2(vec),
                    gender=gender,
                    price_min=active_price_min,
                    price_max=active_price_max,
                )
                page = _filter_available_page(_exclude_seen(ranked), offset, limit)
                _append_impression_logs(page, email_hash)
                return jsonify(page)
            print("Invalid style_vector shape for /feed; returning cache", flush=True)
        except Exception as e:
            print(f"Invalid style_vector for /feed: {e}", flush=True)

    # Cache fallback (anonymous users with no vector) — still apply gender filter
    cache = _style_results_cache or json.loads(_style_results_path().read_text(encoding="utf-8"))
    filtered = [
        _recompute_cached_listing_score(r)
        for r in cache
        if not gender or not r.get("gender") or r["gender"] == gender
    ]
    filtered.sort(key=lambda item: item["final_score"], reverse=True)
    page = _filter_available_page(_exclude_seen(filtered), offset, limit)
    _append_impression_logs(page, email_hash)
    return jsonify(page)


@app.post("/listings/<int:listing_id>/open")
def open_listing(listing_id: int):
    body = request.get_json(silent=True) or {}
    listing = _listings.get(listing_id)
    if listing is None:
        _mark_listing_dead(listing_id, "listing_missing")
        return jsonify({"available": False, "replacement": None})

    auth_header = request.headers.get("Authorization", "")
    token = auth_header.removeprefix("Bearer ").strip() or None
    email_hash, auth_user = _get_user_from_token(token)
    auth_user = _ensure_taste_profile(auth_user, email_hash)
    user_id = email_hash or "anonymous"
    exclude_ids = _coerce_listing_id_set(body.get("exclude_ids") or [])
    exclude_ids.add(listing_id)

    available = _listing_is_available(listing, force_network=True)
    if not available:
        _mark_listing_dead(listing_id, "open_recheck_unavailable")
        replacement = next(
            iter(_replacement_items_for_user(user_id, auth_user, exclude_ids, limit=1)),
            None,
        )
        return jsonify({"available": False, "replacement": replacement})

    capsule_id = body.get("capsule_id")
    final_score = _as_float(body.get("final_score"))
    _upsert_daily_impression(
        user_id,
        listing_id,
        capsule_id=str(capsule_id) if capsule_id else None,
        final_score=final_score,
        event="item_clicked",
    )
    _remember_seen_listing(auth_user, listing_id)
    if email_hash and auth_user is not None:
        _save_user(email_hash, auth_user)
    _append_daily_drop_event(
        user_id,
        "item_clicked",
        listing_id=listing_id,
        details={
            "capsule_id": capsule_id,
            "final_score": final_score,
            "open_rechecked": True,
        },
    )
    return jsonify({"available": True, "url": listing.get("url", "")})


@app.post("/save")
def save():
    body = request.get_json(silent=True) or {}
    listing_id = body.get("id")
    if listing_id is None:
        return jsonify({"error": "missing id"}), 400

    saved: list = []
    if SAVED_FILE.exists():
        try:
            saved = json.loads(SAVED_FILE.read_text(encoding="utf-8"))
        except Exception:
            saved = []

    if listing_id not in saved:
        saved.append(listing_id)
    SAVED_FILE.write_text(json.dumps(saved, indent=2), encoding="utf-8")
    return jsonify({"ok": True, "saved_count": len(saved)})


@app.post("/register")
def register():
    body = request.get_json(silent=True) or {}
    email = body.get("email", "").lower().strip()
    if not email or "@" not in email:
        return jsonify({"error": "invalid email"}), 400

    import secrets
    code = str(secrets.randbelow(1000000)).zfill(6)
    _pending_codes[email] = {"code": code, "expires_at": time.time() + 600}

    try:
        _send_code_email(email, code)
    except Exception as e:
        if _dev_auth_enabled():
            print(
                f"Email send failed; using local dev auth code for {email}: {code}",
                flush=True,
            )
            return jsonify({"success": True, "dev_code": code})

        print(f"Email send failed: {e}", flush=True)
        return jsonify({"error": "email send failed"}), 500

    return jsonify({"success": True})


@app.post("/verify")
def verify():
    body = request.get_json(silent=True) or {}
    email = body.get("email", "").lower().strip()
    code = body.get("code", "").strip()

    entry = _pending_codes.get(email)
    if not entry or entry["code"] != code or time.time() > entry["expires_at"]:
        return jsonify({"error": "Ungültiger oder abgelaufener Code"}), 400

    del _pending_codes[email]

    email_hash = hashlib.sha256(email.encode()).hexdigest()
    user = _load_user(email_hash)

    if user is None:
        user = {
            "email": email,
            "token": str(uuid.uuid4()),
            "gender": None,
            "size": None,
            "style_vector": None,
            "capsules": [],
            "global_constraints": {
                "gender": None,
                "sizes": [],
                "price_min": None,
                "price_max": None,
            },
            "completed_onboarding": False,
            "feedback_log": [],
            "created_at": int(time.time()),
        }
        _save_user(email_hash, user)
    else:
        user = _ensure_taste_profile(user, email_hash) or user

    return jsonify({
        "token": user["token"],
        "user": _public_user(user),
    })


def _append_feedback_log(listing_id: int, action: str, reason: str) -> None:
    """Append a feedback event to feedback_log.json for pattern analysis."""
    log: list = []
    if FEEDBACK_LOG_FILE.exists():
        try:
            log = json.loads(FEEDBACK_LOG_FILE.read_text(encoding="utf-8"))
        except Exception:
            log = []
    log.append({
        "listing_id": listing_id,
        "action": action,
        "reason": reason,
        "timestamp": int(time.time()),
    })
    FEEDBACK_LOG_FILE.write_text(
        json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _user_path(email_hash: str) -> Path:
    return USERS_DIR / f"{email_hash}.json"


def _load_user(email_hash: str) -> dict | None:
    p = _user_path(email_hash)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_user(email_hash: str, user: dict) -> None:
    _user_path(email_hash).write_text(
        json.dumps(user, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _get_user_from_token(token: str | None) -> tuple[str | None, dict | None]:
    if not token:
        return None, None
    for p in USERS_DIR.glob("*.json"):
        try:
            user = json.loads(p.read_text(encoding="utf-8"))
            if user.get("token") == token:
                return p.stem, user
        except Exception:
            continue
    return None, None


def _dev_auth_enabled() -> bool:
    return os.environ.get("PILO_DEV_AUTH", "").lower() in {"1", "true", "yes", "on"}


def _send_code_email(email: str, code: str) -> None:
    import urllib.request as _req

    api_key = os.environ.get("RESEND_API_KEY", "")
    from_addr = os.environ.get("RESEND_FROM", "onboarding@resend.dev")
    payload = json.dumps({
        "from": f"Pilo <{from_addr}>",
        "to": [email],
        "subject": "Dein Pilo Login-Code",
        "text": f"Dein Einmalcode: {code}\nGültig für 10 Minuten.",
    }).encode()
    req = _req.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with _req.urlopen(req, timeout=10) as resp:
        resp.read()


def _nearest_capsule(capsules: list[dict], emb: np.ndarray) -> dict | None:
    best_capsule = None
    best_score = -float("inf")
    for capsule in capsules:
        vec = _valid_vector(capsule.get("vector"))
        if vec is None:
            continue
        score = float(vec @ emb)
        if score > best_score:
            best_score = score
            best_capsule = capsule
    return best_capsule


def _capsule_by_id(capsules: list[dict], capsule_id: str | None) -> dict | None:
    if not capsule_id:
        return None
    for capsule in capsules:
        if str(capsule.get("id")) == str(capsule_id):
            return capsule
    return None


def _adjust_capsule_price_like(capsule: dict, price: float, is_golden: bool) -> None:
    alpha = 0.22 if is_golden else 0.08
    current_min = _as_float(capsule.get("price_min"))
    current_max = _as_float(capsule.get("price_max"))
    if current_min is None:
        capsule["price_min"] = round(max(0.0, price * 0.65), 2)
    else:
        capsule["price_min"] = round((1 - alpha) * current_min + alpha * min(current_min, price), 2)
    if current_max is None:
        capsule["price_max"] = round(max(price, price * (1.2 if is_golden else 1.4)), 2)
    elif is_golden or price <= current_max:
        capsule["price_max"] = round((1 - alpha) * current_max + alpha * max(current_max, price), 2)


def _adjust_capsule_price_dislike(capsule: dict, price: float, reason: str | None) -> None:
    current_max = _as_float(capsule.get("price_max"))
    reason_key = _norm_key(reason)
    too_expensive = reason_key in {"too_expensive", "price", "expensive"} or (
        current_max is not None and price > current_max
    )
    if not too_expensive:
        return
    lowered = max(1.0, price * 0.9)
    if current_max is None:
        capsule["price_max"] = round(lowered, 2)
    else:
        capsule["price_max"] = round(max(_as_float(capsule.get("price_min"), 0.0) or 0.0, min(current_max, lowered)), 2)


def _update_positive_capsule(capsule: dict, emb: np.ndarray, listing: dict, is_golden: bool) -> None:
    alpha = GOLDEN_EMA_ALPHA if is_golden else LIKE_EMA_ALPHA
    base_vec = _valid_vector(capsule.get("vector"))
    if base_vec is None:
        return
    capsule["vector"] = _l2((1 - alpha) * base_vec + alpha * emb).tolist()
    capsule["confidence"] = round(_clamp(
        float(capsule.get("confidence", DEFAULT_CAPSULE_CONFIDENCE)) + (0.08 if is_golden else 0.04),
        MIN_CAPSULE_CONFIDENCE,
        MAX_CAPSULE_CONFIDENCE,
    ), 4)
    _bump_weight(capsule.setdefault("category_weights", {}), _listing_category_key(listing), 0.08 if is_golden else 0.04)
    _bump_weight(capsule.setdefault("brand_weights", {}), _listing_brand_key(listing), 0.10 if is_golden else 0.05)
    _adjust_capsule_price_like(capsule, float(listing.get("price", 0.0)), is_golden)


def _update_negative_capsule(capsule: dict, emb: np.ndarray, listing: dict, reason: str | None) -> None:
    base_vec = _valid_vector(capsule.get("vector"))
    if base_vec is None:
        return
    reason_key = _norm_key(reason)

    # Condition signal only — penalise this condition in negative_attributes, nothing else.
    if _is_bad_condition(listing, reason):
        _bump_weight(capsule.setdefault("negative_attributes", {}), f"condition:{_condition_key(listing)}", 0.25)
        return

    # Price signal only — tighten budget, no vector or confidence change.
    if reason_key in {"too_expensive", "price", "expensive"}:
        _adjust_capsule_price_dislike(capsule, float(listing.get("price", 0.0)), reason)
        return

    # Non-style signals (size, already seen, bad photo, not now) — item is marked
    # seen upstream; no style information is present, so leave the vector alone.
    if reason_key in _SKIP_NO_SIGNAL:
        return

    # Explicit style rejection: full vector repulsion + confidence drop.
    # Unspecified reason (ambiguous intent): quarter-strength nudge, no confidence change.
    if reason_key in _SKIP_STYLE_DISLIKE:
        alpha = SKIP_EMA_ALPHA
        confidence_delta = -0.05
    else:
        alpha = SKIP_EMA_ALPHA * 0.25
        confidence_delta = 0.0

    capsule["vector"] = _l2((1 - alpha) * base_vec - alpha * emb).tolist()
    capsule["confidence"] = round(_clamp(
        float(capsule.get("confidence", DEFAULT_CAPSULE_CONFIDENCE)) + confidence_delta,
        MIN_CAPSULE_CONFIDENCE,
        MAX_CAPSULE_CONFIDENCE,
    ), 4)
    _adjust_capsule_price_dislike(capsule, float(listing.get("price", 0.0)), reason)


@app.post("/feedback")
def feedback():
    global _style_vec, _like_count

    body = request.get_json(silent=True) or {}

    # Accept new format (listing_id, action) or legacy (id, direction)
    listing_id = body.get("listing_id") or body.get("id")
    action     = body.get("action") or body.get("direction")
    reason     = body.get("reason", "none")
    is_golden  = bool(body.get("golden", False))
    capsule_id = body.get("capsule_id")
    listing_id = _coerce_listing_id(listing_id)

    # Normalise: frontend sends 'dislike', internal logic uses 'skip'
    if action == "dislike":
        action = "skip"

    if listing_id is None or action not in ("like", "skip"):
        return jsonify({"error": "invalid payload"}), 400

    with _lock:
        _append_feedback_log(listing_id, action, reason)

    auth_header = request.headers.get("Authorization", "")
    token = auth_header.removeprefix("Bearer ").strip() or None
    fb_email_hash, fb_user = _get_user_from_token(token)
    fb_user = _ensure_taste_profile(fb_user, fb_email_hash)
    if fb_user is not None:
        fb_user.setdefault("feedback_log", []).append({
            "listing_id": listing_id,
            "action": action,
            "reason": reason,
            "capsule_id": capsule_id,
            "timestamp": int(time.time()),
        })
        _remember_seen_listing(fb_user, listing_id)
        _save_user(fb_email_hash, fb_user)

    # Return early when there is no actionable reason: the skip is recorded as
    # seen but carries no capsule signal. Authenticated users who supply a
    # reason (e.g. from the skip-reason chip) fall through to
    # _update_negative_capsule so the reason is acted on.
    has_reason = bool(fb_user) and bool(reason) and _norm_key(reason) not in {"none", ""}
    if action == "skip" and not has_reason:
        updated_vec = (
            _public_user(fb_user).get("style_vector")
            if fb_user is not None
            else (_style_vec.tolist() if _style_vec is not None else [])
        )
        return jsonify({"ok": True, "rescored": False, "like_count": _like_count, "style_vector": updated_vec})

    if not _emb_index:
        return jsonify({"ok": True, "rescored": False, "note": "embeddings not loaded"})

    emb = _emb_index.get(listing_id)
    if emb is None:
        return jsonify({"error": "embedding not found for id"}), 404

    listing = _listings.get(listing_id, {})
    rescored = False
    nudge = SKIP_WEIGHT if action == "skip" else (GOLDEN_WEIGHT if is_golden else LIKE_WEIGHT)

    if fb_user is not None:
        # Authenticated: update only the targeted personal capsule.
        capsules = _serializable_capsules(fb_user.get("capsules"))
        if not capsules:
            base_vec = _valid_vector(fb_user.get("style_vector"))
            if base_vec is None:
                base_vec = _style_vec
            capsules = [_new_capsule("legacy-default", "saved style", base_vec, confidence=0.6)]

        if action == "like":
            target = _nearest_capsule(capsules, emb)
        else:
            target = _capsule_by_id(capsules, capsule_id) or _nearest_capsule(capsules, emb)

        if target is None:
            return jsonify({"error": "no capsule available"}), 400

        if action == "like":
            _update_positive_capsule(target, emb, listing, is_golden)
        else:
            _update_negative_capsule(target, emb, listing, reason)

        fb_user["capsules"] = _serializable_capsules(capsules)
        updated_profile_vec = _profile_vector_from_capsules(fb_user["capsules"])
        updated_vec = (updated_profile_vec if updated_profile_vec is not None else emb).tolist()
        fb_user["style_vector"] = updated_vec
        _save_user(fb_email_hash, fb_user)
    else:
        # Anonymous/dev mode keeps the legacy single-user vector behavior for likes.
        base_vec = _style_vec if _style_vec is not None else emb
        _style_vec = _l2((1 - nudge) * base_vec + nudge * emb)
        _like_count += 1
        np.save(STYLE_VECTOR_FILE, _style_vec)
        if _emb_matrix is not None and _like_count % RESCORE_EVERY == 0:
            _rescore_and_save(_style_vec)
            rescored = True
        updated_vec = _style_vec.tolist()

    return jsonify({"ok": True, "rescored": rescored, "like_count": _like_count, "style_vector": updated_vec})


@app.post("/dev/reset")
def dev_reset():
    """Wipe impression history, feedback log, and cached drops for the authenticated user.
    Style vector and capsules are preserved so onboarding is not lost."""
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.removeprefix("Bearer ").strip() or None
    email_hash, user = _get_user_from_token(token)
    if email_hash is None or user is None:
        return jsonify({"error": "authentication required"}), 401

    # Clear feedback log and impression memory on the user profile
    user.pop("feedback_log", None)
    user.pop("seen_listing_ids", None)
    user.pop("top_daily_items", None)
    _save_user(email_hash, user)

    # Remove this user's impression records
    impressions = _load_daily_impressions()
    impressions = [r for r in impressions if r.get("user_id") != email_hash]
    _save_daily_impressions(impressions)

    # Remove this user's cached daily drops
    drops = _load_daily_drops()
    keys_to_drop = [k for k in drops if k.startswith(f"{email_hash}:")]
    for k in keys_to_drop:
        del drops[k]
    _save_daily_drops(drops)

    _invalidate_daily_drop(email_hash)

    return jsonify({"ok": True, "cleared_drops": len(keys_to_drop)})


# ─── Startup ─────────────────────────────────────────────────────────────────

# Run at import time so gunicorn workers have data loaded before serving.
_load_all()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    print(f"\nPilo API running on http://localhost:{port}\n", flush=True)
    app.run(host="0.0.0.0", port=port)
