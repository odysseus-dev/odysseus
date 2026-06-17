import { create } from "zustand"

// Contextual right panel (Claude "artifact"-style). Opens on demand for deep
// research progress / sources / streamed docs — NOT a persistent config rail.
export type PanelKind = "research" | "sources" | "doc"
export interface DocState { title: string; language?: string; content: string; docId?: string }
interface PanelState {
  open: boolean
  kind?: PanelKind
  title?: string
  payload?: unknown
  doc?: DocState
  show: (kind: PanelKind, opts?: { title?: string; payload?: unknown }) => void
  showDoc: (title: string, language?: string) => void
  setDocContent: (content: string) => void
  setDocId: (id: string) => void
  close: () => void
}
export const usePanel = create<PanelState>((set, get) => ({
  open: false,
  show: (kind, opts) => set({ open: true, kind, title: opts?.title, payload: opts?.payload }),
  showDoc: (title, language) => set({ open: true, kind: "doc", title: title || "Document", doc: { title: title || "Document", language, content: "" } }),
  setDocContent: (content) => { const d = get().doc; set({ doc: { title: d?.title || "Document", language: d?.language, content, docId: d?.docId } }) },
  setDocId: (id) => { const d = get().doc; if (d) set({ doc: { ...d, docId: id } }) },
  close: () => set({ open: false }),
}))
