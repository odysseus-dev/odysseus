import { useQuery } from "@tanstack/react-query"
import { apiJson } from "@/lib/api"

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
