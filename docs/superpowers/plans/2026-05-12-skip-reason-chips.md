# Skip Reason Chips Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show a row of 3 optional skip-reason chips above the action bar on 1 in 5 left swipes, posting the selected reason to `/feedback` on tap and dismissing silently after 3s if ignored.

**Architecture:** All changes are in `App.tsx`. `postFeedback` gains an optional `reason` param. A new `reasonChip` state drives visibility. A `useEffect` handles auto-dismiss. The 20% sampling roll and chip clear live in `flyCard` and `flyCardGolden`/`handleUndoSwipe`.

**Tech Stack:** React, Framer Motion (`AnimatePresence`, `motion.div`), TypeScript

---

### Task 1: Update `postFeedback` to forward an optional reason

**Files:**
- Modify: `pilo-app/src/App.tsx:138-153`

- [ ] **Step 1: Replace the `postFeedback` function**

Find the existing function (lines 138–153) and replace it with:

```ts
function postFeedback(
  id: number,
  action: 'like' | 'dislike',
  token?: string | null,
  golden = false,
  capsuleId?: string,
  reason?: string,
) {
  fetch(`${API}/feedback`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({
      listing_id: id,
      action,
      golden,
      capsule_id: capsuleId,
      ...(reason ? { reason } : {}),
    }),
  }).catch((err) => console.warn('feedback failed:', err))
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd pilo-app && npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add pilo-app/src/App.tsx
git commit -m "feat(feedback): add optional reason param to postFeedback"
```

---

### Task 2: Add `reasonChip` state and auto-dismiss effect

**Files:**
- Modify: `pilo-app/src/App.tsx`

- [ ] **Step 1: Add `reasonChip` state**

In the `App` function, directly after the `showGoldenToast` / `lastGoldenItem` state declarations (around line 185–188), add:

```ts
const [reasonChip, setReasonChip] = useState<{ listingId: number; capsuleId?: string } | null>(null)
```

- [ ] **Step 2: Add auto-dismiss effect**

Directly after the existing golden toast `useEffect` (the one that clears `showGoldenToast` after 2500ms, around line 246–250), add:

```ts
useEffect(() => {
  if (!reasonChip) return
  const t = setTimeout(() => setReasonChip(null), 3000)
  return () => clearTimeout(t)
}, [reasonChip])
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
cd pilo-app && npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add pilo-app/src/App.tsx
git commit -m "feat(feedback): add reasonChip state with 3s auto-dismiss"
```

---

### Task 3: Clear chips on any card advance and sample on left swipe

**Files:**
- Modify: `pilo-app/src/App.tsx`

- [ ] **Step 1: Clear chips at the top of `flyCard`**

In `flyCard`, after `flying.current = true` and before `rememberSwipe()`, add:

```ts
setReasonChip(null)
```

Then inside the `if (dir === 'left')` branch, after `postDailyDropEvent('item_disliked', current, token)` and before `animate(x, -700, ...)`, add the sampling roll:

```ts
if (Math.random() < 0.2) {
  setReasonChip({ listingId: current.id, capsuleId: current.capsule_id })
}
```

The full `flyCard` left branch should look like:

```ts
if (dir === 'left') {
  recordAction(current.id, 'skip')
  postDailyDropEvent('item_disliked', current, token)
  if (Math.random() < 0.2) {
    setReasonChip({ listingId: current.id, capsuleId: current.capsule_id })
  }
  animate(x, -700, { duration: 0.32, ease: [0.22, 1, 0.36, 1] })
}
```

- [ ] **Step 2: Clear chips in `flyCardGolden`**

In `flyCardGolden`, after `flying.current = true` and before `rememberSwipe()`, add:

```ts
setReasonChip(null)
```

- [ ] **Step 3: Clear chips in `handleUndoSwipe`**

In `handleUndoSwipe`, after `if (flying.current) return` and before `const entry = swipeHistory.at(-1)`, add:

```ts
setReasonChip(null)
```

- [ ] **Step 4: Verify TypeScript compiles**

```bash
cd pilo-app && npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add pilo-app/src/App.tsx
git commit -m "feat(feedback): sample skip reason chips on 20% of left swipes"
```

---

### Task 4: Render the chip row above the action bar

**Files:**
- Modify: `pilo-app/src/App.tsx`

- [ ] **Step 1: Add the chip row JSX**

In the main card view return (the `return` block that starts with `<div className="relative h-full w-full overflow-hidden bg-black...`), add the following **immediately before** the `{/* Action buttons */}` div:

```tsx
{/* Skip reason chips */}
<AnimatePresence>
  {reasonChip && (
    <motion.div
      className="absolute left-0 right-0 z-[60] flex justify-center gap-2 px-6"
      style={{ bottom: 'calc(max(env(safe-area-inset-bottom), 22px) + 80px)' }}
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: 6 }}
      transition={{ duration: 0.18 }}
    >
      {([
        { label: 'Wrong size', reason: 'wrong_size' },
        { label: 'Too expensive', reason: 'too_expensive' },
        { label: 'Not my style', reason: 'not_my_style' },
      ] as const).map(({ label, reason }) => (
        <button
          key={reason}
          onClick={() => {
            postFeedback(reasonChip.listingId, 'dislike', token, false, reasonChip.capsuleId, reason)
            setReasonChip(null)
          }}
          className="rounded-full px-4 py-2 font-mono text-[10px] tracking-widest text-[#1E1C1A] whitespace-nowrap"
          style={{
            background: 'rgba(255,255,255,0.93)',
            border: '1px solid rgba(30,28,26,0.12)',
            boxShadow: '0 2px 10px rgba(0,0,0,0.12)',
          }}
        >
          {label}
        </button>
      ))}
    </motion.div>
  )}
</AnimatePresence>
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd pilo-app && npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Start dev server and verify manually**

```bash
cd pilo-app && npm run dev
```

Open the app in a browser. Swipe left repeatedly (use the ✕ button). After roughly 1 in 5 skips, the chip row should animate up above the action buttons. Verify:

1. Chips appear above the ✕ / ↶ / ♥ buttons without overlapping them
2. Tapping a chip dismisses it immediately (no lingering animation)
3. Chips auto-dismiss after ~3 seconds if untapped
4. A second left swipe while chips are visible clears the old chips before potentially showing new ones
5. Golden swipe (↑ or SAVE button) clears visible chips
6. Undo clears visible chips

Check the browser Network tab when tapping a chip: confirm a `POST /feedback` request fires with body `{ listing_id: <id>, action: "dislike", golden: false, capsule_id: "...", reason: "wrong_size" }` (or whichever chip was tapped).

Check that left swipes where chips don't appear send **no** `/feedback` request (only the `daily-drop/events` post).

- [ ] **Step 4: Commit**

```bash
git add pilo-app/src/App.tsx
git commit -m "feat(feedback): render skip reason chips above action bar"
```

---

### Task 5: Verify server receives and routes reasons correctly

**Files:** none — server-side logic already implemented

- [ ] **Step 1: Tail the server log while tapping chips**

In a separate terminal with the server running:

```bash
cd /path/to/pilo && python3 server.py
```

Tap "Too expensive" on a chip. Confirm the server does not log a vector update for that user (price-only path, early return in `_update_negative_capsule`).

Tap "Not my style". Confirm the capsule confidence and vector are updated in the user's JSON file in `users/`.

- [ ] **Step 2: Final commit if any debug logging was added**

```bash
git add pilo-app/src/App.tsx
git commit -m "fix(feedback): remove debug logging from reason chips"
```

Only needed if you added `console.log` statements during testing.
