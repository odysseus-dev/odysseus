import { useQuery } from "@tanstack/react-query"
import { apiJson } from "@/lib/api"
import type { DocItem } from "@/types"
export function useDocuments() {
  return useQuery({
    queryKey: ["documents"],
    queryFn: async () => {
      const r = await apiJson<{ documents?: DocItem[]; items?: DocItem[] }>("/api/documents/library?limit=50&sort=recent")
      return r.documents || r.items || []
    },
  })
}
