import {
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
  type RefObject,
  type ReactNode,
  type TouchEvent as ReactTouchEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import { useNavigate } from "react-router-dom"
import {
  Archive,
  Bell,
  Bot,
  Brush,
  CalendarDays,
  Check,
  Circle,
  Clipboard,
  Eraser,
  ExternalLink,
  Image,
  ImagePlus,
  ListChecks,
  Palette,
  Pencil,
  Pin,
  Search,
  Sparkles,
  StickyNote,
  Target,
  Trash2,
  Type,
  Undo2,
  X,
} from "lucide-react"
import { uploadNoteImage, useNoteMutations, useNotes, type NotePayload } from "@/api/notes"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { hasActiveNoteReminder, hasReminderTime, useNoteReminders } from "@/stores/noteReminders"
import { toast } from "@/stores/toast"
import type { Note, NoteItem } from "@/types"

const input = "h-9 w-full rounded-md border bg-background px-3 text-sm outline-none focus-visible:border-ring"
const area = "w-full resize-none rounded-md border bg-background px-3 py-2 text-sm outline-none focus-visible:border-ring"
const EMPTY_NOTES: Note[] = []

type NoteFormType = "note" | "todo" | "draw" | "goal"
type NoteFilter = "all" | "default" | "reminders" | "no-reminders" | "goals" | "today"
type DrawTool = "pen" | "eraser" | "text" | "line" | "circle"
type DrawSize = "s" | "m" | "l"

const NOTE_COLORS = [
  { label: "None", value: "", bg: "transparent" },
  { label: "Red", value: "red", bg: "#fee2e2" },
  { label: "Orange", value: "orange", bg: "#ffedd5" },
  { label: "Yellow", value: "yellow", bg: "#fef9c3" },
  { label: "Green", value: "green", bg: "#dcfce7" },
  { label: "Blue", value: "blue", bg: "#dbeafe" },
  { label: "Purple", value: "purple", bg: "#ede9fe" },
] as const

const REPEATS = [
  { label: "Doesn't repeat", value: "none" },
  { label: "Daily", value: "daily" },
  { label: "Weekly", value: "weekly" },
  { label: "Monthly", value: "monthly" },
  { label: "Yearly", value: "yearly" },
]

const DRAW_SIZE_SEQUENCE: DrawSize[] = ["s", "m", "l"]
const DRAW_TEXT_SIZES: Record<DrawSize, number> = { s: 16, m: 26, l: 40 }
const DRAW_SHAPE_WIDTHS: Record<DrawSize, number> = { s: 2, m: 5, l: 10 }
const NOTE_DRAFT_PREFIX = "odysseus-note-draft-"
const NOTES_FIRST_OPEN_HINT_KEY = "odysseus-notes-first-open-hint-v1"

interface FormState {
  title: string
  content: string
  noteType: NoteFormType
  itemsText: string
  color: string
  label: string
  dueDate: string
  repeat: string
  imageUrl: string
}

interface TextDraft {
  id: number
  value: string
  x: number
  y: number
  left: number
  top: number
  fontCss: number
  fontCanvas: number
  maxWidth: number
  color: string
}

interface SavedNoteDraft {
  _ts?: number
  note_type?: string
  title?: string
  content?: string
  items?: NoteItem[] | null
  color?: string
  label?: string
  due_date?: string | null
  repeat?: string
  image_url?: string
}

function noteItems(note: Note): NoteItem[] {
  return Array.isArray(note.items) ? note.items : []
}

function itemDone(item: NoteItem): boolean {
  return !!(item.done || item.checked)
}

function isChecklistType(type?: string): boolean {
  return type === "todo" || type === "goal" || type === "checklist"
}

function noteFormType(note?: Note): NoteFormType {
  if (note?.note_type === "draw") return "draw"
  if (note?.note_type === "goal") return "goal"
  if (note?.note_type === "todo" || note?.note_type === "checklist") return "todo"
  return "note"
}

function itemsFromText(value: string, previous: NoteItem[] = []): NoteItem[] {
  return value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((text, index) => {
      const old = previous[index]
      const same = old && (old.text || "").trim() === text
      return {
        text,
        done: same ? itemDone(old) : false,
        indent: same ? old.indent || 0 : 0,
        id: same ? old.id : undefined,
      }
    })
}

function itemsToText(items: NoteItem[] | null | undefined): string {
  return Array.isArray(items) ? items.map((item) => item.text || "").filter(Boolean).join("\n") : ""
}

function draftKey(id?: string): string {
  return `${NOTE_DRAFT_PREFIX}${id || "__new__"}`
}

function isFormDraftEmpty(form: FormState): boolean {
  if (form.title.trim()) return false
  if (form.content.trim()) return false
  if (form.itemsText.split("\n").some((item) => item.trim())) return false
  if (form.imageUrl.trim()) return false
  return true
}

function sameFormDraft(a: FormState, b: FormState): boolean {
  return (
    a.title === b.title &&
    a.content === b.content &&
    a.noteType === b.noteType &&
    a.itemsText === b.itemsText &&
    a.color === b.color &&
    a.label === b.label &&
    a.dueDate === b.dueDate &&
    a.repeat === b.repeat &&
    a.imageUrl === b.imageUrl
  )
}

function savedDraftFromForm(form: FormState): SavedNoteDraft {
  const isChecklist = form.noteType === "todo" || form.noteType === "goal"
  return {
    _ts: Date.now(),
    note_type: form.noteType,
    title: form.title,
    label: form.label,
    due_date: form.dueDate || null,
    repeat: form.repeat || "none",
    color: form.color,
    image_url: form.imageUrl,
    content: form.noteType === "note" || form.noteType === "goal" ? form.content : "",
    items: isChecklist ? itemsFromText(form.itemsText) : [],
  }
}

function formFromDraft(base: FormState, draft: SavedNoteDraft | null): FormState {
  if (!draft) return base
  const has = (key: keyof SavedNoteDraft) => Object.prototype.hasOwnProperty.call(draft, key)
  const noteType = noteFormType({ note_type: draft.note_type } as Note)
  const items = Array.isArray(draft.items) ? draft.items : []
  return {
    ...base,
    title: typeof draft.title === "string" ? draft.title : base.title,
    content: typeof draft.content === "string" ? draft.content : base.content,
    noteType,
    itemsText: has("items") ? itemsToText(items) : base.itemsText,
    color: has("color") && typeof draft.color === "string" ? draft.color : base.color,
    label: typeof draft.label === "string" ? draft.label : base.label,
    dueDate: has("due_date") ? (typeof draft.due_date === "string" && draft.due_date ? isoToLocal(draft.due_date) : "") : base.dueDate,
    repeat: typeof draft.repeat === "string" ? draft.repeat : base.repeat,
    imageUrl: has("image_url") && typeof draft.image_url === "string" ? draft.image_url : base.imageUrl,
  }
}

function loadNoteDraft(id: string | undefined, base: FormState): { form: FormState; restored: boolean } {
  if (typeof localStorage === "undefined") return { form: base, restored: false }
  try {
    const raw = localStorage.getItem(draftKey(id))
    if (!raw) return { form: base, restored: false }
    const draft = JSON.parse(raw) as SavedNoteDraft | null
    const form = formFromDraft(base, draft)
    return isFormDraftEmpty(form) ? { form: base, restored: false } : { form, restored: true }
  } catch {
    return { form: base, restored: false }
  }
}

function saveNoteDraft(id: string | undefined, form: FormState): void {
  if (typeof localStorage === "undefined") return
  try {
    const key = draftKey(id)
    if (isFormDraftEmpty(form)) {
      localStorage.removeItem(key)
      return
    }
    localStorage.setItem(key, JSON.stringify(savedDraftFromForm(form)))
  } catch {
    /* localStorage may be unavailable or full */
  }
}

function clearNoteDraft(id: string | undefined): void {
  if (typeof localStorage === "undefined") return
  try {
    localStorage.removeItem(draftKey(id))
  } catch {
    /* ignore */
  }
}

function newNoteItem(text: string): NoteItem {
  const randomId = typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `item-${Date.now()}-${Math.random().toString(36).slice(2)}`
  return { id: randomId, text, done: false }
}

function formFromNote(note?: Note): FormState {
  return {
    title: note?.title || "",
    content: note?.content || "",
    noteType: noteFormType(note),
    itemsText: itemsToText(noteItems(note || ({} as Note))),
    color: note?.color || "",
    label: note?.label || "",
    dueDate: note?.due_date ? isoToLocal(note.due_date) : "",
    repeat: note?.repeat || "none",
    imageUrl: note?.image_url || "",
  }
}

function payloadFromForm(form: FormState, previousItems: NoteItem[] = []): NotePayload {
  const isChecklist = form.noteType === "todo" || form.noteType === "goal"
  return {
    title: form.title.trim(),
    content: form.noteType === "note" || form.noteType === "goal" ? form.content : "",
    items: isChecklist ? itemsFromText(form.itemsText, previousItems) : [],
    note_type: form.noteType,
    color: form.color || undefined,
    label: normalizeLabel(form.label),
    due_date: form.dueDate || undefined,
    repeat: form.dueDate ? form.repeat || "none" : "none",
    image_url: form.imageUrl || undefined,
  }
}

function normalizeLabel(label: string): string | undefined {
  const cleaned = label
    .split(/\s+/)
    .map((part) => part.trim().replace(/^#/, ""))
    .filter(Boolean)
    .join(" ")
  return cleaned || undefined
}

function noteLabels(note: Note): string[] {
  return (note.label || "").split(/\s+/).map((part) => part.trim().replace(/^#/, "")).filter(Boolean)
}

function serializeNote(note: Note): string {
  const lines: string[] = []
  if (note.title) lines.push(note.title)
  if (note.content) lines.push(note.content)
  const items = noteItems(note)
  if (items.length) {
    if (lines.length) lines.push("")
    for (const item of items) {
      const text = (item.text || "").trim()
      if (text) lines.push(`- [${itemDone(item) ? "x" : " "}] ${text}`)
    }
  }
  return lines.join("\n").trim()
}

function noteMatches(note: Note, q: string): boolean {
  if (!q) return true
  const items = noteItems(note).map((item) => item.text || "").join(" ")
  const haystack = [note.title, note.content, note.label, items].join(" ").toLowerCase()
  return haystack.includes(q)
}

function isoToLocal(value: string): string {
  if (!value) return ""
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value)) return value
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value.slice(0, 16)
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function dueLabel(value?: string): string {
  if (!value) return ""
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  return d.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })
}

function isOverdue(value?: string): boolean {
  if (!value) return false
  const d = new Date(value)
  return !Number.isNaN(d.getTime()) && d.getTime() < Date.now()
}

function isPastReminder(note: Note): boolean {
  return !!note.due_date && hasReminderTime(note.due_date) && isOverdue(note.due_date)
}

function noteTime(value?: string | null): number {
  const time = new Date(value || 0).getTime()
  return Number.isFinite(time) ? time : 0
}

function sortNotesForReminderPriority(notes: Note[]): Note[] {
  return [...notes].sort((a, b) => {
    if (!!a.pinned !== !!b.pinned) return a.pinned ? -1 : 1
    const aActive = hasActiveNoteReminder(a)
    const bActive = hasActiveNoteReminder(b)
    if (aActive !== bActive) return aActive ? -1 : 1
    const sortDelta = (a.sort_order || 0) - (b.sort_order || 0)
    if (sortDelta !== 0) return sortDelta
    return noteTime(b.updated_at) - noteTime(a.updated_at)
  })
}

function isMobileNotesMode(): boolean {
  if (typeof window === "undefined") return false
  const coarse = window.matchMedia?.("(pointer: coarse)").matches ?? true
  return coarse && window.innerWidth <= 768
}

function isInteractiveTarget(target: EventTarget | null): boolean {
  return !!(target instanceof HTMLElement && target.closest("button,input,a,label,textarea,select,[role='button']"))
}

function colorClasses(color?: string): string {
  switch (color) {
    case "red": return "border-red-200 bg-red-50 dark:border-red-950 dark:bg-red-950/25"
    case "orange": return "border-orange-200 bg-orange-50 dark:border-orange-950 dark:bg-orange-950/25"
    case "yellow": return "border-yellow-200 bg-yellow-50 dark:border-yellow-950 dark:bg-yellow-950/20"
    case "green": return "border-green-200 bg-green-50 dark:border-green-950 dark:bg-green-950/25"
    case "blue": return "border-blue-200 bg-blue-50 dark:border-blue-950 dark:bg-blue-950/25"
    case "purple": return "border-purple-200 bg-purple-50 dark:border-purple-950 dark:bg-purple-950/25"
    default: return "bg-card"
  }
}

function bgImageUrl(color?: string): string {
  return color?.startsWith("bg:") ? color.slice(3) : ""
}

function backgroundStyle(color?: string): CSSProperties | undefined {
  const url = bgImageUrl(color)
  if (!url) return undefined
  const escaped = url.replace(/"/g, "%22")
  return {
    backgroundImage: `linear-gradient(rgba(0,0,0,.34), rgba(0,0,0,.34)), url("${escaped}")`,
    backgroundPosition: "center",
    backgroundSize: "cover",
  }
}

function goalProgress(note: Note): string {
  const items = noteItems(note)
  if (!items.length) return ""
  const done = items.filter(itemDone).length
  return ` ${done}/${items.length}`
}

function nextGoalStep(note: Note): { item: NoteItem; index: number } | null {
  const items = noteItems(note)
  const index = items.findIndex((item) => !itemDone(item))
  return index >= 0 ? { item: items[index], index } : null
}

function canvasBlob(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) resolve(blob)
      else reject(new Error("Drawing export failed"))
    }, "image/png")
  })
}

function clearCanvas(canvas: HTMLCanvasElement) {
  const ctx = canvas.getContext("2d")
  if (!ctx) return
  ctx.save()
  ctx.setTransform(1, 0, 0, 1, 0, 0)
  ctx.globalCompositeOperation = "source-over"
  ctx.fillStyle = "#ffffff"
  ctx.fillRect(0, 0, canvas.width, canvas.height)
  ctx.restore()
}

function DrawingPad({ canvasRef, initialImageUrl }: { canvasRef: RefObject<HTMLCanvasElement | null>; initialImageUrl?: string }) {
  const [tool, setTool] = useState<DrawTool>("pen")
  const [stroke, setStroke] = useState("#222222")
  const [size, setSize] = useState(4)
  const [toolSize, setToolSize] = useState<DrawSize>("s")
  const [textDraft, setTextDraft] = useState<TextDraft | null>(null)
  const drawingRef = useRef(false)
  const lastRef = useRef<{ x: number; y: number } | null>(null)
  const shapeStartRef = useRef<{ x: number; y: number } | null>(null)
  const shapeSnapshotRef = useRef<ImageData | null>(null)
  const undoRef = useRef<ImageData[]>([])
  const textDraftRef = useRef<TextDraft | null>(null)
  const textInputRef = useRef<HTMLInputElement>(null)
  const textDraftId = textDraft?.id

  useEffect(() => {
    textDraftRef.current = textDraft
  }, [textDraft])

  useEffect(() => {
    if (textDraftId == null) return
    requestAnimationFrame(() => {
      textInputRef.current?.focus()
      textInputRef.current?.select()
    })
  }, [textDraftId])

  useEffect(() => {
    const canvas = canvasRef.current
    const ctx = canvas?.getContext("2d")
    if (!canvas || !ctx) return
    clearCanvas(canvas)
    if (!initialImageUrl) return
    const img = new window.Image()
    img.crossOrigin = "anonymous"
    img.onload = () => {
      try {
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
      } catch {
        clearCanvas(canvas)
      }
    }
    img.src = initialImageUrl
  }, [canvasRef, initialImageUrl])

  const snapshot = () => {
    const canvas = canvasRef.current
    const ctx = canvas?.getContext("2d")
    if (!canvas || !ctx) return
    try {
      undoRef.current.push(ctx.getImageData(0, 0, canvas.width, canvas.height))
      if (undoRef.current.length > 30) undoRef.current.shift()
    } catch {
      /* canvas may be tainted by an older remote image */
    }
  }

  const point = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    const canvas = event.currentTarget
    const rect = canvas.getBoundingClientRect()
    return {
      x: ((event.clientX - rect.left) * canvas.width) / rect.width,
      y: ((event.clientY - rect.top) * canvas.height) / rect.height,
    }
  }

  const cssPoint = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    const rect = event.currentTarget.getBoundingClientRect()
    return {
      x: event.clientX - rect.left,
      y: event.clientY - rect.top,
      rect,
    }
  }

  const commitTextDraft = (draft = textDraftRef.current) => {
    if (!draft) return
    textDraftRef.current = null
    setTextDraft(null)
    const text = draft.value.trim()
    if (!text) return
    const canvas = canvasRef.current
    const ctx = canvas?.getContext("2d")
    if (!canvas || !ctx) return
    snapshot()
    ctx.save()
    ctx.globalCompositeOperation = "source-over"
    ctx.fillStyle = draft.color
    ctx.font = `${draft.fontCanvas}px sans-serif`
    ctx.textBaseline = "top"
    ctx.fillText(text, draft.x, draft.y - draft.fontCanvas * 0.7)
    ctx.restore()
  }

  const openTextInput = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    commitTextDraft()
    const canvas = event.currentTarget
    const p = point(event)
    const { x, y, rect } = cssPoint(event)
    const fontCss = DRAW_TEXT_SIZES[toolSize]
    const fontCanvas = fontCss * (canvas.width / rect.width)
    const top = Math.max(0, y - fontCss * 0.7)
    const left = Math.max(0, x)
    const maxWidth = Math.max(120, rect.width - left - 4)
    setTextDraft({
      id: Date.now(),
      value: "",
      x: p.x,
      y: p.y,
      left,
      top,
      fontCss,
      fontCanvas,
      maxWidth,
      color: stroke,
    })
  }

  const drawShape = (to: { x: number; y: number }) => {
    const canvas = canvasRef.current
    const ctx = canvas?.getContext("2d")
    const from = shapeStartRef.current
    if (!canvas || !ctx || !from) return
    if (shapeSnapshotRef.current) ctx.putImageData(shapeSnapshotRef.current, 0, 0)
    ctx.save()
    ctx.globalCompositeOperation = "source-over"
    ctx.strokeStyle = stroke
    ctx.lineWidth = DRAW_SHAPE_WIDTHS[toolSize]
    ctx.lineCap = "round"
    ctx.lineJoin = "round"
    ctx.beginPath()
    if (tool === "line") {
      ctx.moveTo(from.x, from.y)
      ctx.lineTo(to.x, to.y)
    } else {
      ctx.arc(from.x, from.y, Math.hypot(to.x - from.x, to.y - from.y), 0, Math.PI * 2)
    }
    ctx.stroke()
    ctx.restore()
  }

  const begin = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    const canvas = event.currentTarget
    const ctx = canvas.getContext("2d")
    if (!ctx) return
    event.preventDefault()
    if (tool === "text") {
      event.stopPropagation()
      openTextInput(event)
      return
    }
    canvas.setPointerCapture(event.pointerId)
    const p = point(event)
    snapshot()
    drawingRef.current = true
    lastRef.current = p
    if (tool === "line" || tool === "circle") {
      shapeStartRef.current = p
      try {
        shapeSnapshotRef.current = ctx.getImageData(0, 0, canvas.width, canvas.height)
      } catch {
        shapeSnapshotRef.current = null
      }
      return
    }
    ctx.beginPath()
    ctx.moveTo(p.x, p.y)
  }

  const move = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    if (!drawingRef.current) return
    const canvas = event.currentTarget
    const ctx = canvas.getContext("2d")
    const last = lastRef.current
    if (!ctx || !last) return
    event.preventDefault()
    const p = point(event)
    if (tool === "line" || tool === "circle") {
      drawShape(p)
      return
    }
    ctx.save()
    ctx.globalCompositeOperation = "source-over"
    ctx.strokeStyle = tool === "eraser" ? "#ffffff" : stroke
    ctx.lineWidth = size * (tool === "eraser" ? 5 : 2)
    ctx.lineCap = "round"
    ctx.lineJoin = "round"
    ctx.lineTo(p.x, p.y)
    ctx.stroke()
    ctx.restore()
    lastRef.current = p
  }

  const end = () => {
    drawingRef.current = false
    lastRef.current = null
    shapeStartRef.current = null
    shapeSnapshotRef.current = null
  }

  const undo = () => {
    if (textDraftRef.current) {
      setTextDraft(null)
      textDraftRef.current = null
      return
    }
    const canvas = canvasRef.current
    const ctx = canvas?.getContext("2d")
    const previous = undoRef.current.pop()
    if (!canvas || !ctx || !previous) return
    ctx.putImageData(previous, 0, 0)
  }

  const cycleSizedTool = (value: Extract<DrawTool, "text" | "line" | "circle">) => {
    if (tool !== value) {
      setTool(value)
      setToolSize("s")
      return
    }
    const index = DRAW_SIZE_SEQUENCE.indexOf(toolSize)
    const next = DRAW_SIZE_SEQUENCE[index + 1]
    if (next) setToolSize(next)
    else setTool("pen")
  }

  const toolButton = (value: DrawTool, title: string, icon: ReactNode) => (
    <button
      type="button"
      title={title}
      onClick={() => setTool(value)}
      className={cn("rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground", tool === value && "bg-secondary text-secondary-foreground")}
    >
      {icon}
    </button>
  )

  const sizedToolButton = (value: Extract<DrawTool, "text" | "line" | "circle">, title: string, icon: ReactNode) => {
    const active = tool === value
    return (
      <button
        type="button"
        title={title}
        onClick={() => cycleSizedTool(value)}
        className={cn("relative rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground", active && "bg-secondary text-secondary-foreground")}
      >
        {icon}
        {active && (
          <span className="absolute -right-1 -top-1 rounded-full border bg-background px-1 text-[9px] font-semibold leading-3 text-foreground">
            {toolSize.toUpperCase()}
          </span>
        )}
      </button>
    )
  }

  return (
    <div className="space-y-2">
      <div className="relative">
        <canvas
          ref={canvasRef}
          width={900}
          height={480}
          className={cn("aspect-[15/8] w-full touch-none rounded-md border bg-white shadow-inner", tool === "text" ? "cursor-text" : "cursor-crosshair")}
          onPointerDown={begin}
          onPointerMove={move}
          onPointerUp={end}
          onPointerCancel={end}
          data-testid="note-drawing-canvas"
        />
        {textDraft && (
          <input
            ref={textInputRef}
            value={textDraft.value}
            onChange={(event) => setTextDraft((draft) => draft ? { ...draft, value: event.target.value } : draft)}
            onBlur={() => commitTextDraft()}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault()
                commitTextDraft()
              } else if (event.key === "Escape") {
                event.preventDefault()
                textDraftRef.current = null
                setTextDraft(null)
              }
            }}
            onPointerDown={(event) => event.stopPropagation()}
            placeholder="type then Enter"
            className="absolute z-10 min-w-28 rounded border-2 border-ring bg-white px-2 py-0.5 text-sm text-black shadow-lg outline-none"
            style={{
              left: textDraft.left,
              top: textDraft.top,
              fontSize: textDraft.fontCss,
              color: textDraft.color,
              maxWidth: textDraft.maxWidth,
            }}
          />
        )}
      </div>
      <div className="flex flex-wrap items-center gap-1 rounded-md border bg-background/95 px-2 py-1.5 sm:gap-2">
        {toolButton("pen", "Brush", <Brush className="size-4" />)}
        {toolButton("eraser", "Eraser", <Eraser className="size-4" />)}
        {sizedToolButton("text", "Add text - click to cycle size", <Type className="size-4" />)}
        {sizedToolButton("line", "Line - click to cycle size", <span className={cn("block h-4 w-4 rotate-45 border-t border-current", tool === "line" && toolSize === "m" && "border-t-2", tool === "line" && toolSize === "l" && "border-t-4")} />)}
        {sizedToolButton("circle", "Circle - click to cycle size", <Circle className={cn("size-4", tool === "circle" && toolSize === "l" && "stroke-[3]")} />)}
        <input type="color" value={stroke} onChange={(e) => setStroke(e.target.value)} title="Stroke color" className="h-7 w-8 rounded border bg-transparent p-0.5" />
        <input type="range" min={1} max={18} value={size} onChange={(e) => setSize(Number(e.target.value))} title="Stroke size" className="w-20 sm:w-32" />
        <span className="flex-1" />
        <button type="button" onClick={undo} title="Undo" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground">
          <Undo2 className="size-4" />
        </button>
        <button type="button" onClick={() => { const canvas = canvasRef.current; if (canvas) { snapshot(); clearCanvas(canvas) } }} title="Clear" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-destructive">
          <Trash2 className="size-4" />
        </button>
      </div>
    </div>
  )
}

function NoteForm({
  mode,
  initial,
  busy,
  onSubmit,
  onCancel,
}: {
  mode: "create" | "edit"
  initial?: Note
  busy?: boolean
  onSubmit: (payload: NotePayload) => void
  onCancel?: () => void
}) {
  const draftId = initial?.id
  const [loadedDraft] = useState(() => loadNoteDraft(draftId, formFromNote(initial)))
  const [form, setForm] = useState<FormState>(() => loadedDraft.form)
  const [draftRestored, setDraftRestored] = useState(loadedDraft.restored)
  const [uploading, setUploading] = useState(false)
  const [savingDrawing, setSavingDrawing] = useState(false)
  const [error, setError] = useState("")
  const fileRef = useRef<HTMLInputElement>(null)
  const bgFileRef = useRef<HTMLInputElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const latestFormRef = useRef(form)
  const skipDraftSaveRef = useRef(false)
  const discardedFormRef = useRef<FormState | null>(null)
  const set = <K extends keyof FormState>(key: K, value: FormState[K]) => setForm((prev) => ({ ...prev, [key]: value }))
  const formBg = backgroundStyle(form.color)

  useEffect(() => {
    latestFormRef.current = form
  }, [form])

  useEffect(() => {
    if (!draftRestored) return
    toast(mode === "create" ? "Restored unsaved note" : "Restored unsaved changes", "info")
  }, [draftRestored, mode])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      if (discardedFormRef.current) {
        if (sameFormDraft(discardedFormRef.current, form)) {
          clearNoteDraft(draftId)
          return
        }
        discardedFormRef.current = null
      }
      if (!skipDraftSaveRef.current) saveNoteDraft(draftId, form)
    }, 600)
    return () => window.clearTimeout(timer)
  }, [draftId, form])

  useEffect(() => {
    return () => {
      if (discardedFormRef.current && sameFormDraft(discardedFormRef.current, latestFormRef.current)) {
        clearNoteDraft(draftId)
        return
      }
      if (!skipDraftSaveRef.current) saveNoteDraft(draftId, latestFormRef.current)
    }
  }, [draftId])

  const discardDraft = () => {
    clearNoteDraft(draftId)
    const restored = formFromNote(initial)
    discardedFormRef.current = restored
    latestFormRef.current = restored
    setForm(restored)
    setDraftRestored(false)
  }

  const cancel = () => {
    skipDraftSaveRef.current = true
    clearNoteDraft(draftId)
    setDraftRestored(false)
    onCancel?.()
  }

  const chooseImage = async (file?: File) => {
    if (!file) return
    setError("")
    setUploading(true)
    try {
      const url = await uploadNoteImage(file)
      set("imageUrl", url)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Image upload failed")
    } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ""
    }
  }

  const chooseBackground = async (file?: File) => {
    if (!file) return
    setError("")
    setUploading(true)
    try {
      const url = await uploadNoteImage(file)
      set("color", `bg:${url}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Background upload failed")
    } finally {
      setUploading(false)
      if (bgFileRef.current) bgFileRef.current.value = ""
    }
  }

  const submit = async () => {
    let payload = payloadFromForm(form, noteItems(initial || ({} as Note)))
    if (form.noteType === "draw") {
      const canvas = canvasRef.current
      if (canvas) {
        setSavingDrawing(true)
        try {
          const blob = await canvasBlob(canvas)
          const url = await uploadNoteImage(blob, "drawing.png")
          payload = { ...payload, content: "", items: [], image_url: url, note_type: "draw" }
        } catch (e) {
          setError(e instanceof Error ? e.message : "Drawing upload failed")
          setSavingDrawing(false)
          return
        } finally {
          setSavingDrawing(false)
        }
      }
    }
    if (!payload.title && !payload.content && (!payload.items || payload.items.length === 0) && !payload.image_url) return
    skipDraftSaveRef.current = true
    clearNoteDraft(draftId)
    setDraftRestored(false)
    onSubmit(payload)
    if (mode === "create") {
      const next = formFromNote()
      latestFormRef.current = next
      setForm(next)
      skipDraftSaveRef.current = false
    }
  }

  const busyNow = !!busy || uploading || savingDrawing
  const typeBtn = (type: NoteFormType, label: string, icon: ReactNode) => (
    <button
      className={cn("inline-flex items-center gap-1.5 rounded px-2 py-1 text-xs", form.noteType === type ? "bg-secondary text-secondary-foreground" : "text-muted-foreground")}
      onClick={() => set("noteType", type)}
      type="button"
    >
      {icon}{label}
    </button>
  )

  return (
    <div
      className={cn("space-y-3 rounded-lg border p-3", formBg ? "border-white/20 bg-card shadow-sm" : colorClasses(form.color))}
      style={formBg}
      data-testid={mode === "create" ? "note-create-form" : "note-edit-form"}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="inline-flex flex-wrap rounded-md border bg-background p-0.5" aria-label="Note type">
          {typeBtn("note", "Note", <StickyNote className="size-3.5" />)}
          {typeBtn("todo", "Todo", <ListChecks className="size-3.5" />)}
          {typeBtn("draw", "Draw", <Brush className="size-3.5" />)}
          {typeBtn("goal", "Goal", <Target className="size-3.5" />)}
        </div>
        {onCancel && (
          <button onClick={cancel} title="Cancel" className="rounded-md bg-background/80 p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground">
            <X className="size-4" />
          </button>
        )}
      </div>
      {draftRestored && (
        <div className="flex items-center justify-between gap-2 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-200">
          <span>{mode === "create" ? "Restored unsaved note" : "Restored unsaved changes"}</span>
          <button type="button" onClick={discardDraft} className="rounded px-2 py-1 font-medium hover:bg-amber-500/15">
            Discard
          </button>
        </div>
      )}
      <input value={form.title} onChange={(e) => set("title", e.target.value)} placeholder="Title" className={input} />
      {form.imageUrl && form.noteType !== "draw" && (
        <div className="relative overflow-hidden rounded-md border">
          <img src={form.imageUrl} alt="" className="max-h-48 w-full object-cover" />
          <button onClick={() => set("imageUrl", "")} title="Remove image" className="absolute right-2 top-2 rounded-md bg-background/90 p-1.5 shadow hover:bg-background">
            <X className="size-4" />
          </button>
        </div>
      )}
      {form.noteType === "draw" ? (
        <DrawingPad canvasRef={canvasRef} initialImageUrl={form.imageUrl} />
      ) : form.noteType === "todo" ? (
        <textarea value={form.itemsText} onChange={(e) => set("itemsText", e.target.value)} placeholder="One checklist item per line" rows={4} className={area} />
      ) : form.noteType === "goal" ? (
        <div className="grid gap-2">
          <textarea value={form.content} onChange={(e) => set("content", e.target.value)} placeholder="Describe the goal..." rows={3} className={area} />
          <textarea value={form.itemsText} onChange={(e) => set("itemsText", e.target.value)} placeholder="One next step per line" rows={4} className={area} />
        </div>
      ) : (
        <textarea value={form.content} onChange={(e) => set("content", e.target.value)} placeholder="Take a note..." rows={4} className={area} />
      )}
      <div className="grid gap-2 md:grid-cols-[minmax(0,1fr)_9rem_minmax(0,1fr)]">
        <div>
          <label htmlFor={`${mode}-note-reminder`} className="mb-1 block text-xs text-muted-foreground">Reminder</label>
          <input id={`${mode}-note-reminder`} type="datetime-local" value={form.dueDate} onChange={(e) => set("dueDate", e.target.value)} className={input} />
        </div>
        <div>
          <label htmlFor={`${mode}-note-repeat`} className="mb-1 block text-xs text-muted-foreground">Repeat</label>
          <select id={`${mode}-note-repeat`} value={form.repeat} onChange={(e) => set("repeat", e.target.value)} disabled={!form.dueDate} className={input}>
            {REPEATS.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
          </select>
        </div>
        <div>
          <label htmlFor={`${mode}-note-tags`} className="mb-1 block text-xs text-muted-foreground">Tags</label>
          <input id={`${mode}-note-tags`} value={form.label} onChange={(e) => set("label", e.target.value)} placeholder="#home #work" className={input} />
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-1.5" aria-label="Note color">
          <Palette className="size-4 text-muted-foreground" />
          {NOTE_COLORS.map((c) => (
            <button
              key={c.value || "none"}
              type="button"
              onClick={() => set("color", c.value)}
              title={c.label}
              aria-label={`${c.label} color`}
              className={cn("size-6 rounded-full border", form.color === c.value && "ring-2 ring-ring ring-offset-2 ring-offset-background")}
              style={{ background: c.bg }}
            />
          ))}
          {bgImageUrl(form.color) && (
            <button type="button" onClick={() => set("color", "")} title="Remove background" className="rounded-full border bg-background p-1 text-muted-foreground hover:text-foreground">
              <X className="size-3.5" />
            </button>
          )}
        </div>
        <input ref={fileRef} type="file" accept="image/*" capture="environment" className="hidden" onChange={(e) => void chooseImage(e.target.files?.[0])} />
        <input ref={bgFileRef} type="file" accept="image/*" className="hidden" onChange={(e) => void chooseBackground(e.target.files?.[0])} />
        <Button size="sm" variant="outline" disabled={busyNow} onClick={() => fileRef.current?.click()}>
          <Image className="size-4" />{form.noteType === "draw" ? "Base" : "Image"}
        </Button>
        <Button size="sm" variant="outline" disabled={busyNow} onClick={() => bgFileRef.current?.click()}>
          <ImagePlus className="size-4" />Background
        </Button>
        <div className="ml-auto flex items-center gap-2">
          {error && <span className="text-xs text-destructive">{error}</span>}
          <Button size="sm" disabled={busyNow} onClick={() => void submit()}>
            {busyNow ? "Saving..." : mode === "create" ? "Add note" : "Save"}
          </Button>
        </div>
      </div>
    </div>
  )
}

function NoteCard({
  note,
  selected,
  selectMode,
  archiveView,
  solving,
  onToggleSelect,
  onEdit,
  onPin,
  onArchive,
  onDelete,
  onToggleItem,
  onDeleteItem,
  onAddItem,
  onColor,
  onLabel,
  onSolveAgent,
  onOpenAgent,
}: {
  note: Note
  selected: boolean
  selectMode: boolean
  archiveView: boolean
  solving: boolean
  onToggleSelect: () => void
  onEdit: () => void
  onPin: () => void
  onArchive: () => void
  onDelete: () => void
  onToggleItem: (index: number) => void
  onDeleteItem: (index: number) => void
  onAddItem: (text: string) => void
  onColor: (color: string) => void
  onLabel: (label: string) => void
  onSolveAgent: () => void
  onOpenAgent: () => void
}) {
  const [quickItem, setQuickItem] = useState("")
  const [uploadingBg, setUploadingBg] = useState(false)
  const bgFileRef = useRef<HTMLInputElement>(null)
  const items = noteItems(note)
  const labels = noteLabels(note)
  const overdue = isOverdue(note.due_date)
  const isGoal = note.note_type === "goal"
  const isDraw = note.note_type === "draw"
  const cardBg = backgroundStyle(note.color)
  const muted = cardBg ? "text-white/80" : "text-muted-foreground"
  const iconButton = cardBg
    ? "grid size-8 place-items-center rounded-md p-1.5 text-white/80 hover:bg-white/15 hover:text-white disabled:opacity-60 md:size-auto md:p-1.5"
    : "grid size-8 place-items-center rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-60 md:size-auto md:p-1.5"
  const submitQuickItem = () => {
    const text = quickItem.trim()
    if (!text) return
    onAddItem(text)
    setQuickItem("")
  }
  const chooseCardBackground = async (file?: File) => {
    if (!file) return
    setUploadingBg(true)
    try {
      const url = await uploadNoteImage(file)
      onColor(`bg:${url}`)
    } catch {
      // Keep the card stable; full image/background upload errors are still handled in edit mode.
    } finally {
      setUploadingBg(false)
      if (bgFileRef.current) bgFileRef.current.value = ""
    }
  }
  return (
    <article
      className={cn("group relative min-w-0 rounded-lg border p-3", cardBg ? "border-white/25 bg-card text-white shadow-sm" : colorClasses(note.color), selected && "ring-2 ring-primary")}
      style={cardBg}
      data-testid={`note-card-${note.id}`}
    >
      {selectMode && (
        <input type="checkbox" checked={selected} onChange={onToggleSelect} aria-label={`Select ${note.title || "note"}`} className="absolute left-2 top-2 z-10 size-4" />
      )}
      <div className={cn("flex items-start gap-2", selectMode && "pl-6")}>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5 pr-24">
            {note.pinned && <Pin className={cn("size-3.5 shrink-0 fill-current", muted)} />}
            {isGoal ? <Target className={cn("size-3.5 shrink-0", muted)} /> : isDraw ? <Brush className={cn("size-3.5 shrink-0", muted)} /> : isChecklistType(note.note_type) ? <ListChecks className={cn("size-3.5 shrink-0", muted)} /> : null}
            <h2 className="truncate text-sm font-semibold">{note.title || "(untitled)"}</h2>
            {isGoal && <span className={cn("rounded-full border px-1.5 py-0.5 text-[11px]", cardBg ? "border-white/30 bg-white/15 text-white/85" : "bg-muted text-muted-foreground")}>Goal{goalProgress(note)}</span>}
          </div>
          {note.image_url && <img src={note.image_url} alt="" className="mt-2 max-h-56 w-full rounded-md object-cover" />}
          {isGoal && note.content && <p className={cn("mt-2 whitespace-pre-wrap break-words text-sm", muted)}>{note.content}</p>}
          {items.length > 0 ? (
            <div className="mt-2 space-y-1.5">
              {items.slice(0, 10).map((item, index) => {
                const done = itemDone(item)
                return (
                  <div key={`${item.id || index}-${item.text || ""}`} className="group/item flex min-w-0 items-start gap-1 text-sm" style={{ paddingLeft: `${Math.min(item.indent || 0, 3) * 12}px` }}>
                    <label className="flex min-w-0 flex-1 items-start gap-2">
                      <input type="checkbox" checked={done} onChange={() => onToggleItem(index)} className="mt-0.5 size-4 shrink-0" />
                      <span className={cn("min-w-0 flex-1 break-words", done && (cardBg ? "text-white/55 line-through" : "text-muted-foreground line-through"))}>{item.text || "(blank)"}</span>
                    </label>
                    <button
                      type="button"
                      onClick={(event) => { event.stopPropagation(); onDeleteItem(index) }}
                      title="Delete item"
                      className={cn("shrink-0 rounded p-0.5 opacity-100 md:opacity-0 md:transition-opacity md:group-hover/item:opacity-100", cardBg ? "text-white/60 hover:bg-white/15 hover:text-white" : "text-muted-foreground hover:bg-accent hover:text-destructive")}
                    >
                      <X className="size-3.5" />
                    </button>
                  </div>
                )
              })}
              {items.length > 10 && <div className={cn("text-xs", muted)}>+{items.length - 10} more</div>}
              <label className="mt-2 flex items-center gap-2">
                <input
                  value={quickItem}
                  onChange={(event) => setQuickItem(event.target.value)}
                  onKeyDown={(event) => {
                    event.stopPropagation()
                    if (event.key === "Enter") {
                      event.preventDefault()
                      submitQuickItem()
                    }
                  }}
                  onClick={(event) => event.stopPropagation()}
                  placeholder="+ Add item"
                  className={cn("h-8 min-w-0 flex-1 rounded-md border px-2 text-sm outline-none focus-visible:border-ring", cardBg ? "border-white/25 bg-white/10 text-white placeholder:text-white/55" : "bg-background")}
                />
                <button
                  type="button"
                  onClick={(event) => { event.stopPropagation(); submitQuickItem() }}
                  title="Add item"
                  className={cn("rounded-md border p-1.5", cardBg ? "border-white/25 bg-white/10 text-white/80 hover:bg-white/15 hover:text-white" : "text-muted-foreground hover:bg-accent hover:text-foreground")}
                >
                  <Check className="size-3.5" />
                </button>
              </label>
            </div>
          ) : !isGoal && note.content ? (
            <p className={cn("mt-2 whitespace-pre-wrap break-words text-sm", muted)}>{note.content}</p>
          ) : null}
          {note.due_date && (
            <div className={cn("mt-2 inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs", overdue ? "border-destructive/30 bg-destructive/10 text-destructive" : cardBg ? "border-white/30 bg-white/15 text-white/85" : "text-muted-foreground")}>
              <Bell className="size-3" />
              {dueLabel(note.due_date)}
              {note.repeat && note.repeat !== "none" && <span>· {note.repeat}</span>}
            </div>
          )}
          {labels.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {labels.map((label) => (
                <button key={label} onClick={() => onLabel(label)} className={cn("rounded px-1.5 py-0.5 text-xs", cardBg ? "bg-white/15 text-white/80 hover:text-white" : "bg-muted text-muted-foreground hover:text-foreground")}>
                  #{label}
                </button>
              ))}
            </div>
          )}
          {note.agent_session_id && (
            <button onClick={onOpenAgent} className={cn("mt-2 inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs", cardBg ? "border-white/30 bg-white/15 text-white/85 hover:text-white" : "text-muted-foreground hover:text-foreground")}>
              <Bot className="size-3.5" />Agent <ExternalLink className="size-3" />
            </button>
          )}
        </div>
      </div>
      <div className={cn("mt-3 flex flex-wrap items-center gap-1.5 border-t pt-2", cardBg ? "border-white/20" : "border-border/70")} aria-label="Card color">
        <Palette className={cn("size-3.5", muted)} />
        {NOTE_COLORS.map((color) => (
          <button
            key={color.value || "none"}
            type="button"
            onClick={(event) => { event.stopPropagation(); onColor(color.value) }}
            title={`${color.label} color`}
            aria-label={`${color.label} color`}
            className={cn("size-5 rounded-full border", color.value === "" && "bg-background", note.color === color.value && "ring-2 ring-ring ring-offset-1 ring-offset-background")}
            style={{ background: color.bg }}
          />
        ))}
        <input ref={bgFileRef} type="file" accept="image/*" className="hidden" onChange={(event) => void chooseCardBackground(event.target.files?.[0])} />
        <button
          type="button"
          onClick={(event) => { event.stopPropagation(); bgFileRef.current?.click() }}
          title="Background image"
          aria-label="Background image"
          disabled={uploadingBg}
          className={cn("grid size-5 place-items-center rounded-full border", bgImageUrl(note.color) && "ring-2 ring-ring ring-offset-1 ring-offset-background")}
          style={{ background: bgImageUrl(note.color) ? `center / cover url("${bgImageUrl(note.color).replace(/"/g, "%22")}")` : "conic-gradient(from 0deg, #e06c75, #d19a66, #e5c07b, #98c379, #61afef, #c678dd, #e06c75)" }}
        >
          {uploadingBg && <ImagePlus className="size-3 text-white drop-shadow" />}
        </button>
      </div>
      <div className="absolute right-2 top-2 flex gap-1 opacity-100 md:opacity-0 md:transition-opacity md:group-hover:opacity-100">
        <button onClick={onSolveAgent} disabled={solving} title={note.agent_session_id ? "Re-run agent" : "Agent: solve this"} className={iconButton}>
          {solving ? <Sparkles className="size-3.5 animate-pulse" /> : <Bot className="size-3.5" />}
        </button>
        <button onClick={onPin} title={note.pinned ? "Unpin" : "Pin"} className={cn(iconButton, note.pinned && "text-foreground")}>
          <Pin className="size-3.5" />
        </button>
        <button onClick={onEdit} title="Edit" className={iconButton}>
          <Pencil className="size-3.5" />
        </button>
        <button onClick={onArchive} title={archiveView ? "Unarchive" : "Archive"} className={iconButton}>
          {archiveView ? <Undo2 className="size-3.5" /> : <Archive className="size-3.5" />}
        </button>
        <button onClick={onDelete} title="Delete" className={cn(iconButton, cardBg ? "hover:text-white" : "hover:text-destructive")}>
          <Trash2 className="size-3.5" />
        </button>
      </div>
    </article>
  )
}

export function NotesRoute() {
  const navigate = useNavigate()
  const [archiveView, setArchiveView] = useState(false)
  const { data: notes, isLoading } = useNotes({ archived: archiveView })
  const { create, update, remove, pin, archive, toggleItem, reorder, solveAgent } = useNoteMutations()
  const [editNote, setEditNote] = useState<Note | null>(null)
  const [q, setQ] = useState("")
  const [labelFilter, setLabelFilter] = useState("")
  const [filter, setFilter] = useState<NoteFilter>("all")
  const [nextReminderFilter, setNextReminderFilter] = useState<"reminders" | "no-reminders">("reminders")
  const [selectMode, setSelectMode] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [copied, setCopied] = useState("")
  const [dragId, setDragId] = useState("")
  const [dragOverId, setDragOverId] = useState("")
  const [mobileDragId, setMobileDragId] = useState("")
  const [showFirstOpenHint, setShowFirstOpenHint] = useState(false)
  const activeHighlightIds = useNoteReminders((s) => s.activeHighlightIds)
  const longPressTimerRef = useRef<number | null>(null)
  const touchStartRef = useRef<{ id: string; x: number; y: number; armed: boolean; dragging: boolean } | null>(null)
  const suppressMobileClickRef = useRef(false)
  const scrolledReminderRef = useRef<Set<string>>(new Set())

  const allNotes = notes || EMPTY_NOTES
  const activeHighlightSet = useMemo(() => new Set(activeHighlightIds), [activeHighlightIds])

  useEffect(() => {
    if (typeof localStorage === "undefined") return
    let timer: number | null = null
    try {
      if (localStorage.getItem(NOTES_FIRST_OPEN_HINT_KEY)) return
      localStorage.setItem(NOTES_FIRST_OPEN_HINT_KEY, "1")
      timer = window.setTimeout(() => setShowFirstOpenHint(true), 0)
    } catch {
      // Ignore unavailable localStorage; the rest of Notes still works.
    }
    return () => {
      if (timer != null) window.clearTimeout(timer)
    }
  }, [])

  useEffect(() => {
    if (!showFirstOpenHint) return
    const timer = window.setTimeout(() => setShowFirstOpenHint(false), 6500)
    return () => window.clearTimeout(timer)
  }, [showFirstOpenHint])

  useEffect(() => {
    if (archiveView || !notes) return
    const reminders = useNoteReminders.getState()
    const newlyHighlighted = reminders.flushHighlights(notes)
    reminders.dismissFired(notes)
    const firstId = newlyHighlighted[0] || useNoteReminders.getState().activeHighlightIds.find((id) => notes.some((note) => note.id === id && hasActiveNoteReminder(note)))
    if (!firstId) return
    if (scrolledReminderRef.current.has(firstId)) return
    scrolledReminderRef.current.add(firstId)
    window.setTimeout(() => {
      const card = document.querySelector(`[data-note-id="${CSS.escape(firstId)}"]`)
      card?.scrollIntoView({ behavior: "smooth", block: "center" })
    }, 80)
  }, [archiveView, notes])

  const labels = useMemo(() => {
    const counts = new Map<string, number>()
    for (const note of allNotes) for (const label of noteLabels(note)) counts.set(label, (counts.get(label) || 0) + 1)
    return Array.from(counts.entries()).sort((a, b) => a[0].localeCompare(b[0]))
  }, [allNotes])

  const counts = useMemo(() => {
    const active = allNotes.filter((note) => !note.archived)
    return {
      default: active.filter((note) => noteLabels(note).length === 0).length,
      reminders: active.filter((note) => !!note.due_date && hasReminderTime(note.due_date)).length,
      pastReminders: active.filter(isPastReminder).length,
      goals: active.filter((note) => note.note_type === "goal").length,
      today: active.filter((note) => note.note_type === "goal" && !!nextGoalStep(note)).length,
    }
  }, [allNotes])

  const filtered = useMemo(() => {
    const query = q.trim().toLowerCase()
    const base = allNotes.filter((note) => {
      if (!noteMatches(note, query)) return false
      if (labelFilter && !noteLabels(note).includes(labelFilter)) return false
      if (filter === "default" && noteLabels(note).length > 0) return false
      if (filter === "reminders" && !(note.due_date && hasReminderTime(note.due_date))) return false
      if (filter === "no-reminders" && note.due_date && hasReminderTime(note.due_date)) return false
      if (filter === "goals" && note.note_type !== "goal") return false
      if (filter === "today" && (note.note_type !== "goal" || !nextGoalStep(note))) return false
      return true
    })
    if (filter === "reminders") {
      return [...base].sort((a, b) => new Date(a.due_date || 0).getTime() - new Date(b.due_date || 0).getTime())
    }
    if (!archiveView) return sortNotesForReminderPriority(base)
    return base
  }, [allNotes, archiveView, filter, labelFilter, q])

  const selectedCount = selected.size
  const toggleSelected = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }
  const clearSelect = () => { setSelected(new Set()); setSelectMode(false) }
  const selectAll = () => setSelected(new Set(filtered.map((note) => note.id)))

  const bulkArchive = () => {
    for (const id of selected) archive.mutate(id)
    clearSelect()
  }
  const bulkDelete = () => {
    if (!confirm(`Delete ${selected.size} note${selected.size === 1 ? "" : "s"}?`)) return
    for (const id of selected) remove.mutate(id)
    clearSelect()
  }
  const clearPastReminders = () => {
    const targets = allNotes.filter(isPastReminder)
    if (!targets.length) return
    if (!confirm(`Delete ${targets.length} past reminder${targets.length === 1 ? "" : "s"}?`)) return
    for (const note of targets) remove.mutate(note.id)
  }
  const copyNote = async (note: Note) => {
    const text = serializeNote(note)
    if (!text) return
    await navigator.clipboard?.writeText(text)
    setCopied(note.id)
    window.setTimeout(() => setCopied((id) => id === note.id ? "" : id), 1200)
  }
  const toggleReminderFilter = () => {
    setLabelFilter("")
    if (filter === "reminders" || filter === "no-reminders") {
      setFilter("all")
      return
    }
    setFilter(nextReminderFilter)
    setNextReminderFilter((next) => next === "reminders" ? "no-reminders" : "reminders")
  }
  const moveNoteById = (sourceId: string, targetId: string) => {
    if (!sourceId || sourceId === targetId) return
    const ids = filtered.map((note) => note.id)
    const from = ids.indexOf(sourceId)
    const to = ids.indexOf(targetId)
    if (from < 0 || to < 0) return
    const [moved] = ids.splice(from, 1)
    ids.splice(to, 0, moved)
    reorder.mutate(ids)
  }
  const moveVisibleNotes = (targetId: string) => {
    moveNoteById(dragId, targetId)
    setDragId("")
    setDragOverId("")
  }
  const clearLongPress = () => {
    if (longPressTimerRef.current != null) {
      window.clearTimeout(longPressTimerRef.current)
      longPressTimerRef.current = null
    }
  }
  const resetTouchDrag = () => {
    clearLongPress()
    touchStartRef.current = null
    setMobileDragId("")
    setDragId("")
    setDragOverId("")
  }
  const startTouchPress = (note: Note, event: ReactTouchEvent<HTMLDivElement>) => {
    if (archiveView || selectMode || !isMobileNotesMode() || isInteractiveTarget(event.target)) return
    if (event.touches.length !== 1) return
    const touch = event.touches[0]
    clearLongPress()
    touchStartRef.current = { id: note.id, x: touch.clientX, y: touch.clientY, armed: true, dragging: false }
    longPressTimerRef.current = window.setTimeout(() => {
      const state = touchStartRef.current
      if (!state?.armed) return
      state.dragging = true
      state.armed = false
      suppressMobileClickRef.current = true
      setMobileDragId(note.id)
      setDragId(note.id)
      setDragOverId(note.id)
      window.navigator.vibrate?.(15)
    }, 450)
  }
  const moveTouchPress = (event: ReactTouchEvent<HTMLDivElement>) => {
    const state = touchStartRef.current
    if (!state || event.touches.length !== 1) return
    const touch = event.touches[0]
    const dx = Math.abs(touch.clientX - state.x)
    const dy = Math.abs(touch.clientY - state.y)
    if (!state.dragging && (dx > 8 || dy > 8)) {
      resetTouchDrag()
      return
    }
    if (!state.dragging) return
    event.preventDefault()
    const under = document.elementFromPoint(touch.clientX, touch.clientY)
    const target = under instanceof HTMLElement ? under.closest<HTMLElement>("[data-note-id]") : null
    const targetId = target?.dataset.noteId || ""
    if (targetId && targetId !== dragOverId) setDragOverId(targetId)
  }
  const endTouchPress = () => {
    const state = touchStartRef.current
    const targetId = dragOverId
    if (state?.dragging) {
      suppressMobileClickRef.current = true
      window.setTimeout(() => { suppressMobileClickRef.current = false }, 350)
      if (targetId && targetId !== state.id) moveNoteById(state.id, targetId)
    }
    resetTouchDrag()
  }
  const openMobileEdit = (note: Note, event: ReactTouchEvent<HTMLDivElement> | React.MouseEvent<HTMLDivElement>) => {
    if (suppressMobileClickRef.current) {
      suppressMobileClickRef.current = false
      return
    }
    if (!isMobileNotesMode() || archiveView || selectMode || mobileDragId || isInteractiveTarget(event.target)) return
    setEditNote(note)
  }

  const hasActiveFilter = !!q || !!labelFilter || filter !== "all"

  return (
    <div className="mx-auto flex h-full w-full max-w-6xl flex-col">
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b px-4 py-2">
        <div className="min-w-0">
          <span className="text-sm font-semibold">Notes</span>
          <span className="ml-2 text-sm text-muted-foreground">{archiveView ? "Archive" : `${allNotes.length} active`}</span>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <Button size="sm" variant={archiveView ? "secondary" : "outline"} onClick={() => { setArchiveView((v) => !v); clearSelect(); setEditNote(null); setFilter("all") }}>
            <Archive className="size-4" />Archive
          </Button>
          <Button size="sm" variant={selectMode ? "secondary" : "outline"} onClick={() => { setSelectMode((v) => !v); setSelected(new Set()) }}>
            <Check className="size-4" />Select
          </Button>
        </div>
      </header>

      {showFirstOpenHint && (
        <div
          id="notes-first-open-hint"
          data-testid="notes-first-open-hint"
          className="fixed right-4 top-16 z-50 w-[260px] rounded-lg border bg-popover px-3 py-3 text-sm text-popover-foreground shadow-lg animate-pop-in"
        >
          <div className="flex items-start gap-2">
            <Bell className="mt-0.5 size-4 shrink-0 text-primary" />
            <p className="min-w-0 flex-1 leading-5"><b>Notes</b> is your basic todo list, and also where reminders are managed.</p>
          </div>
          <button type="button" onClick={() => setShowFirstOpenHint(false)} className="ml-auto mt-2 block rounded-md border px-3 py-1 text-xs font-medium hover:bg-accent">
            OK
          </button>
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {!archiveView && !editNote && (
          <NoteForm
            mode="create"
            busy={create.isPending}
            onSubmit={(payload) => create.mutate(payload)}
          />
        )}
        {editNote && (
          <div className="fixed inset-0 z-50 overflow-y-auto bg-background p-3 md:static md:z-auto md:mb-4 md:overflow-visible md:bg-transparent md:p-0">
            <div className="sticky top-0 z-10 -mx-3 -mt-3 mb-3 flex items-center gap-2 border-b bg-background/95 px-3 py-2 backdrop-blur md:hidden">
              <button onClick={() => setEditNote(null)} title="Back" className="rounded-md p-2 text-muted-foreground hover:bg-accent hover:text-foreground">
                <Undo2 className="size-4" />
              </button>
              <span className="min-w-0 flex-1 truncate text-sm font-semibold">{editNote.title || "Edit note"}</span>
            </div>
            <NoteForm
              key={editNote.id}
              mode="edit"
              initial={editNote}
              busy={update.isPending}
              onCancel={() => setEditNote(null)}
              onSubmit={(payload) => update.mutate({ id: editNote.id, ...payload }, { onSuccess: () => setEditNote(null) })}
            />
          </div>
        )}

        <div className="mt-4 space-y-2">
          <div className="grid gap-2 md:grid-cols-[minmax(0,1fr)_auto] md:items-start">
            <label className="relative block">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search notes..." className="h-9 w-full rounded-md border bg-background pl-9 pr-3 text-sm outline-none focus-visible:border-ring" />
            </label>
            <div className="flex flex-wrap gap-1.5">
              <Button size="sm" variant={!labelFilter && filter === "all" ? "secondary" : "outline"} onClick={() => { setLabelFilter(""); setFilter("all") }}>All</Button>
              <Button size="sm" variant={filter === "default" ? "secondary" : "outline"} onClick={() => { setLabelFilter(""); setFilter(filter === "default" ? "all" : "default") }}>
                Default <span className="text-muted-foreground">{counts.default}</span>
              </Button>
              {counts.today > 0 && (
                <Button size="sm" variant={filter === "today" ? "secondary" : "outline"} onClick={() => { setLabelFilter(""); setFilter(filter === "today" ? "all" : "today") }}>
                  <CalendarDays className="size-4" />Today <span className="text-muted-foreground">{counts.today}</span>
                </Button>
              )}
              {counts.goals > 0 && (
                <Button size="sm" variant={filter === "goals" ? "secondary" : "outline"} onClick={() => { setLabelFilter(""); setFilter(filter === "goals" ? "all" : "goals") }}>
                  <Target className="size-4" />Goals <span className="text-muted-foreground">{counts.goals}</span>
                </Button>
              )}
              <Button size="sm" variant={filter === "reminders" || filter === "no-reminders" ? "secondary" : "outline"} onClick={toggleReminderFilter}>
                <Bell className="size-4" />{filter === "no-reminders" ? "No reminders" : "Reminders"} <span className="text-muted-foreground">{counts.reminders}</span>
              </Button>
              {filter === "reminders" && counts.pastReminders > 0 && (
                <Button size="sm" variant="outline" onClick={clearPastReminders}>
                  <Trash2 className="size-4" />Clear past <span className="text-muted-foreground">{counts.pastReminders}</span>
                </Button>
              )}
              {labels.map(([label, count]) => (
                <Button key={label} size="sm" variant={labelFilter === label ? "secondary" : "outline"} onClick={() => { setFilter("all"); setLabelFilter(labelFilter === label ? "" : label) }}>
                  #{label} <span className="text-muted-foreground">{count}</span>
                </Button>
              ))}
            </div>
          </div>

          {selectMode && (
            <div className="flex flex-wrap items-center gap-2 rounded-md border bg-card px-3 py-2 text-sm">
              <span className="text-muted-foreground">{selectedCount} selected</span>
              <Button size="sm" variant="outline" onClick={selectAll} data-testid="notes-bulk-select-all">All</Button>
              <Button size="sm" variant="outline" disabled={!selectedCount} onClick={bulkArchive} data-testid="notes-bulk-archive">
                {archiveView ? <Undo2 className="size-4" /> : <Archive className="size-4" />}{archiveView ? "Unarchive" : "Archive"}
              </Button>
              <Button size="sm" variant="outline" disabled={!selectedCount} onClick={bulkDelete} data-testid="notes-bulk-delete">
                <Trash2 className="size-4" />Delete
              </Button>
              <button onClick={clearSelect} title="Cancel select" className="ml-auto rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground">
                <X className="size-4" />
              </button>
            </div>
          )}
        </div>

        {filter === "today" ? (
          <div className="mt-4 overflow-hidden rounded-lg border bg-card" data-testid="notes-today-view">
            <div className="flex items-center gap-2 border-b px-3 py-2 text-sm font-medium">
              <CalendarDays className="size-4 text-muted-foreground" />Today
            </div>
            {filtered.map((note) => {
              const next = nextGoalStep(note)
              if (!next) return null
              return (
                <div key={note.id} className="grid gap-2 border-b px-3 py-2 last:border-b-0 sm:grid-cols-[auto_minmax(0,1fr)_auto] sm:items-center">
                  <input type="checkbox" checked={false} onChange={() => toggleItem.mutate({ id: note.id, index: next.index })} title="Mark step done" className="size-4" />
                  <button onClick={() => setEditNote(note)} className="min-w-0 text-left">
                    <span className="block truncate text-sm font-medium">{note.title || "(untitled goal)"}</span>
                    <span className="block break-words text-sm text-muted-foreground">{next.item.text || "(blank)"}</span>
                  </button>
                  <span className="text-xs text-muted-foreground">{goalProgress(note).trim()}</span>
                </div>
              )
            })}
          </div>
        ) : (
          <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {filtered.map((note) => {
              const reminderGlow = activeHighlightSet.has(note.id) && hasActiveNoteReminder(note)
              return (
                <div
                  key={note.id}
                  className={cn(
                    "group relative touch-pan-y",
                    dragOverId === note.id && "rounded-lg ring-2 ring-ring",
                    mobileDragId === note.id && "scale-[.98] opacity-80",
                    reminderGlow && "note-reminder-fired-sticky rounded-lg",
                  )}
                  data-note-id={note.id}
                  data-testid={`note-wrap-${note.id}`}
                  draggable={!archiveView && !selectMode}
                  onClickCapture={() => {
                    if (reminderGlow) useNoteReminders.getState().dismissHighlight(note.id)
                  }}
                  onClick={(event) => openMobileEdit(note, event)}
                  onTouchStart={(event) => startTouchPress(note, event)}
                  onTouchMove={moveTouchPress}
                  onTouchEnd={endTouchPress}
                  onTouchCancel={resetTouchDrag}
                  onDragStart={(event) => {
                    if ((event.target as HTMLElement).closest("button,input,a,label,textarea,select")) {
                      event.preventDefault()
                      return
                    }
                    setDragId(note.id)
                    event.dataTransfer.effectAllowed = "move"
                  }}
                  onDragOver={(event) => {
                    if (!dragId || dragId === note.id) return
                    event.preventDefault()
                    setDragOverId(note.id)
                  }}
                  onDragLeave={() => setDragOverId((id) => id === note.id ? "" : id)}
                  onDrop={(event) => {
                    event.preventDefault()
                    moveVisibleNotes(note.id)
                  }}
                  onDragEnd={() => { setDragId(""); setDragOverId("") }}
                >
                  <NoteCard
                    note={note}
                    selected={selected.has(note.id)}
                    selectMode={selectMode}
                    archiveView={archiveView}
                    solving={solveAgent.isPending && solveAgent.variables?.id === note.id}
                    onToggleSelect={() => toggleSelected(note.id)}
                    onEdit={() => setEditNote(note)}
                    onPin={() => pin.mutate(note.id)}
                    onArchive={() => archive.mutate(note.id)}
                    onDelete={() => { if (confirm("Delete this note?")) remove.mutate(note.id) }}
                    onToggleItem={(index) => toggleItem.mutate({ id: note.id, index })}
                    onDeleteItem={(index) => update.mutate({ id: note.id, items: noteItems(note).filter((_, itemIndex) => itemIndex !== index) })}
                    onAddItem={(text) => update.mutate({ id: note.id, items: [...noteItems(note), newNoteItem(text)] })}
                    onColor={(color) => update.mutate({ id: note.id, color })}
                    onLabel={(label) => { setFilter("all"); setLabelFilter(label) }}
                    onSolveAgent={() => solveAgent.mutate(note)}
                    onOpenAgent={() => { if (note.agent_session_id) navigate(`/chat/${note.agent_session_id}`) }}
                  />
                  <div className="absolute bottom-2 right-2 flex gap-1 opacity-100 transition-opacity md:opacity-0 md:group-hover:opacity-100">
                    <button onClick={() => void copyNote(note)} title="Copy" className="grid size-8 place-items-center rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground md:size-auto md:p-1.5">
                      {copied === note.id ? <Check className="size-3.5" /> : <Clipboard className="size-3.5" />}
                    </button>
                  </div>
                </div>
              )
            })}
          </div>
        )}
        {isLoading && <p className="py-8 text-center text-sm text-muted-foreground">Loading notes...</p>}
        {!isLoading && filtered.length === 0 && <p className="py-8 text-center text-sm text-muted-foreground">{hasActiveFilter ? "No matching notes." : archiveView ? "No archived notes." : "No notes yet."}</p>}
      </div>
    </div>
  )
}
