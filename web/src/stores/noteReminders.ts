import { create } from "zustand"
import type { Note, NoteItem } from "@/types"

export const NOTE_REMINDER_FIRED_KEY = "odysseus-notes-reminder-fired"
export const NOTE_REMINDER_GLOWED_KEY = "odysseus-notes-reminder-glowed"
export const NOTE_REMINDER_PENDING_HIGHLIGHT_KEY = "odysseus-notes-reminder-pending-highlight"
export const NOTE_REMINDER_ACTIVE_HIGHLIGHT_KEY = "odysseus-notes-reminder-active-highlight"
export const NOTE_REMINDER_DISMISSED_AT_KEY = "odysseus-notes-reminder-dismissed-at"

interface NoteReminderState {
  firedCount: number
  activeHighlightIds: string[]
  refreshFromNotes: (notes: Note[]) => void
  markFired: (noteId: string) => void
  markHighlight: (noteId: string) => void
  queueHighlight: (noteId: string) => void
  flushHighlights: (notes: Note[]) => string[]
  dismissHighlight: (noteId: string) => void
  dismissFired: (notes?: Note[]) => void
}

function loadSet(key: string): Set<string> {
  if (typeof localStorage === "undefined") return new Set()
  try {
    const value = JSON.parse(localStorage.getItem(key) || "[]")
    return new Set(Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [])
  } catch {
    return new Set()
  }
}

function saveSet(key: string, value: Set<string>): void {
  if (typeof localStorage === "undefined") return
  try {
    localStorage.setItem(key, JSON.stringify([...value]))
  } catch {
    /* localStorage may be unavailable or full */
  }
}

function loadDismissedAt(): number {
  if (typeof localStorage === "undefined") return 0
  try {
    const value = Number(localStorage.getItem(NOTE_REMINDER_DISMISSED_AT_KEY) || "0")
    return Number.isFinite(value) && value > 0 ? value : 0
  } catch {
    return 0
  }
}

function saveDismissedAt(value: number): void {
  if (typeof localStorage === "undefined") return
  try {
    localStorage.setItem(NOTE_REMINDER_DISMISSED_AT_KEY, String(value))
  } catch {
    /* ignore */
  }
}

export function hasReminderTime(value?: string): boolean {
  return typeof value === "string" && /T\d{2}:\d{2}/.test(value)
}

export function noteItemDone(item: NoteItem): boolean {
  return !!(item.done || item.checked)
}

export function isNoteFullyDone(note: Note): boolean {
  if (!Array.isArray(note.items) || note.items.length === 0) return false
  if (note.note_type !== "todo" && note.note_type !== "goal" && note.note_type !== "checklist") return false
  return note.items.every(noteItemDone)
}

export function hasActiveNoteReminder(note: Note, now = Date.now()): boolean {
  if (note.archived || isNoteFullyDone(note)) return false
  if (!note.due_date || !hasReminderTime(note.due_date)) return false
  const due = new Date(note.due_date).getTime()
  return Number.isFinite(due) && due <= now
}

export function countFiredNoteReminders(notes: Note[], dismissedAt = loadDismissedAt(), now = Date.now()): number {
  return notes.filter((note) => {
    if (!hasActiveNoteReminder(note, now)) return false
    const due = new Date(note.due_date || "").getTime()
    return Number.isFinite(due) && due > dismissedAt
  }).length
}

function activeHighlightArray(): string[] {
  return [...loadSet(NOTE_REMINDER_ACTIVE_HIGHLIGHT_KEY)]
}

export const useNoteReminders = create<NoteReminderState>((set, get) => ({
  firedCount: 0,
  activeHighlightIds: activeHighlightArray(),

  refreshFromNotes: (notes) => set({
    firedCount: countFiredNoteReminders(notes),
    activeHighlightIds: activeHighlightArray(),
  }),

  markFired: (noteId) => {
    if (!noteId) return
    const fired = loadSet(NOTE_REMINDER_FIRED_KEY)
    fired.add(noteId)
    saveSet(NOTE_REMINDER_FIRED_KEY, fired)
  },

  markHighlight: (noteId) => {
    if (!noteId) return
    const active = loadSet(NOTE_REMINDER_ACTIVE_HIGHLIGHT_KEY)
    active.add(noteId)
    saveSet(NOTE_REMINDER_ACTIVE_HIGHLIGHT_KEY, active)
    set({ activeHighlightIds: [...active] })
  },

  queueHighlight: (noteId) => {
    if (!noteId) return
    const pending = loadSet(NOTE_REMINDER_PENDING_HIGHLIGHT_KEY)
    pending.add(noteId)
    saveSet(NOTE_REMINDER_PENDING_HIGHLIGHT_KEY, pending)
    get().markHighlight(noteId)
  },

  flushHighlights: (notes) => {
    const pending = loadSet(NOTE_REMINDER_PENDING_HIGHLIGHT_KEY)
    const glowed = loadSet(NOTE_REMINDER_GLOWED_KEY)
    const active = loadSet(NOTE_REMINDER_ACTIVE_HIGHLIGHT_KEY)
    const visible = new Set(notes.map((note) => note.id))
    const next = new Set<string>()

    for (const id of pending) {
      if (visible.has(id)) next.add(id)
    }
    for (const note of notes) {
      if (!hasActiveNoteReminder(note)) continue
      if (pending.has(note.id) || !glowed.has(note.id)) next.add(note.id)
    }
    for (const id of next) {
      active.add(id)
      glowed.add(id)
    }
    saveSet(NOTE_REMINDER_PENDING_HIGHLIGHT_KEY, new Set())
    saveSet(NOTE_REMINDER_ACTIVE_HIGHLIGHT_KEY, active)
    saveSet(NOTE_REMINDER_GLOWED_KEY, glowed)
    set({ activeHighlightIds: [...active] })
    return [...next]
  },

  dismissHighlight: (noteId) => {
    if (!noteId) return
    const active = loadSet(NOTE_REMINDER_ACTIVE_HIGHLIGHT_KEY)
    active.delete(noteId)
    saveSet(NOTE_REMINDER_ACTIVE_HIGHLIGHT_KEY, active)
    set({ activeHighlightIds: [...active] })
  },

  dismissFired: (notes) => {
    const dismissedAt = Date.now()
    saveDismissedAt(dismissedAt)
    set({ firedCount: notes ? countFiredNoteReminders(notes, dismissedAt) : 0 })
  },
}))
