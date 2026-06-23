import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { apiJson, apiFetch } from "@/lib/api"
import type { Skill as BaseSkill, BuiltinSkill } from "@/types"

// Extended view of a skill that includes the usage/audit metadata the backend
// returns from GET /api/skills (uses, last_used, audit_verdict, …). The shared
// `Skill` type in @/types is the minimal contract; we widen it locally rather
// than editing the shared file.
// Omit `audit_verdict` from the base type so we can widen it to allow null
// (the backend returns null when a skill has never been audited).
export interface SkillRow extends Omit<BaseSkill, "audit_verdict"> {
  category?: string
  tags?: string[]
  uses?: number
  last_used?: number | null
  audit_verdict?: string | null
  audited_at?: number | null
  created?: string | null
}

export function useSkills() {
  return useQuery({ queryKey: ["skills"], queryFn: async () => (await apiJson<{ skills: SkillRow[] }>("/api/skills")).skills })
}
export function useBuiltinSkills() {
  return useQuery({ queryKey: ["builtin-skills"], queryFn: async () => (await apiJson<{ builtin: BuiltinSkill[] }>("/api/skills/builtin")).builtin })
}

export interface SlashCommand { token: string; name: string; help?: string; usage?: string; category?: string }
export function useSlashCatalog() {
  return useQuery({
    queryKey: ["slash-catalog"],
    staleTime: 60_000,
    retry: false,
    queryFn: async () => {
      try { return (await apiJson<{ skills?: SlashCommand[] }>("/api/skills/slash-catalog")).skills || [] }
      catch { return [] as SlashCommand[] }
    },
  })
}
// Expand a /skill slash command into the model-facing prompt to actually send.
export async function invokeSkill(name: string, request: string): Promise<string | null> {
  const r = await apiFetch(`/api/skills/${encodeURIComponent(name)}/invoke`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ request }),
  })
  if (!r.ok) return null
  const j = await r.json()
  return (j.message as string) || null
}
export function useSkillMarkdown(id: string | null) {
  return useQuery({
    queryKey: ["skill-md", id],
    enabled: !!id,
    queryFn: () => apiJson<{ name: string; markdown: string }>(`/api/skills/${id}/markdown`),
  })
}

// ── Run / invoke a skill ────────────────────────────────────────────────────
// Expands a skill into a skill-pinned prompt against the user's request. This
// only returns the prompt text the model would receive (today's behaviour);
// there is no backend endpoint that runs the prompt through a model and returns
// a completion, so the run panel renders this expanded prompt.
export function useRunSkill() {
  return useMutation({
    mutationFn: async (v: { id: string; request: string }) => {
      const r = await apiFetch(`/api/skills/${encodeURIComponent(v.id)}/invoke`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ request: v.request }),
      })
      if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || "Run failed") }
      return r.json() as Promise<{ ok: boolean; type: string; name: string; command: string; message: string }>
    },
    meta: { silent: true },
  })
}

// ── Single-skill test (background job + polling) ─────────────────────────────
export interface SkillVerdict {
  verdict: "pass" | "needs_work" | "fail" | "inconclusive" | "unknown"
  confidence: number
  summary?: string
  issues?: string[]
}
export interface SkillTestLogEntry {
  type: string
  text?: string
  tool?: string
  command?: string
  output?: string
  round?: number
  task?: string
  skill?: string
  model?: string
  error?: string
}
export interface SkillTestStatus {
  status: "none" | "running" | "done"
  task?: string
  model?: string
  log?: SkillTestLogEntry[]
  verdict?: SkillVerdict | null
}

export function useStartSkillTest() {
  return useMutation({
    mutationFn: async (id: string) => {
      const r = await apiFetch(`/api/skills/${encodeURIComponent(id)}/test`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}),
      })
      if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || "Test failed to start") }
      return r.json() as Promise<{ ok: boolean; status: string; skill: string; model: string }>
    },
    meta: { silent: true }, // TestPanel renders start.error inline
  })
}

export function useSkillTestStatus(id: string | null, poll: boolean) {
  return useQuery({
    queryKey: ["skill-test-status", id],
    enabled: !!id,
    refetchInterval: poll ? 1500 : false,
    queryFn: () => apiJson<SkillTestStatus>(`/api/skills/${encodeURIComponent(id as string)}/test-status`),
  })
}

// ── Audit-all (bulk) progress + cancel ──────────────────────────────────────
export interface AuditResult {
  skill: string
  result: "skipped" | "pass" | "inconclusive" | "pass_after_self_edit" | "pass_after_teacher" | "flagged" | "error"
  reason?: string
  confidence?: number
  status?: string
  verdict?: SkillVerdict | null
  skill_state?: { name?: string; status?: string; confidence?: number; audit_verdict?: string }
}
export interface AuditAllStatus {
  status: "none" | "running" | "done" | "cancelled"
  scope?: string
  total?: number
  done?: number
  current?: string | null
  model?: string
  teacher?: string | null
  results?: AuditResult[]
  log?: string[]
  started?: number
  finished?: number
}

export function useAuditAllStatus(poll: boolean) {
  return useQuery({
    queryKey: ["skill-audit-status"],
    refetchInterval: poll ? 1500 : false,
    queryFn: () => apiJson<AuditAllStatus>("/api/skills/audit-all/status"),
  })
}

export function useCancelAuditAll() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      const r = await apiFetch("/api/skills/audit-all/cancel", { method: "POST" })
      if (!r.ok) throw new Error("cancel failed")
      return r.json()
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["skill-audit-status"] }); qc.invalidateQueries({ queryKey: ["skills"] }) },
    meta: { silent: true },
  })
}

// ── Built-in tool override (admin-gated PUT/DELETE) ──────────────────────────
export interface BuiltinDetail { name: string; text: string; default: string; is_overridden: boolean }
export function useBuiltinSkill(name: string | null) {
  return useQuery({
    queryKey: ["builtin-skill", name],
    enabled: !!name,
    queryFn: () => apiJson<BuiltinDetail>(`/api/skills/builtin/${encodeURIComponent(name as string)}`),
  })
}

export function useSkillMutations() {
  const qc = useQueryClient()
  const inv = () => qc.invalidateQueries({ queryKey: ["skills"] })
  const invBuiltin = (name?: string) => {
    qc.invalidateQueries({ queryKey: ["builtin-skills"] })
    if (name) qc.invalidateQueries({ queryKey: ["builtin-skill", name] })
  }
  return {
    importFromUrl: useMutation({
      mutationFn: async (url: string) => {
        const r = await apiFetch("/api/skills/import-from-url", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url }),
        })
        if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || "Import failed") }
        return r.json() as Promise<{ ok: boolean; skill: BaseSkill; files: number }>
      },
      onSuccess: inv,
      meta: { silent: true },
    }),
    saveBuiltinOverride: useMutation({
      mutationFn: async (v: { name: string; text: string }) => {
        const r = await apiFetch(`/api/skills/builtin/${encodeURIComponent(v.name)}`, {
          method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: v.text }),
        })
        if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || "Save failed") }
        return r.json()
      },
      onSuccess: (_d, v) => invBuiltin(v.name),
      meta: { silent: true },
    }),
    resetBuiltinOverride: useMutation({
      mutationFn: async (name: string) => {
        const r = await apiFetch(`/api/skills/builtin/${encodeURIComponent(name)}`, { method: "DELETE" })
        if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || "Reset failed") }
        return r.json()
      },
      onSuccess: (_d, name) => invBuiltin(name),
      meta: { silent: true },
    }),
    remove: useMutation({
      mutationFn: async (id: string) => { await apiFetch(`/api/skills/${id}`, { method: "DELETE" }) },
      onSuccess: inv,
    }),
    saveMarkdown: useMutation({
      mutationFn: async (v: { id: string; markdown: string }) => {
        const r = await apiFetch(`/api/skills/${v.id}/markdown`, {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ markdown: v.markdown }),
        })
        if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || "Save failed") }
        return r.json()
      },
      onSuccess: (_d, v) => { qc.invalidateQueries({ queryKey: ["skill-md", v.id] }); inv() },
      meta: { silent: true },
    }),
    // Publish / unpublish (or any status flip) via PUT /api/skills/{id}.
    setStatus: useMutation({
      mutationFn: async (v: { id: string; status: "published" | "draft" }) => {
        const r = await apiFetch(`/api/skills/${encodeURIComponent(v.id)}`, {
          method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: v.status }),
        })
        if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || "Update failed") }
        return r.json()
      },
      onSuccess: inv,
      meta: { silent: true },
    }),
    auditAll: useMutation({
      // Pass {names} to audit a selected subset (scope "selected" audits even
      // already-published skills); omit for a full audit of every skill.
      mutationFn: async (v?: { names?: string[]; scope?: string }) => {
        const body = v?.names?.length ? { scope: v.scope || "selected", names: v.names } : { scope: v?.scope || "all" }
        const r = await apiFetch("/api/skills/audit-all", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
        if (!r.ok) throw new Error("audit failed"); return r.json() as Promise<{ ok: boolean; status: string; total: number; model?: string }>
      },
      onSuccess: inv,
      meta: { silent: true },
    }),
    create: useMutation({
      mutationFn: async (v: { name: string; description: string; procedure: string; when_to_use?: string }) => {
        const r = await apiFetch("/api/skills/add", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(v),
        })
        if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || "Create failed") }
        return r.json()
      },
      onSuccess: inv,
      meta: { silent: true },
    }),
  }
}
