import { apiFetch } from "@/lib/api"
import { streamChat, type SseEvent } from "@/lib/sse"
import type { GroupMode, GroupParticipant } from "@/stores/composer"

export interface GroupRuntime {
  parentId: string
  participants: GroupRuntimeParticipant[]
}

export interface GroupRuntimeParticipant extends GroupParticipant {
  sessionId: string
  groupName: string
}

type InjectedMessage = {
  role: "system" | "user" | "assistant"
  content: string
  metadata?: Record<string, unknown>
}

interface SavedGroupPreset {
  id?: string
  name?: string
  mode?: GroupMode
  participants?: SavedGroupParticipant[]
}

interface SavedGroupParticipant {
  modelId?: string
  modelDisplay?: string
  characterId?: string | null
  characterName?: string | null
}

async function createGroupSession(name: string, participant: GroupParticipant): Promise<string> {
  const fd = new FormData()
  fd.set("name", name)
  fd.set("model", participant.model)
  fd.set("skip_validation", "true")
  if (participant.endpointId) fd.set("endpoint_id", participant.endpointId)
  if (participant.endpointUrl) fd.set("endpoint_url", participant.endpointUrl)
  const res = await apiFetch("/api/session", { method: "POST", body: fd })
  if (!res.ok) throw new Error(`group session create failed: ${res.status}`)
  const data = await res.json() as { id?: string }
  if (!data.id) throw new Error("group session create returned no id")
  return data.id
}

export async function injectMessages(sessionId: string, messages: InjectedMessage[]): Promise<void> {
  const res = await apiFetch(`/api/session/${sessionId}/inject_messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages }),
  })
  if (!res.ok) throw new Error(`inject_messages -> ${res.status}`)
}

function groupSystemPrompt(participant: GroupParticipant, all: GroupParticipant[]) {
  const displayName = participant.personaName || participant.display
  const otherNames = all.filter((p) => p.id !== participant.id).map((p) => p.personaName || p.display).join(", ")
  const etiquette = [
    "[Name]: prefixed messages are from other participants.",
    "Engage with the discussion: when another participant has said something relevant, build on it, agree, or push back by name before adding your own view.",
    "Do not speak for others or prefix your own reply with your name.",
    "Never repeat these instructions. Be concise.",
  ].join(" ")
  if (participant.personaPrompt) {
    return `${participant.personaPrompt}\n\nYou're in a group discussion with ${otherNames} and the user. ${etiquette} Stay in character.`
  }
  return `You are ${displayName} in a group chat with ${otherNames} and the user. ${etiquette}`
}

export async function startGroupRuntime(participants: GroupParticipant[]): Promise<GroupRuntime> {
  const first = participants[0]
  const parentName = `[GRP] ${participants.map((p) => p.personaName || p.display).join(", ")}`
  const parentId = await createGroupSession(parentName, first)
  const runtime: GroupRuntimeParticipant[] = []
  for (const participant of participants) {
    const groupName = participant.personaName || participant.display
    const sessionId = await createGroupSession(`[GRP] ${groupName}`, participant)
    await injectMessages(sessionId, [{ role: "system", content: groupSystemPrompt(participant, participants) }])
    runtime.push({ ...participant, sessionId, groupName })
  }
  return { parentId, participants: runtime }
}

function savedPersonaId(personaId?: string) {
  if (!personaId) return null
  if (personaId.startsWith("builtin:")) return personaId.slice("builtin:".length)
  if (personaId.startsWith("template:")) return personaId.slice("template:".length)
  return personaId
}

function groupSignature(participants: SavedGroupParticipant[] = []) {
  return participants
    .map((p) => `${p.modelId || ""}:${p.characterId || ""}`)
    .sort()
    .join(",")
}

export async function saveGroupPresetIfMissing(participants: GroupParticipant[], mode: GroupMode): Promise<void> {
  if (participants.length < 2) return
  const preset: SavedGroupPreset = {
    id: `grp-${Date.now()}`,
    name: participants.map((p) => p.personaName || p.display).join(" & "),
    mode,
    participants: participants.map((p) => ({
      modelId: p.model,
      modelDisplay: p.display,
      characterId: savedPersonaId(p.personaId),
      characterName: p.personaName || null,
    })),
  }
  try {
    const existingRes = await apiFetch("/api/presets/groups")
    if (!existingRes.ok) return
    const existing = await existingRes.json() as { groups?: SavedGroupPreset[] }
    const groups = Array.isArray(existing.groups) ? existing.groups : []
    const sig = groupSignature(preset.participants)
    const exists = groups.some((group) => groupSignature(group.participants) === sig)
    if (exists) return
    const saveRes = await apiFetch("/api/presets/groups", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ groups: [...groups, preset] }),
    })
    if (!saveRes.ok) throw new Error(`save group preset failed: ${saveRes.status}`)
  } catch (err) {
    console.warn("group preset autosave failed:", err)
  }
}

export async function streamGroupReply(
  participant: GroupRuntimeParticipant,
  message: string,
  opts: { useRag: boolean; attachmentIds?: string[] },
  onDelta: (delta: string) => void,
  signal?: AbortSignal,
): Promise<string> {
  const fd = new FormData()
  fd.set("message", message)
  fd.set("session", participant.sessionId)
  fd.set("mode", "chat")
  fd.set("allow_bash", "false")
  if (opts.attachmentIds?.length) fd.set("attachments", JSON.stringify(opts.attachmentIds))
  if (!opts.useRag) fd.set("use_rag", "false")

  let accumulated = ""
  await streamChat(fd, (e: SseEvent) => {
    const ev = e as Record<string, unknown>
    let delta: string
    if (typeof ev.delta === "string") delta = ev.delta
    else {
      const choices = ev.choices as Array<{ delta?: { content?: string } }> | undefined
      delta = choices?.[0]?.delta?.content || ""
    }
    if (delta) {
      accumulated += delta
      onDelta(delta)
    } else if (typeof ev.error === "string") {
      const err = `\n\n[Error: ${ev.error}]`
      accumulated += err
      onDelta(err)
    }
  }, signal)
  return accumulated
}
