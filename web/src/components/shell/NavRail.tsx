import { NavLink } from "react-router-dom"
import { MessageSquare, Brain, Image, Calendar, Mail, StickyNote, ListChecks, FlaskConical, Sparkles, Settings } from "lucide-react"
import { cn } from "@/lib/utils"

const items = [
  { to: "/chat", icon: MessageSquare, label: "Chat" },
  { to: "/memory", icon: Brain, label: "Memory" },
  { to: "/gallery", icon: Image, label: "Gallery" },
  { to: "/calendar", icon: Calendar, label: "Calendar" },
  { to: "/email", icon: Mail, label: "Email" },
  { to: "/notes", icon: StickyNote, label: "Notes" },
  { to: "/tasks", icon: ListChecks, label: "Tasks" },
  { to: "/cookbook", icon: FlaskConical, label: "Cookbook" },
  { to: "/skills", icon: Sparkles, label: "Skills" },
]

const itemCls = ({ isActive }: { isActive: boolean }) =>
  cn(
    "flex size-10 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground",
    isActive && "bg-accent text-foreground",
  )

export function NavRail() {
  return (
    <nav className="flex h-full w-14 shrink-0 flex-col items-center gap-1 border-r bg-sidebar py-3">
      {items.map(({ to, icon: Icon, label }) => (
        <NavLink key={to} to={to} title={label} className={itemCls}>
          <Icon className="size-5" />
        </NavLink>
      ))}
      <div className="mt-auto">
        <NavLink to="/settings" title="Settings" className={itemCls}>
          <Settings className="size-5" />
        </NavLink>
      </div>
    </nav>
  )
}
