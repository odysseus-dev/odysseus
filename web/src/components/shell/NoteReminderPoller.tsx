import { useEffect } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { toast } from "@/stores/toast"
import {
  NOTE_REMINDER_FIRED_KEY,
  hasReminderTime,
  isNoteFullyDone,
  useNoteReminders,
} from "@/stores/noteReminders"
import type { Note } from "@/types"

interface NotesResponse {
  notes?: Note[]
}

const POLL_MS = 30_000
const NOTIFICATION_ICON = "/static/favicon.ico"

function loadFired(): Set<string> {
  if (typeof localStorage === "undefined") return new Set()
  try {
    const value = JSON.parse(localStorage.getItem(NOTE_REMINDER_FIRED_KEY) || "[]")
    return new Set(Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [])
  } catch {
    return new Set()
  }
}

function saveFired(value: Set<string>): void {
  if (typeof localStorage === "undefined") return
  try {
    localStorage.setItem(NOTE_REMINDER_FIRED_KEY, JSON.stringify([...value]))
  } catch {
    /* ignore */
  }
}

function toLocalDatetime(value: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}T${pad(value.getHours())}:${pad(value.getMinutes())}`
}

function nthWeekdayOfMonth(year: number, month: number, weekday: number, n: number): Date {
  const first = new Date(year, month, 1)
  const offset = (weekday - first.getDay() + 7) % 7
  let day = 1 + offset + (n - 1) * 7
  const lastDay = new Date(year, month + 1, 0).getDate()
  if (day > lastDay) day -= 7
  return new Date(year, month, day, 0, 0, 0)
}

function lastWeekdayOfMonth(year: number, month: number, weekday: number): Date {
  const lastDay = new Date(year, month + 1, 0)
  const back = (lastDay.getDay() - weekday + 7) % 7
  return new Date(year, month, lastDay.getDate() - back, 0, 0, 0)
}

function normalizeRepeat(repeat: string | undefined, originalDate: Date): string {
  if (!repeat || repeat === "none") return "none"
  if (repeat === "daily" || repeat === "yearly") return repeat
  if (/^(weekly|monthly):/.test(repeat)) return repeat
  const weekday = originalDate.getDay()
  const nth = Math.ceil(originalDate.getDate() / 7)
  if (repeat === "weekly") return `weekly:${weekday}`
  if (repeat === "monthly") return `monthly:day:${originalDate.getDate()}`
  if (repeat === "monthly_nth_weekday") return `monthly:nth:${nth}:${weekday}`
  if (repeat === "monthly_last_weekday") return `monthly:last:${weekday}`
  return repeat
}

function advanceRecurring(value: string, repeat: string | undefined): string | null {
  const original = new Date(value)
  if (Number.isNaN(original.getTime())) return null
  const hour = original.getHours()
  const minute = original.getMinutes()
  const norm = normalizeRepeat(repeat, original)
  if (norm === "none") return null
  let next: Date | null = new Date(original)

  const step = () => {
    if (!next) return
    if (norm === "daily") {
      next.setDate(next.getDate() + 1)
      return
    }
    if (norm === "yearly") {
      next.setFullYear(next.getFullYear() + 1)
      return
    }
    const parts = norm.split(":")
    if (parts[0] === "weekly") {
      const weekday = Number(parts[1])
      let delta = (weekday - next.getDay() + 7) % 7
      if (delta === 0) delta = 7
      next.setDate(next.getDate() + delta)
      next.setHours(hour, minute, 0, 0)
      return
    }
    if (parts[0] === "monthly") {
      const year = next.getFullYear() + (next.getMonth() === 11 ? 1 : 0)
      const month = (next.getMonth() + 1) % 12
      if (parts[1] === "day") {
        const wanted = Number(parts[2])
        const lastDay = new Date(year, month + 1, 0).getDate()
        next = Number.isFinite(wanted) ? new Date(year, month, Math.min(wanted, lastDay), hour, minute, 0, 0) : null
        return
      }
      if (parts[1] === "nth") {
        const nth = Number(parts[2])
        const weekday = Number(parts[3])
        next = Number.isFinite(nth) && Number.isFinite(weekday) ? nthWeekdayOfMonth(year, month, weekday, nth) : null
        next?.setHours(hour, minute, 0, 0)
        return
      }
      if (parts[1] === "last") {
        const weekday = Number(parts[2])
        next = Number.isFinite(weekday) ? lastWeekdayOfMonth(year, month, weekday) : null
        next?.setHours(hour, minute, 0, 0)
        return
      }
    }
    next = null
  }

  let guard = 5000
  while (next && next.getTime() <= Date.now()) {
    if (--guard <= 0) return null
    step()
  }
  return next ? toLocalDatetime(next) : null
}

function reminderBody(note: Note): string {
  if (Array.isArray(note.items) && note.items.length > 0) {
    const pending = note.items
      .filter((item) => !(item.done || item.checked))
      .map((item) => (item.text || "").trim())
      .filter(Boolean)
    if (pending.length > 0) {
      const shown = pending.slice(0, 8).map((text) => `- ${text}`).join("\n")
      const extra = pending.length > 8 ? `\n...and ${pending.length - 8} more` : ""
      return `Pending (${pending.length}):\n${shown}${extra}`
    }
    return `${note.items.length} item${note.items.length === 1 ? "" : "s"}`
  }
  return (note.content || "").slice(0, 400)
}

function notifyLocal(title: string, body: string, noteId: string): void {
  let fired = false
  if ("Notification" in window && Notification.permission === "granted") {
    try {
      const notification = new Notification(title, { body, tag: `note-${noteId}`, icon: NOTIFICATION_ICON })
      notification.onclick = () => {
        window.focus()
        window.location.assign("/v2/notes")
        notification.close()
      }
      fired = true
    } catch {
      fired = false
    }
  }
  if (!fired) toast(title, "info", 7000)
}

async function fireReminder(note: Note): Promise<void> {
  const title = note.title || "Note reminder"
  const body = reminderBody(note)
  let shown = false
  const show = (message: string) => {
    if (shown) return
    shown = true
    notifyLocal(title, message, note.id)
  }
  const timer = window.setTimeout(() => show(body), 1500)
  try {
    const res = await apiFetch("/api/notes/fire-reminder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note_id: note.id, title, body }),
    })
    window.clearTimeout(timer)
    const data = res.ok ? await res.json().catch(() => null) as { synthesis?: string } | null : null
    show(data?.synthesis || body)
  } catch {
    window.clearTimeout(timer)
  }
  show(body)
}

async function patchReminderDate(noteId: string, dueDate: string): Promise<void> {
  await apiFetch(`/api/notes/${noteId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ due_date: dueDate }),
  })
}

async function fetchNotes(): Promise<Note[]> {
  const res = await apiFetch("/api/notes")
  if (!res.ok) return []
  const data = await res.json().catch(() => ({})) as NotesResponse
  return Array.isArray(data.notes) ? data.notes : []
}

export function NoteReminderPoller() {
  const queryClient = useQueryClient()

  useEffect(() => {
    let cancelled = false
    let busy = false

    const poll = async () => {
      if (busy) return
      busy = true
      try {
        const notes = await fetchNotes()
        if (cancelled) return
        const now = Date.now()
        const fired = loadFired()
        let changed = false
        let patched = false
        const reminders = useNoteReminders.getState()

        for (const note of notes) {
          if (note.archived || isNoteFullyDone(note) || !note.due_date || !hasReminderTime(note.due_date)) continue
          if (fired.has(note.id)) continue
          const due = new Date(note.due_date).getTime()
          if (!Number.isFinite(due) || due > now) continue

          if (due > now - 60_000) {
            void fireReminder(note)
            reminders.queueHighlight(note.id)
          }

          if (note.repeat && note.repeat !== "none") {
            const next = advanceRecurring(note.due_date, note.repeat)
            if (next) {
              void patchReminderDate(note.id, next).then(() => {
                void queryClient.invalidateQueries({ queryKey: ["notes"] })
              }).catch(() => {})
              patched = true
              continue
            }
          }

          fired.add(note.id)
          changed = true
        }

        if (changed) saveFired(fired)
        if (changed || patched) void queryClient.invalidateQueries({ queryKey: ["notes"] })
        useNoteReminders.getState().refreshFromNotes(notes)
      } catch {
        // Reminder polling is background work; it should never interrupt the shell.
      } finally {
        busy = false
      }
    }

    void poll()
    const timer = window.setInterval(() => { void poll() }, POLL_MS)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [queryClient])

  return null
}
