import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { apiJson, apiFetch } from "@/lib/api"

export interface PersonalFile {
  name: string
  size: number
  path: string
}
export interface PersonalIndex {
  files: PersonalFile[]
  directories: string[]
}

export interface AddDirectoryResult {
  success: boolean
  message?: string
  indexed_count?: number
  failed_count?: number
  directory?: string
}
export interface UploadResult {
  success: boolean
  uploaded: string[]
  indexed_count: number
  failed_count: number
}

export interface BrowseDir {
  name: string
  path: string
}
export interface BrowseResult {
  path: string
  parent: string | null
  dirs: BrowseDir[]
  truncated: boolean
  selectable: boolean
}

export function usePersonalIndex() {
  return useQuery({
    queryKey: ["personal"],
    retry: false,
    queryFn: async () => {
      try {
        const r = await apiJson<PersonalIndex>("/api/personal")
        return { files: r.files || [], directories: r.directories || [], ok: true }
      } catch {
        return { files: [] as PersonalFile[], directories: [] as string[], ok: false }
      }
    },
  })
}

// Admin-gated server filesystem browser. A 403 means the caller isn't admin.
export function useWorkspaceBrowse(path: string | null) {
  return useQuery({
    queryKey: ["workspace-browse", path ?? ""],
    enabled: path !== null,
    retry: false,
    queryFn: async () => {
      const r = await apiFetch(`/api/workspace/browse?path=${encodeURIComponent(path ?? "")}`)
      if (r.status === 403) return { admin: false as const }
      if (!r.ok) throw new Error(`workspace/browse -> ${r.status}`)
      return { admin: true as const, ...(await r.json() as BrowseResult) }
    },
  })
}

export function usePersonalMutations() {
  const qc = useQueryClient()
  const inv = () => qc.invalidateQueries({ queryKey: ["personal"] })
  return {
    reload: useMutation({
      mutationFn: async () => {
        const r = await apiFetch("/api/personal/reload", { method: "POST" })
        if (!r.ok) throw new Error("Reload failed")
        return r.json() as Promise<{ ok: boolean; count: number }>
      },
      onSuccess: inv,
    }),
    addDirectory: useMutation({
      mutationFn: async (directory: string) => {
        const r = await apiFetch("/api/personal/add_directory", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ directory }),
        })
        if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || "Add failed") }
        return r.json() as Promise<AddDirectoryResult>
      },
      onSuccess: inv,
    }),
    removeDirectory: useMutation({
      mutationFn: async (directory: string) => {
        const r = await apiFetch(`/api/personal/remove_directory?directory=${encodeURIComponent(directory)}`, { method: "DELETE" })
        if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || "Remove failed") }
        return r.json()
      },
      onSuccess: inv,
    }),
    upload: useMutation({
      mutationFn: async (files: FileList | File[]) => {
        const fd = new FormData()
        for (const f of Array.from(files)) fd.append("files", f)
        const r = await apiFetch("/api/personal/upload", { method: "POST", body: fd })
        if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || "Upload failed") }
        return r.json() as Promise<UploadResult>
      },
      onSuccess: inv,
    }),
    removeFile: useMutation({
      mutationFn: async (filepath: string) => {
        const r = await apiFetch(`/api/personal/file?filepath=${encodeURIComponent(filepath)}`, { method: "DELETE" })
        if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || "Delete failed") }
        return r.json() as Promise<{ success: boolean; removed_chunks: number; deleted_from_disk: boolean }>
      },
      onSuccess: inv,
    }),
  }
}
