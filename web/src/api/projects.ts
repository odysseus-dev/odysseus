import { useQueryClient } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { usePrefs, useSetPref } from "@/api/prefs"
import { useSessions } from "@/api/sessions"
import type { Session } from "@/types"

// Projects are built on the existing per-session `folder` field. Membership is
// authoritative on the session (folder = project name); per-project instructions
// and the canonical project list live in user prefs so empty projects persist
// and the backend can inject instructions for chats in the project.

export interface Project { name: string; count: number; instructions: string }

function readInstructions(prefs: Record<string, unknown> | undefined): Record<string, string> {
  const v = prefs?.project_instructions
  return v && typeof v === "object" ? (v as Record<string, string>) : {}
}
function readProjectList(prefs: Record<string, unknown> | undefined): string[] {
  const v = prefs?.projects
  return Array.isArray(v) ? (v as string[]).filter((x) => typeof x === "string") : []
}

export function useProjects() {
  const { data: sessions } = useSessions()
  const { data: prefs } = usePrefs()
  const instructions = readInstructions(prefs)
  const explicit = readProjectList(prefs)
  const counts = new Map<string, number>()
  for (const s of sessions || []) {
    if (s.folder && !s.archived) counts.set(s.folder, (counts.get(s.folder) || 0) + 1)
  }
  const names = Array.from(new Set([...explicit, ...Object.keys(instructions), ...counts.keys()]))
    .sort((a, b) => a.localeCompare(b))
  const projects: Project[] = names.map((n) => ({ name: n, count: counts.get(n) || 0, instructions: instructions[n] || "" }))
  return { projects, instructions, explicit }
}

/** Chats belonging to a project (folder), newest activity first. */
export function sessionsInProject(sessions: Session[] | undefined, name: string): Session[] {
  return (sessions || [])
    .filter((s) => !s.archived && s.folder === name)
    .sort((a, b) => new Date(b.last_message_at || b.updated_at || 0).getTime() - new Date(a.last_message_at || a.updated_at || 0).getTime())
}

async function setSessionFolder(id: string, folder: string): Promise<void> {
  const fd = new FormData()
  fd.set("folder", folder) // "" clears the assignment
  const r = await apiFetch(`/api/session/${id}`, { method: "PATCH", body: fd })
  if (!r.ok) throw new Error("Couldn't move the chat")
}

export function useProjectActions() {
  const qc = useQueryClient()
  const { data: prefs } = usePrefs()
  const setPref = useSetPref()
  const invSessions = () => qc.invalidateQueries({ queryKey: ["sessions"] })

  const saveList = (names: string[]) => setPref.mutateAsync({ key: "projects", value: Array.from(new Set(names)) })
  const saveInstructions = (map: Record<string, string>) => setPref.mutateAsync({ key: "project_instructions", value: map })

  return {
    create: async (name: string) => {
      const n = name.trim()
      if (!n) return
      await saveList([...readProjectList(prefs), n])
    },
    setInstructions: async (name: string, text: string) => {
      const map = { ...readInstructions(prefs) }
      if (text.trim()) map[name] = text
      else delete map[name]
      await saveInstructions(map)
    },
    assign: async (sessionId: string, name: string | null) => {
      await setSessionFolder(sessionId, name || "")
      invSessions()
    },
    rename: async (oldName: string, newName: string, members: Session[]) => {
      const n = newName.trim()
      if (!n || n === oldName) return
      for (const s of members) await setSessionFolder(s.id, n)
      const map = { ...readInstructions(prefs) }
      if (map[oldName] !== undefined) { map[n] = map[oldName]; delete map[oldName] }
      await saveInstructions(map)
      await saveList(readProjectList(prefs).filter((x) => x !== oldName).concat(n))
      invSessions()
    },
    remove: async (name: string, members: Session[]) => {
      for (const s of members) await setSessionFolder(s.id, "")
      const map = { ...readInstructions(prefs) }
      delete map[name]
      await saveInstructions(map)
      await saveList(readProjectList(prefs).filter((x) => x !== name))
      invSessions()
    },
    isSaving: setPref.isPending,
  }
}
