import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { apiJson, apiFetch } from "@/lib/api"
import type { Memory } from "@/types"

export interface MemoryImportSuggestion { text: string; category?: string }
export interface MemoryImportResult { suggestions?: (MemoryImportSuggestion | string)[]; filename?: string; message?: string }
export interface MemoryAuditResult { ok?: boolean; before?: number; after?: number; removed?: number; already_tidy?: boolean }

export function useMemory() {
  return useQuery({
    queryKey: ["memory"],
    queryFn: async () => (await apiJson<{ memory: Memory[] }>("/api/memory")).memory,
  })
}
export function useMemoryMutations() {
  const qc = useQueryClient()
  const inv = () => qc.invalidateQueries({ queryKey: ["memory"] })
  return {
    add: useMutation({
      mutationFn: async (v: { text: string; category: string }) => {
        const r = await apiFetch("/api/memory/add", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: v.text, category: v.category, source: "user" }),
        })
        if (!r.ok) throw new Error("add failed"); return r.json()
      },
      onSuccess: inv,
    }),
    update: useMutation({
      mutationFn: async (v: { id: string; text: string; category?: string }) => {
        const fd = new FormData(); fd.set("text", v.text); if (v.category) fd.set("category", v.category)
        const r = await apiFetch(`/api/memory/${v.id}`, { method: "PUT", body: fd })
        if (!r.ok) throw new Error("update failed"); return r.json()
      },
      onSuccess: inv,
    }),
    remove: useMutation({ mutationFn: async (id: string) => { const r = await apiFetch(`/api/memory/${id}`, { method: "DELETE" }); if (!r.ok) throw new Error("Couldn't delete the memory") }, onSuccess: inv }),
    bulkRemove: useMutation({
      mutationFn: async (ids: string[]) => {
        for (const id of ids) {
          const r = await apiFetch(`/api/memory/${id}`, { method: "DELETE" })
          if (!r.ok) throw new Error("Couldn't delete one of the selected memories")
        }
      },
      onSuccess: inv,
    }),
    pin: useMutation({
      mutationFn: async (v: { id: string; pinned: boolean }) => {
        const fd = new FormData(); fd.set("pinned", String(v.pinned))
        const r = await apiFetch(`/api/memory/${v.id}/pin`, { method: "POST", body: fd })
        if (!r.ok) throw new Error("Couldn't update pin")
        return r.json()
      },
      onSuccess: inv,
    }),
    tidy: useMutation({
      mutationFn: async () => {
        const r = await apiFetch("/api/memory/audit", { method: "POST" })
        if (!r.ok) {
          const e = await r.json().catch(() => ({}))
          throw new Error(e.detail || "Memory tidy failed")
        }
        return r.json() as Promise<MemoryAuditResult>
      },
      onSuccess: inv,
    }),
    importFile: useMutation({
      mutationFn: async (file: File) => {
        const fd = new FormData(); fd.set("file", file)
        const r = await apiFetch("/api/memory/import", { method: "POST", body: fd })
        if (!r.ok) {
          const e = await r.json().catch(() => ({}))
          throw new Error(e.detail || "Import failed")
        }
        return r.json() as Promise<MemoryImportResult>
      },
    }),
    // Analyze a chat session and return memory suggestions for review. The
    // backend returns a plain { suggestions: string[] }; reuse the same
    // review -> save flow the file import uses.
    extract: useMutation({
      mutationFn: async (sessionId: string) => {
        const fd = new FormData(); fd.set("session", sessionId)
        const r = await apiFetch("/api/memory/extract", { method: "POST", body: fd })
        if (!r.ok) {
          const e = await r.json().catch(() => ({}))
          throw new Error(e.detail || "Couldn't extract memories from that session")
        }
        return r.json() as Promise<MemoryImportResult>
      },
    }),
  }
}
