import { useState } from "react"
import { ArrowLeft, PenSquare, Send, X, Reply, Archive, MailOpen, Trash2 } from "lucide-react"
import { useInbox, useEmail, useEmailActions, sendEmail, saveDraft } from "@/api/email"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

interface Prefill { to?: string; subject?: string; body?: string }

function Reader({ uid, onBack, onReply }: { uid: string; onBack: () => void; onReply: (p: Prefill) => void }) {
  const { data, isLoading } = useEmail(uid)
  const { markUnread, archive, remove } = useEmailActions()
  const html = data?.body_html || data?.html
  const text = data?.body_text || data?.body || data?.text
  const from = data?.from_name || data?.from_address || data?.from || data?.from_addr || data?.sender || ""
  const addr = data?.from_address || data?.from_addr || from
  const after = (fn: () => void) => { fn(); onBack() }
  return (
    <div className="flex h-full flex-col">
      <header className="flex h-13 shrink-0 items-center gap-2 border-b px-3">
        <Button variant="ghost" size="icon" onClick={onBack} title="Back"><ArrowLeft className="size-4" /></Button>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold">{data?.subject || "(no subject)"}</div>
          <div className="truncate text-xs text-muted-foreground">{from}{data?.date ? ` · ${new Date(data.date).toLocaleString()}` : ""}</div>
        </div>
        <div className="flex shrink-0 items-center gap-0.5">
          <button onClick={() => onReply({ to: addr, subject: /^re:/i.test(data?.subject || "") ? data?.subject : `Re: ${data?.subject || ""}`, body: `\n\n---\n${text || ""}` })} title="Reply" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"><Reply className="size-4" /></button>
          <button onClick={() => after(() => markUnread.mutate(uid))} title="Mark unread" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"><MailOpen className="size-4" /></button>
          <button onClick={() => after(() => archive.mutate(uid))} title="Archive" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"><Archive className="size-4" /></button>
          <button onClick={() => { if (confirm("Delete this email?")) after(() => remove.mutate(uid)) }} title="Delete" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-destructive"><Trash2 className="size-4" /></button>
        </div>
      </header>
      {isLoading ? <div className="p-6 text-sm text-muted-foreground">Loading…</div>
        : data?.error ? <div className="p-6 text-sm text-muted-foreground">Couldn't load this message.</div>
        : html ? <iframe title="email" sandbox="" srcDoc={html} className="min-h-0 w-full flex-1 bg-white" />
        : <pre className="flex-1 overflow-auto whitespace-pre-wrap p-6 text-sm">{text || "(empty)"}</pre>}
    </div>
  )
}

function Compose({ onClose, initial }: { onClose: () => void; initial?: Prefill }) {
  const [to, setTo] = useState(initial?.to || "")
  const [subject, setSubject] = useState(initial?.subject || "")
  const [body, setBody] = useState(initial?.body || "")
  const [busy, setBusy] = useState("")
  const [err, setErr] = useState("")
  const act = async (kind: "send" | "draft") => {
    if (!to.trim()) { setErr("Recipient required"); return }
    setBusy(kind); setErr("")
    try {
      const r = kind === "send" ? await sendEmail({ to, subject, body }) : await saveDraft({ to, subject, body })
      if (r && r.success === false) setErr(r.error || "Failed"); else onClose()
    } catch { setErr("Failed") } finally { setBusy("") }
  }
  const inp = "h-9 w-full rounded-md border bg-background px-3 text-sm outline-none focus-visible:border-ring"
  return (
    <div className="absolute inset-0 z-10 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-lg rounded-xl border bg-popover p-4 shadow-lg">
        <div className="mb-3 flex items-center justify-between"><div className="text-sm font-semibold">New message</div><button onClick={onClose} className="text-muted-foreground hover:text-foreground"><X className="size-4" /></button></div>
        <input value={to} onChange={(e) => setTo(e.target.value)} placeholder="To" className={cn(inp, "mb-2")} />
        <input value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="Subject" className={cn(inp, "mb-2")} />
        <textarea value={body} onChange={(e) => setBody(e.target.value)} placeholder="Write a message…" rows={8} className="mb-3 w-full resize-none rounded-md border bg-background px-3 py-2 text-sm outline-none focus-visible:border-ring" />
        {err && <p className="mb-2 text-xs text-destructive">{err}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="outline" size="sm" disabled={!!busy} onClick={() => act("draft")}>{busy === "draft" ? "Saving…" : "Save draft"}</Button>
          <Button size="sm" disabled={!!busy} onClick={() => act("send")}><Send className="size-4" />{busy === "send" ? "Sending…" : "Send"}</Button>
        </div>
      </div>
    </div>
  )
}

export function EmailRoute() {
  const { data } = useInbox()
  const [uid, setUid] = useState<string | null>(null)
  const [composing, setComposing] = useState(false)
  const [prefill, setPrefill] = useState<Prefill | undefined>(undefined)
  const emails = data?.emails || []
  const reply = (p: Prefill) => { setPrefill(p); setUid(null); setComposing(true) }
  return (
    <div className="relative mx-auto flex h-full w-full max-w-3xl flex-col">
      {composing && <Compose initial={prefill} onClose={() => { setComposing(false); setPrefill(undefined) }} />}
      {uid ? <Reader uid={uid} onBack={() => setUid(null)} onReply={reply} /> : (
        <>
          <header className="flex h-13 shrink-0 items-center justify-between border-b px-4">
            <div className="text-sm font-semibold">Email <span className="font-normal text-muted-foreground">· Inbox</span></div>
            <Button size="sm" onClick={() => setComposing(true)}><PenSquare className="size-4" />Compose</Button>
          </header>
          <div className="flex-1 overflow-y-auto">
            {data?.error && <p className="p-4 text-sm text-muted-foreground">No mail account connected (or unavailable).</p>}
            <div className="divide-y">
              {emails.map((m) => {
                const from = m.from_name || m.from_address || m.from || m.from_addr || m.sender || "Unknown"
                const unread = m.is_read != null ? !m.is_read : (m.unread ?? m.seen === false)
                return (
                  <div key={m.uid} onClick={() => setUid(m.uid)} className="flex cursor-pointer items-baseline gap-3 px-4 py-3 hover:bg-accent/50">
                    <div className={cn("w-44 shrink-0 truncate text-sm", unread ? "font-semibold text-foreground" : "text-muted-foreground")}>{from}</div>
                    <div className="min-w-0 flex-1">
                      <span className={cn("text-sm", unread ? "font-medium text-foreground" : "text-muted-foreground")}>{m.subject || "(no subject)"}</span>
                      {(m.snippet || m.preview) && <span className="ml-2 text-sm text-muted-foreground">— {m.snippet || m.preview}</span>}
                    </div>
                    {m.date && <div className="shrink-0 text-xs text-muted-foreground">{new Date(m.date).toLocaleDateString()}</div>}
                  </div>
                )
              })}
            </div>
            {!data?.error && emails.length === 0 && <p className="p-8 text-center text-sm text-muted-foreground">Inbox empty.</p>}
          </div>
        </>
      )}
    </div>
  )
}
