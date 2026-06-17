import { create } from "zustand"
type Mode = "chat" | "agent"
type ToggleKey = "useWeb" | "useResearch" | "allowBash" | "useRag" | "incognito"
interface ComposerState {
  model: string; endpointId: string; endpointUrl: string; presetId: string
  mode: Mode
  useWeb: boolean; useResearch: boolean; allowBash: boolean; useRag: boolean; incognito: boolean
  setModel: (model: string, endpointId: string, endpointUrl: string) => void
  setMode: (m: Mode) => void
  setPreset: (id: string) => void
  toggle: (k: ToggleKey) => void
}
export const useComposer = create<ComposerState>((set, get) => ({
  model: "", endpointId: "", endpointUrl: "", presetId: "",
  mode: "chat",
  useWeb: false, useResearch: false, allowBash: true, useRag: true, incognito: false,
  setModel: (model, endpointId, endpointUrl) => set({ model, endpointId, endpointUrl }),
  setMode: (mode) => set({ mode }),
  setPreset: (presetId) => set({ presetId }),
  toggle: (k) => set({ [k]: !get()[k] } as Partial<ComposerState>),
}))
