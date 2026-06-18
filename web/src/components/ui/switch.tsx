import { cn } from "@/lib/utils"

// Shared toggle switch.
//
// The knob animates via an INLINE `transform` rather than a Tailwind
// `translate-x-*` utility. In Tailwind v4 `translate-x-*` compiles to the CSS
// `translate:` property backed by `--tw-translate-*` custom properties that are
// registered with `syntax: "*"`, which the spec defines as NON-interpolatable —
// so `transition-transform` could not tween it and the knob snapped to the
// "on" position instead of sliding. A plain inline `transform` is an ordinary
// animatable property, so it transitions smoothly.
export function Switch({
  checked,
  onCheckedChange,
  disabled,
  className,
}: {
  checked: boolean
  onCheckedChange: (next: boolean) => void
  disabled?: boolean
  className?: string
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onCheckedChange(!checked)}
      className={cn(
        "relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full outline-none transition-colors duration-200 ease-out focus-visible:ring-2 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50",
        checked ? "bg-primary" : "bg-input",
        className,
      )}
    >
      <span
        className="pointer-events-none block size-4 rounded-full bg-background shadow-sm transition-transform duration-200 ease-out will-change-transform"
        style={{ transform: checked ? "translateX(1.125rem)" : "translateX(0.125rem)" }}
      />
    </button>
  )
}
