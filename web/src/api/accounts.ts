import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { apiJson, apiFetch } from "@/lib/api"

const jx = (method: string, path: string, body?: unknown) =>
  apiFetch(path, { method, headers: { "Content-Type": "application/json" }, body: body !== undefined ? JSON.stringify(body) : undefined })

// ───────────────────────── Email accounts ─────────────────────────
export interface EmailAccount {
  id: string; name: string; is_default: boolean; enabled: boolean
  imap_host: string; imap_port: number; imap_user: string; imap_starttls: boolean
  smtp_host: string; smtp_port: number; smtp_security: string; smtp_user: string
  from_address: string; display_name: string; has_imap_password: boolean; has_smtp_password: boolean
}
export type EmailAccountInput = Partial<EmailAccount> & { imap_password?: string; smtp_password?: string }

export function useEmailAccounts() {
  return useQuery({ queryKey: ["email-accounts"], retry: false, queryFn: async () => { try { return (await apiJson<{ accounts: EmailAccount[] }>("/api/email/accounts")).accounts } catch { return [] } } })
}
export function useEmailAccountMutations() {
  const qc = useQueryClient()
  const inv = () => qc.invalidateQueries({ queryKey: ["email-accounts"] })
  return {
    create: useMutation({ mutationFn: async (v: EmailAccountInput) => { const r = await jx("POST", "/api/email/accounts", v); const d = await r.json().catch(() => ({})); if (!r.ok || d.ok === false) throw new Error(d.error || "Create failed"); return d }, onSuccess: inv, meta: { silent: true } }),
    update: useMutation({ mutationFn: async (v: { id: string } & EmailAccountInput) => { const { id, ...rest } = v; const r = await jx("PUT", `/api/email/accounts/${id}`, rest); const d = await r.json().catch(() => ({})); if (!r.ok || d.ok === false) throw new Error(d.error || "Update failed"); return d }, onSuccess: inv, meta: { silent: true } }),
    remove: useMutation({ mutationFn: async (id: string) => { const r = await jx("DELETE", `/api/email/accounts/${id}`); if (!r.ok) throw new Error("Delete failed"); return r.json() }, onSuccess: inv }),
    setDefault: useMutation({ mutationFn: async (id: string) => { const r = await jx("POST", `/api/email/accounts/${id}/set-default`); if (!r.ok) throw new Error("Failed"); return r.json() }, onSuccess: inv }),
  }
}
export async function testEmailAccount(body: unknown): Promise<{ ok?: boolean; imap?: { ok?: boolean; error?: string }; smtp?: { ok?: boolean; error?: string } }> {
  const r = await jx("POST", "/api/email/accounts/test", body); return r.json().catch(() => ({ ok: false }))
}

// Email writing style
export function useEmailStyle() {
  return useQuery({ queryKey: ["email-style"], retry: false, queryFn: async () => { try { return await apiJson<{ style?: string; writing_style?: string }>("/api/email/style") } catch { return {} } } })
}
export async function saveEmailStyle(style: string) { await jx("PUT", "/api/email/style", { style }) }
export async function extractEmailStyle(): Promise<{ style?: string; writing_style?: string; error?: string }> {
  const r = await jx("POST", "/api/email/extract-style"); return r.json().catch(() => ({}))
}

// ───────────────────────── CalDAV (calendar) accounts ─────────────────────────
export interface CalDavAccount { id: string; label: string; url: string; username: string; has_password: boolean }
export function useCalDavAccounts() {
  return useQuery({ queryKey: ["caldav-accounts"], retry: false, queryFn: async () => { try { return (await apiJson<{ accounts: CalDavAccount[] }>("/api/calendar/config/accounts")).accounts } catch { return [] } } })
}
export function useCalDavMutations() {
  const qc = useQueryClient()
  const inv = () => qc.invalidateQueries({ queryKey: ["caldav-accounts"] })
  return {
    create: useMutation({ mutationFn: async (v: { label?: string; url: string; username?: string; password: string }) => { const r = await jx("POST", "/api/calendar/config/accounts", v); const d = await r.json().catch(() => ({})); if (!r.ok) throw new Error(d.detail || "Add failed"); return d }, onSuccess: inv, meta: { silent: true } }),
    update: useMutation({ mutationFn: async (v: { id: string; label?: string; url?: string; username?: string; password?: string }) => { const { id, ...rest } = v; const r = await jx("PUT", `/api/calendar/config/accounts/${id}`, rest); if (!r.ok) throw new Error("Update failed"); return r.json() }, onSuccess: inv, meta: { silent: true } }),
    remove: useMutation({ mutationFn: async (id: string) => { const r = await jx("DELETE", `/api/calendar/config/accounts/${id}`); if (!r.ok) throw new Error("Delete failed"); return r.json() }, onSuccess: inv }),
  }
}
export async function testCalDav(body: unknown): Promise<{ ok?: boolean; error?: string; calendars?: unknown[] }> {
  const r = await jx("POST", "/api/calendar/test", body); return r.json().catch(() => ({ ok: false }))
}

// ───────────────────────── MCP per-server tools ─────────────────────────
export interface McpTool { name: string; server_id: string; is_disabled: boolean; description?: string }
export function useMcpServerTools(serverId: string | null) {
  return useQuery({ queryKey: ["mcp-tools", serverId], enabled: !!serverId, retry: false, queryFn: async () => { try { return await apiJson<McpTool[]>(`/api/mcp/servers/${serverId}/tools`) } catch { return [] } } })
}
export function useSetMcpDisabledTools() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (v: { serverId: string; disabled: string[] }) => { const r = await jx("PATCH", `/api/mcp/servers/${v.serverId}/tools`, { disabled: v.disabled }); if (!r.ok) throw new Error("Couldn't update tool settings"); return r.json() },
    // Optimistically reflect the new disabled set so rapid consecutive toggles
    // build on the latest state instead of a stale query snapshot (which would
    // silently revert a just-toggled tool).
    onMutate: async (v) => {
      await qc.cancelQueries({ queryKey: ["mcp-tools", v.serverId] })
      const prev = qc.getQueryData<McpTool[]>(["mcp-tools", v.serverId])
      qc.setQueryData<McpTool[]>(["mcp-tools", v.serverId], (old) => (old || []).map((t) => ({ ...t, is_disabled: v.disabled.includes(t.name) })))
      return { prev }
    },
    onError: (_e, v, ctx) => { if (ctx?.prev) qc.setQueryData(["mcp-tools", v.serverId], ctx.prev) },
    onSettled: (_d, _e, v) => qc.invalidateQueries({ queryKey: ["mcp-tools", v.serverId] }),
  })
}

// ───────────────────────── Contacts ─────────────────────────
export function useContactsCount() {
  return useQuery({ queryKey: ["contacts-count"], retry: false, queryFn: async () => { try { const r = await apiJson<{ contacts?: unknown[]; items?: unknown[] }>("/api/contacts/list"); return (r.contacts || r.items || []).length } catch { return 0 } } })
}
export async function clearContacts() { const r = await jx("DELETE", "/api/contacts/clear"); return r.json().catch(() => ({})) }
export interface Contact {
  uid: string; name: string; emails: string[]; phones: string[]; address?: string
}
export interface CardDavConfig { url?: string; username?: string; password?: string }
export function useContactList() {
  return useQuery({ queryKey: ["contacts-list"], retry: false, queryFn: async () => {
    try { return (await apiJson<{ contacts: Contact[] }>("/api/contacts/list")).contacts || [] } catch { return [] }
  } })
}
export function useCardDavConfig() {
  return useQuery({ queryKey: ["carddav-config"], retry: false, queryFn: async () => {
    try { return await apiJson<CardDavConfig>("/api/contacts/config") } catch { return {} }
  } })
}
export function useContactMutations() {
  const qc = useQueryClient()
  const inv = () => { qc.invalidateQueries({ queryKey: ["contacts-list"] }); qc.invalidateQueries({ queryKey: ["contacts-count"] }) }
  return {
    add: useMutation({ mutationFn: async (v: { name: string; email: string; phone?: string; address?: string }) => { const r = await jx("POST", "/api/contacts/add", v); const d = await r.json(); if (!r.ok || d.success === false) throw new Error(d.error || "Add failed"); return d }, onSuccess: inv }),
    update: useMutation({ mutationFn: async (v: Contact) => { const r = await jx("PUT", `/api/contacts/${encodeURIComponent(v.uid)}`, v); const d = await r.json(); if (!r.ok || d.success === false) throw new Error(d.error || "Update failed"); return d }, onSuccess: inv }),
    remove: useMutation({ mutationFn: async (uid: string) => { const r = await jx("DELETE", `/api/contacts/${encodeURIComponent(uid)}`); const d = await r.json(); if (!r.ok || d.success === false) throw new Error(d.error || "Delete failed"); return d }, onSuccess: inv }),
    saveConfig: useMutation({ mutationFn: async (v: { carddav_url: string; carddav_username: string; carddav_password?: string }) => { const r = await jx("PUT", "/api/contacts/config", v); if (!r.ok) throw new Error("Couldn't save CardDAV settings"); return r.json() }, onSuccess: () => { qc.invalidateQueries({ queryKey: ["carddav-config"] }); inv() } }),
  }
}
