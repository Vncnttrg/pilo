# Skip Reason Chips — Design Spec

**Date:** 2026-05-12  
**Status:** Approved

---

## Problem

Left swipes currently send no feedback to the server — only a `daily-drop/events` log entry (`item_disliked`). The `/feedback` endpoint's `_update_negative_capsule` logic was recently tiered by reason (wrong_size, too_expensive, not_my_style, etc.), but the client never sends a reason, so all that work has no data to act on.

A per-swipe reason prompt was previously tried and felt like too much friction. A batch prompt every N swipes loses per-item attribution. The right middle ground: an optional, sampled chip row that appears 1 in 5 skips.

---

## Solution

After 1 in 5 left swipes (random 20% sample), a row of 3 reason chips animates in above the action bar. The user can tap one or ignore it.

- **Tap** → posts skip feedback with the selected reason, chips dismiss immediately
- **Ignore** → chips auto-dismiss silently after 3 seconds, nothing posted

No feedback is posted on dismiss. Unengaged skips don't add noise — the server's unspecified path already handles them with a small nudge.

---

## Chips

Three options, matching the server-side reason tiers exactly:

| Label | `reason` value | Server effect |
|---|---|---|
| Wrong size | `wrong_size` | No-op (non-style signal) |
| Too expensive | `too_expensive` | Tightens `price_max` only |
| Not my style | `not_my_style` | Full vector repulsion + confidence −0.05 |

---

## State

One new state variable:

```ts
reasonChip: { listingId: number; capsuleId?: string } | null
```

- `null` → chips hidden
- Set when the 20% roll hits at the end of `flyCard('left')`
- Cleared on chip tap or auto-dismiss timeout

---

## Sampling

In `flyCard('left')`, after recording the skip action:

```ts
if (Math.random() < 0.2) {
  setReasonChip({ listingId: current.id, capsuleId: current.capsule_id })
}
```

Random sampling (not every-5th) so it doesn't feel mechanical.

---

## `postFeedback` update

Add optional `reason` param:

```ts
function postFeedback(
  id: number,
  action: 'like' | 'dislike',
  token?: string | null,
  golden = false,
  capsuleId?: string,
  reason?: string,
)
```

Include in request body: `reason: reason ?? 'none'` only when action is `'dislike'` and reason is provided.

---

## Auto-dismiss

```ts
useEffect(() => {
  if (!reasonChip) return
  const t = setTimeout(() => setReasonChip(null), 3000)
  return () => clearTimeout(t)
}, [reasonChip])
```

---

## Animation

Uses Framer Motion `AnimatePresence`, consistent with the golden toast pattern already in the file:

```
initial:  { opacity: 0, y: 10 }
animate:  { opacity: 1, y: 0 }
exit:     { opacity: 0, y: 6 }
transition: duration 0.18
```

Position: absolute, centered, above the action bar. `z-[60]` (above action bar's `z-50`).

The chip row sits approximately 12px above the top edge of the action bar. Computed using the same bottom padding pattern as the action bar itself.

---

## Visual style

Matches Pilo's existing chip/badge aesthetic:

- Background: `rgba(255,255,255,0.93)` (matches action buttons)
- Border: `1px solid rgba(30,28,26,0.12)`
- Text: `font-mono text-[10px] tracking-widest` in `#1E1C1A`
- Rounded pill shape: `rounded-full px-4 py-2`
- Box shadow: `0 2px 10px rgba(0,0,0,0.12)`
- Gap between chips: `gap-2`

No hover states needed (touch-first interface).

---

## Files changed

`pilo-app/src/App.tsx` only. No new files.

Changes:
1. Update `postFeedback` signature and body to include optional `reason`
2. Add `reasonChip` state
3. Add auto-dismiss `useEffect`
4. Add 20% roll in `flyCard('left')`
5. Add `SkipReasonChips` inline component (or inline JSX) rendered above action bar inside `AnimatePresence`

---

## Out of scope

- Persisting which skips showed chips
- Analytics on chip tap rate
- More than 3 chip options
