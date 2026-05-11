# Three-Tier Swipe Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the two-action (like/skip + reason sheet) swipe model with skip / like / golden, where golden means "I'd buy this today," eliminating all blocking friction on left swipe and adding a swipe-up gesture that stamps the card, posts a toast with an escape-hatch link, and stores the item in a separate Wants pile.

**Architecture:** All gesture and state changes live in `App.tsx`. The `golden` action type is added to `types.ts`. The backend receives a `golden: true` flag on the feedback POST and applies a higher nudge weight to the style vector. Post-session view gains two tabs (Likes / Wants) rendered from the existing `listings` + `actions` state — no new data fetching needed.

**Tech Stack:** React 19, Framer Motion 12, TypeScript 6, Vite. Backend: Flask, NumPy. Tests: pytest (backend only — no frontend test framework installed).

---

## File Map

| File | Change |
|---|---|
| `pilo-app/src/types.ts` | Add `Action` export with `'golden'` |
| `pilo-app/src/App.tsx` | Primary — gesture, state, UI changes |
| `server.py` | Accept `golden` flag, apply `GOLDEN_WEIGHT` |
| `tests/test_server.py` | Add golden feedback weighting test |

---

## Task 1: Add `Action` type to types.ts

**Files:**
- Modify: `pilo-app/src/types.ts`

- [ ] **Step 1: Add the Action type**

Append to `pilo-app/src/types.ts`:

```typescript
export type Action = 'like' | 'skip' | 'save' | 'golden'
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd pilo-app && npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add pilo-app/src/types.ts
git commit -m "feat(types): add Action type with golden tier"
```

---

## Task 2: Strip the dislike reason sheet from App.tsx

Remove all blocking dislike flow code. Left swipe will fly the card directly after this task.

**Files:**
- Modify: `pilo-app/src/App.tsx`

- [ ] **Step 1: Import Action from types.ts and remove local type declarations**

At the top of `App.tsx`, the import line is:
```typescript
import type { Listing, UserProfile } from './types'
```

Replace with:
```typescript
import type { Listing, UserProfile, Action } from './types'
```

Then find and delete these three type declarations (lines ~20–22 and ~28–33):
```typescript
type Action = 'like' | 'skip' | 'save'
```
```typescript
type DislikeReason = 'not_my_style' | 'wrong_size' | 'too_expensive' | 'bad_condition' | 'none'
type SwipeHistoryEntry = {
  index: number
  photoIndex: number
  listingId: number
  previousAction?: Action
}
```

Keep `SwipeHistoryEntry` but inline it without the `DislikeReason` dependency — it doesn't reference `DislikeReason` so it's fine as-is after removing the `DislikeReason` type. Delete only the `type Action` and `type DislikeReason` lines.

Also delete the `DISLIKE_REASONS` constant block:
```typescript
const DISLIKE_REASONS: { label: string; value: DislikeReason }[] = [
  { label: 'Not my style', value: 'not_my_style' },
  { label: 'Wrong size', value: 'wrong_size' },
  { label: 'Too expensive', value: 'too_expensive' },
  { label: 'Bad condition', value: 'bad_condition' },
]
```

- [ ] **Step 2: Remove `showReasonSheet` state**

Find:
```typescript
const [showReasonSheet, setShowReasonSheet] = useState(false)
```
Delete it.

- [ ] **Step 3: Remove the three dislike flow functions**

Delete `cancelDislikeFlow`:
```typescript
function cancelDislikeFlow() {
  setShowReasonSheet(false)
  animate(x, 0, { type: 'spring', stiffness: 420, damping: 30 })
  flying.current = false
}
```

Delete `commitDislike`:
```typescript
function commitDislike(reason: DislikeReason) {
  setShowReasonSheet(false)
  if (!current) {
    flying.current = false
    return
  }
  rememberSwipe()
  recordAction(current.id, 'skip')
  postFeedback(current.id, 'dislike', reason, token)
  animate(x, -700, { duration: 0.32, ease: [0.22, 1, 0.36, 1] })
  setTimeout(() => {
    setIdx((i) => i + 1)
    setPhotoIdx(0)
    x.set(0)
    flying.current = false
  }, 360)
}
```

Delete `triggerDislikeFlow`:
```typescript
function triggerDislikeFlow() {
  if (flying.current || !current) return
  flying.current = true
  setShowReasonSheet(true)
}
```

- [ ] **Step 4: Update `handleUndoSwipe` — remove the `showReasonSheet` line**

Find inside `handleUndoSwipe`:
```typescript
setShowReasonSheet(false)
```
Delete that line. The function should now read:
```typescript
function handleUndoSwipe() {
  if (flying.current) return
  const entry = swipeHistory.at(-1)
  if (!entry) return

  setSwipeHistory((prev) => prev.slice(0, -1))
  restorePreviousAction(entry)
  setIdx(entry.index)
  setPhotoIdx(entry.photoIndex)
  x.set(0)
}
```

- [ ] **Step 5: Update `flyCard` — make left swipe fly the card directly**

Find the existing `flyCard` function:
```typescript
function flyCard(dir: 'left' | 'right') {
  if (flying.current || !current) return
  if (dir === 'left') {
    triggerDislikeFlow()
    return
  }
  flying.current = true
  rememberSwipe()
  recordAction(current.id, 'like')
  postFeedback(current.id, 'like', 'none', token)
  animate(x, 700, { duration: 0.32, ease: [0.22, 1, 0.36, 1] })
  setTimeout(() => {
    setIdx((i) => i + 1)
    setPhotoIdx(0)
    x.set(0)
    flying.current = false
  }, 360)
}
```

Replace with:
```typescript
function flyCard(dir: 'left' | 'right') {
  if (flying.current || !current) return
  flying.current = true
  rememberSwipe()
  if (dir === 'left') {
    recordAction(current.id, 'skip')
    animate(x, -700, { duration: 0.32, ease: [0.22, 1, 0.36, 1] })
  } else {
    recordAction(current.id, 'like')
    postFeedback(current.id, 'like', token)
    animate(x, 700, { duration: 0.32, ease: [0.22, 1, 0.36, 1] })
  }
  setTimeout(() => {
    setIdx((i) => i + 1)
    setPhotoIdx(0)
    x.set(0)
    flying.current = false
  }, 360)
}
```

Note: `postFeedback` signature is updated in Step 7 of this task. TypeScript will error between this step and Step 7 — that's expected, keep going.

- [ ] **Step 6: Delete the reason sheet JSX block**

Find and delete the entire `{/* Reason picker overlay + sheet */}` block at the bottom of the return (the `<AnimatePresence>` block containing `showReasonSheet && (...)`). It starts at the comment and ends at the closing `</AnimatePresence>` tag.

- [ ] **Step 7: Update `postFeedback` function signature**

Find the existing `postFeedback` function:
```typescript
function postFeedback(
  id: number,
  action: 'like' | 'dislike',
  reason: DislikeReason = 'none',
  token?: string | null,
) {
  fetch(`${API}/feedback`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ listing_id: id, action, reason }),
  }).catch((err) => console.warn('feedback failed:', err))
}
```

Replace with:
```typescript
function postFeedback(
  id: number,
  action: 'like' | 'dislike',
  token?: string | null,
  golden = false,
) {
  fetch(`${API}/feedback`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ listing_id: id, action, golden }),
  }).catch((err) => console.warn('feedback failed:', err))
}
```

- [ ] **Step 8: Verify TypeScript compiles**

```bash
cd pilo-app && npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 9: Manual smoke test**

Run `npm run dev` in `pilo-app/`. Swipe left — the card should fly away immediately with no sheet. Swipe right — like flow unchanged.

- [ ] **Step 10: Commit**

```bash
git add pilo-app/src/App.tsx pilo-app/src/types.ts
git commit -m "feat(swipe): remove dislike reason sheet, left swipe now instant"
```

---

## Task 3: Add swipe-up gesture detection

**Files:**
- Modify: `pilo-app/src/App.tsx`

- [ ] **Step 1: Add `SWIPE_Y` constant**

After the existing constants at the top of the file:
```typescript
const SWIPE_X = 90
const FLICK_V = 280
```

Add:
```typescript
const SWIPE_Y = 80
```

- [ ] **Step 2: Add `y` motion value and `goldenOpacity` transform**

After the existing motion values:
```typescript
const x = useMotionValue(0)
const rotate = useTransform(x, [-280, 280], [-16, 16])
const likeOpacity = useTransform(x, [20, SWIPE_X * 1.5], [0, 1])
const skipOpacity = useTransform(x, [-SWIPE_X * 1.5, -20], [1, 0])
const nextScale = useTransform(x, [-220, 0, 220], [0.97, 0.93, 0.97])
const nextOpacity = useTransform(x, [-220, 0, 220], [0.8, 0.55, 0.8])
```

Add these two lines immediately after:
```typescript
const y = useMotionValue(0)
const goldenOpacity = useTransform(y, [-SWIPE_Y * 1.5, -20], [1, 0])
```

- [ ] **Step 3: Update `handleDragEnd` to detect upward swipe**

Find the existing `handleDragEnd`:
```typescript
function handleDragEnd(_e: MouseEvent | TouchEvent | PointerEvent, info: PanInfo) {
  if (flying.current) return
  const ox = info.offset.x
  const vx = info.velocity.x

  if (ox > SWIPE_X || (ox > 30 && vx > FLICK_V)) {
    flyCard('right')
  } else if (ox < -SWIPE_X || (ox < -30 && vx < -FLICK_V)) {
    flyCard('left')
  } else {
    animate(x, 0, { type: 'spring', stiffness: 420, damping: 30 })
  }
}
```

Replace with:
```typescript
function handleDragEnd(_e: MouseEvent | TouchEvent | PointerEvent, info: PanInfo) {
  if (flying.current) return
  const ox = info.offset.x
  const oy = info.offset.y
  const vx = info.velocity.x
  const vy = info.velocity.y

  if (oy < -SWIPE_Y || (oy < -30 && vy < -FLICK_V)) {
    flyCardGolden()
  } else if (ox > SWIPE_X || (ox > 30 && vx > FLICK_V)) {
    flyCard('right')
  } else if (ox < -SWIPE_X || (ox < -30 && vx < -FLICK_V)) {
    flyCard('left')
  } else {
    animate(x, 0, { type: 'spring', stiffness: 420, damping: 30 })
    animate(y, 0, { type: 'spring', stiffness: 420, damping: 30 })
  }
}
```

Note: `flyCardGolden` is defined in Task 4. TypeScript will error between this step and Task 4.

- [ ] **Step 4: Update the active card `motion.div` to use both axes**

Find the active card motion.div opening tag:
```tsx
<motion.div
  className="absolute inset-0 overflow-hidden bg-black cursor-grab active:cursor-grabbing"
  style={{ x, rotate }}
  drag="x"
  dragConstraints={{ left: 0, right: 0 }}
  dragElastic={0.82}
  onDragEnd={handleDragEnd}
  onTap={handleCardTap}
>
```

Replace with:
```tsx
<motion.div
  className="absolute inset-0 overflow-hidden bg-black cursor-grab active:cursor-grabbing"
  style={{ x, y, rotate }}
  drag
  dragConstraints={{ left: 0, right: 0, top: 0, bottom: 0 }}
  dragElastic={{ left: 0.82, right: 0.82, top: 0.82, bottom: 0 }}
  onDragEnd={handleDragEnd}
  onTap={handleCardTap}
>
```

- [ ] **Step 5: TypeScript check (will fail — flyCardGolden undefined)**

```bash
cd pilo-app && npx tsc --noEmit
```

Expected: error about `flyCardGolden` not found. This is intentional — continue to Task 4.

---

## Task 4: Add golden stamp + `flyCardGolden` function

**Files:**
- Modify: `pilo-app/src/App.tsx`

- [ ] **Step 1: Add `flyCardGolden` function**

Add after the `flyCard` function:

```typescript
function flyCardGolden() {
  if (flying.current || !current) return
  flying.current = true
  rememberSwipe()
  recordAction(current.id, 'golden')
  postFeedback(current.id, 'like', token, true)
  setLastGoldenItem(current)
  animate(y, -700, { duration: 0.32, ease: [0.22, 1, 0.36, 1] })
  setTimeout(() => {
    setIdx((i) => i + 1)
    setPhotoIdx(0)
    x.set(0)
    y.set(0)
    setShowGoldenToast(true)
    flying.current = false
  }, 360)
}
```

`setLastGoldenItem` and `setShowGoldenToast` are added in the next step.

- [ ] **Step 2: Add golden toast state**

After the existing state declarations, add:
```typescript
const [showGoldenToast, setShowGoldenToast] = useState(false)
const [lastGoldenItem, setLastGoldenItem] = useState<Listing | null>(null)
```

- [ ] **Step 3: Add golden stamp overlay inside the card**

Inside the active card `motion.div`, after the existing LIKE and SKIP stamps, add:

```tsx
<motion.div
  className="absolute top-[130px] left-1/2 -translate-x-1/2 border-[3px] border-[#F0C050] text-[#F0C050] px-4 py-1 font-bebas text-[28px] tracking-widest -rotate-6 whitespace-nowrap"
  style={{ opacity: goldenOpacity }}
  aria-hidden
>
  ⭐ WANT
</motion.div>
```

The existing stamps for reference (add after these):
```tsx
<motion.div ... style={{ opacity: likeOpacity }} aria-hidden>LIKE</motion.div>
<motion.div ... style={{ opacity: skipOpacity }} aria-hidden>SKIP</motion.div>
```

- [ ] **Step 4: TypeScript check**

```bash
cd pilo-app && npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 5: Manual smoke test**

Run dev server. Swipe up on a card — the card should stamp ⭐ WANT as you drag upward, then fly off the top. Toast is not visible yet (Task 5).

- [ ] **Step 6: Commit**

```bash
git add pilo-app/src/App.tsx
git commit -m "feat(swipe): add golden swipe-up gesture and stamp animation"
```

---

## Task 5: Golden toast with auto-dismiss and "Go →" link

**Files:**
- Modify: `pilo-app/src/App.tsx`

- [ ] **Step 1: Add auto-dismiss effect**

After the existing `useEffect` for fetching the feed, add:

```typescript
useEffect(() => {
  if (!showGoldenToast) return
  const timer = setTimeout(() => setShowGoldenToast(false), 2500)
  return () => clearTimeout(timer)
}, [showGoldenToast])
```

- [ ] **Step 2: Add toast JSX**

In the main return, inside the outermost `<div className="relative h-full w-full ...">`, add the toast block just before the closing `</div>`. Place it after the action buttons block:

```tsx
{/* Golden toast */}
<AnimatePresence>
  {showGoldenToast && lastGoldenItem && (
    <motion.div
      className="absolute left-4 right-4 z-[110] flex items-center justify-between px-4 py-3 rounded-xl"
      style={{
        top: 'calc(max(env(safe-area-inset-top), 16px) + 64px)',
        background: 'rgba(20, 16, 4, 0.95)',
        backdropFilter: 'blur(16px)',
        WebkitBackdropFilter: 'blur(16px)',
        border: '1px solid rgba(240, 192, 80, 0.3)',
      }}
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -8 }}
      transition={{ duration: 0.18 }}
    >
      <div className="flex items-center gap-2">
        <span className="text-[#F0C050] text-sm">⭐</span>
        <span className="font-mono text-[11px] text-white/70 tracking-wide">
          {lastGoldenItem.brand} · €{lastGoldenItem.price % 1 === 0 ? lastGoldenItem.price.toFixed(0) : lastGoldenItem.price.toFixed(2)}
        </span>
      </div>
      <a
        href={lastGoldenItem.url}
        target="_blank"
        rel="noopener noreferrer"
        onClick={() => setShowGoldenToast(false)}
        className="font-mono text-[11px] font-bold text-[#F0C050] tracking-wider hover:text-yellow-300 transition-colors"
      >
        Go →
      </a>
    </motion.div>
  )}
</AnimatePresence>
```

- [ ] **Step 3: TypeScript check**

```bash
cd pilo-app && npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 4: Manual smoke test**

Run dev server. Swipe up — card flies off top, then a gold-bordered toast appears with brand + price + "Go →". It should auto-dismiss after ~2.5s. Tapping "Go →" opens the Vinted URL in a new tab and dismisses the toast immediately.

- [ ] **Step 5: Commit**

```bash
git add pilo-app/src/App.tsx
git commit -m "feat(swipe): add golden toast with Go link and auto-dismiss"
```

---

## Task 6: End screen — Likes and Wants tabs

**Files:**
- Modify: `pilo-app/src/App.tsx`

- [ ] **Step 1: Add end-screen tab state**

After the golden toast state declarations, add:
```typescript
const [endTab, setEndTab] = useState<'likes' | 'wants'>('wants')
```

- [ ] **Step 2: Compute liked and golden listings**

In the component body, after `const isSaved = ...`, add:

```typescript
const likedListings = listings.filter((l) => actions[l.id] === 'like')
const goldenListings = listings.filter((l) => actions[l.id] === 'golden')
```

- [ ] **Step 3: Replace the `!current` end screen**

Find the existing end-screen block:
```tsx
if (!current) {
  return (
    <div className="flex flex-col items-center justify-center h-full bg-[#0A0A0A] text-white">
      <p className="font-bebas text-6xl tracking-[0.2em] text-white mb-1">ALL DONE</p>
      <p className="font-mono text-xs text-white/30 tracking-wider mb-10">
        {liked} LIKED · {saved} SAVED
      </p>
      <button
        onClick={() => {
          setIdx(0)
          setPhotoIdx(0)
          setActions({})
          localStorage.removeItem(LS_ACTIONS)
        }}
        className="px-8 py-3 border border-white/15 text-white/40 font-mono text-xs tracking-widest hover:bg-white/5 transition-colors"
      >
        START OVER
      </button>
      {swipeHistory.length > 0 && (
        <button
          onClick={handleUndoSwipe}
          className="mt-3 px-8 py-3 border border-white/15 text-white/40 font-mono text-xs tracking-widest hover:bg-white/5 transition-colors"
        >
          UNDO LAST
        </button>
      )}
    </div>
  )
}
```

Replace with:

```tsx
if (!current) {
  const tabListings = endTab === 'wants' ? goldenListings : likedListings

  return (
    <div className="flex flex-col h-full bg-[#0A0A0A] text-white">
      {/* Header */}
      <div
        className="px-5 flex items-center justify-between"
        style={{ paddingTop: 'max(env(safe-area-inset-top), 16px)', paddingBottom: '10px' }}
      >
        <span className="font-bebas text-[26px] tracking-[0.3em]">PILO</span>
        <button
          onClick={() => {
            setIdx(0)
            setPhotoIdx(0)
            setActions({})
            setEndTab('wants')
            localStorage.removeItem(LS_ACTIONS)
          }}
          className="font-mono text-[10px] text-white/25 tracking-widest hover:text-white/50 transition-colors"
        >
          START OVER
        </button>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-white/[0.07] mx-5">
        {(['wants', 'likes'] as const).map((tab) => {
          const count = tab === 'wants' ? goldenListings.length : likedListings.length
          const active = endTab === tab
          return (
            <button
              key={tab}
              onClick={() => setEndTab(tab)}
              className={`flex-1 py-3 font-mono text-[11px] tracking-widest uppercase transition-colors ${
                active ? 'text-white border-b-2 border-white/70' : 'text-white/25 hover:text-white/50'
              }`}
            >
              {tab === 'wants' ? '⭐ Wants' : '♥ Likes'} ({count})
            </button>
          )
        })}
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto">
        {tabListings.length === 0 ? (
          <div className="flex items-center justify-center h-32">
            <p className="font-mono text-[11px] text-white/20 tracking-wider">
              {endTab === 'wants' ? 'No golden swipes yet' : 'Nothing liked'}
            </p>
          </div>
        ) : (
          <div className="divide-y divide-white/[0.05]">
            {tabListings.map((item) => (
              <a
                key={item.id}
                href={item.url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-4 px-5 py-4 hover:bg-white/[0.03] transition-colors"
              >
                <img
                  src={item.image_urls[0] || item.image_url}
                  className="w-14 h-14 object-cover rounded-lg flex-shrink-0"
                />
                <div className="flex-1 min-w-0">
                  <p className="font-cormorant text-lg italic text-white leading-tight truncate">
                    {item.brand}
                  </p>
                  <p className="font-mono text-[10px] text-white/40 mt-0.5 leading-relaxed line-clamp-1">
                    {item.title}
                  </p>
                </div>
                <div className="flex flex-col items-end gap-1 flex-shrink-0">
                  <span className="font-mono text-sm font-bold text-[#F0C050]">
                    €{item.price % 1 === 0 ? item.price.toFixed(0) : item.price.toFixed(2)}
                  </span>
                  <span className="font-mono text-[9px] text-white/20">→</span>
                </div>
              </a>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 4: TypeScript check**

```bash
cd pilo-app && npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 5: Manual smoke test**

Swipe through all cards (or reduce the feed temporarily). End screen should show:
- "⭐ Wants (N)" tab defaulting active — lists golden swipes with image, brand, price, Vinted link
- "♥ Likes (N)" tab — lists regular likes
- "START OVER" resets both piles and returns to feed
- Tapping any item opens Vinted in a new tab

- [ ] **Step 6: Commit**

```bash
git add pilo-app/src/App.tsx
git commit -m "feat(end-screen): add Wants and Likes tabs with item list"
```

---

## Task 7: Backend — `golden` flag and weighted nudge

**Files:**
- Modify: `server.py`
- Modify: `tests/test_server.py`

- [ ] **Step 1: Add `GOLDEN_WEIGHT` constant to server.py**

Find:
```python
LIKE_WEIGHT    = 0.3    # how much each like nudges the style vector
RESCORE_EVERY  = 10     # re-rank all 931 listings every N likes
```

Add after `LIKE_WEIGHT`:
```python
GOLDEN_WEIGHT  = 0.55   # golden swipe nudges harder — confirmed style + price fit
```

- [ ] **Step 2: Read the `golden` flag in the `/feedback` handler**

Find inside the `/feedback` route:
```python
body = request.get_json(silent=True) or {}

# Accept new format (listing_id, action) or legacy (id, direction)
listing_id = body.get("listing_id") or body.get("id")
action     = body.get("action") or body.get("direction")
reason     = body.get("reason", "none")
```

Add after the existing assignments:
```python
is_golden  = bool(body.get("golden", False))
```

- [ ] **Step 3: Apply the higher weight for golden swipes**

Find:
```python
with _lock:
    _style_vec = _l2(_style_vec + LIKE_WEIGHT * emb)
    np.save(STYLE_VECTOR_FILE, _style_vec)
```

Replace with:
```python
weight = GOLDEN_WEIGHT if is_golden else LIKE_WEIGHT
with _lock:
    _style_vec = _l2(_style_vec + weight * emb)
    np.save(STYLE_VECTOR_FILE, _style_vec)
```

- [ ] **Step 4: Write a failing test**

Open `tests/test_server.py`. Add at the end of the file:

```python
def test_golden_feedback_applies_higher_weight(client):
    """Golden swipe should nudge the style vector more than a regular like."""
    if not srv._emb_index:
        pytest.skip("embeddings not loaded in test environment")

    listing_id = srv._emb_ids[0]
    vec_before = srv._style_vec.copy()

    with patch("numpy.save"):
        client.post("/feedback", json={"listing_id": listing_id, "action": "like", "golden": False})
    vec_after_like = srv._style_vec.copy()
    like_delta = float(np.linalg.norm(vec_after_like - vec_before))

    srv._style_vec[:] = vec_before  # reset

    with patch("numpy.save"):
        client.post("/feedback", json={"listing_id": listing_id, "action": "like", "golden": True})
    vec_after_golden = srv._style_vec.copy()
    golden_delta = float(np.linalg.norm(vec_after_golden - vec_before))

    assert golden_delta > like_delta, (
        f"golden delta ({golden_delta:.6f}) should exceed like delta ({like_delta:.6f})"
    )
```

Run it:
```bash
cd /Users/vincenttroger/pilo && python -m pytest tests/test_server.py::test_golden_feedback_applies_higher_weight -v
```

Expected: SKIP (embeddings not loaded in test env — the server loads them at import time from the real file which may not be present). If it runs with embeddings, it will FAIL until Step 3's weight logic is in place.

- [ ] **Step 5: Run the test**

```bash
python -m pytest tests/test_server.py -v
```

Expected: existing tests pass, new test passes or skips.

- [ ] **Step 6: Manual verification**

Start the server and send two feedback requests via curl:

```bash
# Regular like
curl -s -X POST http://localhost:5001/feedback \
  -H "Content-Type: application/json" \
  -d '{"listing_id": 1, "action": "like", "golden": false}' | python3 -m json.tool

# Golden like
curl -s -X POST http://localhost:5001/feedback \
  -H "Content-Type: application/json" \
  -d '{"listing_id": 1, "action": "like", "golden": true}' | python3 -m json.tool
```

Both should return `{"ok": true, "rescored": false, "like_count": N}` without errors.

- [ ] **Step 7: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat(backend): apply GOLDEN_WEIGHT for golden swipe feedback"
```

---

## Final Check

- [ ] Run full test suite: `python -m pytest tests/ -v`
- [ ] Run frontend build: `cd pilo-app && npm run build`
- [ ] End-to-end: open dev server, swipe left (no sheet), swipe right (like), swipe up (golden stamp → toast → Wants pile)
