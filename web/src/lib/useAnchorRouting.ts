import { useEffect } from "react"
import { useNavigate } from "react-router-dom"
import { usePanel } from "@/stores/panel"

// Jump-to-entity anchors. The agent emits in-text links like
//   [Open in Deep Research](#research-<id>)   [View note](#note-<id>)
//   [Color Demo](#document-<id>)              [New chat](#session-<id>)
// A global click delegate turns them into in-app navigation instead of the
// browser's default (broken) hash jump. Mirrors the legacy chatRenderer routing
// (static/js/chatRenderer.js) mapped onto V2's router + side panel.
const ANCHOR_RE = /^#(session|document|note|image|email|event|task|skill|research)-(.+)$/

export function useAnchorRouting() {
  const navigate = useNavigate()
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey) return
      // Clicking the link text yields a Text node whose .closest is undefined —
      // walk up to the element so we still catch the anchor.
      let t = e.target as Node | null
      while (t && t.nodeType === Node.TEXT_NODE) t = t.parentElement
      const anchor = (t as Element | null)?.closest?.("a[href]") as HTMLAnchorElement | null
      const match = anchor?.getAttribute("href")?.match(ANCHOR_RE)
      if (!match) return
      e.preventDefault()
      e.stopPropagation()
      const [, kind, id] = match
      switch (kind) {
        case "session": navigate(`/chat/${id}`); break
        case "note": navigate("/notes"); break
        case "image": navigate("/gallery"); break
        case "email": navigate(`/email?uid=${encodeURIComponent(id)}`); break
        case "event": navigate("/calendar"); break
        case "task": navigate("/tasks"); break
        case "skill": navigate("/skills"); break
        case "research": navigate("/research"); break
        case "document": {
          // Open the document in the side panel (parity with legacy loadDocument);
          // fall back to the library list if the fetch fails.
          const panel = usePanel.getState()
          fetch(`/api/document/${id}`, { credentials: "same-origin" })
            .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
            .then((full: { title?: string; language?: string; current_content?: string }) => {
              panel.showDoc(full.title || "Document", full.language)
              panel.setDocId(id)
              panel.setDocContent(full.current_content || "")
            })
            .catch(() => navigate("/library"))
          break
        }
      }
    }
    document.addEventListener("click", onClick)
    return () => document.removeEventListener("click", onClick)
  }, [navigate])
}
