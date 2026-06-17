import { useState } from "react"
import { ArrowLeft } from "lucide-react"
import { useInbox, useEmail } from "@/api/email"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

function Reader({ uid, onBack }: { uid: string; onBack: () => void }) {
  const { data, isLoading } = useEmail(uid)
  const html = data?.body_html || data?.html
  const text = data?.body_text || data?.body || data?.text
  const from = data?.from || data?.from_addr || data?.sender || ""
  return (
    <div className="flex h-full flex-col">
      <header className="flex h-13 shrink-0 items-center gap-2 border-b px-3">
        <Button variant="ghost" size="icon" onClick={onBack} title="Back"><ArrowLeft className="size-4" /></Button>
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold">{data?.subject || "(no subject)"}</div>
          <div className="truncate text-xs text-muted-foreground">{from}{data?.date ? ` · ${new Date(data.date).toLocaleString()}` : ""}</div>
        </div>
      </header>
      {isLoading ? (
        <div className="p-6 text-sm text-muted-foreground">Loading…</div>
      ) : data?.error ? (
        <div className="p-6 text-sm text-muted-foreground">Couldn't load this message.</div>
      ) : html ? (
        <iframe title="email" sandbox="" srcDoc={html} className="min-h-0 flex-1 w-full bg-white" />
      ) : (
        <pre className="flex-1 overflow-auto whitespace-pre-wrap p-6 text-sm">{text || "(empty)"}</pre>
      )}
    </div>
  )
}

export function EmailRoute() {
  const { data } = useInbox()
  const [uid, setUid] = useState<string | null>(null)
  const emails = data?.emails || []
  if (uid) return <div className="mx-auto h-full w-full max-w-3xl"><Reader uid={uid} onBack={() => setUid(null)} /></div>
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
              <div key={m.uid} onClick={() => setUid(m.uid)} className="flex cursor-pointer items-baseline gap-3 px-4 py-3 hover:bg-accent/50">
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
