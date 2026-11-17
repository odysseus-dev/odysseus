"""Per-principal extraction directories for email attachments.

Shared by the HTTP email routes and the email MCP server, which both extract
attachments under MAIL_ATTACHMENTS_DIR.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def attachment_scope_dir(root, folder, uid, *, owner, account_id) -> Path:
    """Return the extraction directory for one message's attachments.

    IMAP UIDs are small per-mailbox counters and folder names are server- or
    caller-supplied (`/`-delimited hierarchies, absolute or `..` segments), so
    neither may become a path. The directory is one hex segment derived from
    (owner, account, folder, uid): distinct principals and mailboxes never
    share it, and no input can steer it out of *root*. Containment is checked
    after resolution, so a symlinked entry cannot redirect it either.
    """
    scope = json.dumps(
        [str(owner or ""), str(account_id or ""), str(folder or ""), str(uid or "")],
        ensure_ascii=False,
    )
    base = Path(root).resolve()
    target = (base / hashlib.sha256(scope.encode("utf-8")).hexdigest()[:32]).resolve()
    if target.parent != base:
        raise ValueError("attachment directory escapes the extraction root")
    return target
