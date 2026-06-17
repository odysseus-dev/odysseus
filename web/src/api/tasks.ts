import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { apiJson, apiFetch } from "@/lib/api"
import type { Task } from "@/types"

export function useTasks() {
  return useQuery({
    queryKey: ["tasks"],
    queryFn: async () => (await apiJson<{ tasks: Task[] }>("/api/tasks")).tasks,
  })
}
export function useTaskMutations() {
  const qc = useQueryClient()
  const inv = () => qc.invalidateQueries({ queryKey: ["tasks"] })
  const post = (id: string, action: string) => apiFetch(`/api/tasks/${id}/${action}`, { method: "POST" })
  return {
    run: useMutation({ mutationFn: (id: string) => post(id, "run"), onSuccess: inv }),
    pause: useMutation({ mutationFn: (id: string) => post(id, "pause"), onSuccess: inv }),
    resume: useMutation({ mutationFn: (id: string) => post(id, "resume"), onSuccess: inv }),
    remove: useMutation({ mutationFn: async (id: string) => { await apiFetch(`/api/tasks/${id}`, { method: "DELETE" }) }, onSuccess: inv }),
  }
}
