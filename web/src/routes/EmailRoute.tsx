import { useState } from "react"
import { ArrowLeft, PenSquare, Send, X, Reply, Archive, MailOpen, Trash2, Search, Inbox, ChevronDown, Star, FolderInput, CheckCheck, Paperclip, Download, FileText } from "lucide-react"
import {
  useInbox, useEmail, useEmailActions, sendEmail, saveDraft,
  useFolders, useEmailSearch, useContacts, useAttachments,
  attachmentDownloadUrl, attachmentAsDoc,
  type EmailContact, type EmailAttachment,
} from "@/api/email"
import type { EmailMsg } from "@/types"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

// The list/search endpoints return a few fields the shared EmailMsg type
// doesn't model yet (folder stamp, flag/attachment markers). Augment locally
// rather than touch the shared types module.
type EmailListItem = EmailMsg & { folder?: string; is_flagged?: boolean; has_attachments?: boolean }

interface Prefill { to?: string; subject?: string; body?: string }

function formatSize(bytes?: number): string {
  if (!bytes) return ""
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

const DOC_EXTS = [".pdf", ".docx", ".txt", ".md", ".markdown"]
function canOpenAsDoc(filename: string): boolean {
  const lower = filename.toLowerCase()
  return DOC_EXTS.some((e) => lower.endsWith(e))
}

function AttachmentRow({ uid, folder, att }: { uid: string; folder: string; att: EmailAttachment }) {
  const [busy, setBusy] = useState("")
  const [done, setDone] = useState("")
  const open = canOpenAsDoc(att.filename)
  const saveDoc = async () => {
    setBusy("doc"); setDone("")
    try {
      const r = await attachmentAsDoc(uid, att.index, folder)
      setDone(r.error ? r.error : "Saved to documents")
    } catch { setDone("Failed") } finally { setBusy("") }
  }
  return (
    <div className="flex items-center gap-2 rounded-md border bg-card px-3 py-2 text-sm">
      <Paperclip className="size-4 shrink-0 text-muted-foreground" />
      <span className="min-w-0 flex-1 truncate" title={att.filename}>{att.filename}</span>
      {att.size ? <span className="shrink-0 text-xs text-muted-foreground">{formatSize(att.size)}</span> : null}
      {done && <span className="shrink-0 text-xs text-muted-foreground">{done}</span>}
      {open && (
        <button onClick={saveDoc} disabled={!!busy} title="Save as document" className="shrink-0 rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-50"><FileText className="size-4" /></button>
      )}
      <a href={attachmentDownloadUrl(uid, att.index, folder)} target="_blank" rel="noopener noreferrer" title="Download" className="shrink-0 rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"><Download className="size-4" /></a>
    </div>
  )
}

function MoveMenu({ folders, current, onMove }: { folders: string[]; current: string; onMove: (dest: string) => void }) {
  const [open, setOpen] = useState(false)
  const targets = folders.filter((f) => f !== current)
  return (
    <div className="relative">
      <button onClick={() => setOpen((o) => !o)} title="Move to folder" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"><FolderInput className="size-4" /></button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute right-0 top-full z-20 mt-1 max-h-64 w-56 origin-top-right animate-pop-in overflow-y-auto rounded-md border bg-popover py-1 text-sm shadow-lg">
            {targets.length === 0 && <div className="px-3 py-2 text-xs text-muted-foreground">No other folders</div>}
            {targets.map((f) => (
              <button key={f} onClick={() => { onMove(f); setOpen(false) }} className="block w-full truncate px-3 py-1.5 text-left hover:bg-accent">{f}</button>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

function Reader({ uid, folder, folders, onBack, onReply }: { uid: string; folder: string; folders: string[]; onBack: () => void; onReply: (p: Prefill) => void }) {
  const { data, isLoading } = useEmail(uid, folder)
  const { markUnread, archive, remove, flag, move, markAnswered } = useEmailActions(folder)
  const { data: attData } = useAttachments(uid, folder)
  const html = data?.body_html || data?.html
  const text = data?.body_text || data?.body || data?.text
  const from = data?.from_name || data?.from_address || data?.from || data?.from_addr || data?.sender || ""
  const addr = data?.from_address || data?.from_addr || from
  const flagged = !!data?.is_flagged
  const answered = !!data?.is_answered
  const attachments = attData?.attachments || []
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
          <button onClick={() => flag.mutate({ uid, on: !flagged })} title={flagged ? "Unflag" : "Flag"} className={cn("rounded-md p-1.5 hover:bg-accent", flagged ? "text-foreground" : "text-muted-foreground hover:text-foreground")}><Star className={cn("size-4", flagged && "fill-current")} /></button>
          <button onClick={() => markAnswered.mutate(uid)} title="Mark answered" className={cn("rounded-md p-1.5 hover:bg-accent", answered ? "text-foreground" : "text-muted-foreground hover:text-foreground")}><CheckCheck className="size-4" /></button>
          <button onClick={() => onReply({ to: addr, subject: /^re:/i.test(data?.subject || "") ? data?.subject : `Re: ${data?.subject || ""}`, body: `\n\n---\n${text || ""}` })} title="Reply" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"><Reply className="size-4" /></button>
          <button onClick={() => after(() => markUnread.mutate(uid))} title="Mark unread" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"><MailOpen className="size-4" /></button>
          <MoveMenu folders={folders} current={folder} onMove={(dest) => after(() => move.mutate({ uid, dest }))} />
          <button onClick={() => after(() => archive.mutate(uid))} title="Archive" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"><Archive className="size-4" /></button>
          <button onClick={() => { if (confirm("Delete this email?")) after(() => remove.mutate(uid)) }} title="Delete" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-destructive"><Trash2 className="size-4" /></button>
        </div>
      </header>
      {attachments.length > 0 && (
        <div className="shrink-0 space-y-1.5 border-b px-4 py-3">
          {attachments.map((att) => <AttachmentRow key={att.index} uid={uid} folder={folder} att={att} />)}
        </div>
      )}
      {isLoading ? <div className="p-6 text-sm text-muted-foreground">Loading…</div>
        : data?.error ? <div className="p-6 text-sm text-muted-foreground">Couldn't load this message.</div>
        : html ? <iframe title="email" sandbox="" srcDoc={html} className="min-h-0 w-full flex-1 bg-white" />
        : <pre className="flex-1 overflow-auto whitespace-pre-wrap p-6 text-sm">{text || "(empty)"}</pre>}
    </div>
  )
}

function ContactSuggest({ query, onPick }: { query: string; onPick: (c: EmailContact) => void }) {
  const { data } = useContacts(query)
  const contacts = data || []
  if (contacts.length === 0) return null
  return (
    <div className="absolute left-0 right-0 top-full z-20 mt-1 max-h-56 origin-top animate-pop-in overflow-y-auto rounded-md border bg-popover py-1 text-sm shadow-lg">
      {contacts.map((c) => (
        <button key={c.address} onMouseDown={(e) => { e.preventDefault(); onPick(c) }} className="block w-full px-3 py-1.5 text-left hover:bg-accent">
          <div className="truncate font-medium">{c.name}</div>
          <div className="truncate text-xs text-muted-foreground">{c.address}</div>
        </button>
      ))}
    </div>
  )
}

function Compose({ onClose, initial }: { onClose: () => void; initial?: Prefill }) {
  const [to, setTo] = useState(initial?.to || "")
  const [subject, setSubject] = useState(initial?.subject || "")
  const [body, setBody] = useState(initial?.body || "")
  const [busy, setBusy] = useState("")
  const [err, setErr] = useState("")
  const [toFocused, setToFocused] = useState(false)
  // The autocomplete query is the fragment after the last comma so it matches
  // the recipient currently being typed in a multi-address "To" field.
  const toQuery = to.split(",").pop()?.trim() || ""
  const pickContact = (c: EmailContact) => {
    const head = to.includes(",") ? to.slice(0, to.lastIndexOf(",") + 1) + " " : ""
    setTo(head + c.address + ", ")
  }
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
    <div className="absolute inset-0 z-10 flex animate-fade-in items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-lg animate-pop-in rounded-xl border bg-popover p-4 shadow-lg">
        <div className="mb-3 flex items-center justify-between"><div className="text-sm font-semibold">New message</div><button onClick={onClose} className="text-muted-foreground hover:text-foreground"><X className="size-4" /></button></div>
        <div className="relative mb-2">
          <input value={to} onChange={(e) => setTo(e.target.value)} onFocus={() => setToFocused(true)} onBlur={() => setToFocused(false)} placeholder="To" className={inp} />
          {toFocused && toQuery.length >= 1 && <ContactSuggest query={toQuery} onPick={pickContact} />}
        </div>
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

function FolderMenu({ folders, current, onPick }: { folders: string[]; current: string; onPick: (f: string) => void }) {
  const [open, setOpen] = useState(false)
  const list = folders.length ? folders : [current]
  return (
    <div className="relative">
      <button onClick={() => setOpen((o) => !o)} className="flex items-center gap-1.5 rounded-md px-2 py-1 text-sm font-semibold hover:bg-accent">
        <Inbox className="size-4 text-muted-foreground" />
        <span>{current}</span>
        <ChevronDown className="size-3.5 text-muted-foreground" />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute left-0 top-full z-20 mt-1 max-h-72 w-56 origin-top-left animate-pop-in overflow-y-auto rounded-md border bg-popover py-1 text-sm shadow-lg">
            {list.map((f) => (
              <button key={f} onClick={() => { onPick(f); setOpen(false) }} className={cn("block w-full truncate px-3 py-1.5 text-left hover:bg-accent", f === current && "font-medium")}>{f}</button>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

function EmailList({ emails, error, folder, emptyLabel, onOpen }: { emails: EmailListItem[]; error?: string; folder: string; emptyLabel: string; onOpen: (uid: string, folder: string) => void }) {
  return (
    <div className="flex-1 overflow-y-auto">
      {error && <p className="p-4 text-sm text-muted-foreground">No mail account connected (or unavailable).</p>}
      <div className="divide-y">
        {emails.map((m) => {
          const from = m.from_name || m.from_address || m.from || m.from_addr || m.sender || "Unknown"
          const unread = m.is_read != null ? !m.is_read : (m.unread ?? m.seen === false)
          return (
            <div key={m.uid} onClick={() => onOpen(m.uid, m.folder || folder)} className="flex cursor-pointer items-baseline gap-3 px-4 py-3 hover:bg-accent/50">
              <div className={cn("w-44 shrink-0 truncate text-sm", unread ? "font-semibold text-foreground" : "text-muted-foreground")}>{from}</div>
              <div className="min-w-0 flex-1">
                <span className={cn("text-sm", unread ? "font-medium text-foreground" : "text-muted-foreground")}>{m.subject || "(no subject)"}</span>
                {(m.snippet || m.preview) && <span className="ml-2 text-sm text-muted-foreground">— {m.snippet || m.preview}</span>}
              </div>
              {m.is_flagged && <Star className="size-3.5 shrink-0 fill-current text-foreground" />}
              {m.has_attachments && <Paperclip className="size-3.5 shrink-0 text-muted-foreground" />}
              {m.date && <div className="shrink-0 text-xs text-muted-foreground">{new Date(m.date).toLocaleDateString()}</div>}
            </div>
          )
        })}
      </div>
      {!error && emails.length === 0 && <p className="p-8 text-center text-sm text-muted-foreground">{emptyLabel}</p>}
    </div>
  )
}

export function EmailRoute() {
  const [folder, setFolder] = useState("INBOX")
  const [query, setQuery] = useState("")
  const [reader, setReader] = useState<{ uid: string; folder: string } | null>(null)
  const [composing, setComposing] = useState(false)
  const [prefill, setPrefill] = useState<Prefill | undefined>(undefined)
  const { data: folderData } = useFolders()
  const { data } = useInbox(folder)
  const searching = query.trim().length >= 2
  const { data: searchData, isFetching: searchFetching } = useEmailSearch(query, folder)
  const folders = folderData?.folders || []
  const reply = (p: Prefill) => { setPrefill(p); setReader(null); setComposing(true) }
  const openEmail = (uid: string, f: string) => setReader({ uid, folder: f })
  return (
    <div className="relative mx-auto flex h-full w-full max-w-3xl flex-col">
      {composing && <Compose initial={prefill} onClose={() => { setComposing(false); setPrefill(undefined) }} />}
      {reader ? <Reader uid={reader.uid} folder={reader.folder} folders={folders} onBack={() => setReader(null)} onReply={reply} /> : (
        <>
          <header className="flex h-13 shrink-0 items-center justify-between gap-2 border-b px-4">
            <FolderMenu folders={folders} current={folder} onPick={(f) => { setFolder(f); setQuery("") }} />
            <Button size="sm" onClick={() => setComposing(true)}><PenSquare className="size-4" />Compose</Button>
          </header>
          <div className="shrink-0 border-b px-4 py-2">
            <div className="relative">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search mail…"
                className="h-9 w-full rounded-md border bg-background pl-8 pr-8 text-sm outline-none focus-visible:border-ring"
              />
              {query && <button onClick={() => setQuery("")} title="Clear" className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"><X className="size-4" /></button>}
            </div>
          </div>
          {searching ? (
            searchFetching && !searchData ? <p className="p-8 text-center text-sm text-muted-foreground">Searching…</p>
              : <EmailList emails={searchData?.emails || []} error={searchData?.error} folder={folder} emptyLabel="No matches." onOpen={openEmail} />
          ) : (
            <EmailList emails={data?.emails || []} error={data?.error} folder={folder} emptyLabel={`${folder} empty.`} onOpen={openEmail} />
          )}
        </>
      )}
    </div>
  )
}
