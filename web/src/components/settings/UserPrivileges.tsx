import { useState } from "react"
import { ChevronRight } from "lucide-react"
import { useUserMutations, type AppUser } from "@/api/auth"
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

export function UserPrivileges({ user }: { user: AppUser }) {
  const { setPrivileges } = useUserMutations()
  const [open, setOpen] = useState(false)
  const p = (user.privileges || {}) as Record<string, unknown>
  const max = Number(p.max_messages_per_day ?? 0)
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
        </div>
      )}
    </div>
  )
}
