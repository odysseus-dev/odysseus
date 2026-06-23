import { useEffect, type ReactNode } from "react"
import { useLocation } from "react-router-dom"
import { Menu } from "lucide-react"
import { Sidebar } from "./Sidebar"
import { ShortcutsOverlay } from "./ShortcutsOverlay"
import { ConversationSearch } from "./ConversationSearch"
import { GuidedTourOverlay } from "./GuidedTourOverlay"
import { TaskNotificationPoller } from "./TaskNotificationPoller"
import { NoteReminderPoller } from "./NoteReminderPoller"
import { InboxPoller } from "./InboxPoller"
import { useHotkeys } from "@/lib/useHotkeys"
import { useAnchorRouting } from "@/lib/useAnchorRouting"
import { useUi } from "@/stores/ui"

export function AppShell({ children }: { children: ReactNode }) {
  const [helpOpen, setHelpOpen] = useHotkeys()
  useAnchorRouting()
  const { pathname } = useLocation()
  const toggleMobileNav = useUi((s) => s.toggleMobileNav)
  const setMobileNav = useUi((s) => s.setMobileNav)
  // Close the off-canvas nav whenever the route changes (e.g. tapping a link).
  useEffect(() => { setMobileNav(false) }, [pathname, setMobileNav])
  return (
    <div className="flex h-full w-full overflow-hidden bg-background text-foreground">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        {/* Mobile/tablet top bar — desktop uses the in-flow sidebar instead. */}
        <header className="flex h-12 shrink-0 items-center gap-1 border-b px-2 lg:hidden">
          <button onClick={toggleMobileNav} aria-label="Open navigation menu" className="rounded-md p-2 text-muted-foreground hover:bg-accent hover:text-foreground"><Menu className="size-5" /></button>
          <span className="text-sm font-semibold">Odysseus <span className="font-normal text-muted-foreground">/ v2</span></span>
        </header>
        <main className="flex min-w-0 flex-1 flex-col overflow-hidden">{children}</main>
      </div>
      <ConversationSearch />
      <GuidedTourOverlay />
      <TaskNotificationPoller />
      <NoteReminderPoller />
      <InboxPoller />
      {helpOpen && <ShortcutsOverlay onClose={() => setHelpOpen(false)} />}
    </div>
  )
}
