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
  const json = (path: string, body: unknown) => apiFetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
  return {
    favorite: useMutation({ mutationFn: async (id: string) => { await apiFetch(`/api/gallery/${id}/favorite`, { method: "POST" }) }, onSuccess: inv }),
    remove: useMutation({ mutationFn: async (id: string) => { await apiFetch(`/api/gallery/${id}`, { method: "DELETE" }) }, onSuccess: inv }),
    rename: useMutation({ mutationFn: async (v: { id: string; name: string }) => { await json(`/api/gallery/${v.id}/rename`, { name: v.name }) }, onSuccess: inv }),
    rotate: useMutation({ mutationFn: async (v: { id: string; angle: number }) => { await json(`/api/gallery/${v.id}/rotate`, { angle: v.angle }) }, onSuccess: inv }),
    setTags: useMutation({ mutationFn: async (v: { id: string; tags: string }) => { await apiFetch(`/api/gallery/${v.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ tags: v.tags }) }) }, onSuccess: inv }),
    upload: useMutation({
      mutationFn: async (files: FileList | File[]) => {
        for (const f of Array.from(files)) {
          const fd = new FormData(); fd.set("file", f)
          const r = await apiFetch("/api/gallery/upload", { method: "POST", body: fd })
          if (!r.ok) throw new Error("upload failed")
        }
      },
      onSuccess: inv,
    }),
  }
}
