import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { apiJson, apiFetch } from "@/lib/api"

export interface CalEvent {
  uid: string; summary?: string; title?: string; dtstart?: string; dtend?: string;
  all_day?: boolean; location?: string; description?: string; calendar?: string;
  calendar_href?: string; color?: string; rrule?: string; series_uid?: string;
  event_type?: string; importance?: string;
  is_recurrence?: boolean;
}
export function useEvents(start: string, end: string) {
  return useQuery({
    queryKey: ["calendar", start, end],
    queryFn: async () =>
      (await apiJson<{ events: CalEvent[] }>(`/api/calendar/events?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`)).events,
  })
}

export interface Calendar { name: string; href: string; color?: string; source?: string }
export function useCalendars() {
  return useQuery({
    queryKey: ["calendars"],
    queryFn: async () =>
      (await apiJson<{ calendars: Calendar[] }>("/api/calendar/calendars")).calendars,
  })
}

export interface EventInput {
  summary: string; dtstart: string; dtend?: string; all_day?: boolean; location?: string;
  description?: string; calendar_href?: string; rrule?: string; color?: string;
  event_type?: string; importance?: string;
}
export interface EventPatch {
  summary?: string; dtstart?: string; dtend?: string; all_day?: boolean; location?: string;
  description?: string; rrule?: string; color?: string;
  event_type?: string; importance?: string;
}
export function useEventMutations() {
  const qc = useQueryClient()
  const inv = () => qc.invalidateQueries({ queryKey: ["calendar"] })
  return {
    create: useMutation({
      mutationFn: async (v: EventInput) => {
        const r = await apiFetch("/api/calendar/events", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(v) })
        if (!r.ok) throw new Error("Couldn't create event"); return r.json()
      },
      onSuccess: inv,
      meta: { silent: true },
    }),
    update: useMutation({
      mutationFn: async (v: { uid: string } & EventPatch) => {
        const { uid, ...patch } = v
        const r = await apiFetch(`/api/calendar/events/${encodeURIComponent(uid)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch) })
        if (!r.ok) throw new Error("Couldn't update event"); return r.json()
      },
      onSuccess: inv,
      meta: { silent: true },
    }),
    remove: useMutation({
      mutationFn: async (uid: string) => { const r = await apiFetch(`/api/calendar/events/${encodeURIComponent(uid)}`, { method: "DELETE" }); if (!r.ok) throw new Error("delete failed") },
      onSuccess: inv,
    }),
  }
}

export function useCalendarMutations() {
  const qc = useQueryClient()
  const inv = () => {
    qc.invalidateQueries({ queryKey: ["calendars"] })
    qc.invalidateQueries({ queryKey: ["calendar"] })
  }
  return {
    create: useMutation({
      mutationFn: async (v: { name: string; color?: string }) => {
        const q = new URLSearchParams({ name: v.name })
        if (v.color) q.set("color", v.color)
        const r = await apiFetch(`/api/calendar/calendars?${q.toString()}`, { method: "POST" })
        if (!r.ok) throw new Error("Couldn't create calendar"); return r.json()
      },
      onSuccess: inv,
    }),
    update: useMutation({
      mutationFn: async (v: { href: string; name?: string; color?: string }) => {
        const q = new URLSearchParams()
        if (v.name !== undefined) q.set("name", v.name)
        if (v.color !== undefined) q.set("color", v.color)
        const r = await apiFetch(`/api/calendar/calendars/${encodeURIComponent(v.href)}?${q.toString()}`, { method: "PUT" })
        if (!r.ok) throw new Error("Couldn't update calendar"); return r.json()
      },
      onSuccess: inv,
    }),
    remove: useMutation({
      mutationFn: async (href: string) => {
        const r = await apiFetch(`/api/calendar/calendars/${encodeURIComponent(href)}`, { method: "DELETE" })
        if (!r.ok) throw new Error("Couldn't delete calendar")
      },
      onSuccess: inv,
    }),
  }
}

// Extract a cookbook task id from an event description. Cookbook-generated
// calendar events embed `cookbook_task_id:<id>` so we can link back to Tasks.
export function cookbookTaskId(description?: string): string | null {
  if (!description) return null
  const m = description.match(/cookbook_task_id:\s*([A-Za-z0-9._-]+)/)
  return m ? m[1] : null
}

export async function createCalendarReminder(v: { title: string; content: string; dueDate: string; eventStart?: string; color?: string }): Promise<void> {
  const r = await apiFetch("/api/notes", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      title: v.title,
      content: v.eventStart ? `${v.content}\n\nEvent starts: ${v.eventStart}` : v.content,
      note_type: "note",
      label: "calendar",
      color: v.color || "#5b8abf",
      due_date: v.dueDate,
      source: "calendar",
    }),
  })
  if (!r.ok) throw new Error("Couldn't create reminder")
}

export async function uploadCalendarBackgroundImage(file: File): Promise<string> {
  const fd = new FormData()
  fd.append("files", file, file.name)
  const r = await apiFetch("/api/upload", { method: "POST", body: fd })
  if (!r.ok) throw new Error("Couldn't upload image")
  const data = (await r.json()) as { files?: { id?: string }[] }
  const fileId = data.files?.[0]?.id
  if (!fileId) throw new Error("Couldn't upload image")
  const origin = typeof window !== "undefined" ? window.location.origin : ""
  return `${origin}/api/upload/${encodeURIComponent(fileId)}`
}

export interface SyncResult { calendars?: number; events?: number; deleted?: number; errors?: string[] }
export function useSync() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (direction: string = "pull") => {
      const r = await apiFetch(`/api/calendar/sync?direction=${encodeURIComponent(direction)}`, { method: "POST" })
      if (!r.ok) throw new Error("Sync failed")
      return r.json() as Promise<SyncResult>
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["calendar"] })
      qc.invalidateQueries({ queryKey: ["calendars"] })
    },
    meta: { silent: true },
  })
}

export interface ImportResult { ok?: boolean; imported?: number; skipped?: number; calendar?: string; calendar_id?: string }
export function useImportIcs() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (v: { file: File; calendar_name?: string }) => {
      const fd = new FormData()
      fd.set("file", v.file, v.file.name)
      const q = v.calendar_name ? `?calendar_name=${encodeURIComponent(v.calendar_name)}` : ""
      const r = await apiFetch(`/api/calendar/import${q}`, { method: "POST", body: fd })
      if (!r.ok) throw new Error("Import failed")
      return r.json() as Promise<ImportResult>
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["calendar"] })
      qc.invalidateQueries({ queryKey: ["calendars"] })
    },
    meta: { silent: true },
  })
}

// Download a calendar as an .ics file. The backend sets Content-Disposition,
// so we fetch the blob and trigger a save with an object URL.
export async function exportIcs(calId: string, name: string): Promise<void> {
  const r = await apiFetch(`/api/calendar/export/${encodeURIComponent(calId)}`)
  if (!r.ok) throw new Error("Export failed")
  const blob = await r.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = `${(name || "calendar").replace(/[^A-Za-z0-9._-]/g, "_") || "calendar"}.ics`
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

interface ParsedEvent { summary?: string; dtstart?: string; dtend?: string; all_day?: boolean; location?: string; description?: string }
export function useQuickAddEvent() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (text: string) => {
      const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"
      const p = await apiFetch("/api/calendar/quick-parse", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text, tz }) })
      if (!p.ok) throw new Error("Couldn't parse that")
      const parsed = (await p.json()) as { event?: ParsedEvent }
      const ev = parsed.event
      if (!ev?.summary || !ev?.dtstart) throw new Error("Couldn't understand that event")
      const c = await apiFetch("/api/calendar/events", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(ev) })
      if (!c.ok) throw new Error("Couldn't create event")
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["calendar"] }),
  })
}
