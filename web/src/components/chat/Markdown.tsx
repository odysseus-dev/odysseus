import { Children, isValidElement, memo, useMemo, useRef, useState, type ComponentPropsWithoutRef, type ReactElement, type ReactNode } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import remarkMath from "remark-math"
import rehypeHighlight from "rehype-highlight"
import rehypeKatex from "rehype-katex"
import { Copy, Check, Pencil, Play, X, Loader2 } from "lucide-react"
import { ErrorBoundary } from "@/components/ErrorBoundary"
import { splitFinalized } from "@/lib/streamMarkdown"

// A fenced code block with a header bar (language + copy). rehypeHighlight has
// already added the `language-x` / hljs classes to the inner <code> before this
// component renders, so we only decorate; copy reads the rendered text.
function nodeText(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node)
  if (Array.isArray(node)) return node.map(nodeText).join("")
  if (isValidElement<{ children?: ReactNode }>(node)) return nodeText(node.props.children)
  return ""
}

function encodeBase64(text: string) {
  const bytes = new TextEncoder().encode(text)
  let binary = ""
  bytes.forEach((byte) => { binary += String.fromCharCode(byte) })
  return btoa(binary)
}

async function executeCode(code: string, lang: string): Promise<{ text: string; error?: boolean }> {
  const normalized = lang.toLowerCase()
  if (normalized === "javascript" || normalized === "js") {
    return new Promise((resolve) => {
      const iframe = document.createElement("iframe")
      iframe.hidden = true
      iframe.sandbox.add("allow-scripts")
      document.body.appendChild(iframe)
      const timer = window.setTimeout(() => finish({ text: "Execution timed out (10 s)", error: true }), 10000)
      const finish = (result: { text: string; error?: boolean }) => {
        window.clearTimeout(timer); window.removeEventListener("message", receive); iframe.remove(); resolve(result)
      }
      const receive = (event: MessageEvent) => {
        if (event.source !== iframe.contentWindow || event.data?.source !== "odysseus-code-runner") return
        const error = typeof event.data.error === "string" ? event.data.error : ""
        const logs = Array.isArray(event.data.logs) ? event.data.logs.join("\n") : ""
        finish({ text: error || logs || "(no output)", error: !!error })
      }
      window.addEventListener("message", receive)
      const safe = code.replace(/<\/script>/gi, "<\\/script>")
      iframe.srcdoc = `<!doctype html><script>const logs=[];['log','warn','error'].forEach(k=>console[k]=(...a)=>logs.push(a.map(v=>{try{return typeof v==='object'?JSON.stringify(v):String(v)}catch{return String(v)}}).join(' ')));try{${safe}\nparent.postMessage({source:'odysseus-code-runner',logs},'*')}catch(error){parent.postMessage({source:'odysseus-code-runner',error:String(error),logs},'*')}</script>`
    })
  }
  if (normalized === "html") {
    const win = window.open("", "_blank", "width=800,height=600,menubar=no,toolbar=no")
    if (!win) return { text: "Popup blocked — allow popups to preview HTML.", error: true }
    try { win.opener = null } catch { /* browser may make opener readonly */ }
    win.document.open(); win.document.write(code); win.document.close()
    return { text: "Opened in a new window." }
  }
  if (["python", "py", "bash", "sh", "shell", "zsh"].includes(normalized)) {
    const b64 = encodeBase64(code)
    const command = normalized === "python" || normalized === "py"
      ? `python3 -c "import base64; exec(base64.b64decode('${b64}').decode('utf-8'))"`
      : `python3 -c "import base64, subprocess, sys; sys.exit(subprocess.run(['bash','-c',base64.b64decode('${b64}').decode('utf-8')]).returncode)"`
    const response = await fetch("/api/shell/exec", {
      method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command }),
    })
    if (!response.ok) return { text: `Execution failed (${response.status}).`, error: true }
    const result = await response.json() as { stdout?: string; stderr?: string; exit_code?: number }
    const text = [result.stderr, result.stdout].filter(Boolean).join("\n").trim()
    return { text: text || `(no output)${result.exit_code ? ` — exit code ${result.exit_code}` : ""}`, error: !!result.stderr || !!result.exit_code }
  }
  return { text: `Running ${lang || "this language"} isn't supported yet.`, error: true }
}

function CodeBlock({ node: _node, children, ...props }: ComponentPropsWithoutRef<"pre"> & { node?: unknown }) {
  void _node
  const ref = useRef<HTMLPreElement>(null)
  const [copied, setCopied] = useState(false)
  const [editing, setEditing] = useState(false)
  const [running, setRunning] = useState(false)
  const [draft, setDraft] = useState(() => nodeText(Children.toArray(children)).replace(/\n$/, ""))
  const [output, setOutput] = useState<{ text: string; error?: boolean } | null>(null)
  const child = Array.isArray(children) ? children[0] : children
  const cls = (child as ReactElement<{ className?: string }> | undefined)?.props?.className || ""
  const lang = /language-([\w+#.-]+)/.exec(cls)?.[1] || ""
  const copy = () => {
    const text = editing ? draft : (ref.current?.innerText ?? draft)
    navigator.clipboard.writeText(text).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500) }).catch(() => { /* clipboard blocked */ })
  }
  const runnable = ["javascript", "js", "html", "python", "py", "bash", "sh", "shell", "zsh"].includes(lang.toLowerCase())
  const run = async () => {
    setRunning(true); setOutput(null)
    try { setOutput(await executeCode(draft, lang)) }
    catch (error) { setOutput({ text: error instanceof Error ? error.message : "Execution failed.", error: true }) }
    finally { setRunning(false) }
  }
  return (
    <div className="code-block group">
      <div className="code-block-bar">
        <span className="code-lang">{lang || "text"}</span>
        <span className="flex items-center gap-1">
          <button onClick={() => setEditing((value) => !value)} className="code-copy" title={editing ? "Close editor" : "Edit code"}>
            {editing ? <><X className="size-3" /> Close</> : <><Pencil className="size-3" /> Edit</>}
          </button>
          {runnable && <button onClick={run} disabled={running} className="code-copy" title="Run code">
            {running ? <><Loader2 className="size-3 animate-spin" /> Running</> : <><Play className="size-3" /> Run</>}
          </button>}
          <button onClick={copy} className="code-copy" title="Copy code">
            {copied ? <><Check className="size-3" /> Copied</> : <><Copy className="size-3" /> Copy</>}
          </button>
        </span>
      </div>
      {editing
        ? <textarea value={draft} onChange={(event) => setDraft(event.target.value)} spellCheck={false} className="min-h-40 w-full resize-y border-0 bg-muted/40 p-4 font-mono text-sm outline-none" />
        : <pre ref={ref} {...props}>{draft === nodeText(Children.toArray(children)).replace(/\n$/, "") ? children : <code className={cls}>{draft}</code>}</pre>}
      {output && <div className="relative border-t bg-muted/30 p-3">
        <div className="mb-1 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">Output</div>
        <pre className={output.error ? "whitespace-pre-wrap text-xs text-destructive" : "whitespace-pre-wrap text-xs"}>{output.text}</pre>
      </div>}
    </div>
  )
}

const COMPONENTS = { pre: CodeBlock }
const REMARK = [remarkGfm, remarkMath]
const REHYPE = [rehypeHighlight, rehypeKatex]

// One parsed markdown block. memo'd on `children` so an identical string (e.g. a
// frozen streaming prefix) is NOT re-parsed or re-highlighted on every token.
const Block = memo(function Block({ children }: { children: string }) {
  return <ReactMarkdown remarkPlugins={REMARK} rehypePlugins={REHYPE} components={COMPONENTS}>{children}</ReactMarkdown>
})

function Fallback({ text }: { text: string }) {
  return <pre className="whitespace-pre-wrap break-words text-sm">{text}</pre>
}

export const Markdown = memo(function Markdown({ children }: { children: string }) {
  return (
    <div className="prose-chat">
      <ErrorBoundary fallback={<Fallback text={children} />}><Block>{children}</Block></ErrorBoundary>
    </div>
  )
})

// Streaming-aware markdown: freezes the settled leading blocks (parsed/highlighted
// once) and only re-renders the growing tail each token — this is what makes the
// thread flow smoothly instead of flickering as the whole message re-parses.
export const StreamingMarkdown = memo(function StreamingMarkdown({ content, streaming }: { content: string; streaming: boolean }) {
  const cut = useMemo(() => (streaming ? splitFinalized(content) : content.length), [content, streaming])
  const prefix = cut > 0 ? content.slice(0, cut) : ""
  const tail = cut < content.length ? content.slice(cut) : ""
  return (
    <div className="prose-chat">
      {prefix && <ErrorBoundary fallback={<Fallback text={prefix} />}><Block>{prefix}</Block></ErrorBoundary>}
      {/* Keyed by length so a transient malformed-LaTeX/markdown state in the
          volatile tail recovers as more tokens arrive — only the small tail
          remounts, never the frozen prefix. */}
      {tail && <ErrorBoundary key={tail.length} fallback={<Fallback text={tail} />}><Block>{tail}</Block></ErrorBoundary>}
    </div>
  )
})
