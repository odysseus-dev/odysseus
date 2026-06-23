import { useCallback, type RefObject } from "react"
import { Bold, Code, Code2, Heading1, Heading2, Italic, Link2, List, ListOrdered } from "lucide-react"
import { cn } from "@/lib/utils"

// A pragmatic markdown-source toolbar: it wraps/inserts markdown syntax around
// the current selection in a plain <textarea>. This is NOT a WYSIWYG editor —
// the textarea stays the source of truth, the toolbar just saves keystrokes.
//
// The parent owns the content state; we mutate the value through `onChange`
// (so React stays in sync) and then restore focus + selection imperatively so
// the caret lands where the user expects after each action.

export interface MarkdownToolbarProps {
  textareaRef: RefObject<HTMLTextAreaElement | null>
  value: string
  onChange: (next: string) => void
  disabled?: boolean
  className?: string
}

type Edit = { value: string; selStart: number; selEnd: number }

// Wrap the selection in `before`/`after`. If the selection is already wrapped,
// unwrap it (toggle). With no selection, insert the markers and drop the caret
// between them (or over `placeholder` if given) so the user can type right away.
function wrapSelection(value: string, start: number, end: number, before: string, after: string, placeholder = ""): Edit {
  const selected = value.slice(start, end)
  const pre = value.slice(0, start)
  const post = value.slice(end)
  // Toggle off when the markers already hug the selection.
  if (
    selected &&
    pre.endsWith(before) &&
    post.startsWith(after)
  ) {
    const next = pre.slice(0, pre.length - before.length) + selected + post.slice(after.length)
    const s = start - before.length
    return { value: next, selStart: s, selEnd: s + selected.length }
  }
  const body = selected || placeholder
  const next = pre + before + body + after + post
  const s = start + before.length
  return { value: next, selStart: s, selEnd: s + body.length }
}

// Prefix each selected line (or the current line) with `marker`. Toggles off
// when every targeted line already carries the marker. `ordered` renumbers.
function prefixLines(value: string, start: number, end: number, marker: string, ordered = false): Edit {
  const lineStart = value.lastIndexOf("\n", start - 1) + 1
  let lineEnd = value.indexOf("\n", end)
  if (lineEnd === -1) lineEnd = value.length
  const block = value.slice(lineStart, lineEnd)
  const lines = block.split("\n")
  const re = ordered ? /^\d+\.\s+/ : new RegExp(`^${marker.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`)
  const allMarked = lines.every((ln) => ln.trim() === "" || re.test(ln))
  const next = lines
    .map((ln, i) => {
      if (ln.trim() === "") return ln
      if (allMarked) return ln.replace(re, "")
      return ordered ? `${i + 1}. ${ln}` : `${marker}${ln}`
    })
    .join("\n")
  const delta = next.length - block.length
  return {
    value: value.slice(0, lineStart) + next + value.slice(lineEnd),
    selStart: lineStart,
    selEnd: lineEnd + delta,
  }
}

function makeLink(value: string, start: number, end: number): Edit {
  const selected = value.slice(start, end)
  const text = selected || "text"
  const snippet = `[${text}](url)`
  const next = value.slice(0, start) + snippet + value.slice(end)
  // Select the "url" placeholder so the user can paste straight away.
  const urlStart = start + text.length + 3 // "[" + text + "]("
  return { value: next, selStart: urlStart, selEnd: urlStart + 3 }
}

function makeCodeBlock(value: string, start: number, end: number): Edit {
  const selected = value.slice(start, end)
  const pre = value.slice(0, start)
  const post = value.slice(end)
  const needsLeadingNl = pre && !pre.endsWith("\n") ? "\n" : ""
  const needsTrailingNl = post && !post.startsWith("\n") ? "\n" : ""
  const body = selected || "code"
  const block = `${needsLeadingNl}\`\`\`\n${body}\n\`\`\`${needsTrailingNl}`
  const next = pre + block + post
  const s = start + needsLeadingNl.length + 4 // marker + newline
  return { value: next, selStart: s, selEnd: s + body.length }
}

const btn = "inline-flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-40 disabled:pointer-events-none"

export function MarkdownToolbar({ textareaRef, value, onChange, disabled, className }: MarkdownToolbarProps) {
  const apply = useCallback(
    (fn: (v: string, s: number, e: number) => Edit) => {
      const ta = textareaRef.current
      if (!ta) return
      const start = ta.selectionStart ?? value.length
      const end = ta.selectionEnd ?? start
      const edit = fn(value, start, end)
      onChange(edit.value)
      // Restore focus + selection after React commits the new value.
      requestAnimationFrame(() => {
        const node = textareaRef.current
        if (!node) return
        node.focus()
        node.setSelectionRange(edit.selStart, edit.selEnd)
      })
    },
    [textareaRef, value, onChange],
  )

  return (
    <div className={cn("flex shrink-0 flex-wrap items-center gap-0.5 border-b px-2 py-1", className)} role="toolbar" aria-label="Formatting">
      <button type="button" className={btn} disabled={disabled} title="Bold" aria-label="Bold" onClick={() => apply((v, s, e) => wrapSelection(v, s, e, "**", "**", "bold text"))}><Bold className="size-4" /></button>
      <button type="button" className={btn} disabled={disabled} title="Italic" aria-label="Italic" onClick={() => apply((v, s, e) => wrapSelection(v, s, e, "*", "*", "italic text"))}><Italic className="size-4" /></button>
      <button type="button" className={btn} disabled={disabled} title="Inline code" aria-label="Inline code" onClick={() => apply((v, s, e) => wrapSelection(v, s, e, "`", "`", "code"))}><Code className="size-4" /></button>
      <span className="mx-1 h-5 w-px bg-border" aria-hidden />
      <button type="button" className={btn} disabled={disabled} title="Heading 1" aria-label="Heading 1" onClick={() => apply((v, s, e) => prefixLines(v, s, e, "# "))}><Heading1 className="size-4" /></button>
      <button type="button" className={btn} disabled={disabled} title="Heading 2" aria-label="Heading 2" onClick={() => apply((v, s, e) => prefixLines(v, s, e, "## "))}><Heading2 className="size-4" /></button>
      <span className="mx-1 h-5 w-px bg-border" aria-hidden />
      <button type="button" className={btn} disabled={disabled} title="Bulleted list" aria-label="Bulleted list" onClick={() => apply((v, s, e) => prefixLines(v, s, e, "- "))}><List className="size-4" /></button>
      <button type="button" className={btn} disabled={disabled} title="Numbered list" aria-label="Numbered list" onClick={() => apply((v, s, e) => prefixLines(v, s, e, "1. ", true))}><ListOrdered className="size-4" /></button>
      <span className="mx-1 h-5 w-px bg-border" aria-hidden />
      <button type="button" className={btn} disabled={disabled} title="Link" aria-label="Link" onClick={() => apply(makeLink)}><Link2 className="size-4" /></button>
      <button type="button" className={btn} disabled={disabled} title="Code block" aria-label="Code block" onClick={() => apply(makeCodeBlock)}><Code2 className="size-4" /></button>
    </div>
  )
}
