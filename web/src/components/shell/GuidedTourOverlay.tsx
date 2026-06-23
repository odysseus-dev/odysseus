import { useEffect, useMemo, useState } from "react"
import type { CSSProperties } from "react"
import { useLocation } from "react-router-dom"
import { ChevronLeft, ChevronRight, X } from "lucide-react"

interface TourStep {
  text: string
  selector?: string
}

interface TourGuide {
  title: string
  route?: string
  steps: Array<TourStep | string>
  closing?: string
}

interface ViewRect {
  top: number
  left: number
  width: number
  height: number
  right: number
  bottom: number
}

const PADDING = 7
const TIP_WIDTH = 320
const TIP_HEIGHT = 188

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max)
}

function rectFromElement(el: Element): ViewRect | null {
  const rect = el.getBoundingClientRect()
  if (rect.width < 1 || rect.height < 1) return null
  return {
    top: rect.top,
    left: rect.left,
    width: rect.width,
    height: rect.height,
    right: rect.right,
    bottom: rect.bottom,
  }
}

function normalizeGuide(guide: TourGuide): TourGuide {
  return {
    ...guide,
    steps: guide.steps.map((step) => typeof step === "string" ? { text: step } : step),
  }
}

function tooltipStyle(rect: ViewRect | null): CSSProperties {
  const viewportWidth = window.innerWidth
  const viewportHeight = window.innerHeight
  if (!rect) {
    return {
      left: clamp((viewportWidth - TIP_WIDTH) / 2, 16, Math.max(16, viewportWidth - TIP_WIDTH - 16)),
      top: clamp(96, 16, Math.max(16, viewportHeight - TIP_HEIGHT - 16)),
      width: Math.min(TIP_WIDTH, viewportWidth - 32),
    }
  }

  const left = clamp(rect.left + rect.width / 2 - TIP_WIDTH / 2, 16, Math.max(16, viewportWidth - TIP_WIDTH - 16))
  let top = rect.bottom + 12
  if (top + TIP_HEIGHT > viewportHeight && rect.top > TIP_HEIGHT + 24) {
    top = rect.top - TIP_HEIGHT - 12
  } else {
    top = clamp(top, 16, Math.max(16, viewportHeight - TIP_HEIGHT - 16))
  }
  return { left, top, width: Math.min(TIP_WIDTH, viewportWidth - 32) }
}

function haloStyle(rect: ViewRect): CSSProperties {
  return {
    top: Math.max(8, rect.top - PADDING),
    left: Math.max(8, rect.left - PADDING),
    width: Math.min(window.innerWidth - 16, rect.width + PADDING * 2),
    height: Math.min(window.innerHeight - 16, rect.height + PADDING * 2),
    boxShadow: "0 0 0 9999px rgb(2 6 23 / 0.18), 0 0 0 4px hsl(var(--ring) / 0.22)",
  }
}

export function GuidedTourOverlay() {
  const location = useLocation()
  const routeKey = `${location.pathname}${location.search}`
  const [guide, setGuide] = useState<TourGuide | null>(null)
  const [index, setIndex] = useState(0)
  const [rect, setRect] = useState<ViewRect | null>(null)
  const [missing, setMissing] = useState(false)

  const steps = useMemo(() => (guide?.steps || []) as TourStep[], [guide])
  const step = steps[index]
  const total = steps.length

  useEffect(() => {
    const start = (event: Event) => {
      const detail = (event as CustomEvent<TourGuide>).detail
      if (!detail?.title || !detail.steps?.length) return
      setGuide(normalizeGuide(detail))
      setIndex(0)
      setMissing(false)
    }
    window.addEventListener("odysseus:start-tour", start)
    return () => window.removeEventListener("odysseus:start-tour", start)
  }, [])

  useEffect(() => {
    if (!guide) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setGuide(null)
      if (event.key === "ArrowRight") setIndex((current) => Math.min(current + 1, total - 1))
      if (event.key === "ArrowLeft") setIndex((current) => Math.max(current - 1, 0))
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [guide, total])

  useEffect(() => {
    if (!guide || !step) return
    let cancelled = false
    let timer: number | undefined
    let frame: number | undefined

    const locate = (attempt = 0) => {
      if (cancelled) return
      if (!step.selector) {
        setRect(null)
        setMissing(false)
        return
      }
      const el = document.querySelector(step.selector)
      const nextRect = el ? rectFromElement(el) : null
      if (nextRect) {
        if (attempt === 0) el?.scrollIntoView({ block: "center", inline: "nearest", behavior: "smooth" })
        frame = window.requestAnimationFrame(() => {
          if (cancelled) return
          setRect(el ? rectFromElement(el) : nextRect)
          setMissing(false)
        })
        return
      }
      if (attempt < 25) {
        timer = window.setTimeout(() => locate(attempt + 1), 80)
      } else {
        setRect(null)
        setMissing(true)
      }
    }

    const update = () => {
      if (!step.selector) return
      const el = document.querySelector(step.selector)
      setRect(el ? rectFromElement(el) : null)
    }

    locate()
    window.addEventListener("resize", update)
    window.addEventListener("scroll", update, true)
    return () => {
      cancelled = true
      if (timer) window.clearTimeout(timer)
      if (frame) window.cancelAnimationFrame(frame)
      window.removeEventListener("resize", update)
      window.removeEventListener("scroll", update, true)
    }
  }, [guide, index, routeKey, step])

  if (!guide || !step) return null

  const close = () => setGuide(null)
  const next = () => {
    if (index >= total - 1) close()
    else setIndex((current) => current + 1)
  }

  return (
    <div className="pointer-events-none fixed inset-0 z-[80]" aria-live="polite">
      {rect && (
        <div
          className="absolute rounded-lg border-2 border-ring bg-transparent transition-all duration-150"
          style={haloStyle(rect)}
        />
      )}
      <section
        role="dialog"
        aria-label={guide.title}
        className="pointer-events-auto absolute rounded-lg border bg-popover p-3 text-popover-foreground shadow-xl"
        style={tooltipStyle(rect)}
      >
        <div className="flex items-start gap-3">
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-semibold">{guide.title}</div>
            <div className="mt-0.5 text-xs text-muted-foreground">Step {index + 1} of {total}</div>
          </div>
          <button
            type="button"
            onClick={close}
            className="inline-flex size-7 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground"
            aria-label="Skip tour"
            title="Skip tour"
          >
            <X className="size-3.5" />
          </button>
        </div>
        <p className="mt-3 text-sm leading-5 text-foreground">{step.text}</p>
        {missing && <p className="mt-2 text-xs text-muted-foreground">This tour target is not visible yet, but the step is still available here.</p>}
        {index === total - 1 && guide.closing && <p className="mt-2 text-xs text-muted-foreground">{guide.closing}</p>}
        <div className="mt-3 flex items-center justify-between gap-2">
          <button
            type="button"
            onClick={() => setIndex((current) => Math.max(current - 1, 0))}
            disabled={index === 0}
            className="inline-flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-xs font-medium text-muted-foreground hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
          >
            <ChevronLeft className="size-3.5" />Back
          </button>
          <button
            type="button"
            onClick={next}
            className="inline-flex h-8 items-center gap-1.5 rounded-md bg-primary px-2.5 text-xs font-medium text-primary-foreground hover:bg-primary/90"
          >
            {index === total - 1 ? "Done" : "Next"}{index < total - 1 && <ChevronRight className="size-3.5" />}
          </button>
        </div>
      </section>
    </div>
  )
}
