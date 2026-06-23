import { useEffect, useMemo, useRef, useState, type ReactNode } from "react"
import { ArrowLeft, PenSquare, Send, X, Reply, ReplyAll, Forward, Archive, MailOpen, Trash2, Search, Inbox, ChevronDown, Star, FolderInput, CheckCheck, Paperclip, Download, FileText, Sparkles, Filter, Bell, BellPlus, Clock, AlertCircle, Newspaper, Megaphone, RefreshCw, MoreVertical, ExternalLink, UserPlus, Ban, ShieldCheck, MessagesSquare, type LucideIcon } from "lucide-react"
import {
  useInbox, useEmail, useEmailActions, sendEmail, saveDraft,
  useFolders, useEmailSearch, useContacts, useAttachments,
  attachmentDownloadUrl, attachmentAsDoc, aiReply, summarizeEmail,
  uploadComposeAttachment, deleteComposeAttachment, scheduleEmail, useScheduledEmails, useCancelScheduledEmail, saveEmailSenderContact,
  type ComposeUpload, type EmailContact, type EmailAttachment, type EmailBoundaries, type EmailListFilter, type ScheduledEmail, type ThreadTurn,
} from "@/api/email"
import { useEmailAccounts, useEmailAccountMutations, type EmailAccount } from "@/api/accounts"
import { useDocMutations } from "@/api/documents"
import { useNoteMutations } from "@/api/notes"
import { EmailDraftEditor } from "@/components/email/EmailDraftEditor"
import { buildEmailDraft } from "@/lib/emailDraft"
import type { EmailMsg } from "@/types"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

// The list/search endpoints return a few fields the shared EmailMsg type
// doesn't model yet (folder stamp, flag/attachment markers). Augment locally
// rather than touch the shared types module.
type EmailListItem = EmailMsg & {
  folder?: string; is_flagged?: boolean; has_attachments?: boolean; is_answered?: boolean;
  tags?: string[]; is_spam_verdict?: boolean; date_epoch?: number; to?: string; cc?: string;
}

interface Prefill { to?: string; cc?: string; bcc?: string; subject?: string; body?: string; inReplyTo?: string; references?: string; accountId?: string }
interface SenderFilter { address: string; label: string }
interface ReplyDoc { id: string; title: string; content: string }

interface EmailFilterOption { value: EmailListFilter; label: string; icon: LucideIcon }

const SCHEDULED_FOLDER = "__scheduled__"

const EMAIL_FILTERS: EmailFilterOption[] = [
  { value: "all", label: "All", icon: Inbox },
  { value: "unread", label: "Unread", icon: MailOpen },
  { value: "favorites", label: "Favorites", icon: Star },
  { value: "undone", label: "Undone", icon: CheckCheck },
  { value: "reminders", label: "Reminders", icon: Bell },
  { value: "unanswered", label: "Unanswered", icon: Reply },
  { value: "pending_30d", label: "Pending 30d", icon: Clock },
  { value: "stale_30d", label: "Stale >30d", icon: Clock },
  { value: "tag:urgent", label: "Urgent", icon: AlertCircle },
  { value: "tag:reply-soon", label: "Reply soon", icon: Sparkles },
  { value: "tag:spam", label: "Spam", icon: Filter },
  { value: "tag:newsletter", label: "Newsletter", icon: Newspaper },
  { value: "tag:marketing", label: "Marketing", icon: Megaphone },
]

function filterLabel(value: EmailListFilter): string {
  return EMAIL_FILTERS.find((item) => item.value === value)?.label || "All"
}

function folderLabel(value: string): string {
  if (value === SCHEDULED_FOLDER) return "Scheduled"
  return value
}

function replySubject(subject?: string): string {
  const base = (subject || "").trim()
  return /^re\s*:/i.test(base) ? base : `Re: ${base}`
}

function forwardSubject(subject?: string): string {
  const base = (subject || "").trim()
  return /^fwd?\s*:/i.test(base) ? base : `Fwd: ${base}`
}

function splitAddressList(value?: string): string[] {
  return (value || "").split(",").map((item) => item.trim()).filter(Boolean)
}

function buildReplyAllCc(data: { to?: string; cc?: string } | undefined, ownAddresses: string[]): string {
  const mine = new Set(ownAddresses.map(extractEmailAddress).filter(Boolean))
  const seen = new Set<string>()
  const result: string[] = []
  for (const addr of [...splitAddressList(data?.to), ...splitAddressList(data?.cc)]) {
    const key = extractEmailAddress(addr)
    if (!key || mine.has(key) || seen.has(key)) continue
    seen.add(key)
    result.push(addr)
  }
  return result.join(", ")
}

function ownEmailAddresses(accounts: EmailAccount[]): string[] {
  const seen = new Set<string>()
  const result: string[] = []
  for (const account of accounts) {
    for (const addr of [account.from_address, account.smtp_user, account.imap_user]) {
      const key = extractEmailAddress(addr)
      if (!key || seen.has(key)) continue
      seen.add(key)
      result.push(key)
    }
  }
  return result
}

function htmlToText(html: string): string {
  if (typeof document === "undefined") return html.replace(/<[^>]+>/g, " ")
  const el = document.createElement("div")
  el.innerHTML = html
  return el.textContent || el.innerText || ""
}

function cleanAiReply(text: string): string {
  return text
    .replace(/<<<\s*(?:REPLY|SUMMARY|OUTPUT)\s*>>+/gi, "")
    .replace(/<<<\s*END\s*>>+/gi, "")
    .trim()
}

function quoteOriginal(body: string, from: string, date?: string): string {
  const quoted = body.split("\n").map((line) => `> ${line}`).join("\n")
  let when = date || ""
  try {
    if (date) {
      const d = new Date(date)
      if (!Number.isNaN(d.getTime())) when = d.toLocaleString()
    }
  } catch { /* keep server date */ }
  return `\n\n---------- Previous message ----------\nOn ${when}, ${from} wrote:\n${quoted}`
}

type PlainTextFoldKind = "signature" | "quote"
interface PlainTextFold { kind: PlainTextFoldKind; label: string; meta?: string; body: string }
interface FoldedPlainEmail { head: string; folds: PlainTextFold[] }

const SIGNATURE_BLOAT_MIN_CHARS = 200
const WROTE_WORDS = "(?:wrote|escribio|schrieb|skrev|schreef|napisal|napisala|napisali|hat geschrieben|kirjoitti|escreveu)"
const HTML_EMAIL_FOLD_CSS = `
body{margin:0;padding:24px;background:#fff;color:#18181b;overflow-wrap:anywhere;}
img,table{max-width:100%;}
.odys-email-fold{margin:12px 0;border:1px solid #e4e4e7;border-radius:6px;background:#fafafa;overflow:hidden;}
.odys-email-fold>summary{display:flex;align-items:center;gap:8px;min-height:34px;padding:8px 10px;cursor:pointer;color:#71717b;font:600 12px/1.35 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;list-style:none;}
.odys-email-fold>summary::-webkit-details-marker{display:none;}
.odys-email-fold>summary::after{content:"v";margin-left:auto;font-size:10px;transition:transform .15s ease;}
.odys-email-fold[open]>summary::after{transform:rotate(180deg);}
.odys-email-fold-meta{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:400;}
.odys-email-fold-body{border-top:1px solid #e4e4e7;padding:10px;color:#52525b;}
.odys-email-fold-body blockquote{margin:8px 0;padding-left:12px;border-left:2px solid #d4d4d8;}
`

function lineSlices(text: string): Array<{ line: string; start: number; end: number; next: number }> {
  const matches = text.matchAll(/[^\r\n]*(?:\r\n|\n|\r|$)/g)
  const rows: Array<{ line: string; start: number; end: number; next: number }> = []
  for (const match of matches) {
    const raw = match[0]
    if (!raw) continue
    const start = match.index || 0
    const line = raw.replace(/\r?\n|\r$/, "")
    rows.push({ line, start, end: start + line.length, next: start + raw.length })
  }
  return rows
}

function compactPlainText(text: string): string {
  return text.replace(/\s+/g, " ").trim()
}

function isBloatedSignature(text: string): boolean {
  return compactPlainText(text).length >= SIGNATURE_BLOAT_MIN_CHARS
}

function truncateFoldMeta(value: string, limit: number): string {
  const clean = value.replace(/[<>]/g, "").replace(/\s+/g, " ").trim()
  return clean.length > limit ? `${clean.slice(0, Math.max(0, limit - 3))}...` : clean
}

function quoteFoldMeta(section: string): string {
  const unquoted = section.replace(/^\s*>+\s?/gm, "")
  const lines = unquoted.split(/\r?\n/).map((line) => line.trim()).filter(Boolean).slice(0, 14)
  const from = lines.find((line) => /^From\s*:/i.test(line))?.replace(/^From\s*:\s*/i, "") || ""
  const sent = lines.find((line) => /^(Sent|Date)\s*:/i.test(line))?.replace(/^(Sent|Date)\s*:\s*/i, "") || ""
  if (from && sent) return `${truncateFoldMeta(from, 60)} · ${truncateFoldMeta(sent, 28)}`
  if (from) return truncateFoldMeta(from, 80)
  if (sent) return truncateFoldMeta(sent, 80)

  const wroteLine = lines.find((line) => new RegExp(`^On\\s+.+\\s${WROTE_WORDS}\\s*:\\s*$`, "i").test(line))
  const wroteMatch = wroteLine?.match(new RegExp(`^On\\s+(.+?)\\s${WROTE_WORDS}\\s*:\\s*$`, "i"))
  if (!wroteMatch) return ""
  const attribution = wroteMatch[1].replace(/,\s*$/, "").trim()
  const splitAt = attribution.lastIndexOf(",")
  if (splitAt > 0) {
    const date = attribution.slice(0, splitAt).trim()
    const person = attribution.slice(splitAt + 1).trim()
    if (person && date) return `${truncateFoldMeta(person, 60)} · ${truncateFoldMeta(date, 28)}`
  }
  return truncateFoldMeta(attribution, 80)
}

function findQuoteStart(text: string): number {
  const rows = lineSlices(text)
  if (rows.length === 0) return -1
  const wroteLineRe = new RegExp(`^\\s*On\\s+.+\\s${WROTE_WORDS}\\s*:\\s*$`, "i")
  const originalRe = /^\s*[-_=]{3,}\s*(?:Original Message|Forwarded message|Previous message|Ursprungliche Nachricht|Mensaje original|Messaggio originale|Oorspronkelijk bericht)\s*[-_=]{3,}\s*$/i
  for (let i = 0; i < rows.length; i++) {
    const line = rows[i].line.trim()
    if (originalRe.test(line) || wroteLineRe.test(line)) return rows[i].start
    if (/^From\s*:/i.test(line)) {
      const window = rows.slice(i, i + 8).map((row) => row.line).join("\n")
      if (/^(Sent|Date)\s*:/im.test(window) && /^Subject\s*:/im.test(window)) return rows[i].start
    }
  }
  for (let i = 0; i < rows.length; i++) {
    if (!/^\s*>/.test(rows[i].line)) continue
    const head = text.slice(0, rows[i].start)
    if (!head.trim()) continue
    const tailRows = rows.slice(i)
    const quotedRows = tailRows.filter((row) => /^\s*>/.test(row.line))
    const quotedText = quotedRows.map((row) => row.line.replace(/^\s*>+\s?/, "")).join("\n")
    if (quotedRows.length >= 2 && compactPlainText(quotedText).length >= 120) return rows[i].start
  }
  return -1
}

function looksContactish(line: string): boolean {
  return /[@]|tel\.?:|mobile:|phone:|www\.|https?:\/\/|^\+?\d[\d \-().]{6,}$/i.test(line.trim())
}

function findSignatureFoldStart(text: string): number {
  const rows = lineSlices(text)
  if (rows.length === 0) return -1
  const closingRe = /^(?:Best regards|Best wishes|Kind regards|Yours truly|Yours sincerely|Yours faithfully|Sincerely|Cheers|Thanks|Thank you|Regards|Warm regards|Many thanks|Talk soon|Take care)[,!.\s]*$/i
  const mobileRe = /^(?:Sent from my (?:iPhone|iPad|Android|Galaxy|Pixel|phone|mobile)|Get Outlook for (?:iOS|Android|Windows|Mac|mobile))/i
  const disclaimerRe = /^(?:CONFIDENTIALITY NOTICE|DISCLAIMER|This e-?mail (?:is confidential|may contain confidential)|The information (?:contained )?in this e-?mail|This message and any attachments)/i

  for (const row of rows) {
    if (row.line.trim() !== "--") continue
    const tail = text.slice(row.next)
    if (isBloatedSignature(tail)) return row.start
  }

  for (let i = 0; i < rows.length; i++) {
    const row = rows[i]
    if (!mobileRe.test(row.line.trim()) && !disclaimerRe.test(row.line.trim())) continue
    const tail = text.slice(row.start)
    if (isBloatedSignature(tail)) return row.start
  }

  const lateBodyStart = Math.max(text.length * 0.45, text.length - 1200)
  for (let i = 0; i < rows.length; i++) {
    const row = rows[i]
    if (row.start < lateBodyStart || !closingRe.test(row.line.trim())) continue
    let foldStart = row.next
    for (let j = i + 1; j < rows.length; j++) {
      const nextLine = rows[j].line.trim()
      if (!nextLine) continue
      if (!looksContactish(nextLine)) foldStart = rows[j].next
      break
    }
    const tail = text.slice(foldStart)
    if (isBloatedSignature(tail)) return foldStart
  }
  return -1
}

function boundaryOffset(value: unknown, length: number): number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value < length ? Math.floor(value) : -1
}

function splitFoldedPlainEmailByBoundaries(text: string, boundaries?: EmailBoundaries | null): FoldedPlainEmail | null {
  if (!boundaries || !text) return null
  const sigStart = boundaryOffset(boundaries.sig_start, text.length)
  const quoteStart = boundaryOffset(boundaries.quote_start, text.length)
  if (sigStart < 0 && quoteStart < 0) return null

  const folds: PlainTextFold[] = []

  if (sigStart >= 0 && quoteStart >= 0) {
    if (sigStart < quoteStart) {
      const sigBody = text.slice(sigStart, quoteStart).trim()
      const quoteBody = text.slice(quoteStart).trim()
      if (sigBody && isBloatedSignature(sigBody)) {
        folds.push({ kind: "signature", label: "Signature", body: sigBody })
        if (quoteBody) folds.push({ kind: "quote", label: "Earlier thread", meta: quoteFoldMeta(quoteBody), body: quoteBody })
        return { head: text.slice(0, sigStart).trimEnd(), folds }
      }
      if (quoteBody) folds.push({ kind: "quote", label: "Earlier thread", meta: quoteFoldMeta(quoteBody), body: quoteBody })
      return { head: text.slice(0, quoteStart).trimEnd(), folds }
    }

    const quoteBody = text.slice(quoteStart, sigStart).trim()
    const sigBody = text.slice(sigStart).trim()
    if (sigBody && isBloatedSignature(sigBody)) {
      if (quoteBody) folds.push({ kind: "quote", label: "Earlier thread", meta: quoteFoldMeta(quoteBody), body: quoteBody })
      folds.push({ kind: "signature", label: "Signature", body: sigBody })
      return { head: text.slice(0, quoteStart).trimEnd(), folds }
    }
    const fullQuote = text.slice(quoteStart).trim()
    if (fullQuote) folds.push({ kind: "quote", label: "Earlier thread", meta: quoteFoldMeta(fullQuote), body: fullQuote })
    return { head: text.slice(0, quoteStart).trimEnd(), folds }
  }

  if (quoteStart >= 0) {
    const quoteBody = text.slice(quoteStart).trim()
    if (quoteBody) folds.push({ kind: "quote", label: "Earlier thread", meta: quoteFoldMeta(quoteBody), body: quoteBody })
    return { head: text.slice(0, quoteStart).trimEnd(), folds }
  }

  const sigBody = text.slice(sigStart).trim()
  if (!sigBody || !isBloatedSignature(sigBody)) return null
  folds.push({ kind: "signature", label: "Signature", body: sigBody })
  return { head: text.slice(0, sigStart).trimEnd(), folds }
}

function splitFoldedPlainEmail(text: string, boundaries?: EmailBoundaries | null): FoldedPlainEmail {
  const boundarySplit = splitFoldedPlainEmailByBoundaries(text, boundaries)
  if (boundarySplit) return boundarySplit

  const quoteStart = findQuoteStart(text)
  const visibleEnd = quoteStart >= 0 ? quoteStart : text.length
  const visibleText = text.slice(0, visibleEnd)
  const sigStart = findSignatureFoldStart(visibleText)
  const folds: PlainTextFold[] = []
  const headEnd = sigStart >= 0 ? sigStart : visibleEnd
  if (sigStart >= 0) {
    const sigBody = text.slice(sigStart, visibleEnd).trim()
    if (sigBody) folds.push({ kind: "signature", label: "Signature", body: sigBody })
  }
  if (quoteStart >= 0) {
    const quoteBody = text.slice(quoteStart).trim()
    if (quoteBody) folds.push({ kind: "quote", label: "Earlier thread", meta: quoteFoldMeta(quoteBody), body: quoteBody })
  }
  return { head: text.slice(0, headEnd).trimEnd(), folds }
}

function FoldedPlainEmailBody({ text, boundaries }: { text?: string; boundaries?: EmailBoundaries | null }) {
  const value = text || ""
  const folded = useMemo(() => splitFoldedPlainEmail(value, boundaries), [value, boundaries])
  if (!value || folded.folds.length === 0) {
    return <pre className="flex-1 overflow-auto whitespace-pre-wrap break-words p-6 text-sm">{value || "(empty)"}</pre>
  }
  return (
    <div className="flex-1 overflow-auto p-6 text-sm">
      {folded.head && <pre className="whitespace-pre-wrap break-words font-sans leading-relaxed">{folded.head}</pre>}
      <div className={cn("space-y-2", folded.head && "mt-4")}>
        {folded.folds.map((fold, index) => (
          <details key={`${fold.kind}-${index}`} className="group rounded-md border bg-muted/20">
            <summary className="flex min-h-9 cursor-pointer list-none items-center gap-2 px-3 py-2 text-xs font-medium text-muted-foreground marker:hidden hover:bg-accent/60">
              <span>{fold.label}</span>
              {fold.meta && <span className="truncate font-normal">{fold.meta}</span>}
              <ChevronDown className="ml-auto size-3.5 shrink-0 transition-transform group-open:rotate-180" />
            </summary>
            <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words border-t p-3 font-sans text-xs leading-relaxed text-muted-foreground">{fold.body}</pre>
          </details>
        ))}
      </div>
    </div>
  )
}

// Thread-turn meta from the backend parser looks like "Author <email> · date".
// Pull out a display author + date for the stacked card header. Falls back to
// the raw meta string when it doesn't match the expected shape.
function parseTurnMeta(meta?: string | null): { author: string; date: string } {
  const raw = (meta || "").trim()
  if (!raw) return { author: "", date: "" }
  const [left, ...rest] = raw.split("·")
  const date = rest.join("·").trim()
  let author = left.trim()
  const bracket = author.match(/^(.*?)\s*<[^>]+>\s*$/)
  if (bracket && bracket[1].trim()) author = bracket[1].trim()
  return { author: author || raw, date }
}

// Render parsed thread turns as stacked message cards. Level 0 (the current
// message) shows expanded; deeper, older turns collapse into <details> so the
// reader can drill into the history without it dominating the pane. Mirrors the
// legacy collapsible thread view (emailLibrary.js).
function ThreadedEmailView({
  turns,
  from,
  date,
}: {
  turns: ThreadTurn[]
  from: string
  date?: string
}) {
  const ordered = useMemo(() => turns.slice().sort((a, b) => a.level - b.level), [turns])
  return (
    <div className="flex-1 space-y-3 overflow-auto p-6">
      {ordered.map((turn, index) => {
        const meta = turn.level === 0
          ? { author: from || "Me", date: date ? new Date(date).toLocaleString() : "" }
          : parseTurnMeta(turn.meta)
        const html = transformEmailHtml(turn.body_html || "")
        const header = (
          <div className="flex min-w-0 items-baseline gap-2">
            <span className="truncate text-sm font-medium text-foreground">{meta.author || "Earlier reply"}</span>
            {meta.date && <span className="shrink-0 text-xs text-muted-foreground">{meta.date}</span>}
          </div>
        )
        if (turn.level === 0) {
          return (
            <div key={`turn-${index}`} className="overflow-hidden rounded-md border bg-card">
              <div className="border-b px-3 py-2">{header}</div>
              <iframe title={`thread-turn-${index}`} sandbox="" srcDoc={html} className="min-h-48 w-full bg-white" />
            </div>
          )
        }
        return (
          <details key={`turn-${index}`} className="group overflow-hidden rounded-md border bg-muted/20">
            <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 marker:hidden hover:bg-accent/60">
              {header}
              <ChevronDown className="ml-auto size-3.5 shrink-0 text-muted-foreground transition-transform group-open:rotate-180" />
            </summary>
            <iframe title={`thread-turn-${index}`} sandbox="" srcDoc={html} className="min-h-40 w-full border-t bg-white" />
          </details>
        )
      })}
    </div>
  )
}

function escapeHtmlText(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
}

function htmlFragmentText(html: string): string {
  if (typeof document === "undefined") return html.replace(/<[^>]+>/g, " ")
  const el = document.createElement("div")
  el.innerHTML = html
  return el.textContent || el.innerText || ""
}

function foldSummaryHtml(label: string, meta?: string): string {
  const cleanMeta = (meta || "").trim()
  return `<summary><span>${escapeHtmlText(label)}</span>${cleanMeta ? `<span class="odys-email-fold-meta">${escapeHtmlText(cleanMeta)}</span>` : ""}</summary>`
}

function createHtmlFold(doc: Document, kind: PlainTextFoldKind, label: string, meta?: string): { details: HTMLDetailsElement; body: HTMLDivElement } {
  const details = doc.createElement("details")
  details.className = `odys-email-fold odys-email-${kind}-fold`
  const summary = doc.createElement("summary")
  const labelSpan = doc.createElement("span")
  labelSpan.textContent = label
  summary.appendChild(labelSpan)
  if (meta) {
    const metaSpan = doc.createElement("span")
    metaSpan.className = "odys-email-fold-meta"
    metaSpan.textContent = meta
    summary.appendChild(metaSpan)
  }
  const body = doc.createElement("div")
  body.className = "odys-email-fold-body"
  details.append(summary, body)
  return { details, body }
}

function htmlLooksLikeSignature(el: Element): boolean {
  const text = compactPlainText(el.textContent || "")
  if (!text) return false
  const sigTells = [
    /\bregistered\s+in\b/i,
    /\blimited\s+liability\s+partnership\b/i,
    /\b(Pte\.?\s*Ltd|GmbH|S\.A\.|S\.A\.S|LLC|LLP|Inc\.?)\b/,
    /\bintended\s+solely\s+for\b/i,
    /\bconfidential(?:ity)?\s+(?:notice|information)\b/i,
    /\b(?:disclaimer|please\s+(?:notify|delete))\b/i,
    /\bunsubscribe\b/i,
    /\b\+\d[\d\s().-]{6,}\b/,
  ]
  const priorTells = [/\bHi\s+[A-Z][a-z]+\b/, /\bDear\s+[A-Z][a-z]+\b/, /\bRegards\b/i, /\?\s*$/]
  const sigScore = sigTells.filter((re) => re.test(text)).length
  const priorScore = priorTells.filter((re) => re.test(text)).length
  return sigScore >= 3 && priorScore <= 1
}

function nearbyAttributionMeta(bq: Element): string {
  const parent = bq.parentNode
  if (!parent) return ""
  const nodes: ChildNode[] = []
  let cursor = bq.previousSibling
  let nonEmpty = 0
  while (cursor && nonEmpty < 3) {
    nodes.unshift(cursor)
    const text = (cursor.textContent || "").trim()
    if (text) nonEmpty += 1
    const collected = compactPlainText(nodes.map((node) => node.textContent || "").join("\n"))
    if (collected.length > 800) break
    cursor = cursor.previousSibling
  }
  const text = nodes.map((node) => node.textContent || "").join("\n").trim()
  if (!text) return ""
  const isAttribution = new RegExp(`\\bOn\\b.+\\b${WROTE_WORDS}\\s*:\\s*$`, "i").test(text)
    || /Original Message|Forwarded message|Previous message/i.test(text)
    || (/From\s*:/i.test(text) && /(Sent|Date)\s*:/i.test(text))
  if (!isAttribution) return ""
  const meta = quoteFoldMeta(text) || truncateFoldMeta(text, 80)
  if (text.length <= 320) {
    for (const node of nodes) {
      if (node.parentNode === parent) parent.removeChild(node)
    }
  }
  return meta
}

function foldHtmlQuotedBlocks(doc: Document): void {
  const root = doc.body
  const blockquotes = Array.from(root.querySelectorAll("blockquote")).filter((bq) =>
    !bq.parentElement?.closest("blockquote") && !bq.closest("details")
  )
  for (const bq of blockquotes) {
    const parent = bq.parentNode
    if (!parent) continue
    const meta = nearbyAttributionMeta(bq) || quoteFoldMeta(bq.textContent || "")
    const kind: PlainTextFoldKind = !meta && htmlLooksLikeSignature(bq) ? "signature" : "quote"
    const { details, body } = createHtmlFold(doc, kind, kind === "signature" ? "Signature" : "Earlier thread", meta)
    parent.insertBefore(details, bq)
    body.appendChild(bq)
  }
}

function foldHtmlExplicitSignatures(doc: Document): void {
  const selector = ".gmail_signature, [data-smartmail='gmail_signature'], #Signature, #signature, #divRplyFwdMsg"
  const candidates = Array.from(doc.body.querySelectorAll(selector)).filter((el) =>
    !el.closest("details") && !el.parentElement?.closest(selector)
  )
  for (const el of candidates) {
    if (!isBloatedSignature(el.textContent || "")) continue
    const parent = el.parentNode
    if (!parent) continue
    const { details, body } = createHtmlFold(doc, "signature", "Signature")
    parent.insertBefore(details, el)
    body.appendChild(el)
  }
}

function foldHtmlRegexQuote(html: string): string {
  if (html.includes("odys-email-quote-fold")) return html
  const outlookRe = /(<br\s*\/?>|<\/p>|<\/div>|<p[^>]*>|<div[^>]*>|\n)\s*((?:<[^>]+>\s*)*From\s*:\s*[\s\S]+?(?:Sent|Date)\s*:[\s\S]+?Subject\s*:[\s\S]+)$/i
  const match = html.match(outlookRe)
  if (!match) return html
  const idx = html.lastIndexOf(match[0])
  if (idx < 0) return html
  const quoteHtml = match[2]
  const meta = quoteFoldMeta(htmlFragmentText(quoteHtml))
  return `${html.slice(0, idx)}${match[1]}<details class="odys-email-fold odys-email-quote-fold">${foldSummaryHtml("Earlier thread", meta)}<div class="odys-email-fold-body">${quoteHtml}</div></details>`
}

function foldHtmlRegexSignature(html: string): string {
  const wrap = (idx: number, marker: string, tail: string) => {
    if (!isBloatedSignature(htmlFragmentText(tail))) return html
    if (/<blockquote\b/i.test(tail) || tail.includes("odys-email-quote-fold")) return html
    return `${html.slice(0, idx)}${marker}<details class="odys-email-fold odys-email-signature-fold">${foldSummaryHtml("Signature")}<div class="odys-email-fold-body">${tail}</div></details>`
  }
  let match = html.match(/(<br\s*\/?>|\n)\s*--\s*(<br\s*\/?>|\n)([\s\S]*)$/i)
  if (match) {
    const idx = html.lastIndexOf(match[0])
    if (idx >= 0) return wrap(idx, match[1], match[3])
  }
  const blockBoundary = "(?:<br\\s*/?>|<\\/p>|<\\/div>|<\\/li>|<p[^>]*>|<div[^>]*>|<span[^>]*>|\\n)"
  match = html.match(new RegExp(`(${blockBoundary})\\s*((?:Sent from my (?:iPhone|iPad|Android|Galaxy|Pixel|phone|mobile)|Get Outlook for (?:iOS|Android|Windows|Mac|mobile)|CONFIDENTIALITY NOTICE|DISCLAIMER|This e-?mail (?:is confidential|may contain confidential)|The information (?:contained )?in this e-?mail|This message and any attachments)[\\s\\S]*)$`, "i"))
  if (match) {
    const idx = html.lastIndexOf(match[0])
    if (idx >= 0) return wrap(idx, match[1], match[2])
  }
  return html
}

function transformEmailHtml(html: string): string {
  if (typeof DOMParser === "undefined") return html
  try {
    const doc = new DOMParser().parseFromString(html, "text/html")
    foldHtmlQuotedBlocks(doc)
    doc.body.innerHTML = foldHtmlRegexQuote(doc.body.innerHTML)
    foldHtmlExplicitSignatures(doc)
    doc.body.innerHTML = foldHtmlRegexSignature(doc.body.innerHTML)
    doc.querySelectorAll("a[href]").forEach((link) => {
      link.setAttribute("target", "_blank")
      link.setAttribute("rel", "noopener noreferrer")
    })
    const base = doc.createElement("base")
    base.target = "_blank"
    doc.head.prepend(base)
    const style = doc.createElement("style")
    style.textContent = HTML_EMAIL_FOLD_CSS
    doc.head.appendChild(style)
    return `<!doctype html>\n${doc.documentElement.outerHTML}`
  } catch {
    return html
  }
}

function HtmlEmailFrame({ html }: { html: string }) {
  const srcDoc = useMemo(() => transformEmailHtml(html), [html])
  return <iframe title="email" sandbox="" srcDoc={srcDoc} className="min-h-0 w-full flex-1 bg-white" />
}

function currentHtmlMessageText(html: string): string {
  if (typeof DOMParser === "undefined") return htmlToText(html)
  try {
    const transformed = transformEmailHtml(html)
    const doc = new DOMParser().parseFromString(transformed, "text/html")
    doc.body.querySelectorAll(".odys-email-fold").forEach((el) => el.remove())
    return compactPlainText(doc.body.textContent || "") ? (doc.body.textContent || "").trim() : htmlToText(html).trim()
  } catch {
    return htmlToText(html).trim()
  }
}

function currentMessageText(text?: string, html?: string, boundaries?: EmailBoundaries | null): string {
  const raw = text || (html ? currentHtmlMessageText(html) : "")
  if (!raw.trim()) return ""
  const folded = splitFoldedPlainEmail(raw, boundaries)
  const head = folded.folds.length > 0 ? folded.head.trim() : raw.trim()
  return head || raw.trim()
}

function forwardedBody(data: { from?: string; from_name?: string; from_address?: string; date?: string; subject?: string; to?: string; cc?: string }, body: string): string {
  const from = data.from || (data.from_name && data.from_address ? `${data.from_name} <${data.from_address}>` : data.from_address) || ""
  const headers = [
    "---------- Forwarded message ----------",
    from ? `From: ${from}` : "",
    data.date ? `Date: ${data.date}` : "",
    data.subject ? `Subject: ${data.subject}` : "",
    data.to ? `To: ${data.to}` : "",
    data.cc ? `Cc: ${data.cc}` : "",
  ].filter(Boolean).join("\n")
  return `\n\n${headers}\n\n${body || ""}`
}

function firstNameFromSender(value: string): string {
  const namePart = (value.match(/^"?([^"<,@]+(?:\s+[^"<,@]+)*)"?\s*</)?.[1] || value.split("@")[0] || "").trim()
  const first = namePart.replace(/^["']|["']$/g, "").split(/[\s,]+/)[0] || "someone"
  return first ? first.charAt(0).toUpperCase() + first.slice(1) : "Someone"
}

function localDateTimeValue(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

interface ReminderPreset { label: string; detail: string; date: Date }
function emailReminderPresets(now = new Date()): ReminderPreset[] {
  const sixPm = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 18, 0, 0, 0)
  const laterToday = new Date(sixPm.getTime() - now.getTime() < 60 * 60 * 1000 ? now.getTime() + 3 * 60 * 60 * 1000 : sixPm.getTime())
  const tomorrow = new Date(now)
  tomorrow.setDate(tomorrow.getDate() + 1)
  tomorrow.setHours(8, 0, 0, 0)
  const daysUntilMonday = (8 - now.getDay()) % 7 || 7
  const nextWeek = new Date(now)
  nextWeek.setDate(now.getDate() + daysUntilMonday)
  nextWeek.setHours(8, 0, 0, 0)
  return [
    { label: "Later today", detail: laterToday.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }), date: laterToday },
    { label: "Tomorrow", detail: tomorrow.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }), date: tomorrow },
    { label: "Next week", detail: `${nextWeek.toLocaleDateString([], { weekday: "short" })} ${nextWeek.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`, date: nextWeek },
  ]
}

function shouldUseFastAiReply(subject: string, body: string, attachments: EmailAttachment[]): boolean {
  if (attachments.length > 0) return false
  const text = `${subject}\n${body}`.toLowerCase()
  if (/\b(attach(?:ed|ment)?|pdf|document|contract|invoice|receipt|quote|estimate|proposal|question|questions|details|schedule|booking|reservation|meeting|calendar|availability|confirm|confirmation|review|sign|signature)\b/.test(text)) {
    return false
  }
  return body.length < 2500
}

function formatSize(bytes?: number): string {
  if (!bytes) return ""
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatScheduledDate(value?: string): string {
  if (!value) return "Unknown time"
  const normalized = /(?:Z|[+-]\d\d:\d\d)$/.test(value) ? value : `${value}Z`
  const d = new Date(normalized)
  if (Number.isNaN(d.getTime())) return value
  return d.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })
}

const DOC_EXTS = [".pdf", ".docx", ".txt", ".md", ".markdown"]
function canOpenAsDoc(filename: string): boolean {
  const lower = filename.toLowerCase()
  return DOC_EXTS.some((e) => lower.endsWith(e))
}

function extractEmailAddress(value?: string): string {
  const raw = (value || "").trim()
  const bracket = raw.match(/<([^>]+)>/)
  return (bracket?.[1] || raw).trim().toLowerCase()
}

function emailSenderAddress(m: EmailListItem): string {
  return extractEmailAddress(m.from_address || m.from_addr || m.from || m.sender)
}

function emailIsRead(m: EmailListItem): boolean {
  if (typeof m.is_read === "boolean") return m.is_read
  if (typeof m.seen === "boolean") return m.seen
  if (typeof m.unread === "boolean") return !m.unread
  return true
}

function emailTimeMs(m: EmailListItem): number {
  if (m.date_epoch) return m.date_epoch * 1000
  if (!m.date) return 0
  const t = new Date(m.date).getTime()
  return Number.isNaN(t) ? 0 : t
}

function emailTags(m: EmailListItem): string[] {
  return (m.tags || []).map((tag) => String(tag).trim().toLowerCase().replace("_", "-"))
}

function matchesFilter(m: EmailListItem, filter: EmailListFilter): boolean {
  const read = emailIsRead(m)
  const answered = !!m.is_answered
  const tags = emailTags(m)
  const ageMs = Date.now() - emailTimeMs(m)
  const thirtyDaysMs = 30 * 24 * 60 * 60 * 1000
  switch (filter) {
    case "all": return true
    case "unread": return !read
    case "favorites": return !!m.is_flagged
    case "undone": return !answered
    case "reminders": return /^Reminder \(Odysseus\):/i.test(m.subject || "")
    case "unanswered": return !read && !answered
    case "pending_30d": return !answered && ageMs >= 0 && ageMs <= thirtyDaysMs
    case "stale_30d": return !answered && ageMs > thirtyDaysMs
    case "tag:urgent": return tags.includes("urgent")
    case "tag:reply-soon": return tags.includes("reply-soon")
    case "tag:spam": return !!m.is_spam_verdict || tags.includes("spam")
    case "tag:newsletter": return tags.includes("newsletter")
    case "tag:marketing": return tags.includes("marketing") || tags.includes("promo")
    default: return true
  }
}

function matchesSender(m: EmailListItem, sender: SenderFilter | null): boolean {
  return !sender || emailSenderAddress(m) === sender.address.toLowerCase()
}

function applySearchFilters(emails: EmailListItem[], filter: EmailListFilter, hasAttachments: boolean, sender: SenderFilter | null): EmailListItem[] {
  return emails.filter((m) => matchesFilter(m, filter) && (!hasAttachments || !!m.has_attachments) && matchesSender(m, sender))
}

function emailEmptyLabel(folder: string, filter: EmailListFilter, hasAttachments: boolean, sender: SenderFilter | null) {
  if (sender) return `No mail from ${sender.label}.`
  if (filter !== "all" && hasAttachments) return `No ${filterLabel(filter).toLowerCase()} mail with attachments.`
  if (filter !== "all") return `No ${filterLabel(filter).toLowerCase()} mail.`
  if (hasAttachments) return "No mail with attachments."
  return `${folder} empty.`
}

function parseEmailHash(hash: string): { folder: string; uid: string } | null {
  const decoded = decodeURIComponent((hash || "").replace(/^#/, ""))
  if (!decoded.startsWith("email=")) return null
  const raw = decoded.slice("email=".length)
  const sep = raw.lastIndexOf(":")
  if (sep <= 0 || sep >= raw.length - 1) return null
  return { folder: raw.slice(0, sep), uid: raw.slice(sep + 1) }
}

function AttachmentRow({ uid, folder, accountId, att }: { uid: string; folder: string; accountId?: string; att: EmailAttachment }) {
  const [busy, setBusy] = useState("")
  const [done, setDone] = useState("")
  const open = canOpenAsDoc(att.filename)
  const saveDoc = async () => {
    setBusy("doc"); setDone("")
    try {
      const r = await attachmentAsDoc(uid, att.index, folder, accountId)
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
      <a href={attachmentDownloadUrl(uid, att.index, folder, accountId)} target="_blank" rel="noopener noreferrer" title="Download" className="shrink-0 rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"><Download className="size-4" /></a>
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

function ReminderMenu({
  busy,
  onPick,
}: {
  busy: boolean
  onPick: (date: Date) => void
}) {
  const [open, setOpen] = useState(false)
  const [custom, setCustom] = useState(() => {
    const tomorrow = new Date()
    tomorrow.setDate(tomorrow.getDate() + 1)
    tomorrow.setHours(8, 0, 0, 0)
    return localDateTimeValue(tomorrow)
  })
  const presets = emailReminderPresets()
  const pick = (date: Date) => {
    onPick(date)
    setOpen(false)
  }
  return (
    <div className="relative">
      <button onClick={() => setOpen((o) => !o)} disabled={busy} title="Remind to reply" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-50"><BellPlus className="size-4" /></button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute right-0 top-full z-20 mt-1 w-64 origin-top-right animate-pop-in rounded-md border bg-popover py-1 text-sm shadow-lg">
            <div className="px-3 py-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Remind me</div>
            {presets.map((preset) => (
              <button key={preset.label} onClick={() => pick(preset.date)} className="flex w-full items-center justify-between gap-3 px-3 py-1.5 text-left hover:bg-accent">
                <span>{preset.label}</span>
                <span className="text-xs text-muted-foreground">{preset.detail}</span>
              </button>
            ))}
            <div className="my-1 border-t" />
            <div className="px-3 pb-2 pt-1">
              <label className="mb-1 block text-xs text-muted-foreground">Pick date and time</label>
              <div className="flex gap-2">
                <input type="datetime-local" value={custom} onChange={(e) => setCustom(e.target.value)} className="h-8 min-w-0 flex-1 rounded-md border bg-background px-2 text-xs outline-none focus-visible:border-ring" />
                <button type="button" onClick={() => custom && pick(new Date(custom))} className="h-8 rounded-md border px-2 text-xs text-muted-foreground hover:bg-accent hover:text-foreground">Set</button>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

function ReaderMoreMenu({
  onOpenTab,
  onSaveSender,
  onMoveSpam,
  onDeletePermanent,
  disabled,
  busy,
}: {
  onOpenTab: () => void
  onSaveSender: () => void
  onMoveSpam: () => void
  onDeletePermanent: () => void
  disabled?: boolean
  busy?: string
}) {
  const [open, setOpen] = useState(false)
  const itemClass = "flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-accent disabled:pointer-events-none disabled:opacity-50"
  const pick = (fn: () => void) => {
    fn()
    setOpen(false)
  }
  return (
    <div className="relative">
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        title="More actions"
        className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-50"
      >
        <MoreVertical className="size-4" />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute right-0 top-full z-20 mt-1 w-56 origin-top-right animate-pop-in rounded-md border bg-popover py-1 text-sm shadow-lg">
            <button type="button" disabled={!!busy} onClick={() => pick(onOpenTab)} className={itemClass}>
              <ExternalLink className="size-3.5 text-muted-foreground" />
              <span>Open in new tab</span>
            </button>
            <button type="button" disabled={!!busy} onClick={() => pick(onSaveSender)} className={itemClass}>
              <UserPlus className="size-3.5 text-muted-foreground" />
              <span>{busy === "contact" ? "Saving..." : "Save sender to contacts"}</span>
            </button>
            <div className="my-1 border-t" />
            <button type="button" disabled={!!busy} onClick={() => pick(onMoveSpam)} className={itemClass}>
              <Ban className="size-3.5 text-muted-foreground" />
              <span>{busy === "spam" ? "Moving..." : "Move to Spam"}</span>
            </button>
            <button type="button" disabled={!!busy} onClick={() => pick(onDeletePermanent)} className={cn(itemClass, "text-destructive hover:bg-destructive/10")}>
              <Trash2 className="size-3.5" />
              <span>{busy === "permanent" ? "Deleting..." : "Delete Permanently"}</span>
            </button>
          </div>
        </>
      )}
    </div>
  )
}

function Reader({
  uid,
  folder,
  accountId,
  isSpam,
  ownAddresses,
  folders,
  onBack,
  onReply,
  onReplyDocument,
}: {
  uid: string
  folder: string
  accountId?: string
  isSpam?: boolean
  ownAddresses: string[]
  folders: string[]
  onBack: () => void
  onReply: (p: Prefill) => void
  onReplyDocument: (draft: { title: string; content: string }) => Promise<void>
}) {
  const { data, isLoading } = useEmail(uid, folder, accountId)
  const { markRead, markUnread, archive, remove, deletePermanent, flag, move, markAnswered, clearAnswered, unflagSpam } = useEmailActions(folder, accountId)
  const { create: createNote } = useNoteMutations()
  const { data: attData } = useAttachments(uid, folder, accountId)
  const [aiBusy, setAiBusy] = useState(false)
  const [aiErr, setAiErr] = useState("")
  const [actionBusy, setActionBusy] = useState("")
  const [actionNotice, setActionNotice] = useState("")
  const [actionErr, setActionErr] = useState("")
  const [reminderBusy, setReminderBusy] = useState(false)
  const [reminderNotice, setReminderNotice] = useState("")
  const [reminderErr, setReminderErr] = useState("")
  const [summaryState, setSummaryState] = useState<{ key: string; open: boolean; text?: string; err?: string } | null>(null)
  const [summaryBusyKey, setSummaryBusyKey] = useState("")
  const [docBusy, setDocBusy] = useState(false)
  const [threadView, setThreadView] = useState(true)
  const html = data?.body_html || data?.html
  const text = data?.body_text || data?.body || data?.text
  const from = data?.from_name || data?.from_address || data?.from || data?.from_addr || data?.sender || ""
  const addr = data?.from_address || data?.from_addr || from
  const flagged = !!data?.is_flagged
  const answered = !!data?.is_answered
  const attachments = attData?.attachments || data?.attachments || []
  const bodyText = text || (html ? htmlToText(html) : "")
  const boundaries = data?.boundaries || null
  const threadTurns = useMemo(() => (data?.thread_turns || []).filter((t): t is ThreadTurn => !!t && typeof t.level === "number"), [data?.thread_turns])
  // Offer the stacked thread view only when the parser actually split the
  // message into multiple turns (i.e. there's earlier history to show).
  const hasThread = threadTurns.length >= 2
  const replySourceText = useMemo(() => currentMessageText(text, html, boundaries) || bodyText, [text, html, boundaries, bodyText])
  const subject = data?.subject || ""
  const messageKey = `${folder}:${uid}`
  const cachedSummary = cleanAiReply(data?.cached_summary || "")
  const summaryForMessage = summaryState?.key === messageKey ? summaryState : null
  const summaryText = summaryForMessage?.text ?? cachedSummary
  const summaryOpen = summaryForMessage?.open ?? !!cachedSummary
  const summaryErr = summaryForMessage?.err || ""
  const summaryBusy = summaryBusyKey === messageKey
  const replyMeta = {
    to: addr,
    subject: replySubject(subject),
    inReplyTo: data?.message_id,
    references: data?.message_id ? [data.references, data.message_id].filter(Boolean).join(" ") : data?.references,
    accountId: data?.account_id || accountId,
  }
  const openReply = (draftBody = `\n\n---\n${replySourceText}`) => {
    onReply({ ...replyMeta, body: draftBody })
  }
  const openReplyAll = () => {
    onReply({ ...replyMeta, cc: buildReplyAllCc(data, ownAddresses), body: `\n\n---\n${replySourceText}` })
  }
  const openForward = () => {
    if (!data) return
    onReply({
      to: "",
      subject: forwardSubject(subject),
      body: forwardedBody({ ...data, from }, bodyText),
      accountId: data.account_id || accountId,
    })
  }
  const openStandaloneTab = () => {
    if (typeof window === "undefined") return
    const href = `${window.location.origin}/v2/email#email=${encodeURIComponent(folder)}:${encodeURIComponent(uid)}`
    window.open(href, "_blank", "noopener,noreferrer")
  }
  const saveSender = async () => {
    if (!data || actionBusy) return
    const email = extractEmailAddress(data.from_address || data.from_addr || data.from || addr)
    if (!email) {
      setActionErr("No sender address to save.")
      return
    }
    const name = (data.from_name || from || email.split("@")[0]).replace(/<[^>]+>/g, "").trim()
    setActionBusy("contact"); setActionNotice(""); setActionErr("")
    try {
      const r = await saveEmailSenderContact({ name, email })
      setActionNotice(r.message === "Already exists" ? "Already in contacts." : "Saved sender to contacts.")
    } catch {
      setActionErr("Couldn't save sender to contacts.")
    } finally {
      setActionBusy("")
    }
  }
  const moveToSpam = async () => {
    if (actionBusy) return
    if (!confirm("Move this email to Spam?")) return
    setActionBusy("spam"); setActionNotice(""); setActionErr("")
    try {
      await move.mutateAsync({ uid, dest: "Junk" })
      onBack()
    } catch {
      setActionErr("Couldn't move the email to Spam.")
    } finally {
      setActionBusy("")
    }
  }
  const markNotSpam = async () => {
    if (actionBusy) return
    setActionBusy("not-spam"); setActionNotice(""); setActionErr("")
    try {
      await unflagSpam.mutateAsync(uid)
      setActionNotice("Marked as not spam.")
    } catch {
      setActionErr("Couldn't mark the email as not spam.")
    } finally {
      setActionBusy("")
    }
  }
  const permanentlyDelete = async () => {
    if (actionBusy) return
    if (!confirm(`Permanently delete "${subject || "(no subject)"}"? This cannot be undone.`)) return
    setActionBusy("permanent"); setActionNotice(""); setActionErr("")
    try {
      await deletePermanent.mutateAsync(uid)
      onBack()
    } catch {
      setActionErr("Couldn't permanently delete the email.")
    } finally {
      setActionBusy("")
    }
  }
  const openDocumentReply = async () => {
    if (!data || docBusy) return
    setAiErr("")
    const body = `${quoteOriginal(replySourceText || "", from, data.date)}`
    const content = buildEmailDraft({
      to: addr,
      cc: "",
      bcc: "",
      subject: replyMeta.subject,
      inReplyTo: replyMeta.inReplyTo || "",
      references: replyMeta.references || "",
      sourceUid: uid,
      sourceFolder: folder,
      sourceAccount: data.account_id || accountId || "",
      attachments: [],
      body,
    })
    setDocBusy(true)
    try {
      await onReplyDocument({ title: replyMeta.subject, content })
    } catch {
      setAiErr("Couldn't create the reply document.")
    } finally {
      setDocBusy(false)
    }
  }
  const openAiReply = async () => {
    if (!data || aiBusy) return
    setAiErr("")
    const source = replySourceText.trim()
    if (!source) {
      setAiErr("No email body to draft from.")
      return
    }
    const cached = cleanAiReply(data.cached_ai_reply || "")
    if (cached) {
      onReply({ ...replyMeta, body: `${cached}${quoteOriginal(source, from, data.date)}` })
      return
    }
    setAiBusy(true)
    try {
      const r = await aiReply({
        uid,
        folder,
        to: addr,
        subject: replyMeta.subject,
        original_body: source,
        message_id: data.message_id,
        account_id: data.account_id || accountId,
        fast: shouldUseFastAiReply(subject, source, attachments),
      })
      if (r.success === false || !r.reply) {
        setAiErr(r.error || "AI reply could not be generated.")
        return
      }
      onReply({ ...replyMeta, body: `${cleanAiReply(r.reply)}${quoteOriginal(source, from, data.date)}` })
    } catch {
      setAiErr("AI reply failed.")
    } finally {
      setAiBusy(false)
    }
  }
  const toggleAnswered = async () => {
    if (answered) {
      clearAnswered.mutate(uid)
      return
    }
    try {
      await markAnswered.mutateAsync(uid)
      await markRead.mutateAsync(uid)
    } catch {
      setAiErr("Couldn't update the email.")
    }
  }
  const createReplyReminder = async (dueDate: Date) => {
    if (!data || reminderBusy) return
    setReminderBusy(true)
    setReminderErr("")
    setReminderNotice("")
    try {
      const who = firstNameFromSender(from || addr || "someone")
      const due = localDateTimeValue(dueDate)
      const origin = typeof window !== "undefined" ? window.location.origin : ""
      const link = origin ? `${origin}/v2/email#email=${encodeURIComponent(folder)}:${encodeURIComponent(uid)}` : ""
      await createNote.mutateAsync({
        title: `Reply: ${subject || "(no subject)"}`,
        note_type: "todo",
        items: [{ text: `Reply to ${who}: ${subject || "(no subject)"}`, checked: false }],
        content: link ? `Open email: ${link}` : "Remember to reply to this email.",
        label: "email reminder",
        due_date: due,
      })
      setReminderNotice(`Reminder set for ${dueDate.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}.`)
      if (typeof window !== "undefined" && "Notification" in window && Notification.permission === "default") {
        try { void Notification.requestPermission() } catch { /* ignore */ }
      }
    } catch {
      setReminderErr("Couldn't create the reminder.")
    } finally {
      setReminderBusy(false)
    }
  }
  const toggleSummary = () => {
    setSummaryState((current) => ({
      key: messageKey,
      open: !summaryOpen,
      text: current?.key === messageKey ? current.text : undefined,
      err: "",
    }))
  }
  const generateSummary = async () => {
    if (!data || summaryBusy) return
    setSummaryState((current) => ({
      key: messageKey,
      open: true,
      text: current?.key === messageKey ? current.text : undefined,
      err: "",
    }))
    const source = bodyText.trim()
    if (!source) {
      setSummaryState((current) => ({
        key: messageKey,
        open: true,
        text: current?.key === messageKey ? current.text : undefined,
        err: "No email body to summarize.",
      }))
      return
    }
    setSummaryBusyKey(messageKey)
    try {
      const r = await summarizeEmail({
        uid,
        folder,
        body: source,
        subject,
        from: data.from_name ? `${data.from_name} <${addr}>` : addr,
        message_id: data.message_id,
        account_id: data.account_id || accountId,
      })
      if (r.success === false || !r.summary) {
        setSummaryState((current) => ({
          key: messageKey,
          open: true,
          text: current?.key === messageKey ? current.text : undefined,
          err: r.error || "Summary could not be generated.",
        }))
        return
      }
      setSummaryState({ key: messageKey, open: true, text: cleanAiReply(r.summary), err: "" })
    } catch {
      setSummaryState((current) => ({
        key: messageKey,
        open: true,
        text: current?.key === messageKey ? current.text : undefined,
        err: "Summary failed.",
      }))
    } finally {
      setSummaryBusyKey((current) => current === messageKey ? "" : current)
    }
  }
  const after = (fn: () => void) => { fn(); onBack() }
  return (
    <div className="flex h-full flex-col">
      <header className="flex min-h-13 shrink-0 flex-wrap items-center gap-2 border-b px-3 py-1.5 md:flex-nowrap md:py-0">
        <Button variant="ghost" size="icon" onClick={onBack} title="Back"><ArrowLeft className="size-4" /></Button>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold">{data?.subject || "(no subject)"}</div>
          <div className="truncate text-xs text-muted-foreground">{from}{data?.date ? ` · ${new Date(data.date).toLocaleString()}` : ""}</div>
        </div>
        <div className="flex shrink-0 flex-wrap items-center justify-end gap-0.5">
          <button onClick={() => flag.mutate({ uid, on: !flagged })} title={flagged ? "Unflag" : "Flag"} className={cn("rounded-md p-1.5 hover:bg-accent", flagged ? "text-foreground" : "text-muted-foreground hover:text-foreground")}><Star className={cn("size-4", flagged && "fill-current")} /></button>
          <button onClick={toggleAnswered} title={answered ? "Mark not done" : "Mark done"} className={cn("rounded-md p-1.5 hover:bg-accent", answered ? "text-foreground" : "text-muted-foreground hover:text-foreground")}><CheckCheck className="size-4" /></button>
          {isSpam && (
            <button onClick={markNotSpam} disabled={!!actionBusy} title="Not spam" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-50"><ShieldCheck className="size-4" /></button>
          )}
          <ReminderMenu busy={reminderBusy} onPick={createReplyReminder} />
          <button onClick={toggleSummary} title={summaryOpen ? "Hide summary" : "Summary"} className={cn("rounded-md p-1.5 hover:bg-accent", summaryOpen || summaryText ? "text-foreground" : "text-muted-foreground hover:text-foreground")}><FileText className={cn("size-4", summaryText && "fill-current")} /></button>
          {hasThread && (
            <button onClick={() => setThreadView((v) => !v)} title={threadView ? "Show full message" : "Show thread"} className={cn("rounded-md p-1.5 hover:bg-accent", threadView ? "text-foreground" : "text-muted-foreground hover:text-foreground")}><MessagesSquare className="size-4" /></button>
          )}
          <button onClick={() => openReply()} title="Reply" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"><Reply className="size-4" /></button>
          <button onClick={openReplyAll} disabled={!data} title="Reply all" className="hidden rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-50 md:inline-flex"><ReplyAll className="size-4" /></button>
          <button onClick={openForward} disabled={!data} title="Forward" className="hidden rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-50 md:inline-flex"><Forward className="size-4" /></button>
          <button onClick={openDocumentReply} disabled={docBusy || isLoading} title="Draft reply in Library" className="hidden rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-50 md:inline-flex"><PenSquare className="size-4" /></button>
          <button onClick={openAiReply} disabled={aiBusy || isLoading} title={data?.cached_ai_reply ? "AI reply (cached draft ready)" : "AI reply"} className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-50"><Sparkles className={cn("size-4", data?.cached_ai_reply && "fill-current text-foreground")} /></button>
          <button onClick={() => after(() => markUnread.mutate(uid))} title="Mark unread" className="hidden rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground md:inline-flex"><MailOpen className="size-4" /></button>
          <MoveMenu folders={folders} current={folder} onMove={(dest) => after(() => move.mutate({ uid, dest }))} />
          <button onClick={() => after(() => archive.mutate(uid))} title="Archive" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"><Archive className="size-4" /></button>
          <button onClick={() => { if (confirm("Delete this email?")) after(() => remove.mutate(uid)) }} title="Delete" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-destructive"><Trash2 className="size-4" /></button>
          <ReaderMoreMenu
            disabled={isLoading}
            busy={actionBusy}
            onOpenTab={openStandaloneTab}
            onSaveSender={saveSender}
            onMoveSpam={moveToSpam}
            onDeletePermanent={permanentlyDelete}
          />
        </div>
      </header>
      {(actionNotice || actionErr) && <div className={cn("shrink-0 border-b px-4 py-2 text-xs", actionErr ? "text-destructive" : "text-muted-foreground")}>{actionErr || actionNotice}</div>}
      {(reminderNotice || reminderErr) && <div className={cn("shrink-0 border-b px-4 py-2 text-xs", reminderErr ? "text-destructive" : "text-muted-foreground")}>{reminderErr || reminderNotice}</div>}
      {summaryOpen && (
        <section className="shrink-0 border-b bg-muted/20 px-4 py-3 text-sm">
          <div className="mb-1.5 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            <FileText className="size-3.5" />
            <span>Summary</span>
            <button onClick={() => setSummaryState((current) => ({ key: messageKey, open: false, text: current?.key === messageKey ? current.text : undefined, err: "" }))} title="Hide summary" className="ml-auto rounded-md p-1 text-muted-foreground hover:bg-accent hover:text-foreground"><X className="size-3.5" /></button>
          </div>
          {summaryText ? (
            <p className="whitespace-pre-wrap leading-relaxed text-foreground">{summaryText}</p>
          ) : (
            <div className="flex flex-wrap items-center gap-2 text-muted-foreground">
              <span>No AI summary generated.</span>
              <Button variant="outline" size="sm" disabled={summaryBusy} onClick={generateSummary}>{summaryBusy ? "Generating…" : "Generate now"}</Button>
            </div>
          )}
          {summaryErr && <p className="mt-2 text-xs text-destructive">{summaryErr}</p>}
        </section>
      )}
      {aiErr && <div className="shrink-0 border-b px-4 py-2 text-xs text-destructive">{aiErr}</div>}
      {attachments.length > 0 && (
        <div className="shrink-0 space-y-1.5 border-b px-4 py-3">
          {attachments.map((att) => <AttachmentRow key={att.index} uid={uid} folder={folder} accountId={accountId} att={att} />)}
        </div>
      )}
      {isLoading ? <div className="p-6 text-sm text-muted-foreground">Loading…</div>
        : data?.error ? <div className="p-6 text-sm text-muted-foreground">Couldn't load this message.</div>
        : hasThread && threadView ? <ThreadedEmailView turns={threadTurns} from={from} date={data?.date} />
        : html ? <HtmlEmailFrame html={html} />
        : <FoldedPlainEmailBody text={text} boundaries={boundaries} />}
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
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [to, setTo] = useState(initial?.to || "")
  const [cc, setCc] = useState(initial?.cc || "")
  const [bcc, setBcc] = useState(initial?.bcc || "")
  const [subject, setSubject] = useState(initial?.subject || "")
  const [body, setBody] = useState(initial?.body || "")
  const [attachments, setAttachments] = useState<ComposeUpload[]>([])
  const [minSchedule] = useState(() => localDateTimeValue(new Date(Date.now() + 60 * 1000)))
  const [scheduleAt, setScheduleAt] = useState(() => localDateTimeValue(new Date(Date.now() + 60 * 60 * 1000)))
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

  const discardAndClose = () => {
    for (const att of attachments) {
      void deleteComposeAttachment(att.token).catch(() => undefined)
    }
    setAttachments([])
    onClose()
  }

  const uploadFiles = async (files: FileList | null) => {
    const list = Array.from(files || [])
    if (list.length === 0) return
    setBusy("upload"); setErr("")
    try {
      for (const file of list) {
        const uploaded = await uploadComposeAttachment(file)
        setAttachments((prev) => [...prev, uploaded])
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Couldn't upload attachment")
    } finally {
      setBusy("")
    }
  }

  const removeAttachment = async (att: ComposeUpload) => {
    setAttachments((prev) => prev.filter((item) => item.token !== att.token))
    try {
      await deleteComposeAttachment(att.token)
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Couldn't remove attachment")
    }
  }

  const act = async (kind: "send" | "draft" | "schedule") => {
    if (!to.trim()) { setErr("Recipient required"); return }
    const attachmentTokens = attachments.map((att) => att.token)
    let sendAtIso: string | undefined
    if (kind === "schedule") {
      const parsed = new Date(scheduleAt)
      if (!scheduleAt || Number.isNaN(parsed.getTime()) || parsed.getTime() <= Date.now()) {
        setErr("Choose a future send time")
        return
      }
      sendAtIso = parsed.toISOString()
    }
    setBusy(kind); setErr("")
    try {
      const payload = {
        to,
        cc: cc.trim() || undefined,
        bcc: bcc.trim() || undefined,
        subject,
        body,
        in_reply_to: initial?.inReplyTo,
        references: initial?.references,
        attachments: attachmentTokens.length ? attachmentTokens : undefined,
        account_id: initial?.accountId,
      }
      const r = kind === "send" ? await sendEmail(payload) : kind === "draft" ? await saveDraft(payload) : await scheduleEmail({ ...payload, send_at: sendAtIso || "" })
      if (r && r.success === false) setErr(r.error || "Failed"); else onClose()
    } catch (e) { setErr(e instanceof Error ? e.message : "Failed") } finally { setBusy("") }
  }
  const inp = "h-9 w-full rounded-md border bg-background px-3 text-sm outline-none focus-visible:border-ring"
  return (
    <div className="absolute inset-0 z-10 flex animate-fade-in items-center justify-center bg-black/40 p-2 sm:p-4">
      <div className="flex max-h-[92vh] w-[min(96vw,36rem)] flex-col overflow-y-auto animate-pop-in rounded-xl border bg-popover p-4 shadow-lg">
        <div className="mb-3 flex items-center justify-between"><div className="text-sm font-semibold">New message</div><button onClick={discardAndClose} className="text-muted-foreground hover:text-foreground"><X className="size-4" /></button></div>
        <div className="relative mb-2">
          <input value={to} onChange={(e) => setTo(e.target.value)} onFocus={() => setToFocused(true)} onBlur={() => setToFocused(false)} placeholder="To" className={inp} />
          {toFocused && toQuery.length >= 1 && <ContactSuggest query={toQuery} onPick={pickContact} />}
        </div>
        <input value={cc} onChange={(e) => setCc(e.target.value)} placeholder="Cc" className={cn(inp, "mb-2")} />
        <input value={bcc} onChange={(e) => setBcc(e.target.value)} placeholder="Bcc" className={cn(inp, "mb-2")} />
        <input value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="Subject" className={cn(inp, "mb-2")} />
        <textarea value={body} onChange={(e) => setBody(e.target.value)} placeholder="Write a message…" rows={8} className="mb-3 w-full resize-none rounded-md border bg-background px-3 py-2 text-sm outline-none focus-visible:border-ring" />
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={(e) => {
              void uploadFiles(e.target.files)
              e.target.value = ""
            }}
          />
          <Button variant="outline" size="sm" disabled={!!busy} onClick={() => fileInputRef.current?.click()}>
            <Paperclip className="size-4" />{busy === "upload" ? "Uploading…" : "Attach"}
          </Button>
          <label className="flex w-full min-w-0 flex-1 flex-col gap-2 rounded-md border bg-background px-3 py-1.5 text-xs text-muted-foreground sm:w-auto sm:flex-row sm:items-center">
            <span className="flex shrink-0 items-center gap-2">
              <Clock className="size-4 shrink-0" />
              <span className="shrink-0">Send at</span>
            </span>
            <input
              type="datetime-local"
              min={minSchedule}
              value={scheduleAt}
              onChange={(e) => setScheduleAt(e.target.value)}
              className="min-w-0 flex-1 bg-transparent text-sm text-foreground outline-none"
            />
          </label>
        </div>
        {attachments.length > 0 && (
          <div className="mb-3 flex flex-wrap gap-2">
            {attachments.map((att) => (
              <span key={att.token} className="inline-flex max-w-full items-center gap-1.5 rounded-md border bg-background px-2 py-1 text-xs">
                <Paperclip className="size-3.5 shrink-0 text-muted-foreground" />
                <span className="max-w-48 truncate">{att.filename}</span>
                {att.size ? <span className="shrink-0 text-muted-foreground">{formatSize(att.size)}</span> : null}
                <button type="button" title="Remove attachment" onClick={() => void removeAttachment(att)} className="rounded p-0.5 text-muted-foreground hover:bg-accent hover:text-foreground">
                  <X className="size-3.5" />
                </button>
              </span>
            ))}
          </div>
        )}
        {err && <p className="mb-2 text-xs text-destructive">{err}</p>}
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="outline" size="sm" disabled={!!busy} onClick={() => act("draft")}>{busy === "draft" ? "Saving…" : "Save draft"}</Button>
          <Button variant="outline" size="sm" disabled={!!busy} onClick={() => act("schedule")}><Clock className="size-4" />{busy === "schedule" ? "Scheduling…" : "Schedule send"}</Button>
          <Button size="sm" disabled={!!busy} onClick={() => act("send")}><Send className="size-4" />{busy === "send" ? "Sending…" : "Send"}</Button>
        </div>
      </div>
    </div>
  )
}

function FolderMenu({ folders, current, onPick }: { folders: string[]; current: string; onPick: (f: string) => void }) {
  const [open, setOpen] = useState(false)
  const baseFolders = folders.length ? folders : [current === SCHEDULED_FOLDER ? "INBOX" : current]
  const list = baseFolders.includes(SCHEDULED_FOLDER) ? baseFolders : [...baseFolders, SCHEDULED_FOLDER]
  const CurrentIcon = current === SCHEDULED_FOLDER ? Clock : Inbox
  return (
    <div className="relative">
      <button onClick={() => setOpen((o) => !o)} className="flex items-center gap-1.5 rounded-md px-2 py-1 text-sm font-semibold hover:bg-accent">
        <CurrentIcon className="size-4 text-muted-foreground" />
        <span>{folderLabel(current)}</span>
        <ChevronDown className="size-3.5 text-muted-foreground" />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute left-0 top-full z-20 mt-1 max-h-72 w-56 origin-top-left animate-pop-in overflow-y-auto rounded-md border bg-popover py-1 text-sm shadow-lg">
            {list.map((f) => (
              <button
                key={f}
                onClick={() => { onPick(f); setOpen(false) }}
                className={cn("block w-full truncate px-3 py-1.5 text-left hover:bg-accent", f === current && "font-medium", f === SCHEDULED_FOLDER && "mt-1 border-t pt-2")}
              >
                {folderLabel(f)}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

function accountLabel(account: EmailAccount): string {
  return account.name || account.from_address || account.imap_user || "Account"
}

function AccountStrip({ accounts, current, onPick }: { accounts: EmailAccount[]; current?: string; onPick: (id: string) => void }) {
  const { setDefault } = useEmailAccountMutations()
  if (accounts.length === 0) return null
  return (
    <div className="shrink-0 border-b px-4 py-2">
      <div className="flex gap-1.5 overflow-x-auto">
        {accounts.map((account) => {
          const active = account.id === current
          const label = accountLabel(account)
          return (
            <span key={account.id} className="relative inline-flex shrink-0 items-center">
              <button
                type="button"
                disabled={account.enabled === false}
                onClick={() => onPick(account.id)}
                title={account.from_address || account.imap_user || label}
                className={cn(
                  "max-w-48 truncate rounded-md border py-1 pl-2.5 pr-7 text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-50",
                  active ? "border-foreground bg-accent text-foreground" : "text-muted-foreground hover:bg-accent hover:text-foreground",
                )}
              >
                {label}
              </button>
              <button
                type="button"
                disabled={setDefault.isPending}
                onClick={(e) => { e.stopPropagation(); setDefault.mutate(account.id) }}
                title={account.is_default ? "Default account" : "Set as default"}
                className={cn("absolute right-1.5 rounded-sm p-0.5 text-muted-foreground hover:text-foreground disabled:opacity-50", account.is_default && "text-foreground")}
              >
                <Star className={cn("size-3", account.is_default && "fill-current")} />
              </button>
            </span>
          )
        })}
      </div>
    </div>
  )
}

function EmailFilterPicker({ value, onChange }: { value: EmailListFilter; onChange: (value: EmailListFilter) => void }) {
  const [open, setOpen] = useState(false)
  const current = EMAIL_FILTERS.find((item) => item.value === value) || EMAIL_FILTERS[0]
  const Icon = current.icon
  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        title="Filter mail"
        className="inline-flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
      >
        <Icon className="size-3.5" />
        <span>{current.label}</span>
        <ChevronDown className="size-3.5" />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute left-0 top-full z-20 mt-1 max-h-72 w-48 origin-top-left animate-pop-in overflow-y-auto rounded-md border bg-popover py-1 text-sm shadow-lg">
            {EMAIL_FILTERS.map((item) => {
              const ItemIcon = item.icon
              return (
                <button
                  key={item.value}
                  type="button"
                  onClick={() => { onChange(item.value); setOpen(false) }}
                  className={cn("flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-accent", item.value === value && "font-medium")}
                >
                  <ItemIcon className="size-3.5 text-muted-foreground" />
                  <span>{item.label}</span>
                </button>
              )
            })}
          </div>
        </>
      )}
    </div>
  )
}

function EmailFilterBar({
  filter,
  hasAttachments,
  sender,
  selectMode,
  onFilter,
  onAttachments,
  onSelectMode,
  onClearSender,
  onClearAll,
}: {
  filter: EmailListFilter
  hasAttachments: boolean
  sender: SenderFilter | null
  selectMode: boolean
  onFilter: (value: EmailListFilter) => void
  onAttachments: () => void
  onSelectMode: () => void
  onClearSender: () => void
  onClearAll: () => void
}) {
  const hasActiveFilters = filter !== "all" || hasAttachments || !!sender
  return (
    <div className="mt-2 flex flex-wrap items-center gap-1.5">
      <EmailFilterPicker value={filter} onChange={onFilter} />
      <button
        type="button"
        onClick={onAttachments}
        title={hasAttachments ? "Show all mail" : "Show mail with attachments"}
        className={cn(
          "inline-flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-xs transition-colors",
          hasAttachments ? "border-foreground bg-accent text-foreground" : "text-muted-foreground hover:bg-accent hover:text-foreground",
        )}
      >
        <Paperclip className="size-3.5" />
        <span>Attachments</span>
      </button>
      <button
        type="button"
        onClick={onSelectMode}
        title={selectMode ? "Cancel selection" : "Select mail"}
        className={cn(
          "inline-flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-xs transition-colors",
          selectMode ? "border-foreground bg-accent text-foreground" : "text-muted-foreground hover:bg-accent hover:text-foreground",
        )}
      >
        {selectMode ? <X className="size-3.5" /> : <CheckCheck className="size-3.5" />}
        <span>{selectMode ? "Cancel" : "Select"}</span>
      </button>
      {sender && (
        <button
          type="button"
          onClick={onClearSender}
          title="Clear sender filter"
          className="inline-flex h-8 max-w-full items-center gap-1.5 rounded-md border bg-accent px-2.5 text-xs text-foreground"
        >
          <span className="max-w-56 truncate">From: {sender.label}</span>
          <X className="size-3.5" />
        </button>
      )}
      {hasActiveFilters && (
        <button
          type="button"
          onClick={onClearAll}
          title="Clear filters"
          className="inline-flex h-8 items-center gap-1 rounded-md px-2 text-xs text-muted-foreground hover:bg-accent hover:text-foreground"
        >
          <X className="size-3.5" />
          <span>Clear</span>
        </button>
      )}
    </div>
  )
}

type BulkAction = "done" | "read" | "unread" | "delete"
type EmailListRowAction =
  | "open-tab"
  | "remind-later"
  | "remind-tomorrow"
  | "read-toggle"
  | "favorite-toggle"
  | "done-toggle"
  | "archive"
  | "save-sender"
  | "spam"
  | "trash"
  | "permanent"

function EmailBulkBar({
  selectedCount,
  allSelected,
  busy,
  onToggleAll,
  onAction,
  onCancel,
}: {
  selectedCount: number
  allSelected: boolean
  busy: string
  onToggleAll: () => void
  onAction: (action: BulkAction) => void
  onCancel: () => void
}) {
  const [open, setOpen] = useState(false)
  const disabled = selectedCount === 0 || !!busy
  return (
    <div className="flex shrink-0 flex-wrap items-center gap-2 border-b bg-muted/20 px-4 py-2 text-xs">
      <label className="inline-flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-muted-foreground">
        <input type="checkbox" checked={allSelected} onChange={onToggleAll} disabled={!!busy} className="size-3.5 accent-current" />
        <span>All</span>
      </label>
      <span className="text-muted-foreground">{busy ? `${busy}...` : `${selectedCount} selected`}</span>
      <div className="relative">
        <button
          type="button"
          disabled={disabled}
          onClick={() => setOpen((o) => !o)}
          className="inline-flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-muted-foreground hover:bg-accent hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
        >
          <CheckCheck className="size-3.5" />
          <span>Actions</span>
          <ChevronDown className="size-3.5" />
        </button>
        {open && (
          <>
            <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
            <div className="absolute left-0 top-full z-20 mt-1 w-40 origin-top-left animate-pop-in rounded-md border bg-popover py-1 text-sm shadow-lg">
              <button type="button" onClick={() => { setOpen(false); onAction("done") }} className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-accent"><CheckCheck className="size-3.5 text-muted-foreground" />Done</button>
              <button type="button" onClick={() => { setOpen(false); onAction("read") }} className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-accent"><MailOpen className="size-3.5 text-muted-foreground" />Mark Read</button>
              <button type="button" onClick={() => { setOpen(false); onAction("unread") }} className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-accent"><MailOpen className="size-3.5 text-muted-foreground" />Mark Unread</button>
            </div>
          </>
        )}
      </div>
      <button
        type="button"
        disabled={disabled}
        onClick={() => onAction("delete")}
        className="inline-flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-destructive hover:bg-destructive/10 disabled:cursor-not-allowed disabled:opacity-50"
      >
        <Trash2 className="size-3.5" />
        <span>Delete</span>
      </button>
      <button type="button" onClick={onCancel} disabled={!!busy} title="Cancel selection" className="ml-auto rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-50"><X className="size-3.5" /></button>
    </div>
  )
}

function EmailListRowMenu({
  item,
  busy,
  open,
  onOpenChange,
  onAction,
}: {
  item: EmailListItem
  busy?: string
  open: boolean
  onOpenChange: (open: boolean) => void
  onAction: (item: EmailListItem, action: EmailListRowAction) => void
}) {
  const read = emailIsRead(item)
  const disabled = !!busy
  const itemClass = "flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-accent disabled:pointer-events-none disabled:opacity-50"
  const pick = (action: EmailListRowAction) => {
    onAction(item, action)
    onOpenChange(false)
  }
  return (
    <div className="relative shrink-0" onClick={(e) => e.stopPropagation()}>
      <button
        type="button"
        disabled={disabled}
        onClick={() => onOpenChange(!open)}
        title="Email actions"
        className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-50"
      >
        <MoreVertical className="size-4" />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => onOpenChange(false)} />
          <div className="absolute right-0 top-full z-20 mt-1 w-56 origin-top-right animate-pop-in rounded-md border bg-popover py-1 text-sm shadow-lg">
            <button type="button" disabled={disabled} onClick={() => pick("open-tab")} className={itemClass}>
              <ExternalLink className="size-3.5 text-muted-foreground" />
              <span>Open in new tab</span>
            </button>
            <div className="px-3 py-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">Remind to reply</div>
            <button type="button" disabled={disabled} onClick={() => pick("remind-later")} className={itemClass}>
              <BellPlus className="size-3.5 text-muted-foreground" />
              <span>Later today</span>
            </button>
            <button type="button" disabled={disabled} onClick={() => pick("remind-tomorrow")} className={itemClass}>
              <Clock className="size-3.5 text-muted-foreground" />
              <span>Tomorrow</span>
            </button>
            <div className="my-1 border-t" />
            <button type="button" disabled={disabled} onClick={() => pick("read-toggle")} className={itemClass}>
              <MailOpen className="size-3.5 text-muted-foreground" />
              <span>{read ? "Mark as Unread" : "Mark as Read"}</span>
            </button>
            <button type="button" disabled={disabled} onClick={() => pick("favorite-toggle")} className={itemClass}>
              <Star className={cn("size-3.5 text-muted-foreground", item.is_flagged && "fill-current text-foreground")} />
              <span>{item.is_flagged ? "Unfavorite" : "Favorite"}</span>
            </button>
            <button type="button" disabled={disabled} onClick={() => pick("done-toggle")} className={itemClass}>
              <CheckCheck className="size-3.5 text-muted-foreground" />
              <span>{item.is_answered ? "Mark as Not Done" : "Mark as Done"}</span>
            </button>
            <button type="button" disabled={disabled} onClick={() => pick("archive")} className={itemClass}>
              <Archive className="size-3.5 text-muted-foreground" />
              <span>Move to Archive</span>
            </button>
            <button type="button" disabled={disabled} onClick={() => pick("save-sender")} className={itemClass}>
              <UserPlus className="size-3.5 text-muted-foreground" />
              <span>Save sender to contacts</span>
            </button>
            <div className="my-1 border-t" />
            <button type="button" disabled={disabled} onClick={() => pick("spam")} className={itemClass}>
              <Ban className="size-3.5 text-muted-foreground" />
              <span>Move to Spam</span>
            </button>
            <button type="button" disabled={disabled} onClick={() => pick("trash")} className={itemClass}>
              <Trash2 className="size-3.5 text-muted-foreground" />
              <span>Move to Trash</span>
            </button>
            <button type="button" disabled={disabled} onClick={() => pick("permanent")} className={cn(itemClass, "text-destructive hover:bg-destructive/10")}>
              <Trash2 className="size-3.5" />
              <span>Delete Permanently</span>
            </button>
          </div>
        </>
      )}
    </div>
  )
}

function EmailListRow({
  item,
  folder,
  selectMode,
  selected,
  actionBusy,
  onOpen,
  onSender,
  onToggleSelected,
  onAction,
}: {
  item: EmailListItem
  folder: string
  selectMode: boolean
  selected: boolean
  actionBusy?: string
  onOpen: (uid: string, folder: string) => void
  onSender: (sender: SenderFilter) => void
  onToggleSelected: (uid: string) => void
  onAction: (item: EmailListItem, action: EmailListRowAction) => void
}) {
  const holdTimer = useRef<number | null>(null)
  const holdStart = useRef<{ x: number; y: number } | null>(null)
  const [menuOpen, setMenuOpen] = useState(false)
  const from = item.from_name || item.from_address || item.from || item.from_addr || item.sender || "Unknown"
  const fromAddress = emailSenderAddress(item)
  const unread = !emailIsRead(item)
  const itemFolder = item.folder || folder
  const rowAction = () => {
    if (selectMode) onToggleSelected(item.uid)
    else onOpen(item.uid, itemFolder)
  }
  const cancelHold = () => {
    if (holdTimer.current !== null) {
      window.clearTimeout(holdTimer.current)
      holdTimer.current = null
    }
    holdStart.current = null
  }
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={rowAction}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault()
          rowAction()
        }
      }}
      onPointerDown={(e) => {
        if (selectMode || e.target instanceof Element && e.target.closest("button,input,a")) return
        holdStart.current = { x: e.clientX, y: e.clientY }
        holdTimer.current = window.setTimeout(() => {
          holdTimer.current = null
          setMenuOpen(true)
        }, 500)
      }}
      onPointerMove={(e) => {
        const start = holdStart.current
        if (!start) return
        if (Math.hypot(e.clientX - start.x, e.clientY - start.y) > 10) cancelHold()
      }}
      onPointerUp={cancelHold}
      onPointerCancel={cancelHold}
      className={cn("flex w-full cursor-pointer items-baseline gap-3 px-4 py-3 text-left outline-none hover:bg-accent/50 focus-visible:bg-accent/50", selected && "bg-accent/60")}
    >
      {selectMode && (
        <input
          type="checkbox"
          checked={selected}
          onClick={(e) => e.stopPropagation()}
          onChange={() => onToggleSelected(item.uid)}
          className="mt-0.5 size-3.5 shrink-0 accent-current"
          aria-label={`Select ${item.subject || "email"}`}
        />
      )}
      {fromAddress && !selectMode ? (
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onSender({ address: fromAddress, label: from }) }}
          title={`Show mail from ${from}`}
          className={cn("w-24 shrink-0 truncate text-left text-sm hover:underline sm:w-44", unread ? "font-semibold text-foreground" : "text-muted-foreground")}
        >
          {from}
        </button>
      ) : (
        <div className={cn("w-24 shrink-0 truncate text-sm sm:w-44", unread ? "font-semibold text-foreground" : "text-muted-foreground")}>{from}</div>
      )}
      <div className="min-w-0 flex-1">
        <span className={cn("text-sm", unread ? "font-medium text-foreground" : "text-muted-foreground")}>{item.subject || "(no subject)"}</span>
        {(item.snippet || item.preview) && <span className="ml-2 text-sm text-muted-foreground">- {item.snippet || item.preview}</span>}
      </div>
      {item.is_flagged && <Star className="size-3.5 shrink-0 fill-current text-foreground" />}
      {item.has_attachments && <Paperclip className="size-3.5 shrink-0 text-muted-foreground" />}
      {item.date && <div className="hidden shrink-0 text-xs text-muted-foreground sm:block">{new Date(item.date).toLocaleDateString()}</div>}
      {!selectMode && (
        <EmailListRowMenu item={item} busy={actionBusy} open={menuOpen} onOpenChange={setMenuOpen} onAction={onAction} />
      )}
    </div>
  )
}

function EmailList({
  emails,
  error,
  folder,
  emptyLabel,
  selectMode,
  selectedUids,
  onOpen,
  onSender,
  onToggleSelected,
  onAction,
  actionBusy,
  footer,
}: {
  emails: EmailListItem[]
  error?: string
  folder: string
  emptyLabel: string
  selectMode: boolean
  selectedUids: Set<string>
  onOpen: (uid: string, folder: string) => void
  onSender: (sender: SenderFilter) => void
  onToggleSelected: (uid: string) => void
  onAction: (item: EmailListItem, action: EmailListRowAction) => void
  actionBusy?: string
  footer?: ReactNode
}) {
  return (
    <div className="flex-1 overflow-y-auto">
      {error && <p className="p-4 text-sm text-muted-foreground">No mail account connected (or unavailable).</p>}
      <div className="divide-y">
        {emails.map((m) => {
          const selected = selectedUids.has(m.uid)
          return (
            <EmailListRow
              key={m.uid}
              item={m}
              folder={folder}
              selectMode={selectMode}
              selected={selected}
              actionBusy={actionBusy}
              onOpen={onOpen}
              onSender={onSender}
              onToggleSelected={onToggleSelected}
              onAction={onAction}
            />
          )
        })}
      </div>
      {!error && emails.length === 0 && <p className="p-8 text-center text-sm text-muted-foreground">{emptyLabel}</p>}
      {footer}
    </div>
  )
}

function ScheduledEmailList({
  items,
  error,
  isLoading,
  cancellingId,
  onCancel,
}: {
  items: ScheduledEmail[]
  error?: string
  isLoading: boolean
  cancellingId?: string
  onCancel: (item: ScheduledEmail) => void
}) {
  if (isLoading) return <p className="p-8 text-center text-sm text-muted-foreground">Loading scheduled mail...</p>
  if (error) return <p className="p-8 text-center text-sm text-muted-foreground">Couldn't load scheduled mail.</p>
  if (items.length === 0) return <p className="p-8 text-center text-sm text-muted-foreground">No scheduled emails.</p>
  return (
    <div className="flex-1 overflow-y-auto">
      <div className="divide-y">
        {items.map((item) => {
          const failed = item.status === "failed"
          const subject = item.subject || "(no subject)"
          const to = item.to || "(no recipient)"
          const cancelling = cancellingId === item.id
          return (
            <div key={item.id} className="flex items-start gap-3 px-4 py-3">
              <div className={cn("mt-0.5 rounded-md border p-1.5", failed ? "border-destructive/30 text-destructive" : "text-muted-foreground")}>
                {failed ? <AlertCircle className="size-4" /> : <Clock className="size-4" />}
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <div className="truncate text-sm font-medium">{subject}</div>
                  <span className={cn("rounded border px-1.5 py-0.5 text-[10px] uppercase", failed ? "border-destructive/40 text-destructive" : "text-muted-foreground")}>
                    {failed ? "Failed" : "Pending"}
                  </span>
                </div>
                <div className="mt-1 text-xs text-muted-foreground">To: {to} · Sends {formatScheduledDate(item.send_at)}</div>
                {item.cc && <div className="mt-0.5 text-xs text-muted-foreground">Cc: {item.cc}</div>}
                {item.error && <div className="mt-1 text-xs text-destructive">{item.error}</div>}
              </div>
              <button
                type="button"
                disabled={cancelling}
                onClick={() => onCancel(item)}
                title={failed ? "Remove failed scheduled email" : "Cancel scheduled send"}
                className="shrink-0 rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-50"
              >
                <X className="size-4" />
              </button>
            </div>
          )
        })}
      </div>
    </div>
  )
}

export function EmailRoute() {
  const [folder, setFolder] = useState("INBOX")
  const [accountId, setAccountId] = useState("")
  const [query, setQuery] = useState("")
  const [listFilter, setListFilter] = useState<EmailListFilter>("all")
  const [hasAttachments, setHasAttachments] = useState(false)
  const [senderFilter, setSenderFilter] = useState<SenderFilter | null>(null)
  const [selectMode, setSelectMode] = useState(false)
  const [selectedUids, setSelectedUids] = useState<Set<string>>(() => new Set())
  const [bulkBusy, setBulkBusy] = useState("")
  const [listActionBusy, setListActionBusy] = useState("")
  const [bulkError, setBulkError] = useState("")
  const [listNotice, setListNotice] = useState("")
  const [reader, setReader] = useState<{ uid: string; folder: string } | null>(null)
  const [replyDoc, setReplyDoc] = useState<ReplyDoc | null>(null)
  const [composing, setComposing] = useState(false)
  const [prefill, setPrefill] = useState<Prefill | undefined>(undefined)
  const { create: createDoc } = useDocMutations()
  const { create: createNote } = useNoteMutations()
  const { data: accounts } = useEmailAccounts()
  const accountList = useMemo(() => accounts || [], [accounts])
  const defaultAccount = accountList.find((account) => account.is_default) || accountList[0]
  const activeAccount = accountList.find((account) => account.id === accountId) || defaultAccount
  const activeAccountId = activeAccount?.id || ""
  const ownAddresses = useMemo(() => ownEmailAddresses(accountList), [accountList])
  const scheduledView = folder === SCHEDULED_FOLDER
  const { data: folderData } = useFolders(activeAccountId)
  const {
    data,
    refetch: refetchList,
    isFetching: listFetching,
    hasNextPage,
    isFetchingNextPage,
    fetchNextPage,
  } = useInbox(folder, { accountId: activeAccountId, filter: listFilter, from: senderFilter?.address, hasAttachments, enabled: !scheduledView })
  const inboxEmails = useMemo<EmailListItem[]>(
    () => (data?.pages || []).flatMap((page) => page.emails as EmailListItem[]),
    [data],
  )
  const inboxError = data?.pages?.[0]?.error
  const searching = query.trim().length >= 2
  const {
    data: searchData,
    isFetching: searchFetching,
    refetch: refetchSearch,
    hasNextPage: hasMoreSearch,
    isFetchingNextPage: fetchingMoreSearch,
    fetchNextPage: fetchMoreSearch,
  } = useEmailSearch(query, folder, activeAccountId, !scheduledView)
  const searchEmails = useMemo<EmailListItem[]>(
    () => (searchData?.pages || []).flatMap((page) => page.emails as EmailListItem[]),
    [searchData],
  )
  const searchError = searchData?.pages?.[0]?.error
  const { data: scheduledData, isFetching: scheduledFetching, refetch: refetchScheduled } = useScheduledEmails()
  const cancelScheduled = useCancelScheduledEmail()
  const bulkActions = useEmailActions(folder, activeAccountId)
  const folders = folderData?.folders || []
  const filteredSearchEmails = applySearchFilters(searchEmails, listFilter, hasAttachments, senderFilter)
  const displayedEmails = searching ? filteredSearchEmails : inboxEmails
  const displayedUids = displayedEmails.map((email) => email.uid)
  const selectedVisibleUids = displayedUids.filter((uid) => selectedUids.has(uid))
  const allSelected = displayedUids.length > 0 && selectedVisibleUids.length === displayedUids.length
  const emptyLabel = emailEmptyLabel(folder, listFilter, hasAttachments, senderFilter)
  const clearSelection = () => {
    setSelectMode(false)
    setSelectedUids(new Set())
    setBulkError("")
  }
  const clearFilters = () => {
    setListFilter("all")
    setHasAttachments(false)
    setSenderFilter(null)
    clearSelection()
  }
  const reply = (p: Prefill) => { setPrefill(p); setReplyDoc(null); setReader(null); setComposing(true) }
  const openEmail = (uid: string, f: string) => { setReplyDoc(null); setReader({ uid, folder: f }) }
  // The read endpoint doesn't echo a spam verdict, so derive the "Not spam"
  // affordance from the list row that was opened (its tags/verdict marker) or
  // from a spam-context folder/filter. The looked-up item also survives the
  // open because list pages stay cached while the reader is mounted.
  const readerSpamContext = useMemo(() => {
    if (!reader) return false
    const item = displayedEmails.find((m) => m.uid === reader.uid)
    if (item && (item.is_spam_verdict || emailTags(item).includes("spam"))) return true
    const f = reader.folder.toLowerCase()
    return listFilter === "tag:spam" || f.includes("junk") || f.includes("spam")
  }, [reader, displayedEmails, listFilter])
  useEffect(() => {
    const openHashEmail = () => {
      if (typeof window === "undefined") return
      const parsed = parseEmailHash(window.location.hash)
      if (!parsed) return
      setFolder(parsed.folder)
      setQuery("")
      setSenderFilter(null)
      setReplyDoc(null)
      setReader({ uid: parsed.uid, folder: parsed.folder })
    }
    openHashEmail()
    window.addEventListener("hashchange", openHashEmail)
    return () => window.removeEventListener("hashchange", openHashEmail)
  }, [])
  const createReplyDocument = async (draft: { title: string; content: string }) => {
    const doc = await createDoc.mutateAsync({ title: draft.title, content: draft.content, language: "email" })
    if (doc?.id) setReplyDoc({ id: doc.id, title: draft.title, content: draft.content })
  }
  const pickAccount = (id: string) => {
    setAccountId(id)
    setFolder("INBOX")
    setQuery("")
    clearFilters()
    clearSelection()
    setReader(null)
    setReplyDoc(null)
  }
  const compose = () => { setPrefill({ accountId: activeAccountId || undefined }); setComposing(true) }
  const refreshMail = async () => {
    setListNotice("")
    if (scheduledView) {
      await refetchScheduled()
      return
    }
    if (searching) await refetchSearch()
    await refetchList()
  }
  const cancelScheduledSend = async (item: ScheduledEmail) => {
    const subject = item.subject || "(no subject)"
    const action = item.status === "failed" ? "Remove" : "Cancel"
    if (!confirm(`${action} scheduled email "${subject}"?`)) return
    setListNotice("")
    try {
      await cancelScheduled.mutateAsync(item.id)
      setListNotice(item.status === "failed" ? "Removed failed scheduled email." : "Cancelled scheduled email.")
    } catch {
      setListNotice("Couldn't cancel scheduled email.")
    }
  }
  const clearReminderEmails = async () => {
    if (!confirm("Permanently delete all Odysseus reminder emails?")) return
    setListNotice("")
    try {
      const r = await bulkActions.deleteReminderEmails.mutateAsync({ permanent: true })
      setListNotice(`Deleted ${r.deleted || 0} reminder email${(r.deleted || 0) === 1 ? "" : "s"}.`)
    } catch {
      setListNotice("Couldn't clear reminder emails.")
    }
  }
  const toggleSelectMode = () => {
    setSelectMode((current) => !current)
    setSelectedUids(new Set())
    setBulkError("")
  }
  const pickFilter = (value: EmailListFilter) => {
    setListFilter(value)
    clearSelection()
  }
  const toggleAttachments = () => {
    setHasAttachments((current) => !current)
    clearSelection()
  }
  const pickSender = (sender: SenderFilter) => {
    setSenderFilter(sender)
    clearSelection()
  }
  const toggleSelected = (uid: string) => {
    setSelectedUids((current) => {
      const next = new Set(current)
      if (next.has(uid)) next.delete(uid)
      else next.add(uid)
      return next
    })
  }
  const toggleAll = () => {
    setSelectedUids((current) => {
      if (allSelected) return new Set()
      const next = new Set(current)
      displayedUids.forEach((uid) => next.add(uid))
      return next
    })
  }
  const runBulkAction = async (action: BulkAction) => {
    const uids = selectedVisibleUids
    if (uids.length === 0 || bulkBusy) return
    if (action === "delete" && !confirm(`Delete ${uids.length} selected email${uids.length === 1 ? "" : "s"}?`)) return
    const label = action === "done" ? "Marking done" : action === "read" ? "Marking read" : action === "unread" ? "Marking unread" : "Deleting"
    setBulkBusy(label)
    setBulkError("")
    try {
      for (const uid of uids) {
        if (action === "done") {
          await bulkActions.markAnswered.mutateAsync(uid)
          await bulkActions.markRead.mutateAsync(uid)
        } else if (action === "read") {
          await bulkActions.markRead.mutateAsync(uid)
        } else if (action === "unread") {
          await bulkActions.markUnread.mutateAsync(uid)
        } else {
          await bulkActions.remove.mutateAsync(uid)
        }
      }
      clearSelection()
    } catch {
      setBulkError("Some selected emails could not be updated.")
    } finally {
      setBulkBusy("")
    }
  }
  const createListReminder = async (m: EmailListItem, dueDate: Date) => {
    const itemFolder = m.folder || folder
    const sender = m.from_name || m.from_address || m.from || m.from_addr || m.sender || "someone"
    const who = firstNameFromSender(sender)
    const due = localDateTimeValue(dueDate)
    const origin = typeof window !== "undefined" ? window.location.origin : ""
    const link = origin ? `${origin}/v2/email#email=${encodeURIComponent(itemFolder)}:${encodeURIComponent(m.uid)}` : ""
    await createNote.mutateAsync({
      title: `Reply: ${m.subject || "(no subject)"}`,
      note_type: "todo",
      items: [{ text: `Reply to ${who}: ${m.subject || "(no subject)"}`, checked: false }],
      content: link ? `Open email: ${link}` : "Remember to reply to this email.",
      label: "email reminder",
      due_date: due,
    })
  }
  const runListRowAction = async (m: EmailListItem, action: EmailListRowAction) => {
    const uid = m.uid
    const itemFolder = m.folder || folder
    const subject = m.subject || "(no subject)"
    const senderAddress = extractEmailAddress(m.from_address || m.from_addr || m.from || m.sender)
    const senderName = (m.from_name || m.from || senderAddress.split("@")[0] || "").replace(/<[^>]+>/g, "").trim()
    const busyKey = `${uid}:${action}`
    setListNotice("")
    setBulkError("")
    try {
      switch (action) {
        case "open-tab": {
          if (typeof window !== "undefined") {
            window.open(`${window.location.origin}/v2/email#email=${encodeURIComponent(itemFolder)}:${encodeURIComponent(uid)}`, "_blank", "noopener,noreferrer")
          }
          return
        }
        case "remind-later": {
          setListActionBusy(busyKey)
          await createListReminder(m, emailReminderPresets()[0].date)
          setListNotice("Reminder set.")
          return
        }
        case "remind-tomorrow": {
          setListActionBusy(busyKey)
          await createListReminder(m, emailReminderPresets()[1].date)
          setListNotice("Reminder set for tomorrow.")
          return
        }
        case "read-toggle": {
          setListActionBusy(busyKey)
          if (emailIsRead(m)) await bulkActions.markUnread.mutateAsync(uid)
          else await bulkActions.markRead.mutateAsync(uid)
          return
        }
        case "favorite-toggle": {
          setListActionBusy(busyKey)
          await bulkActions.flag.mutateAsync({ uid, on: !m.is_flagged })
          return
        }
        case "done-toggle": {
          setListActionBusy(busyKey)
          if (m.is_answered) await bulkActions.clearAnswered.mutateAsync(uid)
          else {
            await bulkActions.markAnswered.mutateAsync(uid)
            await bulkActions.markRead.mutateAsync(uid)
          }
          return
        }
        case "archive": {
          setListActionBusy(busyKey)
          await bulkActions.archive.mutateAsync(uid)
          return
        }
        case "save-sender": {
          if (!senderAddress) {
            setListNotice("No sender address to save.")
            return
          }
          setListActionBusy(busyKey)
          const r = await saveEmailSenderContact({ name: senderName, email: senderAddress })
          setListNotice(r.message === "Already exists" ? "Already in contacts." : "Saved sender to contacts.")
          return
        }
        case "spam": {
          if (!confirm(`Move "${subject}" to Spam?`)) return
          setListActionBusy(busyKey)
          await bulkActions.move.mutateAsync({ uid, dest: "Junk" })
          return
        }
        case "trash": {
          if (!confirm(`Move "${subject}" to Trash?`)) return
          setListActionBusy(busyKey)
          await bulkActions.remove.mutateAsync(uid)
          return
        }
        case "permanent": {
          if (!confirm(`Permanently delete "${subject}"? This cannot be undone.`)) return
          setListActionBusy(busyKey)
          await bulkActions.deletePermanent.mutateAsync(uid)
          return
        }
      }
    } catch {
      setListNotice("Couldn't update this email.")
    } finally {
      setListActionBusy("")
    }
  }
  return (
    <div className={cn("relative mx-auto flex h-full w-full flex-col", reader && replyDoc ? "max-w-6xl" : "max-w-3xl")}>
      {composing && <Compose initial={prefill} onClose={() => { setComposing(false); setPrefill(undefined) }} />}
      {reader ? (
        replyDoc ? (
          <div className="flex h-full min-h-0 flex-col md:flex-row">
            <div className="min-h-0 min-w-0 flex-1 border-b md:border-b-0 md:border-r">
              <Reader uid={reader.uid} folder={reader.folder} accountId={activeAccountId} isSpam={readerSpamContext} ownAddresses={ownAddresses} folders={folders} onBack={() => { setReader(null); setReplyDoc(null) }} onReply={reply} onReplyDocument={createReplyDocument} />
            </div>
            <div className="min-h-0 min-w-0 flex-1">
              <EmailDraftEditor key={replyDoc.id} docId={replyDoc.id} title={replyDoc.title} content={replyDoc.content} onClose={() => setReplyDoc(null)} />
            </div>
          </div>
        ) : (
          <Reader uid={reader.uid} folder={reader.folder} accountId={activeAccountId} isSpam={readerSpamContext} ownAddresses={ownAddresses} folders={folders} onBack={() => setReader(null)} onReply={reply} onReplyDocument={createReplyDocument} />
        )
      ) : (
        <>
          <header className="flex h-13 shrink-0 items-center justify-between gap-2 border-b px-4">
            <FolderMenu folders={folders} current={folder} onPick={(f) => { setFolder(f); setQuery(""); setListFilter("all"); setHasAttachments(false); setSenderFilter(null); clearSelection() }} />
            <div className="flex items-center gap-1.5">
              <button
                type="button"
                onClick={refreshMail}
                disabled={scheduledView ? scheduledFetching : (listFetching || searchFetching)}
                title="Refresh mail"
                className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-50"
              >
                <RefreshCw className={cn("size-4", (scheduledView ? scheduledFetching : (listFetching || searchFetching)) && "animate-spin")} />
              </button>
              {listFilter === "reminders" && (
                <button
                  type="button"
                  onClick={clearReminderEmails}
                  disabled={bulkActions.deleteReminderEmails.isPending}
                  title="Permanently delete Odysseus reminder emails"
                  className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-destructive disabled:pointer-events-none disabled:opacity-50"
                >
                  <Trash2 className="size-4" />
                </button>
              )}
              <Button size="sm" onClick={compose}><PenSquare className="size-4" />Compose</Button>
            </div>
          </header>
          <AccountStrip accounts={accountList} current={activeAccountId} onPick={pickAccount} />
          {listNotice && <div className={cn("shrink-0 border-b px-4 py-2 text-xs", listNotice.startsWith("Couldn't") ? "text-destructive" : "text-muted-foreground")}>{listNotice}</div>}
          {scheduledView ? (
            <ScheduledEmailList
              items={scheduledData?.scheduled || []}
              error={scheduledData?.error}
              isLoading={scheduledFetching && !scheduledData}
              cancellingId={cancelScheduled.isPending ? cancelScheduled.variables : undefined}
              onCancel={cancelScheduledSend}
            />
          ) : (
            <>
              <div className="shrink-0 border-b px-4 py-2">
                <div className="relative">
                  <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                  <input
                    value={query}
                    onChange={(e) => { setQuery(e.target.value); clearSelection() }}
                    placeholder="Search mail…"
                    className="h-9 w-full rounded-md border bg-background pl-8 pr-8 text-sm outline-none focus-visible:border-ring"
                  />
                  {query && <button onClick={() => setQuery("")} title="Clear" className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"><X className="size-4" /></button>}
                </div>
                <EmailFilterBar
                  filter={listFilter}
                  hasAttachments={hasAttachments}
                  sender={senderFilter}
                  selectMode={selectMode}
                  onFilter={pickFilter}
                  onAttachments={toggleAttachments}
                  onSelectMode={toggleSelectMode}
                  onClearSender={() => { setSenderFilter(null); clearSelection() }}
                  onClearAll={clearFilters}
                />
              </div>
              {selectMode && (
                <EmailBulkBar
                  selectedCount={selectedVisibleUids.length}
                  allSelected={allSelected}
                  busy={bulkBusy}
                  onToggleAll={toggleAll}
                  onAction={runBulkAction}
                  onCancel={clearSelection}
                />
              )}
              {bulkError && <div className="shrink-0 border-b px-4 py-2 text-xs text-destructive">{bulkError}</div>}
              {searching ? (
                searchFetching && !searchData ? <p className="p-8 text-center text-sm text-muted-foreground">Searching…</p>
                  : <EmailList emails={filteredSearchEmails} error={searchError} folder={folder} emptyLabel={filteredSearchEmails.length === 0 && (listFilter !== "all" || hasAttachments || senderFilter) ? emptyLabel : "No matches."} selectMode={selectMode} selectedUids={selectedUids} actionBusy={listActionBusy} onOpen={openEmail} onSender={pickSender} onToggleSelected={toggleSelected} onAction={runListRowAction}
                      footer={hasMoreSearch ? (
                        <div className="p-3 text-center">
                          <Button variant="outline" size="sm" disabled={fetchingMoreSearch} onClick={() => { void fetchMoreSearch() }}>
                            {fetchingMoreSearch ? "Loading…" : "Load more"}
                          </Button>
                        </div>
                      ) : undefined} />
              ) : (
                <EmailList
                  emails={inboxEmails}
                  error={inboxError}
                  folder={folder}
                  emptyLabel={emptyLabel}
                  selectMode={selectMode}
                  selectedUids={selectedUids}
                  actionBusy={listActionBusy}
                  onOpen={openEmail}
                  onSender={pickSender}
                  onToggleSelected={toggleSelected}
                  onAction={runListRowAction}
                  footer={hasNextPage ? (
                    <div className="p-3 text-center">
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={isFetchingNextPage}
                        onClick={() => { void fetchNextPage() }}
                      >
                        {isFetchingNextPage ? "Loading…" : "Load more"}
                      </Button>
                    </div>
                  ) : undefined}
                />
              )}
            </>
          )}
        </>
      )}
    </div>
  )
}
