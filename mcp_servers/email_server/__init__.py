"""Email MCP server package -- split from email_server.py"""

import asyncio

from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from mcp_servers.email_server import _utils as _email

server = _email.server

# Explicit handler dependencies. Keeping their authoritative implementations
# in _utils means helper functions share one stateful module namespace.
_CURRENT_OWNER = _email._CURRENT_OWNER
_MCP_OWNER_ARG = _email._MCP_OWNER_ARG
_OWNER_SCOPE_ERROR = _email._OWNER_SCOPE_ERROR
_ai_draft_reply_to_email = _email._ai_draft_reply_to_email
_archive_email = _email._archive_email
_bulk_move = _email._bulk_move
_bulk_set_flag = _email._bulk_set_flag
_create_email_draft_document = _email._create_email_draft_document
_delete_email = _email._delete_email
_download_attachment = _email._download_attachment
_draft_reply_to_email = _email._draft_reply_to_email
_filter_accounts_for_owner = _email._filter_accounts_for_owner
_fixture_account_rows = _email._fixture_account_rows
_list_accounts_raw = _email._list_accounts_raw
_list_emails = _email._list_emails
_list_emails_across_accounts = _email._list_emails_across_accounts
_load_config = _email._load_config
_mcp_owner_required = _email._mcp_owner_required
_read_accounts_from_db = _email._read_accounts_from_db
_read_email = _email._read_email
_read_email_across_accounts = _email._read_email_across_accounts
_reply_to_email = _email._reply_to_email
_search_emails = _email._search_emails
_search_uids = _email._search_uids
_send_email = _email._send_email
_set_flag = _email._set_flag
_writing_style_guidance = _email._writing_style_guidance

# Main MCP entry points
@server.list_tools()
async def list_tools() -> list[Tool]:
    # The user may have multiple IMAP accounts configured. Every tool accepts an
    # optional `account` param — match by name (e.g. "work"), email address,
    # or account id. Leave it out to use the default account.
    ACCOUNT_PROP = {
        "account": {
            "type": "string",
            "description": "Which email account to use (name, email, or id). "
                           "Omit to use the default account. Use list_email_accounts to discover available accounts.",
        },
    }
    return [
        Tool(
            name="list_email_accounts",
            description=(
                "List the email accounts configured in Odysseus. Returns each account's "
                "name, email address, and whether it's the default. Use this first when "
                "the user asks about a specific inbox by name (e.g. 'check work')."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="list_emails",
            description=(
                "List unread or unresponded emails from the inbox. "
                "Returns subject, sender, date, and cached AI summary for each. "
                "Use this to check what emails need attention. "
                "Pass `account` to scan a non-default mailbox."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "folder": {
                        "type": "string",
                        "description": "IMAP folder to check (default: INBOX)",
                        "default": "INBOX",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of emails to return (default: 20)",
                        "default": 20,
                    },
                    "unresponded_only": {
                        "type": "boolean",
                        "description": "Only show emails without replies (default: false)",
                        "default": False,
                    },
                    "unread_only": {
                        "type": "boolean",
                        "description": "Only show unread emails. Default false so latest/all inbox requests match normal mail clients.",
                        "default": False,
                    },
                    **ACCOUNT_PROP,
                },
                "required": [],
            },
        ),
        Tool(
            name="download_attachment",
            description=(
                "Download an email attachment to the local disk so you can read it. "
                "Returns the local file path which you can then read with read_file. "
                "Use this when you need to review a document, spreadsheet, or other "
                "file attached to an email."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Email UID from list_emails"},
                    "index": {"type": "integer", "description": "Attachment index (from read_email's attachments list)"},
                    "folder": {"type": "string", "description": "IMAP folder (default: INBOX)", "default": "INBOX"},
                    **ACCOUNT_PROP,
                },
                "required": ["uid", "index"],
            },
        ),
        Tool(
            name="send_email",
            description=(
                "Send a new email via SMTP. Provide recipient(s), subject, and body. "
                "This sends immediately; for normal assistant-written email, prefer "
                "draft_email so the user can review and send from Odysseus. "
                "For replying to an existing thread, use reply_to_email instead. "
                "Pass `account` to send from a non-default mailbox."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Recipient email address(es), comma-separated"},
                    "subject": {"type": "string", "description": "Email subject line"},
                    "body": {"type": "string", "description": "Plain text body"},
                    "cc": {"type": "string", "description": "CC address(es), comma-separated (optional)"},
                    "bcc": {"type": "string", "description": "BCC address(es), comma-separated (optional)"},
                    **ACCOUNT_PROP,
                },
                "required": ["to", "subject", "body"],
            },
        ),
        Tool(
            name="draft_email",
            description=(
                "Create a new Odysseus email compose draft document. This DOES NOT send. "
                "Use this as the default way to write an email for the user: it opens "
                "a reviewable email document with To/Cc/Bcc/Subject/body, and the user "
                "can edit or press Send in Odysseus. "
                f"{_writing_style_guidance()}"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Recipient email address(es), comma-separated"},
                    "subject": {"type": "string", "description": "Email subject line"},
                    "body": {"type": "string", "description": "Draft body"},
                    "cc": {"type": "string", "description": "CC address(es), comma-separated (optional)"},
                    "bcc": {"type": "string", "description": "BCC address(es), comma-separated (optional)"},
                    "title": {"type": "string", "description": "Optional Odysseus document title"},
                    **ACCOUNT_PROP,
                },
                "required": ["to", "subject", "body"],
            },
        ),
        Tool(
            name="reply_to_email",
            description=(
                "Reply to an existing email by UID. This sends immediately. Do NOT use "
                "for normal 'write/draft a reply saying X' requests; use "
                "draft_email_reply so the user can review and send from Odysseus. "
                "Only use this when the user explicitly says to send now. Automatically threads the reply with "
                "In-Reply-To and References headers, prefixes 'Re:' on the subject, and "
                "uses the original sender as the recipient. Set reply_all=true to also CC "
                "the original To/Cc recipients. For follow-up 'reply ...' requests, use "
                "the exact UID from the latest list_emails/read_email result; never invent UID 1."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Exact Email UID from list_emails/read_email; never invent UID 1"},
                    "body": {"type": "string", "description": "Reply body text"},
                    "folder": {"type": "string", "description": "IMAP folder (default: INBOX)", "default": "INBOX"},
                    "reply_all": {"type": "boolean", "description": "Reply to all recipients (default: false)", "default": False},
                    **ACCOUNT_PROP,
                },
                "required": ["uid", "body"],
            },
        ),
        Tool(
            name="draft_email_reply",
            description=(
                "Create an Odysseus email reply draft document for an existing email UID. "
                "This DOES NOT send. It threads the draft with In-Reply-To/References, "
                "prefills the recipient and subject, and stores source email metadata so "
                "the user can review and send from the normal email composer. "
                f"{_writing_style_guidance()}"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Exact Email UID from list_emails/read_email; never invent UID 1"},
                    "body": {"type": "string", "description": "Draft reply body text"},
                    "folder": {"type": "string", "description": "IMAP folder (default: INBOX)", "default": "INBOX"},
                    "reply_all": {"type": "boolean", "description": "Reply to all recipients (default: false)", "default": False},
                    "title": {"type": "string", "description": "Optional Odysseus document title"},
                    **ACCOUNT_PROP,
                },
                "required": ["uid", "body"],
            },
        ),
        Tool(
            name="ai_draft_email_reply",
            description=(
                "Generate an AI reply using Odysseus' existing AI Reply behavior, "
                "including Settings > Email > Writing Style, then create an email "
                "compose document for review. This DOES NOT send and does NOT save "
                "to the mailbox Drafts folder. Use this when the user asks you to "
                "write or draft a reply to an email without dictating the exact body."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Exact Email UID from list_emails/read_email; never invent UID 1"},
                    "folder": {"type": "string", "description": "IMAP folder (default: INBOX)", "default": "INBOX"},
                    "reply_all": {"type": "boolean", "description": "Reply to all recipients (default: false)", "default": False},
                    "title": {"type": "string", "description": "Optional Odysseus document title"},
                    **ACCOUNT_PROP,
                },
                "required": ["uid"],
            },
        ),
        Tool(
            name="archive_email",
            description="Move an email out of the inbox into the Archive folder. Use after handling an email you want to keep but no longer need in the inbox.",
            inputSchema={
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Email UID from list_emails"},
                    "folder": {"type": "string", "description": "Source folder (default: INBOX)", "default": "INBOX"},
                    **ACCOUNT_PROP,
                },
                "required": ["uid"],
            },
        ),
        Tool(
            name="delete_email",
            description="Delete an email. By default moves it to the Trash folder; pass permanent=true to expunge immediately.",
            inputSchema={
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Email UID from list_emails"},
                    "folder": {"type": "string", "description": "Source folder (default: INBOX)", "default": "INBOX"},
                    "permanent": {"type": "boolean", "description": "Hard-delete instead of move to Trash", "default": False},
                    **ACCOUNT_PROP,
                },
                "required": ["uid"],
            },
        ),
        Tool(
            name="mark_email_read",
            description="Mark an email as read (\\Seen flag) or unread (read=false).",
            inputSchema={
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Email UID"},
                    "folder": {"type": "string", "description": "IMAP folder", "default": "INBOX"},
                    "read": {"type": "boolean", "description": "True to mark read, false to mark unread", "default": True},
                    **ACCOUNT_PROP,
                },
                "required": ["uid"],
            },
        ),
        Tool(
            name="bulk_email",
            description=(
                "Perform one action on MANY emails at once — the efficient way to "
                "'mark all as read', 'archive these', 'delete all spam', etc. Select "
                "messages either by an explicit `uids` list OR by `all_unread: true` "
                "(operates on every unread message in the folder). Far better than "
                "calling mark_email_read / archive_email once per message."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["mark_read", "mark_unread", "archive", "delete", "junk"],
                        "description": "What to do to every selected message.",
                    },
                    "uids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Explicit list of UIDs. Omit if using all_unread.",
                    },
                    "all_unread": {
                        "type": "boolean",
                        "description": "Operate on ALL unread messages in the folder (ignores uids).",
                        "default": False,
                    },
                    "folder": {"type": "string", "description": "IMAP folder", "default": "INBOX"},
                    "permanent": {"type": "boolean", "description": "For delete: expunge instead of moving to Trash.", "default": False},
                    **ACCOUNT_PROP,
                },
                "required": ["action"],
            },
        ),
        Tool(
            name="search_emails",
            description=(
                "Search emails by free-text query (sender, subject, or body). "
                "Walks INBOX + Sent + Archive by default so older threads are findable, "
                "not just recent unread. Use this whenever the user names a person or "
                "topic that isn't in the most recent inbox slice — e.g. 'Sara Sotheby's', "
                "'invoice from EY', 'last email about the property'. Returns matching "
                "emails with their UIDs so you can read_email or reply_to_email."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Free-text query. Matches FROM, SUBJECT, and body TEXT.",
                    },
                    "folders": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Folders to search (default: INBOX, Sent, Archive)",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Max results per folder (default: 20)",
                        "default": 20,
                    },
                    **ACCOUNT_PROP,
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="read_email",
            description=(
                "Read the full content of a specific email. "
                "Provide either the UID (from list_emails) or a Message-ID. "
                "Returns the subject, sender, date, and full body text."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "uid": {
                        "type": "string",
                        "description": "Email UID from list_emails results",
                    },
                    "message_id": {
                        "type": "string",
                        "description": "RFC Message-ID header value",
                    },
                    "folder": {
                        "type": "string",
                        "description": "IMAP folder (default: INBOX)",
                        "default": "INBOX",
                    },
                    **ACCOUNT_PROP,
                },
                "required": [],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    arguments = dict(arguments) if isinstance(arguments, dict) else {}
    owner = str(arguments.pop(_MCP_OWNER_ARG, "") or "").strip()
    owner_token = _CURRENT_OWNER.set(owner or None)
    try:
        all_db_accounts = _read_accounts_from_db()
        if _mcp_owner_required(all_db_accounts):
            return [TextContent(type="text", text=_OWNER_SCOPE_ERROR)]

        if name == "list_email_accounts":
            rows = _filter_accounts_for_owner(all_db_accounts)
            if not rows:
                rows = _fixture_account_rows()
            if not rows:
                if all_db_accounts and owner:
                    return [TextContent(type="text", text="No email accounts configured for this owner.")]
                return [TextContent(type="text", text="No email accounts configured. Legacy single-account mode active.")]
            lines = [f"Found {len(rows)} email account(s):\n"]
            for r in rows:
                star = " (default)" if r.get("is_default") else ""
                lines.append(
                    f"- **{r['name']}**{star}\n"
                    f"  email: {r.get('imap_user') or r.get('from_address') or '(unknown)'}\n"
                    f"  id: {r['id']}"
                )
            return [TextContent(type="text", text="\n".join(lines))]

        acct = arguments.get("account")  # consumed by all email ops

        if name == "list_emails":
            max_results = arguments.get("max_results", arguments.get("limit", 20))
            unresponded_only = arguments.get("unresponded_only", False)
            unread_only = arguments.get("unread_only", False)
            # Build a header note so the LLM always knows which account was hit
            # AND what other accounts exist. Prevents "I can see emails" →
            # user: "I have 2 inboxes" → "which one?" loop.
            all_accounts = _list_accounts_raw()
            header_lines = []
            errors = []
            if len(all_accounts) >= 2 and not acct:
                results, errors = _list_emails_across_accounts(
                    folder=arguments.get("folder", "INBOX"),
                    max_results=max_results,
                    unresponded_only=unresponded_only,
                    unread_only=unread_only,
                )
                account_names = [
                    f"{a.get('name') or a.get('imap_user')} <{a.get('imap_user') or a.get('from_address') or '?'}>"
                    for a in all_accounts
                ]
                header_lines.append(
                    f"[EMAIL ACCOUNT CONTEXT: No `account` was provided, so this result is merged across configured accounts: "
                    f"{', '.join(account_names)}. Each row includes its source account.]\n"
                )
            else:
                results = _list_emails(
                    folder=arguments.get("folder", "INBOX"),
                    max_results=max_results,
                    unresponded_only=unresponded_only,
                    unread_only=unread_only,
                    account=acct,
                )
                active_cfg = _load_config(acct)
                if active_cfg.get("account_name") or active_cfg.get("imap_user"):
                    for item in results:
                        item["_account"] = active_cfg.get("account_name") or active_cfg.get("imap_user") or "default"
                        item["_account_email"] = active_cfg.get("imap_user") or ""

            if len(all_accounts) >= 2 and acct:
                active_cfg = _load_config(acct)
                active_name = active_cfg.get("account_name") or "default"
                active_email = active_cfg.get("imap_user") or ""
                other = [
                    f"{a['name']} <{a.get('imap_user') or a.get('from_address') or '?'}>"
                    for a in all_accounts
                    if a['id'] != active_cfg.get("account_id")
                ]
                header_lines.append(
                    f"[EMAIL ACCOUNT CONTEXT: This result is ONLY from account `{active_name}` ({active_email}). "
                    f"Other configured accounts: {', '.join(other)}. "
                    f"If the user asks for Gmail/another inbox, call list_emails again with `account` set to that account name or email.]\n"
                )
            if errors:
                header_lines.append("[EMAIL ACCOUNT ERRORS: " + "; ".join(errors) + "]\n")

            if not results:
                msg = "No unread/unresponded emails found."
                if header_lines:
                    msg = "\n".join(header_lines) + msg
                return [TextContent(type="text", text=msg)]

            lines = header_lines + [f"Found {len(results)} email(s):\n"]
            for i, em in enumerate(results, 1):
                line = f"{i}. **{em['subject']}**\n   From: {em['from']} ({em['from_address']})\n   Date: {em['date']}\n   UID: {em['uid']}"
                if em.get("_account"):
                    account_label = em.get("_account")
                    if em.get("_account_email"):
                        account_label += f" <{em['_account_email']}>"
                    line += f"\n   Account: {account_label}"
                if em.get("summary"):
                    line += f"\n   Summary: {em['summary']}"
                lines.append(line)
            return [TextContent(type="text", text="\n\n".join(lines))]

        elif name == "download_attachment":
            uid = arguments.get("uid")
            index = arguments.get("index")
            folder = arguments.get("folder", "INBOX")
            if uid is None or index is None:
                return [TextContent(type="text", text="Error: uid and index are required")]
            result = _download_attachment(uid, index, folder, account=acct)
            if "error" in result:
                return [TextContent(type="text", text=f"Error: {result['error']}")]
            text = (
                f"Attachment downloaded to: `{result['path']}`\n"
                f"Filename: {result['filename']}\n"
                f"Size: {result['size']} bytes\n\n"
                f"You can now read this file using the read_file tool."
            )
            return [TextContent(type="text", text=text)]

        elif name == "search_emails":
            q = arguments.get("query", "")
            folders = arguments.get("folders") or None
            max_results = arguments.get("max_results", 20)
            try:
                hits = _search_emails(q, folders=folders, max_results=max_results, account=acct)
            except Exception as e:
                return [TextContent(type="text", text=f"Search failed: {e}")]
            if not hits:
                return [TextContent(type="text", text=f'No emails matched "{q}".')]
            lines = [f'Found {len(hits)} email(s) matching "{q}":\n']
            for i, em in enumerate(hits, 1):
                lines.append(
                    f"{i}. **{em['subject']}**\n"
                    f"   From: {em['from']} ({em['from_address']})\n"
                    f"   Date: {em['date']}\n"
                    f"   Folder: {em.get('_folder', 'INBOX')}\n"
                    f"   UID: {em['uid']}"
                )
                if em.get('to'):
                    lines.append(f"   To: {em['to']}")
                if em.get('summary'):
                    lines.append(f"   Summary: {em['summary']}")
            return [TextContent(type="text", text="\n".join(lines))]

        elif name == "read_email":
            all_accounts = _list_accounts_raw()
            if len(all_accounts) >= 2 and not acct:
                result = _read_email_across_accounts(
                    uid=arguments.get("uid"),
                    message_id=arguments.get("message_id"),
                    folder=arguments.get("folder", "INBOX"),
                )
            else:
                result = _read_email(
                    uid=arguments.get("uid"),
                    message_id=arguments.get("message_id"),
                    folder=arguments.get("folder", "INBOX"),
                    account=acct,
                )
            if "error" in result:
                return [TextContent(type="text", text=f"Error: {result['error']}")]

            text = (
                f"**Subject:** {result['subject']}\n"
                f"**From:** {result['from']} ({result['from_address']})\n"
                f"**Date:** {result['date']}\n"
                f"**UID:** {result['uid']}\n"
                f"**Account:** {result.get('account', 'default')} ({result.get('account_email', '')})\n"
                f"**Message-ID:** {result['message_id']}\n"
            )
            if result.get('attachments'):
                text += f"\n**Attachments ({len(result['attachments'])}):**\n"
                for a in result['attachments']:
                    size_kb = a['size'] // 1024
                    text += f"  - [{a['index']}] {a['filename']} ({a['content_type']}, {size_kb}KB)\n"
                text += "\n_Use `download_attachment` with the UID and index to download._\n"
            text += f"\n---\n\n{result['body']}"
            return [TextContent(type="text", text=text)]

        elif name == "send_email":
            to = arguments.get("to")
            subject = arguments.get("subject")
            body = arguments.get("body")
            if not to or not subject or body is None:
                return [TextContent(type="text", text="Error: to, subject, and body are required")]
            result = _send_email(
                to=to,
                subject=subject,
                body=body,
                cc=arguments.get("cc"),
                bcc=arguments.get("bcc"),
                account=acct,
            )
            if "error" in result:
                return [TextContent(type="text", text=f"Error: {result['error']}")]
            if result.get("pending"):
                return [TextContent(
                    type="text",
                    text=(
                        f"Draft staged for approval (pending id: {result.get('pending_id')}). "
                        "Nothing has been sent yet. Review and approve it in Odysseus before delivery."
                    ),
                )]
            acct_note = f" (from {result['account']})" if result.get("account") else ""
            return [TextContent(type="text", text=f"Sent email to {result['to']} with subject '{result['subject']}'{acct_note}.")]

        elif name == "draft_email":
            to = arguments.get("to")
            subject = arguments.get("subject")
            body = arguments.get("body")
            if not to or not subject or body is None:
                return [TextContent(type="text", text="Error: to, subject, and body are required")]
            result = _create_email_draft_document(
                to=to,
                subject=subject,
                body=body,
                title=arguments.get("title"),
                cc=arguments.get("cc"),
                bcc=arguments.get("bcc"),
                account=acct,
            )
            acct_note = f" from {result['account']}" if result.get("account") else ""
            return [TextContent(
                type="text",
                text=(
                    f"Created Odysseus email draft `{result['title']}` "
                    f"(document ID: {result['doc_id']}){acct_note}. "
                    "It has not been sent; open the document in Odysseus to review and send."
                ),
            )]

        elif name == "reply_to_email":
            uid = arguments.get("uid")
            body = arguments.get("body")
            if not uid or body is None:
                return [TextContent(type="text", text="Error: uid and body are required")]
            result = _reply_to_email(
                uid=uid,
                body=body,
                folder=arguments.get("folder", "INBOX"),
                reply_all=bool(arguments.get("reply_all", False)),
                account=acct,
            )
            if "error" in result:
                return [TextContent(type="text", text=f"Error: {result['error']}")]
            # Mark original as answered
            try:
                _set_flag(uid, arguments.get("folder", "INBOX"), "\\Answered", add=True, account=acct)
            except Exception:
                pass
            return [TextContent(type="text", text=f"Replied to UID {uid}: '{result['subject']}' → {result['to']}")]

        elif name == "draft_email_reply":
            uid = arguments.get("uid")
            body = arguments.get("body")
            if not uid or body is None:
                return [TextContent(type="text", text="Error: uid and body are required")]
            result = _draft_reply_to_email(
                uid=uid,
                body=body,
                folder=arguments.get("folder", "INBOX"),
                reply_all=bool(arguments.get("reply_all", False)),
                account=acct,
                title=arguments.get("title"),
            )
            if "error" in result:
                return [TextContent(type="text", text=f"Error: {result['error']}")]
            acct_note = f" from {result['account']}" if result.get("account") else ""
            return [TextContent(
                type="text",
                text=(
                    f"Created Odysseus reply draft `{result['title']}` for UID {uid} "
                    f"(document ID: {result['doc_id']}){acct_note}. "
                    "It has not been sent; open the document in Odysseus to review and send."
                ),
            )]

        elif name == "ai_draft_email_reply":
            uid = arguments.get("uid")
            if not uid:
                return [TextContent(type="text", text="Error: uid is required")]
            result = await _ai_draft_reply_to_email(
                uid=uid,
                folder=arguments.get("folder", "INBOX"),
                reply_all=bool(arguments.get("reply_all", False)),
                account=acct,
                title=arguments.get("title"),
            )
            if "error" in result:
                return [TextContent(type="text", text=f"Error: {result['error']}")]
            acct_note = f" from {result['account']}" if result.get("account") else ""
            return [TextContent(
                type="text",
                text=(
                    f"Generated AI reply and created Odysseus compose draft "
                    f"`{result['title']}` for UID {uid} (document ID: {result['doc_id']}){acct_note}. "
                    "It has not been sent; open the document in Odysseus to review and send."
                ),
            )]

        elif name == "archive_email":
            uid = arguments.get("uid")
            if not uid:
                return [TextContent(type="text", text="Error: uid is required")]
            ok = _archive_email(uid, arguments.get("folder", "INBOX"), account=acct)
            return [TextContent(type="text", text=f"{'Archived' if ok else 'Failed to archive'} UID {uid}")]

        elif name == "delete_email":
            uid = arguments.get("uid")
            if not uid:
                return [TextContent(type="text", text="Error: uid is required")]
            ok = _delete_email(
                uid,
                arguments.get("folder", "INBOX"),
                permanent=bool(arguments.get("permanent", False)),
                account=acct,
            )
            return [TextContent(type="text", text=f"{'Deleted' if ok else 'Failed to delete'} UID {uid}")]

        elif name == "mark_email_read":
            uid = arguments.get("uid")
            if not uid:
                return [TextContent(type="text", text="Error: uid is required")]
            read = bool(arguments.get("read", True))
            ok = _set_flag(uid, arguments.get("folder", "INBOX"), "\\Seen", add=read, account=acct)
            state = "read" if read else "unread"
            return [TextContent(type="text", text=f"{'Marked' if ok else 'Failed to mark'} UID {uid} as {state}")]

        elif name == "bulk_email":
            action = arguments.get("action", "")
            folder = arguments.get("folder", "INBOX")
            all_unread = bool(arguments.get("all_unread", False))
            uids = arguments.get("uids") or []
            if all_unread:
                uids = _search_uids(folder, "UNSEEN", account=acct)
            if not uids:
                return [TextContent(type="text", text="No messages selected (pass uids or all_unread=true).")]
            requested_n = len(uids)
            changed_n = 0
            try:
                if action == "mark_read":
                    changed_n = _bulk_set_flag(uids, folder, "\\Seen", add=True, account=acct)
                    verb = "marked read"
                elif action == "mark_unread":
                    changed_n = _bulk_set_flag(uids, folder, "\\Seen", add=False, account=acct)
                    verb = "marked unread"
                elif action == "archive":
                    cfg = _load_config(acct)
                    changed_n = _bulk_move(uids, folder, cfg["archive_folder"], account=acct, role="archive")
                    verb = "archived"
                elif action == "junk":
                    cfg = _load_config(acct)
                    junk_folder = cfg.get("junk_folder") or "Junk"
                    changed_n = _bulk_move(uids, folder, junk_folder, account=acct, role="junk")
                    verb = "moved to Junk"
                elif action == "delete":
                    permanent = bool(arguments.get("permanent", False))
                    if permanent:
                        changed_n = _bulk_set_flag(uids, folder, "\\Deleted", add=True, account=acct)
                        verb = "permanently deleted"
                    else:
                        cfg = _load_config(acct)
                        changed_n = _bulk_move(uids, folder, cfg["trash_folder"], account=acct, role="trash")
                        verb = "moved to Trash"
                else:
                    return [TextContent(type="text", text=f"Unknown bulk action: {action!r}. Use mark_read/mark_unread/archive/delete/junk.")]
            except Exception as e:
                return [TextContent(type="text", text=f"Bulk {action} failed after partial work: {e}")]
            if changed_n <= 0:
                return [TextContent(type="text", text=f"No matching UIDs found in {folder}; 0 of {requested_n} email(s) {verb}.")]
            suffix = "" if changed_n == requested_n else f" ({changed_n} of {requested_n} requested UIDs matched)"
            return [TextContent(type="text", text=f"Done — {changed_n} email(s) {verb}{suffix}.")]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as e:
        return [TextContent(type="text", text=f"Error: {e}")]
    finally:
        _CURRENT_OWNER.reset(owner_token)


# ── Main ──

async def run():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )
