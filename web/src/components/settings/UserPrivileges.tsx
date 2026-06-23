import { useState } from "react"
import { ChevronRight } from "lucide-react"
import { useUserMutations, type AppUser } from "@/api/auth"
import { useModels } from "@/api/models"
import { Switch } from "@/components/ui/switch"
import { cn } from "@/lib/utils"

// Boolean privilege toggles backed by core/auth.py DEFAULT_PRIVILEGES.
const PRIV_TOGGLES: { key: string; label: string }[] = [
  { key: "can_use_agent", label: "Agent mode" },
  { key: "can_use_browser", label: "Browser" },
  { key: "can_use_bash", label: "Bash" },
  { key: "can_use_documents", label: "Documents" },
  { key: "can_use_research", label: "Deep research" },
  { key: "can_generate_images", label: "Image generation" },
  { key: "can_manage_memory", label: "Memory" },
  { key: "allowed_models_restricted", label: "Restrict model access" },
  { key: "block_all_models", label: "Block all models" },
]

// Editor for the `allowed_models` allow-list. When "Restrict model access" is
// on, only models in this list are usable; an empty list means "none". Backed
// by the list privilege in core/auth.py DEFAULT_PRIVILEGES.
function AllowedModelsEditor({ user, allowed }: { user: AppUser; allowed: string[] }) {
  const { setPrivileges } = useUserMutations()
  const { data: models } = useModels()
  const all = (models?.items || []).flatMap((e) => [...(e.models || []), ...(e.models_extra || [])])
  const unique = Array.from(new Set([...all, ...allowed]))
  const sel = new Set(allowed)
  const toggle = (m: string) => {
    const next = new Set(sel)
    if (next.has(m)) next.delete(m); else next.add(m)
    setPrivileges.mutate({ username: user.username, privileges: { allowed_models: Array.from(next) } })
  }
  return (
    <div className="mt-1 space-y-1 border-t pt-2">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-muted-foreground">Allowed models ({sel.size})</span>
        {sel.size > 0 && <button onClick={() => setPrivileges.mutate({ username: user.username, privileges: { allowed_models: [] } })} className="text-[11px] text-muted-foreground underline underline-offset-2 hover:text-foreground">Clear</button>}
      </div>
      {unique.length === 0
        ? <p className="text-[11px] text-muted-foreground">No models available to choose from.</p>
        : (
          <div className="max-h-40 space-y-0.5 overflow-y-auto rounded-md border bg-background/50 p-1.5">
            {unique.map((m) => (
              <label key={m} className="flex items-center gap-2 rounded px-1 py-0.5 text-sm text-muted-foreground hover:bg-accent/60">
                <input type="checkbox" checked={sel.has(m)} onChange={() => toggle(m)} className="size-3.5 accent-foreground" />
                <span className="min-w-0 truncate">{m}</span>
              </label>
            ))}
          </div>
        )}
    </div>
  )
}

export function UserPrivileges({ user }: { user: AppUser }) {
  const { setPrivileges } = useUserMutations()
  const [open, setOpen] = useState(false)
  const p = (user.privileges || {}) as Record<string, unknown>
  const max = Number(p.max_messages_per_day ?? 0)
  const allowedModels = Array.isArray(p.allowed_models) ? (p.allowed_models as unknown[]).map(String) : []
  return (
    <div className="mt-1">
      <button onClick={() => setOpen((o) => !o)} className="flex items-center gap-1.5 text-[11px] text-muted-foreground transition-colors hover:text-foreground">
        <ChevronRight className={cn("size-3.5 transition-transform duration-200", open && "rotate-90")} />Privileges
      </button>
      {open && (
        <div className="mt-1 space-y-1 rounded-md border bg-background/50 p-2.5">
          {PRIV_TOGGLES.map((t) => (
            <div key={t.key} className="flex items-center justify-between gap-2 py-0.5">
              <span className="text-sm text-muted-foreground">{t.label}</span>
              <Switch checked={!!p[t.key]} onCheckedChange={(v) => setPrivileges.mutate({ username: user.username, privileges: { [t.key]: v } })} />
            </div>
          ))}
          <label className="flex items-center justify-between gap-2 py-0.5 text-sm text-muted-foreground">
            Max messages/day (0 = ∞)
            <input key={String(max)} defaultValue={max} type="number" min={0} className="h-7 w-24 rounded-md border bg-background px-2 text-sm outline-none focus-visible:border-ring"
              onBlur={(e) => { const n = Number(e.target.value) || 0; if (n !== max) setPrivileges.mutate({ username: user.username, privileges: { max_messages_per_day: n } }) }} />
          </label>
          {!!p.allowed_models_restricted && <AllowedModelsEditor user={user} allowed={allowedModels} />}
        </div>
      )}
    </div>
  )
}
