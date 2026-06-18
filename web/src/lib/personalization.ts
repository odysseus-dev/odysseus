// Per-user personalization (Settings → Personalization + first-run onboarding).
// Stored server-side under the `personalization` pref key so it follows the user
// across devices and is injected into the chat system prompt by the backend
// (see routes/chat_helpers.py:compose_personalization).

export type Tone = "" | "concise" | "friendly" | "formal" | "technical"

export interface Personalization {
  nickname?: string
  about?: string
  instructions?: string
  tone?: Tone
}

export const TONE_OPTIONS: { value: Tone; label: string; hint: string }[] = [
  { value: "", label: "Default", hint: "Balanced, no extra steer" },
  { value: "concise", label: "Concise", hint: "Short and to the point" },
  { value: "friendly", label: "Friendly", hint: "Warm and conversational" },
  { value: "formal", label: "Formal", hint: "Professional tone" },
  { value: "technical", label: "Technical", hint: "Precise, expert-level" },
]

/** Normalize an arbitrary prefs value into a Personalization object. */
export function asPersonalization(value: unknown): Personalization {
  if (!value || typeof value !== "object") return {}
  const v = value as Record<string, unknown>
  return {
    nickname: typeof v.nickname === "string" ? v.nickname : "",
    about: typeof v.about === "string" ? v.about : "",
    instructions: typeof v.instructions === "string" ? v.instructions : "",
    tone: (typeof v.tone === "string" ? v.tone : "") as Tone,
  }
}

/** Time-of-day greeting, optionally personalized with a name. */
export function greeting(name?: string): string {
  const h = new Date().getHours()
  const part = h < 5 ? "Good evening" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening"
  const trimmed = (name || "").trim()
  return trimmed ? `${part}, ${trimmed}` : part
}
