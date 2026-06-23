import { useEffect, useMemo, useState } from "react"
import { useNavigate } from "react-router-dom"
import {
  Bell,
  Bot,
  CalendarDays,
  Check,
  Clipboard,
  Copy,
  Edit3,
  FileText,
  History,
  Link2,
  Mail,
  Pause,
  Play,
  RotateCcw,
  RotateCw,
  Search,
  Sparkles,
  Square,
  Trash2,
  Webhook,
  X,
  Zap,
  type LucideIcon,
} from "lucide-react"
import { useModels } from "@/api/models"
import { createSession } from "@/api/sessions"
import {
  useRecentTaskRuns,
  useTaskActions,
  useTaskEvents,
  useTaskMutations,
  useTaskOutputTargets,
  useTaskRuns,
  useTasks,
  useTasksOnboarding,
  useUrgentEmailSettings,
  type TaskPayload,
} from "@/api/tasks"
import { Markdown } from "@/components/chat/Markdown"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { apiFetch } from "@/lib/api"
import { cn } from "@/lib/utils"
import { toast } from "@/stores/toast"
import type { Task, TaskRun } from "@/types"

const inputClass = "h-9 w-full rounded-md border bg-background px-3 text-sm outline-none focus-visible:border-ring"
const textareaClass = "w-full resize-y rounded-md border bg-background px-3 py-2 text-sm outline-none focus-visible:border-ring"
const labelClass = "mb-1.5 block text-xs font-medium text-muted-foreground"
const iconBtn = "inline-flex size-8 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-50"
const chipBase = "inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-xs font-medium transition-colors"

const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
const PERSONAS = [
  ["", "Default"],
  ["socrates", "Socrates"],
  ["razor", "Razor"],
  ["nietzsche", "Nietzsche"],
  ["spark", "Spark"],
  ["odysseus", "Odysseus"],
]
const TASK_TYPES = [
  { value: "llm", label: "Prompt", icon: Bot },
  { value: "research", label: "Research", icon: Search },
  { value: "action", label: "Action", icon: Zap },
]
const TRIGGERS = [
  { value: "schedule", label: "Schedule", icon: CalendarDays },
  { value: "event", label: "Event", icon: Bell },
  { value: "webhook", label: "Webhook", icon: Webhook },
]
const PRESETS = [
  { label: "Prompt on schedule", task_type: "llm", trigger_type: "schedule" },
  { label: "Prompt on event", task_type: "llm", trigger_type: "event" },
  { label: "Research on schedule", task_type: "research", trigger_type: "schedule" },
  { label: "Research on event", task_type: "research", trigger_type: "event" },
  { label: "Action on schedule", task_type: "action", trigger_type: "schedule" },
  { label: "Action on event", task_type: "action", trigger_type: "event" },
  { label: "Webhook triggered", task_type: "llm", trigger_type: "webhook" },
]
const CATEGORY_ORDER = ["Cookbook", "Other", "Calendar", "Email", "Chats", "Documents", "Memory", "Research", "Skills", "Assistant", "System"]
const CATEGORY_MAP: Record<string, string> = {
  cookbook_serve: "Cookbook",
  create_calendar_event: "Calendar",
  extract_email_events: "Calendar",
  summarize_emails: "Email",
  draft_email_replies: "Email",
  learn_sender_signatures: "Email",
  check_email_urgency: "Email",
  tidy_sessions: "Chats",
  tidy_documents: "Documents",
  consolidate_memory: "Memory",
  daily_brief: "Assistant",
  test_skills: "Skills",
  audit_skills: "Skills",
  ssh_command: "System",
  run_script: "System",
  run_local: "System",
}
const CLEARABLE_ACTIONS = new Set(["summarize_emails", "draft_email_replies", "extract_email_events", "learn_sender_signatures", "check_email_urgency"])
const CLEAR_LABELS: Record<string, string> = {
  summarize_emails: "email summaries",
  draft_email_replies: "AI reply drafts",
  extract_email_events: "email calendar cache",
  learn_sender_signatures: "sender signatures",
  check_email_urgency: "email tags",
}
const ACTIVITY_LABELS = [
  { label: "email", kw: /\b(email|inbox|mail|smtp|imap|reply|spam|urgency)\b/i },
  { label: "research", kw: /\b(research|web ?search|deep[-_ ]research|sources?|investigate)\b/i },
  { label: "cookbook", kw: /\b(cookbook|model[-_ ]?(serve|download)|hf|huggingface|vllm|llama|ollama)\b/i },
  { label: "calendar", kw: /\b(calendar|event|meeting|appointment|schedule)\b/i },
  { label: "reminders", kw: /\b(reminder|note|notify|alert)\b/i },
  { label: "check-in", kw: /\b(check[-_ ]?in|morning|evening|daily|standup)\b/i },
  { label: "memory", kw: /\b(memory|memories|remember|recall)\b/i },
]

type TaskFormState = {
  name: string
  task_type: string
  trigger_type: string
  prompt: string
  action: string
  schedule: string
  scheduled_time: string
  scheduled_day: string
  scheduled_date: string
  cron_expression: string
  trigger_event: string
  trigger_count: string
  output_target: string
  model_key: string
  then_task_id: string
  notifications_enabled: boolean
  character_id: string
  urgent_email_prompt: string
}
type TaskLike = Partial<Omit<TaskPayload, "endpoint_url" | "then_task_id" | "character_id">> & Partial<Task>

type ModelEndpoint = {
  url?: string
  endpoint_id?: string
  endpoint_name?: string
  host?: string
  offline?: boolean
  model_type?: string
  models?: string[]
  models_extra?: string[]
}

function fmtTime(value?: string | null) {
  if (!value) return ""
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })
}
function fmtShort(value?: string | null) {
  if (!value) return ""
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString([], { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })
}
function compact(text?: string | null, max = 280) {
  const s = (text || "").trim()
  if (!s) return ""
  return s.length > max ? `${s.slice(0, max)}...` : s
}
function localTimeToUtc(hhmm: string) {
  const [h, m] = hhmm.split(":").map(Number)
  const d = new Date()
  d.setHours(Number.isFinite(h) ? h : 9, Number.isFinite(m) ? m : 0, 0, 0)
  return `${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}`
}
function utcTimeToLocal(hhmm?: string | null) {
  if (!hhmm) return "09:00"
  const [h, m] = hhmm.split(":").map(Number)
  const d = new Date()
  d.setUTCHours(Number.isFinite(h) ? h : 9, Number.isFinite(m) ? m : 0, 0, 0)
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`
}
function localDateValue(value?: string | null) {
  const d = value ? new Date(value) : new Date()
  if (Number.isNaN(d.getTime())) return new Date().toISOString().slice(0, 10)
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, "0")
  const day = String(d.getDate()).padStart(2, "0")
  return `${y}-${m}-${day}`
}
function localTimeValue(value?: string | null, fallback?: string | null) {
  const d = value ? new Date(value) : null
  if (d && !Number.isNaN(d.getTime())) return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`
  return utcTimeToLocal(fallback)
}
function ordinal(n: number) {
  const v = n % 100
  if (v >= 11 && v <= 13) return "th"
  return ["th", "st", "nd", "rd"][Math.min(n % 10, 4)] || "th"
}
function scheduleLabel(t: Task) {
  const trigger = t.trigger_type || "schedule"
  if (trigger === "event") return `Every ${t.trigger_count || 1} ${(t.trigger_event || "event").replaceAll("_", " ")}`
  if (trigger === "webhook") return "Webhook"
  if (t.schedule === "cron") return `Cron: ${t.cron_expression || t.cron || "?"}`
  if (t.schedule === "once") return t.scheduled_date ? `Once: ${fmtTime(t.scheduled_date)}` : "Once"
  const local = utcTimeToLocal(t.scheduled_time)
  if (t.schedule === "weekly") return `Weekly on ${DAYS[t.scheduled_day ?? 0] || "Monday"} at ${local}`
  if (t.schedule === "monthly") {
    const d = t.scheduled_day || 1
    return `Monthly on ${d}${ordinal(d)} at ${local}`
  }
  if (t.schedule === "daily") return `Daily at ${local}`
  if (t.next_run || t.next_run_at) return `Next: ${fmtTime(t.next_run || t.next_run_at)}`
  return [t.schedule, local].filter(Boolean).join(" at ") || "No schedule"
}
function categoryFor(task: Pick<Task, "task_type" | "action" | "crew_member_id">) {
  if (task.task_type === "action" && task.action) return CATEGORY_MAP[task.action] || "Other"
  if (task.task_type === "research") return "Research"
  if (task.task_type === "llm" || !task.task_type) return task.crew_member_id ? "Assistant" : "Other"
  return "Other"
}
function CategoryIcon({ category }: { category: string }) {
  if (category === "Calendar") return <CalendarDays className="size-4" />
  if (category === "Email") return <Mail className="size-4" />
  if (category === "Documents") return <FileText className="size-4" />
  if (category === "Research") return <Search className="size-4" />
  if (category === "Assistant") return <Bot className="size-4" />
  if (category === "Skills" || category === "System") return <Zap className="size-4" />
  if (category === "Cookbook") return <Clipboard className="size-4" />
  return <Sparkles className="size-4" />
}
function activityCategory(name?: string, action?: string) {
  if (action && CATEGORY_MAP[action]) return CATEGORY_MAP[action].toLowerCase()
  const text = name || ""
  for (const item of ACTIVITY_LABELS) if (item.kw.test(text)) return item.label
  return "other"
}
function statusTone(status?: string) {
  const s = (status || "").toLowerCase()
  if (s === "active" || s === "running" || s === "success" || s === "ok") return "border-emerald-500/25 bg-emerald-500/10 text-emerald-600 dark:text-emerald-300"
  if (s === "paused" || s === "queued") return "border-amber-500/25 bg-amber-500/10 text-amber-600 dark:text-amber-300"
  if (s === "error" || s === "failed" || s === "aborted") return "border-destructive/25 bg-destructive/10 text-destructive"
  return "border-border bg-muted text-muted-foreground"
}
function StatusBadge({ status, onClick }: { status?: string; onClick?: () => void }) {
  const label = status || "unknown"
  const cls = cn("inline-flex h-6 items-center rounded-full border px-2 text-[11px] font-medium capitalize", statusTone(label), onClick && "cursor-pointer hover:bg-accent")
  return onClick
    ? <button type="button" onClick={(e) => { e.stopPropagation(); onClick() }} className={cls}>{label}</button>
    : <span className={cls}>{label}</span>
}
function modelLabel(key?: string) {
  if (!key) return "Use default"
  const idx = key.indexOf("::")
  return idx >= 0 ? key.slice(idx + 2) : key
}
function initialState(task?: TaskLike): TaskFormState {
  const taskType = task?.task_type || "llm"
  const triggerType = task?.trigger_type || "schedule"
  const modelKey = task?.endpoint_url && task?.model ? `${task.endpoint_url}::${task.model}` : ""
  return {
    name: task?.name || "",
    task_type: taskType,
    trigger_type: triggerType,
    prompt: task?.prompt || "",
    action: task?.action || "",
    schedule: task?.schedule || "daily",
    scheduled_time: task?.schedule === "once" ? localTimeValue(task?.scheduled_date, task?.scheduled_time) : task?.scheduled_time ? utcTimeToLocal(task.scheduled_time) : "09:00",
    scheduled_day: String(task?.scheduled_day ?? (task?.schedule === "monthly" ? 1 : 0)),
    scheduled_date: localDateValue(task?.scheduled_date),
    cron_expression: task?.cron_expression || "",
    trigger_event: task?.trigger_event || "",
    trigger_count: String(task?.trigger_count || 5),
    output_target: task?.output_target || "session",
    model_key: modelKey,
    then_task_id: task?.then_task_id || "",
    notifications_enabled: task?.notifications_enabled !== false,
    character_id: (task?.character_id || "").toLowerCase(),
    urgent_email_prompt: "",
  }
}
function payloadFromState(form: TaskFormState): TaskPayload {
  const payload: TaskPayload = {
    name: form.name.trim() || undefined,
    task_type: form.task_type,
    trigger_type: form.trigger_type,
    output_target: form.output_target || "session",
    then_task_id: form.then_task_id || "",
    notifications_enabled: form.notifications_enabled,
  }
  if (form.model_key) {
    const idx = form.model_key.indexOf("::")
    payload.endpoint_url = idx >= 0 ? form.model_key.slice(0, idx) : ""
    payload.model = idx >= 0 ? form.model_key.slice(idx + 2) : form.model_key
  } else {
    payload.endpoint_url = ""
    payload.model = ""
  }
  if (form.task_type === "action") {
    payload.action = form.action
    payload.prompt = ""
    payload.character_id = ""
  } else {
    payload.prompt = form.prompt.trim()
    payload.action = ""
    payload.character_id = form.character_id || ""
  }
  if (form.trigger_type === "schedule") {
    payload.schedule = form.schedule
    if (form.schedule === "cron") {
      payload.cron_expression = form.cron_expression.trim()
    } else {
      payload.scheduled_time = localTimeToUtc(form.scheduled_time)
      payload.cron_expression = ""
      if (form.schedule === "weekly" || form.schedule === "monthly") payload.scheduled_day = Number(form.scheduled_day || (form.schedule === "monthly" ? 1 : 0))
      if (form.schedule === "once") {
        const d = new Date(`${form.scheduled_date}T${form.scheduled_time}:00`)
        payload.scheduled_date = d.toISOString()
      }
    }
  } else if (form.trigger_type === "event") {
    payload.trigger_event = form.trigger_event
    payload.trigger_count = Math.max(1, Number(form.trigger_count || 1))
  }
  return payload
}
function validateForm(form: TaskFormState) {
  if ((form.task_type === "llm" || form.task_type === "research") && !form.prompt.trim()) return "Prompt is required"
  if (form.task_type === "action" && !form.action) return "Select an action"
  if (form.trigger_type === "schedule" && form.schedule === "cron" && !form.cron_expression.trim()) return "Cron expression is required"
  if (form.trigger_type === "event" && !form.trigger_event) return "Select an event trigger"
  return ""
}
async function copyText(text: string, label = "Copied") {
  await navigator.clipboard.writeText(text)
  toast(label, "success")
}

function TaskRunOutput({ text, max = 800 }: { text?: string | null; max?: number }) {
  const raw = (text || "").trim()
  const [expanded, setExpanded] = useState(false)
  if (!raw) return null

  const long = raw.length > max || raw.split("\n").length > 10
  const display = long && !expanded ? `${raw.slice(0, max).trimEnd()}...` : raw
  return (
    <div className="task-run-markdown mt-2 rounded-md bg-muted/25 px-3 py-2 text-xs text-muted-foreground">
      <Markdown>{display}</Markdown>
      {long && (
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          className="mt-2 text-[11px] font-medium text-foreground underline-offset-2 hover:underline"
        >
          {expanded ? "Show less" : "Show more"}
        </button>
      )}
    </div>
  )
}

function SegmentGroup({ items, value, onChange }: { items: { value: string; label: string; icon: LucideIcon }[]; value: string; onChange: (v: string) => void }) {
  return (
    <div className="grid grid-cols-3 rounded-lg bg-muted p-0.5">
      {items.map(({ value: v, label, icon: Icon }) => (
        <button
          key={v}
          type="button"
          onClick={() => onChange(v)}
          className={cn("inline-flex h-8 items-center justify-center gap-1.5 rounded-md text-xs font-medium text-muted-foreground transition-colors hover:text-foreground", value === v && "bg-background text-foreground shadow-sm")}
        >
          <Icon className="size-3.5" />{label}
        </button>
      ))}
    </div>
  )
}

function AiDraft({ onDraft }: { onDraft: (draft: TaskPayload) => void }) {
  const { parse } = useTaskMutations()
  const [description, setDescription] = useState("")
  const [error, setError] = useState("")
  const submit = () => {
    if (!description.trim()) return
    setError("")
    parse.mutate(description.trim(), {
      onSuccess: (data) => {
        if (data.draft) onDraft(data.draft)
        else setError(data.message || "No draft returned")
      },
      onError: (e) => setError(e instanceof Error ? e.message : "Couldn't draft task"),
    })
  }
  return (
    <div className="rounded-lg border bg-muted/20 p-3" data-tour="tasks-ai-draft">
      <div className="flex gap-2">
        <input
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") submit() }}
          placeholder="every weekday at 7am research the top AI news and summarize it"
          className={inputClass}
        />
        <Button type="button" size="sm" disabled={parse.isPending || !description.trim()} onClick={submit}>
          <Sparkles className="size-4" />{parse.isPending ? "Drafting" : "Draft"}
        </Button>
      </div>
      {error && <p className="mt-2 text-xs text-destructive">{error}</p>}
    </div>
  )
}

function TaskEditor({ existing, draft, tasks, onClose }: { existing?: Task | null; draft?: TaskPayload | null; tasks: Task[]; onClose: () => void }) {
  const { create, update, saveUrgentEmailSettings } = useTaskMutations()
  const { data: outputTargets } = useTaskOutputTargets()
  const { data: actions } = useTaskActions()
  const { data: events } = useTaskEvents()
  const { data: models } = useModels()
  const { data: urgentSettings } = useUrgentEmailSettings(true)
  const [form, setForm] = useState<TaskFormState>(() => initialState(existing || draft || undefined))
  const [error, setError] = useState("")
  const isEditing = !!existing?.id

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- seed the editor whenever the selected task/draft changes.
    setForm(initialState(existing || draft || undefined))
    setError("")
  }, [existing, draft])
  useEffect(() => {
    if (!actions?.length) return
    // eslint-disable-next-line react-hooks/set-state-in-effect -- default the action after metadata loads.
    setForm((f) => f.task_type === "action" && !f.action ? { ...f, action: actions[0].name } : f)
  }, [actions, form.task_type])
  useEffect(() => {
    if (!events?.length) return
    // eslint-disable-next-line react-hooks/set-state-in-effect -- default the event trigger after metadata loads.
    setForm((f) => f.trigger_type === "event" && !f.trigger_event ? { ...f, trigger_event: events[0].name } : f)
  }, [events, form.trigger_type])
  useEffect(() => {
    if (!urgentSettings) return
    // eslint-disable-next-line react-hooks/set-state-in-effect -- hydrate saved urgent-email rules into the form.
    setForm((f) => ({ ...f, urgent_email_prompt: String(urgentSettings.urgent_email_prompt || "") }))
  }, [urgentSettings])

  const endpointItems = (models?.items || []) as ModelEndpoint[]
  const modelOptions = endpointItems.filter((it) => !it.offline && (it.model_type || "llm") === "llm" && it.url)
  const currentTaskIds = new Set(existing?.id ? [existing.id] : [])
  const otherTasks = tasks.filter((t) => !currentTaskIds.has(t.id))
  const webhookUrl = existing?.trigger_type === "webhook" && existing.webhook_token
    ? `${window.location.origin}/api/tasks/${existing.id}/webhook/${existing.webhook_token}`
    : ""

  const set = <K extends keyof TaskFormState>(key: K, value: TaskFormState[K]) => setForm((f) => ({ ...f, [key]: value }))
  const save = async () => {
    const msg = validateForm(form)
    if (msg) { setError(msg); return }
    const payload = payloadFromState(form)
    setError("")
    try {
      if (form.task_type === "action" && form.action === "check_email_urgency") {
        await saveUrgentEmailSettings.mutateAsync(form.urgent_email_prompt || "")
      }
      if (existing?.id) await update.mutateAsync({ id: existing.id, payload })
      else await create.mutateAsync(payload)
      toast(existing?.id ? "Task updated" : "Task created", "success")
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Task save failed")
    }
  }

  return (
    <div className="space-y-4 rounded-lg border bg-card p-4" data-tour="tasks-editor">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold">{isEditing ? "Edit Task" : "New Task"}</h2>
          <p className="mt-1 text-xs text-muted-foreground">{isEditing ? "Update this automation's trigger, output, and run behavior." : "Configure a prompt, research job, action, event trigger, or webhook."}</p>
        </div>
        <button type="button" onClick={onClose} className={iconBtn} title="Close"><X className="size-4" /></button>
      </div>

      {!isEditing && <AiDraft onDraft={(d) => setForm(initialState(d))} />}

      <div className="grid gap-4 lg:grid-cols-[1.1fr_0.9fr]">
        <section className="space-y-4">
          <div>
            <label className={labelClass}>Name</label>
            <input value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="Auto-generated if blank" className={inputClass} />
          </div>
          <div>
            <label className={labelClass}>Type</label>
            <SegmentGroup items={TASK_TYPES} value={form.task_type} onChange={(v) => setForm((f) => ({ ...f, task_type: v }))} />
          </div>
          {form.task_type === "action" ? (
            <div className="space-y-3">
              <div>
                <label className={labelClass}>Action</label>
                <select value={form.action} onChange={(e) => set("action", e.target.value)} className={inputClass}>
                  {(actions || []).length === 0 && <option value="">Loading actions...</option>}
                  {(actions || []).map((a) => <option key={a.name} value={a.name}>{a.name}{a.description ? ` - ${a.description}` : ""}</option>)}
                </select>
              </div>
              {form.action === "check_email_urgency" && (
                <div>
                  <label className={labelClass}>Email Triage Rules</label>
                  <textarea
                    value={form.urgent_email_prompt}
                    onChange={(e) => set("urgent_email_prompt", e.target.value)}
                    rows={4}
                    placeholder="What should count as urgent? Deadlines, blockers, people waiting outside..."
                    className={textareaClass}
                  />
                  <p className="mt-1 text-xs text-muted-foreground">These rules are saved with your auth settings, while this task controls the schedule and notifications.</p>
                </div>
              )}
            </div>
          ) : (
            <div className="space-y-3">
              <div>
                <label className={labelClass}>{form.task_type === "research" ? "Research Question" : "Prompt"}</label>
                <textarea
                  value={form.prompt}
                  onChange={(e) => set("prompt", e.target.value)}
                  rows={5}
                  placeholder={form.task_type === "research" ? "What should be researched?" : "What should the AI do?"}
                  className={textareaClass}
                />
              </div>
              <div>
                <label className={labelClass}>Persona</label>
                <select value={form.character_id} onChange={(e) => set("character_id", e.target.value)} className={inputClass}>
                  {PERSONAS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select>
              </div>
            </div>
          )}
        </section>

        <section className="space-y-4">
          <div>
            <label className={labelClass}>Trigger</label>
            <SegmentGroup items={TRIGGERS} value={form.trigger_type} onChange={(v) => setForm((f) => ({ ...f, trigger_type: v }))} />
          </div>
          {form.trigger_type === "schedule" && (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div>
                <label className={labelClass}>Frequency</label>
                <select value={form.schedule} onChange={(e) => set("schedule", e.target.value)} className={inputClass}>
                  {["daily", "weekly", "monthly", "once", "cron"].map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </div>
              {form.schedule !== "cron" && (
                <div>
                  <label className={labelClass}>Time</label>
                  <input type="time" value={form.scheduled_time} onChange={(e) => set("scheduled_time", e.target.value)} className={inputClass} />
                </div>
              )}
              {form.schedule === "weekly" && (
                <div>
                  <label className={labelClass}>Day Of Week</label>
                  <select value={form.scheduled_day} onChange={(e) => set("scheduled_day", e.target.value)} className={inputClass}>
                    {DAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}
                  </select>
                </div>
              )}
              {form.schedule === "monthly" && (
                <div>
                  <label className={labelClass}>Day Of Month</label>
                  <input type="number" min={1} max={31} value={form.scheduled_day} onChange={(e) => set("scheduled_day", e.target.value)} className={inputClass} />
                </div>
              )}
              {form.schedule === "once" && (
                <div>
                  <label className={labelClass}>Date</label>
                  <input type="date" value={form.scheduled_date} onChange={(e) => set("scheduled_date", e.target.value)} className={inputClass} />
                </div>
              )}
              {form.schedule === "cron" && (
                <div className="sm:col-span-2">
                  <label className={labelClass}>Cron Expression</label>
                  <input value={form.cron_expression} onChange={(e) => set("cron_expression", e.target.value)} placeholder="0 */2 * * *" className={inputClass} />
                </div>
              )}
            </div>
          )}
          {form.trigger_type === "event" && (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-[1fr_10rem]">
              <div>
                <label className={labelClass}>Event</label>
                <select value={form.trigger_event} onChange={(e) => set("trigger_event", e.target.value)} className={inputClass}>
                  {(events || []).length === 0 && <option value="">Loading events...</option>}
                  {(events || []).map((ev) => <option key={ev.name} value={ev.name}>{ev.name}{ev.description ? ` - ${ev.description}` : ""}</option>)}
                </select>
              </div>
              <div>
                <label className={labelClass}>Every N</label>
                <input type="number" min={1} max={1000} value={form.trigger_count} onChange={(e) => set("trigger_count", e.target.value)} className={inputClass} />
              </div>
            </div>
          )}
          {form.trigger_type === "webhook" && (
            <div>
              <label className={labelClass}>Webhook URL</label>
              {webhookUrl ? (
                <div className="flex gap-2">
                  <input value={webhookUrl} readOnly className={cn(inputClass, "font-mono text-xs")} />
                  <Button type="button" size="sm" variant="outline" onClick={() => copyText(webhookUrl, "Webhook URL copied")}><Copy className="size-4" /></Button>
                </div>
              ) : (
                <div className="rounded-md border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">A webhook URL will be generated when this task is saved.</div>
              )}
            </div>
          )}

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <label className={labelClass}>Output</label>
              <select value={form.output_target} onChange={(e) => set("output_target", e.target.value)} className={inputClass}>
                {(outputTargets || [{ value: "session", label: "Session" }]).map((t) => <option key={t.value} value={t.value}>{t.label || t.value}</option>)}
              </select>
            </div>
            <div>
              <label className={labelClass}>Model</label>
              <select value={form.model_key} onChange={(e) => set("model_key", e.target.value)} className={inputClass}>
                <option value="">Use session default</option>
                {modelOptions.map((ep) => (
                  <optgroup key={ep.endpoint_id || ep.url} label={ep.endpoint_name || ep.host || ep.url || "Endpoint"}>
                    {[...(ep.models || []), ...(ep.models_extra || [])].sort().map((m) => <option key={`${ep.url}::${m}`} value={`${ep.url}::${m}`}>{m}</option>)}
                  </optgroup>
                ))}
                {form.model_key && !modelOptions.some((ep) => [...(ep.models || []), ...(ep.models_extra || [])].some((m) => `${ep.url}::${m}` === form.model_key)) && (
                  <option value={form.model_key}>{modelLabel(form.model_key)} (unlisted endpoint)</option>
                )}
              </select>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-[1fr_auto] sm:items-end">
            <div>
              <label className={labelClass}>Chain</label>
              <select value={form.then_task_id} onChange={(e) => set("then_task_id", e.target.value)} className={inputClass}>
                <option value="">None</option>
                {otherTasks.map((t) => <option key={t.id} value={t.id}>{t.name || t.action || "Task"}</option>)}
              </select>
            </div>
            <label className="flex h-9 items-center gap-2 rounded-md border px-3 text-sm">
              <Switch checked={form.notifications_enabled} onCheckedChange={(v) => set("notifications_enabled", v)} />
              Notifications
            </label>
          </div>
        </section>
      </div>

      {error && <p className="text-xs text-destructive">{error}</p>}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={onClose}>Cancel</Button>
        <Button type="button" size="sm" disabled={create.isPending || update.isPending || saveUrgentEmailSettings.isPending} onClick={save}>
          <Check className="size-4" />{isEditing ? "Save" : "Create"}
        </Button>
      </div>
    </div>
  )
}

function OnboardingBanner() {
  const { data } = useTasksOnboarding()
  const { markOnboarding } = useTaskMutations()
  if (!data || data.opened) return null
  return (
    <div className="mb-3 flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-muted/30 p-3">
      <div>
        <p className="text-sm font-medium">Enable default automations?</p>
        <p className="mt-0.5 text-xs text-muted-foreground">The original Tasks first-run flow can resume useful built-ins like tidy and email helpers.</p>
      </div>
      <div className="flex gap-2">
        <Button size="sm" variant="ghost" onClick={() => markOnboarding.mutate(false)}>Not now</Button>
        <Button size="sm" onClick={() => markOnboarding.mutate(true)}>Enable</Button>
      </div>
    </div>
  )
}

function RunRows({ runs }: { runs?: TaskRun[] }) {
  if (!runs) return <p className="py-3 text-xs text-muted-foreground">Loading...</p>
  if (runs.length === 0) return <p className="py-3 text-xs text-muted-foreground">No runs yet.</p>
  return (
    <div className="space-y-1.5">
      {runs.map((r) => (
        <div key={r.id} className="rounded-md border bg-background px-2.5 py-2">
          <div className="flex min-w-0 items-center gap-2 text-xs">
            <StatusBadge status={r.status} />
            {r.model && <span className="truncate text-muted-foreground">{r.model.split("/").pop()}</span>}
            <span className="ml-auto shrink-0 text-muted-foreground">{fmtShort(r.started_at || r.finished_at)}</span>
          </div>
          {r.result || r.error
            ? <TaskRunOutput text={r.result || r.error} max={320} />
            : <p className="mt-1 text-xs text-muted-foreground">-</p>}
        </div>
      ))}
    </div>
  )
}
function TaskHistory({ taskId }: { taskId: string | null }) {
  const { data: runs } = useTaskRuns(taskId)
  if (!taskId) return null
  return (
    <div className="mt-3 rounded-lg border bg-muted/20 p-2">
      <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Run History</div>
      <RunRows runs={runs} />
    </div>
  )
}

function TaskCard({
  task,
  selectMode,
  selected,
  expanded,
  historyOpen,
  chainName,
  onSelect,
  onEdit,
  onExpand,
  onHistory,
}: {
  task: Task
  selectMode: boolean
  selected: boolean
  expanded: boolean
  historyOpen: boolean
  chainName?: string
  onSelect: (checked: boolean) => void
  onEdit: () => void
  onExpand: () => void
  onHistory: () => void
}) {
  const { run, stop, pause, resume, revert, clearCache, remove } = useTaskMutations()
  const category = categoryFor(task)
  const paused = (task.status || "").toLowerCase() === "paused" || task.enabled === false
  const running = (task.status || "").toLowerCase() === "running"
  const canClear = !!task.action && CLEARABLE_ACTIONS.has(task.action)
  const name = task.name || task.title || task.action || "Task"
  const toggleStatus = () => paused ? resume.mutate(task.id) : pause.mutate(task.id)
  const webhookUrl = task.trigger_type === "webhook" && task.webhook_token ? `${window.location.origin}/api/tasks/${task.id}/webhook/${task.webhook_token}` : ""
  return (
    <div className={cn("rounded-lg border bg-card p-3", paused && "opacity-80")}>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start">
        <div className="flex min-w-0 flex-1 items-start gap-3">
          {selectMode && (
            <input type="checkbox" checked={selected} onChange={(e) => onSelect(e.target.checked)} className="mt-1.5 size-4 rounded border" aria-label={`Select ${name}`} />
          )}
          <div
            role="button"
            tabIndex={0}
            onClick={onExpand}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault()
                onExpand()
              }
            }}
            className="flex min-w-0 flex-1 cursor-pointer items-start gap-3 text-left"
          >
            <span className="mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground"><CategoryIcon category={category} /></span>
            <span className="min-w-0 flex-1">
              <span className="flex min-w-0 flex-wrap items-center gap-2">
                <span className="min-w-0 max-w-full truncate text-sm font-medium">{name}</span>
                <StatusBadge status={task.status} onClick={toggleStatus} />
                <span className="rounded-full border bg-muted/40 px-2 py-0.5 text-[11px] text-muted-foreground">{category}</span>
                {task.is_builtin && <span className="rounded-full border bg-muted/40 px-2 py-0.5 text-[11px] text-muted-foreground">built-in{task.is_modified ? " / edited" : ""}</span>}
                {(task.task_type === "llm" || task.task_type === "research" || task.model) && <Bot className="size-3.5 text-muted-foreground" aria-label="AI task" />}
              </span>
              <span className="mt-1 block truncate text-xs text-muted-foreground">{scheduleLabel(task)}</span>
              {(task.last_run_status || task.last_run_result) && (
                <span className="mt-1 block truncate text-xs text-muted-foreground">
                  Last: {task.last_run_status || "run"}{task.last_run_result ? ` - ${task.last_run_result}` : ""}
                </span>
              )}
            </span>
          </div>
        </div>
        <div className="flex shrink-0 flex-wrap items-center justify-end gap-1 sm:flex-nowrap">
          <button onClick={() => run.mutate(task.id)} title="Run now" className={iconBtn}><RotateCw className="size-4" /></button>
          {running && <button onClick={() => stop.mutate(task.id)} title="Stop" className={iconBtn}><Square className="size-4" /></button>}
          {paused
            ? <button onClick={() => resume.mutate(task.id)} title="Resume" className={iconBtn}><Play className="size-4" /></button>
            : <button onClick={() => pause.mutate(task.id)} title="Pause" className={iconBtn}><Pause className="size-4" /></button>}
          <button onClick={onEdit} title="Edit" className={iconBtn}><Edit3 className="size-4" /></button>
          <button onClick={onHistory} title="Run history" className={cn(iconBtn, historyOpen && "bg-accent text-foreground")}><History className="size-4" /></button>
          {task.is_builtin && task.is_modified && <button onClick={() => revert.mutate(task.id, { onSuccess: () => toast("Task reverted", "success") })} title="Revert to default" className={iconBtn}><RotateCcw className="size-4" /></button>}
          {canClear && <button onClick={() => clearCache.mutate(task.id, { onSuccess: () => toast(`Cleared ${CLEAR_LABELS[task.action || ""] || "cache"}`, "success") })} title="Clear cache" className={iconBtn}><Clipboard className="size-4" /></button>}
          <button onClick={() => { if (confirm("Delete this task and its run history?")) remove.mutate(task.id) }} title="Delete" className="inline-flex size-8 items-center justify-center rounded-md text-muted-foreground hover:bg-destructive/10 hover:text-destructive"><Trash2 className="size-4" /></button>
        </div>
      </div>

      {expanded && (
        <div className="mt-3 grid grid-cols-1 gap-3 border-t pt-3 text-xs text-muted-foreground sm:grid-cols-2">
          <div><span className="font-medium text-foreground">Type:</span> {task.task_type || "llm"}{task.action ? ` / ${task.action}` : ""}</div>
          <div><span className="font-medium text-foreground">Output:</span> {task.output_target || "session"}</div>
          <div><span className="font-medium text-foreground">Model:</span> {task.model || "default"}</div>
          <div><span className="font-medium text-foreground">Chain:</span> {chainName || "none"}</div>
          {task.trigger_type === "event" && <div><span className="font-medium text-foreground">Counter:</span> {task.trigger_counter || 0}/{task.trigger_count || 1}</div>}
          <div><span className="font-medium text-foreground">Notifications:</span> {task.notifications_enabled === false ? "off" : "on"}</div>
          {webhookUrl && (
            <div className="sm:col-span-2">
              <span className="font-medium text-foreground">Webhook:</span>
              <button type="button" onClick={() => copyText(webhookUrl, "Webhook URL copied")} className="ml-2 inline-flex items-center gap-1 text-foreground hover:underline"><Link2 className="size-3.5" />Copy URL</button>
            </div>
          )}
          {(task.prompt || task.last_run_result) && (
            <div className="sm:col-span-2">
              <p className="whitespace-pre-wrap break-words">{compact(task.prompt || task.last_run_result, 520)}</p>
            </div>
          )}
        </div>
      )}
      {historyOpen && <TaskHistory taskId={task.id} />}
    </div>
  )
}

function TasksList({ tasks, onNew, onEdit }: { tasks: Task[]; onNew: () => void; onEdit: (task: Task) => void }) {
  const { pause, resume, remove } = useTaskMutations()
  const [query, setQuery] = useState("")
  const [sort, setSort] = useState<"recent" | "name" | "status">("recent")
  const [filter, setFilter] = useState<string | null>(null)
  const [selectMode, setSelectMode] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [expanded, setExpanded] = useState<string | null>(null)
  const [history, setHistory] = useState<string | null>(null)
  const taskById = useMemo(() => new Map(tasks.map((t) => [t.id, t])), [tasks])
  const counts = useMemo(() => {
    const out = new Map<string, number>()
    tasks.forEach((t) => out.set(categoryFor(t), (out.get(categoryFor(t)) || 0) + 1))
    return out
  }, [tasks])
  const categories = Array.from(counts.keys()).sort((a, b) => (CATEGORY_ORDER.indexOf(a) < 0 ? 99 : CATEGORY_ORDER.indexOf(a)) - (CATEGORY_ORDER.indexOf(b) < 0 ? 99 : CATEGORY_ORDER.indexOf(b)))
  const visible = useMemo(() => {
    const q = query.trim().toLowerCase()
    const list = tasks.filter((t) => {
      if (filter && categoryFor(t) !== filter) return false
      if (q && !`${t.name || ""} ${t.prompt || ""} ${t.action || ""}`.toLowerCase().includes(q)) return false
      return true
    })
    const statusRank: Record<string, number> = { active: 0, running: 0, paused: 1, completed: 2 }
    return list.sort((a, b) => {
      if (sort === "name") return (a.name || "").localeCompare(b.name || "")
      if (sort === "status") {
        const sa = statusRank[(a.status || "").toLowerCase()] ?? 9
        const sb = statusRank[(b.status || "").toLowerCase()] ?? 9
        if (sa !== sb) return sa - sb
        return (a.name || "").localeCompare(b.name || "")
      }
      const ca = categoryFor(a), cb = categoryFor(b)
      const ia = CATEGORY_ORDER.indexOf(ca), ib = CATEGORY_ORDER.indexOf(cb)
      if (ia !== ib) return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib)
      return (a.name || "").localeCompare(b.name || "")
    })
  }, [tasks, query, filter, sort])
  const activeTasks = tasks.filter((t) => (t.status || "").toLowerCase() !== "paused")
  const pausedTasks = tasks.filter((t) => (t.status || "").toLowerCase() === "paused")
  const toggleSelected = (id: string, checked: boolean) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (checked) next.add(id); else next.delete(id)
      return next
    })
  }
  const bulkDelete = async () => {
    const ids = [...selected]
    if (!ids.length) return
    if (!confirm(`Delete ${ids.length} task${ids.length === 1 ? "" : "s"}? This cannot be undone.`)) return
    await Promise.allSettled(ids.map((id) => remove.mutateAsync(id)))
    toast(`Deleted ${ids.length} task${ids.length === 1 ? "" : "s"}`, "success")
    setSelected(new Set())
    setSelectMode(false)
  }
  const bulkPause = async () => {
    await Promise.allSettled(activeTasks.map((t) => pause.mutateAsync(t.id)))
    toast("Active tasks paused", "success")
  }
  const bulkResume = async () => {
    await Promise.allSettled(pausedTasks.map((t) => resume.mutateAsync(t.id)))
    toast("Paused tasks resumed", "success")
  }

  return (
    <div data-tour="tasks-list">
      <OnboardingBanner />
      <div className="mb-3 flex flex-wrap items-center gap-2" data-tour="tasks-bulk-controls">
        <div className="relative min-w-48 flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search tasks..." className={cn(inputClass, "pl-8")} />
        </div>
        <select value={sort} onChange={(e) => setSort(e.target.value as "recent" | "name" | "status")} className={cn(inputClass, "w-28")}>
          <option value="recent">Recent</option>
          <option value="name">A-Z</option>
          <option value="status">Status</option>
        </select>
        <Button size="sm" variant={selectMode ? "secondary" : "outline"} onClick={() => { setSelectMode((v) => !v); setSelected(new Set()) }}>{selectMode ? "Cancel" : "Select"}</Button>
        <Button size="sm" variant="outline" disabled={!activeTasks.length} onClick={bulkPause}><Pause className="size-4" />Pause all</Button>
        <Button size="sm" variant="outline" disabled={!pausedTasks.length} onClick={bulkResume}><Play className="size-4" />Resume all</Button>
        <Button size="sm" onClick={onNew} data-tour="tasks-add"><Sparkles className="size-4" />Add</Button>
      </div>
      {categories.length > 1 && (
        <div className="mb-3 flex flex-wrap gap-1.5">
          <button onClick={() => setFilter(null)} className={cn(chipBase, !filter ? "bg-primary text-primary-foreground" : "bg-background text-muted-foreground hover:bg-accent hover:text-foreground")}>all <span className="opacity-70">{tasks.length}</span></button>
          {categories.map((c) => (
            <button key={c} onClick={() => setFilter(filter === c ? null : c)} className={cn(chipBase, filter === c ? "bg-primary text-primary-foreground" : "bg-background text-muted-foreground hover:bg-accent hover:text-foreground")}>{c} <span className="opacity-70">{counts.get(c)}</span></button>
          ))}
        </div>
      )}
      {selectMode && (
        <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg border bg-muted/30 p-2 text-xs">
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={visible.length > 0 && visible.every((t) => selected.has(t.id))}
              onChange={(e) => setSelected(e.target.checked ? new Set(visible.map((t) => t.id)) : new Set())}
            />
            All visible
          </label>
          <span className="text-muted-foreground">{selected.size} selected</span>
          <Button size="sm" variant="destructive" disabled={!selected.size} onClick={bulkDelete}><Trash2 className="size-4" />Delete</Button>
        </div>
      )}
      <div className="space-y-2">
        {!tasks.length && <p className="py-8 text-center text-sm text-muted-foreground">No tasks yet. Create one to get started.</p>}
        {tasks.length > 0 && visible.length === 0 && <p className="py-8 text-center text-sm text-muted-foreground">No matching tasks.</p>}
        {visible.map((t) => (
          <TaskCard
            key={t.id}
            task={t}
            selectMode={selectMode}
            selected={selected.has(t.id)}
            expanded={expanded === t.id}
            historyOpen={history === t.id}
            chainName={t.then_task_id ? (taskById.get(t.then_task_id)?.name || t.then_task_id) : ""}
            onSelect={(checked) => toggleSelected(t.id, checked)}
            onEdit={() => onEdit(t)}
            onExpand={() => setExpanded((cur) => cur === t.id ? null : t.id)}
            onHistory={() => setHistory((cur) => cur === t.id ? null : t.id)}
          />
        ))}
      </div>
    </div>
  )
}

function stackRuns(runs: TaskRun[]) {
  const out: Array<TaskRun & { repeatCount?: number }> = []
  const seen = new Map<string, TaskRun & { repeatCount?: number }>()
  for (const run of runs) {
    const key = [run.task_id, run.task_name, run.status, run.output_target, compact(run.result || run.error, 80)].join("|")
    const prev = seen.get(key)
    if (prev && run.status !== "running" && run.status !== "queued") {
      prev.repeatCount = (prev.repeatCount || 1) + 1
      continue
    }
    const entry = { ...run, repeatCount: 1 }
    seen.set(key, entry)
    out.push(entry)
  }
  return out
}
async function openRunInChat(run: TaskRun, navigate: (path: string) => void) {
  const session = await createSession({
    name: `Task: ${run.task_name || "Run"}`.slice(0, 60),
    model: run.model,
    endpoint_url: run.endpoint_url,
  })
  const sid = session.id
  const r = await apiFetch(`/api/session/${sid}/inject_messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      messages: [
        { role: "user", content: `Here is the latest run of my scheduled task "${run.task_name || "Task"}". Let's review it.` },
        { role: "assistant", content: run.result || run.error || "(no output)" },
      ],
    }),
  })
  if (!r.ok) throw new Error("Couldn't seed the chat")
  navigate(`/chat/${sid}`)
}
function ActivityView({ active }: { active: boolean }) {
  const navigate = useNavigate()
  const { data: runs } = useRecentTaskRuns(active)
  const { run, stop, clearCache } = useTaskMutations()
  const [query, setQuery] = useState("")
  const [filter, setFilter] = useState<string>("")
  const entries = useMemo(() => stackRuns(runs || []), [runs])
  const chips = useMemo(() => {
    const cats = new Set<string>()
    let hasErrors = false
    let notifications = 0
    for (const r of entries) {
      if (r.output_target === "notification") notifications += 1
      else cats.add(activityCategory(r.task_name, r.action))
      if ((r.status || "").toLowerCase() === "error") hasErrors = true
    }
    return { cats: Array.from(cats), hasErrors, notifications }
  }, [entries])
  const visible = entries.filter((r) => {
    const q = query.trim().toLowerCase()
    if (q && !`${r.task_name || ""} ${r.result || ""} ${r.error || ""}`.toLowerCase().includes(q)) return false
    if (filter === "errors" && (r.status || "").toLowerCase() !== "error") return false
    if (filter === "notifications" && r.output_target !== "notification") return false
    if (filter.startsWith("cat:") && activityCategory(r.task_name, r.action) !== filter.slice(4)) return false
    return true
  })
  const actCopy = async (r: TaskRun) => copyText(`${r.task_name || "Task"}\n${r.result || r.error || ""}`.trim(), "Log copied")
  return (
    <div data-tour="tasks-activity">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="relative min-w-48 flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search activity..." className={cn(inputClass, "pl-8")} />
        </div>
      </div>
      <div className="mb-3 flex flex-wrap gap-1.5">
        <button onClick={() => setFilter("")} className={cn(chipBase, !filter ? "bg-primary text-primary-foreground" : "bg-background text-muted-foreground hover:bg-accent hover:text-foreground")}>all</button>
        {chips.cats.map((c) => <button key={c} onClick={() => setFilter(filter === `cat:${c}` ? "" : `cat:${c}`)} className={cn(chipBase, filter === `cat:${c}` ? "bg-primary text-primary-foreground" : "bg-background text-muted-foreground hover:bg-accent hover:text-foreground")}>{c}</button>)}
        {chips.hasErrors && <button onClick={() => setFilter(filter === "errors" ? "" : "errors")} className={cn(chipBase, filter === "errors" ? "bg-primary text-primary-foreground" : "bg-background text-muted-foreground hover:bg-accent hover:text-foreground")}>errors</button>}
        {chips.notifications > 0 && <button onClick={() => setFilter(filter === "notifications" ? "" : "notifications")} className={cn(chipBase, filter === "notifications" ? "bg-primary text-primary-foreground" : "bg-background text-muted-foreground hover:bg-accent hover:text-foreground")}>notifications <span className="opacity-70">{chips.notifications}</span></button>}
      </div>
      {!runs && <p className="py-8 text-center text-sm text-muted-foreground">Loading activity...</p>}
      {runs?.length === 0 && <p className="py-8 text-center text-sm text-muted-foreground">No activity yet.</p>}
      {runs && runs.length > 0 && visible.length === 0 && <p className="py-8 text-center text-sm text-muted-foreground">No matching activity.</p>}
      <div className="space-y-2">
        {visible.map((r) => {
          const status = (r.status || "").toLowerCase()
          const hasResult = !!(r.result || r.error)
          const chatWorthy = r.task_type === "llm" || r.task_type === "research"
          const canClear = !!r.action && CLEARABLE_ACTIONS.has(r.action)
          return (
            <div key={r.id} className="rounded-lg border bg-card p-3">
              <div className="flex items-start gap-3">
                <span className={cn("mt-1 size-2.5 rounded-full", status === "success" ? "bg-emerald-500" : status === "error" ? "bg-destructive" : status === "running" ? "bg-primary" : "bg-muted-foreground")} />
                <div className="min-w-0 flex-1">
                  <div className="flex min-w-0 flex-wrap items-center gap-2">
                    <span className="truncate text-sm font-medium">{r.task_name || r.action || "Task"}</span>
                    <StatusBadge status={r.status} />
                    {r.repeatCount && r.repeatCount > 1 && <span className="rounded-full border bg-muted/40 px-2 py-0.5 text-[11px] text-muted-foreground">+{r.repeatCount - 1} repeats</span>}
                  </div>
                  <p className="mt-0.5 text-xs text-muted-foreground">{fmtShort(r.finished_at || r.started_at)}{r.model ? ` - ${r.model.split("/").pop()}` : ""}</p>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  {status === "queued" && r.task_id && <button onClick={() => run.mutate({ id: r.task_id!, force: true })} title="Start now" className={iconBtn}><Play className="size-4" /></button>}
                  {status === "running" && r.task_id && <button onClick={() => stop.mutate(r.task_id!)} title="Stop" className={iconBtn}><Square className="size-4" /></button>}
                  {hasResult && chatWorthy && <button onClick={() => openRunInChat(r, navigate).catch((e) => toast(e instanceof Error ? e.message : "Open in chat failed"))} title="Open in chat" className={iconBtn}><Bot className="size-4" /></button>}
                  {hasResult && !chatWorthy && <button onClick={() => actCopy(r)} title="Copy log" className={iconBtn}><Copy className="size-4" /></button>}
                  {r.research_id && <button onClick={() => window.open(`/api/research/report/${encodeURIComponent(r.research_id!)}`, "_blank")} title="Open research report" className={iconBtn}><FileText className="size-4" /></button>}
                  {r.task_id && <button onClick={() => run.mutate(r.task_id!)} title="Run again" className={iconBtn}><RotateCw className="size-4" /></button>}
                  {canClear && r.task_id && <button onClick={() => clearCache.mutate(r.task_id!, { onSuccess: () => toast(`Cleared ${CLEAR_LABELS[r.action || ""] || "cache"}`, "success") })} title="Clear cache" className={iconBtn}><Clipboard className="size-4" /></button>}
                </div>
              </div>
              {(r.result || r.error || status === "queued" || status === "running") && (
                <TaskRunOutput text={r.result || r.error || (status === "queued" ? "_Queued - waiting for a free slot..._" : "_Running..._")} />
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

export function TasksRoute() {
  const { data: tasks } = useTasks()
  const [tab, setTab] = useState<"tasks" | "activity" | "add">("tasks")
  const [editing, setEditing] = useState<Task | null>(null)
  const [draft, setDraft] = useState<TaskPayload | null>(null)
  const list = tasks || []
  useEffect(() => {
    const openAdd = () => {
      setEditing(null)
      setDraft(null)
      setTab("add")
    }
    window.addEventListener("odysseus:tasks-open-add", openAdd)
    return () => window.removeEventListener("odysseus:tasks-open-add", openAdd)
  }, [])
  const openNew = (preset?: TaskPayload) => {
    setEditing(null)
    setDraft(preset || null)
    setTab("add")
  }
  const closeEditor = () => {
    setEditing(null)
    setDraft(null)
    setTab("tasks")
  }
  return (
    <div className="mx-auto flex h-full w-full max-w-5xl flex-col" data-tour="tasks-root">
      <header className="flex min-h-13 shrink-0 flex-wrap items-center justify-between gap-2 border-b px-4 py-2">
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
          <span className="hidden truncate text-sm font-semibold sm:inline">Automations</span>
          <div className="flex max-w-full overflow-x-auto rounded-lg bg-muted p-0.5" data-tour="tasks-tabs">
            {(["tasks", "activity", "add"] as const).map((v) => (
              <button
                key={v}
                onClick={() => { if (v === "add") openNew(); else { setTab(v); setEditing(null); setDraft(null) } }}
                className={cn("whitespace-nowrap rounded-md px-2 py-1 text-[11px] font-medium capitalize sm:px-2.5 sm:text-xs", tab === v ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground")}
              >
                {v === "add" ? "Add" : v}{v === "tasks" && list.length ? ` ${list.length}` : ""}
              </button>
            ))}
          </div>
        </div>
        <Button size="sm" className="shrink-0" onClick={() => openNew()} data-tour="tasks-add"><Sparkles className="size-4" /><span className="hidden sm:inline">New</span></Button>
      </header>
      <div className="flex-1 overflow-y-auto p-4">
        {tab === "add" ? (
          <div className="space-y-3">
            {!editing && !draft && (
              <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4" data-tour="tasks-presets">
                {PRESETS.map((p) => {
                  const Icon = p.trigger_type === "webhook" ? Webhook : p.task_type === "action" ? Zap : p.task_type === "research" ? Search : Bot
                  return (
                    <button
                      key={p.label}
                      type="button"
                      onClick={() => openNew(p)}
                      className="flex items-center gap-2 rounded-lg border bg-card px-3 py-2 text-left text-sm hover:bg-accent"
                    >
                      <Icon className="size-4 text-muted-foreground" />{p.label}
                    </button>
                  )
                })}
              </div>
            )}
            <TaskEditor existing={editing} draft={draft} tasks={list} onClose={closeEditor} />
          </div>
        ) : tab === "activity" ? (
          <ActivityView active={tab === "activity"} />
        ) : (
          <TasksList tasks={list} onNew={() => openNew()} onEdit={(task) => { setEditing(task); setDraft(null); setTab("add") }} />
        )}
      </div>
    </div>
  )
}
