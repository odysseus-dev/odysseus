import { useEffect, useRef, useState } from "react"
import { ArrowUp, Square, Paperclip, X, Mic, Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { uploadFiles } from "@/api/upload"
import { useVoiceCaps, transcribe } from "@/api/voice"
import { cn } from "@/lib/utils"

export function Composer({ onSend, onStop, streaming }: { onSend: (t: string, ids?: string[]) => void; onStop: () => void; streaming: boolean }) {
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
  useEffect(() => { ref.current?.focus() }, [])

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
  const submit = () => {
    if ((!text.trim() && atts.length === 0) || streaming || uploading) return
    onSend(text, atts.map((a) => a.id))
    setText(""); setAtts([]); if (ref.current) ref.current.style.height = "auto"
  }
  const onFiles = async (files: FileList | null) => {
    if (!files || !files.length) return
    setUploading(true)
    try { const up = await uploadFiles(Array.from(files)); setAtts((p) => [...p, ...up.map((f) => ({ id: f.id, name: f.name }))]) }
    catch { /* ignore */ }
    finally { setUploading(false); if (fileRef.current) fileRef.current.value = "" }
  }
  return (
    <div className="mx-auto w-full max-w-[768px] px-4 pb-4">
      <div className="rounded-2xl border bg-card p-2 pl-3 shadow-sm focus-within:border-ring focus-within:ring-[3px] focus-within:ring-ring/35">
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
        <div className="flex items-end gap-2">
          <button onClick={() => fileRef.current?.click()} title="Attach files" className="mb-0.5 rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground">
            <Paperclip className="size-4" />
          </button>
          <input ref={fileRef} type="file" multiple className="hidden" onChange={(e) => onFiles(e.target.files)} />
          {caps?.stt && (
            <button onClick={toggleMic} disabled={transcribing} title={recording ? "Stop recording" : "Dictate"} className={cn("mb-0.5 rounded-md p-1.5 hover:bg-accent hover:text-foreground", recording ? "animate-pulse bg-destructive/15 text-destructive" : "text-muted-foreground")}>
              {transcribing ? <Loader2 className="size-4 animate-spin" /> : <Mic className="size-4" />}
            </button>
          )}
          <textarea
            ref={ref} value={text} rows={1} placeholder={uploading ? "Uploading…" : "Message Odysseus…"}
            onChange={(e) => { setText(e.target.value); grow() }}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit() } }}
            className="max-h-[200px] flex-1 resize-none bg-transparent py-1.5 text-[15px] outline-none placeholder:text-muted-foreground"
          />
          {streaming ? (
            <Button size="icon" variant="secondary" onClick={onStop} title="Stop" className="size-8 rounded-lg"><Square className="size-4" /></Button>
          ) : (
            <Button size="icon" onClick={submit} disabled={(!text.trim() && atts.length === 0) || uploading} title="Send" className="size-8 rounded-lg"><ArrowUp className="size-4" /></Button>
          )}
        </div>
      </div>
      <p className="mt-2 text-center text-[11px] text-muted-foreground">Odysseus can make mistakes. Verify important info.</p>
    </div>
  )
}
