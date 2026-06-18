import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { apiJson, apiFetch } from "@/lib/api"
import type { Memory } from "@/types"

export function useMemory() {
  return useQuery({
    queryKey: ["memory"],
    queryFn: async () => (await apiJson<{ memory: Memory[] }>("/api/memory")).memory,
  })
}
export function useMemoryMutations() {
  const qc = useQueryClient()
  const inv = () => qc.invalidateQueries({ queryKey: ["memory"] })
  return {
    add: useMutation({
      mutationFn: async (v: { text: string; category: string }) => {
        const fd = new FormData(); fd.set("text", v.text); fd.set("category", v.category); fd.set("source", "user")
        const r = await apiFetch("/api/memory/add", { method: "POST", body: fd })
        if (!r.ok) throw new Error("add failed"); return r.json()
      },
      onSuccess: inv,
    }),
    update: useMutation({
      mutationFn: async (v: { id: string; text: string; category?: string }) => {
        const fd = new FormData(); fd.set("text", v.text); if (v.category) fd.set("category", v.category)
        const r = await apiFetch(`/api/memory/${v.id}`, { method: "PUT", body: fd })
        if (!r.ok) throw new Error("update failed"); return r.json()
      },
      onSuccess: inv,
    }),
    remove: useMutation({ mutationFn: async (id: string) => { const r = await apiFetch(`/api/memory/${id}`, { method: "DELETE" }); if (!r.ok) throw new Error("Couldn't delete the memory") }, onSuccess: inv }),
  }
}
