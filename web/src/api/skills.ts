import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { apiJson, apiFetch } from "@/lib/api"
import type { Skill, BuiltinSkill } from "@/types"

export function useSkills() {
  return useQuery({ queryKey: ["skills"], queryFn: async () => (await apiJson<{ skills: Skill[] }>("/api/skills")).skills })
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
        return r.json() as Promise<{ ok: boolean; skill: Skill; files: number }>
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
    auditAll: useMutation({
      mutationFn: async () => {
        const r = await apiFetch("/api/skills/audit-all", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ scope: "all" }) })
        if (!r.ok) throw new Error("audit failed"); return r.json()
      },
      onSuccess: inv,
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
