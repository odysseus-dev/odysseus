import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { apiJson, apiFetch } from "@/lib/api"
import type { ModelsResponse, DefaultChat } from "@/types"

export function useModels() {
  return useQuery({ queryKey: ["models"], queryFn: () => apiJson<ModelsResponse>("/api/models") })
}
export function useDefaultChat() {
  return useQuery({ queryKey: ["default-chat"], queryFn: () => apiJson<DefaultChat>("/api/default-chat") })
}
export function useDeleteEndpoint() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (id: string) => { await apiFetch(`/api/model-endpoints/${id}`, { method: "DELETE" }) },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["models"] }),
  })
}
