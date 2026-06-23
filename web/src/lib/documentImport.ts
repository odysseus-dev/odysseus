export interface ImportedDocumentInput {
  title: string
  language: string | null
  content: string
}

interface XlsxWorkbook {
  SheetNames: string[]
  Sheets: Record<string, unknown>
}

interface XlsxGlobal {
  read: (data: ArrayBuffer, options: { type: "array" }) => XlsxWorkbook
  utils: {
    sheet_to_csv: (sheet: unknown) => string
  }
}

interface MammothGlobal {
  convertToHtml: (input: { arrayBuffer: ArrayBuffer }) => Promise<{ value: string }>
}

declare global {
  interface Window {
    XLSX?: XlsxGlobal
    mammoth?: MammothGlobal
  }
}

const EXT_TO_LANGUAGE: Record<string, string | null> = {
  ".py": "python",
  ".js": "javascript",
  ".jsx": "javascript",
  ".ts": "typescript",
  ".tsx": "typescript",
  ".html": "html",
  ".htm": "html",
  ".vue": "html",
  ".svelte": "html",
  ".css": "css",
  ".scss": "css",
  ".sass": "css",
  ".less": "css",
  ".md": "markdown",
  ".markdown": "markdown",
  ".json": "json",
  ".yml": "yaml",
  ".yaml": "yaml",
  ".csv": "csv",
  ".tsv": "csv",
  ".sh": "bash",
  ".bash": "bash",
  ".sql": "sql",
  ".xml": "xml",
  ".toml": "toml",
  ".ini": "ini",
  ".cfg": "ini",
  ".conf": "ini",
  ".env": null,
  ".txt": null,
  ".log": null,
  ".rs": "rust",
  ".go": "go",
  ".java": "java",
  ".c": "c",
  ".h": "c",
  ".cpp": "cpp",
  ".hpp": "cpp",
  ".rb": "ruby",
  ".php": "php",
  ".xlsx": "csv",
  ".xls": "csv",
  ".ods": "csv",
  ".docx": "markdown",
  ".doc": "markdown",
}

let xlsxReady: Promise<void> | null = null
let mammothReady: Promise<void> | null = null

function extensionFor(filename: string): string {
  const dot = filename.lastIndexOf(".")
  return dot >= 0 ? filename.slice(dot).toLowerCase() : ""
}

export function titleFromImportedFilename(filename: string): string {
  const base = filename.replace(/^.*[\\/]/, "")
  const dot = base.lastIndexOf(".")
  return (dot > 0 ? base.slice(0, dot) : base).trim() || "Imported document"
}

export function inferImportedLanguage(filename: string): string | null {
  const ext = extensionFor(filename)
  return Object.prototype.hasOwnProperty.call(EXT_TO_LANGUAGE, ext) ? EXT_TO_LANGUAGE[ext] : null
}

function loadScriptOnce(src: string, ready: () => boolean): Promise<void> {
  if (ready()) return Promise.resolve()
  return new Promise((resolve, reject) => {
    const existing = document.querySelector<HTMLScriptElement>(`script[src="${src}"]`)
    if (existing) {
      existing.addEventListener("load", () => resolve(), { once: true })
      existing.addEventListener("error", () => reject(new Error(`Failed to load ${src}`)), { once: true })
      return
    }
    const script = document.createElement("script")
    script.src = src
    script.onload = () => resolve()
    script.onerror = () => reject(new Error(`Failed to load ${src}`))
    document.head.appendChild(script)
  })
}

async function ensureXlsx() {
  xlsxReady ||= loadScriptOnce("/static/lib/xlsx.full.min.js", () => !!window.XLSX)
  await xlsxReady
  if (!window.XLSX) throw new Error("Spreadsheet converter did not initialize")
}

async function ensureMammoth() {
  mammothReady ||= loadScriptOnce("/static/lib/mammoth.browser.min.js", () => !!window.mammoth)
  await mammothReady
  if (!window.mammoth) throw new Error("DOCX converter did not initialize")
}

export function htmlToMarkdown(html: string): string {
  const doc = new DOMParser().parseFromString(html, "text/html")
  let markdown = ""

  const walkChildren = (node: Node) => {
    node.childNodes.forEach((child) => walk(child))
  }
  const convertTable = (table: Element) => {
    table.querySelectorAll("tr").forEach((row, index) => {
      const cells = Array.from(row.querySelectorAll("th, td")).map((cell) => cell.textContent?.trim() || "")
      markdown += `| ${cells.join(" | ")} |\n`
      if (index === 0) markdown += `| ${cells.map(() => "---").join(" | ")} |\n`
    })
  }
  const walk = (node: Node) => {
    if (node.nodeType === Node.TEXT_NODE) {
      markdown += node.textContent || ""
      return
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return
    const el = node as Element
    const tag = el.tagName.toLowerCase()
    if (tag === "h1") { markdown += "\n# "; walkChildren(el); markdown += "\n"; return }
    if (tag === "h2") { markdown += "\n## "; walkChildren(el); markdown += "\n"; return }
    if (tag === "h3") { markdown += "\n### "; walkChildren(el); markdown += "\n"; return }
    if (tag === "h4") { markdown += "\n#### "; walkChildren(el); markdown += "\n"; return }
    if (tag === "strong" || tag === "b") { markdown += "**"; walkChildren(el); markdown += "**"; return }
    if (tag === "em" || tag === "i") { markdown += "*"; walkChildren(el); markdown += "*"; return }
    if (tag === "a") { markdown += "["; walkChildren(el); markdown += `](${el.getAttribute("href") || ""})`; return }
    if (tag === "br") { markdown += "\n"; return }
    if (tag === "p") { markdown += "\n"; walkChildren(el); markdown += "\n"; return }
    if (tag === "ul" || tag === "ol") { markdown += "\n"; walkChildren(el); return }
    if (tag === "li") {
      const parent = el.parentElement?.tagName.toLowerCase()
      if (parent === "ol") markdown += `${Array.from(el.parentElement?.children || []).indexOf(el) + 1}. `
      else markdown += "- "
      walkChildren(el)
      markdown += "\n"
      return
    }
    if (tag === "table") { markdown += "\n"; convertTable(el); markdown += "\n"; return }
    if (tag === "img") {
      const src = el.getAttribute("src") || ""
      const alt = el.getAttribute("alt") || ""
      if (!src.startsWith("data:")) markdown += `![${alt}](${src})`
      else if (alt) markdown += `*[image: ${alt}]*`
      return
    }
    walkChildren(el)
  }

  walkChildren(doc.body)
  return markdown.replace(/\n{3,}/g, "\n\n").trim()
}

export async function readImportedDocuments(file: File): Promise<ImportedDocumentInput[]> {
  const ext = extensionFor(file.name)
  const baseTitle = titleFromImportedFilename(file.name)

  if ([".xlsx", ".xls", ".ods"].includes(ext)) {
    await ensureXlsx()
    const workbook = window.XLSX!.read(await file.arrayBuffer(), { type: "array" })
    const documents = workbook.SheetNames.flatMap((sheetName) => {
      const csv = window.XLSX!.utils.sheet_to_csv(workbook.Sheets[sheetName])
      if (!csv.trim()) return []
      return [{
        title: workbook.SheetNames.length > 1 ? `${baseTitle} - ${sheetName}` : baseTitle,
        language: "csv",
        content: csv,
      }]
    })
    if (documents.length === 0) throw new Error("Spreadsheet contained no readable sheets")
    return documents
  }

  if (ext === ".docx") {
    await ensureMammoth()
    const result = await window.mammoth!.convertToHtml({ arrayBuffer: await file.arrayBuffer() })
    const content = htmlToMarkdown(result.value)
    if (!content.trim()) throw new Error("DOCX contained no readable text")
    return [{ title: baseTitle, language: "markdown", content }]
  }

  return [{
    title: baseTitle,
    language: inferImportedLanguage(file.name),
    content: await file.text(),
  }]
}
