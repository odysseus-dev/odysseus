import { create } from "zustand"

export interface Toast { id: number; message: string; kind: "error" | "info" | "success" }
interface ToastState {
  toasts: Toast[]
  push: (message: string, kind?: Toast["kind"]) => void
  dismiss: (id: number) => void
}

let seq = 0
export const useToast = create<ToastState>((set) => ({
  toasts: [],
  push: (message, kind = "error") => {
    const id = ++seq
    set((s) => ({ toasts: [...s.toasts.slice(-3), { id, message, kind }] }))
    setTimeout(() => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })), 5000)
  },
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}))

// Convenience for non-component code (api error surfaces, etc.)
export const toast = (message: string, kind?: Toast["kind"]) => useToast.getState().push(message, kind)
