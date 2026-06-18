// Client-side parser for the agent's `create_document` fence.
//
// The agent streams a document as a fenced block (mirrors the legacy chat.js
// fence fallback and the backend `create_document` tool format in
// src/agent_tools/document_tools.py):
//
//   ```create_document
//   <title>
//   <language?>          (optional — only if it's a known language token)
//   <content…>
//   ```
//
// We detect it live in the assistant's text so the document opens in the side
// panel (with an HTML/SVG preview) and the raw code is stripped from the chat
// bubble — instead of dumping the fence as plaintext.

// Keep in sync with _KNOWN_LANGS in src/agent_tools/document_tools.py.
const KNOWN_LANGS = new Set([
  "python", "javascript", "typescript", "html", "css", "markdown", "json",
  "yaml", "bash", "sql", "rust", "go", "java", "c", "cpp", "xml", "toml",
  "ini", "ruby", "php", "csv", "email", "text", "plain", "svg",
])

// Languages we render as a live preview (sandboxed iframe) rather than code.
const RENDERABLE = new Set(["html", "htm", "svg", "xml"])
export function isRenderable(language?: string): boolean {
  return !!language && RENDERABLE.has(language.toLowerCase())
}

export type RenderLang = "html" | "svg" | ""
// Decide whether a document should render as a live HTML/SVG preview. `language`
// is often empty (a doc titled "page.html" may carry no language), so also infer
// from the title extension and a content sniff.
export function detectRenderLang(content?: string, language?: string, title?: string): RenderLang {
  const lang = language?.toLowerCase()
  if (isRenderable(lang)) return lang === "svg" ? "svg" : "html"
  const t = title || ""
  if (/\.svg$/i.test(t)) return "svg"
  if (/\.(html?|xml)$/i.test(t)) return "html"
  const sniff = (content || "").trimStart().slice(0, 300).toLowerCase()
  if (/^(<!doctype html|<html[\s>]|<\?xml)/.test(sniff)) return "html"
  if (sniff.startsWith("<svg")) return "svg"
  return ""
}

export interface Artifact {
  title: string
  language?: string
  content: string
  closed: boolean // true once the closing ``` has streamed in
}

const FENCE = "```create_document"

// Parse the FIRST create_document fence out of `raw`. Returns the chat text to
// display (fence removed) and the extracted artifact, if any.
export function parseArtifact(raw: string): { display: string; artifact?: Artifact } {
  const idx = raw.indexOf(FENCE)
  if (idx < 0) return { display: raw }
  const headerNl = raw.indexOf("\n", idx + FENCE.length)
  if (headerNl < 0) return { display: raw.slice(0, idx).trimEnd() } // header still streaming

  const pre = raw.slice(0, idx).trimEnd()
  let rest = raw.slice(headerNl + 1)

  let closed = false
  let post = ""
  const closeIdx = rest.indexOf("\n```")
  if (closeIdx >= 0) {
    closed = true
    post = rest.slice(closeIdx + 4).trimStart()
    rest = rest.slice(0, closeIdx)
  }

  const lines = rest.split("\n")
  const title = (lines.shift() || "Untitled").trim() || "Untitled"
  let language: string | undefined
  if (lines.length) {
    const cand = lines[0].trim().toLowerCase()
    if (cand && cand.length < 20 && !cand.includes(" ") && KNOWN_LANGS.has(cand)) {
      language = cand
      lines.shift()
    }
  }
  const content = lines.join("\n")
  const display = [pre, post].filter(Boolean).join("\n\n")
  return { display, artifact: { title, language, content, closed } }
}
