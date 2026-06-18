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
    favorite: useMutation({ mutationFn: async (id: string) => { const r = await apiFetch(`/api/gallery/${id}/favorite`, { method: "POST" }); if (!r.ok) throw new Error("Couldn't update the image") }, onSuccess: inv }),
    remove: useMutation({ mutationFn: async (id: string) => { const r = await apiFetch(`/api/gallery/${id}`, { method: "DELETE" }); if (!r.ok) throw new Error("Couldn't delete the image") }, onSuccess: inv }),
    rename: useMutation({ mutationFn: async (v: { id: string; name: string }) => { const r = await json(`/api/gallery/${v.id}/rename`, { name: v.name }); if (!r.ok) throw new Error("Couldn't rename the image") }, onSuccess: inv }),
    rotate: useMutation({ mutationFn: async (v: { id: string; angle: number }) => { const r = await json(`/api/gallery/${v.id}/rotate`, { angle: v.angle }); if (!r.ok) throw new Error("Couldn't rotate the image") }, onSuccess: inv }),
    setTags: useMutation({ mutationFn: async (v: { id: string; tags: string }) => { const r = await apiFetch(`/api/gallery/${v.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ tags: v.tags }) }); if (!r.ok) throw new Error("Couldn't update the image tags") }, onSuccess: inv }),
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
