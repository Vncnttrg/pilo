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
import threading
from pathlib import Path

import numpy as np
from flask import Flask, jsonify, request
from flask_cors import CORS

# ─── Config ──────────────────────────────────────────────────────────────────

BASE               = Path(__file__).parent
EMBEDDINGS_FILE    = BASE / "embeddings.json"
LISTINGS_FILE      = BASE / "listings.json"
STYLE_VECTOR_FILE  = BASE / "style_vector.npy"
STYLE_RESULTS_FILE = BASE / "style_results.json"
SAVED_FILE         = BASE / "saved.json"

LIKE_WEIGHT    = 0.3    # how much each like nudges the style vector
RESCORE_EVERY  = 10     # re-rank all 931 listings every N likes
TOP_N          = 50     # entries returned by /feed and stored in style_results.json
PRICE_MEDIAN   = 30.0   # € — used in deal scoring formula

# ─── App ─────────────────────────────────────────────────────────────────────

app = Flask(__name__)
CORS(app)

# ─── In-memory cache (loaded once at startup) ─────────────────────────────────

_emb_index:  dict[int, np.ndarray] = {}   # id → 512-dim vector
_emb_matrix: np.ndarray | None = None     # (N, 512) for fast batch scoring
_emb_ids:    list[int] = []               # ordered ids matching _emb_matrix rows
_listings:   dict[int, dict] = {}         # id → listing metadata

_style_vec:  np.ndarray | None = None
_like_count: int = 0
_lock = threading.Lock()


def _load_all() -> None:
    global _emb_index, _emb_matrix, _emb_ids, _listings, _style_vec

    print("Loading embeddings…", flush=True)
    emb_data = json.loads(EMBEDDINGS_FILE.read_text(encoding="utf-8"))
    emb_list = emb_data["embeddings"]
    _emb_ids = [e["id"] for e in emb_list]
    _emb_index = {
        e["id"]: np.array(e["embedding"], dtype=np.float32)
        for e in emb_list
    }
    _emb_matrix = np.stack([_emb_index[i] for i in _emb_ids])  # (N, 512)
    print(f"  {len(_emb_ids)} embeddings ({_emb_matrix.shape[1]}-dim)", flush=True)

    print("Loading listings…", flush=True)
    listing_data = json.loads(LISTINGS_FILE.read_text(encoding="utf-8"))
    _listings = {l["id"]: l for l in listing_data["listings"]}
    print(f"  {len(_listings)} listings", flush=True)

    print("Loading style vector…", flush=True)
    _style_vec = _load_style_vector()
    print("  done", flush=True)


def _load_style_vector() -> np.ndarray:
    """Load from disk, or bootstrap from the current top results if absent."""
    if STYLE_VECTOR_FILE.exists():
        vec = np.load(STYLE_VECTOR_FILE)
        norm = np.linalg.norm(vec)
        return vec / norm if norm > 0 else vec

    print("  style_vector.npy not found — bootstrapping from style_results.json", flush=True)
    results = json.loads(STYLE_RESULTS_FILE.read_text(encoding="utf-8"))
    top_ids = [r["id"] for r in results[:10] if r["id"] in _emb_index]

    if top_ids:
        vecs = np.stack([_emb_index[i] for i in top_ids])
    else:
        vecs = _emb_matrix  # fall back to mean of everything

    mean = vecs.mean(axis=0)
    vec = mean / np.linalg.norm(mean)
    np.save(STYLE_VECTOR_FILE, vec)
    return vec


# ─── Scoring ──────────────────────────────────────────────────────────────────

def _rescore_and_save(style_vec: np.ndarray) -> None:
    """Re-rank all listings and overwrite style_results.json (runs in ~10ms)."""
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
    STYLE_RESULTS_FILE.write_text(
        json.dumps(ranked[:TOP_N], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(
        f"Re-scored {len(ranked)} listings — new #1: {ranked[0]['brand']} "
        f"(score {ranked[0]['final_score']:.4f})",
        flush=True,
    )


# ─── Routes ──────────────────────────────────────────────────────────────────

@app.get("/feed")
def feed():
    results = json.loads(STYLE_RESULTS_FILE.read_text(encoding="utf-8"))
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


@app.post("/feedback")
def feedback():
    global _style_vec, _like_count

    body = request.get_json(silent=True) or {}
    listing_id = body.get("id")
    direction  = body.get("direction")

    if listing_id is None or direction not in ("like", "skip"):
        return jsonify({"error": "invalid payload"}), 400

    if direction == "skip":
        return jsonify({"ok": True, "rescored": False})

    # Like: update style vector
    emb = _emb_index.get(listing_id)
    if emb is None:
        return jsonify({"error": "embedding not found for id"}), 404

    rescored = False
    with _lock:
        _style_vec = _style_vec + LIKE_WEIGHT * emb
        norm = np.linalg.norm(_style_vec)
        _style_vec = _style_vec / norm if norm > 0 else _style_vec
        np.save(STYLE_VECTOR_FILE, _style_vec)

        _like_count += 1
        should_rescore = (_like_count % RESCORE_EVERY) == 0

        if should_rescore:
            _rescore_and_save(_style_vec)
            rescored = True

    return jsonify({"ok": True, "rescored": rescored, "like_count": _like_count})


# ─── Startup ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    _load_all()
    print("\nPilo API running on http://localhost:5001\n", flush=True)
    app.run(host="0.0.0.0", port=5001, debug=False)
