import { useQuery } from "@tanstack/react-query"
import { apiJson, apiFetch } from "@/lib/api"

export function useAuthStatus() {
  return useQuery({
    queryKey: ["auth-status"],
    queryFn: () => apiJson<{ authenticated?: boolean; username?: string; user?: string }>("/api/auth/status"),
  })
}
export async function logout() {
  try { await apiFetch("/api/auth/logout", { method: "POST" }) } catch { /* ignore */ }
  window.location.assign("/login")
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
