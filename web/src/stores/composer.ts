import { create } from "zustand"
type Mode = "chat" | "agent"
type ToggleKey = "useWeb" | "useResearch" | "allowBash" | "useRag" | "incognito"
interface ComposerState {
  model: string; endpointId: string; endpointUrl: string
  mode: Mode
  useWeb: boolean; useResearch: boolean; allowBash: boolean; useRag: boolean; incognito: boolean
  setModel: (model: string, endpointId: string, endpointUrl: string) => void
  setMode: (m: Mode) => void
  toggle: (k: ToggleKey) => void
}
export const useComposer = create<ComposerState>((set, get) => ({
  model: "", endpointId: "", endpointUrl: "",
  mode: "chat",
  useWeb: false, useResearch: false, allowBash: true, useRag: true, incognito: false,
  setModel: (model, endpointId, endpointUrl) => set({ model, endpointId, endpointUrl }),
  setMode: (mode) => set({ mode }),
  toggle: (k) => set({ [k]: !get()[k] } as Partial<ComposerState>),
}))
