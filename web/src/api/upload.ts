import { apiFetch } from "@/lib/api"
export interface Uploaded { id: string; name: string; mime?: string; size?: number }
export async function uploadFiles(files: File[]): Promise<Uploaded[]> {
  const fd = new FormData()
  for (const f of files) fd.append("files", f)
  const r = await apiFetch("/api/upload", { method: "POST", body: fd })
  if (!r.ok) throw new Error("upload failed")
  const j = (await r.json()) as { files?: Uploaded[] }
  return j.files || []
}
