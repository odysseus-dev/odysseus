import { create } from "zustand"
import { persist } from "zustand/middleware"
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
export const useComposer = create<ComposerState>()(
  persist(
    (set, get) => ({
      model: "", endpointId: "", endpointUrl: "", presetId: "",
      mode: "chat",
      useWeb: false, useResearch: false, allowBash: true, useRag: true, incognito: false,
      setModel: (model, endpointId, endpointUrl) => set({ model, endpointId, endpointUrl }),
      setMode: (mode) => set({ mode }),
      setPreset: (presetId) => set({ presetId }),
      toggle: (k) => set({ [k]: !get()[k] } as Partial<ComposerState>),
    }),
    {
      name: "odysseus-composer",
      // Persist the user's model/mode/tool choices across reloads. incognito is
      // intentionally omitted so it always resets to off on a fresh load.
      partialize: (s) => ({
        model: s.model, endpointId: s.endpointId, endpointUrl: s.endpointUrl, presetId: s.presetId,
        mode: s.mode, useWeb: s.useWeb, useResearch: s.useResearch, allowBash: s.allowBash, useRag: s.useRag,
      }),
    },
  ),
)
