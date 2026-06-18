import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { apiJson, apiFetch } from "@/lib/api"
import type { EmailMsg } from "@/types"

export function useInbox(folder = "INBOX") {
  return useQuery({
    queryKey: ["email", folder],
    queryFn: async () => {
      const r = await apiJson<{ emails?: EmailMsg[]; error?: string }>(`/api/email/list?folder=${encodeURIComponent(folder)}&limit=50`)
      return { emails: r.emails || [], error: r.error }
    },
  })
}

export function useFolders() {
  return useQuery({
    queryKey: ["email-folders"],
    queryFn: async () => {
      const r = await apiJson<{ folders?: string[]; error?: string }>("/api/email/folders")
      return { folders: r.folders || [], error: r.error }
    },
  })
}

export function useEmailSearch(query: string, folder = "INBOX") {
  const q = query.trim()
  return useQuery({
    queryKey: ["email-search", folder, q],
    enabled: q.length >= 2,
    queryFn: async () => {
      const r = await apiJson<{ emails?: EmailMsg[]; error?: string }>(`/api/email/search?q=${encodeURIComponent(q)}&folder=${encodeURIComponent(folder)}&limit=50`)
      return { emails: r.emails || [], error: r.error }
    },
  })
}

export interface EmailContact { name: string; address: string }
export function useContacts(query: string) {
  const q = query.trim()
  return useQuery({
    queryKey: ["email-contacts", q],
    enabled: q.length >= 1,
    queryFn: async () => {
      const r = await apiJson<{ contacts?: EmailContact[] }>(`/api/email/contacts?q=${encodeURIComponent(q)}&limit=8`)
      return r.contacts || []
    },
  })
}

export interface EmailAttachment {
  index: number; filename: string; content_type?: string; size?: number; is_inline?: boolean
}
export function useAttachments(uid: string | null, folder = "INBOX") {
  return useQuery({
    queryKey: ["email-attachments", uid, folder],
    enabled: !!uid,
    queryFn: async () => {
      const r = await apiJson<{ attachments?: EmailAttachment[]; error?: string }>(`/api/email/attachments/${uid}?folder=${encodeURIComponent(folder)}`)
      return { attachments: r.attachments || [], error: r.error }
    },
  })
}

export function attachmentDownloadUrl(uid: string, index: number, folder = "INBOX") {
  return `/api/email/attachment/${uid}/${index}?folder=${encodeURIComponent(folder)}`
}

export async function attachmentAsDoc(uid: string, index: number, folder = "INBOX"): Promise<{ doc_id?: string; filename?: string; error?: string }> {
  const r = await apiFetch(`/api/email/attachment-as-doc/${uid}/${index}?folder=${encodeURIComponent(folder)}`, { method: "POST" })
  return r.json().catch(() => ({ error: `HTTP ${r.status}` }))
}

export interface EmailBody {
  subject?: string; from?: string; from_addr?: string; sender?: string; date?: string;
  from_name?: string; from_address?: string;
  body_html?: string; html?: string; body_text?: string; body?: string; text?: string; error?: string;
  is_answered?: boolean; is_flagged?: boolean; attachments?: EmailAttachment[];
}
export function useEmail(uid: string | null, folder = "INBOX") {
  return useQuery({
    queryKey: ["email-read", uid, folder],
    enabled: !!uid,
    queryFn: () => apiJson<EmailBody>(`/api/email/read/${uid}?folder=${encodeURIComponent(folder)}`),
  })
}

export interface ComposePayload { to: string; subject: string; body: string; account_id?: string }
async function post(path: string, p: ComposePayload): Promise<{ success?: boolean; error?: string }> {
  const r = await apiFetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(p) })
  try { return await r.json() } catch { return { success: r.ok } }
}
export const sendEmail = (p: ComposePayload) => post("/api/email/send", p)
export const saveDraft = (p: ComposePayload) => post("/api/email/draft", p)

// POST /api/email/ai-reply expects the email fields in a JSON body (original_body
// is required) and returns { success, reply, error, ... }.
export async function aiReply(email: { uid?: string; folder?: string; original_body: string; to?: string; subject?: string; model?: string }): Promise<{ success?: boolean; reply?: string; error?: string }> {
  const r = await apiFetch("/api/email/ai-reply", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(email) })
  return r.json().catch(() => ({ error: `HTTP ${r.status}` }))
}

export function useEmailActions(folder = "INBOX") {
  const qc = useQueryClient()
  const f = encodeURIComponent(folder)
  const inv = () => {
    qc.invalidateQueries({ queryKey: ["email", folder] })
    qc.invalidateQueries({ queryKey: ["email-search"] })
  }
  return {
    markUnread: useMutation({ mutationFn: async (uid: string) => { const r = await apiFetch(`/api/email/mark-unread/${uid}?folder=${f}`, { method: "POST" }); if (!r.ok) throw new Error("Couldn't mark the email unread") }, onSuccess: inv }),
    archive: useMutation({ mutationFn: async (uid: string) => { const r = await apiFetch(`/api/email/archive/${uid}?folder=${f}`, { method: "POST" }); if (!r.ok) throw new Error("Couldn't archive the email") }, onSuccess: inv }),
    remove: useMutation({ mutationFn: async (uid: string) => { const r = await apiFetch(`/api/email/delete/${uid}?folder=${f}`, { method: "DELETE" }); if (!r.ok) throw new Error("Couldn't delete the email") }, onSuccess: inv }),
    flag: useMutation({
      mutationFn: async (v: { uid: string; on: boolean }) => { const r = await apiFetch(`/api/email/flag/${v.uid}?folder=${f}&on=${v.on}`, { method: "POST" }); if (!r.ok) throw new Error("Couldn't flag the email") },
      onSuccess: (_d, v) => { inv(); qc.invalidateQueries({ queryKey: ["email-read", v.uid] }) },
    }),
    move: useMutation({
      mutationFn: async (v: { uid: string; dest: string }) => { const r = await apiFetch(`/api/email/move/${v.uid}?folder=${f}&dest=${encodeURIComponent(v.dest)}`, { method: "POST" }); if (!r.ok) throw new Error("Couldn't move the email") },
      onSuccess: inv,
    }),
    markAnswered: useMutation({
      mutationFn: async (uid: string) => { const r = await apiFetch(`/api/email/mark-answered/${uid}?folder=${f}`, { method: "POST" }); if (!r.ok) throw new Error("Couldn't update the email") },
      onSuccess: (_d, uid) => { inv(); qc.invalidateQueries({ queryKey: ["email-read", uid] }) },
    }),
  }
}
