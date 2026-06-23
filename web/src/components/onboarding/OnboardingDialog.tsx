import { useState } from "react"
import { useAuthStatus } from "@/api/auth"
import { useOnboarded, useSavePersonalization, useSetPref } from "@/api/prefs"
import { Mascot } from "@/components/ui/Mascot"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { TONE_OPTIONS, type Tone } from "@/lib/personalization"

const inpCls = "h-9 w-full rounded-md border bg-background px-3 text-sm outline-none focus-visible:border-ring"
const taCls = "w-full resize-none rounded-md border bg-background px-3 py-2 text-sm outline-none focus-visible:border-ring"

// First-run welcome. Shows once per user (server-side `onboarded` flag), lets
// them set a preferred name / about / tone, then writes personalization + flag.
export function OnboardingDialog() {
  const { data: auth } = useAuthStatus()
  const { onboarded, isLoaded } = useOnboarded()
  const { save } = useSavePersonalization()
  const setPref = useSetPref()
  const [nickname, setNickname] = useState("")
  const [about, setAbout] = useState("")
  const [tone, setTone] = useState<Tone>("")
  const [busy, setBusy] = useState(false)

  // Don't render until prefs confirm the user hasn't onboarded. The prefs query
  // requires auth, so a successful load also means the user is signed in.
  if (!isLoaded || onboarded || auth?.authenticated === false) return null

  const finish = async (withValues: boolean) => {
    if (busy) return
    setBusy(true)
    try {
      if (withValues && (nickname.trim() || about.trim() || tone)) {
        await save({ nickname: nickname.trim(), about: about.trim(), instructions: "", tone })
      }
      await setPref.mutateAsync({ key: "onboarded", value: true })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex animate-fade-in items-center justify-center bg-black/50 p-4">
      <div className="max-h-[90vh] w-full max-w-[min(92vw,28rem)] animate-pop-in overflow-y-auto rounded-xl border bg-popover p-6 shadow-lg">
        <div className="flex flex-col items-center text-center">
          <Mascot size={18} className="mb-4 animate-pop-in" title="Odysseus" />
          <h2 className="text-xl font-semibold tracking-tight">Welcome to Odysseus</h2>
          <p className="mt-1.5 text-sm text-muted-foreground">
            A couple of quick things to tailor your assistant. You can change these anytime in Settings.
          </p>
        </div>
        <div className="mt-5 space-y-4">
          <div>
            <label className="mb-1 block text-sm font-medium">What should I call you?</label>
            <input value={nickname} onChange={(e) => setNickname(e.target.value)} placeholder="Your name" maxLength={60} autoFocus className={inpCls} />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium">What do you do? <span className="font-normal text-muted-foreground">(optional)</span></label>
            <textarea value={about} onChange={(e) => setAbout(e.target.value)} rows={2} maxLength={1500}
              placeholder="e.g. Backend engineer, mostly Python & TypeScript." className={taCls} />
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium">Preferred response tone</label>
            <div className="flex flex-wrap gap-1.5">
              {TONE_OPTIONS.map((t) => (
                <button key={t.value || "default"} type="button" onClick={() => setTone(t.value as Tone)} title={t.hint}
                  className={cn("rounded-full border px-3 py-1 text-sm transition-colors",
                    tone === t.value ? "border-foreground bg-accent text-foreground" : "text-muted-foreground hover:bg-accent hover:text-foreground")}>
                  {t.label}
                </button>
              ))}
            </div>
          </div>
        </div>
        <div className="mt-6 flex items-center justify-between gap-2">
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => finish(false)}>Skip for now</Button>
          <Button size="sm" disabled={busy} onClick={() => finish(true)}>{busy ? "Saving…" : "Get started"}</Button>
        </div>
      </div>
    </div>
  )
}
