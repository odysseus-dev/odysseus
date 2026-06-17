import { useMemo, useRef, useState } from "react"
import { Plus, Trash2, Pencil, RefreshCw, Upload, Download, X, CalendarPlus } from "lucide-react"
import {
  useEvents, useCalendars, useQuickAddEvent, useEventMutations, useCalendarMutations,
  useSync, useImportIcs, exportIcs,
  type CalEvent, type EventInput, type EventPatch,
} from "@/api/calendar"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const inp = "h-9 w-full rounded-md border bg-background px-3 text-sm outline-none focus-visible:border-ring"

// Recurrence presets → raw RRULE strings the backend expands with dateutil.
const RECUR_OPTIONS: { label: string; value: string }[] = [
  { label: "Does not repeat", value: "" },
  { label: "Daily", value: "FREQ=DAILY" },
  { label: "Weekly", value: "FREQ=WEEKLY" },
  { label: "Every weekday (Mon–Fri)", value: "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR" },
  { label: "Monthly", value: "FREQ=MONTHLY" },
  { label: "Yearly", value: "FREQ=YEARLY" },
]

// Map an arbitrary stored RRULE to the closest preset value, falling back to
// "custom" so we don't silently drop a recurrence the form can't represent.
function matchRecur(rrule?: string): string {
  const r = (rrule || "").trim().toUpperCase()
  if (!r) return ""
  const known = RECUR_OPTIONS.find((o) => o.value && o.value === r)
  return known ? known.value : "custom"
}

// ISO (possibly with trailing Z) → value for <input type="datetime-local">.
function isoToLocalInput(iso?: string): string {
  if (!iso) return ""
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ""
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}
// ISO → value for <input type="date">.
function isoToDateInput(iso?: string): string {
  if (!iso) return ""
  const d = new Date(iso)
  if (isNaN(d.getTime())) return (iso || "").slice(0, 10)
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

interface FormState {
  summary: string; allDay: boolean; start: string; end: string;
  location: string; description: string; recur: string; customRrule: string; calendarHref: string;
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
    customRrule: recur === "custom" ? (ev.rrule || "") : "",
    calendarHref: ev.calendar_href || calendars[0]?.href || "",
  }
}

function emptyForm(defaultCal: string): FormState {
  return { summary: "", allDay: false, start: "", end: "", location: "", description: "", recur: "", customRrule: "", calendarHref: defaultCal }
}

interface EventFormProps {
  mode: "create" | "edit"
  initial: FormState
  calendars: { name: string; href: string }[]
  pending: boolean
  error?: string
  onCancel: () => void
  onSubmit: (f: FormState) => void
}

function EventForm({ mode, initial, calendars, pending, error, onCancel, onSubmit }: EventFormProps) {
  const [f, setF] = useState<FormState>(initial)
  const set = <K extends keyof FormState>(k: K, v: FormState[K]) => setF((p) => ({ ...p, [k]: v }))
  return (
    <div className="mb-3 space-y-2 rounded-lg border bg-card p-3">
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold">{mode === "create" ? "New event" : "Edit event"}</span>
        <button onClick={onCancel} title="Close" className="text-muted-foreground hover:text-foreground"><X className="size-4" /></button>
      </div>
      <input value={f.summary} onChange={(e) => set("summary", e.target.value)} placeholder="Title" className={inp} />
      <label className="flex items-center gap-2 text-sm text-muted-foreground">
        <input type="checkbox" checked={f.allDay} onChange={(e) => set("allDay", e.target.checked)} className="size-4" />
        All day
      </label>
      <div className="flex flex-wrap gap-2">
        <div className="flex-1">
          <label className="mb-1 block text-xs text-muted-foreground">Start</label>
          <input type={f.allDay ? "date" : "datetime-local"} value={f.start} onChange={(e) => set("start", e.target.value)} className={inp} />
        </div>
        <div className="flex-1">
          <label className="mb-1 block text-xs text-muted-foreground">End</label>
          <input type={f.allDay ? "date" : "datetime-local"} value={f.end} onChange={(e) => set("end", e.target.value)} className={inp} />
        </div>
      </div>
      <input value={f.location} onChange={(e) => set("location", e.target.value)} placeholder="Location (optional)" className={inp} />
      <textarea value={f.description} onChange={(e) => set("description", e.target.value)} placeholder="Description (optional)" rows={2} className="w-full resize-none rounded-md border bg-background px-3 py-2 text-sm outline-none focus-visible:border-ring" />
      <div className="flex flex-wrap gap-2">
        {calendars.length > 1 && mode === "create" && (
          <div className="flex-1">
            <label className="mb-1 block text-xs text-muted-foreground">Calendar</label>
            <select value={f.calendarHref} onChange={(e) => set("calendarHref", e.target.value)} className={inp}>
              {calendars.map((c) => <option key={c.href} value={c.href}>{c.name}</option>)}
            </select>
          </div>
        )}
        <div className="flex-1">
          <label className="mb-1 block text-xs text-muted-foreground">Repeat</label>
          <select value={f.recur} onChange={(e) => set("recur", e.target.value)} className={inp}>
            {RECUR_OPTIONS.map((o) => <option key={o.value || "none"} value={o.value}>{o.label}</option>)}
            {f.recur === "custom" && <option value="custom">Custom (RRULE)</option>}
          </select>
        </div>
      </div>
      {f.recur === "custom" && (
        <input value={f.customRrule} onChange={(e) => set("customRrule", e.target.value)} placeholder="FREQ=WEEKLY;INTERVAL=2" className={inp} />
      )}
      {error && <p className="text-xs text-destructive">{error}</p>}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={onCancel}>Cancel</Button>
        <Button size="sm" disabled={pending} onClick={() => onSubmit(f)}>
          {pending ? "Saving…" : mode === "create" ? "Create" : "Save"}
        </Button>
      </div>
    </div>
  )
}

interface NewCalendarProps { pending: boolean; onCancel: () => void; onSubmit: (name: string, color: string) => void }
function NewCalendar({ pending, onCancel, onSubmit }: NewCalendarProps) {
  const [name, setName] = useState("")
  const [color, setColor] = useState("#5b8abf")
  return (
    <div className="mb-3 space-y-2 rounded-lg border bg-card p-3">
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold">New calendar</span>
        <button onClick={onCancel} title="Close" className="text-muted-foreground hover:text-foreground"><X className="size-4" /></button>
      </div>
      <div className="flex gap-2">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Calendar name" className={inp} />
        <input type="color" value={color} onChange={(e) => setColor(e.target.value)} title="Color" className="h-9 w-12 shrink-0 cursor-pointer rounded-md border bg-background" />
      </div>
      <div className="flex justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={onCancel}>Cancel</Button>
        <Button size="sm" disabled={pending || !name.trim()} onClick={() => onSubmit(name.trim(), color)}>{pending ? "Creating…" : "Create"}</Button>
      </div>
    </div>
  )
}

function rruleFor(f: FormState): string {
  if (f.recur === "custom") return f.customRrule.trim()
  return f.recur
}

export function CalendarRoute() {
  const { start, end } = useMemo(() => {
    const s = new Date(); s.setHours(0, 0, 0, 0)
    const e = new Date(s); e.setDate(e.getDate() + 30)
    return { start: s.toISOString(), end: e.toISOString() }
  }, [])
  const { data: events } = useEvents(start, end)
  const { data: calendars } = useCalendars()
  const qa = useQuickAddEvent()
  const { create, update, remove } = useEventMutations()
  const calMut = useCalendarMutations()
  const sync = useSync()
  const importIcs = useImportIcs()
  const fileRef = useRef<HTMLInputElement>(null)

  const cals = calendars || []
  const [text, setText] = useState("")
  const [filter, setFilter] = useState("")      // calendar href filter ("" = all)
  const [creating, setCreating] = useState(false)
  const [editUid, setEditUid] = useState<string | null>(null)
  const [formErr, setFormErr] = useState("")
  const [newCal, setNewCal] = useState(false)
  const [notice, setNotice] = useState("")

  const add = () => { if (text.trim()) qa.mutate(text, { onSuccess: () => setText("") }) }

  const submitCreate = (f: FormState) => {
    if (!f.summary.trim()) { setFormErr("Title is required"); return }
    if (!f.start) { setFormErr("Start is required"); return }
    setFormErr("")
    const payload: EventInput = {
      summary: f.summary.trim(),
      dtstart: f.start,
      dtend: f.end || undefined,
      all_day: f.allDay,
      location: f.location.trim() || undefined,
      description: f.description.trim() || undefined,
      rrule: rruleFor(f) || undefined,
      calendar_href: f.calendarHref || undefined,
    }
    create.mutate(payload, { onSuccess: () => setCreating(false), onError: (e) => setFormErr(e instanceof Error ? e.message : "Failed") })
  }

  const submitEdit = (uid: string, f: FormState) => {
    if (!f.summary.trim()) { setFormErr("Title is required"); return }
    if (!f.start) { setFormErr("Start is required"); return }
    setFormErr("")
    const patch: EventPatch = {
      summary: f.summary.trim(),
      dtstart: f.start,
      dtend: f.end || undefined,
      all_day: f.allDay,
      location: f.location.trim(),
      description: f.description.trim(),
      rrule: rruleFor(f),
    }
    update.mutate({ uid, ...patch }, { onSuccess: () => setEditUid(null), onError: (e) => setFormErr(e instanceof Error ? e.message : "Failed") })
  }

  const runSync = () => {
    setNotice("")
    sync.mutate("pull", {
      onSuccess: (r) => setNotice(r.errors && r.errors.length ? `Sync: ${r.errors[0]}` : `Synced — ${r.events ?? 0} event(s) from ${r.calendars ?? 0} calendar(s)`),
      onError: (e) => setNotice(e instanceof Error ? e.message : "Sync failed"),
    })
  }

  const onPickFile = (file: File) => {
    setNotice("")
    importIcs.mutate({ file }, {
      onSuccess: (r) => setNotice(`Imported ${r.imported ?? 0} event(s)${r.skipped ? `, skipped ${r.skipped}` : ""} into “${r.calendar ?? "Imported"}”`),
      onError: (e) => setNotice(e instanceof Error ? e.message : "Import failed"),
    })
  }

  const doExport = (calId: string, name: string) => {
    exportIcs(calId, name).catch((e) => setNotice(e instanceof Error ? e.message : "Export failed"))
  }

  const visible = (events || []).filter((ev) => !filter || ev.calendar_href === filter)
  const sorted = [...visible].sort((a, b) => (a.dtstart || "").localeCompare(b.dtstart || ""))
  const groups: Record<string, CalEvent[]> = {}
  for (const ev of sorted) {
    const d = ev.dtstart ? new Date(ev.dtstart).toDateString() : "Undated"
    ;(groups[d] = groups[d] || []).push(ev)
  }

  return (
    <div className="mx-auto flex h-full w-full max-w-2xl flex-col">
      <header className="flex h-13 shrink-0 items-center justify-between gap-2 border-b px-4">
        <span className="text-sm font-semibold">Calendar <span className="font-normal text-muted-foreground">· next 30 days</span></span>
        <div className="flex items-center gap-1.5">
          {cals.length > 1 && (
            <select value={filter} onChange={(e) => setFilter(e.target.value)} className="h-8 rounded-md border bg-background px-2 text-[13px] outline-none focus-visible:border-ring" title="Filter by calendar">
              <option value="">All calendars</option>
              {cals.map((c) => <option key={c.href} value={c.href}>{c.name}</option>)}
            </select>
          )}
          <Button size="sm" variant="outline" disabled={sync.isPending} onClick={runSync} title="Sync with CalDAV"><RefreshCw className={cn("size-4", sync.isPending && "animate-spin")} />Sync</Button>
          <input ref={fileRef} type="file" accept=".ics,text/calendar" className="hidden" onChange={(e) => { const fl = e.target.files; if (fl?.length) onPickFile(fl[0]); if (fileRef.current) fileRef.current.value = "" }} />
          <Button size="sm" variant="outline" disabled={importIcs.isPending} onClick={() => fileRef.current?.click()} title="Import .ics file"><Upload className="size-4" />{importIcs.isPending ? "Importing…" : "Import"}</Button>
          <Button size="sm" onClick={() => { setEditUid(null); setFormErr(""); setCreating((c) => !c) }}><Plus className="size-4" />New</Button>
        </div>
      </header>

      <div className="border-b p-3">
        <div className="flex gap-2">
          <input
            value={text} onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") add() }}
            placeholder="Add an event — e.g. “lunch with Sara Friday 1pm downtown”"
            className="h-9 flex-1 rounded-md border bg-background px-3 text-sm outline-none focus-visible:border-ring"
          />
          <Button onClick={add} disabled={qa.isPending}><Plus className="size-4" />{qa.isPending ? "Adding…" : "Add"}</Button>
        </div>
        {qa.isError && <p className="mt-1.5 text-xs text-destructive">{(qa.error as Error)?.message || "Couldn't add that event"}</p>}
        {notice && <p className="mt-1.5 text-xs text-muted-foreground">{notice}</p>}
      </div>

      <div className="flex-1 space-y-5 overflow-y-auto p-4">
        {creating && (
          <EventForm
            mode="create"
            initial={emptyForm(cals[0]?.href || "")}
            calendars={cals}
            pending={create.isPending}
            error={formErr}
            onCancel={() => { setCreating(false); setFormErr("") }}
            onSubmit={submitCreate}
          />
        )}

        {/* Calendar management: create + per-calendar ICS export */}
        <div className="rounded-lg border bg-card p-3">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Calendars</span>
            <Button size="sm" variant="ghost" onClick={() => setNewCal((c) => !c)}><CalendarPlus className="size-4" />Add</Button>
          </div>
          {newCal && (
            <NewCalendar
              pending={calMut.create.isPending}
              onCancel={() => setNewCal(false)}
              onSubmit={(name, color) => calMut.create.mutate({ name, color }, { onSuccess: () => setNewCal(false) })}
            />
          )}
          <div className="space-y-1.5">
            {cals.map((c) => (
              <div key={c.href} className="flex items-center gap-2 text-sm">
                <span className="size-2.5 shrink-0 rounded-full" style={{ background: c.color || "var(--muted-foreground)" }} />
                <span className="min-w-0 flex-1 truncate">{c.name}</span>
                <span className="text-xs text-muted-foreground">{c.source}</span>
                <button onClick={() => doExport(c.href, c.name)} title="Export .ics" className="text-muted-foreground hover:text-foreground"><Download className="size-4" /></button>
              </div>
            ))}
            {cals.length === 0 && <p className="text-xs text-muted-foreground">No calendars yet.</p>}
          </div>
        </div>

        {Object.entries(groups).map(([day, evs]) => (
          <div key={day}>
            <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">{day}</div>
            <div className="space-y-2">
              {evs.map((ev) => (
                editUid === ev.uid ? (
                  <EventForm
                    key={ev.uid}
                    mode="edit"
                    initial={eventToForm(ev, cals)}
                    calendars={cals}
                    pending={update.isPending}
                    error={formErr}
                    onCancel={() => { setEditUid(null); setFormErr("") }}
                    onSubmit={(f) => submitEdit(ev.series_uid || ev.uid, f)}
                  />
                ) : (
                  <div key={ev.uid} className="group flex items-center gap-3 rounded-lg border bg-card p-3">
                    <span className="h-8 w-1 shrink-0 rounded-full" style={{ background: ev.color || "var(--muted-foreground)" }} />
                    <div className="w-16 shrink-0 text-xs text-muted-foreground">
                      {ev.all_day ? "All day" : ev.dtstart ? new Date(ev.dtstart).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : ""}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5">
                        <span className="truncate text-sm font-medium">{ev.summary || ev.title || "(untitled)"}</span>
                        {(ev.is_recurrence || ev.rrule) && <RefreshCw className="size-3 shrink-0 text-muted-foreground" />}
                      </div>
                      {ev.location && <div className="truncate text-xs text-muted-foreground">{ev.location}</div>}
                    </div>
                    <div className="flex shrink-0 items-center gap-1.5 opacity-0 transition-opacity group-hover:opacity-100">
                      <button onClick={() => { setCreating(false); setFormErr(""); setEditUid(ev.uid) }} title="Edit" className="text-muted-foreground hover:text-foreground"><Pencil className="size-4" /></button>
                      <button onClick={() => { if (confirm("Delete this event?")) remove.mutate(ev.series_uid || ev.uid) }} title="Delete" className="text-muted-foreground hover:text-destructive"><Trash2 className="size-4" /></button>
                    </div>
                  </div>
                )
              ))}
            </div>
          </div>
        ))}
        {sorted.length === 0 && <p className="py-8 text-center text-sm text-muted-foreground">No upcoming events.</p>}
      </div>
    </div>
  )
}
