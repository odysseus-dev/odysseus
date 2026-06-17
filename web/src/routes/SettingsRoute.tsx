import { Trash2 } from "lucide-react"
import { useUi } from "@/stores/ui"
import { useAuthStatus, useUsers, logout } from "@/api/auth"
import { useModels, useDeleteEndpoint } from "@/api/models"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

export function SettingsRoute() {
  const { theme, setTheme } = useUi()
  const { data: status } = useAuthStatus()
  const { data: models } = useModels()
  const { data: users } = useUsers()
  const del = useDeleteEndpoint()
  const user = status?.username || status?.user || "—"
  const endpoints = (models?.items || []).filter((e) => e.endpoint_id)
  const userList = users || []

  return (
    <div className="mx-auto flex h-full w-full max-w-2xl flex-col">
      <header className="flex h-13 shrink-0 items-center border-b px-4 text-sm font-semibold">Settings</header>
      <div className="flex-1 space-y-6 overflow-y-auto p-4">
        <section>
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Appearance</h2>
          <div className="flex items-center justify-between rounded-lg border bg-card p-3">
            <span className="text-sm">Theme</span>
            <div className="flex rounded-lg bg-muted p-0.5">
              {(["light", "dark"] as const).map((t) => (
                <button key={t} onClick={() => setTheme(t)} className={cn("rounded-md px-3 py-1 text-sm capitalize transition-colors", theme === t ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground")}>{t}</button>
              ))}
            </div>
          </div>
        </section>

        <section>
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Model endpoints</h2>
          <div className="space-y-2">
            {endpoints.map((e) => (
              <div key={e.endpoint_id} className="group flex items-center gap-3 rounded-lg border bg-card p-3">
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">{e.endpoint_name || e.url}</div>
                  <div className="truncate text-xs text-muted-foreground">{e.url} · {(e.models?.length || 0) + (e.models_extra?.length || 0)} models{e.category ? ` · ${e.category}` : ""}</div>
                </div>
                <button onClick={() => { if (confirm("Delete this endpoint?")) del.mutate(e.endpoint_id) }} className="text-muted-foreground opacity-0 transition-opacity hover:text-destructive group-hover:opacity-100"><Trash2 className="size-4" /></button>
              </div>
            ))}
            {endpoints.length === 0 && <p className="py-2 text-sm text-muted-foreground">No saved endpoints.</p>}
          </div>
        </section>

        {userList.length > 0 && (
          <section>
            <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Users <span className="normal-case text-muted-foreground/70">(admin)</span></h2>
            <div className="space-y-2">
              {userList.map((u) => (
                <div key={u.username} className="flex items-center justify-between rounded-lg border bg-card p-3">
                  <span className="text-sm font-medium">{u.username}</span>
                  {u.is_admin && <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">admin</span>}
                </div>
              ))}
            </div>
          </section>
        )}

        <section>
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Account</h2>
          <div className="flex items-center justify-between rounded-lg border bg-card p-3">
            <div className="text-sm"><span className="text-muted-foreground">Signed in as </span><span className="font-medium">{user}</span></div>
            <Button variant="outline" size="sm" onClick={logout}>Log out</Button>
          </div>
        </section>
      </div>
    </div>
  )
}
