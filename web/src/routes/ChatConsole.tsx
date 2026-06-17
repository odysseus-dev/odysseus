import { Moon, Sun } from "lucide-react"
import { Button } from "@/components/ui/button"
import { useUi } from "@/stores/ui"

export function ChatConsole() {
  const { theme, toggleTheme } = useUi()
  return (
    <>
      <header className="flex h-13 shrink-0 items-center justify-between border-b px-4">
        <div className="text-sm font-semibold">
          Odysseus <span className="font-normal text-muted-foreground">/ v2</span>
        </div>
        <Button variant="ghost" size="icon" onClick={toggleTheme} title="Toggle theme">
          {theme === "dark" ? <Sun /> : <Moon />}
        </Button>
      </header>
      <div className="flex flex-1 items-center justify-center p-8 text-center">
        <div className="max-w-md">
          <h1 className="text-2xl font-semibold tracking-tight">Odysseus v2</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            Monochrome-zinc console shell is live. The chat experience arrives in M1.
          </p>
        </div>
      </div>
    </>
  )
}
