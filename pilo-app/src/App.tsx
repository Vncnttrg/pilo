import { useState, useRef, useEffect } from 'react'
import {
  motion,
  AnimatePresence,
  useMotionValue,
  useTransform,
  animate,
  type PanInfo,
} from 'framer-motion'
import { Auth } from './Auth'
import { Onboarding, type OnboardingResult } from './Onboarding'
import type { Listing, UserProfile, Action } from './types'
import { API, LS_ACTIONS, LS_STYLE_VECTOR, LS_GENDER, LS_TOKEN, LS_USER, LS_PRICE_MIN, LS_PRICE_MAX, LS_DROP_CACHE } from './config'

const SWIPE_X = 90
const FLICK_V = 280
const SWIPE_Y = 80
const FEED_PAGE_SIZE = 50
const DAILY_DROP_SIZE = 12
const DROP_CACHE_TTL_MS = 6 * 60 * 60 * 1000

type Actions = Record<number, Action>
type SwipeHistoryEntry = {
  index: number
  photoIndex: number
  listingId: number
  previousAction?: Action
}
type DailyDropEvent =
  | 'item_seen'
  | 'item_clicked'
  | 'item_liked'
  | 'item_disliked'
  | 'drop_completed'
  | 'explore_more_clicked'
type DailyDropResponse = Listing[] | { listings: Listing[]; count?: number; date?: string }
type OpenListingResponse = { available: true; url: string } | { available: false; replacement?: Listing | null }

function todayKey(): string {
  return new Date().toISOString().slice(0, 10)
}

function loadDropCache(): { listings: Listing[]; idx: number } | null {
  try {
    const raw = localStorage.getItem(LS_DROP_CACHE)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    if (parsed?.date !== todayKey()) return null
    if (!parsed?.saved_at || Date.now() - parsed.saved_at > DROP_CACHE_TTL_MS) return null
    return { listings: parsed.listings, idx: parsed.idx ?? 0 }
  } catch {
    return null
  }
}

function saveDropCache(listings: Listing[], idx: number) {
  try {
    localStorage.setItem(LS_DROP_CACHE, JSON.stringify({ date: todayKey(), listings, idx, saved_at: Date.now() }))
  } catch {}
}

function clearDropCache() {
  localStorage.removeItem(LS_DROP_CACHE)
}

function loadActions(): Actions {
  try {
    return JSON.parse(localStorage.getItem(LS_ACTIONS) || '{}')
  } catch {
    return {}
  }
}

function loadStoredStyleVector(): number[] | null {
  try {
    const stored = localStorage.getItem(LS_STYLE_VECTOR)
    if (!stored) return null
    const parsed = JSON.parse(stored)
    return Array.isArray(parsed) ? parsed : null
  } catch {
    localStorage.removeItem(LS_STYLE_VECTOR)
    return null
  }
}

function loadStoredUserProfile(): UserProfile | null {
  try {
    const stored = localStorage.getItem(LS_USER)
    return stored ? (JSON.parse(stored) as UserProfile) : null
  } catch {
    localStorage.removeItem(LS_USER)
    return null
  }
}

function profileVector(user: UserProfile | null): number[] | null {
  return user?.capsules?.[0]?.vector ?? user?.style_vector ?? null
}

function loadPriceRange(): { price_min: number | null; price_max: number | null } {
  const rawMin = localStorage.getItem(LS_PRICE_MIN)
  const rawMax = localStorage.getItem(LS_PRICE_MAX)
  return {
    price_min: rawMin ? Number(rawMin) : null,
    price_max: rawMax !== null && rawMax !== '' ? Number(rawMax) : null,
  }
}

function loadInitialStyleVector(): number[] | null {
  const storedToken = localStorage.getItem(LS_TOKEN)
  const storedUser = loadStoredUserProfile()

  if (storedToken && storedUser) {
    return storedUser.completed_onboarding ? profileVector(storedUser) : null
  }

  return loadStoredStyleVector()
}

function ExternalLinkIcon() {
  return (
    <svg width="11" height="11" viewBox="0 0 11 11" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4.5 2H2a1 1 0 00-1 1v6a1 1 0 001 1h6a1 1 0 001-1V6.5" />
      <path d="M7 1h3v3" />
      <path d="M10 1L5.5 5.5" />
    </svg>
  )
}

function StarOutlineIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>
    </svg>
  )
}

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

function postDailyDropEvent(event: DailyDropEvent, listing?: Listing, token?: string | null) {
  fetch(`${API}/daily-drop/events`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({
      event,
      listing_id: listing?.id,
      capsule_id: listing?.capsule_id,
      final_score: listing?.final_score,
    }),
  }).catch((err) => console.warn('daily drop event failed:', err))
}

function getDailyDropListings(data: DailyDropResponse): Listing[] {
  return Array.isArray(data) ? data : data.listings
}

export default function App() {
  const [listings, setListings] = useState<Listing[]>([])
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [hasMore, setHasMore] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [idx, setIdx] = useState(0)
  const [photoIdx, setPhotoIdx] = useState(0)
  const [actions, setActions] = useState<Actions>(loadActions)
  const [swipeHistory, setSwipeHistory] = useState<SwipeHistoryEntry[]>([])
  const [showGoldenToast, setShowGoldenToast] = useState(false)
  const [lastGoldenItem, setLastGoldenItem] = useState<Listing | null>(null)
  const [reasonChip, setReasonChip] = useState<{ listingId: number; capsuleId?: string } | null>(null)
  const [endTab, setEndTab] = useState<'likes' | 'wants'>('wants')
  const [viewingList, setViewingList] = useState(false)
  const [batchCount, setBatchCount] = useState(1)
  const [confirmReset, setConfirmReset] = useState(false)
  const [usingDailyDrop, setUsingDailyDrop] = useState(true)
  const [dropCompletedLogged, setDropCompletedLogged] = useState(false)
  const flying = useRef(false)
  const seenDailyItems = useRef<Set<number>>(new Set())
  const sessionLikeCount = useRef(0)
  const [token, setToken] = useState<string | null>(() => localStorage.getItem(LS_TOKEN))
  const [user, setUser] = useState<UserProfile | null>(loadStoredUserProfile)
  const [styleVector, setStyleVector] = useState<number[] | null>(loadInitialStyleVector)
  const showOnboarding = styleVector === null

  useEffect(() => {
    if (!styleVector) return

    // Restore today's drop from cache so a reload resumes where the user left off
    const cached = loadDropCache()
    if (cached && cached.listings.length > 0) {
      setListings(cached.listings)
      setIdx(cached.idx)
      setHasMore(false)
      setUsingDailyDrop(true)
      setDropCompletedLogged(false)
      return
    }

    setLoading(true)
    setError(null)
    setUsingDailyDrop(true)
    setDropCompletedLogged(false)
    seenDailyItems.current = new Set()

    fetch(`${API}/daily-drop?limit=${DAILY_DROP_SIZE}`, {
      method: 'GET',
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json() as Promise<DailyDropResponse>
      })
      .then((data) => {
        const dropListings = getDailyDropListings(data)
        setListings(dropListings)
        setHasMore(false)
        setIdx(0)
        setPhotoIdx(0)
        saveDropCache(dropListings, 0)
        setLoading(false)
      })
      .catch((err) => {
        setError(String(err))
        setLoading(false)
      })
  }, [styleVector, token])

  useEffect(() => {
    if (!showGoldenToast) return
    const timer = setTimeout(() => setShowGoldenToast(false), 2500)
    return () => clearTimeout(timer)
  }, [showGoldenToast])

  useEffect(() => {
    if (!reasonChip) return
    const t = setTimeout(() => setReasonChip(null), 3000)
    return () => clearTimeout(t)
  }, [reasonChip])

  const x = useMotionValue(0)
  const rotate = useTransform(x, [-280, 280], [-16, 16])
  const likeOpacity = useTransform(x, [20, SWIPE_X * 1.5], [0, 1])
  const skipOpacity = useTransform(x, [-SWIPE_X * 1.5, -20], [1, 0])
  const nextScale = useTransform(x, [-220, 0, 220], [1, 0.94, 1])
  const nextOpacity = useTransform(x, [-220, 0, 220], [1, 0.55, 1])
  const y = useMotionValue(0)
  const goldenOpacity = useTransform(y, [-SWIPE_Y * 1.5, -20], [1, 0])

  const current = listings[idx] as Listing | undefined
  const next = listings[idx + 1] as Listing | undefined

  useEffect(() => {
    if (!current || seenDailyItems.current.has(current.id)) return
    seenDailyItems.current.add(current.id)
    postDailyDropEvent('item_seen', current, token)
  }, [current, token])

  useEffect(() => {
    if (!usingDailyDrop || loading || viewingList || current || listings.length === 0 || dropCompletedLogged) return
    postDailyDropEvent('drop_completed', undefined, token)
    setDropCompletedLogged(true)
  }, [current, dropCompletedLogged, listings.length, loading, token, usingDailyDrop, viewingList])

  function recordAction(id: number, action: Action) {
    const updated = { ...actions, [id]: action }
    setActions(updated)
    localStorage.setItem(LS_ACTIONS, JSON.stringify(updated))
  }

  function rememberSwipe() {
    if (!current) return
    setSwipeHistory((prev) => [
      ...prev,
      {
        index: idx,
        photoIndex: photoIdx,
        listingId: current.id,
        previousAction: actions[current.id],
      },
    ].slice(-20))
  }

  function restorePreviousAction(entry: SwipeHistoryEntry) {
    setActions((prev) => {
      const updated = { ...prev }
      if (entry.previousAction) updated[entry.listingId] = entry.previousAction
      else delete updated[entry.listingId]
      localStorage.setItem(LS_ACTIONS, JSON.stringify(updated))
      return updated
    })
  }

  function handleUndoSwipe() {
    if (flying.current) return
    const entry = swipeHistory.at(-1)
    if (!entry) return

    setSwipeHistory((prev) => prev.slice(0, -1))
    setShowGoldenToast(false)
    restorePreviousAction(entry)
    setIdx(entry.index)
    setPhotoIdx(entry.photoIndex)
    x.set(0)
    y.set(0)
  }

  function fetchPersonalisedRefill() {
    if (!token) return
    const seenIds = Array.from(new Set([
      ...listings.map((l) => l.id),
      ...Array.from(seenDailyItems.current),
    ])).join(',')
    fetch(`${API}/daily-drop?limit=${DAILY_DROP_SIZE}&force_refresh=true&exclude=${seenIds}`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => r.ok ? r.json() as Promise<DailyDropResponse> : Promise.reject())
      .then((data) => {
        const fresh = getDailyDropListings(data).filter((l) => !seenDailyItems.current.has(l.id))
        if (fresh.length > 0) {
          setListings((prev) => {
            const updated = [...prev, ...fresh]
            saveDropCache(updated, idx)
            return updated
          })
        }
      })
      .catch(() => {})
  }

  function loadMoreProducts(forceExplore = false) {
    if (!styleVector || loadingMore || (!hasMore && !forceExplore)) return

    const startIdx = listings.length
    setLoadingMore(true)
    setError(null)
    if (forceExplore) {
      postDailyDropEvent('explore_more_clicked', undefined, token)
      setDropCompletedLogged(true)
    }
    const seenIds = Array.from(new Set([
      ...Object.keys(loadActions()).map(Number),
      ...Array.from(seenDailyItems.current),
      ...listings.map((listing) => listing.id),
    ]))

    fetch(`${API}/feed`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({
        style_vector: styleVector,
        gender: localStorage.getItem(LS_GENDER) || undefined,
        ...loadPriceRange(),
        offset: 0,
        limit: FEED_PAGE_SIZE,
        seen_ids: seenIds,
      }),
    })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json() as Promise<Listing[]>
      })
      .then((data) => {
        const existingIds = new Set(listings.map((listing) => listing.id))
        const freshListings = data.filter((listing) => !existingIds.has(listing.id))

        setHasMore(freshListings.length > 0)
        if (freshListings.length > 0) {
          if (forceExplore) setUsingDailyDrop(false)
          setListings((prev) => [...prev, ...freshListings])
          setIdx(startIdx)
          setBatchCount((n) => n + 1)
          setPhotoIdx(0)
          x.set(0)
          y.set(0)
        }
      })
      .catch((err) => setError(String(err)))
      .finally(() => setLoadingMore(false))
  }

  function replaceUnavailableListing(deadId: number, replacement?: Listing | null) {
    seenDailyItems.current.add(deadId)
    const deadIndex = listings.findIndex((listing) => listing.id === deadId)
    const filtered = listings.filter((listing) => listing.id !== deadId)
    let updated = filtered

    if (
      replacement &&
      !seenDailyItems.current.has(replacement.id) &&
      !filtered.some((listing) => listing.id === replacement.id)
    ) {
      const insertAt = deadIndex >= 0 ? Math.min(deadIndex, filtered.length) : filtered.length
      updated = [...filtered.slice(0, insertAt), replacement, ...filtered.slice(insertAt)]
    }

    const nextIdx = Math.min(idx, Math.max(updated.length - 1, 0))
    setListings(updated)
    setIdx(nextIdx)
    setPhotoIdx(0)
    if (usingDailyDrop) saveDropCache(updated, nextIdx)
  }

  async function openListing(item: Listing) {
    const popup = window.open('about:blank', '_blank')
    if (popup) popup.opener = null

    try {
      const excludeIds = Array.from(new Set([
        ...listings.map((listing) => listing.id),
        ...Array.from(seenDailyItems.current),
      ]))
      const response = await fetch(`${API}/listings/${item.id}/open`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          capsule_id: item.capsule_id,
          final_score: item.final_score,
          exclude_ids: excludeIds,
        }),
      })
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const data = await response.json() as OpenListingResponse
      if (data.available) {
        if (popup) popup.location.href = data.url
        else window.open(data.url, '_blank', 'noopener,noreferrer')
        return
      }

      popup?.close()
      replaceUnavailableListing(item.id, data.replacement ?? null)
    } catch (err) {
      popup?.close()
      console.warn('open recheck failed:', err)
    }
  }

  function flyCard(dir: 'left' | 'right') {
    if (flying.current || !current) return
    flying.current = true
    rememberSwipe()
    if (dir === 'left') {
      recordAction(current.id, 'skip')
      postDailyDropEvent('item_disliked', current, token)
      animate(x, -700, { duration: 0.32, ease: [0.22, 1, 0.36, 1] })
    } else {
      recordAction(current.id, 'like')
      postFeedback(current.id, 'like', token, false, current.capsule_id)
      postDailyDropEvent('item_liked', current, token)
      if (usingDailyDrop) {
        sessionLikeCount.current += 1
        if (sessionLikeCount.current % 3 === 0) fetchPersonalisedRefill()
      }
      animate(x, 700, { duration: 0.32, ease: [0.22, 1, 0.36, 1] })
    }
    setTimeout(() => {
      const nextIndex = idx + 1
      if (nextIndex >= listings.length && hasMore && !usingDailyDrop) {
        setIdx(listings.length)
        loadMoreProducts()
      } else {
        setIdx(nextIndex)
        if (usingDailyDrop) saveDropCache(listings, nextIndex)
      }
      setPhotoIdx(0)
      x.set(0)
      y.set(0)
      flying.current = false
    }, 360)
  }

  function flyCardGolden() {
    if (flying.current || !current) return
    flying.current = true
    rememberSwipe()
    recordAction(current.id, 'golden')
    postFeedback(current.id, 'like', token, true, current.capsule_id)
    postDailyDropEvent('item_liked', current, token)
    setLastGoldenItem(current)
    animate(y, -700, { duration: 0.32, ease: [0.22, 1, 0.36, 1] })
    setTimeout(() => {
      const nextIndex = idx + 1
      if (nextIndex >= listings.length && hasMore && !usingDailyDrop) {
        setIdx(listings.length)
        loadMoreProducts()
      } else {
        setIdx(nextIndex)
      }
      setPhotoIdx(0)
      x.set(0)
      y.set(0)
      setShowGoldenToast(true)
      flying.current = false
    }, 360)
  }

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

  function handleCardTap(event: MouseEvent | TouchEvent | PointerEvent) {
    if (flying.current || !current) return
    if ((event.target as HTMLElement).closest('a, button')) return
    const images = current.image_urls.length > 0 ? current.image_urls : [current.image_url]
    setPhotoIdx((i) => (i + 1) % images.length)
  }

  const likedListings = listings.filter((listing) => actions[listing.id] === 'like')
  const goldenListings = listings.filter((listing) => actions[listing.id] === 'golden')

  function handleOnboardingComplete(result: OnboardingResult) {
    clearDropCache()
    setStyleVector(result.style_vector)
    if (user && token) {
      const updated: UserProfile = {
        ...user,
        style_vector: result.style_vector,
        capsules: result.capsules,
        global_constraints: result.global_constraints,
        completed_onboarding: true,
      }
      setUser(updated)
      localStorage.setItem(LS_USER, JSON.stringify(updated))
    }
  }

  // ── Loading / error states ────────────────────────────────────────────────

  if (!token || !user) {
    return (
      <Auth
        onComplete={(tok, usr) => {
          setToken(tok)
          setUser(usr)
          const vec = profileVector(usr)
          if (usr.completed_onboarding && vec) {
            setStyleVector(vec)
          } else {
            setStyleVector(null)
            localStorage.removeItem(LS_STYLE_VECTOR)
          }
        }}
      />
    )
  }

  if (showOnboarding) {
    return <Onboarding onComplete={handleOnboardingComplete} token={token} />
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full bg-[#FAFAF8]">
        <span className="font-dmserif text-2xl text-[#1E1C1A]/30 animate-pulse">
          Pilo
        </span>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-full bg-[#FAFAF8] text-[#1E1C1A] gap-3 px-8 text-center">
        <p className="font-dmserif text-3xl">No connection</p>
        <p className="font-mono text-xs text-[#1E1C1A]/40">
          Make sure the server is running:
        </p>
        <code className="font-mono text-xs text-[#1E1C1A]/40 bg-[#1E1C1A]/[0.04] px-4 py-2 rounded">
          python3 server.py
        </code>
        <button
          onClick={() => { setLoading(true); setError(null); window.location.reload() }}
          className="mt-4 px-6 py-2 border border-[#1E1C1A]/10 text-[#1E1C1A]/40 font-mono text-xs tracking-widest hover:bg-[#1E1C1A]/[0.03] transition-colors"
        >
          RETRY
        </button>
      </div>
    )
  }

  if (!current && usingDailyDrop && !viewingList) {
    const likedCount = likedListings.length + goldenListings.length

    return (
      <div className="flex flex-col h-full bg-[#FAFAF8] text-[#1E1C1A] px-6">
        <div
          className="flex items-center justify-between"
          style={{ paddingTop: 'max(env(safe-area-inset-top), 16px)', paddingBottom: '16px' }}
        >
          <div>
            <p className="font-mono text-[9px] tracking-widest uppercase text-[#1E1C1A]/30">
              Today's Pilo Drop — {listings.length} finds matched to your taste
            </p>
            <h1 className="font-dmserif text-[28px] leading-none mt-1">Pilo</h1>
          </div>
          {goldenListings.length > 0 && (
            <button
              onClick={() => { setEndTab('wants'); setViewingList(true) }}
              className="font-mono text-[10px] text-[#1E1C1A]/45 tracking-widest hover:text-[#1E1C1A] transition-colors flex items-center gap-1"
            >
              <StarOutlineIcon /> {goldenListings.length}
            </button>
          )}
        </div>

        <div className="flex-1 flex flex-col items-center justify-center text-center pb-20">
          <p className="font-dmserif text-[36px] leading-none max-w-[280px]">
            You're caught up for today
          </p>
          <p className="font-mono text-[11px] leading-relaxed text-[#1E1C1A]/40 mt-4 max-w-[260px]">
            {likedCount > 0
              ? `${likedCount} saved signal${likedCount === 1 ? '' : 's'} from today's drop.`
              : 'Nothing saved yet, and your taste stays unchanged.'}
          </p>
          <button
            onClick={() => loadMoreProducts(true)}
            disabled={loadingMore}
            className="mt-8 px-6 py-3 rounded-full bg-[#1E1C1A] text-[#FAFAF8] font-mono text-[10px] tracking-widest uppercase disabled:opacity-40"
          >
            {loadingMore ? 'Loading…' : 'Explore more'}
          </button>
        </div>
      </div>
    )
  }

  if (!current || viewingList) {
    const tabListings = endTab === 'wants' ? goldenListings : likedListings
    const batchSwiped = listings.length
    const batchLiked = likedListings.length + goldenListings.length
    const likeRate = batchSwiped > 0 ? Math.round((batchLiked / batchSwiped) * 100) : 0
    const likedPrices = [...likedListings, ...goldenListings].map((l) => l.price).filter(Boolean)
    const avgLikedPrice = likedPrices.length > 0
        ? Math.round(likedPrices.reduce((a, b) => a + b, 0) / likedPrices.length)
        : null

    return (
      <div className="flex flex-col h-full bg-[#FAFAF8] text-[#1E1C1A]">
        {/* Header */}
        <div
          className="px-5"
          style={{ paddingTop: 'max(env(safe-area-inset-top), 16px)', paddingBottom: '0px' }}
        >
          {viewingList ? (
            <div className="flex items-center py-4">
              <button
                onClick={() => setViewingList(false)}
                className="font-mono text-[10px] text-[#1E1C1A]/40 tracking-widest hover:text-[#1E1C1A]/70 transition-colors"
              >
                ← Zurück
              </button>
            </div>
          ) : (
            <div className="pt-4 pb-3">
              <div className="flex items-center justify-between mb-4">
                <div>
                  <p className="font-mono text-[9px] tracking-widest text-[#1E1C1A]/30 uppercase mb-0.5">Runde {batchCount}</p>
                  <div className="flex items-baseline gap-3">
                    <span className="font-dmserif text-[28px] text-[#1E1C1A] leading-none">{batchLiked}</span>
                    <span className="font-mono text-[11px] text-[#1E1C1A]/35">von {batchSwiped} · {likeRate}%{avgLikedPrice !== null ? ` · ø €${avgLikedPrice}` : ''}</span>
                  </div>
                </div>
                <button
                  onClick={() => {
                    if (hasMore) {
                      setConfirmReset(false)
                      loadMoreProducts()
                    } else if (confirmReset) {
                      setIdx(0)
                      setPhotoIdx(0)
                      setActions({})
                      setEndTab('wants')
                      setShowGoldenToast(false)
                      setBatchCount(1)
                      setConfirmReset(false)
                      localStorage.removeItem(LS_ACTIONS)
                    } else {
                      setConfirmReset(true)
                      setTimeout(() => setConfirmReset(false), 3000)
                    }
                  }}
                  disabled={loadingMore}
                  className="px-4 py-2 rounded-full font-mono text-[10px] tracking-widest transition-colors"
                  style={{ background: '#1E1C1A', color: '#FAFAF8', opacity: loadingMore ? 0.4 : 1 }}
                >
                  {loadingMore ? 'Laden…' : hasMore ? 'Weiter →' : confirmReset ? 'Sicher?' : 'Neu starten'}
                </button>
              </div>
            </div>
          )}
        </div>

        {/* Tabs */}
        <div className="flex border-b border-[#1E1C1A]/[0.08] mx-5">
          {(['wants', 'likes'] as const).map((tab) => {
            const count = tab === 'wants' ? goldenListings.length : likedListings.length
            const active = endTab === tab
            return (
              <button
                key={tab}
                onClick={() => setEndTab(tab)}
                className={`flex-1 py-3 font-mono text-[10px] tracking-widest uppercase transition-colors ${
                  active
                    ? 'text-[#1E1C1A] border-b border-[#1E1C1A]'
                    : 'text-[#1E1C1A]/30 hover:text-[#1E1C1A]/60'
                }`}
              >
                {tab === 'wants' ? 'Gespeichert' : 'Gefällt'} {count > 0 ? `(${count})` : ''}
              </button>
            )
          })}
        </div>

        {/* Grid */}
        <div className="flex-1 overflow-y-auto">
          {tabListings.length === 0 ? (
            <div className="flex items-center justify-center h-32">
              <p className="font-mono text-[10px] text-[#1E1C1A]/25 tracking-wider">
                {loadingMore ? 'Laden…' : endTab === 'wants' ? 'Noch nichts gespeichert' : 'Noch keine Likes'}
              </p>
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-px" style={{ background: 'rgba(30,28,26,0.06)' }}>
              {tabListings.map((item) => (
                <a
                  key={item.id}
                  href={item.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={(e) => {
                    e.preventDefault()
                    openListing(item)
                  }}
                  className="relative bg-[#F0EDE8] overflow-hidden"
                  style={{ aspectRatio: '3/4' }}
                >
                  <img
                    src={item.image_urls[0] || item.image_url}
                    className="absolute inset-0 w-full h-full object-cover"
                  />
                  <div
                    className="absolute bottom-0 left-0 right-0 px-3 py-2.5"
                    style={{ background: 'linear-gradient(to top, rgba(0,0,0,0.62), transparent)' }}
                  >
                    <p className="font-dmserif text-[15px] text-white leading-tight truncate">{item.brand}</p>
                    <p className="font-mono text-[11px] text-white/75 font-bold">
                      €{item.price % 1 === 0 ? item.price.toFixed(0) : item.price.toFixed(2)}
                    </p>
                  </div>
                </a>
              ))}
            </div>
          )}
        </div>
      </div>
    )
  }

  const images = current.image_urls.length > 0 ? current.image_urls : [current.image_url]

  return (
    <div className="relative h-full w-full overflow-hidden bg-black touch-none select-none">
      {/* Top bar */}
      <div
        className="absolute top-0 left-0 right-0 z-50 px-5"
        style={{ paddingTop: 'max(env(safe-area-inset-top), 16px)', paddingBottom: '10px' }}
      >
        <div className="flex items-center justify-between py-3">
          <div>
            <span className="font-dmserif text-[22px] text-white">
              {usingDailyDrop ? "Today's Pilo Drop" : 'Pilo'}
            </span>
            {usingDailyDrop && (
              <p className="font-mono text-[8px] tracking-widest text-white/40 uppercase mt-0.5">
                {listings.length} finds matched to your taste
              </p>
            )}
          </div>
          <div className="flex items-center gap-3">
            {goldenListings.length > 0 && (
              <button
                onClick={() => { setEndTab('wants'); setViewingList(true) }}
                className="font-mono text-[9px] text-white/40 tracking-widest hover:text-white transition-colors flex items-center gap-1"
              >
                <StarOutlineIcon /> {goldenListings.length}
              </button>
            )}
            <span className="font-mono text-[9px] text-white/40 tracking-widest">
              {idx + 1} / {listings.length}
            </span>
          </div>
        </div>
      </div>

      {/* Card stack */}
      <div className="absolute inset-0">
        {/* Next card */}
        {next && (
          <motion.div
            className="absolute inset-0 overflow-hidden"
            style={{ scale: nextScale, opacity: nextOpacity }}
          >
            <img
              src={next.image_urls[0] || next.image_url}
              className="absolute inset-0 w-full h-full object-cover"
              draggable={false}
            />
          </motion.div>
        )}

        {/* Active card */}
        <motion.div
          key={current.id}
          className="absolute inset-0 overflow-hidden bg-[#111] cursor-grab active:cursor-grabbing"
          style={{ x, y, rotate }}
          drag
          dragConstraints={{ left: 0, right: 0, top: 0, bottom: 0 }}
          dragElastic={{ left: 0.82, right: 0.82, top: 0.82, bottom: 0 }}
          onDragEnd={handleDragEnd}
          onTap={handleCardTap}
        >
          {/* Photo carousel */}
          <AnimatePresence mode="sync" initial={false}>
            <motion.img
              key={`${current.id}-${photoIdx}`}
              src={images[photoIdx]}
              className="absolute inset-0 w-full h-full object-cover"
              draggable={false}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.18 }}
            />
          </AnimatePresence>

          {/* Bottom gradient */}
          <div className="absolute inset-0" style={{ background: 'linear-gradient(to top, rgba(0,0,0,0.78) 0%, rgba(0,0,0,0.3) 38%, transparent 60%)' }} />

          {/* Photo dots */}
          {images.length > 1 && (
            <div
              className="absolute left-0 right-0 z-10 flex justify-center gap-[5px]"
              style={{ top: 'calc(max(env(safe-area-inset-top), 16px) + 54px)' }}
            >
              {images.map((_, i) => (
                <div
                  key={i}
                  className="h-[3px] rounded-full transition-all duration-200"
                  style={{
                    width: i === photoIdx ? '18px' : '6px',
                    background: i === photoIdx ? 'rgba(255,255,255,0.80)' : 'rgba(255,255,255,0.30)',
                  }}
                />
              ))}
            </div>
          )}

          {/* Want button */}
          <button
            onClick={(e) => {
              e.stopPropagation()
              flyCardGolden()
            }}
            className="absolute right-4 z-10 h-10 rounded-full flex items-center justify-center gap-1.5 px-3 transition-colors"
            style={{
              top: 'calc(max(env(safe-area-inset-top), 16px) + 50px)',
              background: 'rgba(255,255,255,0.90)',
              boxShadow: '0 2px 10px rgba(0,0,0,0.15)',
              color: '#1A1612',
            }}
            aria-label="Want this"
          >
            <StarOutlineIcon />
            <span className="font-mono text-[10px] tracking-wider">SAVE</span>
          </button>

          {/* Swipe stamps */}
          <motion.div
            className="absolute top-[130px] left-5 border-[2px] border-[#5DC48C] text-[#5DC48C] px-4 py-1 font-mono font-bold text-[15px] tracking-widest -rotate-12"
            style={{ opacity: likeOpacity }}
            aria-hidden
          >
            LIKE
          </motion.div>

          <motion.div
            className="absolute top-[130px] right-5 border-[2px] border-[#FF6B6B] text-[#FF6B6B] px-4 py-1 font-mono font-bold text-[15px] tracking-widest rotate-12"
            style={{ opacity: skipOpacity }}
            aria-hidden
          >
            SKIP
          </motion.div>

          <motion.div
            className="absolute top-[130px] left-1/2 -translate-x-1/2 border-[2px] border-white text-white px-4 py-1 font-mono font-bold text-[13px] tracking-widest -rotate-6 whitespace-nowrap"
            style={{ opacity: goldenOpacity }}
            aria-hidden
          >
            ⭐ WANT
          </motion.div>

          {/* Listing info */}
          <div
            className="absolute bottom-0 left-0 right-0 px-5"
            style={{ paddingBottom: `calc(max(env(safe-area-inset-bottom), 0px) + 76px)` }}
          >
            {current.daily_drop_tag && (
              <div className="mb-2 inline-flex rounded-full border border-white/15 bg-white/10 px-2.5 py-1 font-mono text-[9px] tracking-widest text-white/70 uppercase backdrop-blur">
                {current.daily_drop_tag}
              </div>
            )}
            {current.recommendation_reason && (
              <p className="mb-2 font-mono text-[9px] tracking-wide text-white/40">
                {current.recommendation_reason}
              </p>
            )}
            <div className="flex items-end justify-between gap-3 mb-1.5">
              <h2 className="font-dmserif text-[30px] text-white leading-none truncate">
                {current.brand}
              </h2>
              <span className="font-mono text-[22px] text-white font-bold leading-none shrink-0">
                €{current.price % 1 === 0 ? current.price.toFixed(0) : current.price.toFixed(2)}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <p className="font-mono text-[10px] text-white/40 line-clamp-1 flex-1">
                {current.title}
              </p>
              <a
                href={current.url}
                target="_blank"
                rel="noopener noreferrer"
                onClick={(e) => {
                  e.preventDefault()
                  e.stopPropagation()
                  openListing(current)
                }}
                className="flex items-center gap-1 font-mono text-[10px] text-white/35 ml-3 shrink-0 hover:text-white/60 transition-colors"
              >
                <ExternalLinkIcon />
                vinted
              </a>
            </div>
          </div>
        </motion.div>
      </div>

      {/* Action buttons */}
      <div
        className="absolute bottom-0 left-0 right-0 z-50 flex justify-center items-center gap-7 px-8"
        style={{ paddingBottom: `max(env(safe-area-inset-bottom), 22px)`, paddingTop: '14px' }}
      >
        <motion.button
          whileTap={{ scale: 0.88 }}
          onClick={() => flyCard('left')}
          className="w-[54px] h-[54px] rounded-full flex items-center justify-center text-[#E53935] text-lg"
          style={{ background: 'rgba(255,255,255,0.93)', boxShadow: '0 2px 14px rgba(0,0,0,0.18)' }}
          aria-label="Skip"
        >
          ✕
        </motion.button>

        <motion.button
          whileTap={swipeHistory.length > 0 ? { scale: 0.88 } : undefined}
          onClick={handleUndoSwipe}
          disabled={swipeHistory.length === 0}
          className={`w-[42px] h-[42px] rounded-full flex items-center justify-center text-base transition-opacity ${
            swipeHistory.length > 0 ? 'opacity-100' : 'opacity-30'
          }`}
          style={{ background: 'rgba(255,255,255,0.93)', boxShadow: '0 2px 10px rgba(0,0,0,0.12)', color: '#9E9E9E' }}
          aria-label="Undo last swipe"
        >
          ↶
        </motion.button>

        <motion.button
          whileTap={{ scale: 0.88 }}
          onClick={() => flyCard('right')}
          className="w-[54px] h-[54px] rounded-full flex items-center justify-center text-[#43A047] text-xl"
          style={{ background: 'rgba(255,255,255,0.93)', boxShadow: '0 2px 14px rgba(0,0,0,0.18)' }}
          aria-label="Like"
        >
          ♥
        </motion.button>
      </div>

      {/* Golden toast */}
      <AnimatePresence>
        {showGoldenToast && lastGoldenItem && (
          <motion.div
            className="absolute left-4 right-4 z-[110] flex items-center justify-between px-4 py-3 rounded-xl"
            style={{
              top: 'calc(max(env(safe-area-inset-top), 16px) + 64px)',
              background: 'rgba(250,250,248,0.96)',
              backdropFilter: 'blur(16px)',
              WebkitBackdropFilter: 'blur(16px)',
              border: '1px solid rgba(30,28,26,0.12)',
            }}
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={{ duration: 0.18 }}
          >
            <div className="flex items-center gap-2">
              <span className="text-[#1E1C1A]/50"><StarOutlineIcon /></span>
              <span className="font-mono text-[11px] text-[#1E1C1A]/60 tracking-wide">
                {lastGoldenItem.brand} · €{lastGoldenItem.price % 1 === 0 ? lastGoldenItem.price.toFixed(0) : lastGoldenItem.price.toFixed(2)}
              </span>
            </div>
            <a
              href={lastGoldenItem.url}
              target="_blank"
              rel="noopener noreferrer"
              onClick={(e) => {
                e.preventDefault()
                openListing(lastGoldenItem)
                setShowGoldenToast(false)
              }}
              className="font-mono text-[11px] font-bold text-[#1E1C1A] tracking-wider hover:text-[#1E1C1A]/70 transition-colors"
            >
              Go →
            </a>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
