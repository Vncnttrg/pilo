# Pilo Onboarding Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a 3-screen onboarding flow (gender → size → style grid) that computes a personalized CLIP style vector and uses it to rank the feed from the first session.

**Architecture:** A one-time `embed_onboarding.py` script pre-computes CLIP embeddings for the 24 onboarding images and commits `onboarding_embeddings.json`. The server gains a `POST /onboard` endpoint that averages selected embeddings into a 512-dim style vector and returns it. `/feed` is extended to accept a `style_vector` in its POST body and rescores in-memory. The frontend gates on `localStorage['pilo_style_vector']`; new users see `Onboarding.tsx` before the feed loads.

**Tech Stack:** Python 3 + open_clip + Flask + numpy (backend); React 19 + TypeScript + Framer Motion + Tailwind (frontend)

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `embed_onboarding.py` | Create | One-time script: walk `public/onboarding/`, embed with CLIP, write `onboarding_embeddings.json` |
| `onboarding_embeddings.json` | Create (generated) | Pre-computed 512-dim vectors keyed by relative path |
| `requirements.txt` | Modify | Add `pytest` |
| `tests/conftest.py` | Create | Add project root to sys.path so `import server` works |
| `tests/test_server.py` | Create | Integration tests for `/onboard` and `POST /feed` |
| `server.py` | Modify | Add `_compute_rankings` helper, `POST /onboard`, extend `/feed` to accept POST body |
| `pilo-app/src/Onboarding.tsx` | Create | 3-screen onboarding component (gender → size → style grid) |
| `pilo-app/src/App.tsx` | Modify | Add onboarding gate; update feed fetch from GET to POST with style_vector |

---

## Task 1: Generate onboarding embeddings

**Files:**
- Create: `embed_onboarding.py`
- Create (generated): `onboarding_embeddings.json`

- [ ] **Step 1: Create `embed_onboarding.py`**

```python
"""
Embed Onboarding Images
=======================
One-time script. Walks pilo-app/public/onboarding/, runs CLIP ViT-B/32 on
each image, saves results to onboarding_embeddings.json.

Run from the repo root:
    python3 embed_onboarding.py

Dependencies (same as clip_scorer.py):
    pip install open_clip_torch torch torchvision Pillow
"""

import json
import logging
import warnings
from pathlib import Path

import torch
import open_clip

logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", category=UserWarning, module="open_clip")
from PIL import Image

MODEL_NAME     = "ViT-B-32"
PRETRAINED     = "openai"
DEVICE         = "cpu"
ONBOARDING_DIR = Path(__file__).parent / "pilo-app" / "public" / "onboarding"
OUTPUT_FILE    = Path(__file__).parent / "onboarding_embeddings.json"
EXTS           = {".jpg", ".jpeg", ".png", ".webp"}


def embed_image(model, preprocess, path: Path) -> list[float]:
    img = Image.open(path).convert("RGB")
    tensor = preprocess(img).unsqueeze(0).to(DEVICE)
    with torch.no_grad():
        emb = model.encode_image(tensor)
        emb = emb / emb.norm(dim=-1, keepdim=True)
    return emb.squeeze().cpu().tolist()


def run() -> None:
    print(f"Loading {MODEL_NAME} ({PRETRAINED})…")
    model, _, preprocess = open_clip.create_model_and_transforms(
        MODEL_NAME, pretrained=PRETRAINED, device=DEVICE
    )
    model.eval()
    print("Model ready.\n")

    image_paths = sorted(
        p for p in ONBOARDING_DIR.rglob("*") if p.suffix.lower() in EXTS
    )
    print(f"Found {len(image_paths)} images in {ONBOARDING_DIR}\n")

    embeddings: dict[str, list[float]] = {}
    for path in image_paths:
        key = path.relative_to(ONBOARDING_DIR).as_posix()
        print(f"  embedding: {key}")
        try:
            embeddings[key] = embed_image(model, preprocess, path)
        except Exception as e:
            print(f"    ERROR: {e}")

    output = {
        "meta": {
            "model":         MODEL_NAME,
            "pretrained":    PRETRAINED,
            "embedding_dim": 512,
            "total":         len(embeddings),
        },
        "embeddings": embeddings,
    }
    OUTPUT_FILE.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"\n✅ {len(embeddings)} embeddings saved to {OUTPUT_FILE.name}")


if __name__ == "__main__":
    run()
```

- [ ] **Step 2: Run the script**

```bash
cd /Users/vincenttroger/pilo
python3 embed_onboarding.py
```

Expected output ends with: `✅ 24 embeddings saved to onboarding_embeddings.json`

If the count is less than 24, check which images failed (errors printed inline) and fix before continuing.

- [ ] **Step 3: Verify the output**

```bash
python3 -c "
import json
data = json.load(open('onboarding_embeddings.json'))
print('total:', data['meta']['total'])
print('embedding_dim:', data['meta']['embedding_dim'])
keys = list(data['embeddings'].keys())
print('first key:', keys[0])
print('vector length:', len(data['embeddings'][keys[0]]))
"
```

Expected:
```
total: 24
embedding_dim: 512
first key: men/...
vector length: 512
```

- [ ] **Step 4: Commit**

```bash
git add embed_onboarding.py onboarding_embeddings.json
git commit -m "feat: add CLIP embedding script and onboarding embeddings"
```

---

## Task 2: Backend — `_compute_rankings` helper + `POST /onboard`

**Files:**
- Modify: `server.py`
- Modify: `requirements.txt`
- Create: `tests/conftest.py`
- Create: `tests/test_server.py`

- [ ] **Step 1: Add pytest to requirements and install**

Add `pytest` to `requirements.txt`:

```
flask==3.1.3
flask-cors==6.0.2
numpy==2.4.4
gunicorn==26.0.0
pytest==8.3.5
```

```bash
pip install pytest
```

- [ ] **Step 2: Create `tests/conftest.py`**

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
```

- [ ] **Step 3: Write failing tests for `/onboard`**

Create `tests/test_server.py`:

```python
import json
import numpy as np
import pytest
from unittest.mock import patch

import server as srv


@pytest.fixture
def client():
    srv.app.config["TESTING"] = True
    with srv.app.test_client() as c:
        yield c


# ── /onboard ──────────────────────────────────────────────────────────────────

def test_onboard_valid_images_returns_512_dim_vector(client):
    # Uses the first 3 keys from the real onboarding_embeddings.json
    keys = list(srv._onboarding_embs.keys())[:3]
    assert len(keys) == 3, "onboarding_embeddings.json must exist (run embed_onboarding.py)"

    with patch.object(srv, "_rescore_and_save"), \
         patch("numpy.save"):
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

    with patch.object(srv, "_rescore_and_save"), \
         patch("numpy.save"):
        rv = client.post("/onboard", json={
            "gender": "men",
            "size": "M",
            "selected_images": keys,
        })

    vec = np.array(rv.get_json()["style_vector"], dtype=np.float32)
    assert abs(np.linalg.norm(vec) - 1.0) < 1e-5


def test_onboard_empty_images_returns_400(client):
    rv = client.post("/onboard", json={"gender": "men", "size": "M", "selected_images": []})
    assert rv.status_code == 400


def test_onboard_all_invalid_keys_returns_400(client):
    rv = client.post("/onboard", json={
        "gender": "men",
        "size": "M",
        "selected_images": ["nonexistent/image.jpg", "also/missing.jpg"],
    })
    assert rv.status_code == 400
```

- [ ] **Step 4: Run to confirm tests fail**

```bash
cd /Users/vincenttroger/pilo
pytest tests/test_server.py -v -k "onboard"
```

Expected: all 4 `onboard` tests FAIL — `/onboard` returns 404 (not implemented yet).

- [ ] **Step 5: Add `_onboarding_embs` global and load it in `_load_all`**

In `server.py`, after the `STYLE_VECTOR_FILE` line (around line 33), add:

```python
ONBOARDING_EMBEDDINGS_FILE = APP_DIR / "onboarding_embeddings.json"
```

After the `_style_vec` global declaration (around line 59), add:

```python
_onboarding_embs: dict[str, np.ndarray] = {}
```

Inside `_load_all()`, after the listings loading block (around line 91), add:

```python
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
```

Note: use `.update()` on the existing dict so the global reference stays valid after `_load_all()` runs.

- [ ] **Step 6: Extract `_compute_rankings` helper from `_rescore_and_save`**

Replace the existing `_rescore_and_save` function (around line 118) with:

```python
def _compute_rankings(style_vec: np.ndarray) -> list[dict]:
    """Rank all listings by style_vec. Does not write to disk."""
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
    """Re-rank all listings and overwrite style_results.json."""
    ranked = _compute_rankings(style_vec)
    (DATA_DIR / "style_results.json").write_text(
        json.dumps(ranked[:TOP_N], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    if ranked:
        print(
            f"Re-scored {len(ranked)} listings — new #1: {ranked[0]['brand']} "
            f"(score {ranked[0]['final_score']:.4f})",
            flush=True,
        )
```

- [ ] **Step 7: Add `POST /onboard` route**

Add after the `/health` route (around line 173):

```python
@app.post("/onboard")
def onboard():
    global _style_vec

    body = request.get_json(silent=True) or {}
    selected_images: list[str] = body.get("selected_images", [])

    if not selected_images:
        return jsonify({"error": "no images selected"}), 400

    vecs = [_onboarding_embs[k] for k in selected_images if k in _onboarding_embs]
    if not vecs:
        return jsonify({"error": "no valid embeddings"}), 400

    mean = np.stack(vecs).mean(axis=0)
    norm = np.linalg.norm(mean)
    style_vec = mean / norm if norm > 0 else mean

    with _lock:
        _style_vec = style_vec
        np.save(STYLE_VECTOR_FILE, _style_vec)
        if _emb_matrix is not None:
            _rescore_and_save(_style_vec)

    return jsonify({"style_vector": style_vec.tolist()})
```

- [ ] **Step 8: Run tests to confirm they pass**

```bash
pytest tests/test_server.py -v -k "onboard"
```

Expected: all 4 `onboard` tests PASS.

- [ ] **Step 9: Commit**

```bash
git add requirements.txt tests/conftest.py tests/test_server.py server.py
git commit -m "feat(server): add POST /onboard endpoint with CLIP style vector"
```

---

## Task 3: Backend — `/feed` accepts POST body with `style_vector`

**Files:**
- Modify: `server.py`
- Modify: `tests/test_server.py`

- [ ] **Step 1: Write failing tests for `POST /feed`**

Append to `tests/test_server.py`:

```python
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
    # Results must be sorted by final_score descending
    scores = [r["final_score"] for r in data]
    assert scores == sorted(scores, reverse=True)


def test_feed_post_without_style_vector_falls_back_to_cache(client):
    rv = client.post("/feed", json={})
    assert rv.status_code == 200
    data = rv.get_json()
    assert isinstance(data, list)
```

- [ ] **Step 2: Run to confirm tests fail**

```bash
pytest tests/test_server.py -v -k "feed"
```

Expected: `test_feed_get_returns_list` PASSES (existing behavior). The two `post` tests FAIL with 405 Method Not Allowed.

- [ ] **Step 3: Update `/feed` route in `server.py`**

Replace the existing `@app.get("/feed")` route with:

```python
@app.route("/feed", methods=["GET", "POST"])
def feed():
    body = request.get_json(silent=True) or {}
    style_vector = body.get("style_vector")

    if style_vector and _emb_matrix is not None:
        vec = np.array(style_vector, dtype=np.float32)
        norm = np.linalg.norm(vec)
        vec = vec / norm if norm > 0 else vec
        ranked = _compute_rankings(vec)
        return jsonify(ranked[:TOP_N])

    results = json.loads(_style_results_path().read_text(encoding="utf-8"))
    return jsonify(results[:TOP_N])
```

- [ ] **Step 4: Run all tests**

```bash
pytest tests/test_server.py -v
```

Expected: all 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat(server): extend /feed to accept POST body with style_vector"
```

---

## Task 4: Frontend — `src/Onboarding.tsx`

**Files:**
- Create: `pilo-app/src/Onboarding.tsx`

- [ ] **Step 1: Create `pilo-app/src/Onboarding.tsx`**

```tsx
import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:5001'

type Gender = 'men' | 'women'

const MEN_SIZES   = ['XS', 'S', 'M', 'L', 'XL', 'XXL']
const WOMEN_SIZES = ['34', '36', '38', '40', '42']

// Keys match onboarding_embeddings.json. Paths are relative to public/onboarding/.
const ONBOARDING_IMAGES: Record<Gender, string[]> = {
  men: [
    'men/Old money/#ralphlauren #ralph.jpeg',
    'men/Old money/_ (2).jpeg',
    'men/Vintage/style -  - #Style.jpeg',
    'men/Vintage/images (1).jpeg',
    'men/Minimal/Street fashion men.jpeg',
    'men/Minimal/Modern Men Street Style 2026 _ Minimal Casual Outfit Ideas.jpeg',
    'men/Streetwear/dwayne-joe-bbNMSi-lKbk-unsplash.jpg',
    'men/Streetwear/kam-myers-TRdOPdjKnO8-unsplash.jpg',
    'men/Gorpcore/_ (2).jpeg',
    'men/Gorpcore/mountain 🏔️.jpeg',
    'men/Y2K/Follow me on insta _@loganjenkinsiscool_.jpeg',
    'men/Y2K/_ (2).jpeg',
  ],
  women: [
    'women/smart casual/10 Old Money Outfits for Women.jpeg',
    'women/smart casual/_ (2).jpeg',
    'women/Vintage/_ (3).jpeg',
    'women/Vintage/_ (2).jpeg',
    'women/Minimal/khaled-ali-1-Sk6l2lCWY-unsplash.jpg',
    'women/Minimal/helen-ngoc-n-kNTdzGqzsyk-unsplash.jpg',
    'women/Streetwear/Woman Pp.jpeg',
    'women/Streetwear/ben-iwara-VJAXXDs3UdU-unsplash.jpg',
    'women/Gorpcore/Winter Gorpcore Streetwear_ Japan Aesthetic.jpeg',
    'women/Gorpcore/_ (2).jpeg',
    'women/Y2K/_ (3).jpeg',
    'women/Y2K/_ (2).jpeg',
  ],
}

// Hero image shown on each gender card (clean filename, no special chars)
const GENDER_HEROES: Record<Gender, string> = {
  men:   'men/Minimal/Street fashion men.jpeg',
  women: 'women/Minimal/khaled-ali-1-Sk6l2lCWY-unsplash.jpg',
}

// URL-encode each path segment for use in img src
function toSrc(key: string): string {
  return '/onboarding/' + key.split('/').map(encodeURIComponent).join('/')
}

const slideVariants = {
  enter:  { x: '100%', opacity: 0 },
  center: { x: 0,      opacity: 1 },
  exit:   { x: '-100%', opacity: 0 },
}

const slideTransition = { duration: 0.28, ease: [0.22, 1, 0.36, 1] as const }

// ── Screen 0: Gender ──────────────────────────────────────────────────────────

function GenderScreen({ onSelect }: { onSelect: (g: Gender) => void }) {
  return (
    <div className="flex h-full w-full">
      {(['men', 'women'] as Gender[]).map((g) => (
        <button
          key={g}
          onClick={() => onSelect(g)}
          className="relative flex-1 overflow-hidden focus:outline-none"
        >
          <img
            src={toSrc(GENDER_HEROES[g])}
            className="absolute inset-0 h-full w-full object-cover"
            alt={g}
          />
          <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-black/20 to-transparent" />
          <span className="absolute bottom-10 left-0 right-0 text-center font-bebas text-4xl tracking-[0.2em] text-white drop-shadow">
            {g === 'men' ? 'MÄNNER' : 'FRAUEN'}
          </span>
        </button>
      ))}
      <div className="pointer-events-none absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-white/10" />
    </div>
  )
}

// ── Screen 1: Size ────────────────────────────────────────────────────────────

function SizeScreen({ gender, onSelect }: { gender: Gender; onSelect: (s: string) => void }) {
  const sizes = gender === 'men' ? MEN_SIZES : WOMEN_SIZES
  const [pending, setPending] = useState<string | null>(null)

  function handleTap(size: string) {
    setPending(size)
    setTimeout(() => onSelect(size), 160)
  }

  return (
    <div className="flex h-full flex-col items-center justify-center gap-12 px-6">
      <p className="font-bebas text-3xl tracking-[0.25em] text-white">DEINE GRÖSSE</p>
      <div className="flex flex-wrap justify-center gap-3">
        {sizes.map((size) => (
          <motion.button
            key={size}
            whileTap={{ scale: 0.88 }}
            onClick={() => handleTap(size)}
            className={`min-w-[64px] rounded-full px-5 py-3 font-mono text-sm tracking-widest transition-colors ${
              pending === size
                ? 'border border-white bg-white/10 text-white'
                : 'border border-white/20 text-white/40'
            }`}
          >
            {size}
          </motion.button>
        ))}
      </div>
    </div>
  )
}

// ── Screen 2: Style grid ──────────────────────────────────────────────────────

function CheckIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
      <circle cx="10" cy="10" r="9" fill="rgba(0,0,0,0.6)" stroke="white" strokeWidth="1.5" />
      <path d="M6 10l3 3 5-5" stroke="white" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function StyleGrid({
  gender,
  onSubmit,
}: {
  gender: Gender
  onSubmit: (images: string[]) => Promise<void>
}) {
  const images = ONBOARDING_IMAGES[gender]
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function toggle(key: string) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  async function handleSubmit() {
    setSubmitting(true)
    setError(null)
    try {
      await onSubmit(Array.from(selected))
    } catch {
      setError('Fehler. Bitte erneut versuchen.')
      setSubmitting(false)
    }
  }

  const ready = selected.size >= 3

  return (
    <div
      className="flex h-full flex-col"
      style={{ paddingTop: 'max(env(safe-area-inset-top), 16px)' }}
    >
      <p className="shrink-0 py-4 text-center font-bebas text-3xl tracking-[0.25em] text-white">
        DEIN STIL
      </p>

      <div className="grid flex-1 grid-cols-3 overflow-hidden">
        {images.map((key) => {
          const isSelected = selected.has(key)
          return (
            <motion.button
              key={key}
              whileTap={{ scale: 0.95 }}
              onClick={() => toggle(key)}
              className="relative overflow-hidden focus:outline-none"
            >
              <img
                src={toSrc(key)}
                className="absolute inset-0 h-full w-full object-cover"
                alt=""
                loading="lazy"
              />
              <AnimatePresence>
                {isSelected && (
                  <motion.div
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    transition={{ duration: 0.15 }}
                    className="absolute inset-0 flex items-center justify-center bg-black/30"
                  >
                    <CheckIcon />
                  </motion.div>
                )}
              </AnimatePresence>
            </motion.button>
          )
        })}
      </div>

      <div
        className="shrink-0 px-6 pb-4 pt-3"
        style={{ paddingBottom: 'max(env(safe-area-inset-bottom), 16px)' }}
      >
        <p className="mb-3 text-center font-mono text-[10px] tracking-widest text-white/30">
          {selected.size} / 12 AUSGEWÄHLT
        </p>
        {error && (
          <p className="mb-2 text-center font-mono text-[10px] text-red-400">{error}</p>
        )}
        <motion.button
          whileTap={ready && !submitting ? { scale: 0.97 } : undefined}
          onClick={ready && !submitting ? handleSubmit : undefined}
          className={`w-full py-4 font-bebas text-lg tracking-[0.2em] transition-colors ${
            ready && !submitting
              ? 'bg-white text-black'
              : 'cursor-default bg-white/10 text-white/20'
          }`}
        >
          {submitting ? 'WIRD GELADEN…' : 'MEINEN FEED ZEIGEN'}
        </motion.button>
      </div>
    </div>
  )
}

// ── Main export ───────────────────────────────────────────────────────────────

export function Onboarding({ onComplete }: { onComplete: () => void }) {
  const [step, setStep] = useState(0)
  const [gender, setGender] = useState<Gender>('men')
  const [size, setSize] = useState('')

  function handleGender(g: Gender) {
    setGender(g)
    setStep(1)
  }

  function handleSize(s: string) {
    setSize(s)
    setStep(2)
  }

  async function handleSubmit(selectedImages: string[]) {
    const res = await fetch(`${API}/onboard`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ gender, size, selected_images: selectedImages }),
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = (await res.json()) as { style_vector: number[] }
    localStorage.setItem('pilo_style_vector', JSON.stringify(data.style_vector))
    localStorage.setItem('pilo_gender', gender)
    localStorage.setItem('pilo_size', size)
    onComplete()
  }

  return (
    <div className="relative h-full w-full overflow-hidden bg-[#0A0A0A]">
      <AnimatePresence mode="wait" initial={false}>
        {step === 0 && (
          <motion.div
            key="gender"
            variants={slideVariants}
            initial="enter"
            animate="center"
            exit="exit"
            transition={slideTransition}
            className="absolute inset-0"
          >
            <GenderScreen onSelect={handleGender} />
          </motion.div>
        )}
        {step === 1 && (
          <motion.div
            key="size"
            variants={slideVariants}
            initial="enter"
            animate="center"
            exit="exit"
            transition={slideTransition}
            className="absolute inset-0"
          >
            <SizeScreen gender={gender} onSelect={handleSize} />
          </motion.div>
        )}
        {step === 2 && (
          <motion.div
            key="grid"
            variants={slideVariants}
            initial="enter"
            animate="center"
            exit="exit"
            transition={slideTransition}
            className="absolute inset-0"
          >
            <StyleGrid gender={gender} onSubmit={handleSubmit} />
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd /Users/vincenttroger/pilo/pilo-app
npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add pilo-app/src/Onboarding.tsx
git commit -m "feat(frontend): add Onboarding component with 3-screen flow"
```

---

## Task 5: Frontend — `App.tsx` gate and feed fetch

**Files:**
- Modify: `pilo-app/src/App.tsx`

- [ ] **Step 1: Add import and `showOnboarding` state**

At the top of `App.tsx`, add the import after the existing imports:

```tsx
import { Onboarding } from './Onboarding'
```

Inside `App()`, after the `const flying = useRef(false)` line, add:

```tsx
const [showOnboarding, setShowOnboarding] = useState(
  () => !localStorage.getItem('pilo_style_vector')
)
```

- [ ] **Step 2: Update the feed fetch `useEffect` to POST with style_vector**

Replace the existing `useEffect` that fetches `/feed`:

```tsx
// Before:
useEffect(() => {
  fetch(`${API}/feed`)
    .then((r) => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      return r.json() as Promise<Listing[]>
    })
    .then((data) => {
      setListings(data)
      setLoading(false)
    })
    .catch((err) => {
      setError(String(err))
      setLoading(false)
    })
}, [])
```

```tsx
// After:
useEffect(() => {
  if (showOnboarding) return

  const stored = localStorage.getItem('pilo_style_vector')
  const styleVector = stored ? (JSON.parse(stored) as number[]) : null

  fetch(`${API}/feed`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(styleVector ? { style_vector: styleVector } : {}),
  })
    .then((r) => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      return r.json() as Promise<Listing[]>
    })
    .then((data) => {
      setListings(data)
      setLoading(false)
    })
    .catch((err) => {
      setError(String(err))
      setLoading(false)
    })
}, [showOnboarding])
```

The `showOnboarding` dependency means the fetch runs once for returning users (on mount) and once for new users (when onboarding completes and `showOnboarding` flips to `false`).

- [ ] **Step 3: Add the onboarding gate conditional return**

Find the first conditional return in `App()` (`if (loading)`). Immediately before it, add:

```tsx
if (showOnboarding) {
  return <Onboarding onComplete={() => setShowOnboarding(false)} />
}
```

The full block should now read:

```tsx
  if (showOnboarding) {
    return <Onboarding onComplete={() => setShowOnboarding(false)} />
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full bg-[#0A0A0A]">
        ...
```

- [ ] **Step 4: Verify TypeScript compiles**

```bash
cd /Users/vincenttroger/pilo/pilo-app
npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 5: Manual end-to-end test**

Start the server and frontend:

```bash
# Terminal 1
cd /Users/vincenttroger/pilo
python3 server.py

# Terminal 2
cd /Users/vincenttroger/pilo/pilo-app
npm run dev
```

Open `http://localhost:5173` (or whatever port Vite reports).

Test checklist:
- [ ] Gender screen shows two full-bleed photo cards with 'MÄNNER' / 'FRAUEN'
- [ ] Tapping a card slides to the size screen
- [ ] Correct sizes shown for each gender (XS–XXL for men, 34–42 for women)
- [ ] Tapping a size slides to the style grid
- [ ] 12 images displayed in 3-column grid
- [ ] Tapping images toggles checkmark overlay
- [ ] Counter updates correctly ("X / 12 AUSGEWÄHLT")
- [ ] 'MEINEN FEED ZEIGEN' button stays grayed until 3+ selected
- [ ] Tapping button with 3+ selected calls `/onboard`, then slides to feed
- [ ] Feed is visible and showing listings
- [ ] Reload the page — onboarding is skipped, feed loads directly
- [ ] Check localStorage in DevTools: `pilo_style_vector`, `pilo_gender`, `pilo_size` all set

To re-test onboarding: `localStorage.clear()` in DevTools console, then reload.

- [ ] **Step 6: Commit**

```bash
git add pilo-app/src/App.tsx
git commit -m "feat(frontend): add onboarding gate and personalized feed fetch"
```

---

## Self-Review Checklist

**Spec coverage:**
- [x] Screen 0: gender selection, two big tappable cards (Task 4 GenderScreen)
- [x] Screen 1: size selection, gender-conditional sizes, single tap (Task 4 SizeScreen)
- [x] Screen 2: 12-photo 3×4 grid, checkmark overlay, min 3 required, 'Meinen Feed zeigen' (Task 4 StyleGrid)
- [x] POST /onboard with { gender, size, selected_images } (Task 2)
- [x] Redirect to main feed on completion (Task 5)
- [x] Store gender, size, style_vector in localStorage (Task 4 handleSubmit)
- [x] Returning users skip onboarding (Task 5 showOnboarding gate)
- [x] /feed sends style_vector in body (Task 5 feed fetch)
- [x] Backend uses provided vector instead of default (Task 3)
- [x] Server returns { style_vector } from /onboard (Task 2)
- [x] CLIP embeddings pre-computed and stored (Task 1)
