import { useEffect, useRef, useState } from "react"
import { Star, Trash2, X, RotateCcw, RotateCw, Save, Upload, Search, Download } from "lucide-react"
import { useGallery, useGalleryMutations } from "@/api/gallery"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import type { GalleryImage } from "@/types"

function Lightbox({ img, onClose }: { img: GalleryImage; onClose: () => void }) {
  const { favorite, remove, rename, rotate, setTags } = useGalleryMutations()
  const [name, setName] = useState(img.prompt || "")
  const [tags, setTagsLocal] = useState(img.tags || "")
  // eslint-disable-next-line react-hooks/set-state-in-effect -- sync editable fields when the selected image changes
  useEffect(() => { setName(img.prompt || ""); setTagsLocal(img.tags || "") }, [img])
  const inp = "h-9 w-full rounded-md border bg-background px-3 text-sm outline-none focus-visible:border-ring"
  return (
    <div className="absolute inset-0 z-20 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div className="flex max-h-full w-full max-w-3xl flex-col overflow-hidden rounded-xl border bg-popover shadow-lg sm:flex-row" onClick={(e) => e.stopPropagation()}>
        <div className="flex min-h-0 flex-1 items-center justify-center bg-black/30 p-2">
          <img src={img.url} alt={img.prompt || img.filename} className="max-h-[60vh] w-auto object-contain" />
        </div>
        <div className="flex w-full shrink-0 flex-col gap-3 p-4 sm:w-72">
          <div className="flex items-center justify-between">
            <span className="text-sm font-semibold">Edit image</span>
            <button onClick={onClose} className="text-muted-foreground hover:text-foreground"><X className="size-4" /></button>
          </div>
          <div>
            <label className="mb-1 block text-xs text-muted-foreground">Name</label>
            <div className="flex gap-1.5">
              <input value={name} onChange={(e) => setName(e.target.value)} className={inp} />
              <Button size="icon" variant="outline" title="Save name" onClick={() => rename.mutate({ id: img.id, name })}><Save className="size-4" /></Button>
            </div>
          </div>
          <div>
            <label className="mb-1 block text-xs text-muted-foreground">Tags (comma-separated)</label>
            <div className="flex gap-1.5">
              <input value={tags} onChange={(e) => setTagsLocal(e.target.value)} className={inp} />
              <Button size="icon" variant="outline" title="Save tags" onClick={() => setTags.mutate({ id: img.id, tags })}><Save className="size-4" /></Button>
            </div>
          </div>
          <div>
            <label className="mb-1 block text-xs text-muted-foreground">Rotate</label>
            <div className="flex gap-2">
              <Button size="sm" variant="outline" onClick={() => rotate.mutate({ id: img.id, angle: -90 })}><RotateCcw className="size-4" />Left</Button>
              <Button size="sm" variant="outline" onClick={() => rotate.mutate({ id: img.id, angle: 90 })}><RotateCw className="size-4" />Right</Button>
            </div>
          </div>
          <div className="mt-auto flex gap-2">
            <Button size="sm" variant="outline" className="flex-1" onClick={() => favorite.mutate(img.id)}><Star className={cn("size-4", img.favorite && "fill-current")} />{img.favorite ? "Favorited" : "Favorite"}</Button>
            <Button size="sm" variant="outline" title="Download" onClick={() => window.open(img.url, "_blank")}><Download className="size-4" /></Button>
            <Button size="sm" variant="outline" onClick={() => { if (confirm("Delete this image?")) { remove.mutate(img.id); onClose() } }}><Trash2 className="size-4" /></Button>
          </div>
        </div>
      </div>
    </div>
  )
}

export function GalleryRoute() {
  const { data: images } = useGallery()
  const { favorite, remove, upload } = useGalleryMutations()
  const [open, setOpen] = useState<GalleryImage | null>(null)
  const [q, setQ] = useState("")
  const fileRef = useRef<HTMLInputElement>(null)
  const list = (images || []).filter((i) => !q || `${i.prompt || ""} ${i.filename || ""} ${i.tags || ""}`.toLowerCase().includes(q.toLowerCase()))
  return (
    <div className="relative flex h-full flex-col">
      {open && <Lightbox img={images?.find((i) => i.id === open.id) || open} onClose={() => setOpen(null)} />}
      <header className="flex h-13 shrink-0 items-center justify-between gap-3 border-b px-4">
        <span className="text-sm font-semibold">Gallery</span>
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search…" className="h-8 w-40 rounded-md border bg-background pl-8 pr-2 text-sm outline-none focus-visible:border-ring" />
          </div>
          <input ref={fileRef} type="file" accept="image/*,video/*" multiple className="hidden" onChange={(e) => { if (e.target.files?.length) upload.mutate(e.target.files); if (fileRef.current) fileRef.current.value = "" }} />
          <Button size="sm" disabled={upload.isPending} onClick={() => fileRef.current?.click()}><Upload className="size-4" />{upload.isPending ? "Uploading…" : "Upload"}</Button>
        </div>
      </header>
      <div className="flex-1 overflow-y-auto p-4">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
          {list.map((img) => (
            <div key={img.id} className="group relative cursor-pointer overflow-hidden rounded-lg border bg-card" onClick={() => setOpen(img)}>
              <img src={img.url} alt={img.prompt || img.filename} loading="lazy" className="aspect-square w-full object-cover" />
              <div className="absolute right-1.5 top-1.5 flex gap-1 opacity-0 transition-opacity group-hover:opacity-100">
                <button onClick={(e) => { e.stopPropagation(); favorite.mutate(img.id) }} title="Favorite" className="rounded-md bg-black/50 p-1.5 text-white hover:bg-black/70">
                  <Star className={cn("size-3.5", img.favorite && "fill-current")} />
                </button>
                <button onClick={(e) => { e.stopPropagation(); if (confirm("Delete this image?")) remove.mutate(img.id) }} title="Delete" className="rounded-md bg-black/50 p-1.5 text-white hover:bg-black/70">
                  <Trash2 className="size-3.5" />
                </button>
              </div>
              {img.prompt && (
                <div className="pointer-events-none absolute inset-x-0 bottom-0 truncate bg-gradient-to-t from-black/70 to-transparent p-2 text-[11px] text-white opacity-0 transition-opacity group-hover:opacity-100">
                  {img.prompt}
                </div>
              )}
            </div>
          ))}
        </div>
        {list.length === 0 && <p className="py-8 text-center text-sm text-muted-foreground">{q ? "No matches." : "No images yet."}</p>}
      </div>
    </div>
  )
}
