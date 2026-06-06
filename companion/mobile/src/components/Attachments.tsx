import { useRef } from 'react';
import { FileIcon, MonitorIcon, PaperclipIcon, XIcon } from './icons';

// A queued attachment, either a local file from the phone (uploaded on send) or
// a PC file already copied into an attachment server-side (has an id already).
export type Pending =
  | { kind: 'local'; file: File; url: string }
  | { kind: 'remote'; id: string; name: string; mime: string };

export function makeLocal(files: FileList | File[]): Pending[] {
  return Array.from(files).map((file) => ({
    kind: 'local' as const,
    file,
    url: URL.createObjectURL(file),
  }));
}

// Paperclip -> native picker (camera/photo library/files on a phone).
export function AttachButton({
  onPick,
  disabled,
}: {
  onPick: (picked: Pending[]) => void;
  disabled?: boolean;
}) {
  const ref = useRef<HTMLInputElement>(null);
  return (
    <>
      <button
        type="button"
        className="ghost attach"
        onClick={() => ref.current?.click()}
        disabled={disabled}
        aria-label="Attach from phone"
      >
        <PaperclipIcon size={22} />
      </button>
      <input
        ref={ref}
        type="file"
        accept="image/*"
        multiple
        hidden
        onChange={(e) => {
          if (e.target.files?.length) onPick(makeLocal(e.target.files));
          e.target.value = ''; // allow re-picking the same file
        }}
      />
    </>
  );
}

// Monitor -> open the PC file browser.
export function PcFilesButton({ onClick, disabled }: { onClick: () => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      className="ghost attach"
      onClick={onClick}
      disabled={disabled}
      aria-label="Attach a file from the PC"
    >
      <MonitorIcon size={22} />
    </button>
  );
}

// Thumbnails for the queued attachments, each with a remove button. Local images
// preview from their object URL; everything else shows a labelled file chip.
export function AttachPreviews({
  pending,
  onRemove,
  disabled,
}: {
  pending: Pending[];
  onRemove: (index: number) => void;
  disabled?: boolean;
}) {
  if (pending.length === 0) return null;
  return (
    <div className="attach-previews">
      {pending.map((p, i) => {
        const key = p.kind === 'local' ? p.url : p.id;
        const name = p.kind === 'local' ? p.file.name : p.name;
        const isLocalImage = p.kind === 'local' && p.file.type.startsWith('image/');
        return (
          <div className="thumb" key={key}>
            {isLocalImage ? (
              <img src={p.url} alt={name} />
            ) : (
              <span className="thumb-file">
                <FileIcon size={18} />
                <span className="thumb-name">{name}</span>
              </span>
            )}
            {!disabled && (
              <button
                type="button"
                className="thumb-x"
                onClick={() => onRemove(i)}
                aria-label={`Remove ${name}`}
              >
                <XIcon size={13} />
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}
