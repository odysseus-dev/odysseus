import { useInbox } from "@/api/email"
import { cn } from "@/lib/utils"

export function EmailRoute() {
  const { data } = useInbox()
  const emails = data?.emails || []
  return (
    <div className="mx-auto flex h-full w-full max-w-3xl flex-col">
      <header className="flex h-13 shrink-0 items-center border-b px-4 text-sm font-semibold">
        Email <span className="ml-2 font-normal text-muted-foreground">· Inbox</span>
      </header>
      <div className="flex-1 overflow-y-auto">
        {data?.error && <p className="p-4 text-sm text-muted-foreground">No mail account connected (or unavailable).</p>}
        <div className="divide-y">
          {emails.map((m) => {
            const from = m.from || m.from_addr || m.sender || "Unknown"
            const unread = m.unread ?? m.seen === false
            return (
              <div key={m.uid} className="flex cursor-pointer items-baseline gap-3 px-4 py-3 hover:bg-accent/50">
                <div className={cn("w-44 shrink-0 truncate text-sm", unread ? "font-semibold text-foreground" : "text-muted-foreground")}>{from}</div>
                <div className="min-w-0 flex-1">
                  <span className={cn("text-sm", unread ? "font-medium text-foreground" : "text-muted-foreground")}>{m.subject || "(no subject)"}</span>
                  {(m.snippet || m.preview) && <span className="ml-2 text-sm text-muted-foreground">— {m.snippet || m.preview}</span>}
                </div>
                {m.date && <div className="shrink-0 text-xs text-muted-foreground">{new Date(m.date).toLocaleDateString()}</div>}
              </div>
            )
          })}
        </div>
        {!data?.error && emails.length === 0 && <p className="p-8 text-center text-sm text-muted-foreground">Inbox empty.</p>}
      </div>
    </div>
  )
}
