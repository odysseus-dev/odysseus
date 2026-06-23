import { useState } from "react"
import { Share2, Copy, Check, Link2Off, Loader2 } from "lucide-react"
import { useShareLink, useShareMutations, shareUrl, type ShareResource } from "@/api/share"
import { Button } from "@/components/ui/button"
import { toast } from "@/stores/toast"
import { cn } from "@/lib/utils"

// Read-only share popover. Reused for chat sessions and document artifacts.
export function ShareMenu({ resourceType, resourceId, label = "Share", placement = "down" }: { resourceType: ShareResource; resourceId: string; label?: string; placement?: "up" | "down" }) {
  const [open, setOpen] = useState(false)
  const [copied, setCopied] = useState(false)
  const { data: link, isLoading } = useShareLink(resourceType, resourceId, open)
  const { create, revoke } = useShareMutations(resourceType, resourceId)
  const url = link?.path ? shareUrl(link.path) : ""

  const copy = async () => {
    if (!url) return
    try { await navigator.clipboard.writeText(url); setCopied(true); setTimeout(() => setCopied(false), 1800) }
    catch { toast("Couldn't copy to clipboard") }
  }

  return (
    <div className="relative">
      <button onClick={() => setOpen((o) => !o)} title="Share a read-only link"
        className="flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground">
        <Share2 className="size-3.5" />{label}
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className={cn("absolute right-0 z-20 w-[min(92vw,20rem)] animate-pop-in rounded-xl border bg-popover p-3 shadow-lg",
            placement === "up" ? "bottom-full mb-1 origin-bottom-right" : "mt-1 origin-top-right")}>
            <div className="mb-1 text-sm font-semibold">Share {resourceType === "session" ? "chat" : "artifact"}</div>
            <p className="mb-3 text-xs text-muted-foreground">Anyone with the link can view a read-only copy. No sign-in required.</p>
            {isLoading ? (
              <div className="flex items-center gap-2 py-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" />Loading…</div>
            ) : link?.token ? (
              <div className="space-y-2">
                <div className="flex items-center gap-1.5">
                  <input readOnly value={url} onFocus={(e) => e.currentTarget.select()}
                    className="h-9 w-full rounded-md border bg-background px-2.5 text-xs outline-none focus-visible:border-ring" />
                  <Button size="icon" variant="outline" title="Copy link" onClick={copy}>
                    {copied ? <Check className="size-4 text-emerald-500" /> : <Copy className="size-4" />}
                  </Button>
                </div>
                <div className="flex items-center justify-between">
                  <a href={url} target="_blank" rel="noreferrer" className="text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline">Open in new tab</a>
                  <Button size="sm" variant="ghost" className="text-destructive hover:text-destructive" disabled={revoke.isPending}
                    onClick={() => revoke.mutate(link.token as string)}>
                    <Link2Off className="size-3.5" />Stop sharing
                  </Button>
                </div>
              </div>
            ) : (
              <Button size="sm" className="w-full" disabled={create.isPending}
                onClick={() => create.mutate(undefined, { onError: () => toast("Couldn't create share link") })}>
                {create.isPending ? <><Loader2 className="size-4 animate-spin" />Creating…</> : <><Share2 className="size-4" />Create link</>}
              </Button>
            )}
          </div>
        </>
      )}
    </div>
  )
}
