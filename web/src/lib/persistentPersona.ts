const KEY = "odysseus-char-sessions"
export const PERSISTENT_PERSONA_CHANGED = "odysseus:persistent-persona-changed"

type PersistentPersonaSessions = Record<string, string>

function storageAvailable() {
  return typeof window !== "undefined" && !!window.localStorage
}

export function readPersistentPersonaSessions(): PersistentPersonaSessions {
  if (!storageAvailable()) return {}
  try {
    const parsed = JSON.parse(window.localStorage.getItem(KEY) || "{}") as unknown
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {}
    return Object.fromEntries(
      Object.entries(parsed as Record<string, unknown>)
        .filter(([id, name]) => id && typeof name === "string" && name.trim())
        .map(([id, name]) => [id, (name as string).trim()]),
    )
  } catch {
    return {}
  }
}

function writePersistentPersonaSessions(sessions: PersistentPersonaSessions) {
  if (!storageAvailable()) return
  window.localStorage.setItem(KEY, JSON.stringify(sessions))
  window.dispatchEvent(new CustomEvent(PERSISTENT_PERSONA_CHANGED))
}

export function getPersistentPersonaName(sessionId?: string | null) {
  if (!sessionId) return ""
  return readPersistentPersonaSessions()[sessionId] || ""
}

export function setPersistentPersonaSession(sessionId: string, personaName: string) {
  const name = personaName.trim()
  if (!sessionId || !name) return
  writePersistentPersonaSessions({ ...readPersistentPersonaSessions(), [sessionId]: name })
}

export function removePersistentPersonaSession(sessionId: string) {
  const sessions = readPersistentPersonaSessions()
  if (!sessions[sessionId]) return
  delete sessions[sessionId]
  writePersistentPersonaSessions(sessions)
}
