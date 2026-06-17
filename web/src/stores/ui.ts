import { create } from "zustand"
import { persist } from "zustand/middleware"
type Theme = "light" | "dark"
export type FontChoice = "sans" | "serif" | "mono"
export type Density = "compact" | "comfortable" | "spacious"
interface UiState {
  theme: Theme; setTheme: (t: Theme) => void; toggleTheme: () => void
  sidebarCollapsed: boolean; setSidebar: (v: boolean) => void; toggleSidebar: () => void
  accent: string; setAccent: (v: string) => void
  font: FontChoice; setFont: (v: FontChoice) => void
  density: Density; setDensity: (v: Density) => void
}
export const useUi = create<UiState>()(
  persist(
    (set, get) => ({
      theme: "dark",
      setTheme: (t) => set({ theme: t }),
      toggleTheme: () => set({ theme: get().theme === "dark" ? "light" : "dark" }),
      sidebarCollapsed: false,
      setSidebar: (v) => set({ sidebarCollapsed: v }),
      toggleSidebar: () => set({ sidebarCollapsed: !get().sidebarCollapsed }),
      accent: "",
      setAccent: (v) => set({ accent: v }),
      font: "sans",
      setFont: (v) => set({ font: v }),
      density: "comfortable",
      setDensity: (v) => set({ density: v }),
    }),
    { name: "odysseus-v2-ui" },
  ),
)
