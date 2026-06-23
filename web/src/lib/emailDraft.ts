export interface EmailDraftAttachment {
  token: string
  filename: string
  size?: number
}

export interface EmailDraftFields {
  to: string
  cc: string
  bcc: string
  subject: string
  inReplyTo: string
  references: string
  sourceUid: string
  sourceFolder: string
  sourceAccount: string
  attachments: EmailDraftAttachment[]
  body: string
}

export const emptyEmailDraft: EmailDraftFields = {
  to: "",
  cc: "",
  bcc: "",
  subject: "",
  inReplyTo: "",
  references: "",
  sourceUid: "",
  sourceFolder: "",
  sourceAccount: "",
  attachments: [],
  body: "",
}

function parseAttachmentList(value: string): EmailDraftAttachment[] {
  return value.split("|").map((part) => {
    const [token, filename, size] = part.split(":")
    if (!token || !filename) return null
    const parsedSize = Number.parseInt(decodeURIComponent(size || ""), 10)
    return {
      token: decodeURIComponent(token),
      filename: decodeURIComponent(filename),
      size: Number.isFinite(parsedSize) ? parsedSize : undefined,
    }
  }).filter(Boolean) as EmailDraftAttachment[]
}

function formatAttachmentList(attachments: EmailDraftAttachment[]): string {
  return attachments
    .filter((att) => att.token && att.filename)
    .map((att) => [att.token, att.filename, att.size ? String(att.size) : ""].map(encodeURIComponent).join(":"))
    .join("|")
}

export function parseEmailDraft(content = ""): EmailDraftFields {
  const parts = content.split(/\n---\n/)
  if (parts.length < 2) return { ...emptyEmailDraft, body: content }
  const fields = { ...emptyEmailDraft, body: parts.slice(1).join("\n---\n") }
  for (const line of parts[0].split("\n")) {
    const m = line.match(/^(To|Cc|Bcc|Subject|In-Reply-To|References|X-Source-UID|X-Source-Folder|X-Source-Account|X-Compose-Attachments):\s*(.*)$/i)
    if (!m) continue
    const value = m[2].trim()
    switch (m[1].toLowerCase()) {
      case "to": fields.to = value; break
      case "cc": fields.cc = value; break
      case "bcc": fields.bcc = value; break
      case "subject": fields.subject = value; break
      case "in-reply-to": fields.inReplyTo = value; break
      case "references": fields.references = value; break
      case "x-source-uid": fields.sourceUid = value; break
      case "x-source-folder": fields.sourceFolder = value; break
      case "x-source-account": fields.sourceAccount = value; break
      case "x-compose-attachments": fields.attachments = parseAttachmentList(value); break
    }
  }
  return fields
}

export function buildEmailDraft(fields: EmailDraftFields): string {
  const lines = [
    `To: ${fields.to}`,
    fields.cc ? `Cc: ${fields.cc}` : null,
    fields.bcc ? `Bcc: ${fields.bcc}` : null,
    `Subject: ${fields.subject}`,
    fields.inReplyTo ? `In-Reply-To: ${fields.inReplyTo}` : null,
    fields.references ? `References: ${fields.references}` : null,
    fields.sourceUid ? `X-Source-UID: ${fields.sourceUid}` : null,
    fields.sourceFolder ? `X-Source-Folder: ${fields.sourceFolder}` : null,
    fields.sourceAccount ? `X-Source-Account: ${fields.sourceAccount}` : null,
    fields.attachments.length > 0 ? `X-Compose-Attachments: ${formatAttachmentList(fields.attachments)}` : null,
  ].filter(Boolean)
  return `${lines.join("\n")}\n---\n${fields.body}`
}

export function replySubject(subject?: string): string {
  const base = (subject || "").trim()
  if (!base) return "Re:"
  return /^re\s*:/i.test(base) ? base : `Re: ${base}`
}
