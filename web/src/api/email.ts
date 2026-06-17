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

export interface EmailBody {
  subject?: string; from?: string; from_addr?: string; sender?: string; date?: string;
  body_html?: string; html?: string; body_text?: string; body?: string; text?: string; error?: string;
}
export function useEmail(uid: string | null) {
  return useQuery({
    queryKey: ["email-read", uid],
    enabled: !!uid,
    queryFn: () => apiJson<EmailBody>(`/api/email/read/${uid}?folder=INBOX`),
  })
}
