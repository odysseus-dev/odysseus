import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { apiJson, apiFetch } from "@/lib/api"

export function useCookbookMutations() {
  const qc = useQueryClient()
  const inv = () => qc.invalidateQueries({ queryKey: ["cookbook", "cached"] })
  return {
    download: useMutation({
      mutationFn: async (v: { repo_id: string; backend: string }) => {
        const r = await apiFetch("/api/model/download", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(v) })
        if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || "Download failed to start") }
        return r.json()
      },
      onSuccess: inv,
    }),
    serve: useMutation({
      mutationFn: async (v: ServeVars) => {
        // Drop empty optionals so the backend ServeRequest validators
        // (validate_remote_host / _validate_gpus / token) see undefined, not "".
        const body: ServeVars = { repo_id: v.repo_id, cmd: v.cmd }
        if (v.remote_host) body.remote_host = v.remote_host
        if (v.ssh_port) body.ssh_port = v.ssh_port
        if (v.env_prefix) body.env_prefix = v.env_prefix
        if (v.hf_token) body.hf_token = v.hf_token
        if (v.gpus) body.gpus = v.gpus
        if (v.platform) body.platform = v.platform
        const r = await apiFetch("/api/model/serve", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
        if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || "Serve failed to start") }
        // /api/model/serve returns { ok, session_id, remote, endpoint_id } — but
        // ok:false is reported in a 200 body (e.g. missing tmux/docker), so surface it.
        const data = (await r.json()) as ServeResult
        if (data.ok === false) throw new Error(data.error || "Serve failed to start")
        return data
      },
    }),
  }
}

export interface ServeVars {
  repo_id: string
  cmd: string
  remote_host?: string
  ssh_port?: string
  env_prefix?: string
  hf_token?: string
  gpus?: string
  platform?: string
}
export interface ServeResult {
  ok?: boolean
  session_id?: string
  remote?: string
  endpoint_id?: string | null
  error?: string
}

export interface CachedModel {
  repo_id: string; size?: string; nb_files?: number; status?: string; path?: string;
  backend?: string; is_gguf?: boolean; is_ollama?: boolean; is_diffusion?: boolean; is_local_dir?: boolean;
}
export interface Gpu {
  index: number; name?: string; free_mb?: number; total_mb?: number; used_mb?: number;
  util_pct?: number; busy?: boolean; processes?: { pid: number; name: string; used_mb: number }[];
}

export function useCachedModels() {
  return useQuery({
    queryKey: ["cookbook", "cached"],
    retry: false,
    staleTime: 30_000,
    queryFn: async () => {
      try {
        const r = await apiJson<{ models?: CachedModel[]; host?: string }>("/api/model/cached")
        return { models: r.models || [], host: r.host || "local", ok: true }
      } catch { return { models: [] as CachedModel[], host: "local", ok: false } }
    },
  })
}

export function useGpus() {
  return useQuery({
    queryKey: ["cookbook", "gpus"],
    retry: false,
    staleTime: 15_000,
    queryFn: async () => {
      try {
        return await apiJson<{ ok: boolean; gpus?: Gpu[]; error?: string; backend?: string; source?: string }>("/api/cookbook/gpus")
      } catch { return { ok: false, gpus: [] as Gpu[], error: "unavailable" } }
    },
  })
}

export interface HwfitSystem {
  hostname?: string; platform?: string; backend?: string; gpu_name?: string; gpu_count?: number; gpu_vram_gb?: number;
  total_ram_gb?: number; available_ram_gb?: number; error?: string;
}
export interface HwfitModel {
  name: string; provider?: string; parameter_count?: string; params_b?: number; use_case?: string;
  fit_level?: "perfect" | "good" | "marginal" | "too_tight" | string; run_mode?: string;
  quant?: string; context?: number; required_gb?: number; speed_tps?: number; score?: number;
  release_date?: string; description?: string; gguf_sources?: unknown[];
}
export interface HwfitQuery { search?: string; useCase?: string; sort?: string; fitOnly?: boolean; limit?: number }

export function useHwfitModels(query: HwfitQuery = {}) {
  return useQuery({
    queryKey: ["cookbook", "hwfit", query],
    staleTime: 30_000,
    queryFn: async () => {
      const qs = new URLSearchParams({ limit: String(query.limit || 100), sort: query.sort || "score" })
      if (query.search) qs.set("search", query.search)
      if (query.useCase) qs.set("use_case", query.useCase)
      if (query.fitOnly) qs.set("fit_only", "true")
      return apiJson<{ system?: HwfitSystem; models?: HwfitModel[]; error?: string }>(`/api/hwfit/models?${qs}`)
    },
  })
}

export interface DiscoveryModel { id?: string; modelId?: string; name?: string; repo_id?: string; downloads?: number; likes?: number; estimated_vram_gb?: number; tags?: string[] }
export function useHfLatest(enabled = true) {
  return useQuery({ queryKey: ["cookbook", "hf-latest"], enabled, staleTime: 300_000, queryFn: async () => (await apiJson<{ models?: DiscoveryModel[] }>("/api/cookbook/hf-latest?limit=20")).models || [] })
}
export function useOllamaLibrary(enabled = true) {
  return useQuery({ queryKey: ["cookbook", "ollama-library"], enabled, staleTime: 300_000, queryFn: async () => (await apiJson<{ models?: DiscoveryModel[] }>("/api/cookbook/ollama/library")).models || [] })
}
export interface ServeProfile { name?: string; label?: string; description?: string; flags?: Record<string, unknown>; [key: string]: unknown }
export function useServeProfiles(model: string) {
  return useQuery({ queryKey: ["cookbook", "profiles", model], enabled: !!model, retry: false, queryFn: async () => apiJson<{ profiles?: ServeProfile[]; model_ctx_max?: number; error?: string }>(`/api/hwfit/profiles?model=${encodeURIComponent(model)}`) })
}
export function useImageFit(search = "") {
  return useQuery({ queryKey: ["cookbook", "image-fit", search], staleTime: 30_000, queryFn: async () => apiJson<{ system?: HwfitSystem; models?: Array<Record<string, unknown>>; error?: string }>(`/api/hwfit/image-models?search=${encodeURIComponent(search)}&sort=fit`) })
}
export function useRecipeManifest() {
  return useQuery({ queryKey: ["cookbook", "recipe-manifest"], staleTime: 12 * 3600_000, retry: false, queryFn: () => apiJson<{ models?: string[]; count?: number; error?: string }>("/api/cookbook/vllm-recipe-manifest") })
}
export function useVllmRecipe(model: string) {
  return useQuery({ queryKey: ["cookbook", "recipe", model], enabled: !!model, retry: false, queryFn: () => apiJson<Record<string, unknown>>(`/api/cookbook/vllm-recipe?repo=${encodeURIComponent(model)}`) })
}
export function useCookbookSetup() {
  return useMutation({ mutationFn: async (v: { host: string; ssh_port?: string }) => {
    const r = await apiFetch("/api/cookbook/setup", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(v) })
    const data = await r.json().catch(() => ({}))
    if (!r.ok || data.ok === false) throw new Error(data.error || data.output || "Setup failed")
    return data as { ok: boolean; output?: string; platform?: string }
  } })
}

// ── Running serve/download tasks ──────────────────────────────────────────
// Shape mirrors the per-task dict returned by GET /api/cookbook/tasks/status
// (see cookbook_routes.py ~3145). Serve tasks run in a tmux session; the
// status reader keys off `session_id` (no PID is exposed here).
export interface RunningTask {
  session_id: string
  type: "serve" | "download" | string
  model: string
  status: string            // ready | running | stopped | error | completed | unknown
  progress?: string
  phase?: string
  diagnosis?: { message?: string } | null
  output_tail?: string
  exit_code?: number | null
  cmd?: string
  tps?: number | null
  reqs?: number | null
  pct?: number | null
  remote?: string           // "local" or a host
}

// A persisted cookbook-state task entry. The status endpoint reads tasks from
// server-side cookbook state; a freshly-launched local serve only appears once
// it has been registered there. (Remote serves are also auto-adopted by the
// backend's orphan sweep, but registering keeps local + remote consistent.)
interface StateTask {
  sessionId: string
  type: "serve" | "download"
  modelId?: string
  name?: string
  remoteHost?: string
  sshPort?: string
  platform?: string
  status?: string
  payload?: { _cmd?: string; repo_id?: string; backend?: string }
  createdAt?: number
}

const RUNNING_KEY = ["cookbook", "running"] as const

export function useRunningTasks(enabled = true) {
  return useQuery({
    queryKey: RUNNING_KEY,
    enabled,
    retry: false,
    // Poll on an interval while mounted (status reflects tmux/log state that
    // changes as a serve warms up). Matches the legacy ~3s monitor cadence.
    refetchInterval: 4000,
    refetchOnWindowFocus: true,
    queryFn: async () => {
      try {
        const r = await apiJson<{ tasks?: RunningTask[] }>("/api/cookbook/tasks/status")
        return { tasks: r.tasks || [], ok: true }
      } catch { return { tasks: [] as RunningTask[], ok: false } }
    },
  })
}

// Register a launched serve into server-side cookbook state so the status
// poller picks it up. Read-modify-write the whole state blob; the backend POST
// handler has anti-wipe merge guards, so appending one task is safe.
async function registerServeTask(task: StateTask) {
  const state: Record<string, unknown> = (await apiJson<Record<string, unknown>>("/api/cookbook/state").catch(() => ({}))) || {}
  const prev = Array.isArray(state.tasks) ? (state.tasks as StateTask[]) : []
  if (prev.some((t) => t?.sessionId === task.sessionId)) return  // already present
  const next = { ...state, tasks: [...prev, task] }
  await apiFetch("/api/cookbook/state", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(next),
  })
}

export function useRunningMutations() {
  const qc = useQueryClient()
  const invRunning = () => qc.invalidateQueries({ queryKey: RUNNING_KEY })
  return {
    registerServeTask,
    // Stop a serve task. Serve tasks are tmux sessions (no PID in the status
    // payload), so the real stop is `tmux kill-session` via /api/shell/exec —
    // exactly what the legacy UI does. session_id is backend-validated as
    // `serve-<hex>` so it is safe to interpolate.
    stop: useMutation({
      mutationFn: async (t: { session_id: string; remote?: string; ssh_port?: string }) => {
        const sid = t.session_id
        const isRemote = !!t.remote && t.remote !== "local"
        const tmux = `tmux send-keys -t ${sid} C-c 2>/dev/null; sleep 2; tmux kill-session -t ${sid} 2>/dev/null`
        const pf = t.ssh_port && t.ssh_port !== "22" ? `-p ${t.ssh_port} ` : ""
        const command = isRemote
          ? `ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no ${pf}${t.remote} '${tmux}'`
          : tmux
        const r = await apiFetch("/api/shell/exec", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ command }),
        })
        if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || "Stop failed") }
        return r.json()
      },
      onSuccess: invRunning,
    }),
    // Kill a GPU-holding PID directly (e.g. a process surfaced by the GPU
    // probe that survived a session kill). POST /api/cookbook/kill-pid.
    killPid: useMutation({
      mutationFn: async (v: { pid: number; host?: string; ssh_port?: string; signal?: "TERM" | "KILL" | "INT" }) => {
        const r = await apiFetch("/api/cookbook/kill-pid", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(v),
        })
        if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || "Kill failed") }
        const data = (await r.json()) as { ok?: boolean; error?: string }
        if (data.ok === false) throw new Error(data.error || "Kill failed")
        return data
      },
      onSuccess: invRunning,
    }),
  }
}

// ── Serve command builder ─────────────────────────────────────────────────
// Mirrors the per-backend launch commands the legacy Cookbook builds
// (static/js/cookbook-hwfit.js ~1670 and cookbookServe.js). Kept minimal: the
// goal is a correct, runnable command preview, not the full advanced-options
// surface (TP, KV-cache dtype, speculative decoding, etc. are deferred).
export type ServeBackend = "vllm" | "sglang" | "llamacpp" | "ollama" | "diffusers"

export const SERVE_BACKENDS: { value: ServeBackend; label: string }[] = [
  { value: "vllm", label: "vLLM" },
  { value: "llamacpp", label: "llama.cpp" },
  { value: "ollama", label: "Ollama" },
  { value: "sglang", label: "SGLang" },
  { value: "diffusers", label: "Diffusers" },
]

export interface ServeOpts {
  backend: ServeBackend
  repoId: string
  isLocalDir?: boolean
  localPath?: string
  quant?: string          // e.g. Q4_K_M for GGUF (llama.cpp), or vLLM --quantization
  maxModelLen?: string    // context length
  port?: string
  tensorParallel?: string
  gpuMemoryUtilization?: string
  dtype?: string
  kvCacheDtype?: string
  maxNumSeqs?: string
  toolCallParser?: string
  speculativeModel?: string
  trustRemoteCode?: boolean
  enforceEager?: boolean
}

export function buildServeCmd(o: ServeOpts): string {
  const { backend, repoId } = o
  const model = o.isLocalDir && o.localPath ? `${o.localPath}/${repoId}` : repoId
  const ctx = (o.maxModelLen || "").trim()
  const port = (o.port || "").trim()
  if (backend === "sglang") {
    let cmd = `python3 -m sglang.launch_server --model-path ${model} --host 0.0.0.0 --port ${port || "8000"}`
    if (ctx) cmd += ` --context-length ${ctx}`
    cmd += " --trust-remote-code"
    return cmd
  }
  if (backend === "llamacpp") {
    // Resolve the GGUF file at runtime from the HF snapshots dir (or a local
    // dir). The quant string narrows the glob when provided (e.g. *Q4_K_M*).
    const dir = o.isLocalDir && o.localPath
      ? `"${o.localPath}/${repoId}"`
      : `"$HOME/.cache/huggingface/hub/models--${repoId.replace(/\//g, "--")}/snapshots"`
    const q = (o.quant || "").trim()
    const pat = q ? `*${q}*` : "*"
    const find = `$({ find ${dir} -iname '${pat}-00001-of-*.gguf' 2>/dev/null | sort; find ${dir} -iname '${pat}.gguf' 2>/dev/null | sort; } | head -1)`
    const p = port || "8080"
    return `MODEL_FILE=${find} && { [ -n "$MODEL_FILE" ] && [ -f "$MODEL_FILE" ]; } || { echo "ERROR: No GGUF found on this host. Download a GGUF quant or switch backend."; exit 1; } && llama-server --model "$MODEL_FILE" --host 0.0.0.0 --port ${p} -ngl 99${ctx ? ` -c ${ctx}` : ""} || python3 -m llama_cpp.server --model "$MODEL_FILE" --host 0.0.0.0 --port ${p} --n_gpu_layers 99${ctx ? ` --n_ctx ${ctx}` : ""}`
  }
  if (backend === "ollama") {
    // repoId is an ollama tag (e.g. qwen2.5:0.5b) for ollama-served models.
    return `ollama serve & sleep 2 && ollama run ${repoId}`
  }
  if (backend === "diffusers") {
    return `python3 scripts/diffusion_server.py --model ${model} --host 0.0.0.0 --port ${port || "8000"}`
  }
  // vLLM (default)
  let cmd = `vllm serve ${model} --host 0.0.0.0 --port ${port || "8000"}`
  if (ctx) cmd += ` --max-model-len ${ctx}`
  if ((o.quant || "").trim()) cmd += ` --quantization ${o.quant!.trim()}`
  if (o.tensorParallel?.trim()) cmd += ` --tensor-parallel-size ${o.tensorParallel.trim()}`
  if (o.gpuMemoryUtilization?.trim()) cmd += ` --gpu-memory-utilization ${o.gpuMemoryUtilization.trim()}`
  if (o.maxNumSeqs?.trim()) cmd += ` --max-num-seqs ${o.maxNumSeqs.trim()}`
  if (o.kvCacheDtype?.trim()) cmd += ` --kv-cache-dtype ${o.kvCacheDtype.trim()}`
  if (o.toolCallParser?.trim()) cmd += ` --enable-auto-tool-choice --tool-call-parser ${o.toolCallParser.trim()}`
  if (o.speculativeModel?.trim()) cmd += ` --speculative-model ${o.speculativeModel.trim()}`
  cmd += ` --dtype ${o.dtype?.trim() || "auto"}`
  if (o.enforceEager !== false) cmd += " --enforce-eager"
  if (o.trustRemoteCode !== false) cmd += " --trust-remote-code"
  return cmd
}
