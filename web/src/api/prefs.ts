import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { apiJson, apiFetch } from "@/lib/api"
import { asPersonalization, type Personalization } from "@/lib/personalization"

export function usePrefs() {
  return useQuery({ queryKey: ["prefs"], queryFn: () => apiJson<Record<string, unknown>>("/api/prefs") })
}
export function useSetPref() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (v: { key: string; value: unknown }) => {
      const r = await apiFetch(`/api/prefs/${v.key}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ value: v.value }) })
      if (!r.ok) throw new Error("save failed"); return r.json()
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["prefs"] }),
  })
}

// ── Personalization (custom instructions / preferred name / tone) ───────────
export function usePersonalization(): { data: Personalization; isLoaded: boolean } {
  const { data, isSuccess } = usePrefs()
  return { data: asPersonalization(data?.personalization), isLoaded: isSuccess }
}
export function useSavePersonalization() {
  const setPref = useSetPref()
  return {
    ...setPref,
    save: (value: Personalization) => setPref.mutateAsync({ key: "personalization", value }),
  }
}

// First-run onboarding flag — stored per-user so it doesn't re-prompt across devices.
export function useOnboarded(): { onboarded: boolean; isLoaded: boolean } {
  const { data, isSuccess } = usePrefs()
  return { onboarded: data?.onboarded === true, isLoaded: isSuccess }
}
