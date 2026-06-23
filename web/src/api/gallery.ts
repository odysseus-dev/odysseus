import { useState } from "react"
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { apiJson, apiFetch } from "@/lib/api"
import type { GalleryAlbum, GalleryImage } from "@/types"

// Backend caps a single page at 100 (gallery_routes.py:479). We page through
// with offset/limit and append, so the old silent "limit:100" truncation is gone.
export const GALLERY_PAGE_SIZE = 60

export interface GalleryQuery {
  search?: string; sort?: "recent" | "oldest" | "shuffle"; model?: string; album?: string | null; favorites?: boolean; tag?: string; pageSize?: number;
}
export interface GalleryLibrary {
  items: GalleryImage[]; total?: number; total_tagged?: number; tags?: string[]; models?: string[];
}

interface GalleryPage extends GalleryLibrary { offset: number }

export function useGallery(q: GalleryQuery = {}) {
  const search = q.search?.trim() || ""
  const sort = q.sort || "recent"
  const model = q.model || ""
  const album = q.album || ""
  const favorites = !!q.favorites
  const tag = q.tag?.trim() || ""
  const pageSize = q.pageSize || GALLERY_PAGE_SIZE
  // Stable seed per hook instance so "shuffle" stays consistent across pages
  // (lazy useState init keeps the random call out of the render path).
  const [seedValue] = useState(() => Math.floor(Math.random() * 1e9))
  const seed = sort === "shuffle" ? seedValue : undefined
  return useInfiniteQuery({
    queryKey: ["gallery", { search, sort, model, album, favorites, tag, pageSize }],
    initialPageParam: 0,
    queryFn: async ({ pageParam }) => {
      const offset = pageParam as number
      const qs = new URLSearchParams({ limit: String(pageSize), offset: String(offset), sort })
      if (search) qs.set("search", search)
      if (model) qs.set("model", model)
      if (album) qs.set("album", album)
      if (favorites) qs.set("favorites", "true")
      if (tag) qs.set("tag", tag)
      if (seed != null) qs.set("seed", String(seed))
      const data = await apiJson<GalleryLibrary>(`/api/gallery/library?${qs}`)
      return { ...data, offset } as GalleryPage
    },
    getNextPageParam: (last, pages) => {
      const loaded = pages.reduce((n, p) => n + (p.items?.length || 0), 0)
      if (last.total != null && loaded >= last.total) return undefined
      if ((last.items?.length || 0) < pageSize) return undefined
      return loaded
    },
  })
}

// Flatten infinite-query pages into the shape the route already expects.
export function flattenGallery(data?: { pages: GalleryPage[] }): GalleryLibrary & { items: GalleryImage[] } {
  const pages = data?.pages || []
  const first = pages[0]
  // De-dupe by id in case a shuffle seed or a concurrent edit overlaps pages.
  const seen = new Set<string>()
  const items: GalleryImage[] = []
  for (const p of pages) for (const img of p.items || []) {
    if (seen.has(img.id)) continue
    seen.add(img.id)
    items.push(img)
  }
  return {
    items,
    total: first?.total,
    total_tagged: first?.total_tagged,
    tags: first?.tags,
    models: first?.models,
  }
}

export function useGalleryAlbums() {
  return useQuery({
    queryKey: ["gallery-albums"],
    queryFn: async () => (await apiJson<{ albums: GalleryAlbum[] }>("/api/gallery/albums")).albums || [],
  })
}

// Fetch the raw bytes and trigger a real download with a sensible filename,
// rather than opening the image in a new tab.
export async function downloadImage(img: GalleryImage) {
  const res = await apiFetch(img.url)
  if (!res.ok) throw new Error("Couldn't download the image")
  const blob = await res.blob()
  const objectUrl = URL.createObjectURL(blob)
  try {
    const a = document.createElement("a")
    a.href = objectUrl
    a.download = filenameFor(img, blob.type)
    document.body.appendChild(a)
    a.click()
    a.remove()
  } finally {
    // Revoke on the next tick so the click has a chance to start the download.
    setTimeout(() => URL.revokeObjectURL(objectUrl), 1000)
  }
}

function filenameFor(img: GalleryImage, mime: string): string {
  // Prefer the stored filename; otherwise derive one from the prompt/id.
  const fromFile = (img.filename || "").split("/").pop()?.split("?")[0]
  if (fromFile && /\.[a-z0-9]{2,5}$/i.test(fromFile)) return fromFile
  const base = (img.prompt || img.filename || img.id || "image")
    .toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60) || "image"
  const extFromMime = mime.split("/")[1]?.split(";")[0]
  const extFromUrl = (img.url || "").split("?")[0].split(".").pop()
  const ext = (extFromUrl && extFromUrl.length <= 5 ? extFromUrl : extFromMime) || "jpg"
  return `${base}.${ext}`
}

interface AiTagResult { ok?: boolean; ai_tags?: string; error?: string }
interface AiTagBatchResult { ok?: boolean; queued?: number; total_untagged?: number; image_ids?: string[] }

export function useGalleryMutations() {
  const qc = useQueryClient()
  const inv = () => {
    qc.invalidateQueries({ queryKey: ["gallery"] })
    qc.invalidateQueries({ queryKey: ["gallery-albums"] })
  }
  const json = (path: string, body: unknown) => apiFetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
  return {
    favorite: useMutation({ mutationFn: async (id: string) => { const r = await apiFetch(`/api/gallery/${id}/favorite`, { method: "POST" }); if (!r.ok) throw new Error("Couldn't update the image") }, onSuccess: inv }),
    remove: useMutation({ mutationFn: async (id: string) => { const r = await apiFetch(`/api/gallery/${id}`, { method: "DELETE" }); if (!r.ok) throw new Error("Couldn't delete the image") }, onSuccess: inv }),
    rename: useMutation({ mutationFn: async (v: { id: string; name: string }) => { const r = await json(`/api/gallery/${v.id}/rename`, { name: v.name }); if (!r.ok) throw new Error("Couldn't rename the image") }, onSuccess: inv }),
    rotate: useMutation({ mutationFn: async (v: { id: string; angle: number }) => { const r = await json(`/api/gallery/${v.id}/rotate`, { angle: v.angle }); if (!r.ok) throw new Error("Couldn't rotate the image") }, onSuccess: inv }),
    setTags: useMutation({ mutationFn: async (v: { id: string; tags: string }) => { const r = await apiFetch(`/api/gallery/${v.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ tags: v.tags }) }); if (!r.ok) throw new Error("Couldn't update the image tags") }, onSuccess: inv }),
    createAlbum: useMutation({ mutationFn: async (name: string) => { const r = await apiFetch("/api/gallery/albums", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) }); if (!r.ok) throw new Error("Couldn't create the album") }, onSuccess: inv }),
    renameAlbum: useMutation({ mutationFn: async (v: { id: string; name: string }) => { const r = await apiFetch(`/api/gallery/albums/${v.id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: v.name }) }); if (!r.ok) throw new Error("Couldn't rename the album") }, onSuccess: inv }),
    deleteAlbum: useMutation({ mutationFn: async (id: string) => { const r = await apiFetch(`/api/gallery/albums/${id}`, { method: "DELETE" }); if (!r.ok) throw new Error("Couldn't delete the album") }, onSuccess: inv }),
    setAlbumCover: useMutation({ mutationFn: async (v: { id: string; coverId: string }) => { const r = await apiFetch(`/api/gallery/albums/${v.id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cover_id: v.coverId }) }); if (!r.ok) throw new Error("Couldn't set the album cover") }, onSuccess: inv }),
    aiTag: useMutation({
      mutationFn: async (id: string) => {
        const r = await apiFetch(`/api/gallery/${id}/ai-tag`, { method: "POST" })
        if (!r.ok) throw new Error("Couldn't auto-tag the image")
        const data = (await r.json()) as AiTagResult
        if (data.error) throw new Error(data.error)
        return data.ai_tags || ""
      },
      onSuccess: inv,
    }),
    clearAiTags: useMutation({
      mutationFn: async (id?: string) => {
        const qs = id ? `?image_id=${encodeURIComponent(id)}` : ""
        const r = await apiFetch(`/api/gallery/clear-ai-tags${qs}`, { method: "POST" })
        if (!r.ok) throw new Error("Couldn't clear AI tags")
      },
      onSuccess: inv,
    }),
    aiTagAll: useMutation({
      // The batch endpoint only returns the untagged IDs; the actual tagging is
      // a per-image call. We fan those out here (bounded concurrency) and report
      // progress so the UI can show "tagged N of M".
      mutationFn: async (opts: { albumId?: string | null; onProgress?: (done: number, total: number) => void } = {}) => {
        const qs = new URLSearchParams({ limit: "200" })
        if (opts.albumId) qs.set("album_id", opts.albumId)
        const batch = await apiJson<AiTagBatchResult>(`/api/gallery/ai-tag-batch?${qs}`, { method: "POST" })
        const ids = batch.image_ids || []
        const total = ids.length
        if (!total) return { tagged: 0, failed: 0, total: 0 }
        let done = 0, tagged = 0, failed = 0
        const queue = [...ids]
        const worker = async () => {
          for (;;) {
            const id = queue.shift()
            if (!id) return
            try {
              const r = await apiFetch(`/api/gallery/${id}/ai-tag`, { method: "POST" })
              const data = r.ok ? ((await r.json()) as AiTagResult) : { error: "request failed" }
              if (data.error) failed++; else tagged++
            } catch { failed++ }
            done++
            opts.onProgress?.(done, total)
          }
        }
        await Promise.all(Array.from({ length: Math.min(3, total) }, worker))
        return { tagged, failed, total }
      },
      onSuccess: inv,
    }),
    upload: useMutation({
      mutationFn: async (value: FileList | File[] | { files: FileList | File[]; albumId?: string | null }) => {
        const files = "files" in value ? value.files : value
        const albumId = "files" in value ? value.albumId : undefined
        for (const f of Array.from(files)) {
          const fd = new FormData(); fd.set("file", f)
          if (albumId) fd.set("album_id", albumId)
          const r = await apiFetch("/api/gallery/upload", { method: "POST", body: fd })
          if (!r.ok) throw new Error("upload failed")
        }
      },
      onSuccess: inv,
    }),
  }
}
