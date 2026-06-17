import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { apiJson, apiFetch } from "@/lib/api"
import type { Skill, BuiltinSkill } from "@/types"

export function useSkills() {
  return useQuery({ queryKey: ["skills"], queryFn: async () => (await apiJson<{ skills: Skill[] }>("/api/skills")).skills })
}
export function useBuiltinSkills() {
  return useQuery({ queryKey: ["builtin-skills"], queryFn: async () => (await apiJson<{ builtin: BuiltinSkill[] }>("/api/skills/builtin")).builtin })
}
export function useSkillMarkdown(id: string | null) {
  return useQuery({
    queryKey: ["skill-md", id],
    enabled: !!id,
    queryFn: () => apiJson<{ name: string; markdown: string }>(`/api/skills/${id}/markdown`),
  })
}

export function useSkillMutations() {
  const qc = useQueryClient()
  const inv = () => qc.invalidateQueries({ queryKey: ["skills"] })
  return {
    remove: useMutation({
      mutationFn: async (id: string) => { await apiFetch(`/api/skills/${id}`, { method: "DELETE" }) },
      onSuccess: inv,
    }),
    saveMarkdown: useMutation({
      mutationFn: async (v: { id: string; markdown: string }) => {
        const r = await apiFetch(`/api/skills/${v.id}/markdown`, {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ markdown: v.markdown }),
        })
        if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || "Save failed") }
        return r.json()
      },
      onSuccess: (_d, v) => { qc.invalidateQueries({ queryKey: ["skill-md", v.id] }); inv() },
    }),
    create: useMutation({
      mutationFn: async (v: { name: string; description: string; procedure: string; when_to_use?: string }) => {
        const r = await apiFetch("/api/skills/add", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(v),
        })
        if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || "Create failed") }
        return r.json()
      },
      onSuccess: inv,
    }),
  }
}
