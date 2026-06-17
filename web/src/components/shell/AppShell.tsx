import type { ReactNode } from "react"
import { NavRail } from "./NavRail"
import { ShortcutsOverlay } from "./ShortcutsOverlay"
import { useHotkeys } from "@/lib/useHotkeys"

export function AppShell({ children }: { children: ReactNode }) {
  const [helpOpen, setHelpOpen] = useHotkeys()
  return (
    <div className="flex h-full w-full overflow-hidden bg-background text-foreground">
      <NavRail />
      <main className="flex min-w-0 flex-1 flex-col overflow-hidden">{children}</main>
      {helpOpen && <ShortcutsOverlay onClose={() => setHelpOpen(false)} />}
    </div>
  )
}
