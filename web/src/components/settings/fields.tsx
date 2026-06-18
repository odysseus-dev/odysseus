import { useState } from "react"
import { X, Plus } from "lucide-react"
import { Switch } from "@/components/ui/switch"
import type { ReactNode } from "react"

// Reusable settings field primitives. Text/number/textarea commit on blur (or
// Enter) so we don't POST on every keystroke; they're uncontrolled and keyed by
// the committed value so a server refresh resets them cleanly (no effect).

const inp = "h-9 w-full rounded-md border bg-background px-3 text-sm outline-none focus-visible:border-ring disabled:opacity-50"

export function SectionCard({ children }: { children: ReactNode }) {
  return <div className="space-y-3 rounded-lg border bg-card p-3">{children}</div>
}

export function Row({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 py-0.5">
      <span className="min-w-0">
        <span className="block text-sm">{label}</span>
        {hint && <span className="block text-xs text-muted-foreground">{hint}</span>}
      </span>
      <span className="shrink-0">{children}</span>
    </div>
  )
}

export function FieldRow({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block space-y-1 py-0.5">
      <span className="block text-sm">{label}</span>
      {hint && <span className="block text-xs text-muted-foreground">{hint}</span>}
      {children}
    </label>
  )
}

export function SettingSwitch({ label, hint, value, onChange, disabled }: { label: string; hint?: string; value: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return <Row label={label} hint={hint}><Switch checked={value} onCheckedChange={onChange} disabled={disabled} /></Row>
}

export function SettingSelect({ label, hint, value, onChange, options, disabled }: { label: string; hint?: string; value: string; onChange: (v: string) => void; options: { value: string; label: string }[]; disabled?: boolean }) {
  return (
    <Row label={label} hint={hint}>
      <select value={value} onChange={(e) => onChange(e.target.value)} disabled={disabled} className="h-9 max-w-[60vw] min-w-40 rounded-md border bg-background px-2 text-sm outline-none focus-visible:border-ring disabled:opacity-50">
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </Row>
  )
}

export function SettingText({ label, hint, value, onCommit, type = "text", placeholder, disabled }: { label: string; hint?: string; value: string; onCommit: (v: string) => void; type?: string; placeholder?: string; disabled?: boolean }) {
  return (
    <FieldRow label={label} hint={hint}>
      <input key={value} defaultValue={value} type={type} placeholder={placeholder} autoComplete="off" disabled={disabled} className={inp}
        onBlur={(e) => { if (e.target.value !== value) onCommit(e.target.value) }}
        onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur() }} />
    </FieldRow>
  )
}

export function SettingNumber({ label, hint, value, onCommit, min, max, step, disabled }: { label: string; hint?: string; value: number; onCommit: (v: number) => void; min?: number; max?: number; step?: number; disabled?: boolean }) {
  return (
    <FieldRow label={label} hint={hint}>
      <input key={value} defaultValue={value} type="number" min={min} max={max} step={step} disabled={disabled} className={inp}
        onBlur={(e) => { const n = e.target.value === "" ? 0 : Number(e.target.value); if (!Number.isNaN(n) && n !== value) onCommit(n) }}
        onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur() }} />
    </FieldRow>
  )
}

export function SettingTextarea({ label, hint, value, onCommit, rows = 3, placeholder, mono }: { label: string; hint?: string; value: string; onCommit: (v: string) => void; rows?: number; placeholder?: string; mono?: boolean }) {
  return (
    <FieldRow label={label} hint={hint}>
      <textarea key={value} defaultValue={value} rows={rows} placeholder={placeholder}
        className={`w-full resize-y rounded-md border bg-background px-3 py-2 text-sm outline-none focus-visible:border-ring ${mono ? "font-mono text-xs" : ""}`}
        onBlur={(e) => { if (e.target.value !== value) onCommit(e.target.value) }} />
    </FieldRow>
  )
}

// Ordered list-of-strings editor (search fallback chain, extra path roots, …).
export function StringListEditor({ label, hint, value, onChange, placeholder }: { label: string; hint?: string; value: string[]; onChange: (v: string[]) => void; placeholder?: string }) {
  const [draft, setDraft] = useState("")
  const add = () => { const v = draft.trim(); if (!v) return; onChange([...value, v]); setDraft("") }
  return (
    <FieldRow label={label} hint={hint}>
      <div className="space-y-1.5">
        {value.map((item, i) => (
          <div key={i} className="flex items-center gap-2">
            <span className="min-w-0 flex-1 truncate rounded-md border bg-background px-3 py-1.5 text-sm">{item}</span>
            <button onClick={() => onChange(value.filter((_, j) => j !== i))} className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-destructive"><X className="size-4" /></button>
          </div>
        ))}
        <div className="flex items-center gap-2">
          <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder={placeholder} className={inp}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add() } }} />
          <button onClick={add} disabled={!draft.trim()} className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-40"><Plus className="size-4" /></button>
        </div>
      </div>
    </FieldRow>
  )
}
