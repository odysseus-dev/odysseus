import { useRef, useState } from "react"
import { ArrowUp, Square } from "lucide-react"
import { Button } from "@/components/ui/button"

export function Composer({ onSend, onStop, streaming }: { onSend: (t: string) => void; onStop: () => void; streaming: boolean }) {
  const [text, setText] = useState("")
  const ref = useRef<HTMLTextAreaElement>(null)
  const grow = () => { const el = ref.current; if (!el) return; el.style.height = "auto"; el.style.height = Math.min(el.scrollHeight, 200) + "px" }
  const submit = () => { if (!text.trim() || streaming) return; onSend(text); setText(""); if (ref.current) ref.current.style.height = "auto" }
  return (
    <div className="mx-auto w-full max-w-[768px] px-4 pb-4">
      <div className="flex items-end gap-2 rounded-2xl border bg-card p-2 pl-3 shadow-sm focus-within:border-ring focus-within:ring-[3px] focus-within:ring-ring/35">
        <textarea
          ref={ref} value={text} rows={1} placeholder="Message Odysseus…"
          onChange={(e) => { setText(e.target.value); grow() }}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit() } }}
          className="max-h-[200px] flex-1 resize-none bg-transparent py-1.5 text-[15px] outline-none placeholder:text-muted-foreground"
        />
        {streaming ? (
          <Button size="icon" variant="secondary" onClick={onStop} title="Stop" className="size-8 rounded-lg"><Square className="size-4" /></Button>
        ) : (
          <Button size="icon" onClick={submit} disabled={!text.trim()} title="Send" className="size-8 rounded-lg"><ArrowUp className="size-4" /></Button>
        )}
      </div>
      <p className="mt-2 text-center text-[11px] text-muted-foreground">Odysseus can make mistakes. Verify important info.</p>
    </div>
  )
}
