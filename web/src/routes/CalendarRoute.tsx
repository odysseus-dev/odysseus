import { useMemo, useState } from "react"
import { Plus } from "lucide-react"
import { useEvents, useQuickAddEvent, type CalEvent } from "@/api/calendar"
import { Button } from "@/components/ui/button"

export function CalendarRoute() {
  const { start, end } = useMemo(() => {
    const s = new Date(); s.setHours(0, 0, 0, 0)
    const e = new Date(s); e.setDate(e.getDate() + 30)
    return { start: s.toISOString(), end: e.toISOString() }
  }, [])
  const { data: events } = useEvents(start, end)
  const qa = useQuickAddEvent()
  const [text, setText] = useState("")
  const add = () => { if (text.trim()) qa.mutate(text, { onSuccess: () => setText("") }) }

  const sorted = [...(events || [])].sort((a, b) => (a.dtstart || "").localeCompare(b.dtstart || ""))
  const groups: Record<string, CalEvent[]> = {}
  for (const ev of sorted) {
    const d = ev.dtstart ? new Date(ev.dtstart).toDateString() : "Undated"
    ;(groups[d] = groups[d] || []).push(ev)
  }
  return (
    <div className="mx-auto flex h-full w-full max-w-2xl flex-col">
      <header className="flex h-13 shrink-0 items-center border-b px-4 text-sm font-semibold">
        Calendar <span className="ml-2 font-normal text-muted-foreground">· next 30 days</span>
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
      </div>
      <div className="flex-1 space-y-5 overflow-y-auto p-4">
        {Object.entries(groups).map(([day, evs]) => (
          <div key={day}>
            <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">{day}</div>
            <div className="space-y-2">
              {evs.map((ev) => (
                <div key={ev.uid} className="flex items-center gap-3 rounded-lg border bg-card p-3">
                  <div className="w-16 shrink-0 text-xs text-muted-foreground">
                    {ev.all_day ? "All day" : ev.dtstart ? new Date(ev.dtstart).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : ""}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium">{ev.summary || ev.title || "(untitled)"}</div>
                    {ev.location && <div className="truncate text-xs text-muted-foreground">{ev.location}</div>}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
        {sorted.length === 0 && <p className="py-8 text-center text-sm text-muted-foreground">No upcoming events.</p>}
      </div>
    </div>
  )
}
