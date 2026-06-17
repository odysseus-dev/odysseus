import { useQuery } from "@tanstack/react-query"
import { apiJson } from "@/lib/api"
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
