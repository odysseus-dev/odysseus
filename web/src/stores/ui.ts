import { create } from "zustand"
import { persist } from "zustand/middleware"
type Theme = "light" | "dark"
interface UiState {
  theme: Theme; setTheme: (t: Theme) => void; toggleTheme: () => void
  sidebarCollapsed: boolean; setSidebar: (v: boolean) => void; toggleSidebar: () => void
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
    }),
    { name: "odysseus-v2-ui" },
  ),
)
