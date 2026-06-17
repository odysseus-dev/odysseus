import { useEffect, useRef, useState } from "react"
import { useNavigate } from "react-router-dom"
import { useUi } from "@/stores/ui"

// g-leader navigation map (press "g" then the key)
const GOTO: Record<string, string> = {
  c: "/chat", k: "/compare", m: "/memory", i: "/gallery", e: "/email",
  n: "/notes", t: "/tasks", b: "/cookbook", s: "/skills", l: "/library", ",": "/settings",
}

function typingInField() {
  const el = document.activeElement as HTMLElement | null
  if (!el) return false
  const tag = el.tagName
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable
}

// Global keyboard shortcuts. Returns the help-overlay open state.
export function useHotkeys(): [boolean, (v: boolean) => void] {
  const navigate = useNavigate()
  const toggleTheme = useUi((s) => s.toggleTheme)
  const toggleSidebar = useUi((s) => s.toggleSidebar)
  const [helpOpen, setHelpOpen] = useState(false)
  const leaderRef = useRef(0)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey
      if (mod && e.key.toLowerCase() === "k") {
        e.preventDefault(); navigate("/chat")
        setTimeout(() => window.dispatchEvent(new CustomEvent("odysseus:focus-composer")), 50)
        return
      }
      if (mod && e.key.toLowerCase() === "j") { e.preventDefault(); toggleTheme(); return }
      if (mod && e.key.toLowerCase() === "b") { e.preventDefault(); toggleSidebar(); return }
      if (typingInField()) return
      if (e.key === "[") { e.preventDefault(); toggleSidebar(); return }
      if (e.key === "?" || (e.shiftKey && e.key === "/")) { e.preventDefault(); setHelpOpen((o) => !o); return }
      if (e.key === "Escape") { setHelpOpen(false); return }
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
