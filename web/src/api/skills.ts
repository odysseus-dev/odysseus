import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { apiJson, apiFetch } from "@/lib/api"
import type { Skill, BuiltinSkill } from "@/types"

export function useSkills() {
  return useQuery({ queryKey: ["skills"], queryFn: async () => (await apiJson<{ skills: Skill[] }>("/api/skills")).skills })
}
export function useBuiltinSkills() {
  return useQuery({ queryKey: ["builtin-skills"], queryFn: async () => (await apiJson<{ builtin: BuiltinSkill[] }>("/api/skills/builtin")).builtin })
}
export function useSkillMutations() {
  const qc = useQueryClient()
  return {
    remove: useMutation({
      mutationFn: async (id: string) => { await apiFetch(`/api/skills/${id}`, { method: "DELETE" }) },
      onSuccess: () => qc.invalidateQueries({ queryKey: ["skills"] }),
    }),
  }
}
