import type { CSSProperties } from "react"
import { cn } from "@/lib/utils"

// The Odysseus mascot: a 3×3 grid that ripples along a diagonal (adapted from
// 21st.dev loader-4). Cell colours derive from --primary, so it tracks the
// accent/theme selection. Doubles as the thinking/reasoning loader and a brand
// mark. `size` is the cell edge in px.
export function Mascot({ size = 12, className, title }: { size?: number; className?: string; title?: string }) {
  return (
    <div
      className={cn("mascot", className)}
      style={{ "--cell-size": `${size}px` } as CSSProperties}
      role="img"
      aria-label={title || "Odysseus"}
    >
      {Array.from({ length: 9 }, (_, i) => <div key={i} className="cell" />)}
    </div>
  )
}
