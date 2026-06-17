import { useQuery } from "@tanstack/react-query"
import { apiJson, apiFetch } from "@/lib/api"

// Whether the optional local STT/TTS services are configured & available.
export function useVoiceCaps() {
  return useQuery({
    queryKey: ["voice-caps"],
    staleTime: 5 * 60_000,
    retry: false,
    queryFn: async () => {
      const [tts, stt] = await Promise.allSettled([
        apiJson<{ available?: boolean }>("/api/tts/stats"),
        apiJson<{ available?: boolean }>("/api/stt/stats"),
      ])
      return {
        tts: tts.status === "fulfilled" && !!tts.value.available,
        stt: stt.status === "fulfilled" && !!stt.value.available,
      }
    },
  })
}

// Synthesize text → play through an <audio> element. Returns the Audio so the
// caller can stop it. Throws if the service is unavailable (503) or fails.
export async function speak(text: string): Promise<HTMLAudioElement> {
  const r = await apiFetch("/api/tts/synthesize", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, format: "audio" }),
  })
  if (!r.ok) throw new Error(`tts -> ${r.status}`)
  const blob = await r.blob()
  const url = URL.createObjectURL(blob)
  const audio = new Audio(url)
  audio.addEventListener("ended", () => URL.revokeObjectURL(url), { once: true })
  await audio.play()
  return audio
}

export async function transcribe(audio: Blob): Promise<string> {
  const fd = new FormData()
  fd.set("file", audio, "recording.webm")
  const r = await apiFetch("/api/stt/transcribe", { method: "POST", body: fd })
  if (!r.ok) throw new Error(`stt -> ${r.status}`)
  const j = await r.json()
  return (j.text as string) || ""
}
