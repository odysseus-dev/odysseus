import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { apiJson, apiFetch } from "@/lib/api"

export interface CalEvent {
  uid: string; summary?: string; title?: string; dtstart?: string; dtend?: string;
  all_day?: boolean; location?: string; description?: string; calendar?: string;
  calendar_href?: string; color?: string; rrule?: string; series_uid?: string;
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
}
export interface EventPatch {
  summary?: string; dtstart?: string; dtend?: string; all_day?: boolean; location?: string;
  description?: string; rrule?: string; color?: string;
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
    }),
    update: useMutation({
      mutationFn: async (v: { uid: string } & EventPatch) => {
        const { uid, ...patch } = v
        const r = await apiFetch(`/api/calendar/events/${encodeURIComponent(uid)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch) })
        if (!r.ok) throw new Error("Couldn't update event"); return r.json()
      },
      onSuccess: inv,
    }),
    remove: useMutation({
      mutationFn: async (uid: string) => { const r = await apiFetch(`/api/calendar/events/${encodeURIComponent(uid)}`, { method: "DELETE" }); if (!r.ok) throw new Error("delete failed") },
      onSuccess: inv,
    }),
  }
}

export function useCalendarMutations() {
  const qc = useQueryClient()
  const inv = () => qc.invalidateQueries({ queryKey: ["calendars"] })
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
  }
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
