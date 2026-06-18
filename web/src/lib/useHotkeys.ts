import { useEffect, useRef, useState } from "react"
import { useNavigate } from "react-router-dom"
import { useUi } from "@/stores/ui"
import { useSettings } from "@/api/settings"

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
  star_session: "ctrl+alt+s",
  delete_session: "ctrl+alt+d",
  admin_panel: "ctrl+shift+u",
  cancel: "escape",
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
  const toggleTheme = useUi((s) => s.toggleTheme)
  const toggleSidebar = useUi((s) => s.toggleSidebar)
  const [helpOpen, setHelpOpen] = useState(false)
  const leaderRef = useRef(0)
  const { data: settings } = useSettings()
  const kbRef = useRef<Record<string, string>>(DEFAULT_KEYBINDS)
  useEffect(() => { kbRef.current = { ...DEFAULT_KEYBINDS, ...((settings?.keybinds as Record<string, string>) || {}) } }, [settings])

  useEffect(() => {
    const focusComposer = () => setTimeout(() => window.dispatchEvent(new CustomEvent("odysseus:focus-composer")), 50)
    const onKey = (e: KeyboardEvent) => {
      const kb = kbRef.current
      const mod = e.metaKey || e.ctrlKey
      // Configurable actions with v2 equivalents.
      if (matchCombo(e, kb.search) || matchCombo(e, kb.new_session)) { e.preventDefault(); navigate("/chat"); focusComposer(); return }
      if (matchCombo(e, kb.toggle_sidebar)) { e.preventDefault(); toggleSidebar(); return }
      // v2 extras (not in the legacy keybind set).
      if (mod && e.key.toLowerCase() === "j") { e.preventDefault(); toggleTheme(); return }
      if (matchCombo(e, kb.admin_panel)) { e.preventDefault(); navigate("/settings"); return }
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
  }, [navigate, toggleTheme, toggleSidebar])

  return [helpOpen, setHelpOpen]
}
