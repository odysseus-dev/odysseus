import { create } from "zustand"

// Contextual right panel (Claude "artifact"-style). Opens on demand for deep
// research progress / sources / streamed docs — NOT a persistent config rail.
export type PanelKind = "research" | "sources" | "doc"
interface PanelState {
  open: boolean
  kind?: PanelKind
  title?: string
  payload?: unknown
  show: (kind: PanelKind, opts?: { title?: string; payload?: unknown }) => void
  close: () => void
}
export const usePanel = create<PanelState>((set) => ({
  open: false,
  show: (kind, opts) => set({ open: true, kind, title: opts?.title, payload: opts?.payload }),
  close: () => set({ open: false }),
}))
