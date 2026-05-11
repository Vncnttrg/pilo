import { useState, useRef, useEffect } from 'react'
import {
  motion,
  AnimatePresence,
  useMotionValue,
  useTransform,
  animate,
  type PanInfo,
} from 'framer-motion'
import type { Listing } from './types'

const API = 'http://localhost:5001'

const SWIPE_X = 90
const FLICK_V = 280

type Action = 'like' | 'skip' | 'save'
type Actions = Record<number, Action>

function loadActions(): Actions {
  try {
    return JSON.parse(localStorage.getItem('pilo-actions') || '{}')
  } catch {
    return {}
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

function postFeedback(id: number, direction: 'like' | 'skip') {
  fetch(`${API}/feedback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id, direction }),
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
  const flying = useRef(false)

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

  const x = useMotionValue(0)
  const rotate = useTransform(x, [-280, 280], [-16, 16])
  const likeOpacity = useTransform(x, [20, SWIPE_X * 1.5], [0, 1])
  const skipOpacity = useTransform(x, [-SWIPE_X * 1.5, -20], [1, 0])
  const nextScale = useTransform(x, [-220, 0, 220], [0.97, 0.93, 0.97])
  const nextOpacity = useTransform(x, [-220, 0, 220], [0.8, 0.55, 0.8])

  const current = listings[idx] as Listing | undefined
  const next = listings[idx + 1] as Listing | undefined

  function recordAction(id: number, action: Action) {
    const updated = { ...actions, [id]: action }
    setActions(updated)
    localStorage.setItem('pilo-actions', JSON.stringify(updated))
  }

  function handleSave() {
    if (!current) return
    if (actions[current.id] === 'save') {
      const updated = { ...actions }
      delete updated[current.id]
      setActions(updated)
      localStorage.setItem('pilo-actions', JSON.stringify(updated))
    } else {
      recordAction(current.id, 'save')
      postSave(current.id)
    }
  }

  function flyCard(dir: 'left' | 'right') {
    if (flying.current || !current) return
    flying.current = true

    const direction = dir === 'right' ? 'like' : 'skip'
    recordAction(current.id, dir === 'right' ? 'like' : 'skip')
    postFeedback(current.id, direction)

    animate(x, dir === 'right' ? 700 : -700, { duration: 0.32, ease: [0.22, 1, 0.36, 1] })

    setTimeout(() => {
      setIdx((i) => i + 1)
      setPhotoIdx(0)
      x.set(0)
      flying.current = false
    }, 360)
  }

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

  function handleCardTap(event: MouseEvent | TouchEvent | PointerEvent) {
    if (flying.current || !current) return
    if ((event.target as HTMLElement).closest('a, button')) return
    const images = current.image_urls.length > 0 ? current.image_urls : [current.image_url]
    setPhotoIdx((i) => (i + 1) % images.length)
  }

  const liked = Object.values(actions).filter((a) => a === 'like').length
  const saved = Object.values(actions).filter((a) => a === 'save').length

  // ── Loading / error states ────────────────────────────────────────────────

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
            localStorage.removeItem('pilo-actions')
          }}
          className="px-8 py-3 border border-white/15 text-white/40 font-mono text-xs tracking-widest hover:bg-white/5 transition-colors"
        >
          START OVER
        </button>
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
          className="absolute inset-0 overflow-hidden cursor-grab active:cursor-grabbing"
          style={{ x, rotate }}
          drag="x"
          dragConstraints={{ left: 0, right: 0 }}
          dragElastic={0.82}
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
        className="absolute bottom-0 left-0 right-0 z-50 flex justify-center gap-8 px-8"
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
          whileTap={{ scale: 0.85 }}
          onClick={() => flyCard('right')}
          className="w-[54px] h-[54px] rounded-full flex items-center justify-center text-[#4ADE80] text-xl"
          style={{ background: 'rgba(74, 222, 128, 0.12)', border: '1px solid rgba(74,222,128,0.25)' }}
          aria-label="Like"
        >
          ♥
        </motion.button>
      </div>
    </div>
  )
}
