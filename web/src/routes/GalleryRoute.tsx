import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { ChevronLeft, ChevronRight, Download, Film, Folder, FolderOpen, FolderPlus, ImageIcon, Loader2, Pencil, RotateCcw, RotateCw, Save, Search, Settings, Sparkles, Star, Tags, Trash2, Upload, X } from "lucide-react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { downloadImage, flattenGallery, useGallery, useGalleryAlbums, useGalleryMutations } from "@/api/gallery"
import { apiFetch, apiJson } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { toast } from "@/stores/toast"
import type { GalleryAlbum, GalleryImage } from "@/types"

const inp = "h-9 w-full rounded-md border bg-background px-3 text-sm outline-none focus-visible:border-ring"
const videoExts = [".mp4", ".mov", ".webm", ".mkv", ".m4v"]
type GalleryTab = "photos" | "albums" | "editor" | "settings"

function isVideo(img: GalleryImage) {
  const src = `${img.filename || ""} ${img.url || ""}`.toLowerCase().split("?")[0]
  return videoExts.some((ext) => src.endsWith(ext))
}
function splitTags(value?: string) {
  return (value || "").split(",").map((t) => t.trim()).filter(Boolean)
}
function formatCount(n?: number) {
  const v = n || 0
  return `${v} photo${v === 1 ? "" : "s"}`
}
interface EditorDraft { id: string; name?: string; width?: number; height?: number; thumbnail?: string; updated_at?: string }

function editorFrameForImage(img: GalleryImage) {
  const qs = new URLSearchParams({ image: img.id, url: img.url, name: img.prompt || img.filename || "Image" })
  return `/v2/gallery-editor-frame?${qs}`
}

function GalleryEditorWorkspace({ images, frameUrl, onFrame }: { images: GalleryImage[]; frameUrl: string; onFrame: (url: string) => void }) {
  const qc = useQueryClient()
  const [size, setSize] = useState("1024x1024")
  const { data: drafts = [] } = useQuery({ queryKey: ["editor-drafts"], queryFn: async () => (await apiJson<{ drafts?: EditorDraft[] }>("/api/editor-drafts")).drafts || [] })
  const removeDraft = async (id: string) => {
    if (!confirm("Delete this saved editor project?")) return
    const r = await apiFetch(`/api/editor-drafts/${id}`, { method: "DELETE" })
    if (r.ok) qc.invalidateQueries({ queryKey: ["editor-drafts"] })
  }
  if (frameUrl) return <div className="-m-4 flex h-[calc(100%+2rem)] min-h-[560px] flex-col">
    <div className="flex shrink-0 items-center justify-between border-b bg-card px-3 py-2">
      <button onClick={() => onFrame("")} className="text-xs text-muted-foreground hover:text-foreground">← Projects &amp; photos</button>
      <button onClick={() => onFrame(`${frameUrl}${frameUrl.includes("?") ? "&" : "?"}reload=${Date.now()}`)} className="text-xs text-muted-foreground hover:text-foreground">Reload editor</button>
    </div>
    <iframe key={frameUrl} src={frameUrl} title="Gallery image editor" className="min-h-0 flex-1 border-0 bg-background" />
  </div>
  return <div className="space-y-6">
    <section className="rounded-xl border bg-card p-4">
      <h2 className="text-sm font-semibold">New canvas</h2>
      <p className="mt-1 text-xs text-muted-foreground">Start a layered project with brush, crop, transform, filters, masks, history, and AI editing tools.</p>
      <div className="mt-3 flex gap-2"><select value={size} onChange={(e) => setSize(e.target.value)} className={inp}><option value="1024x1024">Square · 1024×1024</option><option value="1920x1080">Landscape · 1920×1080</option><option value="1080x1920">Portrait · 1080×1920</option><option value="1200x630">Social · 1200×630</option></select><Button onClick={() => onFrame(`/v2/gallery-editor-frame?size=${size}`)}>Create</Button></div>
    </section>
    {drafts.length > 0 && <section><div className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Saved projects</div><div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">{drafts.map((draft) => <div key={draft.id} className="group overflow-hidden rounded-lg border bg-card"><button onClick={() => onFrame(`/v2/gallery-editor-frame?draft=${encodeURIComponent(draft.id)}&name=${encodeURIComponent(draft.name || "Saved project")}`)} className="block w-full text-left">{draft.thumbnail ? <img src={draft.thumbnail} alt="" className="aspect-video w-full object-cover" /> : <div className="flex aspect-video items-center justify-center bg-muted"><Pencil className="size-7 text-muted-foreground" /></div>}<span className="block truncate px-3 pt-2 text-sm font-medium">{draft.name || "Untitled"}</span><span className="block px-3 pb-2 text-xs text-muted-foreground">{draft.width || "?"}×{draft.height || "?"}</span></button><button onClick={() => removeDraft(draft.id)} className="mx-3 mb-2 text-xs text-muted-foreground hover:text-destructive">Delete project</button></div>)}</div></section>}
    <section><div className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Edit a photo</div><div className="grid grid-cols-3 gap-2 sm:grid-cols-4 lg:grid-cols-6">{images.filter((img) => !isVideo(img)).map((img) => <button key={img.id} onClick={() => onFrame(editorFrameForImage(img))} className="group overflow-hidden rounded-lg border bg-card"><img src={img.url} alt={img.prompt || img.filename} className="aspect-square w-full object-cover" /><span className="block truncate px-2 py-1.5 text-xs text-muted-foreground group-hover:text-foreground">{img.prompt || img.filename}</span></button>)}</div></section>
  </div>
}

function GallerySettingsPanel({ total, tagged, onTagAll, tagging }: { total: number; tagged: number; onTagAll: () => void; tagging: boolean }) {
  const qc = useQueryClient()
  const [busy, setBusy] = useState("")
  const run = async (label: string, path: string) => {
    setBusy(label)
    try {
      const r = await apiFetch(path, { method: "POST" })
      if (!r.ok) throw new Error()
      qc.invalidateQueries({ queryKey: ["gallery"] })
      toast(`${label} complete`, "success")
    } catch { toast(`${label} failed`) } finally { setBusy("") }
  }
  return <div className="mx-auto max-w-2xl space-y-4">
    <section className="rounded-xl border bg-card p-4"><h2 className="text-sm font-semibold">AI tagging</h2><p className="mt-1 text-xs text-muted-foreground">{tagged}/{total} photos tagged. The configured vision model adds content tags while preserving your own tags.</p><div className="mt-3 h-2 overflow-hidden rounded-full bg-muted"><div className="h-full bg-foreground/70" style={{ width: `${total ? Math.round(tagged / total * 100) : 0}%` }} /></div><div className="mt-4 flex flex-wrap gap-2"><Button size="sm" onClick={onTagAll} disabled={tagging}>{tagging ? "Tagging…" : "Tag untagged photos"}</Button><Button size="sm" variant="outline" onClick={() => run("Clear AI tags", "/api/gallery/clear-ai-tags")} disabled={!!busy}>Clear AI tags</Button><a href="/v2/settings" className="inline-flex h-8 items-center rounded-md border px-3 text-xs font-medium hover:bg-accent">Vision model settings</a></div></section>
    <section className="rounded-xl border bg-card p-4"><h2 className="text-sm font-semibold">Tag maintenance</h2><p className="mt-1 text-xs text-muted-foreground">Normalize duplicate tags or clear only manually assigned tags across the library.</p><div className="mt-3 flex flex-wrap gap-2"><Button size="sm" variant="outline" onClick={() => run("Tag cleanup", "/api/gallery/dedupe-tags")} disabled={!!busy}>Merge duplicate tags</Button><Button size="sm" variant="outline" onClick={() => { if (confirm("Clear all manual gallery tags?")) run("Clear manual tags", "/api/gallery/clear-user-tags") }} disabled={!!busy}>Clear manual tags</Button></div></section>
  </div>
}
function Media({ img, className, controls = false }: { img: GalleryImage; className?: string; controls?: boolean }) {
  if (isVideo(img)) {
    return <video src={img.url} controls={controls} playsInline preload="metadata" className={cn("bg-black object-contain", className)} />
  }
  return <img src={img.url} alt={img.prompt || img.filename} loading="lazy" className={className} />
}

function Lightbox({ img, hasPrev, hasNext, onPrev, onNext, onClose, onEdit, onFilterTag }: {
  img: GalleryImage; hasPrev: boolean; hasNext: boolean; onPrev: () => void; onNext: () => void;
  onClose: () => void; onEdit: (img: GalleryImage) => void; onFilterTag: (tag: string) => void;
}) {
  const { favorite, remove, rename, rotate, setTags, aiTag, clearAiTags } = useGalleryMutations()
  const [name, setName] = useState(img.prompt || "")
  const [tags, setTagsLocal] = useState(img.tags || "")
  // Reset the editable fields when navigating between photos with arrow keys.
  // eslint-disable-next-line react-hooks/set-state-in-effect -- sync editable fields to the focused photo
  useEffect(() => { setName(img.prompt || ""); setTagsLocal(img.tags || "") }, [img.id, img.prompt, img.tags])
  // Keyboard navigation: arrows page through, Escape closes.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return
      if (e.key === "ArrowLeft" && hasPrev) { e.preventDefault(); onPrev() }
      else if (e.key === "ArrowRight" && hasNext) { e.preventDefault(); onNext() }
      else if (e.key === "Escape") { e.preventDefault(); onClose() }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [hasPrev, hasNext, onPrev, onNext, onClose])

  const aiTags = splitTags(img.ai_tags)
  const download = () => { downloadImage(img).catch((e) => toast(e instanceof Error ? e.message : "Couldn't download the image")) }
  return (
    <div className="absolute inset-0 z-20 flex animate-fade-in items-center justify-center bg-black/60 p-4" onClick={onClose}>
      {hasPrev && (
        <button onClick={(e) => { e.stopPropagation(); onPrev() }} title="Previous (←)"
          className="absolute left-3 top-1/2 z-10 -translate-y-1/2 rounded-full bg-black/50 p-2 text-white hover:bg-black/70 md:left-4"><ChevronLeft className="size-5" /></button>
      )}
      {hasNext && (
        <button onClick={(e) => { e.stopPropagation(); onNext() }} title="Next (→)"
          className="absolute right-3 top-1/2 z-10 -translate-y-1/2 rounded-full bg-black/50 p-2 text-white hover:bg-black/70 md:right-4"><ChevronRight className="size-5" /></button>
      )}
      <div className="flex max-h-full w-full max-w-4xl flex-col animate-pop-in overflow-hidden rounded-xl border bg-popover shadow-lg md:flex-row" onClick={(e) => e.stopPropagation()}>
        <div className="flex min-h-0 flex-1 items-center justify-center bg-black/30 p-2">
          <Media img={img} controls className="max-h-[68vh] w-auto max-w-full" />
        </div>
        <div className="flex max-h-[50vh] w-full shrink-0 flex-col gap-3 overflow-y-auto p-4 md:max-h-none md:w-80">
          <div className="flex items-center justify-between">
            <span className="text-sm font-semibold">Edit image</span>
            <button onClick={onClose} className="text-muted-foreground hover:text-foreground"><X className="size-4" /></button>
          </div>
          <div className="space-y-1 text-xs text-muted-foreground">
            {img.model && <p className="truncate">Source: {img.model}</p>}
            {(img.width || img.height) && <p>{img.width || "?"} x {img.height || "?"}</p>}
          </div>
          <div>
            <label className="mb-1 block text-xs text-muted-foreground">Name</label>
            <div className="flex gap-1.5">
              <input value={name} onChange={(e) => setName(e.target.value)} className={inp} />
              <Button size="icon" variant="outline" title="Save name" onClick={() => rename.mutate({ id: img.id, name })}><Save className="size-4" /></Button>
            </div>
          </div>
          <div>
            <label className="mb-1 block text-xs text-muted-foreground">Tags</label>
            <div className="flex gap-1.5">
              <input value={tags} onChange={(e) => setTagsLocal(e.target.value)} placeholder="comma-separated" className={inp} />
              <Button size="icon" variant="outline" title="Save tags" onClick={() => setTags.mutate({ id: img.id, tags })}><Save className="size-4" /></Button>
            </div>
          </div>
          <div>
            <div className="mb-1 flex items-center justify-between">
              <label className="text-xs text-muted-foreground">AI tags</label>
              <div className="flex gap-1">
                <button onClick={() => aiTag.mutate(img.id, { onSuccess: () => toast("Tagged with AI", "success"), onError: (e) => toast(e instanceof Error ? e.message : "Couldn't auto-tag") })}
                  disabled={aiTag.isPending} title="Auto-tag with AI"
                  className="inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] text-muted-foreground hover:text-foreground disabled:opacity-50">
                  {aiTag.isPending ? <Loader2 className="size-3 animate-spin" /> : <Sparkles className="size-3" />}AI tag
                </button>
                {aiTags.length > 0 && (
                  <button onClick={() => clearAiTags.mutate(img.id)} title="Clear AI tags"
                    className="inline-flex items-center rounded-md border px-1.5 py-0.5 text-[11px] text-muted-foreground hover:text-foreground"><X className="size-3" /></button>
                )}
              </div>
            </div>
            {aiTags.length > 0 ? (
              <div className="flex flex-wrap gap-1">
                {aiTags.map((t) => (
                  <button key={t} onClick={() => onFilterTag(t)} title={`Filter by "${t}"`}
                    className="rounded-full border bg-muted px-2 py-0.5 text-[11px] text-muted-foreground hover:bg-accent hover:text-foreground">{t}</button>
                ))}
              </div>
            ) : (
              <p className="text-[11px] text-muted-foreground">No AI tags yet.</p>
            )}
          </div>
          {!isVideo(img) && (
            <div>
              <label className="mb-1 block text-xs text-muted-foreground">Rotate</label>
              <div className="flex gap-2">
                <Button size="sm" variant="outline" onClick={() => rotate.mutate({ id: img.id, angle: -90 })}><RotateCcw className="size-4" />Left</Button>
                <Button size="sm" variant="outline" onClick={() => rotate.mutate({ id: img.id, angle: 90 })}><RotateCw className="size-4" />Right</Button>
              </div>
            </div>
          )}
          <div className="mt-auto flex gap-2">
            <Button size="sm" variant="outline" className="flex-1" onClick={() => favorite.mutate(img.id)}><Star className={cn("size-4", img.favorite && "fill-current")} />{img.favorite ? "Favorited" : "Favorite"}</Button>
            {!isVideo(img) && <Button size="sm" variant="outline" title="Edit" onClick={() => onEdit(img)}><Pencil className="size-4" /></Button>}
            <Button size="sm" variant="outline" title="Download" onClick={download}><Download className="size-4" /></Button>
            <Button size="sm" variant="outline" onClick={() => { if (confirm("Delete this image?")) { remove.mutate(img.id); onClose() } }}><Trash2 className="size-4" /></Button>
          </div>
        </div>
      </div>
    </div>
  )
}

function AlbumCover({ album }: { album: GalleryAlbum }) {
  if (album.cover_url && (album.count || 0) > 0) return <img src={album.cover_url} alt="" loading="lazy" className="aspect-square w-full object-cover" />
  return (
    <div className="flex aspect-square w-full items-center justify-center bg-muted text-muted-foreground">
      <Folder className="size-10" />
    </div>
  )
}

export function GalleryRoute() {
  const [openId, setOpenId] = useState<string | null>(null)
  const [tab, setTab] = useState<GalleryTab>("photos")
  const [q, setQ] = useState("")
  const [albumQ, setAlbumQ] = useState("")
  const [sort, setSort] = useState<"recent" | "oldest" | "shuffle">("recent")
  const [model, setModel] = useState("")
  const [albumId, setAlbumId] = useState<string | null>(null)
  const [favorites, setFavorites] = useState(false)
  const [tagFilter, setTagFilter] = useState("")
  const [coverFor, setCoverFor] = useState<GalleryAlbum | null>(null)
  const [dragging, setDragging] = useState(false)
  const [uploadAlbumId, setUploadAlbumId] = useState<string | null>(null)
  const [batchProgress, setBatchProgress] = useState<{ done: number; total: number } | null>(null)
  const [editorFrameUrl, setEditorFrameUrl] = useState("")
  const fileRef = useRef<HTMLInputElement>(null)
  const sentinelRef = useRef<HTMLDivElement>(null)
  const dragDepth = useRef(0)
  const galleryQuery = useGallery({ search: q, sort, model, album: albumId, favorites, tag: tagFilter })
  const { data, isFetching, fetchNextPage, hasNextPage, isFetchingNextPage } = galleryQuery
  const gallery = useMemo(() => flattenGallery(data), [data])
  const { data: albums = [] } = useGalleryAlbums()
  const { favorite, remove, upload, createAlbum, renameAlbum, deleteAlbum, setAlbumCover, aiTagAll } = useGalleryMutations()
  const images = gallery.items
  const open = openId ? images.find((i) => i.id === openId) || null : null
  const openIndex = openId ? images.findIndex((i) => i.id === openId) : -1
  const activeAlbum = albums.find((a) => a.id === albumId)
  const albumList = albums.filter((a) => !albumQ.trim() || a.name.toLowerCase().includes(albumQ.trim().toLowerCase()))
  const coverAlbum = coverFor ? albums.find((a) => a.id === coverFor.id) || null : null

  // Infinite scroll: load the next page whenever the sentinel scrolls into view.
  useEffect(() => {
    if (tab !== "photos") return
    const node = sentinelRef.current
    if (!node) return
    const obs = new IntersectionObserver((entries) => {
      if (entries[0]?.isIntersecting && hasNextPage && !isFetchingNextPage) fetchNextPage()
    }, { rootMargin: "600px" })
    obs.observe(node)
    return () => obs.disconnect()
  }, [tab, hasNextPage, isFetchingNextPage, fetchNextPage])

  const doUpload = useCallback((files: FileList | File[], targetAlbumId: string | null) => {
    if (!("length" in files) || files.length === 0) return
    upload.mutate({ files, albumId: targetAlbumId }, { onError: () => toast("Couldn't upload one or more files") })
  }, [upload])

  const chooseUpload = (targetAlbumId: string | null = albumId) => {
    setUploadAlbumId(targetAlbumId)
    fileRef.current?.click()
  }
  const addAlbum = () => {
    const name = prompt("Album name")
    if (name?.trim()) createAlbum.mutate(name.trim())
  }
  const renameOneAlbum = (album: GalleryAlbum) => {
    const name = prompt("Rename album:", album.name)
    if (name?.trim() && name.trim() !== album.name) renameAlbum.mutate({ id: album.id, name: name.trim() })
  }
  const deleteOneAlbum = (album: GalleryAlbum) => {
    if (confirm(`Delete album "${album.name}"? Photos stay in your library.`)) {
      deleteAlbum.mutate(album.id)
      if (albumId === album.id) setAlbumId(null)
    }
  }
  const editImage = (img: GalleryImage) => {
    setOpenId(null)
    setEditorFrameUrl(editorFrameForImage(img))
    setTab("editor")
  }
  const filterByTag = (t: string) => { setTagFilter(t); setOpenId(null) }
  const runAiTagAll = () => {
    const scope = albumId ? `album "${activeAlbum?.name || ""}"` : "your library"
    if (!confirm(`Auto-tag all untagged photos in ${scope}? This may take a moment.`)) return
    setBatchProgress({ done: 0, total: 0 })
    aiTagAll.mutate({ albumId, onProgress: (done, total) => setBatchProgress({ done, total }) }, {
      onSuccess: (r) => {
        setBatchProgress(null)
        if (!r.total) toast("Everything is already tagged", "info")
        else toast(`AI-tagged ${r.tagged} photo${r.tagged === 1 ? "" : "s"}${r.failed ? `, ${r.failed} failed` : ""}`, r.failed ? "error" : "success")
      },
      onError: () => { setBatchProgress(null); toast("Batch AI-tagging failed") },
    })
  }

  // Drag-and-drop upload onto the grid. Depth counter avoids flicker as the
  // dragenter/dragleave bubble through child elements.
  const onDragEnter = (e: React.DragEvent) => {
    if (!Array.from(e.dataTransfer.types).includes("Files")) return
    dragDepth.current += 1
    setDragging(true)
  }
  const onDragLeave = () => {
    dragDepth.current = Math.max(0, dragDepth.current - 1)
    if (dragDepth.current === 0) setDragging(false)
  }
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault()
    dragDepth.current = 0
    setDragging(false)
    const files = Array.from(e.dataTransfer.files || []).filter((f) => f.type.startsWith("image/") || f.type.startsWith("video/"))
    if (files.length) doUpload(files, albumId)
  }

  return (
    <div className="relative flex h-full flex-col" data-tour="gallery-root">
      {open && (
        <Lightbox
          key={open.id}
          img={open}
          hasPrev={openIndex > 0}
          hasNext={openIndex >= 0 && openIndex < images.length - 1}
          onPrev={() => { if (openIndex > 0) setOpenId(images[openIndex - 1].id) }}
          onNext={() => { if (openIndex >= 0 && openIndex < images.length - 1) setOpenId(images[openIndex + 1].id) }}
          onClose={() => setOpenId(null)}
          onEdit={editImage}
          onFilterTag={filterByTag}
        />
      )}
      {coverAlbum && (
        <CoverPicker album={coverAlbum} onClose={() => setCoverFor(null)} onPick={(id) => {
          setAlbumCover.mutate({ id: coverAlbum.id, coverId: id }, {
            onSuccess: () => { setCoverFor(null); toast("Album cover set", "success") },
            onError: () => toast("Couldn't set the album cover"),
          })
        }} />
      )}
      <input ref={fileRef} type="file" accept="image/*,video/*" multiple className="hidden" onChange={(e) => {
        if (e.target.files?.length) doUpload(e.target.files, uploadAlbumId ?? albumId)
        setUploadAlbumId(null)
        if (fileRef.current) fileRef.current.value = ""
      }} />
      <header className="flex h-13 shrink-0 items-center justify-between gap-3 border-b px-4">
        <div className="flex min-w-0 items-baseline gap-2">
          <span className="text-sm font-semibold">Gallery</span>
          <span className="text-xs text-muted-foreground">{gallery.total != null ? formatCount(gallery.total) : ""}</span>
        </div>
        <div className="flex min-w-0 items-center gap-2">
          {tab === "photos" && <div className="relative hidden sm:block" data-tour="gallery-search">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search photos, tags..." className="h-8 w-56 rounded-md border bg-background pl-8 pr-2 text-sm outline-none focus-visible:border-ring" />
          </div>}
          <Button size="sm" disabled={upload.isPending} onClick={() => chooseUpload()} data-tour="gallery-upload"><Upload className="size-4" />{upload.isPending ? "Uploading..." : "Upload"}</Button>
        </div>
      </header>
      <div className="shrink-0 border-b px-4 py-2">
        <div className="flex flex-wrap items-center gap-1.5 md:gap-2" data-tour="gallery-tabs">
          {(["photos", "albums", "editor", "settings"] as const).map((v) => (
            <button key={v} onClick={() => setTab(v)} className={cn("rounded-md px-2.5 py-1 text-xs font-medium capitalize", tab === v ? "bg-accent text-foreground" : "text-muted-foreground hover:text-foreground")}>
              {v === "editor" && <Pencil className="mr-1 inline size-3" />}
              {v === "settings" && <Settings className="mr-1 inline size-3" />}
              {v}
            </button>
          ))}
          {tab === "photos" && (
            <>
              <button onClick={() => { setAlbumId(null); setFavorites(false); setModel(""); setQ(""); setTagFilter("") }} className={cn("rounded-full border px-2.5 py-1 text-xs", !albumId && !favorites && !model && !q && !tagFilter ? "bg-accent text-foreground" : "text-muted-foreground hover:text-foreground")}>All</button>
              {!albumId && <button onClick={() => setFavorites((v) => !v)} title="Favorites" className={cn("rounded-full border px-2.5 py-1 text-xs", favorites ? "bg-accent text-foreground" : "text-muted-foreground hover:text-foreground")}><Star className={cn("mr-1 inline size-3", favorites && "fill-current")} />Favorites</button>}
              {activeAlbum && (
                <span className="inline-flex items-center gap-1 rounded-full border bg-accent px-2.5 py-1 text-xs">
                  <FolderOpen className="size-3" />{activeAlbum.name}
                  <button onClick={() => setAlbumId(null)} title="Clear album"><X className="size-3" /></button>
                </span>
              )}
              {tagFilter && (
                <span className="inline-flex items-center gap-1 rounded-full border bg-accent px-2.5 py-1 text-xs">
                  <Tags className="size-3" />{tagFilter}
                  <button onClick={() => setTagFilter("")} title="Clear tag filter"><X className="size-3" /></button>
                </span>
              )}
              <select value={model} onChange={(e) => setModel(e.target.value)} className="hidden h-7 rounded-md border bg-background px-2 text-xs outline-none md:inline-flex">
                <option value="">All sources</option>
                {(gallery.models || []).map((m) => <option key={m} value={m}>{m.split("/").pop()}</option>)}
              </select>
              <select value={sort} onChange={(e) => setSort(e.target.value as "recent" | "oldest" | "shuffle")} className="hidden h-7 rounded-md border bg-background px-2 text-xs outline-none md:inline-flex">
                <option value="recent">Recent</option>
                <option value="oldest">Oldest</option>
                <option value="shuffle">Random</option>
              </select>
              <button onClick={runAiTagAll} disabled={aiTagAll.isPending} title="Auto-tag all untagged photos"
                className="hidden items-center gap-1 rounded-full border px-2.5 py-1 text-xs text-muted-foreground hover:text-foreground disabled:opacity-50 md:inline-flex">
                {aiTagAll.isPending ? <Loader2 className="size-3 animate-spin" /> : <Sparkles className="size-3" />}
                {aiTagAll.isPending && batchProgress ? `Tagging ${batchProgress.done}/${batchProgress.total}` : "AI-tag all"}
              </button>
              {isFetching && !isFetchingNextPage && <span className="text-xs text-muted-foreground">Refreshing...</span>}
            </>
          )}
        </div>
        {tab === "photos" && <div className="relative mt-2 sm:hidden">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search photos, tags..." className="h-8 w-full rounded-md border bg-background pl-8 pr-2 text-sm outline-none focus-visible:border-ring" />
        </div>}
      </div>
      <div className="relative flex-1 overflow-y-auto p-4"
        onDragEnter={tab === "photos" ? onDragEnter : undefined}
        onDragOver={tab === "photos" ? (e) => { if (Array.from(e.dataTransfer.types).includes("Files")) e.preventDefault() } : undefined}
        onDragLeave={tab === "photos" ? onDragLeave : undefined}
        onDrop={tab === "photos" ? onDrop : undefined}>
        {dragging && tab === "photos" && (
          <div className="pointer-events-none absolute inset-2 z-10 flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-ring bg-background/80 text-sm font-medium text-foreground">
            <Upload className="size-8" />Drop images or videos to upload{activeAlbum ? ` to "${activeAlbum.name}"` : ""}
          </div>
        )}
        {tab === "albums" ? (
          <div className="space-y-3" data-tour="gallery-albums">
            <div className="flex items-center gap-2">
              <div className="relative flex-1">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
                <input value={albumQ} onChange={(e) => setAlbumQ(e.target.value)} placeholder="Search albums..." className="h-9 w-full rounded-md border bg-background pl-8 pr-2 text-sm outline-none focus-visible:border-ring" />
              </div>
              <Button size="sm" onClick={addAlbum}><FolderPlus className="size-4" />New</Button>
            </div>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6" data-tour="gallery-grid">
              {albumList.map((album) => (
                <div key={album.id} role="button" tabIndex={0} aria-label={`Open ${album.name}`}
                  className="group relative cursor-pointer overflow-hidden rounded-lg border bg-card focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  onClick={() => { setAlbumId(album.id); setFavorites(false); setTab("photos") }}
                  onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setAlbumId(album.id); setFavorites(false); setTab("photos") } }}>
                  <AlbumCover album={album} />
                  <div className="p-2">
                    <div className="truncate text-sm font-medium">{album.name}</div>
                    <div className="text-xs text-muted-foreground">{formatCount(album.count)}</div>
                  </div>
                  <div className="absolute right-1.5 top-1.5 flex gap-1 opacity-100 transition-opacity md:opacity-0 md:group-hover:opacity-100 md:group-focus-within:opacity-100">
                    <button onClick={(e) => { e.stopPropagation(); chooseUpload(album.id) }} title="Upload here" className="rounded-md bg-black/50 p-1.5 text-white hover:bg-black/70"><Upload className="size-3.5" /></button>
                    <button onClick={(e) => { e.stopPropagation(); setCoverFor(album) }} title="Set cover" className="rounded-md bg-black/50 p-1.5 text-white hover:bg-black/70"><ImageIcon className="size-3.5" /></button>
                    <button onClick={(e) => { e.stopPropagation(); renameOneAlbum(album) }} title="Rename album" className="rounded-md bg-black/50 p-1.5 text-white hover:bg-black/70"><Pencil className="size-3.5" /></button>
                    <button onClick={(e) => { e.stopPropagation(); deleteOneAlbum(album) }} title="Delete album" className="rounded-md bg-black/50 p-1.5 text-white hover:bg-black/70"><Trash2 className="size-3.5" /></button>
                  </div>
                </div>
              ))}
              {albumList.length === 0 && <button onClick={addAlbum} className="flex aspect-square flex-col items-center justify-center gap-2 rounded-lg border border-dashed bg-card text-sm text-muted-foreground hover:bg-accent/50"><FolderPlus className="size-8" />New album</button>}
            </div>
          </div>
        ) : tab === "editor" ? (
          <GalleryEditorWorkspace images={images} frameUrl={editorFrameUrl} onFrame={setEditorFrameUrl} />
        ) : tab === "settings" ? (
          <GallerySettingsPanel total={gallery.total || images.length} tagged={gallery.total_tagged || 0} onTagAll={runAiTagAll} tagging={aiTagAll.isPending} />
        ) : (
          <>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6">
              <button onClick={() => chooseUpload(albumId)} className="flex aspect-square flex-col items-center justify-center gap-2 rounded-lg border border-dashed bg-card text-sm text-muted-foreground hover:bg-accent/50">
                <Upload className="size-8" />Upload
              </button>
              {images.map((img) => (
                <GridImage key={img.id} img={img} onOpen={() => setOpenId(img.id)}
                  onFavorite={() => favorite.mutate(img.id)}
                  onDelete={() => { if (confirm("Delete this image?")) remove.mutate(img.id) }}
                  onTag={(t) => filterByTag(t)} />
              ))}
            </div>
            <div ref={sentinelRef} className="h-px" />
            {isFetchingNextPage && <div className="flex justify-center py-4 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" /></div>}
            {images.length === 0 && !isFetching && <p className="py-8 text-center text-sm text-muted-foreground">{q || albumId || favorites || model || tagFilter ? "No matches." : "No photos yet."}</p>}
          </>
        )}
      </div>
    </div>
  )
}

function GridImage({ img, onOpen, onFavorite, onDelete, onTag }: {
  img: GalleryImage; onOpen: () => void; onFavorite: () => void; onDelete: () => void; onTag: (tag: string) => void;
}) {
  const userTags = splitTags(img.tags)
  const aiTags = splitTags(img.ai_tags)
  const chips = [...userTags, ...aiTags.filter((t) => !userTags.includes(t))].slice(0, 3)
  return (
    <div role="button" tabIndex={0} aria-label={img.prompt || img.filename || "Open image"}
      className="group relative cursor-pointer overflow-hidden rounded-lg border bg-card focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      onClick={onOpen}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen() } }}>
      <Media img={img} className="aspect-square w-full object-cover" />
      {isVideo(img) && <span className="absolute left-1.5 top-1.5 rounded-md bg-black/50 p-1 text-white"><Film className="size-3.5" /></span>}
      <div className="absolute right-1.5 top-1.5 flex gap-1 opacity-100 transition-opacity md:opacity-0 md:group-hover:opacity-100 md:group-focus-within:opacity-100">
        <button onClick={(e) => { e.stopPropagation(); onFavorite() }} title="Favorite" className="rounded-md bg-black/50 p-1.5 text-white hover:bg-black/70">
          <Star className={cn("size-3.5", img.favorite && "fill-current")} />
        </button>
        <button onClick={(e) => { e.stopPropagation(); onDelete() }} title="Delete" className="rounded-md bg-black/50 p-1.5 text-white hover:bg-black/70">
          <Trash2 className="size-3.5" />
        </button>
      </div>
      {(img.prompt || chips.length > 0) && (
        <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/80 to-transparent p-2 text-[11px] text-white opacity-100 transition-opacity md:opacity-0 md:group-hover:opacity-100">
          {img.prompt && <div className="pointer-events-none truncate">{img.prompt}</div>}
          {chips.length > 0 && (
            <div className="mt-0.5 flex flex-wrap gap-1">
              {chips.map((t) => (
                <button key={t} onClick={(e) => { e.stopPropagation(); onTag(t) }} title={`Filter by "${t}"`}
                  className="rounded-full bg-white/20 px-1.5 py-0.5 text-[10px] hover:bg-white/40">{t}</button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// Pick a cover for an album from photos already in that album.
function CoverPicker({ album, onClose, onPick }: { album: GalleryAlbum; onClose: () => void; onPick: (imageId: string) => void }) {
  const { data } = useGallery({ album: album.id, pageSize: 60 })
  const images = useMemo(() => flattenGallery(data).items, [data])
  return (
    <div className="absolute inset-0 z-30 flex animate-fade-in items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div className="flex max-h-[80vh] w-full max-w-2xl flex-col animate-pop-in overflow-hidden rounded-xl border bg-popover shadow-lg" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b px-4 py-3">
          <span className="text-sm font-semibold">Set cover for "{album.name}"</span>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground"><X className="size-4" /></button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {images.length === 0 ? (
            <p className="py-8 text-center text-sm text-muted-foreground">This album has no photos yet.</p>
          ) : (
            <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">
              {images.map((img) => (
                <button key={img.id} onClick={() => onPick(img.id)} title="Use as cover"
                  className={cn("overflow-hidden rounded-md border hover:ring-2 hover:ring-ring", album.cover_url === img.url && "ring-2 ring-ring")}>
                  <img src={img.url} alt="" loading="lazy" className="aspect-square w-full object-cover" />
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
