import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { apiJson, apiFetch } from "@/lib/api"
import type { Note, NoteItem } from "@/types"

export interface NotePayload {
  title?: string
  content?: string
  items?: NoteItem[] | null
  note_type?: string
  color?: string
  label?: string
  pinned?: boolean
  archived?: boolean
  due_date?: string
  image_url?: string
  repeat?: string
  sort_order?: number
  agent_session_id?: string
}

export function useNotes(opts: { archived?: boolean } = {}) {
  const qs = opts.archived ? "?archived=true" : ""
  return useQuery({
    queryKey: ["notes", { archived: !!opts.archived }],
    queryFn: async () => (await apiJson<{ notes: Note[] }>(`/api/notes${qs}`)).notes,
  })
}
export function useNoteMutations() {
  const qc = useQueryClient()
  const inv = () => qc.invalidateQueries({ queryKey: ["notes"] })
  return {
    create: useMutation({
      mutationFn: async (v: NotePayload) => {
        const r = await apiFetch("/api/notes", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            title: v.title || "",
            content: v.content || "",
            items: v.items ?? undefined,
            note_type: v.note_type || "note",
            color: v.color || undefined,
            label: v.label || undefined,
            pinned: !!v.pinned,
            archived: !!v.archived,
            due_date: v.due_date || undefined,
            image_url: v.image_url || undefined,
            repeat: v.repeat || "none",
            sort_order: v.sort_order,
          }),
        })
        if (!r.ok) throw new Error("create failed"); return r.json()
      },
      onSuccess: inv,
    }),
    update: useMutation({
      mutationFn: async (v: { id: string } & NotePayload) => {
        const { id, ...body } = v
        const r = await apiFetch(`/api/notes/${id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        })
        if (!r.ok) throw new Error("update failed"); return r.json()
      },
      onSuccess: inv,
    }),
    remove: useMutation({ mutationFn: async (id: string) => { const r = await apiFetch(`/api/notes/${id}`, { method: "DELETE" }); if (!r.ok) throw new Error("Couldn't delete the note") }, onSuccess: inv }),
    pin: useMutation({ mutationFn: async (id: string) => { const r = await apiFetch(`/api/notes/${id}/pin`, { method: "POST" }); if (!r.ok) throw new Error("Couldn't update the note") }, onSuccess: inv }),
    archive: useMutation({ mutationFn: async (id: string) => { const r = await apiFetch(`/api/notes/${id}/archive`, { method: "POST" }); if (!r.ok) throw new Error("Couldn't archive the note") }, onSuccess: inv }),
    toggleItem: useMutation({ mutationFn: async ({ id, index }: { id: string; index: number }) => { const r = await apiFetch(`/api/notes/${id}/items/${index}/toggle`, { method: "POST" }); if (!r.ok) throw new Error("Couldn't update the item") }, onSuccess: inv }),
    reorder: useMutation({
      mutationFn: async (ids: string[]) => {
        const r = await apiFetch("/api/notes/reorder", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ids }),
        })
        if (!r.ok) throw new Error("Couldn't reorder notes")
      },
      onSuccess: inv,
    }),
    solveAgent: useMutation({
      mutationFn: solveNoteWithAgent,
      onSuccess: () => {
        inv()
        qc.invalidateQueries({ queryKey: ["sessions"] })
      },
    }),
  }
}

function noteAgentPrompt(note: Note): string {
  const parts: string[] = []
  if ((note.title || "").trim()) parts.push((note.title || "").trim())
  if ((note.content || "").trim()) parts.push((note.content || "").trim())
  if (Array.isArray(note.items)) {
    for (const item of note.items) {
      const done = !!(item.done || item.checked)
      const text = (item.text || "").trim()
      if (!done && text) parts.push(`- ${text}`)
    }
  }
  const body = parts.join("\n")
  return body ? `Help me get this done:\n\n${body}` : ""
}

async function drainStream(res: Response) {
  if (!res.ok || !res.body) return
  const reader = res.body.getReader()
  while (true) {
    const { done } = await reader.read()
    if (done) return
  }
}

export async function solveNoteWithAgent(note: Note): Promise<string> {
  const prompt = noteAgentPrompt(note)
  if (!prompt) throw new Error("Nothing to solve")

  const dc = await apiJson<{ endpoint_url?: string; endpoint_id?: string; model?: string }>("/api/default-chat")
  if (!dc.endpoint_url || !dc.model) throw new Error("No default chat model configured")

  const label = (note.title || note.items?.[0]?.text || "todo").slice(0, 40)
  const fd = new FormData()
  fd.set("name", `Agent: ${label}`)
  fd.set("endpoint_url", dc.endpoint_url)
  fd.set("model", dc.model)
  if (dc.endpoint_id) fd.set("endpoint_id", dc.endpoint_id)
  fd.set("skip_validation", "true")
  const sessionRes = await apiFetch("/api/session", { method: "POST", body: fd })
  if (!sessionRes.ok) throw new Error("Could not create agent session")
  const session = (await sessionRes.json()) as { id?: string }
  const sid = session.id
  if (!sid) throw new Error("Could not create agent session")

  const linkRes = await apiFetch(`/api/notes/${note.id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ agent_session_id: sid }),
  })
  if (!linkRes.ok) throw new Error("Could not link agent session")

  const run = new FormData()
  run.set("message", prompt)
  run.set("session", sid)
  run.set("mode", "agent")
  void apiFetch("/api/chat_stream", { method: "POST", body: run }).then(drainStream).catch(() => {})
  return sid
}

export async function uploadNoteImage(file: File | Blob, filename = "note-image.png"): Promise<string> {
  const fd = new FormData()
  fd.append("files", file, file instanceof File ? file.name : filename)
  const r = await apiFetch("/api/upload", { method: "POST", body: fd })
  if (!r.ok) throw new Error("Couldn't upload image")
  const data = (await r.json()) as { files?: { id?: string }[] }
  const fileId = data.files?.[0]?.id
  if (!fileId) throw new Error("Couldn't upload image")
  const origin = typeof window !== "undefined" ? window.location.origin : ""
  return `${origin}/api/upload/${encodeURIComponent(fileId)}`
}
