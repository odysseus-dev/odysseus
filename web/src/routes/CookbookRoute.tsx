import { useMemo, useState } from "react"
import { FlaskConical, Cpu, HardDrive, Download, Play, Square, Server, Loader2, AlertTriangle, Gauge, Wrench, PackageSearch, CalendarClock, Image, RotateCcw, FileText, Copy } from "lucide-react"
import {
  useCachedModels, useGpus, useCookbookMutations,
  useRunningTasks, useRunningMutations,
  useHwfitModels, useHfLatest, useOllamaLibrary,
  useServeProfiles, useImageFit, useRecipeManifest, useVllmRecipe, useCookbookSetup,
  SERVE_BACKENDS, buildServeCmd,
  type CachedModel, type DiscoveryModel, type Gpu, type HwfitModel, type ServeBackend, type RunningTask,
} from "@/api/cookbook"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

function gb(mb?: number) { return mb != null ? `${(mb / 1024).toFixed(1)} GB` : "—" }
const inp = "h-9 rounded-md border bg-background px-3 text-sm outline-none focus-visible:border-ring"

function fitTone(level?: string) {
  if (level === "perfect" || level === "good") return "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400"
  if (level === "marginal") return "bg-amber-500/15 text-amber-700 dark:text-amber-400"
  return "bg-destructive/10 text-destructive"
}

function ModelDiscovery() {
  const [source, setSource] = useState<"fit" | "image" | "hf" | "ollama">("fit")
  const [search, setSearch] = useState("")
  const [useCase, setUseCase] = useState("")
  const [sort, setSort] = useState("score")
  const [fitOnly, setFitOnly] = useState(false)
  const fit = useHwfitModels({ search, useCase, sort, fitOnly, limit: 100 })
  const hf = useHfLatest(source === "hf")
  const ollama = useOllamaLibrary(source === "ollama")
  const imageFit = useImageFit(source === "image" ? search : "")
  const { download } = useCookbookMutations()
  const discovered: DiscoveryModel[] = source === "hf" ? (hf.data || []) : (ollama.data || [])
  const downloadRepo = (repo: string, backend: string) => download.mutate({ repo_id: repo, backend })
  const system = fit.data?.system
  return <section data-tour="cookbook-discovery">
    <h2 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground"><Gauge className="size-3.5" />Discover &amp; fit</h2>
    <div className="overflow-hidden rounded-lg border bg-card">
      <div className="flex flex-wrap items-center gap-2 border-b p-3">
        {([['fit', 'Hardware fit'], ['image', 'Image models'], ['hf', 'Hugging Face'], ['ollama', 'Ollama']] as const).map(([value, label]) => <button key={value} onClick={() => setSource(value)} className={cn("rounded-md px-2.5 py-1.5 text-xs font-medium", source === value ? "bg-accent text-foreground" : "text-muted-foreground hover:text-foreground")}>{label}</button>)}
        {(source === "fit" || source === "image") && <><input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search catalog…" className={cn(inp, "ml-auto min-w-48 flex-1")} />{source === "fit" && <><select value={useCase} onChange={(e) => setUseCase(e.target.value)} className={inp}><option value="">All uses</option><option value="general">General</option><option value="coding">Coding</option><option value="reasoning">Reasoning</option><option value="chat">Chat</option><option value="multimodal">Multimodal</option></select><select value={sort} onChange={(e) => setSort(e.target.value)} className={inp}><option value="score">Best score</option><option value="newest">Newest</option><option value="speed">Speed</option><option value="vram">VRAM</option><option value="params">Parameters</option><option value="context">Context</option></select><label className="inline-flex h-9 items-center gap-1.5 rounded-md border px-2 text-xs"><input type="checkbox" checked={fitOnly} onChange={(e) => setFitOnly(e.target.checked)} />Fits only</label></>}</>}
      </div>
      {source === "fit" ? <>
        {system && <div className="flex flex-wrap gap-x-4 gap-y-1 border-b bg-muted/20 px-3 py-2 text-xs text-muted-foreground"><span>{system.hostname || "This host"}</span><span>{system.gpu_name || system.backend || "CPU"}{system.gpu_count ? ` ×${system.gpu_count}` : ""}</span>{system.gpu_vram_gb != null && <span>{system.gpu_vram_gb} GB VRAM</span>}{system.total_ram_gb != null && <span>{system.total_ram_gb} GB RAM</span>}</div>}
        <div className="max-h-[520px] overflow-auto"><table className="w-full min-w-[760px] text-left text-xs"><thead className="sticky top-0 bg-card text-muted-foreground"><tr><th className="px-3 py-2">Model</th><th>Fit</th><th>Quant</th><th>Memory</th><th>Speed</th><th>Context</th><th className="pr-3"></th></tr></thead><tbody className="divide-y">{(fit.data?.models || []).map((model: HwfitModel) => <tr key={model.name}><td className="px-3 py-2"><div className="font-medium">{model.name}</div><div className="text-muted-foreground">{model.parameter_count || (model.params_b ? `${model.params_b}B` : "")} {model.use_case ? `· ${model.use_case}` : ""}</div></td><td><span className={cn("rounded-full px-2 py-0.5", fitTone(model.fit_level))}>{(model.fit_level || "unknown").replace('_', ' ')}</span></td><td>{model.quant || "—"}</td><td>{model.required_gb != null ? `${model.required_gb} GB` : "—"}</td><td>{model.speed_tps != null ? `${Math.round(model.speed_tps)} tok/s` : "—"}</td><td>{model.context ? model.context.toLocaleString() : "—"}</td><td className="pr-3 text-right"><Button size="sm" variant="outline" disabled={download.isPending} onClick={() => downloadRepo(model.name, "hf")}>Download</Button></td></tr>)}</tbody></table>{fit.isLoading && <p className="p-4 text-sm text-muted-foreground">Ranking models for this hardware…</p>}{fit.data?.error && <p className="p-4 text-sm text-destructive">{fit.data.error}</p>}</div>
      </> : source === "image" ? <div className="max-h-[520px] divide-y overflow-auto">{(imageFit.data?.models || []).map((raw, i) => { const repo = String(raw.name || raw.repo_id || raw.id || `Image model ${i + 1}`); return <div key={`${repo}-${i}`} className="flex items-center gap-3 px-3 py-2.5"><Image className="size-4 text-muted-foreground" /><div className="min-w-0 flex-1"><div className="truncate text-sm font-medium">{repo}</div><div className="text-xs text-muted-foreground">{String(raw.fit_level || raw.fit || "unknown")} {raw.required_gb ? `· ${raw.required_gb} GB` : ""}</div></div><Button size="sm" variant="outline" disabled={download.isPending} onClick={() => downloadRepo(repo, "hf")}>Download</Button></div>})}{imageFit.isLoading && <p className="p-4 text-sm text-muted-foreground">Ranking image models…</p>}</div> : <div className="max-h-[520px] divide-y overflow-auto">{discovered.map((model, i) => { const repo = String(model.repo_id || model.modelId || model.id || model.name || ""); return <div key={`${repo}-${i}`} className="flex items-center gap-3 px-3 py-2.5"><div className="min-w-0 flex-1"><div className="truncate text-sm font-medium">{repo}</div><div className="text-xs text-muted-foreground">{model.downloads != null ? `${model.downloads.toLocaleString()} downloads` : ""}{model.likes != null ? ` · ${model.likes} likes` : ""}</div></div><Button size="sm" variant="outline" disabled={!repo || download.isPending} onClick={() => downloadRepo(repo, source === "ollama" ? "ollama" : "hf")}>Download</Button></div>})}{(source === "hf" ? hf.isLoading : ollama.isLoading) && <p className="p-4 text-sm text-muted-foreground">Loading catalog…</p>}</div>}
    </div>
  </section>
}

function DownloadForm() {
  const { download } = useCookbookMutations()
  const [repo, setRepo] = useState("")
  const [backend, setBackend] = useState("hf")
  const [msg, setMsg] = useState("")
  const go = () => {
    if (!repo.trim()) { setMsg("Repo ID required"); return }
    download.mutate({ repo_id: repo.trim(), backend }, {
      onSuccess: () => { setMsg(`Download started for ${repo.trim()} — track it under cached models.`); setRepo("") },
      onError: (e) => setMsg(e instanceof Error ? e.message : "Failed"),
    })
  }
  return (
    <section data-tour="cookbook-download">
      <h2 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground"><Download className="size-3.5" />Download a model</h2>
      <div className="space-y-2 rounded-lg border bg-card p-3">
        <div className="flex gap-2">
          <input value={repo} onChange={(e) => setRepo(e.target.value)} placeholder={backend === "ollama" ? "qwen2.5:0.5b" : "org/model-name (HF repo)"} className={cn(inp, "flex-1")} />
          <select value={backend} onChange={(e) => setBackend(e.target.value)} className={inp}><option value="hf">HuggingFace</option><option value="ollama">Ollama</option></select>
          <Button disabled={download.isPending} onClick={go}><Download className="size-4" />{download.isPending ? "Starting…" : "Download"}</Button>
        </div>
        {msg && <p className="text-xs text-muted-foreground">{msg}</p>}
      </div>
    </section>
  )
}

// ── Serve panel ─────────────────────────────────────────────────────────────
function ServeForm({ models, gpus }: { models: CachedModel[]; gpus: Gpu[] }) {
  const { serve } = useCookbookMutations()
  const { registerServeTask } = useRunningMutations()
  const [repoId, setRepoId] = useState("")
  const [backend, setBackend] = useState<ServeBackend>("vllm")
  const [gpuSel, setGpuSel] = useState("")     // CUDA_VISIBLE_DEVICES, e.g. "0" or "0,1"
  const [quant, setQuant] = useState("")
  const [maxLen, setMaxLen] = useState("")
  const [advanced, setAdvanced] = useState(false)
  const [remoteHost, setRemoteHost] = useState("")
  const [sshPort, setSshPort] = useState("22")
  const [tp, setTp] = useState("")
  const [gpuUtil, setGpuUtil] = useState("0.9")
  const [dtype, setDtype] = useState("auto")
  const [kvDtype, setKvDtype] = useState("")
  const [maxSeqs, setMaxSeqs] = useState("")
  const [toolParser, setToolParser] = useState("")
  const [speculative, setSpeculative] = useState("")
  const [msg, setMsg] = useState("")
  const [err, setErr] = useState(false)

  const selected = useMemo(() => models.find((m) => m.repo_id === repoId), [models, repoId])
  const profiles = useServeProfiles(repoId)

  const cmd = useMemo(() => {
    if (!repoId) return ""
    return buildServeCmd({
      backend,
      repoId,
      isLocalDir: selected?.is_local_dir,
      localPath: selected?.path,
      quant: quant.trim() || undefined,
      maxModelLen: maxLen.trim() || undefined,
      tensorParallel: tp.trim() || undefined,
      gpuMemoryUtilization: gpuUtil.trim() || undefined,
      dtype, kvCacheDtype: kvDtype.trim() || undefined, maxNumSeqs: maxSeqs.trim() || undefined,
      toolCallParser: toolParser.trim() || undefined, speculativeModel: speculative.trim() || undefined,
    })
  }, [backend, repoId, selected, quant, maxLen, tp, gpuUtil, dtype, kvDtype, maxSeqs, toolParser, speculative])

  const go = () => {
    if (!repoId) { setErr(true); setMsg("Pick a cached model to serve."); return }
    if (!cmd) { setErr(true); setMsg("Could not build a serve command."); return }
    setErr(false); setMsg("")
    serve.mutate(
      { repo_id: repoId, cmd, gpus: gpuSel.trim() || undefined, remote_host: remoteHost.trim() || undefined, ssh_port: remoteHost.trim() ? sshPort.trim() || undefined : undefined },
      {
        onSuccess: async (data) => {
          setErr(false)
          setMsg(`Serving ${repoId} — see Running below.`)
          // Register so the status poller surfaces it (local serves aren't
          // auto-adopted; remote ones are, but this keeps both consistent).
          if (data.session_id) {
            try {
              await registerServeTask({
                sessionId: data.session_id,
                type: "serve",
                modelId: repoId,
                name: repoId.split("/").pop() || repoId,
                remoteHost: data.remote && data.remote !== "local" ? data.remote : undefined,
                platform: undefined,
                status: "running",
                payload: { _cmd: cmd, repo_id: repoId, backend },
                createdAt: Date.now(),
              })
            } catch { /* status orphan-sweep may still adopt it */ }
          }
        },
        onError: (e) => { setErr(true); setMsg(e instanceof Error ? e.message : "Serve failed") },
      },
    )
  }

  const showQuant = backend === "llamacpp" || backend === "vllm"
  const showCtx = backend !== "ollama"

  return (
    <section data-tour="cookbook-serve">
      <h2 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground"><Play className="size-3.5" />Serve a model</h2>
      <div className="space-y-3 rounded-lg border bg-card p-3">
        {models.length === 0 ? (
          <p className="text-sm text-muted-foreground">No cached models to serve. Download one first.</p>
        ) : (
          <>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              <label className="flex flex-col gap-1 text-xs text-muted-foreground">Model
                <select value={repoId} onChange={(e) => setRepoId(e.target.value)} className={inp}>
                  <option value="">Select a cached model…</option>
                  {models.map((m) => <option key={m.repo_id} value={m.repo_id}>{m.repo_id}{m.size ? ` (${m.size})` : ""}</option>)}
                </select>
              </label>
              <label className="flex flex-col gap-1 text-xs text-muted-foreground">Backend
                <select value={backend} onChange={(e) => setBackend(e.target.value as ServeBackend)} className={inp}>
                  {SERVE_BACKENDS.map((b) => <option key={b.value} value={b.value}>{b.label}</option>)}
                </select>
              </label>
            </div>

            <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
              {gpus.length > 0 && (
                <label className="flex flex-col gap-1 text-xs text-muted-foreground">GPU(s)
                  <select value={gpuSel} onChange={(e) => setGpuSel(e.target.value)} className={inp}>
                    <option value="">Auto / all</option>
                    {gpus.map((g) => <option key={g.index} value={String(g.index)}>{g.index}: {g.name || `GPU ${g.index}`}</option>)}
                    {gpus.length > 1 && <option value={gpus.map((g) => g.index).join(",")}>All ({gpus.map((g) => g.index).join(",")})</option>}
                  </select>
                </label>
              )}
              {showQuant && (
                <label className="flex flex-col gap-1 text-xs text-muted-foreground">{backend === "llamacpp" ? "GGUF quant" : "Quantization"}
                  <input value={quant} onChange={(e) => setQuant(e.target.value)} placeholder={backend === "llamacpp" ? "Q4_K_M" : "awq / gptq…"} className={inp} />
                </label>
              )}
              {showCtx && (
                <label className="flex flex-col gap-1 text-xs text-muted-foreground">Max model len
                  <input value={maxLen} onChange={(e) => setMaxLen(e.target.value)} placeholder="8192" inputMode="numeric" className={inp} />
                </label>
              )}
            </div>
            <button onClick={() => setAdvanced((value) => !value)} className="text-xs font-medium text-muted-foreground hover:text-foreground">{advanced ? "Hide" : "Show"} advanced serving options</button>
            {advanced && <div className="grid gap-2 rounded-md border bg-muted/20 p-3 sm:grid-cols-2 lg:grid-cols-3">
              <label className="text-xs text-muted-foreground">Remote host<input value={remoteHost} onChange={(event) => setRemoteHost(event.target.value)} placeholder="user@host (optional)" className={cn(inp, "mt-1 w-full")} /></label>
              <label className="text-xs text-muted-foreground">SSH port<input value={sshPort} onChange={(event) => setSshPort(event.target.value)} placeholder="22" className={cn(inp, "mt-1 w-full")} /></label>
              <label className="text-xs text-muted-foreground">Tensor parallel<input value={tp} onChange={(event) => setTp(event.target.value)} placeholder="GPU count" className={cn(inp, "mt-1 w-full")} /></label>
              <label className="text-xs text-muted-foreground">GPU memory utilization<input value={gpuUtil} onChange={(event) => setGpuUtil(event.target.value)} placeholder="0.9" className={cn(inp, "mt-1 w-full")} /></label>
              <label className="text-xs text-muted-foreground">Dtype<select value={dtype} onChange={(event) => setDtype(event.target.value)} className={cn(inp, "mt-1 w-full")}><option value="auto">auto</option><option value="bfloat16">bfloat16</option><option value="float16">float16</option><option value="float32">float32</option></select></label>
              <label className="text-xs text-muted-foreground">KV cache dtype<input value={kvDtype} onChange={(event) => setKvDtype(event.target.value)} placeholder="auto / fp8" className={cn(inp, "mt-1 w-full")} /></label>
              <label className="text-xs text-muted-foreground">Max sequences<input value={maxSeqs} onChange={(event) => setMaxSeqs(event.target.value)} placeholder="256" className={cn(inp, "mt-1 w-full")} /></label>
              <label className="text-xs text-muted-foreground">Tool-call parser<input value={toolParser} onChange={(event) => setToolParser(event.target.value)} placeholder="hermes" className={cn(inp, "mt-1 w-full")} /></label>
              <label className="text-xs text-muted-foreground">Speculative model<input value={speculative} onChange={(event) => setSpeculative(event.target.value)} placeholder="draft model ID" className={cn(inp, "mt-1 w-full")} /></label>
            </div>}

            {cmd && (
              <div>
                <div className="mb-1 text-xs text-muted-foreground">Command preview</div>
                <pre className="max-h-32 overflow-auto whitespace-pre-wrap break-all rounded-md border bg-muted/40 p-2 font-mono text-[11px] leading-relaxed text-muted-foreground">{cmd}</pre>
              </div>
            )}
            {!!profiles.data?.profiles?.length && <div><div className="mb-1 text-xs text-muted-foreground">Hardware-aware profiles</div><div className="grid gap-2 sm:grid-cols-3">{profiles.data.profiles.map((profile, index) => <button key={String(profile.name || profile.label || index)} onClick={() => { const flags = profile.flags || profile; if (typeof flags.context === "number") setMaxLen(String(flags.context)) }} className="rounded-md border p-2 text-left hover:bg-accent"><span className="block text-xs font-medium">{String(profile.label || profile.name || `Profile ${index + 1}`)}</span><span className="mt-0.5 block text-[11px] text-muted-foreground">{String(profile.description || JSON.stringify(profile.flags || profile).slice(0, 90))}</span></button>)}</div></div>}

            <div className="flex items-center gap-2">
              <Button disabled={serve.isPending || !repoId} onClick={go}>
                {serve.isPending ? <><Loader2 className="size-4 animate-spin" />Starting…</> : <><Play className="size-4" />Serve</>}
              </Button>
              {msg && <p className={cn("text-xs", err ? "text-destructive" : "text-muted-foreground")}>{msg}</p>}
            </div>
          </>
        )}
      </div>
    </section>
  )
}

// ── Running tasks ────────────────────────────────────────────────────────────
function statusTone(status: string): string {
  switch (status) {
    case "ready": return "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
    case "running": return "bg-amber-500/15 text-amber-600 dark:text-amber-400"
    case "error": case "crashed": case "failed": return "bg-destructive/15 text-destructive"
    case "completed": return "bg-sky-500/15 text-sky-600 dark:text-sky-400"
    default: return "bg-muted text-muted-foreground"
  }
}

function RunningSection() {
  const { data, isLoading, isError } = useRunningTasks()
  const { stop, registerServeTask } = useRunningMutations()
  const { serve } = useCookbookMutations()
  const [stopping, setStopping] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)
  // Only show live serve/work — finished downloads clutter this view.
  const tasks = (data?.tasks || []).filter((t) => t.type === "serve" || !["completed", "stopped"].includes(t.status))

  const onStop = (t: RunningTask) => {
    setStopping(t.session_id)
    stop.mutate(
      { session_id: t.session_id, remote: t.remote, ssh_port: undefined },
      { onSettled: () => setStopping(null) },
    )
  }
  const onRestart = async (task: RunningTask) => {
    if (!task.cmd || !task.model) return
    try {
      await stop.mutateAsync({ session_id: task.session_id, remote: task.remote })
      const result = await serve.mutateAsync({ repo_id: task.model, cmd: task.cmd, remote_host: task.remote && task.remote !== "local" ? task.remote : undefined })
      if (result.session_id) await registerServeTask({ sessionId: result.session_id, type: "serve", modelId: task.model, name: task.model.split("/").pop(), remoteHost: task.remote, status: "running", payload: { _cmd: task.cmd, repo_id: task.model } })
    } catch { /* mutations surface their own error state */ }
  }

  return (
    <section data-tour="cookbook-running">
      <h2 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground"><Server className="size-3.5" />Running</h2>
      {isLoading ? <p className="text-sm text-muted-foreground">Checking running tasks…</p>
        : isError || !data?.ok ? <p className="rounded-lg border bg-card p-3 text-sm text-muted-foreground">Running-task status unavailable (admin only).</p>
        : tasks.length === 0 ? <p className="rounded-lg border bg-card p-3 text-sm text-muted-foreground">Nothing running. Serve a model above to see it here.</p>
        : (
          <div className="divide-y rounded-lg border bg-card">
            {tasks.map((t) => {
              const busy = stopping === t.session_id || (stop.isPending && stop.variables?.session_id === t.session_id)
              return (
                <div key={t.session_id} className="px-3 py-2.5"><div className="flex items-start gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm font-medium">{t.model || t.session_id}</span>
                      <span className={cn("shrink-0 rounded-full px-2 py-0.5 text-[11px]", statusTone(t.status))}>{t.status}</span>
                    </div>
                    <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                      <span>{t.type}</span>
                      <span>· {t.remote || "local"}</span>
                      {t.tps != null && <span className="inline-flex items-center gap-0.5"><Gauge className="size-3" />{t.tps} tok/s</span>}
                      {t.reqs != null && t.reqs > 0 && <span>· {t.reqs} req{t.reqs === 1 ? "" : "s"}</span>}
                      {t.pct != null && <span>· {t.pct}%</span>}
                      {t.progress && t.progress !== t.phase && <span className="truncate">· {t.progress}</span>}
                    </div>
                    {t.diagnosis?.message && (
                      <div className="mt-1 flex items-start gap-1 text-[11px] text-destructive">
                        <AlertTriangle className="mt-0.5 size-3 shrink-0" /><span className="break-words">{t.diagnosis.message}</span>
                      </div>
                    )}
                  </div>
                  <div className="flex shrink-0 gap-1">
                    {t.output_tail && <Button variant="ghost" size="sm" onClick={() => setExpanded(expanded === t.session_id ? null : t.session_id)}><FileText className="size-4" />Logs</Button>}
                    {t.cmd && <Button variant="ghost" size="sm" disabled={serve.isPending || busy} onClick={() => onRestart(t)}><RotateCcw className="size-4" />Restart</Button>}
                    <Button variant="ghost" size="sm" className="text-muted-foreground hover:text-destructive" disabled={busy} onClick={() => onStop(t)}>{busy ? <Loader2 className="size-4 animate-spin" /> : <Square className="size-4" />}Stop</Button>
                  </div>
                </div>{expanded === t.session_id && t.output_tail && <div className="relative mt-2 rounded-md bg-muted p-2"><button onClick={() => navigator.clipboard.writeText(t.output_tail || "")} className="absolute right-2 top-2 rounded p-1 text-muted-foreground hover:bg-accent"><Copy className="size-3.5" /></button><pre className="max-h-72 overflow-auto whitespace-pre-wrap pr-7 text-[11px]">{t.output_tail}</pre></div>}</div>
              )
            })}
          </div>
        )}
    </section>
  )
}

// ── What fits? (minimal) ─────────────────────────────────────────────────────
function WhatFits({ gpus }: { gpus: Gpu[] }) {
  const [sizeGb, setSizeGb] = useState("")
  const totalVram = gpus.reduce((s, g) => s + (g.total_mb || 0), 0) / 1024
  const freeVram = gpus.reduce((s, g) => s + (g.free_mb ?? (g.total_mb && g.used_mb != null ? g.total_mb - g.used_mb : 0)), 0) / 1024
  const sz = parseFloat(sizeGb)
  // Rough rule of thumb: weights + ~20% runtime/KV-cache overhead.
  const needed = Number.isFinite(sz) && sz > 0 ? sz * 1.2 : 0
  const verdict = needed === 0 ? null : needed <= freeVram ? "fits-free" : needed <= totalVram ? "fits-tight" : "no-fit"

  if (gpus.length === 0) return null
  return (
    <section data-tour="cookbook-whatfits">
      <h2 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground"><Gauge className="size-3.5" />What fits?</h2>
      <div className="space-y-2 rounded-lg border bg-card p-3">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-muted-foreground">Model size (GB on disk):</span>
          <input value={sizeGb} onChange={(e) => setSizeGb(e.target.value)} placeholder="e.g. 14" inputMode="decimal" className={cn(inp, "w-24")} />
        </div>
        <p className="text-xs text-muted-foreground">VRAM: {freeVram.toFixed(1)} GB free of {totalVram.toFixed(1)} GB total across {gpus.length} GPU{gpus.length === 1 ? "" : "s"}.</p>
        {verdict && (
          <p className={cn(
            "text-sm",
            verdict === "fits-free" ? "text-emerald-600 dark:text-emerald-400"
              : verdict === "fits-tight" ? "text-amber-600 dark:text-amber-400"
              : "text-destructive",
          )}>
            {verdict === "fits-free" && `Likely fits — needs ~${needed.toFixed(1)} GB (weights + ~20% overhead), and ${freeVram.toFixed(1)} GB is free now.`}
            {verdict === "fits-tight" && `Tight — needs ~${needed.toFixed(1)} GB; fits total VRAM but exceeds the ${freeVram.toFixed(1)} GB free right now. Stop other jobs or quantize.`}
            {verdict === "no-fit" && `Unlikely to fit — needs ~${needed.toFixed(1)} GB but only ${totalVram.toFixed(1)} GB total. Use a smaller/quantized model or multi-GPU.`}
          </p>
        )}
        <p className="text-[11px] text-muted-foreground/70">Rough estimate only — actual usage depends on context length, KV-cache dtype, and batch size.</p>
      </div>
    </section>
  )
}

function OperationsSection() {
  const manifest = useRecipeManifest()
  const setup = useCookbookSetup()
  const [recipeModel, setRecipeModel] = useState("")
  const [host, setHost] = useState("")
  const [port, setPort] = useState("22")
  const [setupOutput, setSetupOutput] = useState("")
  const recipe = useVllmRecipe(recipeModel)
  const recipeData = recipe.data || {}
  const dependencies = Array.isArray(recipeData.dependencies) ? recipeData.dependencies as Array<{ note?: string; command?: string; optional?: boolean }> : []
  const runSetup = () => setup.mutate({ host: host.trim(), ssh_port: port.trim() || undefined }, {
    onSuccess: (result) => setSetupOutput(result.output || `Setup complete (${result.platform || "remote"}).`),
    onError: (error) => setSetupOutput(error instanceof Error ? error.message : "Setup failed."),
  })
  return <section data-tour="cookbook-operations">
    <h2 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground"><Wrench className="size-3.5" />Provisioning &amp; recipes</h2>
    <div className="grid gap-3 lg:grid-cols-2">
      <div className="space-y-3 rounded-lg border bg-card p-3">
        <div><div className="text-sm font-medium">Remote host setup</div><p className="text-xs text-muted-foreground">Detect Linux, Windows, or Termux over SSH and install the model-download runtime.</p></div>
        <div className="flex gap-2"><input value={host} onChange={(event) => setHost(event.target.value)} placeholder="user@host" className={cn(inp, "min-w-0 flex-1")} /><input value={port} onChange={(event) => setPort(event.target.value)} placeholder="22" className={cn(inp, "w-20")} /><Button disabled={!host.trim() || setup.isPending} onClick={runSetup}>{setup.isPending ? <Loader2 className="size-4 animate-spin" /> : <Wrench className="size-4" />}Setup</Button></div>
        {setupOutput && <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded-md bg-muted p-2 text-[11px]">{setupOutput}</pre>}
        <a href="/v2/tasks" className="inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground"><CalendarClock className="size-3.5" />Schedule downloads and serving in Automations</a>
      </div>
      <div className="space-y-3 rounded-lg border bg-card p-3">
        <div><div className="flex items-center gap-1.5 text-sm font-medium"><PackageSearch className="size-4" />Official vLLM recipes</div><p className="text-xs text-muted-foreground">Dependencies, arguments, variants, and hardware overrides from the vLLM recipe catalog.</p></div>
        <select value={recipeModel} onChange={(event) => setRecipeModel(event.target.value)} className={cn(inp, "w-full")}><option value="">Select a recipe…</option>{(manifest.data?.models || []).map((model) => <option key={model} value={model}>{model}</option>)}</select>
        {recipeModel && recipe.isLoading && <p className="text-xs text-muted-foreground">Loading recipe…</p>}
        {recipeModel && recipeData.exists === false && <p className="text-xs text-muted-foreground">No recipe found for this model.</p>}
        {recipeData.exists === true && <div className="space-y-2 text-xs"><div className="font-medium">{String(recipeData.title || recipeModel)}</div>{recipeData.description ? <p className="text-muted-foreground">{String(recipeData.description)}</p> : null}{Array.isArray(recipeData.base_args) && <code className="block whitespace-pre-wrap rounded bg-muted p-2">{(recipeData.base_args as unknown[]).map(String).join(" ")}</code>}{dependencies.map((dep, index) => <div key={index} className="rounded-md border p-2"><div className="text-muted-foreground">{dep.note || (dep.optional ? "Optional dependency" : "Dependency")}</div>{dep.command && <code className="mt-1 block break-all">{dep.command}</code>}</div>)}</div>}
      </div>
    </div>
  </section>
}

export function CookbookRoute() {
  const { data: cached, isLoading: cl } = useCachedModels()
  const { data: gpu, isLoading: gl } = useGpus()
  const gpus = gpu?.gpus || []
  const models = cached?.models || []

  return (
    <div className="mx-auto flex h-full w-full max-w-4xl flex-col" data-tour="cookbook-root">
      <header className="flex h-13 shrink-0 items-center gap-2 border-b px-4 text-sm font-semibold"><FlaskConical className="size-4" />Cookbook</header>
      <div className="flex-1 space-y-6 overflow-y-auto p-4">
        <ModelDiscovery />
        <DownloadForm />
        <ServeForm models={models} gpus={gpus} />
        <RunningSection />

        <section data-tour="cookbook-hardware">
          <h2 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground"><Cpu className="size-3.5" />Hardware</h2>
          {gl ? <p className="text-sm text-muted-foreground">Probing GPUs…</p>
            : gpus.length === 0 ? <p className="rounded-lg border bg-card p-3 text-sm text-muted-foreground">{gpu?.error ? `No GPU probe available (${gpu.error}).` : "No GPUs detected."}</p>
            : (
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                {gpus.map((g) => {
                  const pct = g.total_mb ? Math.round(((g.used_mb ?? (g.total_mb - (g.free_mb || 0))) / g.total_mb) * 100) : 0
                  return (
                    <div key={g.index} className="rounded-lg border bg-card p-3">
                      <div className="flex items-center justify-between">
                        <span className="truncate text-sm font-medium">{g.name || `GPU ${g.index}`}</span>
                        {g.busy && <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">busy</span>}
                      </div>
                      <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-muted">
                        <div className={cn("h-full rounded-full", pct > 85 ? "bg-destructive" : "bg-primary")} style={{ width: `${pct}%` }} />
                      </div>
                      <div className="mt-1.5 flex justify-between text-xs text-muted-foreground">
                        <span>{gb(g.used_mb ?? (g.total_mb && g.free_mb != null ? g.total_mb - g.free_mb : undefined))} / {gb(g.total_mb)}</span>
                        {g.util_pct != null && <span>{g.util_pct}% util</span>}
                      </div>
                      {g.processes && g.processes.length > 0 && (
                        <div className="mt-2 space-y-0.5 border-t pt-1.5 text-[11px] text-muted-foreground">
                          {g.processes.slice(0, 4).map((p) => <div key={p.pid} className="flex justify-between gap-2"><span className="truncate">{p.name} ({p.pid})</span><span>{gb(p.used_mb)}</span></div>)}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
        </section>

        <WhatFits gpus={gpus} />
        <OperationsSection />

        <section data-tour="cookbook-cached">
          <h2 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground"><HardDrive className="size-3.5" />Cached models <span className="normal-case text-muted-foreground/70">· {cached?.host}</span></h2>
          {cl ? <p className="text-sm text-muted-foreground">Scanning cache…</p>
            : !cached?.ok ? <p className="rounded-lg border bg-card p-3 text-sm text-muted-foreground">Model cache unavailable (admin only, or no host configured).</p>
            : models.length === 0 ? <p className="rounded-lg border bg-card p-3 text-sm text-muted-foreground">No models cached.</p>
            : (
              <div className="divide-y rounded-lg border bg-card">
                {models.map((m) => (
                  <div key={m.repo_id} className="flex items-center gap-3 px-3 py-2.5">
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium">{m.repo_id}</div>
                      <div className="flex flex-wrap gap-1.5 text-xs text-muted-foreground">
                        <span>{m.size}</span>
                        {m.nb_files != null && <span>· {m.nb_files} files</span>}
                        {m.backend && <span>· {m.backend}</span>}
                        {m.is_gguf && <span>· GGUF</span>}
                        {m.is_ollama && <span>· Ollama</span>}
                        {m.is_diffusion && <span>· diffusion</span>}
                      </div>
                    </div>
                    <span className={cn("shrink-0 rounded-full px-2 py-0.5 text-[11px]", m.status === "downloading" ? "bg-amber-500/15 text-amber-600 dark:text-amber-400" : "bg-muted text-muted-foreground")}>{m.status || "ready"}</span>
                  </div>
                ))}
              </div>
            )}
        </section>
      </div>
    </div>
  )
}
