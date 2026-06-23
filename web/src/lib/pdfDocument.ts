export interface PdfAnnotation {
  id: string
  page: number
  x: number
  y: number
  w: number
  h: number
  kind: "text" | "check" | "signature"
  value: string
  lineHeight?: number
}

const PDF_MARKER_RE = /<!--\s*pdf_(?:form_)?source\s+upload_id="[^"]+"/
const ANNOTATION_RE = /^[ \t]*-\s+(.*?)\s*<!--\s*annotation\s+id=([\w-]+)\s+page=(\d+)\s+x=([\d.]+)\s+y=([\d.]+)\s+w=([\d.]+)\s+h=([\d.]+)(?:\s+kind=(\w+))?(?:\s+lh=([\d.]+))?\s*-->[ \t]*$/gm
const FIELD_RE = /^\s*-\s+(.*?)\s*<!--\s*field=([A-Za-z0-9_.%-]+)\s+type=(\w+)\s*-->\s*$/gm

export function isPdfBackedDocument(content?: string): boolean {
  return PDF_MARKER_RE.test(content || "")
}

export function parsePdfAnnotations(content = ""): PdfAnnotation[] {
  const out: PdfAnnotation[] = []
  let m: RegExpExecArray | null
  while ((m = ANNOTATION_RE.exec(content)) !== null) {
    const kind = m[8] === "check" || m[8] === "signature" ? m[8] : "text"
    out.push({
      id: m[2],
      page: Number.parseInt(m[3], 10) || 1,
      x: Number.parseFloat(m[4]) || 0,
      y: Number.parseFloat(m[5]) || 0,
      w: Number.parseFloat(m[6]) || 5,
      h: Number.parseFloat(m[7]) || 2,
      kind,
      lineHeight: m[9] ? Number.parseFloat(m[9]) : 1.3,
      value: m[1] === "_(empty)_" ? "" : unescapeAnnotationValue(m[1]),
    })
  }
  return out
}

export function writePdfAnnotations(content: string, annotations: PdfAnnotation[]): string {
  let next = (content || "").replace(ANNOTATION_RE, "")
  next = next.replace(/\n##\s+Annotations\s*\r?\n+/g, "\n")
  next = next.replace(/\n{3,}/g, "\n\n")
  if (annotations.length === 0) return next.trimEnd() + "\n"
  next = next.trimEnd() + "\n\n## Annotations\n\n"
  for (const ann of annotations) next += annotationLine(ann) + "\n"
  return next
}

export function parsePdfFieldValues(content = ""): Record<string, string | boolean> {
  const values: Record<string, string | boolean> = {}
  let m: RegExpExecArray | null
  while ((m = FIELD_RE.exec(content)) !== null) {
    const body = m[1]
    const name = decodeFieldName(m[2])
    const type = m[3]
    if (type === "checkbox") {
      values[name] = /^\s*\[[xX]\]/.test(body)
      continue
    }
    const value = (
      type === "choice"
        ? body.match(/\]\s*:\s*(.*)$/)?.[1] || ""
        : body.match(/:\*\*\s*(.*)$/)?.[1] || ""
    ).trim()
    values[name] = value === "_(empty)_" || value === "_(not selected)_" || value === "_(unsigned)_" ? "" : value
  }
  return values
}

export function updatePdfFieldValue(content: string, name: string, type: string, value: string | boolean): string {
  const enc = escapeRegExp(encodeFieldName(name))
  const re = new RegExp(`^(\\s*-\\s+)(.*?)(\\s*<!--\\s*field=${enc}\\s+type=\\w+\\s*-->\\s*)$`, "m")
  const m = (content || "").match(re)
  if (!m) return content
  const body = m[2]
  let newBody: string
  if (type === "checkbox") {
    const mark = value ? "[x]" : "[ ]"
    newBody = body.replace(/^\s*\[[ xX]\]/, mark)
  } else if (type === "choice") {
    const shown = value ? String(value) : "_(not selected)_"
    newBody = body.replace(/(\]\s*:\s*).*$/, `$1${shown}`)
  } else if (type === "signature") {
    const shown = value ? String(value) : "_(unsigned)_"
    newBody = body.replace(/(:\*\*\s*).*$/, `$1${shown}`)
  } else {
    const shown = value === "" ? "_(empty)_" : String(value)
    newBody = body.replace(/(:\*\*\s*).*$/, `$1${shown}`)
  }
  return newBody === body ? content : content.replace(re, `${m[1]}${newBody}${m[3]}`)
}

function annotationLine(ann: PdfAnnotation): string {
  const lh = Number.isFinite(ann.lineHeight) ? ann.lineHeight || 1.3 : 1.3
  const value = ann.value ? escapeAnnotationValue(ann.value) : "_(empty)_"
  return `- ${value} <!-- annotation id=${ann.id} page=${ann.page} x=${ann.x.toFixed(2)} y=${ann.y.toFixed(2)} w=${ann.w.toFixed(2)} h=${ann.h.toFixed(2)} kind=${ann.kind} lh=${lh.toFixed(2)} -->`
}

function escapeAnnotationValue(value: string): string {
  return String(value).replace(/\\/g, "\\\\").replace(/\n/g, "\\n")
}

function unescapeAnnotationValue(value: string): string {
  return String(value || "").replace(/\\(.)/g, (m, c: string) => c === "n" ? "\n" : c === "\\" ? "\\" : m)
}

function encodeFieldName(name: string): string {
  const encoder = new TextEncoder()
  let out = ""
  for (const ch of Array.from(name || "")) {
    if (/^[A-Za-z0-9_.-]$/.test(ch)) out += ch
    else for (const b of encoder.encode(ch)) out += `%${b.toString(16).toUpperCase().padStart(2, "0")}`
  }
  return out
}

function decodeFieldName(name: string): string {
  try {
    return decodeURIComponent(name || "")
  } catch {
    return name || ""
  }
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
}
