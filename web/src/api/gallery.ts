import { useQuery } from "@tanstack/react-query"
import { apiJson } from "@/lib/api"
import type { GalleryImage } from "@/types"
export function useGallery() {
  return useQuery({
    queryKey: ["gallery"],
    queryFn: async () => (await apiJson<{ items: GalleryImage[] }>("/api/gallery/library?limit=60&sort=recent")).items,
  })
}
