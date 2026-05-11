# Three-Tier Swipe Model

**Date:** 2026-05-11
**Status:** Ready for implementation

---

## Problem

Every left swipe triggers a blocking bottom sheet ("What didn't work?") that requires a tap before the next card appears. When multiple consecutive items aren't interesting, this mandatory interrupt breaks the swiping rhythm. The two dominant dislike reasons — wrong price and wrong style — were being collected via friction instead of inferred from behavior.

---

## Solution

Replace the two-action model (like / skip) with a three-tier model where each gesture carries a distinct semantic meaning. The feedback loop is solved implicitly: the difference between a like and a golden swipe *is* the price signal, collected for free without ever asking.

---

## The Three Tiers

### Skip — swipe left
**User meaning:** "Not my style."
**What happens:** Card flies left immediately. No sheet. No tap required.
**Algorithm signal:** Weak negative on style. Show less like this.

### Like — swipe right
**User meaning:** "I love this aesthetic. Maybe aspirational — I might not buy it at this price."
**What happens:** Card flies right (unchanged from today). Added to Likes pile.
**Algorithm signal:** Strong positive style signal. Show more like this.

### Golden — swipe up
**User meaning:** "I would buy this today. Style is right *and* the price works for me."
**What happens:** Card gets a ⭐ stamp animation. A toast appears at the top with "Go now →" tap target. Tapping opens the Vinted listing. Ignoring it moves to the next card automatically. Item added to the Golden/Wants pile.
**Algorithm signal:** Strongest signal. Calibrates both style vector and price range expectations.

---

## Implicit Feedback Loop

The three gestures replace the reason sheet entirely:

| Pattern | Inference |
|---|---|
| Skipped | Wrong style |
| Liked but never golden | Right style, price too high |
| Golden | Right style, right price |

No explicit reason collection needed. The system learns price sensitivity from the delta between likes and goldens without asking.

---

## Interaction Changes

### Removed
- The "What didn't work?" bottom sheet on skip — entirely gone
- The `cancelDislikeFlow`, `triggerDislikeFlow`, `commitDislike`, and `showReasonSheet` logic
- The `DISLIKE_REASONS` constant and `DislikeReason` type

### Added
- **Swipe-up gesture** on the active card (y-axis drag detection with threshold, similar to current x-axis logic)
- **Golden stamp animation** — ⭐ overlay appears on card at moment of golden swipe, similar to existing LIKE/SKIP stamps
- **Toast with escape hatch** — appears at top of screen after golden swipe: item name + price + "Go →" button linking to Vinted URL. Auto-dismisses after ~2.5s if not tapped.
- **`golden` action type** added alongside existing `like` and `skip`
- **Separate golden pile** in local state and localStorage

### Unchanged
- Right-swipe like flow
- Bookmark button (save without committing to a tier)
- Undo swipe functionality
- Algorithm feedback POST to `/feedback` — golden sends `action: 'like'` with an additional `golden: true` flag so the backend can weight it more heavily

---

## Post-Session Views

Two distinct review surfaces replace the current single saved/liked state:

### Likes Pile
- Style inspiration and algorithm reference
- Cards the user appreciated visually, may or may not be buyable
- No urgency framing — this is a moodboard

### Golden / Wants Pile
- Items the user would buy at the listed price
- Each item links directly to the Vinted listing
- Framed as an action list: "things you could buy right now"
- Future: price-drop alerts, "still available?" checks

---

## Algorithm Implications

- **Skip** → existing dislike handling (no reason payload needed)
- **Like** → unchanged weight
- **Golden** → higher weight than like; also signals acceptable price range for that style category
- Backend receives `golden: true` on feedback POST for golden swipes; can be used to adjust LIKE_WEIGHT multiplier or tracked separately

---

## What's Parked (Not MVP)

- Deal comparison engine ("this €2 shirt is way under market for this brand")
- "Why haven't you bought this yet?" nudge on liked items
- Reseller value detection
- Price-drop notifications on golden items
- "Still available?" staleness checking

---

## Files That Will Change

- `pilo-app/src/App.tsx` — primary changes (gesture detection, state, UI)
- `server.py` — minor: accept `golden` flag on `/feedback` endpoint
- `pilo-app/src/types.ts` — add `golden` to action types

---

## Success Criteria

1. Left swipe requires zero additional interaction
2. Swipe-up triggers the golden stamp + toast reliably
3. "Go now →" opens the correct Vinted URL
4. Likes and golden items are stored separately and retrievable
5. The algorithm receives correctly weighted signals for all three actions
