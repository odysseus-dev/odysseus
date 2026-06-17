import { X } from "lucide-react"

const GROUPS: { title: string; rows: [string, string][] }[] = [
  { title: "General", rows: [["⌘/Ctrl K", "New chat"], ["⌘/Ctrl B", "Toggle sidebar"], ["⌘/Ctrl J", "Toggle theme"], ["?", "Show this help"], ["Esc", "Close"]] },
  { title: "Go to (press g, then…)", rows: [["g c", "Chat"], ["g k", "Compare"], ["g m", "Memory"], ["g i", "Gallery"], ["g e", "Email"], ["g n", "Notes"], ["g t", "Tasks"], ["g b", "Cookbook"], ["g s", "Skills"], ["g l", "Library"], ["g ,", "Settings"]] },
]

export function ShortcutsOverlay({ onClose }: { onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div className="w-full max-w-md rounded-xl border bg-popover p-4 shadow-lg" onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between">
          <div className="text-sm font-semibold">Keyboard shortcuts</div>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground"><X className="size-4" /></button>
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {GROUPS.map((g) => (
            <div key={g.title}>
              <div className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">{g.title}</div>
              <div className="space-y-1">
                {g.rows.map(([k, label]) => (
                  <div key={k} className="flex items-center justify-between gap-3 text-sm">
                    <span className="text-muted-foreground">{label}</span>
                    <kbd className="rounded border bg-muted px-1.5 py-0.5 font-mono text-[11px]">{k}</kbd>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
