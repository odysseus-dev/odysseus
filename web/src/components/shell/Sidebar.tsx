import { useEffect, useState, type DragEvent } from "react"
import { NavLink, useLocation, useNavigate } from "react-router-dom"
import { Plus, Search, PanelLeft, Settings, Trash2, Moon, Sun, LogOut, EyeOff, Keyboard, ChevronsUpDown, Pencil, Pin, Check, FolderKanban, ChevronDown, Users, Archive, ArchiveRestore, CheckSquare, Square, X } from "lucide-react"
import { useUi } from "@/stores/ui"
import { useComposer } from "@/stores/composer"
import { useSessions, useSessionMutations, useArchivedSessions } from "@/api/sessions"
import { useAuthStatus, logout } from "@/api/auth"
import { usePrefs } from "@/api/prefs"
import { PRIMARY, WORKSPACE } from "./nav"
import type { Session } from "@/types"
import { removePersistentPersonaSession } from "@/lib/persistentPersona"
import { cn } from "@/lib/utils"
import { useNoteReminders } from "@/stores/noteReminders"

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
const tourNav = (to: string) => `nav-${to.replace(/^\//, "") || "chat"}`
const reminderCountLabel = (count: number) => count > 99 ? "99+" : String(count)

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
          <div className="absolute bottom-full left-2 right-2 z-20 mb-1 origin-bottom animate-pop-in overflow-hidden rounded-xl border bg-popover p-1 shadow-lg">
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
  const mobileNavOpen = useUi((s) => s.mobileNavOpen)
  const setMobileNav = useUi((s) => s.setMobileNav)
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const sessionId = /^\/chat\/([^/]+)/.exec(pathname)?.[1]
  const { data: sessions } = useSessions()
  const { data: prefs } = usePrefs()
  const hidden = new Set((prefs?.hidden_nav as string[] | undefined) || [])
  const primary = PRIMARY.filter((i) => i.to === "/chat" || !hidden.has(i.to))
  const workspace = WORKSPACE.filter((i) => !hidden.has(i.to))
  const allNav = [...primary, ...workspace]
  const firedNoteReminders = useNoteReminders((s) => s.firedCount)
  // When the Workspace group is collapsed, surface anything that would be hidden:
  // an active route in the group, and unread Notes reminders.
  const workspaceActive = workspace.some((i) => pathname === i.to || pathname.startsWith(i.to + "/"))
  const workspaceReminders = workspace.some((i) => i.to === "/notes") && firedNoteReminders > 0
  const { remove, rename, setImportant, archive, unarchive, setFolder, bulkDelete, bulkArchive } = useSessionMutations()
  const [q, setQ] = useState("")
  const [editId, setEditId] = useState<string | null>(null)
  const [editName, setEditName] = useState("")
  const [sortMode, setSortMode] = useState<"recent" | "az" | "oldest" | "manual">("recent")
  const [manualOrder, setManualOrder] = useState<string[]>(() => {
    try { return JSON.parse(window.localStorage.getItem("odysseus-session-order") || "[]") as string[] } catch { return [] }
  })
  const [folderOrder, setFolderOrder] = useState<string[]>(() => {
    try { return JSON.parse(window.localStorage.getItem("odysseus-folder-order") || "[]") as string[] } catch { return [] }
  })
  const [draggedId, setDraggedId] = useState<string | null>(null)
  const [draggedFolder, setDraggedFolder] = useState<string | null>(null)
  const [collapsedProjects, setCollapsedProjects] = useState<Set<string>>(new Set())
  // Collapse the Workspace nav group to keep the sidebar scannable. Persisted so
  // the preference survives reloads.
  const [workspaceCollapsed, setWorkspaceCollapsed] = useState<boolean>(() => {
    try { return window.localStorage.getItem("odysseus-nav-workspace-collapsed") === "1" } catch { return false }
  })
  const toggleWorkspace = () => setWorkspaceCollapsed((v) => {
    const next = !v
    try { window.localStorage.setItem("odysseus-nav-workspace-collapsed", next ? "1" : "0") } catch { /* ignore */ }
    return next
  })
  const [view, setView] = useState<"active" | "archived">("active")
  const [selectMode, setSelectMode] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const commitRename = () => { if (editId && editName.trim()) rename.mutate({ id: editId, name: editName.trim() }); setEditId(null) }
  useEffect(() => { window.localStorage.setItem("odysseus-session-order", JSON.stringify(manualOrder)) }, [manualOrder])
  useEffect(() => { window.localStorage.setItem("odysseus-folder-order", JSON.stringify(folderOrder)) }, [folderOrder])
  const manualRank = (session: Session) => { const rank = manualOrder.indexOf(session.id); return rank < 0 ? Number.MAX_SAFE_INTEGER : rank }
  const manualSort = (items: Session[]) => [...items].sort((a, b) => manualRank(a) - manualRank(b) || new Date(b.last_message_at || b.updated_at || 0).getTime() - new Date(a.last_message_at || a.updated_at || 0).getTime())
  const moveChat = (target: Session) => {
    if (!draggedId || draggedId === target.id) return
    const source = list.find((session) => session.id === draggedId)
    setManualOrder((old) => {
      const all = [...new Set([...old, ...list.map((session) => session.id)])].filter((id) => id !== draggedId)
      const targetIndex = all.indexOf(target.id)
      all.splice(targetIndex < 0 ? all.length : targetIndex, 0, draggedId)
      return all
    })
    if ((source?.folder || null) !== (target.folder || null)) setFolder.mutate({ id: draggedId, folder: target.folder || null })
    setDraggedId(null); setSortMode("manual")
  }
  const dropIntoFolder = (folder: string | null) => {
    if (!draggedId) return
    setFolder.mutate({ id: draggedId, folder }); setDraggedId(null); setSortMode("manual")
  }
  const moveFolder = (target: string) => {
    if (!draggedFolder || draggedFolder === target) return
    setFolderOrder((old) => {
      const all = [...new Set([...old, ...projectMap.keys()])].filter((name) => name !== draggedFolder)
      const index = all.indexOf(target); all.splice(index < 0 ? all.length : index, 0, draggedFolder); return all
    })
    setDraggedFolder(null)
  }

  const archivedView = view === "archived"
  const { data: archivedData } = useArchivedSessions(archivedView)
  const archivedList = (archivedData?.sessions || []).filter((s) => !q || (s.name || "").toLowerCase().includes(q.toLowerCase()))

  const list = (sessions || []).filter((s) => !s.archived).filter((s) => !q || (s.name || "").toLowerCase().includes(q.toLowerCase()))
  // Pinned (important) chats float to the top in every sort mode, in their own
  // section above projects and the time/sort buckets. They are excluded from
  // those groups below so they never appear twice.
  const pinned = list
    .filter((s) => s.is_important)
    .sort((x, y) => new Date(y.last_message_at || y.updated_at || 0).getTime() - new Date(x.last_message_at || x.updated_at || 0).getTime())
  const rest = list.filter((s) => !s.is_important)
  // Chats in a project (folder) group next; the remainder fall into the
  // time/sort buckets below.
  const filed = rest.filter((s) => s.folder)
  const unfiled = rest.filter((s) => !s.folder)
  const projectMap = new Map<string, Session[]>()
  for (const s of filed) { const k = s.folder as string; if (!projectMap.has(k)) projectMap.set(k, []); projectMap.get(k)!.push(s) }
  const projectGroups = Array.from(projectMap.entries())
    .map(([name, items]) => ({ name, items: sortMode === "manual" ? manualSort(items) : [...items].sort((x, y) => new Date(y.last_message_at || y.updated_at || 0).getTime() - new Date(x.last_message_at || x.updated_at || 0).getTime()) }))
    .sort((a, b) => { const ar = folderOrder.indexOf(a.name), br = folderOrder.indexOf(b.name); return (ar < 0 ? Number.MAX_SAFE_INTEGER : ar) - (br < 0 ? Number.MAX_SAFE_INTEGER : br) || a.name.localeCompare(b.name) })
  const groups = sortMode === "recent"
    ? BUCKETS.map((b) => ({ b, items: unfiled.filter((s) => bucketOf(s) === b) })).filter((g) => g.items.length)
    : sortMode === "manual" ? [{ b: "Manual order", items: manualSort(unfiled) }]
    : [{ b: sortMode === "az" ? "A–Z" : "Oldest first", items: [...unfiled].sort((x, y) => sortMode === "az" ? (x.name || "").localeCompare(y.name || "") : new Date(x.last_message_at || x.updated_at || 0).getTime() - new Date(y.last_message_at || y.updated_at || 0).getTime()) }]

  // All visible session ids in the active view drive Select-All. Archived rows
  // are not selectable (their actions are Restore/Delete, handled per-row).
  const visibleIds = [...pinned, ...filed, ...unfiled].map((s) => s.id)
  const allSelected = visibleIds.length > 0 && visibleIds.every((id) => selected.has(id))
  const toggleSelected = (id: string) => setSelected((prev) => { const n = new Set(prev); if (n.has(id)) n.delete(id); else n.add(id); return n })
  const exitSelectMode = () => { setSelectMode(false); setSelected(new Set()) }
  const enterSelectMode = () => { setView("active"); setSelectMode(true); setSelected(new Set()) }
  // Selected ids by pinned-state. The backend blocks deleting pinned chats, so
  // bulk delete only sends unpinned ids and we surface how many were skipped.
  const selectedSessions = list.filter((s) => selected.has(s.id))
  const selectedUnpinnedIds = selectedSessions.filter((s) => !s.is_important).map((s) => s.id)
  const runBulkDelete = () => {
    const skipped = selected.size - selectedUnpinnedIds.length
    if (!selectedUnpinnedIds.length) { alert("Pinned chats can't be deleted. Unpin them first."); return }
    const msg = skipped > 0
      ? `Delete ${selectedUnpinnedIds.length} chat(s)? ${skipped} pinned chat(s) will be skipped.`
      : `Delete ${selectedUnpinnedIds.length} chat(s)?`
    if (!confirm(msg)) return
    selectedUnpinnedIds.forEach((id) => removePersistentPersonaSession(id))
    bulkDelete.mutate(selectedUnpinnedIds)
    if (sessionId && selectedUnpinnedIds.includes(sessionId)) navigate("/chat")
    exitSelectMode()
  }
  const runBulkArchive = () => {
    const ids = [...selected]
    if (!ids.length) return
    bulkArchive.mutate(ids)
    if (sessionId && ids.includes(sessionId)) navigate("/chat")
    exitSelectMode()
  }

  const deleteRow = (s: Session) => {
    // Mirror the backend guard: pinned/important chats can't be deleted until
    // they're unpinned.
    if (s.is_important) { alert("Unpin this chat before deleting it."); return }
    if (!confirm("Delete this chat?")) return
    removePersistentPersonaSession(s.id)
    remove.mutate(s.id)
    if (s.id === sessionId) navigate("/chat")
  }

  const renderRow = (s: Session) => editId === s.id ? (
    <div key={s.id} className="flex items-center gap-1 px-2 py-1">
      <input autoFocus value={editName} onChange={(e) => setEditName(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") commitRename(); if (e.key === "Escape") setEditId(null) }}
        onBlur={commitRename}
        className="h-7 flex-1 rounded-md border bg-background px-2 text-sm outline-none focus-visible:border-ring" />
      <button onClick={commitRename} className="text-muted-foreground hover:text-foreground"><Check className="size-3.5" /></button>
    </div>
  ) : (
    <div key={s.id}
      draggable={!selectMode && sortMode === "manual"}
      onDragStart={(event: DragEvent<HTMLDivElement>) => { setDraggedId(s.id); event.dataTransfer.effectAllowed = "move" }}
      onDragEnd={() => setDraggedId(null)}
      onDragOver={(event) => { if (draggedId) event.preventDefault() }}
      onDrop={(event) => { event.preventDefault(); moveChat(s) }}
      onClick={() => { if (selectMode) toggleSelected(s.id); else navigate(`/chat/${s.id}`) }}
      role="button" tabIndex={0}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); if (selectMode) toggleSelected(s.id); else navigate(`/chat/${s.id}`) } }}
      className={cn("group flex cursor-pointer items-center gap-1.5 rounded-md px-2 py-1.5 text-sm",
        s.id === sessionId ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/60 hover:text-foreground")}>
      {selectMode && (
        selected.has(s.id)
          ? <CheckSquare className="size-3.5 shrink-0 text-foreground" />
          : <Square className="size-3.5 shrink-0 text-muted-foreground" />
      )}
      {s.is_important && <Pin className="size-3 shrink-0 fill-current text-muted-foreground" />}
      {(s.name || "").startsWith("[GRP]") && <Users className="size-3.5 shrink-0 text-muted-foreground" />}
      <span className="flex-1 truncate">{s.name || "Untitled"}</span>
      {!selectMode && (
        <span className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
          <button onClick={(e) => { e.stopPropagation(); setImportant.mutate({ id: s.id, important: !s.is_important }) }} title={s.is_important ? "Unpin" : "Pin"} className={cn("hover:text-foreground", s.is_important && "text-foreground")}><Pin className="size-3.5" /></button>
          <button onClick={(e) => { e.stopPropagation(); setEditId(s.id); setEditName(s.name || "") }} title="Rename" className="hover:text-foreground"><Pencil className="size-3.5" /></button>
          <button onClick={(e) => { e.stopPropagation(); archive.mutate(s.id); if (s.id === sessionId) navigate("/chat") }} title="Archive" className="hover:text-foreground"><Archive className="size-3.5" /></button>
          <button onClick={(e) => { e.stopPropagation(); deleteRow(s) }} title="Delete" className="hover:text-destructive"><Trash2 className="size-3.5" /></button>
        </span>
      )}
    </div>
  )

  const renderArchivedRow = (s: { id: string; name: string; is_important?: boolean }) => (
    <div key={s.id}
      className="group flex items-center gap-1.5 rounded-md px-2 py-1.5 text-sm text-muted-foreground hover:bg-accent/60 hover:text-foreground">
      {s.is_important && <Pin className="size-3 shrink-0 fill-current text-muted-foreground" />}
      <span className="flex-1 truncate">{s.name || "Untitled"}</span>
      <span className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
        <button onClick={() => unarchive.mutate(s.id)} title="Restore" className="hover:text-foreground"><ArchiveRestore className="size-3.5" /></button>
        <button onClick={() => deleteRow(s as Session)} title="Delete" className="hover:text-destructive"><Trash2 className="size-3.5" /></button>
      </span>
    </div>
  )

  // Below lg the sidebar is an off-canvas drawer (always the full nav, slid in
  // by mobileNavOpen); at lg+ it's in-flow and respects `collapsed`. The
  // desktop collapsed icon-strip only exists at lg+.
  const drawerShell = cn(
    "fixed inset-y-0 left-0 z-50 transition-transform duration-200 ease-out lg:static lg:z-auto lg:translate-x-0 lg:transition-none lg:shadow-none",
    mobileNavOpen ? "translate-x-0 shadow-2xl" : "-translate-x-full",
  )
  const backdrop = mobileNavOpen ? (
    <div onClick={() => setMobileNav(false)} className="fixed inset-0 z-40 bg-black/50 lg:hidden" aria-hidden />
  ) : null

  return (
    <>
      {backdrop}
      {/* Desktop-only collapsed icon strip */}
      {collapsed && (
        <aside className="hidden h-full w-14 shrink-0 flex-col items-center gap-1 border-r bg-sidebar py-3 lg:flex" data-tour="sidebar">
        <button onClick={toggleSidebar} title="Expand sidebar (⌘B)" className={iconBtn(false)}><PanelLeft className="size-5" /></button>
        <button data-tour="new-chat" onClick={() => navigate("/chat")} title="New chat (⌘⌥N)" className={iconBtn(false)}><Plus className="size-5" /></button>
        <button data-tour="search-conversations" onClick={() => window.dispatchEvent(new CustomEvent("odysseus:open-search"))} title="Search conversations (⌘K)" className={iconBtn(false)}><Search className="size-5" /></button>
        <div className="my-1 h-px w-6 bg-border" />
        <div className="flex flex-col items-center gap-1" data-tour="primary-nav">
          {allNav.map(({ to, icon: Icon, label }) => {
            const showReminderBadge = to === "/notes" && firedNoteReminders > 0
            return (
              <NavLink key={to} to={to} title={label} data-tour={tourNav(to)} className={({ isActive }) => iconBtn(isActive)}>
                <span className="relative grid place-items-center">
                  <Icon className="size-5" />
                  {showReminderBadge && <span className="notes-nav-reminder-badge notes-nav-reminder-badge-icon">{reminderCountLabel(firedNoteReminders)}</span>}
                </span>
              </NavLink>
            )
          })}
        </div>
        <Account collapsed />
        </aside>
      )}
      {/* Expanded sidebar: off-canvas drawer below lg, in-flow at lg (hidden at lg when collapsed) */}
      <aside className={cn("flex h-full w-[264px] shrink-0 flex-col border-r bg-sidebar", drawerShell, collapsed ? "lg:hidden" : "lg:flex")} data-tour="sidebar">
      <div className="flex items-center justify-between px-3 pb-1 pt-3">
        <div className="text-sm font-semibold">Odysseus <span className="font-normal text-muted-foreground">/ v2</span></div>
        <div className="flex items-center gap-1">
          <button onClick={() => setMobileNav(false)} title="Close menu" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground lg:hidden"><X className="size-4" /></button>
          <button onClick={toggleSidebar} title="Collapse sidebar (⌘B)" className="hidden rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground lg:block"><PanelLeft className="size-4" /></button>
        </div>
      </div>
      <div className="space-y-2 px-2 pb-2">
        <button data-tour="new-chat" onClick={() => navigate("/chat")} className="flex w-full items-center gap-2 rounded-md border px-3 py-2 text-sm font-medium hover:bg-accent"><Plus className="size-4" /> New chat</button>
        <div className="flex gap-1.5" data-tour="search-conversations">
          <button onClick={() => window.dispatchEvent(new CustomEvent("odysseus:open-search"))} title="Search conversations (⌘K)" className="flex size-8 shrink-0 items-center justify-center rounded-md border text-muted-foreground hover:bg-accent hover:text-foreground">
            <Search className="size-3.5" />
          </button>
          <div className="relative min-w-0 flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter chat titles..." className="h-8 w-full rounded-md border bg-background pl-8 pr-2 text-sm outline-none focus-visible:border-ring" />
          </div>
        </div>
      </div>
      <nav className="space-y-0.5 px-2" data-tour="primary-nav">
        {primary.map(({ to, icon: Icon, label }) => {
          const showReminderBadge = to === "/notes" && firedNoteReminders > 0
          return (
            <NavLink key={to} to={to} data-tour={tourNav(to)} className={({ isActive }) => navRow(isActive)}>
              <Icon className="size-4 shrink-0" />
              <span className="min-w-0 flex-1 truncate">{label}</span>
              {showReminderBadge && <span className="notes-nav-reminder-badge">{reminderCountLabel(firedNoteReminders)}</span>}
            </NavLink>
          )
        })}
      </nav>
      {workspace.length > 0 && (
        <div className="pt-2" data-tour="workspace-nav">
          <button
            type="button"
            onClick={toggleWorkspace}
            aria-expanded={!workspaceCollapsed}
            title={workspaceCollapsed ? "Expand Workspace" : "Collapse Workspace"}
            className="group/ws flex w-full items-center gap-1.5 px-4 pb-1 pt-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground/80 transition-colors hover:text-foreground"
          >
            <span className="flex-1 text-left">Workspace</span>
            {workspaceCollapsed && workspaceReminders && <span className="notes-nav-reminder-badge">{reminderCountLabel(firedNoteReminders)}</span>}
            {workspaceCollapsed && workspaceActive && !workspaceReminders && <span className="size-1.5 rounded-full bg-foreground/70" aria-hidden />}
            <ChevronDown className={cn("size-3.5 shrink-0 text-muted-foreground/50 transition-transform duration-200 group-hover/ws:text-muted-foreground", workspaceCollapsed && "-rotate-90")} />
          </button>
          <div className={cn("grid transition-[grid-template-rows] duration-200 ease-out", workspaceCollapsed ? "grid-rows-[0fr]" : "grid-rows-[1fr]")}>
            <nav className="min-h-0 space-y-0.5 overflow-hidden px-2">
              {workspace.map(({ to, icon: Icon, label }) => {
                const showReminderBadge = to === "/notes" && firedNoteReminders > 0
                return (
                  <NavLink key={to} to={to} data-tour={tourNav(to)} className={({ isActive }) => navRow(isActive)}>
                    <Icon className="size-4 shrink-0" />
                    <span className="min-w-0 flex-1 truncate">{label}</span>
                    {showReminderBadge && <span className="notes-nav-reminder-badge">{reminderCountLabel(firedNoteReminders)}</span>}
                  </NavLink>
                )
              })}
            </nav>
          </div>
        </div>
      )}
      <div className="mt-3 flex items-center justify-between px-3 pb-1">
        <div className="flex items-center gap-2">
          <button onClick={() => { setView("active"); exitSelectMode() }}
            className={cn("text-xs font-semibold uppercase tracking-wider", archivedView ? "text-muted-foreground/60 hover:text-muted-foreground" : "text-muted-foreground")}>Chats</button>
          <button onClick={() => { setView("archived"); exitSelectMode() }}
            className={cn("text-xs font-semibold uppercase tracking-wider", archivedView ? "text-muted-foreground" : "text-muted-foreground/60 hover:text-muted-foreground")}>Archived</button>
        </div>
        {archivedView ? null : selectMode ? (
          <button onClick={exitSelectMode} title="Cancel selection" className="text-muted-foreground hover:text-foreground"><X className="size-3.5" /></button>
        ) : (
          <div className="flex items-center gap-2">
            <button onClick={enterSelectMode} title="Select chats" className="text-muted-foreground hover:text-foreground"><CheckSquare className="size-3.5" /></button>
            <select value={sortMode} onChange={(e) => setSortMode(e.target.value as "recent" | "az" | "oldest" | "manual")} className="rounded border-0 bg-transparent text-xs text-muted-foreground outline-none hover:text-foreground">
              <option value="recent">Recent</option><option value="az">A–Z</option><option value="oldest">Oldest</option><option value="manual">Manual</option>
            </select>
          </div>
        )}
      </div>
      {selectMode && !archivedView && (
        <div className="mb-1 flex items-center gap-2 px-3 pb-1">
          <button onClick={() => setSelected(allSelected ? new Set() : new Set(visibleIds))}
            className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
            {allSelected ? <CheckSquare className="size-3.5" /> : <Square className="size-3.5" />}
            <span>{allSelected ? "Clear" : "All"}</span>
          </button>
          <span className="flex-1 text-xs text-muted-foreground">{selected.size} selected</span>
          <button onClick={runBulkArchive} disabled={!selected.size} title="Archive selected" className="text-muted-foreground hover:text-foreground disabled:opacity-40"><Archive className="size-3.5" /></button>
          <button onClick={runBulkDelete} disabled={!selected.size} title="Delete selected" className="text-muted-foreground hover:text-destructive disabled:opacity-40"><Trash2 className="size-3.5" /></button>
        </div>
      )}
      <div className="flex-1 overflow-y-auto px-2 pb-2">
        {archivedView ? (
          <>
            {archivedList.map(renderArchivedRow)}
            {archivedList.length === 0 && <p className="px-2 py-4 text-xs text-muted-foreground">{q ? "No matches." : "No archived chats."}</p>}
          </>
        ) : (
          <>
            {pinned.length > 0 && (
              <div className="mb-2">
                <div className="flex items-center gap-1 px-2 py-1 text-xs font-medium text-muted-foreground/80">
                  <Pin className="size-3 shrink-0 fill-current" />
                  <span>Pinned</span>
                </div>
                {pinned.map(renderRow)}
              </div>
            )}
            {projectGroups.length > 0 && (
              <div className="mb-2">
                {projectGroups.map((g) => {
                  const isCollapsed = collapsedProjects.has(g.name)
                  return (
                    <div key={g.name} className="mb-0.5">
                      <button draggable={sortMode === "manual"} onDragStart={(event) => { setDraggedFolder(g.name); event.dataTransfer.effectAllowed = "move" }} onDragEnd={() => setDraggedFolder(null)} onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); if (draggedFolder) moveFolder(g.name); else dropIntoFolder(g.name) }} onClick={() => setCollapsedProjects((prev) => { const n = new Set(prev); if (n.has(g.name)) n.delete(g.name); else n.add(g.name); return n })}
                        className="flex w-full items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-muted-foreground/80 hover:bg-accent/40 hover:text-foreground">
                        <ChevronDown className={cn("size-3.5 shrink-0 transition-transform duration-200", isCollapsed && "-rotate-90")} />
                        <FolderKanban className="size-3.5 shrink-0" />
                        <span className="min-w-0 flex-1 truncate text-left">{g.name}</span>
                        <span className="shrink-0">{g.items.length}</span>
                      </button>
                      {!isCollapsed && <div className="ml-3 border-l pl-1">{g.items.map(renderRow)}</div>}
                    </div>
                  )
                })}
              </div>
            )}
            {groups.map((g) => (
              <div key={g.b} className="mb-2">
                <div onDragOver={(event) => { if (draggedId) event.preventDefault() }} onDrop={(event) => { event.preventDefault(); dropIntoFolder(null) }} className="px-2 py-1 text-xs font-medium text-muted-foreground/80">{g.b}</div>
                {g.items.map(renderRow)}
              </div>
            ))}
            {list.length === 0 && <p className="px-2 py-4 text-xs text-muted-foreground">{q ? "No matches." : "No chats yet."}</p>}
          </>
        )}
      </div>
      <Account collapsed={false} />
      </aside>
    </>
  )
}
