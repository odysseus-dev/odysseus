import { useQuery, useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { apiJson, apiFetch } from "@/lib/api"
import type { EmailMsg } from "@/types"

// Page size for the inbox list. The backend caps each list call to this many
// messages and reports a folder-wide `total`, so we page with offset.
export const INBOX_PAGE_SIZE = 50

function accountQs(accountId?: string | null, prefix = "&") {
  return accountId ? `${prefix}account_id=${encodeURIComponent(accountId)}` : ""
}

export type EmailListFilter =
  | "all"
  | "unread"
  | "favorites"
  | "undone"
  | "reminders"
  | "unanswered"
  | "pending_30d"
  | "stale_30d"
  | "tag:urgent"
  | "tag:reply-soon"
  | "tag:spam"
  | "tag:newsletter"
  | "tag:marketing"

export interface EmailListOptions {
  accountId?: string | null
  filter?: EmailListFilter
  from?: string | null
  hasAttachments?: boolean
  limit?: number
  enabled?: boolean
}

function emailListQuery(folder: string, options: EmailListOptions, offset: number) {
  const params = new URLSearchParams({
    folder,
    limit: String(options.limit ?? INBOX_PAGE_SIZE),
    offset: String(offset),
    filter: options.filter || "all",
  })
  if (options.accountId) params.set("account_id", options.accountId)
  if (options.from) params.set("from", options.from)
  if (options.hasAttachments) params.set("has_attachments", "1")
  return params.toString()
}

interface EmailListPage {
  emails: EmailMsg[]
  error?: string
  total: number
  offset: number
}

// Inbox list with offset-based pagination. The folder/filter/search/sender
// behaviour is preserved — those values are part of the query key, so changing
// any of them resets paging back to the first page.
export function useInbox(folder = "INBOX", options: EmailListOptions = {}) {
  const accountKey = options.accountId || ""
  const filter = options.filter || "all"
  const from = options.from || ""
  const hasAttachments = !!options.hasAttachments
  const pageSize = options.limit ?? INBOX_PAGE_SIZE
  return useInfiniteQuery({
    queryKey: ["email", folder, accountKey, filter, from, hasAttachments],
    enabled: options.enabled ?? true,
    initialPageParam: 0,
    queryFn: async ({ pageParam }) => {
      const offset = typeof pageParam === "number" ? pageParam : 0
      const r = await apiJson<{ emails?: EmailMsg[]; error?: string; total?: number; offset?: number }>(
        `/api/email/list?${emailListQuery(folder, options, offset)}`,
      )
      return { emails: r.emails || [], error: r.error, total: r.total ?? 0, offset } satisfies EmailListPage
    },
    getNextPageParam: (lastPage) => {
      const loaded = lastPage.offset + lastPage.emails.length
      // Stop paging once we've seen every message the folder reports, or when a
      // page comes back short (the backend ran out of messages early).
      if (lastPage.emails.length < pageSize) return undefined
      return loaded < lastPage.total ? loaded : undefined
    },
  })
}

// Read-only unread/urgency snapshot written by the backend urgency task.
// Used by InboxPoller to detect new mail and by anything wanting the badge.
export interface EmailUrgencyState {
  total_unread: number
  total_urgent?: number
  max_score?: number
  per_uid?: Record<string, unknown>
}

export async function fetchEmailUnreadState(): Promise<EmailUrgencyState | null> {
  try {
    const r = await apiFetch("/api/email/urgency-state")
    if (!r.ok) return null
    const data = await r.json().catch(() => null) as EmailUrgencyState | null
    if (!data || typeof data.total_unread !== "number") return null
    return data
  } catch {
    return null
  }
}

// The urgency snapshot is produced asynchronously and can be stale. This call
// performs the same live IMAP list query as the inbox and uses its folder-wide
// unread total, so the global poller observes newly arrived mail immediately.
export async function fetchLiveEmailUnreadCount(): Promise<number | null> {
  try {
    const r = await apiFetch(`/api/email/list?${emailListQuery("INBOX", { filter: "unread", limit: 1 }, 0)}`)
    if (!r.ok) return null
    const data = await r.json().catch(() => null) as { total?: number; emails?: EmailMsg[] } | null
    if (!data) return null
    if (typeof data.total === "number") return data.total
    return Array.isArray(data.emails) ? data.emails.filter((m) => !m.is_read).length : null
  } catch { return null }
}

export function useFolders(accountId?: string | null) {
  const accountKey = accountId || ""
  return useQuery({
    queryKey: ["email-folders", accountKey],
    queryFn: async () => {
      const r = await apiJson<{ folders?: string[]; error?: string }>(`/api/email/folders${accountQs(accountId, "?")}`)
      return { folders: r.folders || [], error: r.error }
    },
  })
}

// Server-side IMAP search, paged like the inbox. `total` is the full match
// count, so the client knows when to stop loading. Changing q/folder/account
// resets paging (they're part of the query key).
export function useEmailSearch(query: string, folder = "INBOX", accountId?: string | null, enabled = true) {
  const q = query.trim()
  const accountKey = accountId || ""
  const pageSize = 50
  return useInfiniteQuery({
    queryKey: ["email-search", folder, q, accountKey],
    enabled: enabled && q.length >= 2,
    initialPageParam: 0,
    queryFn: async ({ pageParam }) => {
      const offset = typeof pageParam === "number" ? pageParam : 0
      const r = await apiJson<{ emails?: EmailMsg[]; error?: string; total?: number }>(
        `/api/email/search?q=${encodeURIComponent(q)}&folder=${encodeURIComponent(folder)}&limit=${pageSize}&offset=${offset}${accountQs(accountId)}`,
      )
      return { emails: r.emails || [], error: r.error, total: r.total ?? 0, offset }
    },
    getNextPageParam: (lastPage) => {
      const loaded = lastPage.offset + lastPage.emails.length
      if (lastPage.emails.length < pageSize) return undefined
      return loaded < lastPage.total ? loaded : undefined
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
export function useAttachments(uid: string | null, folder = "INBOX", accountId?: string | null) {
  const accountKey = accountId || ""
  return useQuery({
    queryKey: ["email-attachments", uid, folder, accountKey],
    enabled: !!uid,
    queryFn: async () => {
      const r = await apiJson<{ attachments?: EmailAttachment[]; error?: string }>(`/api/email/attachments/${uid}?folder=${encodeURIComponent(folder)}${accountQs(accountId)}`)
      return { attachments: r.attachments || [], error: r.error }
    },
  })
}

export function attachmentDownloadUrl(uid: string, index: number, folder = "INBOX", accountId?: string | null) {
  return `/api/email/attachment/${uid}/${index}?folder=${encodeURIComponent(folder)}${accountQs(accountId)}`
}

export async function attachmentAsDoc(uid: string, index: number, folder = "INBOX", accountId?: string | null): Promise<{ doc_id?: string; filename?: string; error?: string }> {
  const r = await apiFetch(`/api/email/attachment-as-doc/${uid}/${index}?folder=${encodeURIComponent(folder)}${accountQs(accountId)}`, { method: "POST" })
  return r.json().catch(() => ({ error: `HTTP ${r.status}` }))
}

export interface EmailBody {
  subject?: string; from?: string; from_addr?: string; sender?: string; date?: string;
  from_name?: string; from_address?: string;
  to?: string; cc?: string; message_id?: string; in_reply_to?: string; references?: string; account_id?: string;
  body_html?: string; html?: string; body_text?: string; body?: string; text?: string; error?: string;
  cached_summary?: string | null; cached_ai_reply?: string | null;
  boundaries?: EmailBoundaries | null; sender_signature?: string | null; thread_turns?: ThreadTurn[] | null;
  is_answered?: boolean; is_flagged?: boolean; attachments?: EmailAttachment[];
}

export interface EmailBoundaries {
  sig_start?: number | null
  quote_start?: number | null
}

// One reply turn from the backend thread parser (src/email_thread_parser.py).
// level 0 is the current message; deeper levels are progressively older quoted
// material. `meta` is an attribution string like "Alice <a@x> · May 5".
export interface ThreadTurn {
  level: number
  body_html: string
  meta?: string | null
}
export function useEmail(uid: string | null, folder = "INBOX", accountId?: string | null) {
  const accountKey = accountId || ""
  return useQuery({
    queryKey: ["email-read", uid, folder, accountKey],
    enabled: !!uid,
    queryFn: () => apiJson<EmailBody>(`/api/email/read/${uid}?folder=${encodeURIComponent(folder)}${accountQs(accountId)}`),
  })
}

export interface ComposePayload {
  to: string; subject: string; body: string; cc?: string; bcc?: string; body_html?: string;
  in_reply_to?: string; references?: string; attachments?: string[]; account_id?: string;
}
async function post(path: string, p: ComposePayload): Promise<{ success?: boolean; error?: string }> {
  const r = await apiFetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(p) })
  try { return await r.json() } catch { return { success: r.ok } }
}
export const sendEmail = (p: ComposePayload) => post("/api/email/send", p)
export const saveDraft = (p: ComposePayload) => post("/api/email/draft", p)

export interface ComposeUpload {
  token: string
  filename: string
  size?: number
}

export interface ScheduleEmailPayload extends ComposePayload {
  send_at: string
}

export async function uploadComposeAttachment(file: File): Promise<ComposeUpload> {
  const form = new FormData()
  form.append("file", file)
  const r = await apiFetch("/api/email/compose-upload", { method: "POST", body: form })
  const data = await r.json().catch(() => ({} as { success?: boolean; token?: string; filename?: string; size?: number; error?: string }))
  if (!r.ok || data.success === false || !data.token) {
    throw new Error(data.error || "Couldn't upload attachment")
  }
  return {
    token: data.token,
    filename: data.filename || file.name,
    size: data.size ?? file.size,
  }
}

export async function deleteComposeAttachment(token: string): Promise<void> {
  const r = await apiFetch(`/api/email/compose-upload/${encodeURIComponent(token)}`, { method: "DELETE" })
  const data = await r.json().catch(() => ({} as { success?: boolean; error?: string }))
  if (!r.ok || data.success === false) throw new Error(data.error || "Couldn't remove attachment")
}

export async function scheduleEmail(p: ScheduleEmailPayload): Promise<{ success?: boolean; id?: string; send_at?: string; error?: string }> {
  const r = await apiFetch("/api/email/schedule", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(p) })
  try { return await r.json() } catch { return { success: r.ok } }
}

export interface ScheduledEmail {
  id: string
  to?: string
  cc?: string
  subject?: string
  send_at?: string
  created_at?: string
  status?: "pending" | "failed" | string
  error?: string | null
}

export function useScheduledEmails() {
  return useQuery({
    queryKey: ["email-scheduled"],
    queryFn: async () => {
      const r = await apiJson<{ scheduled?: ScheduledEmail[]; error?: string }>("/api/email/scheduled")
      return { scheduled: r.scheduled || [], error: r.error }
    },
  })
}

export function useCancelScheduledEmail() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (id: string) => {
      const r = await apiFetch(`/api/email/scheduled/${encodeURIComponent(id)}`, { method: "DELETE" })
      const data = await r.json().catch(() => ({} as { success?: boolean; error?: string }))
      if (!r.ok || data.success === false) throw new Error(data.error || "Couldn't cancel scheduled email")
      return data
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["email-scheduled"] })
    },
  })
}

// POST /api/email/ai-reply expects the email fields in a JSON body (original_body
// is required) and returns { success, reply, error, ... }.
export async function aiReply(email: {
  uid?: string; folder?: string; original_body: string; to?: string; subject?: string;
  model?: string; session_id?: string; message_id?: string; fast?: boolean; user_hint?: string; account_id?: string;
}): Promise<{ success?: boolean; reply?: string; error?: string; cached?: boolean }> {
  const r = await apiFetch("/api/email/ai-reply", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(email) })
  return r.json().catch(() => ({ error: `HTTP ${r.status}` }))
}

export async function summarizeEmail(email: {
  uid?: string; folder?: string; body: string; subject?: string; from?: string; message_id?: string; account_id?: string;
}): Promise<{ success?: boolean; summary?: string; error?: string; model_used?: string }> {
  const r = await apiFetch("/api/email/summarize", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(email) })
  return r.json().catch(() => ({ error: `HTTP ${r.status}` }))
}

export async function deleteOdysseusReminderEmails(accountId?: string | null, permanent = true): Promise<{ success?: boolean; deleted?: number; error?: string }> {
  const params = new URLSearchParams({ permanent: permanent ? "1" : "0" })
  if (accountId) params.set("account_id", accountId)
  const r = await apiFetch(`/api/email/odysseus/reminders?${params.toString()}`, { method: "DELETE" })
  const data = await r.json().catch(() => ({ error: `HTTP ${r.status}` }))
  if (!r.ok || data.success === false) throw new Error(data.error || "Couldn't clear reminder emails")
  return data
}

export async function saveEmailSenderContact(v: { name?: string; email: string }): Promise<{ success?: boolean; message?: string; error?: string }> {
  const r = await apiFetch("/api/email/sender-contact", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: v.name || v.email.split("@")[0], email: v.email }),
  })
  const data = await r.json().catch(() => ({ error: `HTTP ${r.status}` }))
  if (!r.ok || data.success === false) throw new Error(data.error || "Couldn't save contact")
  return data
}

export function useEmailActions(folder = "INBOX", accountId?: string | null) {
  const qc = useQueryClient()
  const f = encodeURIComponent(folder)
  const a = accountQs(accountId)
  const accountKey = accountId || ""
  const inv = () => {
    qc.invalidateQueries({ queryKey: ["email", folder, accountKey] })
    qc.invalidateQueries({ queryKey: ["email-search"] })
  }
  return {
    markRead: useMutation({ mutationFn: async (uid: string) => { const r = await apiFetch(`/api/email/mark-read/${uid}?folder=${f}${a}`, { method: "POST" }); if (!r.ok) throw new Error("Couldn't mark the email read") }, onSuccess: inv }),
    markUnread: useMutation({ mutationFn: async (uid: string) => { const r = await apiFetch(`/api/email/mark-unread/${uid}?folder=${f}${a}`, { method: "POST" }); if (!r.ok) throw new Error("Couldn't mark the email unread") }, onSuccess: inv }),
    archive: useMutation({ mutationFn: async (uid: string) => { const r = await apiFetch(`/api/email/archive/${uid}?folder=${f}${a}`, { method: "POST" }); if (!r.ok) throw new Error("Couldn't archive the email") }, onSuccess: inv }),
    remove: useMutation({ mutationFn: async (uid: string) => { const r = await apiFetch(`/api/email/delete/${uid}?folder=${f}${a}`, { method: "DELETE" }); if (!r.ok) throw new Error("Couldn't delete the email") }, onSuccess: inv }),
    deletePermanent: useMutation({ mutationFn: async (uid: string) => { const r = await apiFetch(`/api/email/delete-permanent/${uid}?folder=${f}${a}`, { method: "DELETE" }); if (!r.ok) throw new Error("Couldn't permanently delete the email") }, onSuccess: inv }),
    flag: useMutation({
      mutationFn: async (v: { uid: string; on: boolean }) => { const r = await apiFetch(`/api/email/flag/${v.uid}?folder=${f}&on=${v.on}${a}`, { method: "POST" }); if (!r.ok) throw new Error("Couldn't flag the email") },
      onSuccess: (_d, v) => { inv(); qc.invalidateQueries({ queryKey: ["email-read", v.uid] }) },
    }),
    move: useMutation({
      mutationFn: async (v: { uid: string; dest: string }) => { const r = await apiFetch(`/api/email/move/${v.uid}?folder=${f}&dest=${encodeURIComponent(v.dest)}${a}`, { method: "POST" }); if (!r.ok) throw new Error("Couldn't move the email") },
      onSuccess: inv,
    }),
    markAnswered: useMutation({
      mutationFn: async (uid: string) => { const r = await apiFetch(`/api/email/mark-answered/${uid}?folder=${f}${a}`, { method: "POST" }); if (!r.ok) throw new Error("Couldn't update the email") },
      onSuccess: (_d, uid) => { inv(); qc.invalidateQueries({ queryKey: ["email-read", uid] }) },
    }),
    unflagSpam: useMutation({
      // POST /api/email/{uid}/unflag-spam — user override marking mail "not spam".
      // The route is owner-scoped only; it takes no folder/account query params.
      mutationFn: async (uid: string) => {
        const r = await apiFetch(`/api/email/${encodeURIComponent(uid)}/unflag-spam`, { method: "POST" })
        const data = await r.json().catch(() => ({} as { ok?: boolean; error?: string }))
        if (!r.ok || data.ok === false) throw new Error(data.error || "Couldn't mark the email as not spam")
        return data
      },
      onSuccess: (_d, uid) => { inv(); qc.invalidateQueries({ queryKey: ["email-read", uid] }) },
    }),
    clearAnswered: useMutation({
      mutationFn: async (uid: string) => { const r = await apiFetch(`/api/email/clear-answered/${uid}?folder=${f}${a}`, { method: "POST" }); if (!r.ok) throw new Error("Couldn't update the email") },
      onSuccess: (_d, uid) => { inv(); qc.invalidateQueries({ queryKey: ["email-read", uid] }) },
    }),
    deleteReminderEmails: useMutation({
      mutationFn: async (v?: { permanent?: boolean }) => deleteOdysseusReminderEmails(accountId, v?.permanent ?? true),
      onSuccess: () => {
        qc.invalidateQueries({ queryKey: ["email"] })
        qc.invalidateQueries({ queryKey: ["email-search"] })
      },
    }),
  }
}
