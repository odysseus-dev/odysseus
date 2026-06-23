import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
} from "react"
import { useNavigate } from "react-router-dom"
import {
  AlertTriangle,
  Bell,
  CalendarPlus,
  ChevronLeft,
  ChevronRight,
  Download,
  ExternalLink,
  Image,
  MoreVertical,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Tag,
  Trash2,
  Upload,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react"
import {
  cookbookTaskId,
  createCalendarReminder,
  exportIcs,
  uploadCalendarBackgroundImage,
  useCalendarMutations,
  useCalendars,
  useEventMutations,
  useEvents,
  useImportIcs,
  useQuickAddEvent,
  useSync,
  type CalEvent,
  type Calendar as CalendarInfo,
  type EventInput,
  type EventPatch,
} from "@/api/calendar"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const inp = "h-9 w-full rounded-md border bg-background px-3 text-sm outline-none focus-visible:border-ring"

const RECUR_OPTIONS: { label: string; value: string }[] = [
  { label: "Does not repeat", value: "" },
  { label: "Daily", value: "FREQ=DAILY" },
  { label: "Weekly", value: "FREQ=WEEKLY" },
  { label: "Every weekday (Mon-Fri)", value: "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR" },
  { label: "Monthly", value: "FREQ=MONTHLY" },
  { label: "Yearly", value: "FREQ=YEARLY" },
]

const EVENT_TYPES = [
  { label: "Work", value: "work" },
  { label: "Personal", value: "personal" },
  { label: "Health", value: "health" },
  { label: "Travel", value: "travel" },
  { label: "Meal", value: "meal" },
  { label: "Social", value: "social" },
  { label: "Admin", value: "admin" },
  { label: "Other", value: "other" },
]

const IMPORTANCE_OPTIONS = [
  { label: "Low", value: "low" },
  { label: "Normal", value: "normal" },
  { label: "High", value: "high" },
  { label: "Critical", value: "critical" },
]

const REMINDER_OPTIONS = [
  { label: "No reminder", value: "" },
  { label: "At event time", value: "0" },
  { label: "5 minutes before", value: "5" },
  { label: "10 minutes before", value: "10" },
  { label: "15 minutes before", value: "15" },
  { label: "30 minutes before", value: "30" },
  { label: "1 hour before", value: "60" },
  { label: "2 hours before", value: "120" },
  { label: "1 day before", value: "1440" },
  { label: "Custom minutes", value: "custom" },
]

// Quick "Remind me" presets, measured in minutes before the event start.
const QUICK_REMINDER_PRESETS: { label: string; minutes: number }[] = [
  { label: "At event time", minutes: 0 },
  { label: "10 minutes before", minutes: 10 },
  { label: "1 hour before", minutes: 60 },
  { label: "1 day before", minutes: 1440 },
]

const VIEWS = [
  { label: "Week", value: "week" },
  { label: "Month", value: "month" },
  { label: "Year", value: "year" },
  { label: "Agenda", value: "agenda" },
] as const

type CalendarView = (typeof VIEWS)[number]["value"]
type WeekStart = "monday" | "sunday"

const WEEK_HOUR_HEIGHT = 56
const WEEK_HOUR_MIN = 36
const WEEK_HOUR_MAX = 96
const WEEK_SLOT_MINUTES = 15
const WEEK_TOTAL_MINUTES = 24 * 60
const DAY_DETAIL_DEFAULT_HEIGHT = 260
const DAY_DETAIL_MIN_HEIGHT = 40
const DAY_DETAIL_STORAGE_KEY = "odysseus.cal.detailH"

interface WeekDraft {
  mode: "create" | "move" | "resize"
  dayKey: string
  startMin: number
  endMin: number
  eventUid?: string
}

interface UndoAction {
  label: string
  run: () => Promise<void>
}

interface FormState {
  summary: string
  allDay: boolean
  start: string
  end: string
  location: string
  description: string
  recur: string
  customRrule: string
  calendarHref: string
  color: string
  eventType: string
  importance: string
  reminder: string
  reminderCustom: string
}

function pad(n: number): string {
  return String(n).padStart(2, "0")
}

function startOfDay(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate())
}

function addDays(d: Date, days: number): Date {
  const next = new Date(d)
  next.setDate(next.getDate() + days)
  return next
}

function addMonths(d: Date, months: number): Date {
  const next = new Date(d)
  next.setMonth(next.getMonth() + months)
  return next
}

function startOfWeek(d: Date, weekStart: WeekStart): Date {
  const day = d.getDay()
  const mondayOffset = day === 0 ? -6 : 1 - day
  const sundayOffset = -day
  return addDays(startOfDay(d), weekStart === "monday" ? mondayOffset : sundayOffset)
}

function parseDate(value?: string): Date | null {
  if (!value) return null
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    const [year, month, day] = value.split("-").map(Number)
    return new Date(year, month - 1, day)
  }
  const d = new Date(value)
  return isNaN(d.getTime()) ? null : d
}

function dateKey(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

function dateFromKey(key: string): Date {
  const [year, month, day] = key.split("-").map(Number)
  return new Date(year, month - 1, day)
}

function localInputForDayMinute(dayKey: string, minutes: number): string {
  const day = dateFromKey(dayKey)
  const d = new Date(day.getFullYear(), day.getMonth(), day.getDate(), 0, minutes)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function clamp(n: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, n))
}

// ISO 8601 week number: weeks start Monday; week 1 contains the year's first
// Thursday. Ported from the legacy calendar's _isoWeekNumber.
function isoWeekNumber(d: Date): number {
  const tgt = new Date(d.getFullYear(), d.getMonth(), d.getDate())
  tgt.setDate(tgt.getDate() + 3 - ((tgt.getDay() + 6) % 7))
  const yearStart = new Date(tgt.getFullYear(), 0, 1)
  return Math.ceil(((tgt.getTime() - yearStart.getTime()) / 86400000 + 1) / 7)
}

function roundToSlot(minutes: number): number {
  return Math.round(minutes / WEEK_SLOT_MINUTES) * WEEK_SLOT_MINUTES
}

function minutesOfDay(d: Date): number {
  return d.getHours() * 60 + d.getMinutes()
}

function minutesLabel(minutes: number): string {
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  return `${pad(h)}:${pad(m)}`
}

function pointerMinutesForHeight(clientY: number, grid: HTMLElement, hourHeight: number): number {
  const rect = grid.getBoundingClientRect()
  const y = clamp(clientY - rect.top, 0, grid.clientHeight)
  return clamp(roundToSlot((y / hourHeight) * 60), 0, WEEK_TOTAL_MINUTES)
}

function isCalBgImage(color?: string): boolean {
  return typeof color === "string" && color.startsWith("bg:")
}

function calBgImageUrl(color?: string): string {
  return isCalBgImage(color) ? (color || "").slice(3) : ""
}

function solidEventColor(color?: string, fallback = "var(--muted-foreground)"): string {
  return color && !isCalBgImage(color) ? color : fallback
}

function colorInputValue(color?: string): string {
  return color && !isCalBgImage(color) && /^#[0-9a-f]{6}$/i.test(color) ? color : "#5b8abf"
}

function calBgImageStyle(color?: string, overlay = "70%"): CSSProperties | undefined {
  const url = calBgImageUrl(color)
  if (!url) return undefined
  return {
    backgroundImage: `linear-gradient(color-mix(in srgb, var(--card) ${overlay}, transparent), color-mix(in srgb, var(--card) ${overlay}, transparent)), url(${JSON.stringify(url)})`,
    backgroundSize: "cover",
    backgroundPosition: "center",
  }
}

function eventIdentity(ev: CalEvent): string {
  return ev.uid
}

function eventMutationUid(ev: CalEvent): string {
  return ev.series_uid || ev.uid
}

function sameDay(a: Date, b: Date): boolean {
  return dateKey(a) === dateKey(b)
}

function sameMonth(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth()
}

function eventStart(ev: CalEvent): Date | null {
  return parseDate(ev.dtstart)
}

function eventEnd(ev: CalEvent): Date | null {
  return parseDate(ev.dtend) || eventStart(ev)
}

function eventOverlapsDay(ev: CalEvent, day: Date): boolean {
  const start = eventStart(ev)
  if (!start) return false
  let end = eventEnd(ev) || start
  if (end <= start) end = addDays(start, ev.all_day ? 1 : 0)
  const dayStart = startOfDay(day)
  const dayEnd = addDays(dayStart, 1)
  return start < dayEnd && end > dayStart
}

function isoToLocalInput(iso?: string): string {
  const d = parseDate(iso)
  if (!d) return ""
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function isoToDateInput(iso?: string): string {
  const d = parseDate(iso)
  if (!d) return (iso || "").slice(0, 10)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

function matchRecur(rrule?: string): string {
  const r = (rrule || "").trim().toUpperCase()
  if (!r) return ""
  const known = RECUR_OPTIONS.find((o) => o.value && o.value === r)
  return known ? known.value : "custom"
}

function eventTypeLabel(value?: string): string {
  return EVENT_TYPES.find((t) => t.value === value)?.label || value || ""
}

function importanceLabel(value?: string): string {
  return IMPORTANCE_OPTIONS.find((i) => i.value === value)?.label || value || "Normal"
}

function timeLabel(ev: CalEvent): string {
  if (ev.all_day) return "All day"
  const start = eventStart(ev)
  if (!start) return ""
  const end = eventEnd(ev)
  const fmt = { hour: "numeric", minute: "2-digit" } as const
  if (end && !sameDay(start, end)) return start.toLocaleString([], { month: "short", day: "numeric", ...fmt })
  if (end && end > start) return `${start.toLocaleTimeString([], fmt)} - ${end.toLocaleTimeString([], fmt)}`
  return start.toLocaleTimeString([], fmt)
}

function eventSearchText(ev: CalEvent): string {
  return [ev.summary, ev.title, ev.location, ev.description, ev.calendar, ev.event_type, ev.importance].join(" ").toLowerCase()
}

function weekTiming(ev: CalEvent, day: Date, draft?: WeekDraft | null): { startMin: number; endMin: number; draft: boolean } | null {
  const key = dateKey(day)
  if (draft?.eventUid === eventIdentity(ev) && draft.dayKey === key && (draft.mode === "move" || draft.mode === "resize")) {
    return { startMin: draft.startMin, endMin: draft.endMin, draft: true }
  }
  const start = eventStart(ev)
  if (!start) return null
  const end = eventEnd(ev) || new Date(start.getTime() + 60 * 60000)
  const dayStart = startOfDay(day)
  const dayEnd = addDays(dayStart, 1)
  if (end <= dayStart || start >= dayEnd) return null
  const clippedStart = new Date(Math.max(start.getTime(), dayStart.getTime()))
  const clippedEnd = new Date(Math.min(Math.max(end.getTime(), start.getTime() + WEEK_SLOT_MINUTES * 60000), dayEnd.getTime()))
  const startMin = minutesOfDay(clippedStart)
  const endMin = Math.max(startMin + WEEK_SLOT_MINUTES, clippedEnd >= dayEnd ? WEEK_TOTAL_MINUTES : minutesOfDay(clippedEnd))
  return { startMin, endMin, draft: false }
}

function weekRangeLabel(startMin: number, endMin: number): string {
  return `${minutesLabel(startMin)} - ${minutesLabel(endMin)}`
}

function eventToForm(ev: CalEvent, calendars: { href: string }[]): FormState {
  const recur = matchRecur(ev.rrule)
  return {
    summary: ev.summary || ev.title || "",
    allDay: !!ev.all_day,
    start: ev.all_day ? isoToDateInput(ev.dtstart) : isoToLocalInput(ev.dtstart),
    end: ev.all_day ? isoToDateInput(ev.dtend) : isoToLocalInput(ev.dtend),
    location: ev.location || "",
    description: ev.description || "",
    recur,
    customRrule: recur === "custom" ? ev.rrule || "" : "",
    calendarHref: ev.calendar_href || calendars[0]?.href || "",
    color: ev.color || "",
    eventType: ev.event_type || "",
    importance: ev.importance || "normal",
    reminder: "",
    reminderCustom: "",
  }
}

function emptyForm(defaultCal: string): FormState {
  return {
    summary: "",
    allDay: false,
    start: "",
    end: "",
    location: "",
    description: "",
    recur: "",
    customRrule: "",
    calendarHref: defaultCal,
    color: "",
    eventType: "",
    importance: "normal",
    reminder: "",
    reminderCustom: "",
  }
}

function rruleFor(f: FormState): string {
  if (f.recur === "custom") return f.customRrule.trim()
  return f.recur
}

function eventPayload(f: FormState): EventInput {
  return {
    summary: f.summary.trim(),
    dtstart: f.start,
    dtend: f.end || undefined,
    all_day: f.allDay,
    location: f.location.trim() || undefined,
    description: f.description.trim() || undefined,
    rrule: rruleFor(f) || undefined,
    calendar_href: f.calendarHref || undefined,
    color: f.color || undefined,
    event_type: f.eventType || undefined,
    importance: f.importance || "normal",
  }
}

function eventPatch(f: FormState): EventPatch {
  return {
    summary: f.summary.trim(),
    dtstart: f.start,
    dtend: f.end || undefined,
    all_day: f.allDay,
    location: f.location.trim(),
    description: f.description.trim(),
    rrule: rruleFor(f),
    color: f.color,
    event_type: f.eventType,
    importance: f.importance || "normal",
  }
}

function formEventStart(f: FormState): Date | null {
  if (!f.start) return null
  if (f.allDay) {
    const [year, month, day] = f.start.split("-").map(Number)
    if (!year || !month || !day) return null
    return new Date(year, month - 1, day, 9, 0, 0)
  }
  const d = new Date(f.start)
  return isNaN(d.getTime()) ? null : d
}

function reminderDue(f: FormState): { dueDate: string; eventStart: string } | null {
  if (!f.reminder) return null
  const start = formEventStart(f)
  if (!start) return null
  const minutes = f.reminder === "custom" ? Number(f.reminderCustom) : Number(f.reminder)
  if (!Number.isFinite(minutes) || minutes < 0) return null
  const due = new Date(start)
  due.setMinutes(due.getMinutes() - minutes)
  return { dueDate: due.toISOString(), eventStart: start.toISOString() }
}

function rangeForView(view: CalendarView, cursor: Date, weekStart: WeekStart): { start: Date; end: Date } {
  if (view === "week") {
    const start = startOfWeek(cursor, weekStart)
    return { start, end: addDays(start, 7) }
  }
  if (view === "month") {
    const first = new Date(cursor.getFullYear(), cursor.getMonth(), 1)
    const start = startOfWeek(first, weekStart)
    return { start, end: addDays(start, 42) }
  }
  if (view === "year") {
    const start = new Date(cursor.getFullYear(), 0, 1)
    return { start, end: new Date(cursor.getFullYear() + 1, 0, 1) }
  }
  const start = startOfDay(cursor)
  return { start, end: addDays(start, 90) }
}

function shiftCursor(cursor: Date, view: CalendarView, amount: number): Date {
  if (view === "week") return addDays(cursor, amount * 7)
  if (view === "year") return new Date(cursor.getFullYear() + amount, cursor.getMonth(), 1)
  if (view === "agenda") return addDays(cursor, amount * 30)
  return addMonths(cursor, amount)
}

function viewTitle(view: CalendarView, cursor: Date, weekStart: WeekStart): string {
  if (view === "week") {
    const start = startOfWeek(cursor, weekStart)
    const end = addDays(start, 6)
    return `${start.toLocaleDateString([], { month: "short", day: "numeric" })} - ${end.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" })}`
  }
  if (view === "month") return cursor.toLocaleDateString([], { month: "long", year: "numeric" })
  if (view === "year") return String(cursor.getFullYear())
  const end = addDays(startOfDay(cursor), 89)
  return `${cursor.toLocaleDateString([], { month: "short", day: "numeric" })} - ${end.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" })}`
}

function EventForm({
  mode,
  initial,
  calendars,
  pending,
  error,
  onCancel,
  onSubmit,
}: {
  mode: "create" | "edit"
  initial: FormState
  calendars: CalendarInfo[]
  pending: boolean
  error?: string
  onCancel: () => void
  onSubmit: (f: FormState) => void | Promise<void>
}) {
  const [f, setF] = useState<FormState>(initial)
  const [uploadingImage, setUploadingImage] = useState(false)
  const [imageError, setImageError] = useState("")
  const imageInputRef = useRef<HTMLInputElement>(null)
  const set = <K extends keyof FormState>(k: K, v: FormState[K]) => setF((p) => ({ ...p, [k]: v }))

  const chooseImage = async (file?: File) => {
    if (!file) return
    setImageError("")
    setUploadingImage(true)
    try {
      const url = await uploadCalendarBackgroundImage(file)
      set("color", `bg:${url}`)
    } catch (e) {
      setImageError(e instanceof Error ? e.message : "Couldn't upload image")
    } finally {
      setUploadingImage(false)
      if (imageInputRef.current) imageInputRef.current.value = ""
    }
  }

  return (
    <div className="space-y-3 rounded-lg border bg-card p-3" style={calBgImageStyle(f.color, "68%")}>
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold">{mode === "create" ? "New event" : "Edit event"}</span>
        <button onClick={onCancel} title="Close" className="text-muted-foreground hover:text-foreground">
          <X className="size-4" />
        </button>
      </div>
      <input value={f.summary} onChange={(e) => set("summary", e.target.value)} placeholder="Title" className={inp} />
      <label className="flex items-center gap-2 text-sm text-muted-foreground">
        <input type="checkbox" checked={f.allDay} onChange={(e) => set("allDay", e.target.checked)} className="size-4" />
        All day
      </label>
      <div className="grid gap-2 sm:grid-cols-2">
        <div>
          <label className="mb-1 block text-xs text-muted-foreground">Start</label>
          <input type={f.allDay ? "date" : "datetime-local"} value={f.start} onChange={(e) => set("start", e.target.value)} className={inp} />
        </div>
        <div>
          <label className="mb-1 block text-xs text-muted-foreground">End</label>
          <input type={f.allDay ? "date" : "datetime-local"} value={f.end} onChange={(e) => set("end", e.target.value)} className={inp} />
        </div>
      </div>
      <input value={f.location} onChange={(e) => set("location", e.target.value)} placeholder="Location (optional)" className={inp} />
      <textarea
        value={f.description}
        onChange={(e) => set("description", e.target.value)}
        placeholder="Description (optional)"
        rows={3}
        className="w-full resize-none rounded-md border bg-background px-3 py-2 text-sm outline-none focus-visible:border-ring"
      />
      <div className="grid gap-2 md:grid-cols-3">
        {mode === "create" && (
          <div>
            <label className="mb-1 block text-xs text-muted-foreground">Calendar</label>
            <select value={f.calendarHref} onChange={(e) => set("calendarHref", e.target.value)} className={inp}>
              {calendars.map((c) => (
                <option key={c.href} value={c.href}>{c.name}</option>
              ))}
            </select>
          </div>
        )}
        <div>
          <label className="mb-1 block text-xs text-muted-foreground">Repeat</label>
          <select value={f.recur} onChange={(e) => set("recur", e.target.value)} className={inp}>
            {RECUR_OPTIONS.map((o) => <option key={o.value || "none"} value={o.value}>{o.label}</option>)}
            {f.recur === "custom" && <option value="custom">Custom RRULE</option>}
          </select>
        </div>
        <div>
          <label className="mb-1 block text-xs text-muted-foreground">Type</label>
          <select value={f.eventType} onChange={(e) => set("eventType", e.target.value)} className={inp}>
            <option value="">No type</option>
            {EVENT_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
          </select>
        </div>
        <div>
          <label className="mb-1 block text-xs text-muted-foreground">Importance</label>
          <select value={f.importance} onChange={(e) => set("importance", e.target.value)} className={inp}>
            {IMPORTANCE_OPTIONS.map((i) => <option key={i.value} value={i.value}>{i.label}</option>)}
          </select>
        </div>
      </div>
      {f.recur === "custom" && (
        <input value={f.customRrule} onChange={(e) => set("customRrule", e.target.value)} placeholder="FREQ=WEEKLY;INTERVAL=2" className={inp} />
      )}
      <div className="grid gap-2 md:grid-cols-[minmax(0,1fr)_9rem_minmax(0,1fr)]">
        <div>
          <label className="mb-1 block text-xs text-muted-foreground">Reminder</label>
          <select value={f.reminder} onChange={(e) => set("reminder", e.target.value)} className={inp}>
            {REMINDER_OPTIONS.map((r) => <option key={r.value || "none"} value={r.value}>{r.label}</option>)}
          </select>
        </div>
        <div>
          <label className="mb-1 block text-xs text-muted-foreground">Color</label>
          <div className="flex gap-1.5">
            <input
              type="color"
              value={colorInputValue(f.color)}
              onChange={(e) => set("color", e.target.value)}
              aria-label="Event color"
              title="Event color"
              className="h-9 min-w-0 flex-1 cursor-pointer rounded-md border bg-background"
            />
            <input
              ref={imageInputRef}
              type="file"
              accept="image/*"
              className="hidden"
              onChange={(e) => void chooseImage(e.target.files?.[0])}
            />
            <button
              type="button"
              onClick={() => imageInputRef.current?.click()}
              disabled={uploadingImage}
              title="Set event background image"
              aria-label="Set event background image"
              className={cn("grid h-9 w-9 shrink-0 place-items-center rounded-md border bg-background text-muted-foreground hover:bg-accent hover:text-foreground", isCalBgImage(f.color) && "border-primary text-primary")}
            >
              {uploadingImage ? <RefreshCw className="size-4 animate-spin" /> : <Image className="size-4" />}
            </button>
            {isCalBgImage(f.color) && (
              <button
                type="button"
                onClick={() => set("color", "")}
                title="Remove event background image"
                aria-label="Remove event background image"
                className="grid h-9 w-9 shrink-0 place-items-center rounded-md border bg-background text-muted-foreground hover:bg-accent hover:text-foreground"
              >
                <X className="size-4" />
              </button>
            )}
          </div>
        </div>
        {f.reminder === "custom" && (
          <div>
            <label className="mb-1 block text-xs text-muted-foreground">Minutes before</label>
            <input type="number" min={0} value={f.reminderCustom} onChange={(e) => set("reminderCustom", e.target.value)} className={inp} />
          </div>
        )}
      </div>
      {(error || imageError) && <p className="text-xs text-destructive">{error || imageError}</p>}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={onCancel}>Cancel</Button>
        <Button size="sm" disabled={pending || uploadingImage} onClick={() => void onSubmit(f)}>
          {pending ? "Saving..." : mode === "create" ? "Create" : "Save"}
        </Button>
      </div>
    </div>
  )
}

function NewCalendar({ pending, onCancel, onSubmit }: { pending: boolean; onCancel: () => void; onSubmit: (name: string, color: string) => void }) {
  const [name, setName] = useState("")
  const [color, setColor] = useState("#5b8abf")
  return (
    <div className="mb-3 space-y-2 border-b pb-3">
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold">New calendar</span>
        <button onClick={onCancel} title="Close" className="text-muted-foreground hover:text-foreground">
          <X className="size-4" />
        </button>
      </div>
      <div className="flex gap-2">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Calendar name" className={inp} />
        <input type="color" value={color} onChange={(e) => setColor(e.target.value)} title="Color" className="h-9 w-12 shrink-0 cursor-pointer rounded-md border bg-background" />
      </div>
      <div className="flex justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={onCancel}>Cancel</Button>
        <Button size="sm" disabled={pending || !name.trim()} onClick={() => onSubmit(name.trim(), color)}>
          {pending ? "Creating..." : "Create"}
        </Button>
      </div>
    </div>
  )
}

function CalendarRow({
  calendar,
  active,
  deleteDisabled,
  pending,
  onFilter,
  onSave,
  onDelete,
  onExport,
}: {
  calendar: CalendarInfo
  active: boolean
  deleteDisabled: boolean
  pending: boolean
  onFilter: () => void
  onSave: (name: string, color: string) => void
  onDelete: () => void
  onExport: () => void
}) {
  const [name, setName] = useState(calendar.name)
  const [color, setColor] = useState(calendar.color || "#5b8abf")
  const changed = name.trim() !== calendar.name || color !== (calendar.color || "#5b8abf")

  return (
    <div className="grid gap-2 rounded-md border bg-background p-2 text-sm md:grid-cols-[1fr_8rem_auto] md:items-center">
      <div className="flex min-w-0 items-center gap-2">
        <span className="size-2.5 shrink-0 rounded-full" style={{ background: color }} />
        <input value={name} onChange={(e) => setName(e.target.value)} aria-label={`${calendar.name} name`} className="h-8 min-w-0 flex-1 rounded-md border bg-card px-2 text-sm outline-none focus-visible:border-ring" />
      </div>
      <div className="flex items-center gap-2">
        <input type="color" value={color} onChange={(e) => setColor(e.target.value)} aria-label={`${calendar.name} color`} className="h-8 w-10 shrink-0 cursor-pointer rounded-md border bg-card" />
        <span className="truncate text-xs text-muted-foreground">{calendar.source || "local"}</span>
      </div>
      <div className="flex justify-end gap-1.5">
        <Button size="sm" variant={active ? "secondary" : "ghost"} onClick={onFilter}>{active ? "Showing" : "Filter"}</Button>
        <Button size="sm" variant="ghost" disabled={pending || !changed || !name.trim()} onClick={() => onSave(name.trim(), color)}>Save</Button>
        <button onClick={onExport} title="Export .ics" className="rounded-md p-2 text-muted-foreground hover:bg-accent hover:text-foreground">
          <Download className="size-4" />
        </button>
        <button disabled={deleteDisabled || pending} onClick={onDelete} title="Delete calendar" className="rounded-md p-2 text-muted-foreground hover:bg-accent hover:text-destructive disabled:pointer-events-none disabled:opacity-50">
          <Trash2 className="size-4" />
        </button>
      </div>
    </div>
  )
}

function quickReminderDue(ev: CalEvent, minutes: number): string | null {
  const start = eventStart(ev)
  if (!start) return null
  const due = new Date(start)
  due.setMinutes(due.getMinutes() - minutes)
  return due.toISOString()
}

// Compact ⋮ menu on event tiles for setting a reminder without opening the
// full editor. Reuses createCalendarReminder with a handful of preset offsets.
function QuickReminderMenu({
  ev,
  align = "right",
  className,
  onResult,
}: {
  ev: CalEvent
  align?: "left" | "right"
  className?: string
  onResult: (message: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const start = eventStart(ev)
  const title = ev.summary || ev.title || "(untitled)"

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false) }
    document.addEventListener("mousedown", onDown)
    document.addEventListener("keydown", onKey)
    return () => {
      document.removeEventListener("mousedown", onDown)
      document.removeEventListener("keydown", onKey)
    }
  }, [open])

  const remind = async (minutes: number, label: string) => {
    const dueDate = quickReminderDue(ev, minutes)
    if (!dueDate) { onResult("Can't set a reminder without an event time."); setOpen(false); return }
    setBusy(true)
    try {
      await createCalendarReminder({
        title: `Reminder: ${title}`,
        content: ev.location ? `${title} at ${ev.location}` : title,
        dueDate,
        eventStart: start ? start.toISOString() : undefined,
        color: ev.color && !isCalBgImage(ev.color) ? ev.color : undefined,
      })
      onResult(`Reminder set ${label.toLowerCase()}.`)
    } catch (e) {
      onResult(e instanceof Error ? e.message : "Couldn't create reminder")
    } finally {
      setBusy(false)
      setOpen(false)
    }
  }

  return (
    <div ref={ref} className={cn("relative", className)}>
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); setOpen((o) => !o) }}
        title="Set a reminder"
        aria-label="Set a reminder"
        aria-haspopup="menu"
        aria-expanded={open}
        className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
      >
        <MoreVertical className="size-4" />
      </button>
      {open && (
        <div
          role="menu"
          onClick={(e) => e.stopPropagation()}
          className={cn(
            "absolute top-full z-30 mt-1 w-44 overflow-hidden rounded-md border bg-popover py-1 text-sm shadow-md",
            align === "right" ? "right-0" : "left-0",
          )}
        >
          <div className="flex items-center gap-1.5 px-3 py-1 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
            <Bell className="size-3" />Remind me
          </div>
          {QUICK_REMINDER_PRESETS.map((p) => (
            <button
              key={p.minutes}
              role="menuitem"
              type="button"
              disabled={busy || !start}
              onClick={() => void remind(p.minutes, p.label)}
              className="block w-full px-3 py-1.5 text-left hover:bg-accent disabled:pointer-events-none disabled:opacity-50"
            >
              {p.label}
            </button>
          ))}
          {!start && <p className="px-3 py-1 text-[11px] text-muted-foreground">No event time</p>}
        </div>
      )}
    </div>
  )
}

function CookbookTaskLink({ ev, compact }: { ev: CalEvent; compact?: boolean }) {
  const navigate = useNavigate()
  const taskId = cookbookTaskId(ev.description)
  if (!taskId) return null
  return (
    <button
      type="button"
      onClick={(e) => { e.stopPropagation(); navigate("/tasks") }}
      title="Open in Tasks"
      className={cn(
        "inline-flex items-center gap-1 rounded bg-muted px-1.5 py-0.5 text-muted-foreground hover:text-foreground",
        compact ? "text-[11px]" : "text-xs",
      )}
    >
      <ExternalLink className="size-3" />Open in Tasks
    </button>
  )
}

function EventCard({ ev, compact, onEdit, onDelete, onReminder }: { ev: CalEvent; compact?: boolean; onEdit: () => void; onDelete: () => void; onReminder: (message: string) => void }) {
  const importance = (ev.importance || "normal").toLowerCase()
  const isImportant = importance === "high" || importance === "critical"
  const title = ev.summary || ev.title || "(untitled)"
  return (
    <div className={cn("group flex min-w-0 gap-2 rounded-md border bg-card p-2", compact ? "text-xs" : "p-3")} style={calBgImageStyle(ev.color)}>
      <span className={cn("w-1 shrink-0 rounded-full", compact ? "h-auto" : "h-10")} style={{ background: solidEventColor(ev.color) }} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className={cn("truncate font-medium", compact ? "text-xs" : "text-sm")}>{title}</span>
          {(ev.is_recurrence || ev.rrule) && <RefreshCw className="size-3 shrink-0 text-muted-foreground" />}
        </div>
        <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
          <span>{timeLabel(ev)}</span>
          {ev.event_type && (
            <span className="inline-flex items-center gap-1 rounded bg-muted px-1.5 py-0.5">
              <Tag className="size-3" />
              {eventTypeLabel(ev.event_type)}
            </span>
          )}
          {isImportant && (
            <span className="inline-flex items-center gap-1 rounded bg-destructive/10 px-1.5 py-0.5 text-destructive">
              <AlertTriangle className="size-3" />
              {importanceLabel(importance)}
            </span>
          )}
          <CookbookTaskLink ev={ev} compact={compact} />
        </div>
        {!compact && ev.location && (
          <a
            href={`https://maps.google.com/?q=${encodeURIComponent(ev.location)}`}
            target="_blank"
            rel="noreferrer"
            className="mt-1 block truncate text-xs text-muted-foreground underline-offset-2 hover:underline"
          >
            {ev.location}
          </a>
        )}
        {!compact && ev.description && <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{ev.description}</p>}
      </div>
      <div className="flex shrink-0 items-center gap-1 opacity-100 md:opacity-0 md:transition-opacity md:group-hover:opacity-100">
        {!compact && <QuickReminderMenu ev={ev} onResult={onReminder} />}
        <button onClick={onEdit} title="Edit" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground">
          <Pencil className="size-4" />
        </button>
        <button onClick={onDelete} title="Delete" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-destructive">
          <Trash2 className="size-4" />
        </button>
      </div>
    </div>
  )
}

export function CalendarRoute() {
  const [view, setView] = useState<CalendarView>("month")
  const [cursor, setCursor] = useState(() => startOfDay(new Date()))
  const [weekStart, setWeekStart] = useState<WeekStart>(() => {
    if (typeof window === "undefined") return "monday"
    return window.localStorage.getItem("calendar-week-start") === "sunday" ? "sunday" : "monday"
  })
  const [weekHourHeight, setWeekHourHeight] = useState(() => {
    if (typeof window === "undefined") return WEEK_HOUR_HEIGHT
    const saved = Number(window.localStorage.getItem("calendar-week-hour-height"))
    return Number.isFinite(saved) ? clamp(saved, WEEK_HOUR_MIN, WEEK_HOUR_MAX) : WEEK_HOUR_HEIGHT
  })
  const [selectedDay, setSelectedDay] = useState(() => dateKey(new Date()))
  const [dayDetailQuery, setDayDetailQuery] = useState("")
  const [dayDetailHeight, setDayDetailHeight] = useState(() => {
    if (typeof window === "undefined") return DAY_DETAIL_DEFAULT_HEIGHT
    const saved = Number(window.localStorage.getItem(DAY_DETAIL_STORAGE_KEY))
    return Number.isFinite(saved) && saved > DAY_DETAIL_MIN_HEIGHT ? saved : DAY_DETAIL_DEFAULT_HEIGHT
  })
  const range = useMemo(() => rangeForView(view, cursor, weekStart), [view, cursor, weekStart])
  const { data: events } = useEvents(range.start.toISOString(), range.end.toISOString())
  const { data: calendars } = useCalendars()
  const qa = useQuickAddEvent()
  const { create, update, remove } = useEventMutations()
  const calMut = useCalendarMutations()
  const sync = useSync()
  const importIcs = useImportIcs()
  const fileRef = useRef<HTMLInputElement>(null)

  const cals = calendars || []
  const [quickText, setQuickText] = useState("")
  const [filter, setFilter] = useState("")
  const [query, setQuery] = useState("")
  const [typeFilter, setTypeFilter] = useState("")
  const [importantOnly, setImportantOnly] = useState(false)
  const [creating, setCreating] = useState(false)
  const [createInitial, setCreateInitial] = useState<FormState | null>(null)
  const [editEvent, setEditEvent] = useState<CalEvent | null>(null)
  const [formErr, setFormErr] = useState("")
  const [newCal, setNewCal] = useState(false)
  const [notice, setNotice] = useState("")
  const [weekDraft, setWeekDraft] = useState<WeekDraft | null>(null)
  const [undoAction, setUndoAction] = useState<UndoAction | null>(null)
  const suppressWeekClick = useRef(false)
  const weekScrollRef = useRef<HTMLDivElement | null>(null)
  const weekScrollTop = useRef<number | null>(null)
  const weekAutoScrolled = useRef(false)

  const weekGridHeight = (WEEK_TOTAL_MINUTES / 60) * weekHourHeight

  useEffect(() => {
    if (view !== "week") return
    const node = weekScrollRef.current
    if (!node) return
    requestAnimationFrame(() => {
      if (weekScrollTop.current != null) {
        node.scrollTop = weekScrollTop.current
      } else if (!weekAutoScrolled.current) {
        node.scrollTop = 7 * weekHourHeight
        weekAutoScrolled.current = true
      }
    })
  }, [cursor, view, weekHourHeight, weekStart])

  const dayNames = useMemo(() => {
    const base = weekStart === "monday" ? ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] : ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    return base
  }, [weekStart])

  const monthDays = useMemo(() => {
    const first = new Date(cursor.getFullYear(), cursor.getMonth(), 1)
    const gridStart = startOfWeek(first, weekStart)
    return Array.from({ length: 42 }, (_, i) => addDays(gridStart, i))
  }, [cursor, weekStart])

  const weekDays = useMemo(() => {
    const start = startOfWeek(cursor, weekStart)
    return Array.from({ length: 7 }, (_, i) => addDays(start, i))
  }, [cursor, weekStart])

  const add = () => {
    if (!quickText.trim()) return
    setNotice("")
    qa.mutate(quickText, { onSuccess: () => setQuickText("") })
  }

  const maybeCreateReminder = async (f: FormState): Promise<boolean> => {
    const due = reminderDue(f)
    if (!due) return false
    await createCalendarReminder({
      title: `Reminder: ${f.summary.trim()}`,
      content: f.location.trim() ? `${f.summary.trim()} at ${f.location.trim()}` : f.summary.trim(),
      dueDate: due.dueDate,
      eventStart: due.eventStart,
      color: f.color || undefined,
    })
    return true
  }

  const submitCreate = async (f: FormState) => {
    if (!f.summary.trim()) { setFormErr("Title is required"); return }
    if (!f.start) { setFormErr("Start is required"); return }
    if (f.reminder === "custom" && (!f.reminderCustom || Number(f.reminderCustom) < 0)) { setFormErr("Reminder minutes must be 0 or greater"); return }
    setFormErr("")
    setNotice("")
    try {
      await create.mutateAsync(eventPayload(f))
    } catch (e) {
      setFormErr(e instanceof Error ? e.message : "Failed")
      return
    }
    try {
      const madeReminder = await maybeCreateReminder(f)
      setNotice(madeReminder ? "Event saved and reminder added." : "Event saved.")
    } catch (e) {
      setNotice(e instanceof Error ? `Event saved, but ${e.message.toLowerCase()}.` : "Event saved, but reminder creation failed.")
    }
    setCreateInitial(null)
    setCreating(false)
  }

  const submitEdit = async (uid: string, f: FormState) => {
    if (!f.summary.trim()) { setFormErr("Title is required"); return }
    if (!f.start) { setFormErr("Start is required"); return }
    if (f.reminder === "custom" && (!f.reminderCustom || Number(f.reminderCustom) < 0)) { setFormErr("Reminder minutes must be 0 or greater"); return }
    setFormErr("")
    setNotice("")
    try {
      await update.mutateAsync({ uid, ...eventPatch(f) })
    } catch (e) {
      setFormErr(e instanceof Error ? e.message : "Failed")
      return
    }
    try {
      const madeReminder = await maybeCreateReminder(f)
      setNotice(madeReminder ? "Event updated and reminder added." : "Event updated.")
    } catch (e) {
      setNotice(e instanceof Error ? `Event updated, but ${e.message.toLowerCase()}.` : "Event updated, but reminder creation failed.")
    }
    setEditEvent(null)
  }

  const runSync = () => {
    setNotice("")
    sync.mutate("pull", {
      onSuccess: (r) => setNotice(r.errors && r.errors.length ? `Sync: ${r.errors[0]}` : `Synced - ${r.events ?? 0} event(s) from ${r.calendars ?? 0} calendar(s)`),
      onError: (e) => setNotice(e instanceof Error ? e.message : "Sync failed"),
    })
  }

  const onPickFile = (file: File) => {
    setNotice("")
    importIcs.mutate({ file }, {
      onSuccess: (r) => setNotice(`Imported ${r.imported ?? 0} event(s)${r.skipped ? `, skipped ${r.skipped}` : ""} into ${r.calendar ?? "Imported"}`),
      onError: (e) => setNotice(e instanceof Error ? e.message : "Import failed"),
    })
  }

  const doExport = (calId: string, name: string) => {
    exportIcs(calId, name).catch((e) => setNotice(e instanceof Error ? e.message : "Export failed"))
  }

  const setStoredWeekStart = (value: WeekStart) => {
    setWeekStart(value)
    window.localStorage.setItem("calendar-week-start", value)
  }

  const setStoredWeekHourHeight = (next: number) => {
    const clamped = clamp(Math.round(next), WEEK_HOUR_MIN, WEEK_HOUR_MAX)
    const currentTopHour = weekScrollRef.current ? weekScrollRef.current.scrollTop / weekHourHeight : null
    weekScrollTop.current = currentTopHour != null ? currentTopHour * clamped : weekScrollTop.current
    setWeekHourHeight(clamped)
    window.localStorage.setItem("calendar-week-hour-height", String(clamped))
  }

  // Week-view zoom via keyboard (+/-/0) and Ctrl/Cmd+wheel, matching the legacy
  // calendar's hour-row zoom. Ignored while typing in a field. Declared after
  // setStoredWeekHourHeight so the effect deps don't hit a TDZ on the const.
  useEffect(() => {
    if (view !== "week") return
    const isTyping = () => {
      const el = document.activeElement
      return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || (el as HTMLElement).isContentEditable)
    }
    const onKey = (e: KeyboardEvent) => {
      if (isTyping() || e.metaKey || e.ctrlKey || e.altKey) return
      if (e.key === "+" || e.key === "=") { e.preventDefault(); setStoredWeekHourHeight(weekHourHeight + 8) }
      else if (e.key === "-" || e.key === "_") { e.preventDefault(); setStoredWeekHourHeight(weekHourHeight - 8) }
      else if (e.key === "0") { e.preventDefault(); setStoredWeekHourHeight(WEEK_HOUR_HEIGHT) }
    }
    const onWheel = (e: WheelEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return
      const grid = weekScrollRef.current
      if (!grid || !grid.contains(e.target as Node)) return
      e.preventDefault()
      setStoredWeekHourHeight(weekHourHeight + (e.deltaY < 0 ? 8 : -8))
    }
    window.addEventListener("keydown", onKey)
    // passive:false so preventDefault can stop the page from pinch-zooming.
    window.addEventListener("wheel", onWheel, { passive: false })
    return () => {
      window.removeEventListener("keydown", onKey)
      window.removeEventListener("wheel", onWheel)
    }
    // setStoredWeekHourHeight is stable enough; it reads weekHourHeight, which is
    // already a dep, so the listeners always act on the current height.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, weekHourHeight])

  const setStoredDayDetailHeight = (next: number) => {
    const viewportHeight = typeof window === "undefined" ? 720 : window.visualViewport?.height || window.innerHeight
    const maxHeight = Math.max(DAY_DETAIL_MIN_HEIGHT, viewportHeight - 24)
    const clamped = clamp(Math.round(next), DAY_DETAIL_MIN_HEIGHT, maxHeight)
    setDayDetailHeight(clamped)
    if (typeof window !== "undefined") window.localStorage.setItem(DAY_DETAIL_STORAGE_KEY, String(clamped))
  }

  const filterBase = useMemo(() => {
    return (events || []).filter((ev) => {
      if (filter && ev.calendar_href !== filter) return false
      if (typeFilter && (ev.event_type || "").toLowerCase() !== typeFilter) return false
      const importance = (ev.importance || "normal").toLowerCase()
      if (importantOnly && importance !== "high" && importance !== "critical") return false
      return true
    })
  }, [events, filter, importantOnly, typeFilter])

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return filterBase
    return filterBase.filter((ev) => eventSearchText(ev).includes(q))
  }, [filterBase, query])

  const sorted = useMemo(
    () => [...visible].sort((a, b) => (a.dtstart || "").localeCompare(b.dtstart || "")),
    [visible],
  )

  const groups = useMemo(() => {
    const byDay = new Map<string, { label: string; date: Date | null; events: CalEvent[] }>()
    for (const ev of sorted) {
      const d = eventStart(ev)
      const key = d ? dateKey(d) : "undated"
      if (!byDay.has(key)) {
        byDay.set(key, {
          label: d ? d.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" }) : "Undated",
          date: d,
          events: [],
        })
      }
      byDay.get(key)!.events.push(ev)
    }
    return Array.from(byDay.values()).sort((a, b) => {
      if (!a.date) return 1
      if (!b.date) return -1
      return a.date.getTime() - b.date.getTime()
    })
  }, [sorted])

  const eventsForDay = (day: Date): CalEvent[] => sorted.filter((ev) => eventOverlapsDay(ev, day))
  const selectedDayDate = selectedDay ? dateFromKey(selectedDay) : null
  const selectedDayLabel = selectedDayDate ? selectedDayDate.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric", year: "numeric" }) : ""
  const selectedDayIsToday = selectedDayDate ? sameDay(selectedDayDate, new Date()) : false
  const showDayDetail = !!selectedDay && (view === "month" || view === "week")
  const dayDetailSearch = dayDetailQuery.trim().toLowerCase()
  const dayDetailEvents = useMemo(() => {
    if (!selectedDay) return []
    const source = dayDetailSearch ? filterBase : visible
    const list = dayDetailSearch
      ? source.filter((ev) => eventSearchText(ev).includes(dayDetailSearch))
      : source.filter((ev) => selectedDayDate && eventOverlapsDay(ev, selectedDayDate))
    return [...list].sort((a, b) => (a.dtstart || "").localeCompare(b.dtstart || ""))
  }, [dayDetailSearch, filterBase, selectedDay, selectedDayDate, visible])

  const openEdit = (ev: CalEvent) => {
    setCreating(false)
    setFormErr("")
    setEditEvent(ev)
  }

  const deleteEvent = (ev: CalEvent) => {
    if (confirm("Delete this event?")) remove.mutate(ev.series_uid || ev.uid)
  }

  const renderEvent = (ev: CalEvent, compact = false) => (
    <EventCard key={ev.uid} ev={ev} compact={compact} onEdit={() => openEdit(ev)} onDelete={() => deleteEvent(ev)} onReminder={setNotice} />
  )

  const runUndo = async () => {
    const action = undoAction
    if (!action) return
    setUndoAction(null)
    setNotice("")
    try {
      await action.run()
      setNotice(`${action.label} undone.`)
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "Undo failed")
    }
  }

  const showUndo = (label: string, run: () => Promise<void>) => {
    setUndoAction({ label, run })
  }

  const nearestWeekGrid = (clientX: number): HTMLElement | null => {
    const grids = Array.from(document.querySelectorAll<HTMLElement>("[data-week-grid='true']"))
    if (grids.length === 0) return null
    let best = grids[0]
    let bestDistance = Infinity
    for (const grid of grids) {
      const rect = grid.getBoundingClientRect()
      if (clientX >= rect.left && clientX <= rect.right) return grid
      const center = (rect.left + rect.right) / 2
      const distance = Math.abs(clientX - center)
      if (distance < bestDistance) {
        best = grid
        bestDistance = distance
      }
    }
    return best
  }

  const weekEventsForDay = (day: Date): CalEvent[] => {
    let dayEvents = eventsForDay(day)
    if (weekDraft?.eventUid && (weekDraft.mode === "move" || weekDraft.mode === "resize")) {
      const active = sorted.find((ev) => eventIdentity(ev) === weekDraft.eventUid)
      if (active && !active.all_day) {
        dayEvents = dayEvents.filter((ev) => eventIdentity(ev) !== weekDraft.eventUid)
        if (dateKey(day) === weekDraft.dayKey) dayEvents = [...dayEvents, active]
      }
    }
    return dayEvents.sort((a, b) => {
      const ta = weekTiming(a, day, weekDraft)?.startMin ?? 0
      const tb = weekTiming(b, day, weekDraft)?.startMin ?? 0
      return ta - tb
    })
  }

  const openCreateForSlot = (dayKey: string, startMin: number, endMin: number) => {
    const next = {
      ...emptyForm(cals[0]?.href || ""),
      start: localInputForDayMinute(dayKey, startMin),
      end: localInputForDayMinute(dayKey, endMin),
    }
    setSelectedDay(dayKey)
    setCreateInitial(next)
    setEditEvent(null)
    setFormErr("")
    setCreating(true)
  }

  const openCreateForDay = (dayKey: string) => {
    openCreateForSlot(dayKey, 9 * 60, 10 * 60)
  }

  const selectDay = (day: Date, moveCursor = false) => {
    const key = dateKey(day)
    if (moveCursor) setCursor(day)
    if (selectedDay === key) {
      openCreateForDay(key)
      return
    }
    setSelectedDay(key)
  }

  const moveCursor = (amount: number) => {
    const next = shiftCursor(cursor, view, amount)
    setCursor(next)
    setSelectedDay(view === "month" || view === "week" ? dateKey(next) : "")
  }

  const goToday = () => {
    const today = startOfDay(new Date())
    setCursor(today)
    setSelectedDay(dateKey(today))
  }

  const changeView = (next: CalendarView) => {
    setView(next)
    setDayDetailQuery("")
    setSelectedDay("")
  }

  const startDayDetailResize = (e: ReactMouseEvent<HTMLDivElement>) => {
    e.preventDefault()
    const startY = e.clientY
    const startH = dayDetailHeight
    let currentH = dayDetailHeight

    const onMove = (move: MouseEvent) => {
      const viewportHeight = window.visualViewport?.height || window.innerHeight
      const maxHeight = Math.max(DAY_DETAIL_MIN_HEIGHT, viewportHeight - 24)
      currentH = clamp(Math.round(startH + (startY - move.clientY)), DAY_DETAIL_MIN_HEIGHT, maxHeight)
      setDayDetailHeight(currentH)
    }

    const onUp = () => {
      document.removeEventListener("mousemove", onMove)
      document.removeEventListener("mouseup", onUp)
      window.localStorage.setItem(DAY_DETAIL_STORAGE_KEY, String(currentH))
    }

    document.addEventListener("mousemove", onMove)
    document.addEventListener("mouseup", onUp)
  }

  const resetDayDetailHeight = () => {
    setDayDetailHeight(DAY_DETAIL_DEFAULT_HEIGHT)
    window.localStorage.removeItem(DAY_DETAIL_STORAGE_KEY)
  }

  const resizeDayDetailByKey = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    if (e.key === "ArrowUp") {
      e.preventDefault()
      setStoredDayDetailHeight(dayDetailHeight + 24)
    } else if (e.key === "ArrowDown") {
      e.preventDefault()
      setStoredDayDetailHeight(dayDetailHeight - 24)
    } else if (e.key === "Home") {
      e.preventDefault()
      setStoredDayDetailHeight(DAY_DETAIL_MIN_HEIGHT)
    } else if (e.key === "End") {
      e.preventDefault()
      setStoredDayDetailHeight(window.innerHeight - 24)
    }
  }

  const startWeekCreateDrag = (e: ReactMouseEvent<HTMLDivElement>, day: Date) => {
    if (e.button !== 0) return
    if ((e.target as HTMLElement).closest("[data-week-event='true']")) return
    e.preventDefault()
    const grid = e.currentTarget
    const dayKey = dateKey(day)
    const initialMin = clamp(pointerMinutesForHeight(e.clientY, grid, weekHourHeight), 0, WEEK_TOTAL_MINUTES - WEEK_SLOT_MINUTES)
    let startMin = initialMin
    let endMin = initialMin + WEEK_SLOT_MINUTES
    setWeekDraft({ mode: "create", dayKey, startMin, endMin })

    const onMove = (move: MouseEvent) => {
      const pointer = pointerMinutesForHeight(move.clientY, grid, weekHourHeight)
      startMin = Math.min(initialMin, pointer)
      endMin = Math.max(initialMin, pointer)
      if (endMin === startMin) endMin = startMin + WEEK_SLOT_MINUTES
      if (endMin - startMin < WEEK_SLOT_MINUTES) endMin = startMin + WEEK_SLOT_MINUTES
      startMin = clamp(startMin, 0, WEEK_TOTAL_MINUTES - WEEK_SLOT_MINUTES)
      endMin = clamp(endMin, startMin + WEEK_SLOT_MINUTES, WEEK_TOTAL_MINUTES)
      setWeekDraft({ mode: "create", dayKey, startMin, endMin })
    }

    const onUp = () => {
      document.removeEventListener("mousemove", onMove)
      document.removeEventListener("mouseup", onUp)
      setWeekDraft(null)
      openCreateForSlot(dayKey, startMin, endMin)
    }

    document.addEventListener("mousemove", onMove)
    document.addEventListener("mouseup", onUp)
  }

  const updateWeekEvent = async (ev: CalEvent, patch: EventPatch, undoPatch: EventPatch, label: string) => {
    setNotice("")
    setUndoAction(null)
    try {
      await update.mutateAsync({ uid: eventMutationUid(ev), ...patch })
      setNotice(`${label}.`)
      showUndo(label, async () => {
        await update.mutateAsync({ uid: eventMutationUid(ev), ...undoPatch })
      })
    } catch (e) {
      setNotice(e instanceof Error ? e.message : `${label} failed`)
    }
  }

  const startWeekMoveDrag = (e: ReactMouseEvent<HTMLDivElement>, ev: CalEvent, day: Date) => {
    if (e.button !== 0) return
    if ((e.target as HTMLElement).closest("[data-week-resize='true']")) return
    const timing = weekTiming(ev, day, weekDraft)
    if (!timing) return
    e.preventDefault()
    e.stopPropagation()

    const block = e.currentTarget
    const rect = block.getBoundingClientRect()
    const grabOffsetMinutes = ((e.clientY - rect.top) / weekHourHeight) * 60
    const duration = Math.max(WEEK_SLOT_MINUTES, timing.endMin - timing.startMin)
    const initialX = e.clientX
    const initialY = e.clientY
    let moved = false
    let nextDayKey = dateKey(day)
    let nextStart = timing.startMin
    let nextEnd = timing.endMin

    const onMove = (move: MouseEvent) => {
      if (!moved && Math.abs(move.clientX - initialX) + Math.abs(move.clientY - initialY) < 4) return
      moved = true
      const grid = nearestWeekGrid(move.clientX)
      if (!grid) return
      const gridDayKey = grid.dataset.date
      if (!gridDayKey) return
      const pointer = pointerMinutesForHeight(move.clientY, grid, weekHourHeight)
      nextStart = clamp(roundToSlot(pointer - grabOffsetMinutes), 0, WEEK_TOTAL_MINUTES - duration)
      nextEnd = nextStart + duration
      nextDayKey = gridDayKey
      setWeekDraft({ mode: "move", eventUid: eventIdentity(ev), dayKey: nextDayKey, startMin: nextStart, endMin: nextEnd })
    }

    const onUp = () => {
      document.removeEventListener("mousemove", onMove)
      document.removeEventListener("mouseup", onUp)
      setWeekDraft(null)
      if (!moved) return
      suppressWeekClick.current = true
      const prevStart = ev.dtstart
      const prevEnd = ev.dtend
      const newStart = localInputForDayMinute(nextDayKey, nextStart)
      const newEnd = localInputForDayMinute(nextDayKey, nextEnd)
      if (newStart === prevStart && newEnd === prevEnd) return
      void updateWeekEvent(ev, { dtstart: newStart, dtend: newEnd }, { dtstart: prevStart, dtend: prevEnd }, "Moved event")
    }

    document.addEventListener("mousemove", onMove)
    document.addEventListener("mouseup", onUp)
  }

  const startWeekResizeDrag = (e: ReactMouseEvent<HTMLDivElement>, ev: CalEvent, day: Date) => {
    if (e.button !== 0) return
    const timing = weekTiming(ev, day, weekDraft)
    if (!timing) return
    e.preventDefault()
    e.stopPropagation()

    const grid = e.currentTarget.closest<HTMLElement>("[data-week-grid='true']")
    if (!grid) return
    const dayKey = dateKey(day)
    const initialY = e.clientY
    let resized = false
    let nextEnd = timing.endMin

    const onMove = (move: MouseEvent) => {
      if (!resized && Math.abs(move.clientY - initialY) < 4) return
      resized = true
      nextEnd = clamp(pointerMinutesForHeight(move.clientY, grid, weekHourHeight), timing.startMin + WEEK_SLOT_MINUTES, WEEK_TOTAL_MINUTES)
      setWeekDraft({ mode: "resize", eventUid: eventIdentity(ev), dayKey, startMin: timing.startMin, endMin: nextEnd })
    }

    const onUp = () => {
      document.removeEventListener("mousemove", onMove)
      document.removeEventListener("mouseup", onUp)
      setWeekDraft(null)
      if (!resized) return
      suppressWeekClick.current = true
      const prevEnd = ev.dtend
      const newEnd = localInputForDayMinute(dayKey, nextEnd)
      if (newEnd === prevEnd) return
      void updateWeekEvent(ev, { dtend: newEnd }, { dtend: prevEnd }, "Resized event")
    }

    document.addEventListener("mousemove", onMove)
    document.addEventListener("mouseup", onUp)
  }

  const settings = (
    <div className="rounded-lg border bg-card p-3">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Calendars</span>
        <div className="flex items-center gap-1.5">
          <div className="inline-flex rounded-md border bg-background p-0.5" aria-label="Week starts on">
            <button className={cn("rounded px-2 py-1 text-xs", weekStart === "monday" ? "bg-secondary text-secondary-foreground" : "text-muted-foreground")} onClick={() => setStoredWeekStart("monday")}>Mon</button>
            <button className={cn("rounded px-2 py-1 text-xs", weekStart === "sunday" ? "bg-secondary text-secondary-foreground" : "text-muted-foreground")} onClick={() => setStoredWeekStart("sunday")}>Sun</button>
          </div>
          <Button size="sm" variant="ghost" onClick={() => setNewCal((c) => !c)}>
            <CalendarPlus className="size-4" />Add
          </Button>
        </div>
      </div>
      {newCal && (
        <NewCalendar
          pending={calMut.create.isPending}
          onCancel={() => setNewCal(false)}
          onSubmit={(name, color) => calMut.create.mutate({ name, color }, { onSuccess: () => setNewCal(false), onError: (e) => setNotice(e instanceof Error ? e.message : "Failed to create calendar") })}
        />
      )}
      <div className="space-y-2">
        {cals.map((c) => (
          <CalendarRow
            key={c.href}
            calendar={c}
            active={filter === c.href}
            deleteDisabled={cals.length <= 1}
            pending={calMut.update.isPending || calMut.remove.isPending}
            onFilter={() => setFilter(filter === c.href ? "" : c.href)}
            onSave={(name, color) => calMut.update.mutate({ href: c.href, name, color }, { onError: (e) => setNotice(e instanceof Error ? e.message : "Failed to update calendar") })}
            onDelete={() => {
              if (cals.length <= 1) return
              if (confirm(`Delete ${c.name} and its events?`)) {
                calMut.remove.mutate(c.href, {
                  onSuccess: () => { if (filter === c.href) setFilter("") },
                  onError: (e) => setNotice(e instanceof Error ? e.message : "Failed to delete calendar"),
                })
              }
            }}
            onExport={() => doExport(c.href, c.name)}
          />
        ))}
        {cals.length === 0 && <p className="text-xs text-muted-foreground">No calendars yet.</p>}
      </div>
    </div>
  )

  return (
    <div className="mx-auto flex h-full w-full max-w-6xl flex-col">
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b px-4 py-2">
        <div className="min-w-0">
          <span className="text-sm font-semibold">Calendar</span>
          <span className="ml-2 text-sm text-muted-foreground">{viewTitle(view, cursor, weekStart)}</span>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-1.5">
          <Button size="icon" variant="ghost" onClick={() => moveCursor(-1)} title="Previous">
            <ChevronLeft className="size-4" />
          </Button>
          <Button size="sm" variant="outline" onClick={goToday}>Today</Button>
          <Button size="icon" variant="ghost" onClick={() => moveCursor(1)} title="Next">
            <ChevronRight className="size-4" />
          </Button>
          <div className="mx-1 hidden h-6 w-px bg-border sm:block" />
          <div className="flex flex-wrap items-center gap-1.5">
            {VIEWS.map((v) => (
              <Button key={v.value} size="sm" variant={view === v.value ? "secondary" : "ghost"} onClick={() => changeView(v.value)} className="px-2 text-xs sm:px-3 sm:text-sm">
                {v.label}
              </Button>
            ))}
          </div>
          <div className="mx-1 hidden h-6 w-px bg-border sm:block" />
          <Button size="sm" variant="outline" disabled={sync.isPending} onClick={runSync} title="Sync with CalDAV">
            <RefreshCw className={cn("size-4", sync.isPending && "animate-spin")} />Sync
          </Button>
          <input ref={fileRef} type="file" accept=".ics,text/calendar" className="hidden" onChange={(e) => { const fl = e.target.files; if (fl?.length) onPickFile(fl[0]); if (fileRef.current) fileRef.current.value = "" }} />
          <Button size="sm" variant="outline" disabled={importIcs.isPending} onClick={() => fileRef.current?.click()} title="Import .ics file">
            <Upload className="size-4" />{importIcs.isPending ? "Importing..." : "Import"}
          </Button>
          <Button
            size="sm"
            onClick={() => {
              setEditEvent(null)
              setFormErr("")
              setCreateInitial(creating ? null : emptyForm(cals[0]?.href || ""))
              setCreating((c) => !c)
            }}
          >
            <Plus className="size-4" />New
          </Button>
        </div>
      </header>

      <div className="space-y-3 border-b p-3">
        <div className="flex gap-2">
          <input
            value={quickText}
            onChange={(e) => setQuickText(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") add() }}
            placeholder="Quick add, e.g. lunch with Sara Friday 1pm downtown"
            className="h-9 min-w-0 flex-1 rounded-md border bg-background px-3 text-sm outline-none focus-visible:border-ring"
          />
          <Button onClick={add} disabled={qa.isPending}>
            <Plus className="size-4" />{qa.isPending ? "Adding..." : "Add"}
          </Button>
        </div>
        <div className="grid gap-2 lg:grid-cols-[minmax(13rem,1fr)_12rem_auto] lg:items-center">
          <label className="relative min-w-0">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search events, places, notes" className="h-9 w-full rounded-md border bg-background pl-9 pr-3 text-sm outline-none focus-visible:border-ring" />
          </label>
          {cals.length > 1 && (
            <select value={filter} onChange={(e) => setFilter(e.target.value)} className={inp} title="Filter by calendar">
              <option value="">All calendars</option>
              {cals.map((c) => <option key={c.href} value={c.href}>{c.name}</option>)}
            </select>
          )}
          <Button size="sm" variant={importantOnly ? "secondary" : "outline"} onClick={() => setImportantOnly((v) => !v)}>
            <AlertTriangle className="size-4" />Important
          </Button>
        </div>
        <div className="flex flex-wrap gap-1.5">
          <Button size="sm" variant={!typeFilter ? "secondary" : "outline"} onClick={() => setTypeFilter("")}>All types</Button>
          {EVENT_TYPES.map((type) => (
            <Button key={type.value} size="sm" variant={typeFilter === type.value ? "secondary" : "outline"} onClick={() => setTypeFilter(typeFilter === type.value ? "" : type.value)}>
              <Tag className="size-4" />{type.label}
            </Button>
          ))}
        </div>
        {qa.isError && <p className="text-xs text-destructive">{(qa.error as Error)?.message || "Couldn't add that event"}</p>}
        {notice && <p className="text-xs text-muted-foreground">{notice}</p>}
        {undoAction && (
          <div className="flex flex-wrap items-center gap-2 rounded-md border bg-card px-3 py-2 text-xs text-muted-foreground">
            <span>{undoAction.label}</span>
            <Button size="sm" variant="outline" onClick={() => void runUndo()}>Undo</Button>
            <button onClick={() => setUndoAction(null)} title="Dismiss undo" className="rounded p-1 hover:bg-accent">
              <X className="size-3.5" />
            </button>
          </div>
        )}
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
        {creating && (
          <EventForm
            key={`${createInitial?.start || "blank"}-${createInitial?.end || ""}`}
            mode="create"
            initial={createInitial || emptyForm(cals[0]?.href || "")}
            calendars={cals}
            pending={create.isPending}
            error={formErr}
            onCancel={() => { setCreating(false); setCreateInitial(null); setFormErr("") }}
            onSubmit={submitCreate}
          />
        )}
        {editEvent && (
          <EventForm
            mode="edit"
            initial={eventToForm(editEvent, cals)}
            calendars={cals}
            pending={update.isPending}
            error={formErr}
            onCancel={() => { setEditEvent(null); setFormErr("") }}
            onSubmit={(f) => submitEdit(editEvent.series_uid || editEvent.uid, f)}
          />
        )}

        {settings}

        {view === "month" && (
          <div className="overflow-x-auto">
            <div className="grid min-w-[760px] grid-cols-7 overflow-hidden rounded-lg border bg-card">
              {dayNames.map((d) => <div key={d} className="border-b bg-muted/40 px-2 py-2 text-xs font-semibold text-muted-foreground">{d}</div>)}
              {monthDays.map((day) => {
                const key = dateKey(day)
                const dayEvents = eventsForDay(day)
                const muted = !sameMonth(day, cursor)
                const isSelected = selectedDay === key
                return (
                  <div
                    key={key}
                    data-testid={`month-day-${key}`}
                    className={cn(
                      "min-h-[9rem] border-b border-r p-2",
                      muted && "bg-muted/20 text-muted-foreground",
                      sameDay(day, new Date()) && "bg-primary/5",
                      isSelected && "ring-2 ring-inset ring-primary",
                    )}
                  >
                    <div className="mb-2 flex items-center justify-between gap-2">
                      <button
                        className={cn("text-xs font-semibold", sameDay(day, new Date()) && "rounded-full bg-primary px-2 py-0.5 text-primary-foreground")}
                        aria-pressed={isSelected}
                        onClick={() => selectDay(day)}
                      >
                        {day.getDate()}
                      </button>
                      {dayEvents.length > 0 && <span className="text-[11px] text-muted-foreground">{dayEvents.length}</span>}
                    </div>
                    <div className="space-y-1.5">
                      {dayEvents.slice(0, 3).map((ev) => renderEvent(ev, true))}
                      {dayEvents.length > 3 && (
                        <button className="text-xs text-muted-foreground underline-offset-2 hover:underline" onClick={() => selectDay(day)}>
                          +{dayEvents.length - 3} more
                        </button>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        )}

        {view === "week" && (
          <div className="overflow-x-auto rounded-lg border bg-card">
            <div className="min-w-[980px]">
              <div className="grid grid-cols-[4rem_repeat(7,minmax(0,1fr))] border-b bg-muted/40">
                <div className="flex flex-col items-center justify-center gap-0.5 px-1 py-2">
                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() => setStoredWeekHourHeight(weekHourHeight - 10)}
                      title="Zoom out (− or Ctrl+scroll)"
                      aria-label="Zoom out"
                      className="grid size-7 place-items-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground"
                    >
                      <ZoomOut className="size-4" />
                    </button>
                    <button
                      type="button"
                      onClick={() => setStoredWeekHourHeight(weekHourHeight + 10)}
                      title="Zoom in (+ or Ctrl+scroll)"
                      aria-label="Zoom in"
                      className="grid size-7 place-items-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground"
                    >
                      <ZoomIn className="size-4" />
                    </button>
                  </div>
                  {weekDays.length > 0 && (
                    <span className="text-[10px] font-medium tabular-nums text-muted-foreground" title={`ISO week ${isoWeekNumber(weekDays[0])}`}>W{isoWeekNumber(weekDays[0])}</span>
                  )}
                </div>
                {weekDays.map((day) => {
                  const key = dateKey(day)
                  const isSelected = selectedDay === key
                  return (
                    <div key={key} className={cn("border-l px-3 py-2", isSelected && "bg-primary/10")}>
                      <div className="text-xs uppercase tracking-wider text-muted-foreground">{day.toLocaleDateString([], { weekday: "short" })}</div>
                      <button
                        className={cn(
                          "mt-1 rounded px-2 py-0.5 text-lg font-semibold",
                          sameDay(day, new Date()) && "bg-primary text-sm text-primary-foreground",
                          isSelected && !sameDay(day, new Date()) && "bg-secondary text-secondary-foreground",
                        )}
                        aria-pressed={isSelected}
                        onClick={() => selectDay(day, true)}
                      >
                        {day.getDate()}
                      </button>
                    </div>
                  )
                })}
              </div>
              <div className="grid grid-cols-[4rem_repeat(7,minmax(0,1fr))] border-b">
                <div className="px-2 py-2 text-[11px] uppercase tracking-wider text-muted-foreground">All day</div>
                {weekDays.map((day) => {
                  const allDayEvents = weekEventsForDay(day).filter((ev) => ev.all_day)
                  return (
                    <div key={dateKey(day)} className="min-h-11 border-l p-1.5">
                      <div className="space-y-1">
                        {allDayEvents.map((ev) => (
                          <button
                            key={eventIdentity(ev)}
                            onClick={() => openEdit(ev)}
                            className="flex w-full items-center gap-1.5 rounded border bg-background px-2 py-1 text-left text-xs hover:bg-accent"
                            style={calBgImageStyle(ev.color)}
                          >
                            <span className="size-2 shrink-0 rounded-full" style={{ background: solidEventColor(ev.color) }} />
                            <span className="truncate">{ev.summary || ev.title || "(untitled)"}</span>
                          </button>
                        ))}
                      </div>
                    </div>
                  )
                })}
              </div>
              <div
                ref={weekScrollRef}
                data-testid="week-scroll"
                className="max-h-[38rem] overflow-y-auto"
                onScroll={(e) => { weekScrollTop.current = e.currentTarget.scrollTop }}
              >
                <div className="grid grid-cols-[4rem_repeat(7,minmax(0,1fr))]">
                  <div className="relative bg-muted/20" style={{ height: weekGridHeight }}>
                    {Array.from({ length: 24 }, (_, hour) => (
                      <div key={hour} className="absolute left-0 right-0 border-t px-2 text-[11px] text-muted-foreground" style={{ top: hour * weekHourHeight, height: weekHourHeight }}>
                        <span className="relative -top-2 bg-card px-1">{minutesLabel(hour * 60)}</span>
                      </div>
                    ))}
                  </div>
                  {weekDays.map((day) => {
                    const key = dateKey(day)
                    const timedEvents = weekEventsForDay(day).filter((ev) => !ev.all_day)
                    return (
                      <div
                        key={key}
                        data-week-grid="true"
                        data-date={key}
                        data-testid={`week-grid-${key}`}
                        className={cn("relative border-l bg-background", sameDay(day, new Date()) && "bg-primary/5")}
                        style={{ height: weekGridHeight }}
                        onMouseDown={(e) => startWeekCreateDrag(e, day)}
                      >
                        {Array.from({ length: 24 }, (_, hour) => (
                          <div key={hour} className="absolute left-0 right-0 border-t border-border/70" style={{ top: hour * weekHourHeight, height: weekHourHeight }} />
                        ))}
                        {weekDraft?.mode === "create" && weekDraft.dayKey === key && (
                          <div
                            className="pointer-events-none absolute left-1 right-1 z-20 rounded-md border border-primary bg-primary/15 px-2 py-1 text-[11px] font-medium text-primary"
                            style={{
                              top: (weekDraft.startMin / 60) * weekHourHeight,
                              height: Math.max(24, ((weekDraft.endMin - weekDraft.startMin) / 60) * weekHourHeight),
                            }}
                          >
                            {weekRangeLabel(weekDraft.startMin, weekDraft.endMin)}
                          </div>
                        )}
                        {timedEvents.map((ev) => {
                          const timing = weekTiming(ev, day, weekDraft)
                          if (!timing) return null
                          const top = (timing.startMin / 60) * weekHourHeight
                          const height = Math.max(28, ((timing.endMin - timing.startMin) / 60) * weekHourHeight)
                          const title = ev.summary || ev.title || "(untitled)"
                          return (
                            <div
                              key={eventIdentity(ev)}
                              role="button"
                              tabIndex={0}
                              data-week-event="true"
                              data-testid={`week-event-${eventIdentity(ev)}`}
                              className={cn(
                                "absolute left-1 right-1 z-10 cursor-grab overflow-hidden rounded-md border bg-card px-2 py-1.5 text-left shadow-sm active:cursor-grabbing",
                                timing.draft && "ring-2 ring-primary",
                              )}
                              style={{
                                top,
                                height,
                                borderLeftWidth: 4,
                                borderLeftColor: solidEventColor(ev.color),
                                ...calBgImageStyle(ev.color, "58%"),
                              }}
                              onMouseDown={(e) => startWeekMoveDrag(e, ev, day)}
                              onClick={(e) => {
                                e.stopPropagation()
                                if (suppressWeekClick.current) {
                                  suppressWeekClick.current = false
                                  return
                                }
                                openEdit(ev)
                              }}
                              onKeyDown={(e) => {
                                if (e.key === "Enter" || e.key === " ") {
                                  e.preventDefault()
                                  openEdit(ev)
                                }
                              }}
                            >
                              <div className="truncate text-xs font-semibold">{title}</div>
                              <div className="truncate text-[11px] text-muted-foreground">{weekRangeLabel(timing.startMin, timing.endMin)}</div>
                              {ev.location && <div className="truncate text-[11px] text-muted-foreground">{ev.location}</div>}
                              <div
                                data-week-resize="true"
                                data-testid={`week-resize-${eventIdentity(ev)}`}
                                title="Drag to resize"
                                className="absolute inset-x-0 bottom-0 h-2 cursor-ns-resize bg-primary/20 opacity-0 transition-opacity hover:opacity-100"
                                onMouseDown={(e) => startWeekResizeDrag(e, ev, day)}
                              />
                            </div>
                          )
                        })}
                      </div>
                    )
                  })}
                </div>
              </div>
            </div>
          </div>
        )}

        {showDayDetail && (
          <>
            <div
              role="separator"
              aria-orientation="horizontal"
              aria-label="Resize selected day panel"
              aria-valuemin={DAY_DETAIL_MIN_HEIGHT}
              aria-valuenow={Math.round(dayDetailHeight)}
              tabIndex={0}
              data-testid="day-detail-splitter"
              title="Drag to resize"
              className="group -my-2 hidden h-5 cursor-row-resize touch-none items-center justify-center rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring lg:flex"
              onMouseDown={startDayDetailResize}
              onDoubleClick={resetDayDetailHeight}
              onKeyDown={resizeDayDetailByKey}
            >
              <div className="h-1 w-14 rounded-full bg-border transition-colors group-hover:bg-primary/50" />
            </div>
            <section
              data-testid="day-detail-panel"
              className="fixed inset-x-0 bottom-0 z-40 overflow-hidden rounded-t-xl border bg-card shadow-lg max-lg:!h-auto max-lg:max-h-[55vh] lg:static lg:rounded-lg lg:shadow-none"
              style={{ height: dayDetailHeight }}
            >
              <div className="flex h-full min-h-0 flex-col">
                <div className="space-y-2 border-b p-3">
                  <label className="relative block">
                    <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                    <input
                      value={dayDetailQuery}
                      onChange={(e) => setDayDetailQuery(e.target.value)}
                      placeholder="Search all events..."
                      data-testid="day-detail-search"
                      className="h-9 w-full rounded-md border bg-background pl-9 pr-3 text-sm outline-none focus-visible:border-ring"
                    />
                  </label>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="min-w-0">
                      <span className="text-sm font-semibold">{selectedDayLabel}</span>
                      {selectedDayIsToday && <span className="ml-2 text-xs font-semibold text-primary">(Today)</span>}
                      {dayDetailSearch && (
                        <span className="ml-2 text-xs text-muted-foreground">
                          {dayDetailEvents.length} result{dayDetailEvents.length === 1 ? "" : "s"}
                        </span>
                      )}
                    </div>
                    <div className="flex shrink-0 items-center gap-1.5">
                      <Button size="sm" onClick={() => selectedDay && openCreateForDay(selectedDay)} data-testid="day-detail-new">
                        <Plus className="size-4" />New
                      </Button>
                      <button
                        onClick={() => { setSelectedDay(""); setDayDetailQuery("") }}
                        title="Close selected day"
                        aria-label="Close selected day"
                        className="grid size-8 place-items-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground"
                      >
                        <X className="size-4" />
                      </button>
                    </div>
                  </div>
                </div>
                <div className="min-h-0 flex-1 overflow-y-auto p-3">
                  {dayDetailEvents.length === 0 ? (
                    <div className="flex h-full flex-wrap items-center justify-center gap-2 text-sm text-muted-foreground">
                      <span>{dayDetailSearch ? "No events match" : "No events"}</span>
                      {!dayDetailSearch && (
                        <>
                          <a className="text-primary underline-offset-2 hover:underline" href="/v2/settings?section=integrations">Settings &gt; Integrations</a>
                          <Button size="sm" variant="ghost" onClick={() => selectedDay && openCreateForDay(selectedDay)}>
                            <Plus className="size-4" />Create event
                          </Button>
                        </>
                      )}
                    </div>
                  ) : (
                    <div className="space-y-2">
                      {dayDetailEvents.map((ev) => {
                        const start = eventStart(ev)
                        return (
                          <div key={`day-detail-${eventIdentity(ev)}-${ev.dtstart || ""}`}>
                            {dayDetailSearch && start && (
                              <div className="mb-1 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                                {start.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" })}
                              </div>
                            )}
                            <EventCard ev={ev} onEdit={() => openEdit(ev)} onDelete={() => deleteEvent(ev)} onReminder={setNotice} />
                          </div>
                        )
                      })}
                    </div>
                  )}
                </div>
              </div>
            </section>
          </>
        )}

        {view === "year" && (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {Array.from({ length: 12 }, (_, month) => {
              const monthDate = new Date(cursor.getFullYear(), month, 1)
              const monthEvents = sorted.filter((ev) => {
                const start = eventStart(ev)
                return !!start && start.getFullYear() === monthDate.getFullYear() && start.getMonth() === monthDate.getMonth()
              })
              return (
                <button
                  key={month}
                  className="rounded-lg border bg-card p-3 text-left transition-colors hover:bg-accent"
                  onClick={() => { setCursor(monthDate); setView("month") }}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-semibold">{monthDate.toLocaleDateString([], { month: "long" })}</span>
                    <span className="text-xs text-muted-foreground">{monthEvents.length}</span>
                  </div>
                  <div className="mt-3 space-y-1.5">
                    {monthEvents.slice(0, 4).map((ev) => (
                      <div key={ev.uid} className="flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
                        <span className="size-2 shrink-0 rounded-full" style={{ background: solidEventColor(ev.color) }} />
                        <span className="truncate">{eventStart(ev)?.getDate()}. {ev.summary || ev.title || "(untitled)"}</span>
                      </div>
                    ))}
                    {monthEvents.length === 0 && <p className="text-xs text-muted-foreground">No events</p>}
                    {monthEvents.length > 4 && <p className="text-xs text-muted-foreground">+{monthEvents.length - 4} more</p>}
                  </div>
                </button>
              )
            })}
          </div>
        )}

        {view === "agenda" && (
          <div className="space-y-5">
            {groups.map((group) => (
              <section key={group.label}>
                <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">{group.label}</div>
                <div className="space-y-2">{group.events.map((ev) => renderEvent(ev))}</div>
              </section>
            ))}
          </div>
        )}

        {sorted.length === 0 && !showDayDetail && <p className="py-8 text-center text-sm text-muted-foreground">No events match this view.</p>}
      </div>
    </div>
  )
}
