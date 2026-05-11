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

import json
import os
import threading
import time
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

EMBEDDINGS_FILE   = APP_DIR  / "embeddings.json"    # read-only, comes from git
LISTINGS_FILE     = APP_DIR  / "listings.json"       # read-only, comes from git
STYLE_VECTOR_FILE = DATA_DIR / "style_vector.npy"   # updated by /feedback
ONBOARDING_EMBEDDINGS_FILE = APP_DIR / "onboarding_embeddings.json"
SAVED_FILE        = DATA_DIR / "saved.json"          # updated by /save
FEEDBACK_LOG_FILE = DATA_DIR / "feedback_log.json"   # append-only reason log


def _style_results_path() -> Path:
    """DATA_DIR version once it exists (post-rescore), else the git seed."""
    p = DATA_DIR / "style_results.json"
    return p if p.exists() else APP_DIR / "style_results.json"

LIKE_WEIGHT    = 0.3    # how much each like nudges the style vector
RESCORE_EVERY  = 10     # re-rank all 931 listings every N likes
TOP_N          = 50     # entries returned by /feed and stored in style_results.json
PRICE_MEDIAN   = 30.0   # € — used in deal scoring formula


def _l2(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n > 0 else v

# ─── App ─────────────────────────────────────────────────────────────────────

app = Flask(__name__)
CORS(app)

# ─── In-memory cache (loaded once at startup) ─────────────────────────────────

_emb_index:  dict[int, np.ndarray] = {}   # id → 512-dim vector
_emb_matrix: np.ndarray | None = None     # (N, 512) for fast batch scoring
_emb_ids:    list[int] = []               # ordered ids matching _emb_matrix rows
_listings:   dict[int, dict] = {}         # id → listing metadata

_style_vec:  np.ndarray | None = None
_onboarding_embs: dict[str, np.ndarray] = {}
_style_results_cache: list[dict] = []
_like_count: int = 0
_lock = threading.Lock()


def _load_all() -> None:
    global _emb_index, _emb_matrix, _emb_ids, _listings, _style_vec

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
        _listings = {l["id"]: l for l in listing_data["listings"]}
        print(f"  {len(_listings)} listings", flush=True)
    else:
        print("listings.json not found — rescoring will be unavailable", flush=True)

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


# ─── Scoring ──────────────────────────────────────────────────────────────────

def _compute_rankings(style_vec: np.ndarray) -> list[dict]:
    """Rank all listings by style_vec. Does not write to disk."""
    if _emb_matrix is None:
        return []

    scores = (_emb_matrix @ style_vec).tolist()

    max_favs = max(
        (_listings[i].get("favourites", 0) for i in _emb_ids if i in _listings),
        default=1,
    ) or 1

    ranked = []
    for listing_id, style_score in zip(_emb_ids, scores):
        listing = _listings.get(listing_id)
        if not listing:
            continue
        price       = listing.get("price", 0.0)
        favs        = listing.get("favourites", 0)
        image_url   = listing.get("image_url", "")
        image_urls  = listing.get("image_urls") or ([image_url] if image_url else [])
        price_score = PRICE_MEDIAN / (price + PRICE_MEDIAN) if price >= 0 else 0.5
        fav_score   = favs / max_favs
        deal_score  = 0.6 * price_score + 0.4 * fav_score
        final_score = 0.7 * style_score + 0.3 * deal_score
        ranked.append({
            "id":          listing_id,
            "style_score": round(style_score, 4),
            "title":       listing.get("title", ""),
            "price":       price,
            "currency":    listing.get("currency", "EUR"),
            "brand":       listing.get("brand", ""),
            "favourites":  favs,
            "image_url":   image_url,
            "image_urls":  image_urls,
            "url":         listing.get("url", ""),
            "price_score": round(price_score, 4),
            "fav_score":   round(fav_score, 4),
            "deal_score":  round(deal_score, 4),
            "final_score": round(final_score, 4),
        })

    ranked.sort(key=lambda x: x["final_score"], reverse=True)
    return ranked


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


# ─── Routes ──────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return jsonify({"ok": True})


@app.post("/onboard")
def onboard():
    global _style_vec

    body = request.get_json(silent=True) or {}
    selected_images = body.get("selected_images", [])

    if not isinstance(selected_images, list) or not selected_images:
        return jsonify({"error": "no images selected"}), 400

    vecs = [_onboarding_embs[k] for k in selected_images if k in _onboarding_embs]
    if not vecs:
        return jsonify({"error": "no valid embeddings"}), 400

    style_vec = _l2(np.stack(vecs).mean(axis=0))

    with _lock:
        _style_vec = style_vec
        np.save(STYLE_VECTOR_FILE, _style_vec)
        if _emb_matrix is not None:
            _rescore_and_save(_style_vec)

    return jsonify({"style_vector": style_vec.tolist()})


@app.route("/feed", methods=["GET", "POST"])
def feed():
    body = request.get_json(silent=True) or {}
    style_vector = body.get("style_vector")

    if style_vector and _emb_matrix is not None:
        try:
            vec = np.array(style_vector, dtype=np.float32)
            if vec.ndim == 1 and vec.shape[0] == _emb_matrix.shape[1]:
                ranked = _compute_rankings(_l2(vec))
                return jsonify(ranked[:TOP_N])
            print("Invalid style_vector shape for /feed POST; returning cache", flush=True)
        except Exception as e:
            print(f"Invalid style_vector for /feed POST: {e}", flush=True)

    if _style_results_cache:
        return jsonify(_style_results_cache[:TOP_N])
    results = json.loads(_style_results_path().read_text(encoding="utf-8"))
    return jsonify(results[:TOP_N])


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


@app.post("/feedback")
def feedback():
    global _style_vec, _like_count

    body = request.get_json(silent=True) or {}

    # Accept new format (listing_id, action) or legacy (id, direction)
    listing_id = body.get("listing_id") or body.get("id")
    action     = body.get("action") or body.get("direction")
    reason     = body.get("reason", "none")

    # Normalise: frontend sends 'dislike', internal logic uses 'skip'
    if action == "dislike":
        action = "skip"

    if listing_id is None or action not in ("like", "skip"):
        return jsonify({"error": "invalid payload"}), 400

    with _lock:
        _append_feedback_log(listing_id, action, reason)

    if action == "skip":
        return jsonify({"ok": True, "rescored": False})

    if not _emb_index:
        return jsonify({"ok": True, "rescored": False, "note": "embeddings not loaded"})

    emb = _emb_index.get(listing_id)
    if emb is None:
        return jsonify({"error": "embedding not found for id"}), 404

    rescored = False
    with _lock:
        _style_vec = _l2(_style_vec + LIKE_WEIGHT * emb)
        np.save(STYLE_VECTOR_FILE, _style_vec)

        _like_count += 1
        should_rescore = (_like_count % RESCORE_EVERY) == 0

        if should_rescore:
            _rescore_and_save(_style_vec)
            rescored = True

    return jsonify({"ok": True, "rescored": rescored, "like_count": _like_count})


# ─── Startup ─────────────────────────────────────────────────────────────────

# Run at import time so gunicorn workers have data loaded before serving.
_load_all()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    print(f"\nPilo API running on http://localhost:{port}\n", flush=True)
    app.run(host="0.0.0.0", port=port)
