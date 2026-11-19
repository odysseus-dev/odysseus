export const _ATTACH_RE = /\b(attach(ed|ment|ments|ing)?|enclosed|enclosing|PFA|find attached|see attached|ci-joint|en pi[eè]ce jointe|ajout[eé]|joint|jointe|anbei|im Anhang|beigef[uü]gt|添付|fichier joint)\b/i;

export function _accountCanSend(account) {
  if (!account || !account.smtp_host || !account.smtp_user) return false;
  return !!(account.has_smtp_password || account.oauth_provider);
}

export function _looksLikeWrappedEmailContent(text) {
  const t = String(text || '').replace(/\r\n/g, '\n').trim();
  return /\n---\n/.test(t) && /^(To|Cc|Bcc|Subject|In-Reply-To|References|X-Source-UID|X-Source-Folder):\s*/im.test(t);
}

export function _decodeBase64EmailWrapper(block) {
  const compact = String(block || '').replace(/\s+/g, '');
  if (compact.length < 24 || compact.length % 4 !== 0 || !/^[A-Za-z0-9+/]+={0,2}$/.test(compact)) return null;
  try {
    const bin = atob(compact);
    let decoded = '';
    if (typeof TextDecoder !== 'undefined') {
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i);
      decoded = new TextDecoder('utf-8', { fatal: false }).decode(bytes);
    } else {
      decoded = decodeURIComponent(escape(bin));
    }
    decoded = decoded.replace(/\r\n/g, '\n');
    return _looksLikeWrappedEmailContent(decoded) ? decoded : null;
  } catch (_) {
    return null;
  }
}

export function _buildEmailContent(to, subject, inReplyTo, references, body, sourceUid, sourceFolder, cc, bcc) {
  let header = `To: ${to}`;
  if (cc) header += `\nCc: ${cc}`;
  if (bcc) header += `\nBcc: ${bcc}`;
  header += `\nSubject: ${subject}`;
  if (inReplyTo) header += `\nIn-Reply-To: ${inReplyTo}`;
  if (references) header += `\nReferences: ${references}`;
  if (sourceUid) header += `\nX-Source-UID: ${sourceUid}`;
  if (sourceFolder) header += `\nX-Source-Folder: ${sourceFolder}`;
  return header + '\n---\n' + body;
}

export function _buildEmailContentFromFields(fields, body) {
  const f = fields || {};
  let header = `To: ${f.to || ''}`;
  if (f.cc) header += `\nCc: ${f.cc}`;
  if (f.bcc) header += `\nBcc: ${f.bcc}`;
  header += `\nSubject: ${f.subject || ''}`;
  if (f.inReplyTo) header += `\nIn-Reply-To: ${f.inReplyTo}`;
  if (f.references) header += `\nReferences: ${f.references}`;
  if (f.sourceUid) header += `\nX-Source-UID: ${f.sourceUid}`;
  if (f.sourceFolder) header += `\nX-Source-Folder: ${f.sourceFolder}`;
  if (f.forwardAttachments) header += `\nX-Forward-Attachments: 1`;
  if (Array.isArray(f.attachments) && f.attachments.length) {
    const attStr = f.attachments
      .map(a => `${a.index}:${a.filename}:${a.size}`)
      .join('|');
    header += `\nX-Attachments: ${attStr}`;
  }
  return header + '\n---\n' + (body || '');
}

function _unfoldEmailHeaderLines(header) {
  const lines = [];
  for (const rawLine of String(header || '').replace(/\r\n/g, '\n').split('\n')) {
    if (/^[ \t]/.test(rawLine) && lines.length) {
      lines[lines.length - 1] += ' ' + rawLine.trim();
    } else {
      lines.push(rawLine);
    }
  }
  return lines;
}

export function _parseEmailHeader(content) {
  const empty = { to: '', cc: '', bcc: '', subject: '', inReplyTo: '', references: '', sourceUid: '', sourceFolder: '', forwardAttachments: false, attachments: [], body: content || '' };
  if (!content) return empty;
  const parts = content.split(/\n---\n/);
  if (parts.length < 2) return empty;
  const header = parts[0];
  const body = parts.slice(1).join('\n---\n');
  const fields = { to: '', cc: '', bcc: '', subject: '', inReplyTo: '', references: '', sourceUid: '', sourceFolder: '', forwardAttachments: false, attachments: [], body: body };
  for (const line of _unfoldEmailHeaderLines(header)) {
    const m = line.match(/^(To|Cc|Bcc|Subject|In-Reply-To|References|X-Source-UID|X-Source-Folder|X-Forward-Attachments|X-Attachments):\s*(.*)$/i);
    if (m) {
      let key = m[1].toLowerCase();
      if (key === 'in-reply-to') key = 'inReplyTo';
      else if (key === 'x-source-uid') key = 'sourceUid';
      else if (key === 'x-source-folder') key = 'sourceFolder';
      else if (key === 'x-forward-attachments') {
        fields.forwardAttachments = /^(1|true|yes)$/i.test((m[2] || '').trim());
        continue;
      }
      else if (key === 'x-attachments') {
        fields.attachments = m[2].trim().split('|').map(a => {
          const [index, filename, size] = a.split(':');
          return { index: parseInt(index), filename, size: parseInt(size) };
        });
        continue;
      }
      fields[key] = m[2].trim();
    }
  }
  return fields;
}

export function _emailQuoteMarkerMatch(text) {
  const raw = String(text || '');
  return raw.match(/(?:<p[^>]*>\s*)?-{5,}\s*Previous message\s*-{5,}(?:\s*<\/p>)?/i)
    || raw.match(/-{5,}\s*Previous message\s*-{5,}/i);
}
