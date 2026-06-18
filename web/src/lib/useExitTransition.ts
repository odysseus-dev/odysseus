import { useEffect, useState } from "react"

// Keeps a transient element mounted while it animates out, so close/open both
// animate without pulling in an animation library. Returns whether to render
// and whether it's currently in the closing phase (apply the exit class then).
//
//   const { render, closing } = useExitTransition(open, 150)
//   if (!render) return null
//   <div className={closing ? "animate-pop-out" : "animate-pop-in"} />
//
// `durationMs` must match the exit animation's duration.
export function useExitTransition(open: boolean, durationMs = 150): { render: boolean; closing: boolean } {
  const [state, setState] = useState<{ render: boolean; closing: boolean }>({ render: open, closing: false })
  useEffect(() => {
    if (open) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- mount/show in response to the open prop
      setState({ render: true, closing: false })
      return
    }
    setState((s) => (s.render ? { render: true, closing: true } : s))
    const t = window.setTimeout(() => setState({ render: false, closing: false }), durationMs)
    return () => window.clearTimeout(t)
  }, [open, durationMs])
  return state
}
