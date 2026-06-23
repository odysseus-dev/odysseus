import { useEffect, useRef, useState } from "react"
import { useLocation, useNavigate } from "react-router-dom"
import { useQueryClient } from "@tanstack/react-query"
import { useUi } from "@/stores/ui"
import { useSettings } from "@/api/settings"
import { deleteSession, setSessionImportant, useSessions } from "@/api/sessions"
import { useComposer } from "@/stores/composer"
import { toast } from "@/stores/toast"

// g-leader navigation map (press "g" then the key). Keep in sync with the
// "Go to" group in ShortcutsOverlay.
const GOTO: Record<string, string> = {
  c: "/chat", k: "/compare", r: "/research", i: "/gallery", m: "/memory",
  a: "/calendar", e: "/email", n: "/notes", t: "/tasks", l: "/library",
  p: "/personal", d: "/knowledge", b: "/cookbook", s: "/skills", ",": "/settings",
}

// Configurable keybinds (src/settings.py `keybinds`). v2 honors the actions
// that have a clear v2 equivalent; the rest are persisted for parity.
export const DEFAULT_KEYBINDS: Record<string, string> = {
  search: "ctrl+k",
  toggle_sidebar: "ctrl+b",
  new_session: "ctrl+alt+n",
  fav_session: "ctrl+alt+f",
  delete_session: "ctrl+alt+d",
  cancel: "escape",
  tts: "alt+shift+t",
  incognito: "ctrl+alt+i",
  settings: "ctrl+,",
  focus_input: "ctrl+/",
  open_calendar: "ctrl+alt+c",
  open_compare: "",
  open_cookbook: "",
  open_research: "",
  open_gallery: "",
  open_library: "",
  open_memory: "",
  open_notes: "",
  open_tasks: "",
  open_theme: "",
}

function typingInField() {
  const el = document.activeElement as HTMLElement | null
  if (!el) return false
  const tag = el.tagName
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable
}

// Match a KeyboardEvent against a combo string like "ctrl+alt+n" / "escape".
// ctrl and cmd/meta are treated as the same "mod" key (cross-platform).
function matchCombo(e: KeyboardEvent, combo: string | undefined): boolean {
  if (!combo) return false
  const parts = combo.toLowerCase().split("+").map((s) => s.trim()).filter(Boolean)
  if (!parts.length) return false
  const key = parts[parts.length - 1]
  const needMod = parts.includes("ctrl") || parts.includes("cmd") || parts.includes("meta")
  const needAlt = parts.includes("alt")
  const needShift = parts.includes("shift")
  if (needMod !== (e.metaKey || e.ctrlKey)) return false
  if (needAlt !== e.altKey) return false
  if (needShift !== e.shiftKey) return false
  const k = e.key.toLowerCase()
  return k === key || (key === "escape" && e.key === "Escape")
}

// Global keyboard shortcuts. Returns the help-overlay open state.
export function useHotkeys(): [boolean, (v: boolean) => void] {
  const navigate = useNavigate()
  const location = useLocation()
  const qc = useQueryClient()
  const toggleTheme = useUi((s) => s.toggleTheme)
  const toggleSidebar = useUi((s) => s.toggleSidebar)
  const toggleComposer = useComposer((s) => s.toggle)
  const [helpOpen, setHelpOpen] = useState(false)
  const leaderRef = useRef(0)
  const { data: settings } = useSettings()
  const { data: sessions } = useSessions()
  const kbRef = useRef<Record<string, string>>(DEFAULT_KEYBINDS)
  useEffect(() => { kbRef.current = { ...DEFAULT_KEYBINDS, ...((settings?.keybinds as Record<string, string>) || {}) } }, [settings])

  useEffect(() => {
    const focusComposer = () => setTimeout(() => window.dispatchEvent(new CustomEvent("odysseus:focus-composer")), 50)
    const openSearch = () => setTimeout(() => window.dispatchEvent(new CustomEvent("odysseus:open-search")), 0)
    const routeFor: Record<string, string> = {
      open_calendar: "/calendar",
      open_compare: "/compare",
      open_cookbook: "/cookbook",
      open_research: "/research",
      open_gallery: "/gallery",
      open_library: "/library",
      open_memory: "/memory",
      open_notes: "/notes",
      open_tasks: "/tasks",
    }
    const currentSessionId = () => decodeURIComponent(location.pathname.match(/^\/chat\/([^/]+)/)?.[1] || "")
    const toggleCurrentFavorite = () => {
      const sid = currentSessionId()
      if (!sid) return
      const s = sessions?.find((item) => item.id === sid)
      if (!s) return
      const next = !s.is_important
      setSessionImportant(sid, next)
        .then(() => { qc.invalidateQueries({ queryKey: ["sessions"] }); toast(next ? "Chat favorited" : "Chat unfavorited", "success") })
        .catch(() => toast("Couldn't update this chat"))
    }
    const deleteCurrentSession = () => {
      const sid = currentSessionId()
      if (!sid) return
      const s = sessions?.find((item) => item.id === sid)
      if (s?.is_important) { toast("Unfavorite the chat before deleting it", "info"); return }
      if (!confirm("Delete this chat?")) return
      deleteSession(sid)
        .then(() => { qc.invalidateQueries({ queryKey: ["sessions"] }); navigate("/chat"); toast("Chat deleted", "success") })
        .catch(() => toast("Couldn't delete this chat"))
    }
    const toggleTts = () => {
      const buttons = [...document.querySelectorAll<HTMLButtonElement>('button[title="Stop"], button[title="Read aloud"]')]
      buttons.at(-1)?.click()
    }
    const onKey = (e: KeyboardEvent) => {
      const kb = kbRef.current
      const mod = e.metaKey || e.ctrlKey
      // Configurable actions with v2 equivalents.
      if (matchCombo(e, kb.search)) { e.preventDefault(); openSearch(); return }
      if (matchCombo(e, kb.new_session)) { e.preventDefault(); navigate("/chat"); focusComposer(); return }
      if (matchCombo(e, kb.toggle_sidebar)) { e.preventDefault(); toggleSidebar(); return }
      if (matchCombo(e, kb.fav_session) || matchCombo(e, kb.star_session)) { e.preventDefault(); toggleCurrentFavorite(); return }
      if (matchCombo(e, kb.delete_session)) { e.preventDefault(); deleteCurrentSession(); return }
      if (matchCombo(e, kb.tts)) { e.preventDefault(); toggleTts(); return }
      if (matchCombo(e, kb.incognito)) { e.preventDefault(); toggleComposer("incognito"); navigate("/chat"); return }
      if (matchCombo(e, kb.settings) || matchCombo(e, kb.admin_panel)) { e.preventDefault(); navigate("/settings"); return }
      if (matchCombo(e, kb.focus_input)) { e.preventDefault(); navigate("/chat"); focusComposer(); return }
      for (const [action, route] of Object.entries(routeFor)) {
        if (matchCombo(e, kb[action])) { e.preventDefault(); navigate(route); return }
      }
      if (matchCombo(e, kb.open_theme)) { e.preventDefault(); toggleTheme(); return }
      // v2 extras (not in the legacy keybind set).
      if (mod && e.key.toLowerCase() === "j") { e.preventDefault(); toggleTheme(); return }
      if (typingInField()) return
      if (e.key === "[") { e.preventDefault(); toggleSidebar(); return }
      if (e.key === "?" || (e.shiftKey && e.key === "/")) { e.preventDefault(); setHelpOpen((o) => !o); return }
      if (matchCombo(e, kb.cancel) || e.key === "Escape") { setHelpOpen(false); return }
      // g-leader navigation
      const now = Date.now()
      if (e.key === "g") { leaderRef.current = now; return }
      if (now - leaderRef.current < 900) {
        const dest = GOTO[e.key]
        if (dest) { e.preventDefault(); navigate(dest) }
        leaderRef.current = 0
      }
    }
    const openHelp = () => setHelpOpen(true)
    window.addEventListener("keydown", onKey)
    window.addEventListener("odysseus:open-shortcuts", openHelp)
    return () => {
      window.removeEventListener("keydown", onKey)
      window.removeEventListener("odysseus:open-shortcuts", openHelp)
    }
  }, [location.pathname, navigate, qc, sessions, toggleComposer, toggleTheme, toggleSidebar])

  return [helpOpen, setHelpOpen]
}
