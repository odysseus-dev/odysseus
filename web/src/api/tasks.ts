import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { apiJson, apiFetch } from "@/lib/api"
import type { Task, TaskRun } from "@/types"

export interface TaskPayload {
  name?: string
  prompt?: string
  task_type?: "llm" | "research" | "action" | string
  action?: string
  schedule?: "once" | "daily" | "weekly" | "monthly" | "cron" | string
  scheduled_time?: string
  scheduled_day?: number
  scheduled_date?: string
  cron_expression?: string
  trigger_type?: "schedule" | "event" | "webhook" | string
  trigger_event?: string
  trigger_count?: number
  output_target?: string
  model?: string
  endpoint_url?: string
  then_task_id?: string
  notifications_enabled?: boolean
  character_id?: string
}

export interface TaskOutputTarget { value: string; label: string; description?: string }
export interface TaskActionMeta { name: string; description?: string }
export interface TaskEventMeta { name: string; description?: string }
export interface TaskDraftResponse { success?: boolean; draft?: TaskPayload; message?: string }
export interface TaskOnboarding { opened?: boolean; enabled?: boolean; resumed?: number }
export interface UrgentEmailSettings { urgent_email_prompt?: string; [key: string]: unknown }

export function useTasks() {
  return useQuery({
    queryKey: ["tasks"],
    queryFn: async () => (await apiJson<{ tasks: Task[] }>("/api/tasks?include_last_run=true")).tasks,
  })
}
export function useTaskRuns(taskId: string | null, open = true) {
  return useQuery({
    queryKey: ["task-runs", taskId],
    enabled: !!taskId && open,
    queryFn: async () => (await apiJson<{ runs: TaskRun[]; total?: number }>(`/api/tasks/${taskId}/runs?limit=20`)).runs,
  })
}
export function useRecentTaskRuns(enabled = true) {
  return useQuery({
    queryKey: ["task-runs", "recent"],
    enabled,
    queryFn: async () => (await apiJson<{ runs: TaskRun[] }>("/api/tasks/runs/recent?limit=100")).runs,
  })
}
export function useTaskOutputTargets() {
  return useQuery({
    queryKey: ["tasks", "meta", "output-targets"],
    queryFn: async () => (await apiJson<{ targets: TaskOutputTarget[] }>("/api/tasks/meta/output-targets")).targets,
  })
}
export function useTaskActions() {
  return useQuery({
    queryKey: ["tasks", "meta", "actions"],
    queryFn: async () => (await apiJson<{ actions: TaskActionMeta[] }>("/api/tasks/meta/actions")).actions,
  })
}
export function useTaskEvents() {
  return useQuery({
    queryKey: ["tasks", "meta", "events"],
    queryFn: async () => (await apiJson<{ events: TaskEventMeta[] }>("/api/tasks/meta/events")).events,
  })
}
export function useTasksOnboarding() {
  return useQuery({
    queryKey: ["tasks", "onboarding"],
    queryFn: () => apiJson<TaskOnboarding>("/api/tasks/onboarding"),
  })
}
export function useUrgentEmailSettings(enabled = true) {
  return useQuery({
    queryKey: ["auth", "settings", "urgent-email"],
    enabled,
    queryFn: () => apiJson<UrgentEmailSettings>("/api/auth/settings"),
  })
}
export function useTaskMutations() {
  const qc = useQueryClient()
  const inv = () => {
    qc.invalidateQueries({ queryKey: ["tasks"] })
    qc.invalidateQueries({ queryKey: ["task-runs"] })
  }
  const post = (id: string, action: string, qs = "") => apiFetch(`/api/tasks/${id}/${action}${qs}`, { method: "POST" })
  const saveJson = async (path: string, method: "POST" | "PUT", payload: TaskPayload) => {
    const r = await apiFetch(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })
    if (!r.ok) {
      const e = await r.json().catch(() => ({}))
      throw new Error(e.detail || e.message || `Task save failed (${r.status})`)
    }
    return r.json() as Promise<Task>
  }
  return {
    create: useMutation({
      mutationFn: async (v: TaskPayload) => saveJson("/api/tasks", "POST", { task_type: "llm", trigger_type: "schedule", output_target: "session", ...v }),
      onSuccess: inv,
      meta: { silent: true },
    }),
    update: useMutation({
      mutationFn: async (v: { id: string; payload: TaskPayload }) => saveJson(`/api/tasks/${v.id}`, "PUT", v.payload),
      onSuccess: inv,
      meta: { silent: true },
    }),
    parse: useMutation({
      mutationFn: async (description: string) => {
        const r = await apiFetch("/api/tasks/parse", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ description }),
        })
        const data = await r.json().catch(() => ({}))
        if (!r.ok || data.success === false) throw new Error(data.message || data.detail || "Couldn't draft task")
        return data as TaskDraftResponse
      },
      meta: { silent: true },
    }),
    saveUrgentEmailSettings: useMutation({
      mutationFn: async (prompt: string) => {
        const r = await apiFetch("/api/auth/settings", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ urgent_email_prompt: prompt || "" }),
        })
        if (!r.ok) throw new Error("Failed to save email triage rules")
        return r.json().catch(() => ({}))
      },
      onSuccess: () => qc.invalidateQueries({ queryKey: ["auth", "settings", "urgent-email"] }),
      meta: { silent: true },
    }),
    markOnboarding: useMutation({
      mutationFn: async (enabled: boolean) => {
        const r = await apiFetch("/api/tasks/onboarding", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled }),
        })
        if (!r.ok) throw new Error("Couldn't update task onboarding")
        return r.json() as Promise<TaskOnboarding>
      },
      onSuccess: () => {
        qc.invalidateQueries({ queryKey: ["tasks"] })
        qc.invalidateQueries({ queryKey: ["tasks", "onboarding"] })
      },
      meta: { silent: true },
    }),
    run: useMutation({ mutationFn: async (v: string | { id: string; force?: boolean }) => {
      const id = typeof v === "string" ? v : v.id
      const force = typeof v === "string" ? false : !!v.force
      const r = await post(id, "run", force ? "?force=true" : "")
      if (!r.ok) {
        const e = await r.json().catch(() => ({}))
        throw new Error(e.detail || "Couldn't run the task")
      }
    }, onSuccess: inv }),
    stop: useMutation({ mutationFn: async (id: string) => {
      const r = await post(id, "stop")
      if (!r.ok) {
        const e = await r.json().catch(() => ({}))
        throw new Error(e.detail || "Couldn't stop the task")
      }
    }, onSuccess: inv }),
    pause: useMutation({ mutationFn: async (id: string) => { const r = await post(id, "pause"); if (!r.ok) throw new Error("Couldn't pause the task") }, onSuccess: inv }),
    resume: useMutation({ mutationFn: async (id: string) => { const r = await post(id, "resume"); if (!r.ok) throw new Error("Couldn't resume the task") }, onSuccess: inv }),
    revert: useMutation({ mutationFn: async (id: string) => { const r = await post(id, "revert"); if (!r.ok) throw new Error("Couldn't revert the task") }, onSuccess: inv }),
    clearCache: useMutation({ mutationFn: async (id: string) => {
      const r = await post(id, "clear-cache")
      const data = await r.json().catch(() => ({}))
      if (!r.ok || data.ok === false) throw new Error(data.detail || data.error || "Couldn't clear cache")
      return data as { ok?: boolean; cleared?: Record<string, number>; files?: number }
    }, onSuccess: inv }),
    remove: useMutation({ mutationFn: async (id: string) => { const r = await apiFetch(`/api/tasks/${id}`, { method: "DELETE" }); if (!r.ok) throw new Error("Couldn't delete the task") }, onSuccess: inv }),
  }
}
