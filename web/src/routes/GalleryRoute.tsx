import { Star, Trash2 } from "lucide-react"
import { useGallery, useGalleryMutations } from "@/api/gallery"
import { cn } from "@/lib/utils"

export function GalleryRoute() {
  const { data: images } = useGallery()
  const { favorite, remove } = useGalleryMutations()
  return (
    <div className="flex h-full flex-col">
      <header className="flex h-13 shrink-0 items-center border-b px-4 text-sm font-semibold">Gallery</header>
      <div className="flex-1 overflow-y-auto p-4">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
          {(images || []).map((img) => (
            <div key={img.id} className="group relative overflow-hidden rounded-lg border bg-card">
              <img src={img.url} alt={img.prompt || img.filename} loading="lazy" className="aspect-square w-full object-cover" />
              <div className="absolute right-1.5 top-1.5 flex gap-1 opacity-0 transition-opacity group-hover:opacity-100">
                <button onClick={() => favorite.mutate(img.id)} title="Favorite" className="rounded-md bg-black/50 p-1.5 text-white hover:bg-black/70">
                  <Star className={cn("size-3.5", img.favorite && "fill-current")} />
                </button>
                <button onClick={() => { if (confirm("Delete this image?")) remove.mutate(img.id) }} title="Delete" className="rounded-md bg-black/50 p-1.5 text-white hover:bg-black/70">
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
        {(images || []).length === 0 && <p className="py-8 text-center text-sm text-muted-foreground">No images yet.</p>}
      </div>
    </div>
  )
}
