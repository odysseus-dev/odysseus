import { useRef, useState } from "react"
import { ArrowLeft, Paperclip, Save, Send, X } from "lucide-react"
import { saveDraft, sendEmail, uploadComposeAttachment, deleteComposeAttachment } from "@/api/email"
import { useDocMutations } from "@/api/documents"
import { buildEmailDraft, parseEmailDraft, type EmailDraftFields } from "@/lib/emailDraft"
import { Button } from "@/components/ui/button"

const inp = "h-9 w-full rounded-md border bg-background px-3 text-sm outline-none focus-visible:border-ring"

export function EmailDraftEditor({
  docId,
  title,
  content,
  onBack,
  onClose,
}: {
  docId: string
  title?: string
  content: string
  onBack?: () => void
  onClose?: () => void
}) {
  const { update } = useDocMutations()
  const [fields, setFields] = useState<EmailDraftFields>(() => parseEmailDraft(content))
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState("")
  const [status, setStatus] = useState("")
  const [err, setErr] = useState("")
  const setField = (key: keyof EmailDraftFields, value: string) => {
    setFields((current) => ({ ...current, [key]: value }))
    setDirty(true)
    setStatus("")
    setErr("")
  }
  const fileInputRef = useRef<HTMLInputElement>(null)
  const removeAttachment = (token: string) => {
    setFields((current) => ({ ...current, attachments: current.attachments.filter((att) => att.token !== token) }))
    setDirty(true)
    setStatus("")
    setErr("")
    // Best-effort: drop the temp upload so abandoned attachments don't linger.
    // A 404 is expected if a prior server-side draft save already cleaned it.
    void deleteComposeAttachment(token).catch(() => { /* already cleaned */ })
  }
  const uploadFiles = async (files: FileList | null) => {
    if (!files || files.length === 0) return
    setBusy("upload"); setErr(""); setStatus("")
    try {
      const uploaded: EmailDraftFields["attachments"] = []
      for (const file of Array.from(files)) uploaded.push(await uploadComposeAttachment(file))
      setFields((current) => ({ ...current, attachments: [...current.attachments, ...uploaded] }))
      setDirty(true)
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Couldn't upload attachment.")
    } finally {
      setBusy("")
    }
  }
  const currentContent = buildEmailDraft(fields)
  const saveDoc = async () => {
    setBusy("save"); setErr("")
    try {
      await update.mutateAsync({ id: docId, content: currentContent })
      setDirty(false)
      setStatus("Saved")
    } catch {
      setErr("Couldn't save the draft document.")
    } finally {
      setBusy("")
    }
  }
  const emailAction = async (kind: "draft" | "send") => {
    if (!fields.to.trim()) {
      setErr("Recipient required.")
      return
    }
    setBusy(kind); setErr(""); setStatus("")
    try {
      if (dirty) {
        await update.mutateAsync({ id: docId, content: currentContent })
        setDirty(false)
      }
      const payload = {
        to: fields.to,
        cc: fields.cc || undefined,
        bcc: fields.bcc || undefined,
        subject: fields.subject,
        body: fields.body,
        in_reply_to: fields.inReplyTo || undefined,
        references: fields.references || undefined,
        attachments: fields.attachments.length > 0 ? fields.attachments.map((att) => att.token) : undefined,
        account_id: fields.sourceAccount || undefined,
      }
      const result = kind === "send" ? await sendEmail(payload) : await saveDraft(payload)
      if (result.success === false) {
        setErr(result.error || "Email action failed.")
        return
      }
      setStatus(kind === "send" ? "Sent" : "Draft saved")
    } catch {
      setErr(kind === "send" ? "Couldn't send the email." : "Couldn't save the email draft.")
    } finally {
      setBusy("")
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-background">
      <header className="flex h-13 shrink-0 items-center gap-2 border-b px-3">
        {onBack && <Button variant="ghost" size="icon" onClick={onBack} title="Back"><ArrowLeft className="size-4" /></Button>}
        <div className="min-w-0 flex-1 truncate text-sm font-semibold">{title || fields.subject || "Email draft"}</div>
        {status && <span className="shrink-0 text-xs text-muted-foreground">{status}</span>}
        {onClose && <button onClick={onClose} title="Close draft" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"><X className="size-4" /></button>}
      </header>
      <div className="shrink-0 space-y-2 border-b p-3">
        <input value={fields.to} onChange={(e) => setField("to", e.target.value)} placeholder="To" className={inp} />
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <input value={fields.cc} onChange={(e) => setField("cc", e.target.value)} placeholder="Cc" className={inp} />
          <input value={fields.bcc} onChange={(e) => setField("bcc", e.target.value)} placeholder="Bcc" className={inp} />
        </div>
        <input value={fields.subject} onChange={(e) => setField("subject", e.target.value)} placeholder="Subject" className={inp} />
      </div>
      {fields.attachments.length > 0 && (
        <div className="flex shrink-0 flex-wrap gap-2 border-b px-3 py-2">
          {fields.attachments.map((att) => (
            <span key={att.token} className="inline-flex max-w-full items-center gap-1.5 rounded-md border bg-muted/30 px-2 py-1 text-xs text-muted-foreground">
              <Paperclip className="size-3.5 shrink-0" />
              <span className="max-w-64 truncate text-foreground">{att.filename}</span>
              {att.size ? <span>{Math.round(att.size / 1024)} KB</span> : null}
              <button type="button" onClick={() => removeAttachment(att.token)} title={`Remove ${att.filename}`} className="rounded p-0.5 hover:bg-accent hover:text-foreground"><X className="size-3" /></button>
            </span>
          ))}
        </div>
      )}
      <textarea
        value={fields.body}
        onChange={(e) => setField("body", e.target.value)}
        placeholder="Write a reply..."
        className="min-h-0 flex-1 resize-none bg-transparent p-4 text-sm leading-relaxed outline-none"
      />
      {err && <div className="shrink-0 border-t px-4 py-2 text-xs text-destructive">{err}</div>}
      <footer className="flex shrink-0 flex-wrap items-center justify-end gap-2 border-t px-3 py-2">
        <input ref={fileInputRef} type="file" multiple className="hidden" onChange={(e) => { void uploadFiles(e.target.files); e.target.value = "" }} />
        <Button variant="outline" size="sm" className="mr-auto" disabled={!!busy} onClick={() => fileInputRef.current?.click()}><Paperclip className="size-4" />{busy === "upload" ? "Uploading..." : "Attach"}</Button>
        <Button variant="outline" size="sm" disabled={!!busy || !dirty} onClick={saveDoc}><Save className="size-4" />{busy === "save" ? "Saving..." : dirty ? "Save document" : "Saved"}</Button>
        <Button variant="outline" size="sm" disabled={!!busy} onClick={() => emailAction("draft")}>{busy === "draft" ? "Saving..." : "Save draft"}</Button>
        <Button size="sm" disabled={!!busy} onClick={() => emailAction("send")}><Send className="size-4" />{busy === "send" ? "Sending..." : "Send"}</Button>
      </footer>
    </div>
  )
}
