import { useQuery } from "@tanstack/react-query"
import { apiJson } from "@/lib/api"
import type { ModelsResponse, DefaultChat } from "@/types"
export function useModels() {
  return useQuery({ queryKey: ["models"], queryFn: () => apiJson<ModelsResponse>("/api/models") })
}
export function useDefaultChat() {
  return useQuery({ queryKey: ["default-chat"], queryFn: () => apiJson<DefaultChat>("/api/default-chat") })
}
