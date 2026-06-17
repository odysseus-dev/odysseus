import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { apiJson, apiFetch } from "@/lib/api"
import type { GalleryImage } from "@/types"

export function useGallery() {
  return useQuery({
    queryKey: ["gallery"],
    queryFn: async () => (await apiJson<{ items: GalleryImage[] }>("/api/gallery/library?limit=60&sort=recent")).items,
  })
}
export function useGalleryMutations() {
  const qc = useQueryClient()
  const inv = () => qc.invalidateQueries({ queryKey: ["gallery"] })
  return {
    favorite: useMutation({ mutationFn: async (id: string) => { await apiFetch(`/api/gallery/${id}/favorite`, { method: "POST" }) }, onSuccess: inv }),
    remove: useMutation({ mutationFn: async (id: string) => { await apiFetch(`/api/gallery/${id}`, { method: "DELETE" }) }, onSuccess: inv }),
  }
}
