import { useUi } from "@/stores/ui"
import { useAuthStatus, logout } from "@/api/auth"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

export function SettingsRoute() {
  const { theme, setTheme } = useUi()
  const { data: status } = useAuthStatus()
  const user = status?.username || status?.user || "—"
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
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Account</h2>
          <div className="flex items-center justify-between rounded-lg border bg-card p-3">
            <div className="text-sm"><span className="text-muted-foreground">Signed in as </span><span className="font-medium">{user}</span></div>
            <Button variant="outline" size="sm" onClick={logout}>Log out</Button>
          </div>
        </section>
        <p className="text-xs text-muted-foreground">More settings (AI defaults, integrations, admin) are being ported to v2.</p>
      </div>
    </div>
  )
}
