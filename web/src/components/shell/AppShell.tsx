import type { ReactNode } from "react"
import { NavRail } from "./NavRail"
import { ConfigPanel } from "./ConfigPanel"

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-full w-full overflow-hidden bg-background text-foreground">
      <NavRail />
      <main className="flex min-w-0 flex-1 flex-col overflow-hidden">{children}</main>
      <ConfigPanel />
    </div>
  )
}
