import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { apiJson, apiFetch } from "@/lib/api"

export function useAuthStatus() {
  return useQuery({
    queryKey: ["auth-status"],
    queryFn: () => apiJson<{ authenticated?: boolean; username?: string; user?: string; is_admin?: boolean; two_factor_enabled?: boolean }>("/api/auth/status"),
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
  }
}
