import { useEffect, useRef, useState } from "react"
import { ArrowUp, Square, Paperclip, X, Mic, Loader2, Slash } from "lucide-react"
import { Button } from "@/components/ui/button"
import { uploadFiles } from "@/api/upload"
import { useVoiceCaps, transcribe } from "@/api/voice"
import { useSlashCatalog, invokeSkill } from "@/api/skills"
import { ModePicker, ModelPicker, ToolsMenu } from "./ComposerControls"
import { cn } from "@/lib/utils"

export function Composer({ onSend, onStop, streaming }: { onSend: (t: string, ids?: string[], sendAs?: string) => void; onStop: () => void; streaming: boolean }) {
  const [text, setText] = useState("")
  const [atts, setAtts] = useState<{ id: string; name: string }[]>([])
  const [uploading, setUploading] = useState(false)
  const ref = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const { data: caps } = useVoiceCaps()
  const [recording, setRecording] = useState(false)
  const [transcribing, setTranscribing] = useState(false)
  const recRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const { data: slashAll } = useSlashCatalog()
  const [slashSel, setSlashSel] = useState(0)
  // Slash autocomplete only while typing the leading command token (no space yet).
  const slashTok = /^\/([\w-]*)$/.exec(text)
  const slashMatches = slashTok && !streaming
    ? (slashAll || []).filter((c) => c.name.toLowerCase().startsWith(slashTok[1].toLowerCase())).slice(0, 8)
    : []
  const slashOpen = slashMatches.length > 0
  const sel = Math.min(slashSel, slashMatches.length - 1)
  const pickSlash = (name: string) => { setText(`/${name} `); setSlashSel(0); requestAnimationFrame(() => { ref.current?.focus(); grow() }) }
  useEffect(() => { ref.current?.focus() }, [])
  useEffect(() => {
    const focus = () => ref.current?.focus()
    const setText_ = (e: Event) => { const t = (e as CustomEvent).detail; if (typeof t === "string") { setText(t); requestAnimationFrame(() => { ref.current?.focus(); grow() }) } }
    window.addEventListener("odysseus:focus-composer", focus)
    window.addEventListener("odysseus:set-composer", setText_)
    return () => { window.removeEventListener("odysseus:focus-composer", focus); window.removeEventListener("odysseus:set-composer", setText_) }
  }, [])

  const toggleMic = async () => {
    if (recording) { recRef.current?.stop(); return }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const rec = new MediaRecorder(stream)
      chunksRef.current = []
      rec.ondataavailable = (e) => { if (e.data.size) chunksRef.current.push(e.data) }
      rec.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop())
        setRecording(false)
        const blob = new Blob(chunksRef.current, { type: "audio/webm" })
        if (!blob.size) return
        setTranscribing(true)
        try {
          const t = await transcribe(blob)
          if (t) { setText((p) => (p ? p + " " : "") + t); requestAnimationFrame(grow) }
        } catch { /* ignore */ } finally { setTranscribing(false); ref.current?.focus() }
      }
      recRef.current = rec
      rec.start()
      setRecording(true)
    } catch { /* mic denied/unavailable */ }
  }
  const grow = () => { const el = ref.current; if (!el) return; el.style.height = "auto"; el.style.height = Math.min(el.scrollHeight, 200) + "px" }
  const submit = async () => {
    if ((!text.trim() && atts.length === 0) || streaming || uploading) return
    const ids = atts.map((a) => a.id)
    const display = text
    setText(""); setAtts([]); setSlashSel(0); if (ref.current) ref.current.style.height = "auto"
    // /skill <request> → expand to the skill-pinned prompt (display the command, send the expansion)
    const cmd = /^\/([\w-]+)\s*([\s\S]*)$/.exec(display.trim())
    if (cmd && (slashAll || []).some((c) => c.name === cmd[1])) {
      const expanded = await invokeSkill(cmd[1], cmd[2] || "")
      onSend(display, ids, expanded || undefined)
      return
    }
    onSend(display, ids)
  }
  const onFiles = async (files: FileList | null) => {
    if (!files || !files.length) return
    setUploading(true)
    try { const up = await uploadFiles(Array.from(files)); setAtts((p) => [...p, ...up.map((f) => ({ id: f.id, name: f.name }))]) }
    catch { /* ignore */ }
    finally { setUploading(false); if (fileRef.current) fileRef.current.value = "" }
  }
  const [dragging, setDragging] = useState(false)
  return (
    <div className="mx-auto w-full max-w-[768px] px-4 pb-4">
      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
        onDragLeave={(e) => { e.preventDefault(); setDragging(false) }}
        onDrop={(e) => { e.preventDefault(); setDragging(false); if (e.dataTransfer.files?.length) onFiles(e.dataTransfer.files) }}
        className={cn("relative rounded-2xl border bg-card p-2 pl-3 shadow-sm focus-within:border-ring focus-within:ring-[3px] focus-within:ring-ring/35", dragging && "border-ring ring-[3px] ring-ring/35")}>
        {dragging && <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center rounded-2xl bg-background/80 text-sm font-medium text-muted-foreground">Drop files to attach</div>}
        {slashOpen && (
          <div className="absolute bottom-full left-0 right-0 mb-2 overflow-hidden rounded-xl border bg-popover shadow-lg">
            <div className="border-b px-3 py-1.5 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">Skills</div>
            <div className="max-h-64 overflow-y-auto py-1">
              {slashMatches.map((c, i) => (
                <button
                  key={c.token}
                  onMouseDown={(e) => { e.preventDefault(); pickSlash(c.name) }}
                  onMouseEnter={() => setSlashSel(i)}
                  className={cn("flex w-full items-start gap-2 px-3 py-1.5 text-left", i === sel ? "bg-accent" : "hover:bg-accent/50")}
                >
                  <Slash className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
                  <span className="min-w-0">
                    <span className="text-sm font-medium">{c.token}</span>
                    {c.help && <span className="ml-2 text-xs text-muted-foreground">{c.help}</span>}
                  </span>
                </button>
              ))}
            </div>
          </div>
        )}
        {atts.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-1.5">
            {atts.map((a) => (
              <span key={a.id} className="flex items-center gap-1 rounded-md bg-muted px-2 py-1 text-xs">
                {a.name}
                <button onClick={() => setAtts((p) => p.filter((x) => x.id !== a.id))} className="text-muted-foreground hover:text-foreground"><X className="size-3" /></button>
              </span>
            ))}
          </div>
        )}
        <textarea
          ref={ref} value={text} rows={1} placeholder={uploading ? "Uploading…" : "Message Odysseus…  (/ for skills)"}
          onChange={(e) => { setText(e.target.value); setSlashSel(0); grow() }}
          onKeyDown={(e) => {
            if (slashOpen) {
              if (e.key === "ArrowDown") { e.preventDefault(); setSlashSel((s) => Math.min(s + 1, slashMatches.length - 1)); return }
              if (e.key === "ArrowUp") { e.preventDefault(); setSlashSel((s) => Math.max(s - 1, 0)); return }
              if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); pickSlash(slashMatches[sel].name); return }
              if (e.key === "Escape") { e.preventDefault(); setText(""); return }
            }
            if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit() }
          }}
          className="max-h-[200px] w-full resize-none bg-transparent px-1 py-1.5 text-[15px] outline-none placeholder:text-muted-foreground"
        />
        <div className="mt-1 flex items-center gap-1">
          <button onClick={() => fileRef.current?.click()} title="Attach files" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground">
            <Paperclip className="size-4" />
          </button>
          <input ref={fileRef} type="file" multiple className="hidden" onChange={(e) => onFiles(e.target.files)} />
          {caps?.stt && (
            <button onClick={toggleMic} disabled={transcribing} title={recording ? "Stop recording" : "Dictate"} className={cn("rounded-md p-1.5 hover:bg-accent hover:text-foreground", recording ? "animate-pulse bg-destructive/15 text-destructive" : "text-muted-foreground")}>
              {transcribing ? <Loader2 className="size-4 animate-spin" /> : <Mic className="size-4" />}
            </button>
          )}
          <ToolsMenu />
          <div className="ml-auto flex items-center gap-2">
            <ModePicker />
            <ModelPicker />
            {streaming ? (
              <Button size="icon" variant="secondary" onClick={onStop} title="Stop" className="size-8 rounded-lg"><Square className="size-4" /></Button>
            ) : (
              <Button size="icon" onClick={submit} disabled={(!text.trim() && atts.length === 0) || uploading} title="Send" className="size-8 rounded-lg"><ArrowUp className="size-4" /></Button>
            )}
          </div>
        </div>
      </div>
      <p className="mt-2 text-center text-[11px] text-muted-foreground">Odysseus can make mistakes. Verify important info.</p>
    </div>
  )
}
