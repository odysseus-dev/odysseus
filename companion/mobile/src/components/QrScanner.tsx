import { useEffect, useRef, useState } from 'react';
import jsQR from 'jsqr';
import { XIcon } from './icons';

// Camera QR scanner for pairing. Uses getUserMedia + jsQR so it needs no native
// plugin and works the same in the installed app and on localhost. getUserMedia
// requires a secure context (https or localhost), so over a plain-http LAN
// preview it shows a clear message and the user falls back to manual entry.
export default function QrScanner({
  onResult,
  onClose,
}: {
  onResult: (text: string) => void;
  onClose: () => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let stream: MediaStream | null = null;
    let raf = 0;
    let stopped = false;
    const canvas = document.createElement('canvas');

    async function start() {
      if (!navigator.mediaDevices?.getUserMedia || !window.isSecureContext) {
        setError(
          'Camera needs a secure connection. It works in the installed app; ' +
            'for now enter the details below by hand.',
        );
        return;
      }
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: 'environment' },
        });
      } catch {
        setError('Could not open the camera. Check the permission, then try again.');
        return;
      }
      const video = videoRef.current;
      if (!video) return;
      video.srcObject = stream;
      try {
        await video.play();
      } catch {
        /* autoplay can reject silently; the frame loop still runs */
      }
      const ctx = canvas.getContext('2d', { willReadFrequently: true });

      const tick = () => {
        if (stopped) return;
        if (ctx && video.readyState === video.HAVE_ENOUGH_DATA) {
          canvas.width = video.videoWidth;
          canvas.height = video.videoHeight;
          ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
          const img = ctx.getImageData(0, 0, canvas.width, canvas.height);
          const code = jsQR(img.data, img.width, img.height, { inversionAttempts: 'dontInvert' });
          if (code?.data) {
            onResult(code.data);
            return; // stop scanning; parent unmounts us
          }
        }
        raf = requestAnimationFrame(tick);
      };
      raf = requestAnimationFrame(tick);
    }

    start();
    return () => {
      stopped = true;
      cancelAnimationFrame(raf);
      stream?.getTracks().forEach((t) => t.stop());
    };
  }, [onResult]);

  return (
    <div className="screen detail qr-scan">
      <header className="detail-header">
        <button className="ghost" onClick={onClose} type="button" aria-label="Cancel">
          <XIcon size={24} />
        </button>
        <span className="status-pill">Scan pairing code</span>
      </header>

      {error ? (
        <div className="error">{error}</div>
      ) : (
        <div className="qr-viewport">
          <video ref={videoRef} playsInline muted />
          <div className="qr-frame" />
        </div>
      )}
      {!error && (
        <p className="muted pad">Point the camera at the QR on your PC&apos;s pairing page.</p>
      )}
    </div>
  );
}
