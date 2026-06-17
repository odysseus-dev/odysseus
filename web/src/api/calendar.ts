import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { apiJson, apiFetch } from "@/lib/api"

export interface CalEvent {
  uid: string; summary?: string; title?: string; dtstart?: string; dtend?: string;
  all_day?: boolean; location?: string; calendar?: string; color?: string;
}
export function useEvents(start: string, end: string) {
  return useQuery({
    queryKey: ["calendar", start, end],
    queryFn: async () =>
      (await apiJson<{ events: CalEvent[] }>(`/api/calendar/events?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`)).events,
  })
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
