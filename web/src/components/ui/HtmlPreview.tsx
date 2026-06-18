import type { RenderLang } from "@/lib/artifact"
import { cn } from "@/lib/utils"

// Sandboxed iframe preview for renderable markup, rendered via `srcDoc` (inline
// content). srcDoc is used rather than a `src` URL because (a) user artifacts
// are untrusted — an opaque-origin sandbox (no allow-same-origin) keeps injected
// scripts away from our DOM/cookies, and (b) backend-rendered pages (the deep-
// research Visual Report) set `frame-ancestors 'none'`, so they can't be framed
// by URL at all; inlining the fetched HTML sidesteps that.
// Drop any leading non-markup noise before the first markup token. Some saved
// docs carry a leftover `title\nlanguage\n` fence header ahead of `<!DOCTYPE>`,
// which derails the parser and renders blank.
function stripToMarkup(s: string): string {
  const m = /<!doctype html|<html[\s>]|<svg[\s>]|<\?xml/i.exec(s)
  return m && m.index > 0 ? s.slice(m.index) : s
}

// FNV-1a hash of the rendered HTML — used as the iframe key so it remounts
// whenever content changes (setting srcDoc on a live iframe doesn't reliably
// re-render), without the collisions a length+prefix key would have.
function hashHtml(s: string): number {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619) }
  return h >>> 0
}

export function HtmlPreview({
  content, renderLang, title, className,
}: { content?: string; renderLang?: RenderLang; title?: string; className?: string }) {
  const clean = stripToMarkup(content || "")
  const html = renderLang === "svg"
    ? `<!doctype html><html><body style="margin:0;display:grid;place-items:center;min-height:100vh;background:#fff">${clean}</body></html>`
    : clean
  return (
    <iframe
      // Remount when the content changes: setting `srcDoc` on an already-mounted
      // iframe doesn't reliably re-render it (e.g. when the editor seeds content
      // after the iframe first mounted empty), leaving a blank preview.
      key={hashHtml(html)}
      title={title || "Preview"}
      sandbox="allow-scripts allow-modals allow-forms allow-popups"
      srcDoc={html}
      className={cn("min-h-0 w-full flex-1 border-0 bg-white", className)}
    />
  )
}
