import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { API, LS_STYLE_VECTOR, LS_GENDER, LS_SIZE } from './config'

type Gender = 'men' | 'women'

const MEN_SIZES = ['XS', 'S', 'M', 'L', 'XL', 'XXL']
const WOMEN_SIZES = ['34', '36', '38', '40', '42']

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

const GENDER_HEROES: Record<Gender, string> = {
  men: 'men/Minimal/Street fashion men.jpeg',
  women: 'women/Minimal/khaled-ali-1-Sk6l2lCWY-unsplash.jpg',
}

function toSrc(key: string): string {
  return '/onboarding/' + key.split('/').map(encodeURIComponent).join('/')
}

const slideVariants = {
  enter: { x: '100%', opacity: 0 },
  center: { x: 0, opacity: 1 },
  exit: { x: '-100%', opacity: 0 },
}

const slideTransition = { duration: 0.28, ease: [0.22, 1, 0.36, 1] as const }

function GenderScreen({ onSelect }: { onSelect: (gender: Gender) => void }) {
  return (
    <div className="flex h-full w-full">
      {(['men', 'women'] as Gender[]).map((gender) => (
        <button
          key={gender}
          onClick={() => onSelect(gender)}
          className="relative flex-1 overflow-hidden focus:outline-none"
        >
          <img
            src={toSrc(GENDER_HEROES[gender])}
            className="absolute inset-0 h-full w-full object-cover"
            alt={gender}
          />
          <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-black/20 to-transparent" />
          <span className="absolute bottom-10 left-0 right-0 text-center font-bebas text-4xl tracking-[0.2em] text-white drop-shadow">
            {gender === 'men' ? 'MÄNNER' : 'FRAUEN'}
          </span>
        </button>
      ))}
      <div className="pointer-events-none absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-white/10" />
    </div>
  )
}

function SizeScreen({
  gender,
  onSelect,
}: {
  gender: Gender
  onSelect: (size: string) => void
}) {
  const sizes = gender === 'men' ? MEN_SIZES : WOMEN_SIZES
  const [pending, setPending] = useState<string | null>(null)

  function handleTap(size: string) {
    setPending(size)
    window.setTimeout(() => onSelect(size), 160)
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

      <div className="grid flex-1 grid-cols-3 content-start overflow-y-auto">
        {images.map((key) => {
          const isSelected = selected.has(key)

          return (
            <motion.button
              key={key}
              whileTap={{ scale: 0.95 }}
              onClick={() => toggle(key)}
              className="relative aspect-square overflow-hidden focus:outline-none"
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
          {selected.size} / {images.length} AUSGEWÄHLT
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

export function Onboarding({ onComplete }: { onComplete: (vector: number[]) => void }) {
  const [step, setStep] = useState(0)
  const [gender, setGender] = useState<Gender>('men')
  const [size, setSize] = useState('')

  function handleGender(nextGender: Gender) {
    setGender(nextGender)
    setStep(1)
  }

  function handleSize(nextSize: string) {
    setSize(nextSize)
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
    localStorage.setItem(LS_STYLE_VECTOR, JSON.stringify(data.style_vector))
    localStorage.setItem(LS_GENDER, gender)
    localStorage.setItem(LS_SIZE, size)
    onComplete(data.style_vector)
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
