import { Component, type ReactNode } from "react"

interface Props { children: ReactNode; fallback?: ReactNode }
interface State { error: Error | null }

// Catches render-time exceptions so one bad view (e.g. malformed KaTeX in a
// message, or a route throw) can't blank the whole app. Wrap routes (keyed by
// pathname so navigation auto-resets) and the markdown renderer.
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }
  static getDerivedStateFromError(error: Error): State { return { error } }
  componentDidCatch(error: Error, info: unknown) { console.error("UI render error:", error, info) }
  render() {
    if (this.state.error) {
      if (this.props.fallback !== undefined) return this.props.fallback
      return (
        <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
          <p className="text-sm font-medium text-foreground">Something went wrong rendering this view.</p>
          <p className="max-w-md break-words text-xs text-muted-foreground">{this.state.error.message}</p>
          <button
            onClick={() => this.setState({ error: null })}
            className="rounded-md border px-3 py-1.5 text-sm hover:bg-accent"
          >
            Try again
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
