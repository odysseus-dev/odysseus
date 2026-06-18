import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { apiJson, apiFetch } from "@/lib/api"

export function useAuthStatus() {
  return useQuery({
    queryKey: ["auth-status"],
    queryFn: () => apiJson<{ authenticated?: boolean; username?: string; user?: string; is_admin?: boolean; two_factor_enabled?: boolean; signup_enabled?: boolean }>("/api/auth/status"),
  })
}
export async function logout() {
  try { await apiFetch("/api/auth/logout", { method: "POST" }) } catch { /* ignore */ }
  window.location.assign("/login")
}

export async function changePassword(current_password: string, new_password: string): Promise<{ ok?: boolean; error?: string }> {
  const r = await apiFetch("/api/auth/change-password", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ current_password, new_password }) })
  return r.json().catch(() => ({ ok: r.ok }))
}
export async function setup2FA(): Promise<{ secret?: string; uri?: string; qr_code?: string; error?: string }> {
  const r = await apiFetch("/api/auth/2fa/setup", { method: "POST" })
  return r.json().catch(() => ({ error: `HTTP ${r.status}` }))
}
export async function confirm2FA(code: string): Promise<{ ok?: boolean; error?: string; backup_codes?: string[] }> {
  const r = await apiFetch("/api/auth/2fa/confirm", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code }) })
  return r.json().catch(() => ({ ok: r.ok }))
}
export async function disable2FA(password: string): Promise<{ ok?: boolean; error?: string }> {
  const r = await apiFetch("/api/auth/2fa/disable", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password }) })
  if (r.ok) return r.json().catch(() => ({ ok: true }))
  return { error: (await r.json().catch(() => ({}))).detail || "Invalid password" }
}
export function useTwoFAStatus() {
  return useQuery({ queryKey: ["2fa-status"], retry: false, queryFn: async () => { try { return await apiJson<{ enabled?: boolean }>("/api/auth/2fa/status") } catch { return { enabled: false } } } })
}
export async function setOpenSignup(enabled: boolean): Promise<{ ok?: boolean; signup_enabled?: boolean }> {
  const r = await apiFetch("/api/auth/open-signup", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled }) })
  return r.json().catch(() => ({ ok: r.ok }))
}

export interface AppUser { username: string; is_admin?: boolean }
export function useUsers() {
  return useQuery({
    queryKey: ["users"],
    retry: false,
    queryFn: async (): Promise<AppUser[]> => {
      try {
        const r = await apiJson<{ users?: (AppUser | string)[] }>("/api/auth/users")
        return (r.users || []).map((u) => (typeof u === "string" ? { username: u } : u))
      } catch {
        return [] // 403 for non-admins
      }
    },
  })
}

export function useUserMutations() {
  const qc = useQueryClient()
  const inv = () => qc.invalidateQueries({ queryKey: ["users"] })
  return {
    create: useMutation({
      mutationFn: async (v: { username: string; password: string; is_admin: boolean }) => {
        const r = await apiFetch("/api/auth/users", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(v),
        })
        if (!r.ok) {
          const msg = await r.json().catch(() => ({}))
          throw new Error(msg.detail || (r.status === 409 ? "Username already taken" : "Create failed"))
        }
        return r.json()
      },
      onSuccess: inv,
    }),
    remove: useMutation({
      mutationFn: async (username: string) => {
        const r = await apiFetch("/api/auth/users", {
          method: "DELETE",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ username }),
        })
        if (!r.ok) throw new Error("Delete failed")
        return r.json()
      },
      onSuccess: inv,
    }),
    rename: useMutation({
      mutationFn: async (v: { username: string; new_username: string }) => {
        const r = await apiFetch(`/api/auth/users/${encodeURIComponent(v.username)}/rename`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ new_username: v.new_username }),
        })
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || "Rename failed")
        return r.json()
      },
      onSuccess: inv,
    }),
  }
}
