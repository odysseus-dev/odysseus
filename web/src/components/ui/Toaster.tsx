import { X, AlertCircle, CheckCircle2, Info } from "lucide-react"
import { useToast } from "@/stores/toast"
import { cn } from "@/lib/utils"

const ICON = { error: AlertCircle, success: CheckCircle2, info: Info }

// Bottom-right toast stack. Surfaces mutation/API errors (and optional info/
// success notices) that would otherwise fail silently.
export function Toaster() {
  const { toasts, dismiss } = useToast()
  if (!toasts.length) return null
  return (
    <div className="fixed bottom-4 right-4 z-[100] flex w-[22rem] max-w-[calc(100vw-2rem)] flex-col gap-2">
      {toasts.map((t) => {
        const Icon = ICON[t.kind]
        return (
          <div
            key={t.id}
            role="status"
            className={cn(
              "flex animate-pop-in items-start gap-2.5 rounded-lg border bg-card p-3 text-sm shadow-lg",
              t.kind === "error" && "border-destructive/40",
              t.kind === "success" && "border-emerald-500/40",
            )}
          >
            <Icon className={cn("mt-0.5 size-4 shrink-0", t.kind === "error" ? "text-destructive" : t.kind === "success" ? "text-emerald-600 dark:text-emerald-400" : "text-muted-foreground")} />
            <span className="min-w-0 flex-1 break-words text-foreground">{t.message}</span>
            <button onClick={() => dismiss(t.id)} title="Dismiss" className="shrink-0 text-muted-foreground transition-colors hover:text-foreground"><X className="size-3.5" /></button>
          </div>
        )
      })}
    </div>
  )
}
