import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { apiJson, apiFetch } from "@/lib/api"
import type { EmailMsg } from "@/types"

export function useInbox() {
  return useQuery({
    queryKey: ["email", "INBOX"],
    queryFn: async () => {
      const r = await apiJson<{ emails?: EmailMsg[]; error?: string }>("/api/email/list?folder=INBOX&limit=50")
      return { emails: r.emails || [], error: r.error }
    },
  })
}

export interface EmailBody {
  subject?: string; from?: string; from_addr?: string; sender?: string; date?: string;
  from_name?: string; from_address?: string;
  body_html?: string; html?: string; body_text?: string; body?: string; text?: string; error?: string;
}
export function useEmail(uid: string | null) {
  return useQuery({
    queryKey: ["email-read", uid],
    enabled: !!uid,
    queryFn: () => apiJson<EmailBody>(`/api/email/read/${uid}?folder=INBOX`),
  })
}

export interface ComposePayload { to: string; subject: string; body: string; account_id?: string }
async function post(path: string, p: ComposePayload): Promise<{ success?: boolean; error?: string }> {
  const r = await apiFetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(p) })
  try { return await r.json() } catch { return { success: r.ok } }
}
export const sendEmail = (p: ComposePayload) => post("/api/email/send", p)
export const saveDraft = (p: ComposePayload) => post("/api/email/draft", p)

export async function aiReply(uid: string): Promise<{ reply?: string; body?: string; error?: string }> {
  const r = await apiFetch(`/api/email/ai-reply/${uid}?folder=INBOX`, { method: "POST" })
  return r.json().catch(() => ({ error: `HTTP ${r.status}` }))
}

export function useEmailActions() {
  const qc = useQueryClient()
  const inv = () => qc.invalidateQueries({ queryKey: ["email", "INBOX"] })
  return {
    markUnread: useMutation({ mutationFn: async (uid: string) => { await apiFetch(`/api/email/mark-unread/${uid}?folder=INBOX`, { method: "POST" }) }, onSuccess: inv }),
    archive: useMutation({ mutationFn: async (uid: string) => { await apiFetch(`/api/email/archive/${uid}?folder=INBOX`, { method: "POST" }) }, onSuccess: inv }),
    remove: useMutation({ mutationFn: async (uid: string) => { await apiFetch(`/api/email/delete/${uid}?folder=INBOX`, { method: "DELETE" }) }, onSuccess: inv }),
  }
}
