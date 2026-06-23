import { useEffect, useMemo, useRef, useState } from "react"
import type { ReactNode } from "react"
import { ChevronDown, ChevronRight, Clock3, FileText, Loader2, Plus, RotateCcw, Save, Search, SlidersHorizontal, Star, Trash2, Users, Wand2, X } from "lucide-react"
import { useNavigate, useParams } from "react-router-dom"
import { useQueryClient } from "@tanstack/react-query"
import { useModels, useDefaultChat } from "@/api/models"
import { useCustomPresetMutations, usePresetConfig, usePresets } from "@/api/presets"
import { useSessionDocuments } from "@/api/documents"
import { usePresetGroups, usePresetTemplateMutations, usePresetTemplates, useSavePresetGroups, type PresetGroupParticipant } from "@/api/advanced"
import { createSession, setSessionImportant } from "@/api/sessions"
import { useComposer } from "@/stores/composer"
import type { GroupMode, GroupParticipant } from "@/stores/composer"
import { usePanel } from "@/stores/panel"
import { toast } from "@/stores/toast"
import { Switch } from "@/components/ui/switch"
import { BUILTIN_PERSONAS } from "@/lib/personas"
import { getPersistentPersonaName, setPersistentPersonaSession } from "@/lib/persistentPersona"
import { cn } from "@/lib/utils"

function Row({ label, children }: { label: string; children: ReactNode }) {
  return <div className="flex items-center justify-between gap-3 py-1.5"><span className="text-sm text-muted-foreground">{label}</span>{children}</div>
}
function Toggle({ on, onClick }: { on: boolean; onClick: () => void }) {
  return <Switch checked={on} onCheckedChange={onClick} />
}
const trigger = "flex items-center gap-1.5 rounded-md px-2 py-1 text-sm text-muted-foreground hover:bg-accent hover:text-foreground"

export function ModePicker() {
  const c = useComposer()
  return (
    <div className="flex rounded-lg bg-muted p-0.5" data-tour="mode-picker">
      {(["chat", "agent"] as const).map((mode) => (
        <button key={mode} onClick={() => c.setMode(mode)} className={cn("rounded-md px-2.5 py-1 text-xs font-medium capitalize transition-colors", c.mode === mode ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground")}>{mode}</button>
      ))}
    </div>
  )
}

export function ModelPicker() {
  const { data: models } = useModels()
  const { data: def } = useDefaultChat()
  const c = useComposer()
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const [favorites, setFavorites] = useState<string[]>(() => {
    try { return JSON.parse(window.localStorage.getItem("odysseus-model-favorites") || "[]") as string[] } catch { return [] }
  })
  const [recent, setRecent] = useState<string[]>(() => {
    try { return JSON.parse(window.localStorage.getItem("odysseus-model-recents") || "[]") as string[] } catch { return [] }
  })
  const [collapsed, setCollapsed] = useState<string[]>([])

  // Seed the default model on first load (moved here from the old ConfigPanel).
  useEffect(() => {
    if (c.model || !def?.model) return
    const ep = models?.items?.find((e) => (e.models || []).includes(def.model) || (e.models_extra || []).includes(def.model))
    c.setModel(def.model, def.endpoint_id || ep?.endpoint_id || "", def.endpoint_url || ep?.url || "")
  }, [def, models, c])

  const options = useMemo(() => (models?.items || []).flatMap((ep) =>
    [...new Set([...(ep.models || []), ...(ep.models_extra || [])])].map((model) => ({
      key: `${ep.endpoint_id}\u0000${model}`, model, endpointId: ep.endpoint_id, endpointName: ep.endpoint_name || ep.url,
      url: ep.url || "",
    }))), [models])
  const byKey = useMemo(() => new Map(options.map((option) => [option.key, option])), [options])
  const filtered = query.trim() ? options.filter((option) => `${option.model} ${option.endpointName}`.toLowerCase().includes(query.trim().toLowerCase())) : options
  const pick = (epId: string, model: string, url: string) => {
    c.setModel(model, epId, url)
    const key = `${epId}\u0000${model}`
    setRecent((values) => {
      const next = [key, ...values.filter((value) => value !== key)].slice(0, 5)
      window.localStorage.setItem("odysseus-model-recents", JSON.stringify(next)); return next
    })
    setOpen(false); setQuery("")
  }
  const toggleFavorite = (key: string) => setFavorites((values) => {
    const next = values.includes(key) ? values.filter((value) => value !== key) : [...values, key]
    window.localStorage.setItem("odysseus-model-favorites", JSON.stringify(next)); return next
  })
  const renderOption = (option: (typeof options)[number], prefix = "") => <div key={`${prefix}${option.key}`} className="group/model flex items-center rounded-md hover:bg-accent">
    <button onClick={() => pick(option.endpointId, option.model, option.url)} className={cn("min-w-0 flex-1 px-2 py-1.5 text-left text-sm", c.model === option.model && c.endpointId === option.endpointId && "text-foreground")}>
      <span className="block truncate">{option.model}</span>
      {prefix && <span className="block truncate text-[10px] text-muted-foreground">{option.endpointName}</span>}
    </button>
    <button onClick={() => toggleFavorite(option.key)} title={favorites.includes(option.key) ? "Remove favorite" : "Favorite model"} className="mr-1 rounded p-1 text-muted-foreground hover:text-foreground">
      <Star className={cn("size-3.5", favorites.includes(option.key) && "fill-current text-foreground")} />
    </button>
  </div>
  const special = (title: string, icon: ReactNode, keys: string[]) => {
    const items = keys.map((key) => byKey.get(key)).filter((item): item is (typeof options)[number] => !!item).filter((item) => filtered.includes(item))
    if (!items.length) return null
    return <div className="mb-1"><div className="flex items-center gap-1.5 px-2 py-1 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">{icon}{title}</div>{items.map((option) => renderOption(option, `${title}:`))}</div>
  }
  return (
    <div className="relative" data-tour="model-picker">
      <button onClick={() => setOpen((o) => !o)} className={trigger}>
        <span className="max-w-[160px] truncate">{c.model || "Select model"}</span>
        <ChevronDown className="size-3.5" />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute bottom-full right-0 z-20 mb-1 flex max-h-96 w-[min(92vw,18rem)] origin-bottom-right animate-pop-in flex-col rounded-xl border bg-popover p-1 shadow-lg">
            <label className="m-1 flex items-center gap-2 rounded-md border bg-background px-2">
              <Search className="size-3.5 text-muted-foreground" />
              <input autoFocus value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search models" className="h-8 min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground" />
              {query && <button onClick={() => setQuery("")}><X className="size-3.5 text-muted-foreground" /></button>}
            </label>
            <div className="min-h-0 overflow-y-auto">
            {special("Favorites", <Star className="size-3" />, favorites)}
            {special("Recent", <Clock3 className="size-3" />, recent.filter((key) => !favorites.includes(key)))}
            {(models?.items || []).map((ep) => (
              <div key={ep.endpoint_id} className="mb-1">
                <button onClick={() => setCollapsed((values) => values.includes(ep.endpoint_id) ? values.filter((value) => value !== ep.endpoint_id) : [...values, ep.endpoint_id])} className="flex w-full items-center gap-1 px-2 py-1 text-[11px] font-medium uppercase tracking-wider text-muted-foreground hover:text-foreground">
                  {collapsed.includes(ep.endpoint_id) ? <ChevronRight className="size-3" /> : <ChevronDown className="size-3" />}{ep.endpoint_name || ep.url}
                </button>
                {!collapsed.includes(ep.endpoint_id) && filtered.filter((option) => option.endpointId === ep.endpoint_id).map((option) => renderOption(option))}
              </div>
            ))}
            {(models?.items || []).length === 0 && <p className="px-2 py-3 text-sm text-muted-foreground">No models.</p>}
            {!!models?.items?.length && !filtered.length && <p className="px-2 py-3 text-sm text-muted-foreground">No matching models.</p>}
            </div>
          </div>
        </>
      )}
    </div>
  )
}

export function ToolsMenu() {
  const { sessionId } = useParams()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { data: models } = useModels()
  const { data: def } = useDefaultChat()
  const { data: presets } = usePresets()
  const { data: presetConfig } = usePresetConfig()
  const { data: templates } = usePresetTemplates()
  const { data: groupPresets } = usePresetGroups()
  const customPreset = useCustomPresetMutations()
  const templateMutations = usePresetTemplateMutations()
  const saveGroupPresets = useSavePresetGroups()
  const { data: threadDocs } = useSessionDocuments(sessionId)
  const c = useComposer()
  const [open, setOpen] = useState(false)
  const [groupPick, setGroupPick] = useState("")
  const [groupPresetPick, setGroupPresetPick] = useState("")
  const [personaPick, setPersonaPick] = useState("")
  const [personaName, setPersonaName] = useState("")
  const [personaPrompt, setPersonaPrompt] = useState("")
  const [temperature, setTemperature] = useState(1)
  const [maxTokens, setMaxTokens] = useState(0)
  const [creatingPersistent, setCreatingPersistent] = useState(false)
  const activePersistentSessionRef = useRef<string | null>(null)
  const appliedPersistentRef = useRef("")
  const lockedPersonaName = getPersistentPersonaName(sessionId)
  const docCount = threadDocs?.length || 0
  const customConfig = presetConfig?.custom
  const tuningActive = temperature !== 1 || maxTokens !== 0
  const promptActive = !!(c.promptPrefix.trim() || c.promptSuffix.trim() || tuningActive || c.presetId)
  const groupActive = c.groupActive && c.groupParticipants.length >= 2
  const activeCount = [c.useWeb, c.useResearch, c.incognito, promptActive, groupActive, !c.useRag, !c.allowBash].filter(Boolean).length
  const selectCls = "h-9 w-full rounded-md border bg-background px-2 text-sm outline-none focus-visible:border-ring"
  const textCls = "min-h-[58px] w-full resize-none rounded-md border bg-background px-2 py-1.5 text-sm outline-none placeholder:text-muted-foreground focus-visible:border-ring"
  const modelOptions = (models?.items || []).flatMap((ep) =>
    [...(ep.models || []), ...(ep.models_extra || [])].map((model) => ({
      key: `${ep.endpoint_id || ep.url}:${model}`,
      model,
      display: model.split("/").pop() || model,
      endpointId: ep.endpoint_id || "",
      endpointUrl: ep.url || "",
      endpointName: ep.endpoint_name || ep.url || "",
    })),
  )
  const savedPersonaOptions = (templates || []).filter((t) => t.id && t.name).map((t) => ({
      id: `template:${t.id}`,
      name: t.name,
      prompt: t.system_prompt || "",
      temperature: t.temperature,
      maxTokens: t.max_tokens,
  }))
  const savedPersonaNames = new Set(savedPersonaOptions.map((p) => p.name))
  const personaOptions = [
    ...savedPersonaOptions,
    ...BUILTIN_PERSONAS.filter((p) => !savedPersonaNames.has(p.name)),
  ]
  const hydrateCustomConfig = () => {
    if (!customConfig) return
    setTemperature(customConfig.temperature ?? 1)
    setMaxTokens(customConfig.max_tokens ?? 0)
    if ((customConfig.inject_prefix || customConfig.inject_suffix) && !useComposer.getState().promptPrefix && !useComposer.getState().promptSuffix) {
      useComposer.getState().setPromptInject(customConfig.inject_prefix || "", customConfig.inject_suffix || "")
    }
    if (customConfig.character_name || customConfig.system_prompt) {
      setPersonaName(customConfig.character_name || "")
      setPersonaPrompt(customConfig.system_prompt || "")
      setPersonaPick("custom")
    }
  }
  const personaByName = (name: string) =>
    personaOptions.find((p) => p.name.toLowerCase() === name.trim().toLowerCase())
  useEffect(() => {
    const name = getPersistentPersonaName(sessionId)
    if (!name) {
      appliedPersistentRef.current = ""
      if (activePersistentSessionRef.current && activePersistentSessionRef.current !== sessionId) {
        activePersistentSessionRef.current = null
        if (useComposer.getState().presetId === "custom") useComposer.getState().setPreset("")
      }
      return
    }

    const persona = personaByName(name)
    const customMatches = customConfig?.character_name?.toLowerCase() === name.toLowerCase()
    const prompt = persona?.prompt || (customMatches ? customConfig?.system_prompt || "" : "")
    const temp = persona?.temperature ?? (customMatches ? customConfig?.temperature ?? 1 : 1)
    const tokens = persona?.maxTokens ?? (customMatches ? customConfig?.max_tokens ?? 0 : 0)
    const signature = [sessionId, name, prompt, temp, tokens].join("\u0000")
    if (appliedPersistentRef.current === signature) return

    activePersistentSessionRef.current = sessionId || null
    appliedPersistentRef.current = signature
    setPersonaPick(persona?.id || "custom")
    setPersonaName(name)
    setPersonaPrompt(prompt)
    setTemperature(temp)
    setMaxTokens(tokens)
    useComposer.getState().setPreset("custom")
    void customPreset.save.mutateAsync({
      name,
      system_prompt: prompt,
      temperature: temp,
      max_tokens: tokens,
      inject_prefix: useComposer.getState().promptPrefix,
      inject_suffix: useComposer.getState().promptSuffix,
      enabled: true,
    }).catch(() => toast("Couldn't activate persona chat"))
  // eslint-disable-next-line react-hooks/exhaustive-deps -- local persona options are rebuilt each render; session/templates/config drive the lock.
  }, [sessionId, templates, customConfig])
  const openDocs = () => { usePanel.getState().showFiles(threadDocs || []); setOpen(false) }
  const clearPrompt = () => {
    c.clearPromptInject()
    if (c.presetId) c.setPreset("")
    setTemperature(1)
    setMaxTokens(0)
    setPersonaPick("")
    setPersonaName("")
    setPersonaPrompt("")
    customPreset.disable.mutate(customConfig)
  }
  const maxTokenValue = maxTokens === 0 ? 8448 : maxTokens
  const maxTokenLabel = maxTokens === 0 || maxTokens > 8192 ? "No limit" : maxTokens.toLocaleString()
  const selectPersona = (personaId: string) => {
    setPersonaPick(personaId)
    if (!personaId) {
      setPersonaName("")
      setPersonaPrompt("")
      return
    }
    if (personaId === "custom") {
      setPersonaName(customConfig?.character_name || "")
      setPersonaPrompt(customConfig?.system_prompt || "")
      setTemperature(customConfig?.temperature ?? 1)
      setMaxTokens(customConfig?.max_tokens ?? 0)
      return
    }
    const persona = personaOptions.find((p) => p.id === personaId)
    if (!persona) return
    setPersonaName(persona.name)
    setPersonaPrompt(persona.prompt)
    setTemperature(persona.temperature ?? 1)
    setMaxTokens(persona.maxTokens ?? 0)
  }
  const persistCustom = async (withPersona: boolean) => {
    const name = withPersona ? personaName.trim() : ""
    const systemPrompt = withPersona ? personaPrompt.trim() : ""
    const hasContent = !!(name || systemPrompt || c.promptPrefix.trim() || c.promptSuffix.trim() || tuningActive)
    await customPreset.save.mutateAsync({
      name,
      system_prompt: systemPrompt,
      temperature,
      max_tokens: maxTokens,
      inject_prefix: c.promptPrefix,
      inject_suffix: c.promptSuffix,
      enabled: hasContent,
    })
    c.setPreset(hasContent ? "custom" : "")
    const pickedBuiltin = personaPick.startsWith("builtin:")
    if (withPersona && name && !pickedBuiltin) {
      const existing = templates?.find((t) => t.name.toLowerCase() === name.toLowerCase() || `template:${t.id}` === personaPick)
      await templateMutations.create.mutateAsync({
        id: existing?.id,
        name,
        system_prompt: systemPrompt,
        temperature,
        max_tokens: maxTokens,
      })
    }
    return hasContent
  }
  const saveCustom = (withPersona: boolean) => {
    void persistCustom(withPersona)
      .then(() => toast(withPersona ? "Persona saved" : "Prompt saved", "success"))
      .catch(() => toast(withPersona ? "Couldn't save persona" : "Couldn't save prompt"))
  }
  const createPersistentPersonaChat = async () => {
    const name = personaName.trim()
    if (!name) return
    setCreatingPersistent(true)
    try {
      await persistCustom(true)
      const session = await createSession({
        name,
        model: c.model || def?.model,
        endpoint_id: c.endpointId || def?.endpoint_id || "",
        endpoint_url: c.endpointUrl || def?.endpoint_url || "",
      })
      await setSessionImportant(session.id, true)
      setPersistentPersonaSession(session.id, name)
      activePersistentSessionRef.current = session.id
      appliedPersistentRef.current = ""
      await qc.invalidateQueries({ queryKey: ["sessions"] })
      toast("Persona chat created", "success")
      setOpen(false)
      navigate(`/chat/${session.id}`)
    } catch {
      toast("Couldn't create persona chat")
    } finally {
      setCreatingPersistent(false)
    }
  }
  const expandPersona = () => {
    templateMutations.expand.mutate({
      name: personaName.trim(),
      prompt: personaPrompt.trim(),
      model: c.model,
    }, {
      onSuccess: (data) => {
        if (data.success && data.prompt) setPersonaPrompt(data.prompt)
        else toast(data.message || "Couldn't expand persona")
      },
      onError: () => toast("Couldn't expand persona"),
    })
  }
  const deletePersona = () => {
    const templateId = personaPick.startsWith("template:") ? personaPick.slice("template:".length) : templates?.find((t) => t.name.toLowerCase() === personaName.trim().toLowerCase())?.id
    if (!templateId) return
    if (!confirm(`Delete "${personaName || "this persona"}"?`)) return
    templateMutations.remove.mutate(templateId, {
      onSuccess: () => {
        setPersonaPick("")
        setPersonaName("")
        setPersonaPrompt("")
      },
    })
  }
  const resetPersona = () => {
    if (personaPick) selectPersona(personaPick)
    else {
      setPersonaName("")
      setPersonaPrompt("")
      setTemperature(1)
      setMaxTokens(0)
    }
  }
  const addGroupParticipant = () => {
    const pick = modelOptions.find((m) => m.key === groupPick)
    if (!pick) return
    c.addGroupParticipant({
      id: pick.key,
      model: pick.model,
      display: pick.display,
      endpointId: pick.endpointId,
      endpointUrl: pick.endpointUrl,
    })
    setGroupPick("")
  }
  const setParticipantPersona = (participantId: string, personaId: string) => {
    const persona = personaOptions.find((p) => p.id === personaId)
    c.setGroupParticipantPersona(participantId, persona
      ? { personaId: persona.id, personaName: persona.name, personaPrompt: persona.prompt }
      : { personaId: undefined, personaName: undefined, personaPrompt: undefined })
  }
  const resolvePresetPersona = (participant: PresetGroupParticipant) => {
    const rawId = participant.characterId || ""
    const idCandidates = rawId ? [rawId, `builtin:${rawId}`, `template:${rawId}`] : []
    const byId = personaOptions.find((p) => idCandidates.includes(p.id))
    if (byId) return byId
    const name = participant.characterName || ""
    if (!name) return null
    return personaOptions.find((p) => p.name.toLowerCase() === name.toLowerCase()) || {
      id: "",
      name,
      prompt: participant.characterPrompt || "",
    }
  }
  const applyGroupPreset = (value: string) => {
    setGroupPresetPick(value)
    const idx = Number(value)
    const group = Number.isInteger(idx) ? groupPresets?.[idx] : undefined
    if (!group) return
    const next = (group.participants || []).map((participant): GroupParticipant | null => {
      const model = modelOptions.find((m) => m.model === participant.modelId) ||
        modelOptions.find((m) => m.display === participant.modelDisplay)
      if (!model) return null
      const persona = resolvePresetPersona(participant)
      return {
        id: model.key,
        model: model.model,
        display: model.display,
        endpointId: model.endpointId,
        endpointUrl: model.endpointUrl,
        personaId: persona?.id || undefined,
        personaName: persona?.name || participant.characterName || undefined,
        personaPrompt: persona?.prompt || participant.characterPrompt || undefined,
      }
    }).filter((participant): participant is GroupParticipant => !!participant)
    const mode: GroupMode = group.mode === "round-robin" ? "round-robin" : "parallel"
    c.setGroupParticipants(next, mode)
  }
  const deleteGroupPreset = () => {
    const idx = Number(groupPresetPick)
    if (!Number.isInteger(idx) || !groupPresets?.[idx]) return
    if (!confirm(`Delete "${groupPresets[idx].name || `Group ${idx + 1}`}"?`)) return
    saveGroupPresets.mutate(groupPresets.filter((_, i) => i !== idx))
    setGroupPresetPick("")
  }
  return (
    <div className="relative" data-tour="tools-menu">
      <button onClick={() => { if (!open) hydrateCustomConfig(); setOpen((o) => !o) }} className={cn(trigger, activeCount && "text-foreground")} title="Tools & options">
        <SlidersHorizontal className="size-4" />
        <span className="hidden sm:inline">Tools</span>
        {activeCount > 0 && <span className="rounded-full bg-primary/15 px-1.5 text-[10px] font-medium text-foreground">{activeCount}</span>}
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute bottom-full left-0 z-20 mb-1 max-h-[min(34rem,calc(100vh-7rem))] w-[min(92vw,20rem)] origin-bottom-left animate-pop-in overflow-y-auto rounded-xl border bg-popover p-3 shadow-lg">
            <div className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Tools</div>
            <Row label="Web search"><Toggle on={c.useWeb} onClick={() => c.toggle("useWeb")} /></Row>
            <Row label="Deep research"><Toggle on={c.useResearch} onClick={() => c.toggle("useResearch")} /></Row>
            <Row label="Allow bash"><Toggle on={c.allowBash} onClick={() => c.toggle("allowBash")} /></Row>
            <Row label="Memory (RAG)"><Toggle on={c.useRag} onClick={() => c.toggle("useRag")} /></Row>
            <Row label="Incognito"><Toggle on={c.incognito} onClick={() => c.toggle("incognito")} /></Row>
            <Row label={`Documents${docCount ? ` (${docCount})` : ""}`}>
              <button
                onClick={openDocs}
                className="inline-flex h-8 items-center gap-1.5 rounded-md border bg-background px-2.5 text-[13px] font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
              >
                <FileText className="size-3.5" />
                Open
              </button>
            </Row>
            <div className="mt-2 space-y-2 border-t pt-2">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  <Wand2 className="size-3.5" />
                  Prompt
                </div>
                {(promptActive || c.presetId) && (
                  <button
                    onClick={clearPrompt}
                    className="inline-flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                    title="Clear prompt options"
                  >
                    <X className="size-3.5" />
                  </button>
                )}
              </div>
              <label className="block text-xs font-medium text-muted-foreground">
                Preset
                <select value={c.presetId} onChange={(e) => c.setPreset(e.target.value)} className={cn(selectCls, "mt-1")} disabled={(presets || []).length === 0}>
                  <option value="">{(presets || []).length ? "None" : "No saved presets"}</option>
                  {(presets || []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
              </label>
              <label className="block text-xs font-medium text-muted-foreground">
                Prefix
                <textarea
                  value={c.promptPrefix}
                  onChange={(e) => c.setPromptInject(e.target.value, c.promptSuffix)}
                  rows={2}
                  maxLength={5000}
                  placeholder="Added before your message"
                  className={cn(textCls, "mt-1")}
                />
              </label>
              <label className="block text-xs font-medium text-muted-foreground">
                Suffix
                <textarea
                  value={c.promptSuffix}
                  onChange={(e) => c.setPromptInject(c.promptPrefix, e.target.value)}
                  rows={2}
                  maxLength={5000}
                  placeholder="Added after your message"
                  className={cn(textCls, "mt-1")}
                />
              </label>
              <label className="block text-xs font-medium text-muted-foreground">
                Temperature <span className="float-right tabular-nums">{temperature.toFixed(1)}</span>
                <input
                  type="range"
                  min={0}
                  max={2}
                  step={0.1}
                  value={temperature}
                  onChange={(e) => setTemperature(Number(e.target.value))}
                  className="mt-1 w-full accent-primary"
                />
              </label>
              <label className="block text-xs font-medium text-muted-foreground">
                Max tokens <span className="float-right tabular-nums">{maxTokenLabel}</span>
                <input
                  type="range"
                  min={256}
                  max={8448}
                  step={256}
                  value={maxTokenValue}
                  onChange={(e) => {
                    const next = Number(e.target.value)
                    setMaxTokens(next > 8192 ? 0 : next)
                  }}
                  className="mt-1 w-full accent-primary"
                />
              </label>
              <button
                onClick={() => saveCustom(false)}
                disabled={customPreset.save.isPending}
                className="inline-flex h-8 w-full items-center justify-center gap-1.5 rounded-md border bg-background px-2.5 text-[13px] font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-50"
              >
                {customPreset.save.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <Save className="size-3.5" />}
                Apply prompt
              </button>
            </div>
            <div className="mt-2 space-y-2 border-t pt-2">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  <Users className="size-3.5" />
                  Persona
                </div>
                <button
                  onClick={resetPersona}
                  disabled={!!lockedPersonaName}
                  className="inline-flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                  title="Reset persona fields"
                >
                  <RotateCcw className="size-3.5" />
                </button>
              </div>
              {lockedPersonaName && (
                <div className="rounded-md border border-dashed px-2 py-1.5 text-xs text-muted-foreground">
                  Persistent chat: persona identity is locked to {lockedPersonaName}.
                </div>
              )}
              <div className="flex gap-1.5">
                <select value={personaPick} onChange={(e) => selectPersona(e.target.value)} className={cn(selectCls, "min-w-0 flex-1")} disabled={!!lockedPersonaName}>
                  <option value="">New persona</option>
                  {(customConfig?.character_name || customConfig?.system_prompt) && <option value="custom">Current custom</option>}
                  {personaOptions.map((persona) => <option key={persona.id} value={persona.id}>{persona.name}</option>)}
                </select>
                <button
                  onClick={() => { setPersonaPick(""); setPersonaName(""); setPersonaPrompt(""); setTemperature(1); setMaxTokens(0) }}
                  disabled={!!lockedPersonaName}
                  className="inline-flex size-9 shrink-0 items-center justify-center rounded-md border bg-background text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                  title="Create new persona"
                >
                  <Plus className="size-4" />
                </button>
              </div>
              <label className="block text-xs font-medium text-muted-foreground">
                Name
                <input
                  value={personaName}
                  onChange={(e) => setPersonaName(e.target.value)}
                  readOnly={!!lockedPersonaName}
                  maxLength={50}
                  placeholder="Give your persona a name"
                  className={cn(selectCls, "mt-1")}
                />
              </label>
              <label className="block text-xs font-medium text-muted-foreground">
                System prompt
                <textarea
                  value={personaPrompt}
                  onChange={(e) => setPersonaPrompt(e.target.value)}
                  rows={3}
                  maxLength={10000}
                  placeholder="Write rough notes and expand, or save as-is"
                  className={cn(textCls, "mt-1 min-h-[78px]")}
                />
              </label>
              <div className={cn("grid gap-1.5", lockedPersonaName ? "grid-cols-[1fr_auto]" : "grid-cols-[1fr_auto_auto_auto]")}>
                <button
                  onClick={() => saveCustom(true)}
                  disabled={customPreset.save.isPending || (!personaName.trim() && !personaPrompt.trim())}
                  className="inline-flex h-8 items-center justify-center gap-1.5 rounded-md border bg-background px-2.5 text-[13px] font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-50"
                >
                  {customPreset.save.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <Save className="size-3.5" />}
                  Save persona
                </button>
                <button
                  onClick={expandPersona}
                  disabled={templateMutations.expand.isPending || (!personaName.trim() && !personaPrompt.trim())}
                  className="inline-flex size-8 items-center justify-center rounded-md border bg-background text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-50"
                  title="AI expand persona"
                >
                  {templateMutations.expand.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <Wand2 className="size-3.5" />}
                </button>
                {!lockedPersonaName && (
                  <>
                    <button
                      onClick={createPersistentPersonaChat}
                      disabled={creatingPersistent || !personaName.trim()}
                      className="inline-flex size-8 items-center justify-center rounded-md border bg-background text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-50"
                      title="Create persistent persona chat"
                    >
                      {creatingPersistent ? <Loader2 className="size-3.5 animate-spin" /> : <Plus className="size-3.5" />}
                    </button>
                    <button
                      onClick={deletePersona}
                      disabled={!personaPick.startsWith("template:") && !templates?.some((t) => t.name.toLowerCase() === personaName.trim().toLowerCase())}
                      className="inline-flex size-8 items-center justify-center rounded-md border bg-background text-muted-foreground transition-colors hover:bg-accent hover:text-destructive disabled:pointer-events-none disabled:opacity-50"
                      title="Delete saved persona"
                    >
                      <Trash2 className="size-3.5" />
                    </button>
                  </>
                )}
              </div>
            </div>
            <div className="mt-2 space-y-2 border-t pt-2">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  <Users className="size-3.5" />
                  Group
                </div>
                <Switch checked={c.groupActive} onCheckedChange={c.setGroupActive} />
              </div>
              {c.groupActive && (
                <div className="space-y-2">
                  {(groupPresets || []).length > 0 && (
                    <div className="flex gap-1.5">
                      <select value={groupPresetPick} onChange={(e) => applyGroupPreset(e.target.value)} className={cn(selectCls, "min-w-0 flex-1")}>
                        <option value="">Saved group…</option>
                        {(groupPresets || []).map((group, idx) => (
                          <option key={group.id || idx} value={idx}>{group.name || `Group ${idx + 1}`}</option>
                        ))}
                      </select>
                      <button
                        onClick={deleteGroupPreset}
                        disabled={!groupPresetPick}
                        className="inline-flex size-9 shrink-0 items-center justify-center rounded-md border bg-background text-muted-foreground transition-colors hover:bg-accent hover:text-destructive disabled:pointer-events-none disabled:opacity-50"
                        title="Delete saved group"
                      >
                        <Trash2 className="size-4" />
                      </button>
                    </div>
                  )}
                  <div className="grid grid-cols-2 rounded-lg bg-muted p-0.5">
                    {(["round-robin", "parallel"] as const).map((mode) => (
                      <button
                        key={mode}
                        onClick={() => c.setGroupMode(mode)}
                        className={cn("rounded-md px-2 py-1 text-xs font-medium transition-colors", c.groupMode === mode ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground")}
                      >
                        {mode === "round-robin" ? "Sequential" : "Parallel"}
                      </button>
                    ))}
                  </div>
                  <div className="space-y-1">
                    {c.groupParticipants.map((p) => (
                      <div key={p.id} className="space-y-1 rounded-md border bg-background px-2 py-1.5 text-sm">
                        <div className="flex items-center gap-2">
                          <span className="min-w-0 flex-1 truncate">
                            {p.personaName || p.display}
                            {p.personaName && <span className="ml-1.5 text-xs text-muted-foreground">{p.display}</span>}
                          </span>
                          <button
                            onClick={() => c.removeGroupParticipant(p.id)}
                            className="shrink-0 rounded-md p-1 text-muted-foreground hover:bg-accent hover:text-destructive"
                            title={`Remove ${p.personaName || p.display}`}
                          >
                            <Trash2 className="size-3.5" />
                          </button>
                        </div>
                        <select value={p.personaId || ""} onChange={(e) => setParticipantPersona(p.id, e.target.value)} className="h-8 w-full rounded-md border bg-background px-2 text-xs text-muted-foreground outline-none focus-visible:border-ring">
                          <option value="">No persona</option>
                          {personaOptions.map((persona) => <option key={persona.id} value={persona.id}>{persona.name}</option>)}
                        </select>
                      </div>
                    ))}
                    {c.groupParticipants.length === 0 && <div className="rounded-md border border-dashed px-2 py-2 text-xs text-muted-foreground">No participants</div>}
                  </div>
                  <div className="flex gap-1.5">
                    <select value={groupPick} onChange={(e) => setGroupPick(e.target.value)} className={cn(selectCls, "min-w-0 flex-1")}>
                      <option value="">Add model…</option>
                      {modelOptions.map((m) => (
                        <option key={m.key} value={m.key} disabled={c.groupParticipants.some((p) => p.id === m.key)}>
                          {m.display}{m.endpointName ? ` · ${m.endpointName}` : ""}
                        </option>
                      ))}
                    </select>
                    <button
                      onClick={addGroupParticipant}
                      disabled={!groupPick || c.groupParticipants.length >= 8}
                      className="inline-flex size-9 shrink-0 items-center justify-center rounded-md border bg-background text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-50"
                      title="Add group participant"
                    >
                      <Plus className="size-4" />
                    </button>
                  </div>
                  {c.groupParticipants.length > 0 && c.groupParticipants.length < 2 && <p className="text-xs text-muted-foreground">Need 2 participants</p>}
                  {c.groupParticipants.length > 0 && (
                    <button onClick={c.clearGroup} className="text-xs font-medium text-muted-foreground hover:text-foreground">Clear group</button>
                  )}
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
