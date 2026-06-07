import { useRef, useState } from 'react';
import type { Connection } from '../lib/connection';
import { transcribeAudio } from '../lib/api';
import { MicIcon } from './icons';

// Push-to-dictate: tap to record, tap again to stop -> uploads the audio to the
// PC's STT service and hands back the text. getUserMedia needs a secure context,
// so it works in the installed app (and localhost) but not the plain-http LAN
// preview -- it reports that instead of failing silently.
type State = 'idle' | 'recording' | 'working';

export default function VoiceButton({
  conn,
  onText,
  onError,
  disabled,
}: {
  conn: Connection;
  onText: (text: string) => void;
  onError?: (msg: string) => void;
  disabled?: boolean;
}) {
  const [state, setState] = useState<State>('idle');
  const recRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);

  function cleanupStream() {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
  }

  async function handleStop() {
    const rec = recRef.current;
    cleanupStream();
    setState('working');
    try {
      const blob = new Blob(chunksRef.current, { type: rec?.mimeType || 'audio/webm' });
      if (!blob.size) {
        onError?.('No audio captured.');
        return;
      }
      const text = await transcribeAudio(conn, blob);
      if (text.trim()) onText(text.trim());
      else onError?.('No speech detected.');
    } catch (e) {
      onError?.(e instanceof Error ? e.message : 'Transcription failed.');
    } finally {
      setState('idle');
    }
  }

  async function start() {
    if (!navigator.mediaDevices?.getUserMedia || !window.isSecureContext) {
      onError?.('Microphone needs the installed app (or an HTTPS connection).');
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const rec = new MediaRecorder(stream);
      chunksRef.current = [];
      rec.ondataavailable = (e) => {
        if (e.data.size) chunksRef.current.push(e.data);
      };
      rec.onstop = handleStop;
      rec.start();
      recRef.current = rec;
      setState('recording');
    } catch {
      cleanupStream();
      onError?.('Could not access the microphone.');
    }
  }

  function stop() {
    recRef.current?.stop();
  }

  return (
    <button
      type="button"
      className={'ghost attach voice voice-' + state}
      onClick={state === 'recording' ? stop : start}
      disabled={disabled || state === 'working'}
      aria-label={state === 'recording' ? 'Stop recording' : 'Dictate'}
    >
      <MicIcon size={22} />
    </button>
  );
}
