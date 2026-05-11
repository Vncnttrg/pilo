# Pilo Onboarding Flow — Design Spec

**Date:** 2026-05-11
**Status:** Approved

---

## Overview

Add a 3-screen onboarding flow to the Pilo PWA. First-time users select their gender, clothing size, and style preferences (via a photo grid). The selections are sent to `/onboard`, which computes a personalized CLIP style vector and returns it. The vector is stored in `localStorage` and sent on every subsequent `/feed` request, ensuring the feed is ranked by the user's taste from the first load.

Returning users (who already have `pilo_style_vector` in `localStorage`) skip onboarding entirely.

---

## Scope

**In scope:**
- `embed_onboarding.py` — one-time script to pre-compute CLIP embeddings for the 24 onboarding images
- `onboarding_embeddings.json` — output of the script, committed to the repo
- `server.py` — add `POST /onboard`; change `/feed` to accept POST with optional `style_vector`
- `src/Onboarding.tsx` — new component with 3 screens
- `src/App.tsx` — onboarding gate + feed fetch updated to POST

**Out of scope:**
- Editing or re-taking onboarding after completion (future)
- Multi-user profiles
- Onboarding analytics

---

## Data Flow

```
App mounts
  └─ check localStorage['pilo_style_vector']
       ├─ present → load feed (POST /feed with stored vector)
       └─ absent  → render <Onboarding onComplete={...} />

Onboarding (step: 0 | 1 | 2)
  step 0: gender selection → gender state ('men' | 'women')
  step 1: size selection   → size state (string)
  step 2: style grid       → selectedImages state (string[])
           ↓ tap 'Meinen Feed zeigen' (enabled at ≥ 3 selections)
  POST /onboard { gender, size, selected_images: [relative_paths] }
           ↓ { style_vector: float[512] }
  localStorage.setItem('pilo_style_vector', JSON.stringify(vector))
  localStorage.setItem('pilo_gender', gender)
  localStorage.setItem('pilo_size', size)
  onComplete() → App shows main feed

Feed fetch (changed from GET → POST)
  POST /feed { style_vector: float[512] }
  Server rescores top 50 in-memory with provided vector, returns results
```

---

## localStorage Keys

| Key | Value | Purpose |
|-----|-------|---------|
| `pilo_style_vector` | `JSON.stringify(float[512])` | Personalized ranking vector; presence gates onboarding |
| `pilo_gender` | `'men'` \| `'women'` | Stored for potential future use |
| `pilo_size` | e.g. `'M'`, `'38'` | Stored for potential future use |

---

## Onboarding Images

The 24 images live in `pilo-app/public/onboarding/{gender}/{style}/{filename}`.

**Men (12 images):**
- Old money: `#ralphlauren #ralph.jpeg`, `_ (2).jpeg`
- Vintage: `style -  - #Style.jpeg`, `images (1).jpeg`
- Minimal: `Street fashion men.jpeg`, `Modern Men Street Style 2026 _ Minimal Casual Outfit Ideas.jpeg`
- Streetwear: `dwayne-joe-bbNMSi-lKbk-unsplash.jpg`, `kam-myers-TRdOPdjKnO8-unsplash.jpg`
- Gorpcore: `_ (2).jpeg`, `mountain 🏔️.jpeg`
- Y2K: `Follow me on insta _@loganjenkinsiscool_.jpeg`, `_ (2).jpeg`

**Women (12 images):**
- Smart casual: `10 Old Money Outfits for Women.jpeg`, `_ (2).jpeg`
- Vintage: `_ (3).jpeg`, `_ (2).jpeg`
- Minimal: `khaled-ali-1-Sk6l2lCWY-unsplash.jpg`, `helen-ngoc-n-kNTdzGqzsyk-unsplash.jpg`
- Streetwear: `Woman Pp.jpeg`, `ben-iwara-VJAXXDs3UdU-unsplash.jpg`
- Gorpcore: `Winter Gorpcore Streetwear_ Japan Aesthetic.jpeg`, `_ (2).jpeg`
- Y2K: `_ (3).jpeg`, `_ (2).jpeg`

Image paths are URL-encoded in the frontend constant and used as `<img src="/onboarding/men/...">`. The `selected_images` POSTed to `/onboard` use the same relative path format (without leading slash) as keys into `onboarding_embeddings.json`.

---

## Frontend — `src/Onboarding.tsx`

Single file. Manages `step` (0|1|2), `gender`, `size`, `selectedImages`, and `submitting` state.

### Screen 0 — Gender

Two tall cards filling the viewport, split 50/50. Each card shows a representative onboarding photo as a full-bleed background with a dark gradient overlay. Label 'MÄNNER' / 'FRAUEN' in `font-bebas` at the card bottom. Tap → sets gender, advances to step 1 with slide-right animation.

### Screen 1 — Size

Header: gender-specific label (e.g. "DEINE GRÖSSE"). Size options in a horizontal row of pill buttons:
- Männer: XS, S, M, L, XL, XXL
- Frauen: 34, 36, 38, 40, 42

Single selection. Selected pill: white border + white text. Unselected: dimmed. Tap selected size → advance to step 2.

No explicit "next" button — tapping the size immediately advances.

### Screen 2 — Style Grid

Header: "DEIN STIL". 3-column × 4-row CSS grid. Each cell is square (`aspect-ratio: 1`), `object-cover` image. Tapping a cell toggles selection. Selected cells show a semi-transparent dark overlay + white checkmark circle in the center.

Counter below grid: "X / 12 ausgewählt". Button 'MEINEN FEED ZEIGEN' at bottom. Disabled (grayed, no pointer events) until ≥ 3 selected. On tap: calls `handleSubmit`.

`handleSubmit`:
1. Set `submitting = true`, show loading state on button ("WIRD GELADEN…")
2. `POST /onboard` with `{ gender, size, selected_images }`
3. On success: write all 3 localStorage keys, call `onComplete()`
4. On error: show inline error message, re-enable button

### Transitions

`AnimatePresence` wraps the active screen. Each screen mounts with `{ x: "100%" }` → `{ x: 0 }` and exits with `{ x: "-100%" }`. Duration 0.28s, ease `[0.22, 1, 0.36, 1]` (matching existing card animation).

### Props

```ts
interface OnboardingProps {
  onComplete: () => void
}
```

---

## Frontend — `src/App.tsx` Changes

### Onboarding gate

```ts
const [showOnboarding, setShowOnboarding] = useState(
  () => !localStorage.getItem('pilo_style_vector')
)
```

If `showOnboarding`: render `<Onboarding onComplete={() => setShowOnboarding(false)} />` instead of the card feed.

### Feed fetch

Change from:
```ts
fetch(`${API}/feed`)
```
To:
```ts
const storedVector = localStorage.getItem('pilo_style_vector')
const styleVector = storedVector ? JSON.parse(storedVector) : null

fetch(`${API}/feed`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(styleVector ? { style_vector: styleVector } : {}),
})
```

---

## Backend — `embed_onboarding.py`

Standalone script, not imported by `server.py`. Run once locally before deploying.

- Walks `pilo-app/public/onboarding/` recursively
- For each image file (`.jpg`, `.jpeg`): loads with PIL, runs CLIP ViT-B/32 (same model as `clip_scorer.py`), L2-normalizes
- Key format: `{gender}/{style}/{filename}` (relative path from the `onboarding/` directory)
- Output: `onboarding_embeddings.json`

```json
{
  "meta": { "model": "ViT-B-32", "pretrained": "openai", "embedding_dim": 512, "total": 24 },
  "embeddings": {
    "men/Minimal/Street fashion men.jpeg": [0.023, ...],
    ...
  }
}
```

`onboarding_embeddings.json` is committed to the repo so the API server never needs CLIP at runtime.

---

## Backend — `server.py` Changes

### Load onboarding embeddings at startup

```python
ONBOARDING_EMBEDDINGS_FILE = APP_DIR / "onboarding_embeddings.json"
_onboarding_embs: dict[str, np.ndarray] = {}  # path → 512-dim vector
```

Loaded in `_load_all()`.

### `POST /onboard`

```
Request:  { gender: str, size: str, selected_images: list[str] }
Response: { style_vector: list[float] }  (512 floats)
```

Logic:
1. Validate `selected_images` is a non-empty list
2. Look up each path in `_onboarding_embs`; skip any not found
3. If no valid embeddings found: return 400
4. Stack vectors, compute mean, L2-normalize → 512-dim vector
5. Save to `STYLE_VECTOR_FILE`, update `_style_vec` in-memory, call `_rescore_and_save`
6. Return `{ style_vector: vector.tolist() }`

### `GET /feed` → accept POST

Change decorator to `@app.route("/feed", methods=["GET", "POST"])`.

If POST body contains `style_vector`:
- Parse as `np.array(body["style_vector"], dtype=np.float32)`
- L2-normalize
- Rescore top 50 in-memory (do not write to disk, do not update `_style_vec`)
- Return ranked results directly

If GET (or no `style_vector` in body):
- Return cached `style_results.json` as before (backwards compatible)

---

## Error Handling

| Scenario | Behaviour |
|----------|-----------|
| `/onboard` — image key not in embeddings | Skip that image; proceed if ≥ 1 found |
| `/onboard` — 0 valid images | Return 400 `{ error: "no valid embeddings" }` |
| Frontend submit error | Show "Fehler. Bitte erneut versuchen." below button; re-enable |
| `/feed` POST — invalid `style_vector` shape | Fall back to cached results; log warning |
| Returning user, localStorage vector malformed | Treat as absent → show onboarding again |

---

## File Changes Summary

| File | Change |
|------|--------|
| `embed_onboarding.py` | New — one-time CLIP embedding script |
| `onboarding_embeddings.json` | New — generated by script, committed |
| `server.py` | Add `POST /onboard`; `/feed` accepts POST body |
| `src/Onboarding.tsx` | New — 3-screen onboarding component |
| `src/App.tsx` | Add gate + update feed fetch |
