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
