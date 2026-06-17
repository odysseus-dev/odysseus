import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { apiJson, apiFetch } from "@/lib/api"
import type { Note } from "@/types"

export function useNotes() {
  return useQuery({
    queryKey: ["notes"],
    queryFn: async () => (await apiJson<{ notes: Note[] }>("/api/notes")).notes,
  })
}
export function useNoteMutations() {
  const qc = useQueryClient()
  const inv = () => qc.invalidateQueries({ queryKey: ["notes"] })
  return {
    create: useMutation({
      mutationFn: async (v: { title: string; content: string }) => {
        const r = await apiFetch("/api/notes", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: v.title, content: v.content, note_type: "note" }),
        })
        if (!r.ok) throw new Error("create failed"); return r.json()
      },
      onSuccess: inv,
    }),
    remove: useMutation({ mutationFn: async (id: string) => { await apiFetch(`/api/notes/${id}`, { method: "DELETE" }) }, onSuccess: inv }),
    pin: useMutation({ mutationFn: async (id: string) => { await apiFetch(`/api/notes/${id}/pin`, { method: "POST" }) }, onSuccess: inv }),
  }
}
