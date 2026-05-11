import { useState, useRef, useEffect } from 'react'
import {
  motion,
  AnimatePresence,
  useMotionValue,
  useTransform,
  animate,
  type PanInfo,
} from 'framer-motion'
import { Onboarding } from './Onboarding'
import type { Listing, Action } from './types'
import { API, LS_ACTIONS, LS_STYLE_VECTOR } from './config'

const SWIPE_X = 90
const FLICK_V = 280
const SWIPE_Y = 80

type Actions = Record<number, Action>
type SwipeHistoryEntry = {
  index: number
  photoIndex: number
  listingId: number
  previousAction?: Action
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

function BookmarkIcon({ filled }: { filled: boolean }) {
  return (
    <svg width="18" height="22" viewBox="0 0 18 22" fill={filled ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M2 2h14v18l-7-4.5L2 20V2z" />
    </svg>
  )
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

function postFeedback(id: number, action: 'like' | 'dislike', golden = false) {
  fetch(`${API}/feedback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ listing_id: id, action, golden }),
  }).catch((err) => console.warn('feedback failed:', err))
}

function postSave(id: number) {
  fetch(`${API}/save`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id }),
  }).catch((err) => console.warn('save failed:', err))
}

export default function App() {
  const [listings, setListings] = useState<Listing[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [idx, setIdx] = useState(0)
  const [photoIdx, setPhotoIdx] = useState(0)
  const [actions, setActions] = useState<Actions>(loadActions)
  const [swipeHistory, setSwipeHistory] = useState<SwipeHistoryEntry[]>([])
  const [showGoldenToast, setShowGoldenToast] = useState(false)
  const [lastGoldenItem, setLastGoldenItem] = useState<Listing | null>(null)
  const [endTab, setEndTab] = useState<'likes' | 'wants'>('wants')
  const flying = useRef(false)
  const [styleVector, setStyleVector] = useState<number[] | null>(loadStoredStyleVector)
  const showOnboarding = styleVector === null

  useEffect(() => {
    if (!styleVector) return

    setLoading(true)
    setError(null)

    fetch(`${API}/feed`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ style_vector: styleVector }),
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
  }, [styleVector])

  useEffect(() => {
    if (!showGoldenToast) return
    const timer = setTimeout(() => setShowGoldenToast(false), 2500)
    return () => clearTimeout(timer)
  }, [showGoldenToast])

  const x = useMotionValue(0)
  const rotate = useTransform(x, [-280, 280], [-16, 16])
  const likeOpacity = useTransform(x, [20, SWIPE_X * 1.5], [0, 1])
  const skipOpacity = useTransform(x, [-SWIPE_X * 1.5, -20], [1, 0])
  const nextScale = useTransform(x, [-220, 0, 220], [0.97, 0.93, 0.97])
  const nextOpacity = useTransform(x, [-220, 0, 220], [0.8, 0.55, 0.8])
  const y = useMotionValue(0)
  const goldenOpacity = useTransform(y, [-SWIPE_Y * 1.5, -20], [1, 0])

  const current = listings[idx] as Listing | undefined
  const next = listings[idx + 1] as Listing | undefined

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

  function handleSave() {
    if (!current) return
    if (actions[current.id] === 'save') {
      const updated = { ...actions }
      delete updated[current.id]
      setActions(updated)
      localStorage.setItem(LS_ACTIONS, JSON.stringify(updated))
    } else {
      recordAction(current.id, 'save')
      postSave(current.id)
    }
  }

  function flyCard(dir: 'left' | 'right') {
    if (flying.current || !current) return
    flying.current = true
    rememberSwipe()
    if (dir === 'left') {
      recordAction(current.id, 'skip')
      animate(x, -700, { duration: 0.32, ease: [0.22, 1, 0.36, 1] })
    } else {
      recordAction(current.id, 'like')
      postFeedback(current.id, 'like')
      animate(x, 700, { duration: 0.32, ease: [0.22, 1, 0.36, 1] })
    }
    setTimeout(() => {
      setIdx((i) => i + 1)
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
    postFeedback(current.id, 'like', true)
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

  // ── Loading / error states ────────────────────────────────────────────────

  if (showOnboarding) {
    return <Onboarding onComplete={setStyleVector} />
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full bg-[#0A0A0A]">
        <span className="font-bebas text-2xl tracking-[0.3em] text-white/30 animate-pulse">
          PILO
        </span>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-full bg-[#0A0A0A] text-white gap-3 px-8 text-center">
        <p className="font-bebas text-3xl tracking-widest">NO CONNECTION</p>
        <p className="font-mono text-xs text-white/30">
          Make sure the server is running:
        </p>
        <code className="font-mono text-xs text-white/40 bg-white/5 px-4 py-2 rounded">
          python3 server.py
        </code>
        <button
          onClick={() => { setLoading(true); setError(null); window.location.reload() }}
          className="mt-4 px-6 py-2 border border-white/15 text-white/40 font-mono text-xs tracking-widest hover:bg-white/5 transition-colors"
        >
          RETRY
        </button>
      </div>
    )
  }

  if (!current) {
    const tabListings = endTab === 'wants' ? goldenListings : likedListings

    return (
      <div className="flex flex-col h-full bg-[#0A0A0A] text-white">
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
              setShowGoldenToast(false)
              localStorage.removeItem(LS_ACTIONS)
            }}
            className="font-mono text-[10px] text-white/25 tracking-widest hover:text-white/50 transition-colors"
          >
            START OVER
          </button>
        </div>

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

  const images = current.image_urls.length > 0 ? current.image_urls : [current.image_url]
  const isSaved = actions[current.id] === 'save'

  return (
    <div className="relative h-full w-full overflow-hidden bg-[#0A0A0A] touch-none select-none">
      {/* Top bar */}
      <div
        className="absolute top-0 left-0 right-0 z-50 px-5"
        style={{ paddingTop: 'max(env(safe-area-inset-top), 16px)', paddingBottom: '10px' }}
      >
        <div className="flex items-center justify-between py-3">
          <span className="font-bebas text-[26px] tracking-[0.3em] text-white">PILO</span>
          <span className="font-mono text-[10px] text-white/25 tracking-widest">
            {idx + 1} / {listings.length}
          </span>
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
          className="absolute inset-0 overflow-hidden bg-black cursor-grab active:cursor-grabbing"
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

          {/* Film grain */}
          <div className="grain absolute inset-0 pointer-events-none" />

          {/* Bottom gradient */}
          <div className="absolute inset-0 bg-gradient-to-t from-black from-0% via-black/60 via-35% to-transparent to-65%" />
          <div className="absolute inset-x-0 bottom-0 h-32 bg-gradient-to-t from-black to-transparent" />

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
                    background: i === photoIdx ? 'rgba(255,255,255,0.9)' : 'rgba(255,255,255,0.3)',
                  }}
                />
              ))}
            </div>
          )}

          {/* Bookmark button */}
          <button
            onClick={(e) => {
              e.stopPropagation()
              handleSave()
            }}
            className="absolute right-4 z-10 w-10 h-10 rounded-full flex items-center justify-center transition-colors"
            style={{
              top: 'calc(max(env(safe-area-inset-top), 16px) + 50px)',
              background: 'rgba(0,0,0,0.45)',
              backdropFilter: 'blur(8px)',
              WebkitBackdropFilter: 'blur(8px)',
              border: '1px solid rgba(255,255,255,0.12)',
              color: isSaved ? '#FBBF24' : 'rgba(255,255,255,0.75)',
            }}
            aria-label={isSaved ? 'Remove from saved' : 'Save'}
          >
            <BookmarkIcon filled={isSaved} />
          </button>

          {/* Swipe stamps */}
          <motion.div
            className="absolute top-[130px] left-5 border-[3px] border-[#4ADE80] text-[#4ADE80] px-4 py-1 font-bebas text-[28px] tracking-widest -rotate-12"
            style={{ opacity: likeOpacity }}
            aria-hidden
          >
            LIKE
          </motion.div>

          <motion.div
            className="absolute top-[130px] right-5 border-[3px] border-[#F87171] text-[#F87171] px-4 py-1 font-bebas text-[28px] tracking-widest rotate-12"
            style={{ opacity: skipOpacity }}
            aria-hidden
          >
            SKIP
          </motion.div>

          <motion.div
            className="absolute top-[130px] left-1/2 -translate-x-1/2 border-[3px] border-[#F0C050] text-[#F0C050] px-4 py-1 font-bebas text-[28px] tracking-widest -rotate-6 whitespace-nowrap"
            style={{ opacity: goldenOpacity }}
            aria-hidden
          >
            ⭐ WANT
          </motion.div>

          {/* Vinted link */}
          <a
            href={current.url}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            className="absolute right-4 z-10 flex items-center gap-[5px] px-3 py-1.5 rounded-full font-mono text-[10px] text-white/50 tracking-wider transition-colors hover:text-white/80"
            style={{
              bottom: 'calc(max(env(safe-area-inset-bottom), 0px) + 96px)',
              background: 'rgba(0,0,0,0.4)',
              backdropFilter: 'blur(8px)',
              WebkitBackdropFilter: 'blur(8px)',
              border: '1px solid rgba(255,255,255,0.10)',
            }}
            aria-label="View on Vinted"
          >
            <ExternalLinkIcon />
            vinted
          </a>

          {/* Listing info */}
          <div
            className="absolute bottom-0 left-0 right-0 px-6"
            style={{ paddingBottom: `calc(max(env(safe-area-inset-bottom), 0px) + 88px)` }}
          >
            <div className="flex items-center gap-2 mb-3">
              <div className="w-[5px] h-[5px] rounded-full bg-emerald-400 shrink-0" />
              <span className="font-mono text-[10px] text-white/50 tracking-widest uppercase">
                {Math.round(current.final_score * 100)}% match
              </span>
            </div>

            <h2 className="font-cormorant text-[54px] italic text-white leading-[0.9] mb-3 drop-shadow-[0_3px_12px_rgba(0,0,0,0.9)]">
              {current.brand}
            </h2>

            <div className="flex items-baseline gap-4 mb-2">
              <span className="font-mono text-[30px] text-[#F0C050] font-bold leading-none">
                €{current.price % 1 === 0 ? current.price.toFixed(0) : current.price.toFixed(2)}
              </span>
              <span className="font-mono text-[11px] text-white/25 tracking-wider">
                ♥ {current.favourites}
              </span>
            </div>

            <p className="font-mono text-[11px] text-white/35 leading-relaxed line-clamp-2 max-w-[88%]">
              {current.title}
            </p>
          </div>
        </motion.div>
      </div>

      {/* Action buttons */}
      <div
        className="absolute bottom-0 left-0 right-0 z-50 flex justify-center gap-6 px-8"
        style={{
          paddingBottom: `max(env(safe-area-inset-bottom), 20px)`,
          paddingTop: '14px',
        }}
      >
        <motion.button
          whileTap={{ scale: 0.85 }}
          onClick={() => flyCard('left')}
          className="w-[54px] h-[54px] rounded-full flex items-center justify-center text-[#F87171] text-lg"
          style={{ background: 'rgba(248, 113, 113, 0.12)', border: '1px solid rgba(248,113,113,0.25)' }}
          aria-label="Skip"
        >
          ✕
        </motion.button>

        <motion.button
          whileTap={swipeHistory.length > 0 ? { scale: 0.85 } : undefined}
          onClick={handleUndoSwipe}
          disabled={swipeHistory.length === 0}
          className={`w-[48px] h-[48px] self-center rounded-full flex items-center justify-center text-lg transition-colors ${
            swipeHistory.length > 0 ? 'text-white/65' : 'text-white/15'
          }`}
          style={{
            background: swipeHistory.length > 0 ? 'rgba(255,255,255,0.08)' : 'rgba(255,255,255,0.03)',
            border: '1px solid rgba(255,255,255,0.12)',
          }}
          aria-label="Undo last swipe"
        >
          ↶
        </motion.button>

        <motion.button
          whileTap={{ scale: 0.85 }}
          onClick={() => flyCard('right')}
          className="w-[54px] h-[54px] rounded-full flex items-center justify-center text-[#4ADE80] text-xl"
          style={{ background: 'rgba(74, 222, 128, 0.12)', border: '1px solid rgba(74,222,128,0.25)' }}
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
    </div>
  )
}
