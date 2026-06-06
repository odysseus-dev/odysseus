import { useRef } from 'react';
import { PaperclipIcon, XIcon } from './icons';

// One file the user has picked but not yet uploaded. `url` is a local object URL
// for the preview thumbnail (revoked when removed).
export interface PendingFile {
  file: File;
  url: string;
}

export function makePending(files: FileList | File[]): PendingFile[] {
  return Array.from(files).map((file) => ({ file, url: URL.createObjectURL(file) }));
}

// Paperclip button that opens the native file picker. On a phone this offers
// the camera, photo library, and files, so "from your phone" is covered without
// a native plugin. `accept="image/*"` keeps it to photos.
export function AttachButton({
  onPick,
  disabled,
}: {
  onPick: (files: PendingFile[]) => void;
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
        aria-label="Attach image"
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
          if (e.target.files?.length) onPick(makePending(e.target.files));
          e.target.value = ''; // allow re-picking the same file
        }}
      />
    </>
  );
}

// Row of thumbnails for the files queued to send, each with a remove button.
export function AttachPreviews({
  pending,
  onRemove,
  disabled,
}: {
  pending: PendingFile[];
  onRemove: (index: number) => void;
  disabled?: boolean;
}) {
  if (pending.length === 0) return null;
  return (
    <div className="attach-previews">
      {pending.map((p, i) => (
        <div className="thumb" key={p.url}>
          {p.file.type.startsWith('image/') ? (
            <img src={p.url} alt={p.file.name} />
          ) : (
            <span className="thumb-file">{p.file.name}</span>
          )}
          {!disabled && (
            <button
              type="button"
              className="thumb-x"
              onClick={() => onRemove(i)}
              aria-label={`Remove ${p.file.name}`}
            >
              <XIcon size={13} />
            </button>
          )}
        </div>
      ))}
    </div>
  );
}
