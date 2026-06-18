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
    create: useMutation({ mutationFn: async (v: EmailAccountInput) => { const r = await jx("POST", "/api/email/accounts", v); const d = await r.json().catch(() => ({})); if (!r.ok || d.ok === false) throw new Error(d.error || "Create failed"); return d }, onSuccess: inv }),
    update: useMutation({ mutationFn: async (v: { id: string } & EmailAccountInput) => { const { id, ...rest } = v; const r = await jx("PUT", `/api/email/accounts/${id}`, rest); const d = await r.json().catch(() => ({})); if (!r.ok || d.ok === false) throw new Error(d.error || "Update failed"); return d }, onSuccess: inv }),
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
    create: useMutation({ mutationFn: async (v: { label?: string; url: string; username?: string; password: string }) => { const r = await jx("POST", "/api/calendar/config/accounts", v); const d = await r.json().catch(() => ({})); if (!r.ok) throw new Error(d.detail || "Add failed"); return d }, onSuccess: inv }),
    update: useMutation({ mutationFn: async (v: { id: string; label?: string; url?: string; username?: string; password?: string }) => { const { id, ...rest } = v; const r = await jx("PUT", `/api/calendar/config/accounts/${id}`, rest); if (!r.ok) throw new Error("Update failed"); return r.json() }, onSuccess: inv }),
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
  return useMutation({ mutationFn: async (v: { serverId: string; disabled: string[] }) => { const r = await jx("PATCH", `/api/mcp/servers/${v.serverId}/tools`, { disabled: v.disabled }); if (!r.ok) throw new Error("Failed"); return r.json() }, onSuccess: (_d, v) => qc.invalidateQueries({ queryKey: ["mcp-tools", v.serverId] }) })
}

// ───────────────────────── Contacts ─────────────────────────
export function useContactsCount() {
  return useQuery({ queryKey: ["contacts-count"], retry: false, queryFn: async () => { try { const r = await apiJson<{ contacts?: unknown[]; items?: unknown[] }>("/api/contacts/list"); return (r.contacts || r.items || []).length } catch { return 0 } } })
}
export async function clearContacts() { const r = await jx("DELETE", "/api/contacts/clear"); return r.json().catch(() => ({})) }
