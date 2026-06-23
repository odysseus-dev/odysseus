import { create } from "zustand"
import { persist } from "zustand/middleware"
type Mode = "chat" | "agent"
type ToggleKey = "useWeb" | "useResearch" | "allowBash" | "useRag" | "incognito"
export type GroupMode = "parallel" | "round-robin"
export interface GroupParticipant {
  id: string
  model: string
  display: string
  endpointId: string
  endpointUrl: string
  sessionId?: string
  groupName?: string
  personaId?: string
  personaName?: string
  personaPrompt?: string
}
interface ComposerState {
  model: string; endpointId: string; endpointUrl: string; presetId: string
  mode: Mode
  useWeb: boolean; useResearch: boolean; allowBash: boolean; useRag: boolean; incognito: boolean
  workspace: string
  promptPrefix: string; promptSuffix: string
  groupActive: boolean; groupMode: GroupMode; groupParticipants: GroupParticipant[]; groupParentId: string
  setModel: (model: string, endpointId: string, endpointUrl: string) => void
  setMode: (m: Mode) => void
  setPreset: (id: string) => void
  setWorkspace: (workspace: string) => void
  setPromptInject: (prefix: string, suffix: string) => void
  clearPromptInject: () => void
  setGroupActive: (active: boolean) => void
  setGroupMode: (mode: GroupMode) => void
  addGroupParticipant: (participant: GroupParticipant) => void
  removeGroupParticipant: (id: string) => void
  setGroupParticipants: (participants: GroupParticipant[], mode?: GroupMode) => void
  setGroupParticipantPersona: (id: string, persona: { personaId?: string; personaName?: string; personaPrompt?: string }) => void
  setGroupRuntime: (parentId: string, participants: GroupParticipant[]) => void
  clearGroup: () => void
  toggle: (k: ToggleKey) => void
}
export const useComposer = create<ComposerState>()(
  persist(
    (set, get) => ({
      model: "", endpointId: "", endpointUrl: "", presetId: "",
      mode: "chat",
      useWeb: false, useResearch: false, allowBash: true, useRag: true, incognito: false,
      workspace: "",
      promptPrefix: "", promptSuffix: "",
      groupActive: false, groupMode: "round-robin", groupParticipants: [], groupParentId: "",
      setModel: (model, endpointId, endpointUrl) => set({ model, endpointId, endpointUrl }),
      setMode: (mode) => set({ mode }),
      setPreset: (presetId) => set({ presetId }),
      setWorkspace: (workspace) => set({ workspace }),
      setPromptInject: (promptPrefix, promptSuffix) => set({ promptPrefix, promptSuffix }),
      clearPromptInject: () => set({ promptPrefix: "", promptSuffix: "" }),
      setGroupActive: (groupActive) => set({ groupActive }),
      setGroupMode: (groupMode) => set({ groupMode }),
      addGroupParticipant: (participant) => set((s) => {
        if (s.groupParticipants.some((p) => p.id === participant.id) || s.groupParticipants.length >= 8) return {}
        return {
          groupParticipants: [...s.groupParticipants.map((p) => ({ ...p, sessionId: undefined })), participant],
          groupParentId: "",
          groupActive: true,
        }
      }),
      removeGroupParticipant: (id) => set((s) => {
        const groupParticipants = s.groupParticipants.filter((p) => p.id !== id).map((p) => ({ ...p, sessionId: undefined }))
        return { groupParticipants, groupParentId: "", groupActive: groupParticipants.length > 0 && s.groupActive }
      }),
      setGroupParticipants: (participants, groupMode) => set(() => {
        const seen = new Set<string>()
        const groupParticipants = participants
          .filter((p) => {
            if (!p.id || seen.has(p.id)) return false
            seen.add(p.id)
            return true
          })
          .slice(0, 8)
          .map((p) => ({ ...p, sessionId: undefined, groupName: p.personaName || p.display }))
        return {
          groupParticipants,
          groupParentId: "",
          groupActive: groupParticipants.length > 0,
          ...(groupMode ? { groupMode } : {}),
        }
      }),
      setGroupParticipantPersona: (id, persona) => set((s) => ({
        groupParticipants: s.groupParticipants.map((p) => (
          p.id === id
            ? { ...p, ...persona, sessionId: undefined, groupName: persona.personaName || p.display }
            : { ...p, sessionId: undefined }
        )),
        groupParentId: "",
      })),
      setGroupRuntime: (groupParentId, groupParticipants) => set({ groupParentId, groupParticipants, groupActive: true }),
      clearGroup: () => set({ groupActive: false, groupParticipants: [], groupParentId: "" }),
      toggle: (k) => set({ [k]: !get()[k] } as Partial<ComposerState>),
    }),
    {
      name: "odysseus-composer",
      // Persist the user's model/mode/tool choices across reloads. incognito is
      // intentionally omitted so it always resets to off on a fresh load.
      partialize: (s) => ({
        model: s.model, endpointId: s.endpointId, endpointUrl: s.endpointUrl, presetId: s.presetId,
        mode: s.mode, useWeb: s.useWeb, useResearch: s.useResearch, allowBash: s.allowBash, useRag: s.useRag, workspace: s.workspace,
        promptPrefix: s.promptPrefix, promptSuffix: s.promptSuffix,
        groupActive: s.groupActive, groupMode: s.groupMode, groupParticipants: s.groupParticipants, groupParentId: s.groupParentId,
      }),
    },
  ),
)
