import { useEffect, useMemo, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { ArrowLeft, Check, Download, FileText, GripVertical, Loader2, Maximize2, MousePointer2, RefreshCw, Reply, Save, Signature, Trash2, Type, Wand2, X } from "lucide-react"
import { apiFetch, apiJson } from "@/lib/api"
import { prepareSignedReply, useDocMutations, type DocFull } from "@/api/documents"
import { buildEmailDraft } from "@/lib/emailDraft"
import { parsePdfAnnotations, parsePdfFieldValues, updatePdfFieldValue, writePdfAnnotations, type PdfAnnotation } from "@/lib/pdfDocument"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

interface PdfField {
  name: string
  type: string
  label?: string
  options?: string[]
  value?: string | boolean
  rect_px: [number, number, number, number]
}

interface PdfPage {
  page: number
  width: number
  height: number
  fields: PdfField[]
}

interface PdfPagesResponse {
  pages: PdfPage[]
}

interface SignatureItem {
  id: string
  name?: string
  data_url: string
  width?: number | null
  height?: number | null
}

interface AiFillAnnotation {
  page?: number | string
  x?: number | string
  y?: number | string
  w?: number | string
  h?: number | string
  value?: string
  kind?: string
}

type DropMode = "text" | "check" | "signature" | null
type SignatureTarget = { kind: "field"; name: string } | { kind: "annotation"; id: string }
type DrawnSignaturePayload = { dataUrl: string; width: number; height: number; name: string }

const pdfDraftCache = new Map<string, string>()

export function PdfDocumentEditor({
  doc,
  content,
  onBack,
  onOpen,
}: {
  doc: DocFull
  content: string
  onBack: () => void
  onOpen: (id: string) => void
}) {
  const { remove, create } = useDocMutations()
  const initialContent = pdfDraftCache.get(doc.id) ?? content
  const [draft, setDraft] = useState(initialContent)
  const draftRef = useRef(initialContent)
  const [fieldValues, setFieldValues] = useState<Record<string, string | boolean>>(() => parsePdfFieldValues(initialContent))
  const [annotations, setAnnotations] = useState<PdfAnnotation[]>(() => parsePdfAnnotations(initialContent))
  const annotationsRef = useRef(annotations)
  const annotationSeqRef = useRef(0)
  const [dirty, setDirty] = useState(false)
  const [status, setStatus] = useState("")
  const [err, setErr] = useState("")
  const [dropMode, setDropMode] = useState<DropMode>(null)
  const [signatureTarget, setSignatureTarget] = useState<SignatureTarget | null>(null)
  const [signatureCaptureOpen, setSignatureCaptureOpen] = useState(false)
  const [sourceOpen, setSourceOpen] = useState(false)
  const [signedBusy, setSignedBusy] = useState(false)
  const [aiBusy, setAiBusy] = useState(false)
  const [saving, setSaving] = useState(false)
  const canSignedReply = !!(doc.source_email_uid && doc.source_email_folder)
  const pages = useQuery({
    queryKey: ["document-pdf-pages", doc.id],
    queryFn: () => apiJson<PdfPagesResponse>(`/api/document/${doc.id}/render-pages`),
  })
  const signatures = useQuery({
    queryKey: ["signatures"],
    queryFn: async () => {
      const r = await apiJson<{ signatures?: SignatureItem[] }>("/api/signatures")
      return r.signatures || []
    },
  })
  const signatureById = useMemo(() => new Map((signatures.data || []).map((sig) => [sig.id, sig])), [signatures.data])

  const rememberDraft = (next: string) => {
    draftRef.current = next
    pdfDraftCache.set(doc.id, next)
    return next
  }

  const save = async (nextContent?: string) => {
    const contentToSave = nextContent ?? draftRef.current
    setErr("")
    setStatus("Saving...")
    setSaving(true)
    try {
      const r = await apiFetch(`/api/document/${doc.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: contentToSave, summary: "Manual edit (v2)" }),
      })
      if (!r.ok) throw new Error("save failed")
      rememberDraft(contentToSave)
      setDirty(false)
      setStatus("Saved")
      return true
    } catch {
      setErr("Couldn't save the PDF document.")
      setStatus("")
      return false
    } finally {
      setSaving(false)
    }
  }

  const updateField = (field: PdfField, value: string | boolean) => {
    setFieldValues((current) => ({ ...current, [field.name]: value }))
    setDraft((current) => {
      const next = updatePdfFieldValue(current, field.name, field.type, value)
      return rememberDraft(next)
    })
    setDirty(true)
    setStatus("")
    setErr("")
  }

  const updateSource = (value: string) => {
    const nextAnnotations = parsePdfAnnotations(value)
    annotationsRef.current = nextAnnotations
    setAnnotations(nextAnnotations)
    setFieldValues(parsePdfFieldValues(value))
    setDraft(rememberDraft(value))
    setDirty(true)
    setStatus("")
    setErr("")
  }

  const commitAnnotations = (updater: (current: PdfAnnotation[]) => PdfAnnotation[]) => {
    setAnnotations((rendered) => {
      const base = currentAnnotations(draftRef.current, rendered.length ? rendered : annotationsRef.current)
      const next = updater(base)
      annotationsRef.current = next
      setDraft((current) => rememberDraft(writePdfAnnotations(current, next)))
      return next
    })
    setDirty(true)
    setStatus("")
    setErr("")
  }

  const nextAnnotationId = () => {
    let id = ""
    do {
      annotationSeqRef.current += 1
      id = `ann-v2-${annotationSeqRef.current.toString(36)}`
    } while (annotationsRef.current.some((ann) => ann.id === id))
    return id
  }

  const addAnnotation = (page: PdfPage, event: React.MouseEvent<HTMLDivElement>) => {
    if (!dropMode || event.target !== event.currentTarget) return
    const rect = event.currentTarget.getBoundingClientRect()
    const rawX = ((event.clientX - rect.left) / rect.width) * 100
    const rawY = ((event.clientY - rect.top) / rect.height) * 100
    const size = dropMode === "signature" ? { w: 22, h: 6 } : dropMode === "check" ? { w: 3, h: 3 } : { w: 18, h: 4 }
    const ann: PdfAnnotation = {
      id: nextAnnotationId(),
      page: page.page,
      x: Math.max(0, Math.min(100 - size.w, dropMode === "check" ? rawX - size.w / 2 : rawX)),
      y: Math.max(0, Math.min(100 - size.h, dropMode === "check" ? rawY - size.h / 2 : rawY)),
      w: size.w,
      h: size.h,
      kind: dropMode,
      value: dropMode === "check" ? "check" : "",
      lineHeight: 1.3,
    }
    commitAnnotations((current) => [...current, ann])
    if (dropMode === "signature") setSignatureTarget({ kind: "annotation", id: ann.id })
  }

  const updateAnnotation = (id: string, patch: Partial<PdfAnnotation>) => {
    commitAnnotations((current) => current.map((ann) => ann.id === id ? { ...ann, ...patch } : ann))
  }

  const deleteAnnotation = (id: string) => {
    commitAnnotations((current) => current.filter((ann) => ann.id !== id))
  }

  const aiFill = async () => {
    if (aiBusy) return
    const instruction = prompt('What should the AI fill in?\n(e.g. "My name is Jane Doe, address 123 Main St, dob 1990-01-15")')?.trim()
    if (!instruction) return
    setAiBusy(true)
    setErr("")
    setStatus("Thinking...")
    try {
      const data = await apiJson<{ annotations?: AiFillAnnotation[] }>(`/api/document/${doc.id}/ai-fill-annotations`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ instruction }),
      })
      const proposed = (data.annotations || []).map((item) => toPdfAnnotation(item, nextAnnotationId())).filter((ann): ann is PdfAnnotation => !!ann)
      if (proposed.length === 0) {
        setStatus("No AI fill suggestions")
        return
      }
      const nextAnnotations = [...currentAnnotations(draftRef.current, annotationsRef.current), ...proposed]
      const nextDraft = writePdfAnnotations(draftRef.current, nextAnnotations)
      annotationsRef.current = nextAnnotations
      setAnnotations(nextAnnotations)
      setDraft(rememberDraft(nextDraft))
      setDirty(true)
      const saved = await save(nextDraft)
      if (saved) setStatus(`AI added ${proposed.length}`)
    } catch (e) {
      setErr(e instanceof Error ? e.message : "AI fill failed.")
      setStatus("")
    } finally {
      setAiBusy(false)
    }
  }

  const applySignature = (signatureId: string) => {
    if (!signatureTarget) return
    if (signatureTarget.kind === "field") {
      const field = pages.data?.pages.flatMap((p) => p.fields).find((item) => item.name === signatureTarget.name)
      if (field) updateField({ ...field, type: "signature" }, `signature:${signatureId}`)
    } else {
      updateAnnotation(signatureTarget.id, { value: `signature:${signatureId}` })
    }
    setSignatureTarget(null)
  }

  const createDrawnSignature = async (payload: DrawnSignaturePayload) => {
    setErr("")
    const sig = await apiJson<SignatureItem>("/api/signatures", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: payload.name || "Signature",
        data: payload.dataUrl,
        width: payload.width,
        height: payload.height,
      }),
    })
    await signatures.refetch()
    applySignature(sig.id)
    setSignatureCaptureOpen(false)
  }

  const deleteSavedSignature = async (signatureId: string) => {
    if (!confirm("Delete this signature?")) return
    setErr("")
    try {
      const r = await apiFetch(`/api/signatures/${signatureId}`, { method: "DELETE" })
      if (!r.ok) throw new Error("delete failed")
      await signatures.refetch()
    } catch {
      setErr("Couldn't delete the signature.")
    }
  }

  const signedReply = async () => {
    if (signedBusy) return
    setSignedBusy(true)
    setErr("")
    try {
      const contentForReply = dirty ? draftRef.current : undefined
      if (contentForReply) {
        const saved = await save(contentForReply)
        if (!saved) throw new Error("Couldn't save the PDF document.")
      }
      const result = await prepareSignedReply(doc.id)
      if (!result.attachment) throw new Error("Signed attachment was not returned")
      const reply = result.reply || {}
      const firstName = (reply.to_name || "").trim().split(/\s+/)[0]
      const emailDoc = buildEmailDraft({
        to: reply.to || "",
        cc: "",
        bcc: "",
        subject: reply.subject || doc.title || "Signed reply",
        inReplyTo: reply.in_reply_to || "",
        references: reply.references || "",
        sourceUid: reply.source_uid || doc.source_email_uid || "",
        sourceFolder: reply.source_folder || doc.source_email_folder || "",
        sourceAccount: reply.account_id || doc.source_email_account_id || "",
        attachments: [result.attachment],
        body: `Hi${firstName ? ` ${firstName}` : ""},\n\nPlease find the signed copy attached.\n\nBest,\n`,
      })
      const created = await create.mutateAsync({
        session_id: doc.session_id || null,
        title: reply.subject || "Signed reply",
        language: "email",
        content: emailDoc,
      })
      if (created.id) onOpen(created.id)
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Couldn't prepare signed reply.")
    } finally {
      setSignedBusy(false)
    }
  }

  const del = () => { if (confirm("Delete this document?")) remove.mutate(doc.id, { onSuccess: onBack }) }

  return (
    <div className="flex h-full min-h-0 flex-col bg-background">
      <header className="flex min-h-13 shrink-0 flex-wrap items-center gap-2 border-b px-3 py-1.5 sm:flex-nowrap sm:py-0">
        <Button variant="ghost" size="icon" onClick={onBack} title="Back"><ArrowLeft className="size-4" /></Button>
        <div className="min-w-0 flex-1 truncate text-sm font-semibold">{doc.title || "PDF document"}</div>
        {status && <span className="shrink-0 text-xs text-muted-foreground">{status}</span>}
        <Button variant="ghost" size="icon" onClick={() => pages.refetch()} title="Refresh PDF"><RefreshCw className="size-4" /></Button>
        <a href={`/api/document/${doc.id}/export-pdf`} target="_blank" rel="noreferrer" className="hidden h-8 items-center gap-1.5 rounded-md border px-3 text-sm font-medium text-muted-foreground hover:bg-accent hover:text-foreground sm:inline-flex"><Download className="size-4" />Export</a>
        {canSignedReply && <Button variant="outline" size="sm" disabled={signedBusy} onClick={signedReply} className="hidden sm:inline-flex"><Reply className="size-4" />{signedBusy ? "Preparing..." : "Signed reply"}</Button>}
        <button onClick={del} title="Delete" className="hidden rounded-md p-1.5 text-muted-foreground hover:text-destructive sm:inline-flex"><Trash2 className="size-4" /></button>
        <Button size="sm" disabled={!dirty || saving} onClick={() => { void save() }}><Save className="size-4" />{saving ? "Saving..." : dirty ? "Save" : "Saved"}</Button>
      </header>

      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b px-3 py-2">
        <span className="inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground"><FileText className="size-3.5" />PDF tools</span>
        <ToolButton active={dropMode === null} onClick={() => setDropMode(null)} title="Select fields"><MousePointer2 className="size-3.5" />Select</ToolButton>
        <ToolButton active={dropMode === "text"} onClick={() => setDropMode(dropMode === "text" ? null : "text")} title="Add text box"><Type className="size-3.5" />Text</ToolButton>
        <ToolButton active={dropMode === "check"} onClick={() => setDropMode(dropMode === "check" ? null : "check")} title="Add check mark"><Check className="size-3.5" />Check</ToolButton>
        <ToolButton active={dropMode === "signature"} onClick={() => setDropMode(dropMode === "signature" ? null : "signature")} title="Add signature"><Signature className="size-3.5" />Sign</ToolButton>
        <ToolButton active={sourceOpen} onClick={() => setSourceOpen(!sourceOpen)} title="Edit source"><FileText className="size-3.5" />Source</ToolButton>
        <Button variant="outline" size="sm" disabled={aiBusy} onClick={aiFill} title="AI fill annotations">
          {aiBusy ? <Loader2 className="size-3.5 animate-spin" /> : <Wand2 className="size-3.5" />}
          AI fill
        </Button>
        <span className="ml-auto hidden text-xs text-muted-foreground sm:inline">{dropMode ? "Click a PDF page to place it." : "Edit fields directly on the PDF."}</span>
      </div>

      {err && <div className="shrink-0 border-b px-4 py-2 text-xs text-destructive">{err}</div>}
      {sourceOpen && (
        <div className="shrink-0 border-b bg-muted/20 p-3">
          <textarea
            aria-label="PDF markdown source"
            value={draft}
            onChange={(e) => updateSource(e.target.value)}
            spellCheck={false}
            className="h-52 w-full resize-y rounded-md border bg-background p-3 font-mono text-xs leading-relaxed text-foreground outline-none focus:border-primary"
          />
        </div>
      )}
      <div className="min-h-0 flex-1 overflow-y-auto bg-muted/20 px-3 py-4">
        {pages.isLoading && <div className="py-12 text-center text-sm text-muted-foreground">Loading PDF...</div>}
        {pages.error && <div className="py-12 text-center text-sm text-destructive">Could not load the PDF view.</div>}
        <div className="space-y-5">
          {(pages.data?.pages || []).map((page) => (
            <PdfPageView
              key={page.page}
              page={page}
              docId={doc.id}
              dropMode={dropMode}
              fieldValues={fieldValues}
              annotations={annotations.filter((ann) => ann.page === page.page)}
              signatureById={signatureById}
              onPageClick={addAnnotation}
              onFieldChange={updateField}
              onPickSignature={(target) => setSignatureTarget(target)}
              onAnnotationChange={updateAnnotation}
              onDeleteAnnotation={deleteAnnotation}
            />
          ))}
        </div>
      </div>
      {signatureTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/70 p-4" onMouseDown={() => setSignatureTarget(null)}>
          <div className="w-full max-w-md rounded-lg border bg-popover p-3 shadow-xl" onMouseDown={(e) => e.stopPropagation()}>
            <div className="mb-3 flex items-center gap-2">
              <div className="flex-1 text-sm font-semibold">Choose signature</div>
              <button onClick={() => setSignatureTarget(null)} title="Close" className="rounded-md p-1 text-muted-foreground hover:bg-accent hover:text-foreground"><X className="size-4" /></button>
            </div>
            <div className="space-y-2">
              <Button className="w-full justify-center" onClick={() => setSignatureCaptureOpen(true)}>
                <Signature className="size-4" />Draw new signature
              </Button>
              {(signatures.data || []).map((sig) => (
                <div key={sig.id} className="flex items-center gap-2 rounded-md border bg-background p-2">
                  <button onClick={() => applySignature(sig.id)} className="flex min-w-0 flex-1 items-center gap-3 text-left">
                    <img src={sig.data_url} alt="" className="h-10 w-28 rounded border bg-white object-contain" />
                    <span className="min-w-0 flex-1 truncate text-sm">{sig.name || "Signature"}</span>
                  </button>
                  <button type="button" onClick={() => { void deleteSavedSignature(sig.id) }} title="Delete signature" className="rounded-md p-1.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive">
                    <Trash2 className="size-4" />
                  </button>
                </div>
              ))}
              {!signatures.isLoading && (signatures.data || []).length === 0 && <p className="py-6 text-center text-sm text-muted-foreground">No saved signatures yet.</p>}
            </div>
          </div>
        </div>
      )}
      {signatureCaptureOpen && (
        <SignatureCaptureModal
          onCancel={() => setSignatureCaptureOpen(false)}
          onSave={createDrawnSignature}
        />
      )}
    </div>
  )
}

function ToolButton({ active, onClick, title, children }: { active: boolean; onClick: () => void; title: string; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className={cn("inline-flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-xs transition-colors", active ? "border-foreground bg-accent text-foreground" : "text-muted-foreground hover:bg-accent hover:text-foreground")}
    >
      {children}
    </button>
  )
}

function SignatureCaptureModal({
  onCancel,
  onSave,
}: {
  onCancel: () => void
  onSave: (payload: DrawnSignaturePayload) => Promise<void>
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const drawingRef = useRef(false)
  const lastPointRef = useRef<{ x: number; y: number } | null>(null)
  const historyRef = useRef<ImageData[]>([])
  const [name, setName] = useState("")
  const [hasInk, setHasInk] = useState(false)
  const [canUndo, setCanUndo] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")

  const paintBlank = (canvas: HTMLCanvasElement) => {
    const ctx = canvas.getContext("2d")
    if (!ctx) return
    ctx.fillStyle = "#ffffff"
    ctx.fillRect(0, 0, canvas.width, canvas.height)
  }

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    paintBlank(canvas)
  }, [])

  const pointFromEvent = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const canvas = event.currentTarget
    const rect = canvas.getBoundingClientRect()
    const sx = canvas.width / rect.width
    const sy = canvas.height / rect.height
    return {
      x: (event.clientX - rect.left) * sx,
      y: (event.clientY - rect.top) * sy,
    }
  }

  const pushHistory = (canvas: HTMLCanvasElement) => {
    const ctx = canvas.getContext("2d")
    if (!ctx) return
    try {
      historyRef.current.push(ctx.getImageData(0, 0, canvas.width, canvas.height))
      if (historyRef.current.length > 30) historyRef.current.shift()
      setCanUndo(historyRef.current.length > 0)
    } catch {
      historyRef.current = []
      setCanUndo(false)
    }
  }

  const startDrawing = (event: React.PointerEvent<HTMLCanvasElement>) => {
    event.preventDefault()
    const canvas = event.currentTarget
    const ctx = canvas.getContext("2d")
    if (!ctx) return
    try { canvas.setPointerCapture(event.pointerId) } catch { /* synthetic pointer events may not be capturable */ }
    pushHistory(canvas)
    const point = pointFromEvent(event)
    drawingRef.current = true
    lastPointRef.current = point
    ctx.strokeStyle = "#111111"
    ctx.lineWidth = 5
    ctx.lineCap = "round"
    ctx.lineJoin = "round"
    ctx.beginPath()
    ctx.moveTo(point.x, point.y)
    ctx.lineTo(point.x + 0.1, point.y + 0.1)
    ctx.stroke()
    setHasInk(true)
  }

  const draw = (event: React.PointerEvent<HTMLCanvasElement>) => {
    if (!drawingRef.current) return
    event.preventDefault()
    const canvas = event.currentTarget
    const ctx = canvas.getContext("2d")
    const last = lastPointRef.current
    if (!ctx || !last) return
    const point = pointFromEvent(event)
    ctx.strokeStyle = "#111111"
    ctx.lineWidth = 5
    ctx.lineCap = "round"
    ctx.lineJoin = "round"
    ctx.beginPath()
    ctx.moveTo(last.x, last.y)
    ctx.lineTo(point.x, point.y)
    ctx.stroke()
    lastPointRef.current = point
  }

  const stopDrawing = (event: React.PointerEvent<HTMLCanvasElement>) => {
    drawingRef.current = false
    lastPointRef.current = null
    try { event.currentTarget.releasePointerCapture(event.pointerId) } catch { /* pointer already released */ }
  }

  const clear = () => {
    const canvas = canvasRef.current
    if (!canvas) return
    paintBlank(canvas)
    historyRef.current = []
    setHasInk(false)
    setCanUndo(false)
    setError("")
  }

  const undo = () => {
    const canvas = canvasRef.current
    const ctx = canvas?.getContext("2d")
    const snap = historyRef.current.pop()
    if (!canvas || !ctx || !snap) return
    ctx.putImageData(snap, 0, 0)
    setCanUndo(historyRef.current.length > 0)
    setHasInk(canvasHasSignatureInk(canvas))
  }

  const save = async () => {
    const canvas = canvasRef.current
    if (!canvas || !hasInk || busy) return
    const trimmed = trimSignatureCanvas(canvas)
    if (!trimmed) {
      setError("Draw a signature first.")
      setHasInk(false)
      return
    }
    setBusy(true)
    setError("")
    try {
      await onSave({ ...trimmed, name: name.trim() || "Signature" })
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save signature.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-background/80 p-4" onMouseDown={onCancel}>
      <div className="w-full max-w-xl rounded-lg border bg-popover p-3 shadow-xl" onMouseDown={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center gap-2">
          <div className="flex-1 text-sm font-semibold">Draw your signature</div>
          <button onClick={onCancel} title="Close" className="rounded-md p-1 text-muted-foreground hover:bg-accent hover:text-foreground"><X className="size-4" /></button>
        </div>
        <canvas
          ref={canvasRef}
          data-testid="signature-canvas"
          width={900}
          height={280}
          className="block w-full touch-none rounded-md border bg-white"
          style={{ aspectRatio: "900 / 280", cursor: "crosshair" }}
          onPointerDown={startDrawing}
          onPointerMove={draw}
          onPointerUp={stopDrawing}
          onPointerCancel={stopDrawing}
          onPointerLeave={(event) => { if (drawingRef.current) stopDrawing(event) }}
        />
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Name"
          className="mt-3 h-9 w-full rounded-md border bg-background px-3 text-sm outline-none focus:border-primary"
        />
        {error && <div className="mt-2 text-xs text-destructive">{error}</div>}
        <div className="mt-3 flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={clear}>Clear</Button>
          <Button variant="outline" size="sm" disabled={!canUndo} onClick={undo}>Undo</Button>
          <div className="flex-1" />
          <Button variant="ghost" size="sm" onClick={onCancel}>Cancel</Button>
          <Button size="sm" disabled={!hasInk || busy} onClick={() => { void save() }}><Save className="size-4" />{busy ? "Saving..." : "Save"}</Button>
        </div>
      </div>
    </div>
  )
}

function PdfPageView({
  page,
  docId,
  dropMode,
  fieldValues,
  annotations,
  signatureById,
  onPageClick,
  onFieldChange,
  onPickSignature,
  onAnnotationChange,
  onDeleteAnnotation,
}: {
  page: PdfPage
  docId: string
  dropMode: DropMode
  fieldValues: Record<string, string | boolean>
  annotations: PdfAnnotation[]
  signatureById: Map<string, SignatureItem>
  onPageClick: (page: PdfPage, event: React.MouseEvent<HTMLDivElement>) => void
  onFieldChange: (field: PdfField, value: string | boolean) => void
  onPickSignature: (target: SignatureTarget) => void
  onAnnotationChange: (id: string, patch: Partial<PdfAnnotation>) => void
  onDeleteAnnotation: (id: string) => void
}) {
  return (
    <div
      data-testid={`pdf-page-${page.page}`}
      data-pdf-page="true"
      className={cn("relative mx-auto bg-white shadow-xl", dropMode && "cursor-crosshair")}
      style={{ width: page.width, maxWidth: "100%", aspectRatio: `${page.width} / ${page.height}` }}
      onClick={(event) => onPageClick(page, event)}
    >
      <img src={`/api/document/${docId}/page/${page.page}.png`} alt={`Page ${page.page}`} className="pointer-events-none h-full w-full select-none object-fill" draggable={false} />
      {page.fields.map((field) => (
        <PdfFieldOverlay
          key={`${page.page}:${field.name}`}
          field={field}
          value={fieldValues[field.name] ?? field.value ?? ""}
          page={page}
          signatureById={signatureById}
          onChange={onFieldChange}
          onPickSignature={() => onPickSignature({ kind: "field", name: field.name })}
        />
      ))}
      {annotations.map((ann) => (
        <PdfAnnotationOverlay
          key={ann.id}
          annotation={ann}
          signatureById={signatureById}
          onChange={onAnnotationChange}
          onPickSignature={() => onPickSignature({ kind: "annotation", id: ann.id })}
          onDelete={onDeleteAnnotation}
        />
      ))}
    </div>
  )
}

function fieldStyle(field: PdfField, page: PdfPage): React.CSSProperties {
  const [x0, y0, x1, y1] = field.rect_px
  return {
    left: `${(x0 / page.width) * 100}%`,
    top: `${(y0 / page.height) * 100}%`,
    width: `${((x1 - x0) / page.width) * 100}%`,
    height: `${((y1 - y0) / page.height) * 100}%`,
  }
}

function PdfFieldOverlay({
  field,
  value,
  page,
  signatureById,
  onChange,
  onPickSignature,
}: {
  field: PdfField
  value: string | boolean
  page: PdfPage
  signatureById: Map<string, SignatureItem>
  onChange: (field: PdfField, value: string | boolean) => void
  onPickSignature: () => void
}) {
  const style = fieldStyle(field, page)
  const type = /sign(?:ed|ature)/i.test(`${field.name} ${field.label || ""}`) ? "signature" : field.type
  if (type === "signature") {
    const sigId = typeof value === "string" && value.startsWith("signature:") ? value.slice("signature:".length) : ""
    const sig = signatureById.get(sigId)
    return (
      <button type="button" title={field.label || field.name} onClick={onPickSignature} className="absolute flex items-center justify-center overflow-hidden border border-dashed border-primary/70 bg-primary/10 text-[10px] text-primary" style={style}>
        {sig ? <img src={sig.data_url} alt="" className="h-full w-full object-contain" /> : "Sign here"}
      </button>
    )
  }
  if (field.type === "checkbox") {
    return <input type="checkbox" checked={!!value} onChange={(e) => onChange(field, e.target.checked)} className="absolute m-0 rounded-sm accent-primary [color-scheme:light]" style={style} title={field.label || field.name} />
  }
  if (field.type === "choice" && field.options?.length) {
    return (
      <select value={String(value || "")} onChange={(e) => onChange(field, e.target.value)} className="absolute border border-primary/60 bg-white/90 px-1 text-[10px] text-black outline-none" style={style} title={field.label || field.name}>
        <option value="">-</option>
        {field.options.map((option) => <option key={option} value={option}>{option}</option>)}
      </select>
    )
  }
  return (
    <input
      value={String(value || "")}
      onChange={(e) => onChange(field, e.target.value)}
      className="absolute border border-primary/60 bg-white/90 px-1 text-[11px] text-black outline-none"
      style={style}
      title={field.label || field.name}
    />
  )
}

function PdfAnnotationOverlay({
  annotation,
  signatureById,
  onChange,
  onPickSignature,
  onDelete,
}: {
  annotation: PdfAnnotation
  signatureById: Map<string, SignatureItem>
  onChange: (id: string, patch: Partial<PdfAnnotation>) => void
  onPickSignature: () => void
  onDelete: (id: string) => void
}) {
  const style: React.CSSProperties = {
    left: `${annotation.x}%`,
    top: `${annotation.y}%`,
    width: `${annotation.w}%`,
    height: `${annotation.h}%`,
  }
  const pageRect = (target: HTMLElement) => target.closest<HTMLElement>("[data-pdf-page]")?.getBoundingClientRect()
  const startMove = (event: React.PointerEvent<HTMLButtonElement>) => {
    event.preventDefault()
    event.stopPropagation()
    const rect = pageRect(event.currentTarget)
    if (!rect?.width || !rect.height) return
    event.currentTarget.setPointerCapture(event.pointerId)
    const start = { x: annotation.x, y: annotation.y, mx: event.clientX, my: event.clientY }
    const onMove = (moveEvent: PointerEvent) => {
      const dx = ((moveEvent.clientX - start.mx) / rect.width) * 100
      const dy = ((moveEvent.clientY - start.my) / rect.height) * 100
      onChange(annotation.id, {
        x: clamp(start.x + dx, 0, 100 - annotation.w),
        y: clamp(start.y + dy, 0, 100 - annotation.h),
      })
    }
    const onUp = () => {
      document.removeEventListener("pointermove", onMove)
      document.removeEventListener("pointerup", onUp)
    }
    document.addEventListener("pointermove", onMove)
    document.addEventListener("pointerup", onUp)
  }
  const startResize = (event: React.PointerEvent<HTMLButtonElement>) => {
    event.preventDefault()
    event.stopPropagation()
    const rect = pageRect(event.currentTarget)
    if (!rect?.width || !rect.height) return
    event.currentTarget.setPointerCapture(event.pointerId)
    const start = { w: annotation.w, h: annotation.h, mx: event.clientX, my: event.clientY }
    const onMove = (moveEvent: PointerEvent) => {
      const dw = ((moveEvent.clientX - start.mx) / rect.width) * 100
      const dh = ((moveEvent.clientY - start.my) / rect.height) * 100
      onChange(annotation.id, {
        w: clamp(start.w + dw, 1, 100 - annotation.x),
        h: clamp(start.h + dh, 0.8, 100 - annotation.y),
      })
    }
    const onUp = () => {
      document.removeEventListener("pointermove", onMove)
      document.removeEventListener("pointerup", onUp)
    }
    document.addEventListener("pointermove", onMove)
    document.addEventListener("pointerup", onUp)
  }
  const controls = (
    <>
      <button type="button" title="Drag to move" onPointerDown={startMove} className="absolute -left-5 -top-5 z-10 rounded-sm border bg-white p-0.5 text-black shadow hover:bg-accent"><GripVertical className="size-3" /></button>
      <button type="button" title="Remove annotation" onClick={() => onDelete(annotation.id)} className="absolute -right-5 -top-5 z-10 rounded-full border bg-white p-0.5 text-black shadow hover:bg-red-50"><X className="size-3" /></button>
      <button type="button" title="Drag to resize" onPointerDown={startResize} className="absolute -bottom-5 -right-5 z-10 rounded-sm border bg-white p-0.5 text-black shadow hover:bg-accent"><Maximize2 className="size-3" /></button>
    </>
  )
  if (annotation.kind === "check") {
    return <div className="absolute group" style={style}>{controls}<Check className="h-full w-full text-black" strokeWidth={3} /></div>
  }
  if (annotation.kind === "signature") {
    const sigId = annotation.value.startsWith("signature:") ? annotation.value.slice("signature:".length) : ""
    const sig = signatureById.get(sigId)
    return (
      <div className="absolute group" style={style}>
        {controls}
        <button type="button" onClick={onPickSignature} className="flex h-full w-full items-center justify-center border border-dashed border-primary/70 bg-primary/10 text-[10px] text-primary">
          {sig ? <img src={sig.data_url} alt="" className="h-full w-full object-contain" /> : <span>Sign here</span>}
        </button>
      </div>
    )
  }
  return (
    <div className="absolute group" style={style}>
      {controls}
      <textarea
        value={annotation.value}
        onChange={(e) => onChange(annotation.id, { value: e.target.value })}
        placeholder="Type"
        className="h-full w-full resize-none border border-dashed border-primary/70 bg-primary/10 px-1 py-0.5 text-[11px] leading-tight text-black outline-none"
      />
    </div>
  )
}

function toPdfAnnotation(item: AiFillAnnotation, id: string): PdfAnnotation | null {
  const x = clamp(numberFrom(item.x), 0, 99)
  const y = clamp(numberFrom(item.y), 0, 99)
  const w = clamp(numberFrom(item.w, 22), 0.5, 100 - x)
  const h = clamp(numberFrom(item.h, 3.5), 0.3, 100 - y)
  const value = String(item.value || "").trim()
  if (!value || w <= 0.5 || h <= 0.3) return null
  return {
    id,
    page: Math.max(1, Math.floor(numberFrom(item.page, 1))),
    x,
    y,
    w,
    h,
    kind: item.kind === "check" || item.kind === "signature" ? item.kind : "text",
    value,
    lineHeight: 1.3,
  }
}

function canvasHasSignatureInk(canvas: HTMLCanvasElement): boolean {
  const ctx = canvas.getContext("2d")
  if (!ctx) return false
  const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data
  for (let i = 0; i < data.length; i += 4) {
    if (data[i + 3] > 10 && (data[i] < 245 || data[i + 1] < 245 || data[i + 2] < 245)) return true
  }
  return false
}

function trimSignatureCanvas(canvas: HTMLCanvasElement): { dataUrl: string; width: number; height: number } | null {
  const ctx = canvas.getContext("2d")
  if (!ctx) return null
  const image = ctx.getImageData(0, 0, canvas.width, canvas.height)
  const data = image.data
  let minX = canvas.width
  let minY = canvas.height
  let maxX = -1
  let maxY = -1
  for (let y = 0; y < canvas.height; y += 1) {
    for (let x = 0; x < canvas.width; x += 1) {
      const i = (y * canvas.width + x) * 4
      if (data[i + 3] > 10 && (data[i] < 245 || data[i + 1] < 245 || data[i + 2] < 245)) {
        minX = Math.min(minX, x)
        minY = Math.min(minY, y)
        maxX = Math.max(maxX, x)
        maxY = Math.max(maxY, y)
      }
    }
  }
  if (maxX < 0 || maxY < 0) return null
  const pad = 8
  minX = Math.max(0, minX - pad)
  minY = Math.max(0, minY - pad)
  maxX = Math.min(canvas.width - 1, maxX + pad)
  maxY = Math.min(canvas.height - 1, maxY + pad)
  const width = maxX - minX + 1
  const height = maxY - minY + 1
  const out = document.createElement("canvas")
  out.width = width
  out.height = height
  const outCtx = out.getContext("2d")
  if (!outCtx) return null
  outCtx.drawImage(canvas, minX, minY, width, height, 0, 0, width, height)
  const trimmed = outCtx.getImageData(0, 0, width, height)
  for (let i = 0; i < trimmed.data.length; i += 4) {
    if (trimmed.data[i] > 245 && trimmed.data[i + 1] > 245 && trimmed.data[i + 2] > 245) trimmed.data[i + 3] = 0
  }
  outCtx.putImageData(trimmed, 0, 0)
  return { dataUrl: out.toDataURL("image/png"), width, height }
}

function currentAnnotations(draft: string, state: PdfAnnotation[]): PdfAnnotation[] {
  const parsed = parsePdfAnnotations(draft)
  return parsed.length > state.length ? parsed : state
}

function numberFrom(value: unknown, fallback = 0): number {
  const n = typeof value === "number" ? value : Number.parseFloat(String(value ?? ""))
  return Number.isFinite(n) ? n : fallback
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value))
}
