import { useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { usePersonalization, useSavePersonalization } from "@/api/prefs"
import { TONE_OPTIONS, type Personalization, type Tone } from "@/lib/personalization"

const inpCls = "h-9 w-full rounded-md border bg-background px-3 text-sm outline-none focus-visible:border-ring"
const taCls = "w-full resize-none rounded-md border bg-background px-3 py-2 text-sm outline-none focus-visible:border-ring"

export function PersonalizationSection() {
  const { data, isLoaded } = usePersonalization()
  const { save, isPending } = useSavePersonalization()
  const [form, setForm] = useState<Personalization>(data)
  const [saved, setSaved] = useState(false)
  // Seed the form once prefs finish loading. Keyed on isLoaded (not data) so a
  // refetch after Save doesn't clobber in-progress edits.
  // eslint-disable-next-line react-hooks/set-state-in-effect, react-hooks/exhaustive-deps -- one-time seed from server prefs
  useEffect(() => { setForm(data) }, [isLoaded])

  const set = (patch: Partial<Personalization>) => { setForm((f) => ({ ...f, ...patch })); setSaved(false) }
  const onSave = async () => {
    await save({
      nickname: (form.nickname || "").trim(),
      about: (form.about || "").trim(),
      instructions: (form.instructions || "").trim(),
      tone: form.tone || "",
    })
    setSaved(true)
  }

  return (
    <section>
      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Personalization</h2>
      <p className="mb-3 text-sm text-muted-foreground">
        Tell Odysseus how to address you and how you'd like it to respond. These apply to every chat (except incognito).
      </p>
      <div className="space-y-4 rounded-lg border bg-card p-3">
        <div>
          <label className="mb-1 block text-sm font-medium">What should Odysseus call you?</label>
          <input value={form.nickname || ""} onChange={(e) => set({ nickname: e.target.value })} placeholder="e.g. Noah" maxLength={60} className={inpCls} />
        </div>
        <div>
          <label className="mb-1 block text-sm font-medium">About you</label>
          <p className="mb-1.5 text-xs text-muted-foreground">Your role, what you work on, anything that helps tailor responses.</p>
          <textarea value={form.about || ""} onChange={(e) => set({ about: e.target.value })} rows={3} maxLength={1500}
            placeholder="e.g. I'm a backend engineer working mostly in Python and TypeScript." className={taCls} />
        </div>
        <div>
          <label className="mb-1 block text-sm font-medium">Custom instructions</label>
          <p className="mb-1.5 text-xs text-muted-foreground">How should Odysseus respond? Formatting, depth, things to avoid.</p>
          <textarea value={form.instructions || ""} onChange={(e) => set({ instructions: e.target.value })} rows={4} maxLength={2000}
            placeholder="e.g. Be direct. Prefer code examples over prose. Don't apologize." className={taCls} />
        </div>
        <div>
          <label className="mb-1.5 block text-sm font-medium">Response tone</label>
          <div className="flex flex-wrap gap-1.5">
            {TONE_OPTIONS.map((t) => (
              <button key={t.value || "default"} type="button" onClick={() => set({ tone: t.value as Tone })} title={t.hint}
                className={cn("rounded-full border px-3 py-1 text-sm transition-colors",
                  (form.tone || "") === t.value ? "border-foreground bg-accent text-foreground" : "text-muted-foreground hover:bg-accent hover:text-foreground")}>
                {t.label}
              </button>
            ))}
          </div>
        </div>
        <div className="flex items-center justify-end gap-3 border-t pt-3">
          {saved && <span className="text-xs text-muted-foreground">Saved.</span>}
          <Button size="sm" disabled={isPending} onClick={onSave}>{isPending ? "Saving…" : "Save"}</Button>
        </div>
      </div>
    </section>
  )
}
