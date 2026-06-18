import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"

export type ShareResource = "session" | "document"
export interface ShareLink { token: string | null; path: string | null }

async function lookupShare(resourceType: ShareResource, resourceId: string): Promise<ShareLink> {
  const r = await apiFetch(`/api/share/lookup?resource_type=${resourceType}&resource_id=${encodeURIComponent(resourceId)}`)
  if (!r.ok) return { token: null, path: null }
  return r.json()
}
async function createShare(resourceType: ShareResource, resourceId: string): Promise<ShareLink> {
  const r = await apiFetch("/api/share", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ resource_type: resourceType, resource_id: resourceId }) })
  if (!r.ok) throw new Error("Couldn't create share link")
  return r.json()
}
async function revokeShare(token: string): Promise<void> {
  const r = await apiFetch(`/api/share/${encodeURIComponent(token)}`, { method: "DELETE" })
  if (!r.ok) throw new Error("Couldn't stop sharing")
}

/** Absolute URL for a share path — built off the browser origin (the share
 * page is served by the backend at /share/{token}, outside the /v2 SPA). */
export function shareUrl(path: string): string {
  return `${window.location.origin}${path}`
}

export function useShareLink(resourceType: ShareResource, resourceId: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ["share", resourceType, resourceId],
    enabled: enabled && !!resourceId,
    queryFn: () => lookupShare(resourceType, resourceId as string),
  })
}

export function useShareMutations(resourceType: ShareResource, resourceId: string | undefined) {
  const qc = useQueryClient()
  const inv = () => qc.invalidateQueries({ queryKey: ["share", resourceType, resourceId] })
  return {
    create: useMutation({ mutationFn: () => createShare(resourceType, resourceId as string), onSuccess: inv, meta: { silent: true } }),
    revoke: useMutation({ mutationFn: (token: string) => revokeShare(token), onSuccess: inv, meta: { silent: true } }),
  }
}
