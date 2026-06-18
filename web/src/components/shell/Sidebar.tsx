import { useState } from "react"
import { NavLink, useNavigate, useParams } from "react-router-dom"
import { Plus, Search, PanelLeft, Settings, Trash2, Moon, Sun, LogOut, EyeOff, Keyboard, ChevronsUpDown, Pencil, Pin, Check } from "lucide-react"
import { useUi } from "@/stores/ui"
import { useComposer } from "@/stores/composer"
import { useSessions, useSessionMutations } from "@/api/sessions"
import { useAuthStatus, logout } from "@/api/auth"
import { usePrefs } from "@/api/prefs"
import { PRIMARY, WORKSPACE } from "./nav"
import type { Session } from "@/types"
import { cn } from "@/lib/utils"

const BUCKETS = ["Today", "Yesterday", "Previous 7 days", "Older"] as const
function bucketOf(s: Session): typeof BUCKETS[number] {
  const t = new Date(s.last_message_at || s.updated_at || 0).getTime()
  if (!t) return "Older"
  const day = 86400000
  const startOfToday = new Date(); startOfToday.setHours(0, 0, 0, 0)
  const diff = startOfToday.getTime() - t
  if (t >= startOfToday.getTime()) return "Today"
  if (diff <= day) return "Yesterday"
  if (diff <= 7 * day) return "Previous 7 days"
  return "Older"
}

const navRow = (active: boolean) =>
  cn("flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm transition-colors",
    active ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/60 hover:text-foreground")
const iconBtn = (active: boolean) =>
  cn("flex size-10 items-center justify-center rounded-md transition-colors",
    active ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent hover:text-foreground")

function Account({ collapsed }: { collapsed: boolean }) {
  const { data: status } = useAuthStatus()
  const { theme, toggleTheme } = useUi()
  const incognito = useComposer((s) => s.incognito)
  const toggle = useComposer((s) => s.toggle)
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const name = status?.username || status?.user || "Account"
  const item = "flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-sm text-muted-foreground hover:bg-accent hover:text-foreground"
  return (
    <div className="relative mt-auto border-t p-2">
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute bottom-full left-2 right-2 z-20 mb-1 overflow-hidden rounded-xl border bg-popover p-1 shadow-lg">
            <NavLink to="/settings" onClick={() => setOpen(false)} className={item}><Settings className="size-4" />Settings</NavLink>
            <button onClick={() => { toggleTheme() }} className={item}>{theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}{theme === "dark" ? "Light mode" : "Dark mode"}</button>
            <button onClick={() => { toggle("incognito"); navigate("/chat"); setOpen(false) }} className={cn(item, incognito && "text-foreground")}><EyeOff className="size-4" />Incognito {incognito ? "on" : "off"}</button>
            <button onClick={() => { window.dispatchEvent(new CustomEvent("odysseus:open-shortcuts")); setOpen(false) }} className={item}><Keyboard className="size-4" />Keyboard shortcuts</button>
            <button onClick={logout} className={item}><LogOut className="size-4" />Log out</button>
          </div>
        </>
      )}
      {collapsed ? (
        <button onClick={() => setOpen((o) => !o)} title={name} className="mx-auto flex size-9 items-center justify-center rounded-full bg-muted text-sm font-medium uppercase">{name[0]}</button>
      ) : (
        <button onClick={() => setOpen((o) => !o)} className="flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 hover:bg-accent">
          <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-medium uppercase">{name[0]}</span>
          <span className="min-w-0 flex-1 truncate text-left text-sm font-medium">{name}</span>
          <ChevronsUpDown className="size-4 shrink-0 text-muted-foreground" />
        </button>
      )}
    </div>
  )
}

export function Sidebar() {
  const collapsed = useUi((s) => s.sidebarCollapsed)
  const toggleSidebar = useUi((s) => s.toggleSidebar)
  const navigate = useNavigate()
  const { sessionId } = useParams()
  const { data: sessions } = useSessions()
  const { data: prefs } = usePrefs()
  const hidden = new Set((prefs?.hidden_nav as string[] | undefined) || [])
  const primary = PRIMARY.filter((i) => i.to === "/chat" || !hidden.has(i.to))
  const workspace = WORKSPACE.filter((i) => !hidden.has(i.to))
  const allNav = [...primary, ...workspace]
  const { remove, rename, setImportant } = useSessionMutations()
  const [q, setQ] = useState("")
  const [editId, setEditId] = useState<string | null>(null)
  const [editName, setEditName] = useState("")
  const [sortMode, setSortMode] = useState<"recent" | "az" | "oldest">("recent")
  const commitRename = () => { if (editId && editName.trim()) rename.mutate({ id: editId, name: editName.trim() }); setEditId(null) }

  const list = (sessions || []).filter((s) => !s.archived).filter((s) => !q || (s.name || "").toLowerCase().includes(q.toLowerCase()))
  const groups = sortMode === "recent"
    ? BUCKETS.map((b) => ({ b, items: list.filter((s) => bucketOf(s) === b) })).filter((g) => g.items.length)
    : [{ b: sortMode === "az" ? "A–Z" : "Oldest first", items: [...list].sort((x, y) =>
        sortMode === "az" ? (x.name || "").localeCompare(y.name || "")
        : new Date(x.last_message_at || x.updated_at || 0).getTime() - new Date(y.last_message_at || y.updated_at || 0).getTime()) }]

  if (collapsed) {
    return (
      <aside className="flex h-full w-14 shrink-0 flex-col items-center gap-1 border-r bg-sidebar py-3">
        <button onClick={toggleSidebar} title="Expand sidebar (⌘B)" className={iconBtn(false)}><PanelLeft className="size-5" /></button>
        <button onClick={() => navigate("/chat")} title="New chat (⌘K)" className={iconBtn(false)}><Plus className="size-5" /></button>
        <div className="my-1 h-px w-6 bg-border" />
        {allNav.map(({ to, icon: Icon, label }) => (
          <NavLink key={to} to={to} title={label} className={({ isActive }) => iconBtn(isActive)}><Icon className="size-5" /></NavLink>
        ))}
        <Account collapsed />
      </aside>
    )
  }

  return (
    <aside className="flex h-full w-[264px] shrink-0 flex-col border-r bg-sidebar">
      <div className="flex items-center justify-between px-3 pb-1 pt-3">
        <div className="text-sm font-semibold">Odysseus <span className="font-normal text-muted-foreground">/ v2</span></div>
        <button onClick={toggleSidebar} title="Collapse sidebar (⌘B)" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"><PanelLeft className="size-4" /></button>
      </div>
      <div className="space-y-2 px-2 pb-2">
        <button onClick={() => navigate("/chat")} className="flex w-full items-center gap-2 rounded-md border px-3 py-2 text-sm font-medium hover:bg-accent"><Plus className="size-4" /> New chat</button>
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search chats…" className="h-8 w-full rounded-md border bg-background pl-8 pr-2 text-sm outline-none focus-visible:border-ring" />
        </div>
      </div>
      <nav className="space-y-0.5 px-2">
        {primary.map(({ to, icon: Icon, label }) => (
          <NavLink key={to} to={to} className={({ isActive }) => navRow(isActive)}><Icon className="size-4 shrink-0" />{label}</NavLink>
        ))}
      </nav>
      {workspace.length > 0 && <div className="px-4 pb-1 pt-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Workspace</div>}
      <nav className="space-y-0.5 px-2">
        {workspace.map(({ to, icon: Icon, label }) => (
          <NavLink key={to} to={to} className={({ isActive }) => navRow(isActive)}><Icon className="size-4 shrink-0" />{label}</NavLink>
        ))}
      </nav>
      <div className="mt-3 flex items-center justify-between px-3 pb-1">
        <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Chats</span>
        <select value={sortMode} onChange={(e) => setSortMode(e.target.value as "recent" | "az" | "oldest")} className="rounded border-0 bg-transparent text-xs text-muted-foreground outline-none hover:text-foreground">
          <option value="recent">Recent</option><option value="az">A–Z</option><option value="oldest">Oldest</option>
        </select>
      </div>
      <div className="flex-1 overflow-y-auto px-2 pb-2">
        {groups.map((g) => (
          <div key={g.b} className="mb-2">
            <div className="px-2 py-1 text-xs font-medium text-muted-foreground/80">{g.b}</div>
            {g.items.map((s) => editId === s.id ? (
              <div key={s.id} className="flex items-center gap-1 px-2 py-1">
                <input autoFocus value={editName} onChange={(e) => setEditName(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") commitRename(); if (e.key === "Escape") setEditId(null) }}
                  onBlur={commitRename}
                  className="h-7 flex-1 rounded-md border bg-background px-2 text-sm outline-none focus-visible:border-ring" />
                <button onClick={commitRename} className="text-muted-foreground hover:text-foreground"><Check className="size-3.5" /></button>
              </div>
            ) : (
              <div key={s.id} onClick={() => navigate(`/chat/${s.id}`)}
                className={cn("group flex cursor-pointer items-center gap-1.5 rounded-md px-2 py-1.5 text-sm",
                  s.id === sessionId ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/60 hover:text-foreground")}>
                {s.is_important && <Pin className="size-3 shrink-0 fill-current text-muted-foreground" />}
                <span className="flex-1 truncate">{s.name || "Untitled"}</span>
                <span className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
                  <button onClick={(e) => { e.stopPropagation(); setImportant.mutate({ id: s.id, important: !s.is_important }) }} title={s.is_important ? "Unpin" : "Pin"} className={cn("hover:text-foreground", s.is_important && "text-foreground")}><Pin className="size-3.5" /></button>
                  <button onClick={(e) => { e.stopPropagation(); setEditId(s.id); setEditName(s.name || "") }} title="Rename" className="hover:text-foreground"><Pencil className="size-3.5" /></button>
                  <button onClick={(e) => { e.stopPropagation(); if (confirm("Delete this chat?")) { remove.mutate(s.id); if (s.id === sessionId) navigate("/chat") } }} title="Delete" className="hover:text-destructive"><Trash2 className="size-3.5" /></button>
                </span>
              </div>
            ))}
          </div>
        ))}
        {list.length === 0 && <p className="px-2 py-4 text-xs text-muted-foreground">{q ? "No matches." : "No chats yet."}</p>}
      </div>
      <Account collapsed={false} />
    </aside>
  )
}
