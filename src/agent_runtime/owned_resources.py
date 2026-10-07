"""Resolve owned selectors before execution and consume exact server identities."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.agent_runtime.authority import ExactOperation

from src.agent_runtime.resources import (
    FilesystemResource, FilesystemRoot, FilesystemScope, OwnedResource,
    OWNED_TOOL_NAMESPACES, ResourceIdentityError,
)


def _args(content):
    if not isinstance(json.loads(content or "{}"), dict):
        raise ResourceIdentityError("Owned resource arguments must be an object")
    from src.tools._common import _parse_tool_args
    value = _parse_tool_args(content)
    if not isinstance(value, dict):
        raise ResourceIdentityError("Owned resource arguments must be an object")
    return dict(value)


def _selector(args, keys):
    values = [args[k] for k in keys if k in args and args[k] not in (None, "")]
    if any(not isinstance(v, str) or not v.strip() for v in values):
        raise ResourceIdentityError("Record selectors must be strings")
    values = [v.strip() for v in values]
    if len(set(values)) > 1:
        raise ResourceIdentityError("Conflicting record aliases")
    return values[0] if values else ""


def _revision(row, namespace):
    created = getattr(row, "created_at", None)
    updated = getattr(row, "updated_at", None)
    if created is None or not hasattr(created, "isoformat") or updated is None or not hasattr(updated, "isoformat"):
        raise ResourceIdentityError("Record has no observable revision")
    version = getattr(row, "version_count", "") if namespace == "documents" else ""
    if namespace == "documents" and type(version) is not int:
        raise ResourceIdentityError("Document version is unresolved")
    return f"{created.isoformat()}:{updated.isoformat()}:{version}"


def _record(namespace, owner, thread, row):
    if (getattr(row, "owner", None) != owner or not isinstance(getattr(row, "id", None), str)
            or row.id in {"", "*"}):
        raise ResourceIdentityError("Record ownership is unresolved")
    linked = str(getattr(row, "session_id", "") or "") if namespace == "documents" else row.id if namespace == "threads" else ""
    return OwnedResource(namespace, owner, thread, namespace, row.id, _revision(row, namespace), linked)


def _row(namespace, identifier, owner):
    from core.database import SessionLocal, Document, Session, Note
    model = {"documents": Document, "threads": Session, "notes": Note}[namespace]
    db = SessionLocal()
    try:
        row = db.query(model).filter(model.id == identifier, model.owner == owner).first()
        if row is None or (namespace == "documents" and not row.is_active):
            raise ResourceIdentityError("Owned record is missing or inaccessible")
        db.expunge(row)
        return row
    finally:
        db.close()


@dataclass(frozen=True)
class AttachmentResource:
    record: OwnedResource
    file: FilesystemResource

    def __post_init__(self):
        if not isinstance(self.record, OwnedResource) or not isinstance(self.file, FilesystemResource) or self.file.root.owner != self.record.owner:
            raise ValueError("Malformed attachment identity")

    def to_dict(self):
        return {"record": self.record.to_dict(), "file": self.file.to_dict()}


def _attachment(identifier, owner, thread):
    from src.tool_utils import get_upload_handler
    handler = get_upload_handler()
    if handler is None:
        raise ResourceIdentityError("Attachment store is unavailable")
    info = handler.resolve_upload(identifier, owner=owner, allow_admin=False)
    if not isinstance(info, dict) or info.get("id") != identifier or info.get("owner") != owner:
        raise ResourceIdentityError("Attachment ownership is unresolved")
    root = FilesystemRoot.seal(handler.upload_dir, scope=FilesystemScope.PRIVATE, owner=owner)
    file = FilesystemResource.resolve(root, info.get("path"))
    if file.identity.kind != "file":
        raise ResourceIdentityError("Attachment must identify a file")
    revision = str(info.get("checksum_sha256") or info.get("hash") or info.get("uploaded_at") or "")
    if not revision:
        raise ResourceIdentityError("Attachment has no observable revision")
    return AttachmentResource(OwnedResource("attachments", owner, thread, "attachments", identifier, revision), file)


_VAULT_RECORDS = {}


def _vault_revision(cfg, owner):
    from src.agent_runtime.remote_resources import endpoint_identity, configuration_incarnation
    if not isinstance(cfg, dict) or cfg.get("owner") != owner:
        raise ResourceIdentityError("Vault configuration has no matching explicit owner")
    endpoint = endpoint_identity(cfg.get("server_url") or cfg.get("url") or "")
    return endpoint + ":" + configuration_incarnation((cfg.get("server_url") or cfg.get("url"), cfg.get("email"), cfg.get("unlocked_at"), cfg.get("session")))


def observe_vault_records(owner, cfg, records):
    """Only a server search response produces record observations, not grants."""
    from src.tools.vault import _load_vault_config
    from src.agent_runtime.remote_resources import configuration_incarnation
    from uuid import UUID
    revision = _vault_revision(cfg, owner)
    if _vault_revision(_load_vault_config(), owner) != revision or not isinstance(records, list):
        raise ResourceIdentityError("Vault producer configuration changed")
    observed = {}
    for row in records:
        if not isinstance(row, dict):
            raise ResourceIdentityError("Malformed vault producer record")
        try:
            identifier = str(UUID(row.get("id", "")))
        except (ValueError, TypeError, AttributeError) as error:
            raise ResourceIdentityError("Vault producer record has no exact UUID") from error
        if identifier in observed or not isinstance(row.get("name", ""), str):
            raise ResourceIdentityError("Ambiguous vault producer identity")
        observed[identifier] = (row.get("name", ""), configuration_incarnation(json.dumps(row, sort_keys=True, allow_nan=False)))
    catalog = _VAULT_RECORDS.setdefault((owner, revision), {})
    catalog.update(observed)


def _vault_resource(owner, thread, identifier):
    from src.tools.vault import _load_vault_config
    revision = _vault_revision(_load_vault_config(), owner)
    if identifier != "*":
        record = _VAULT_RECORDS.get((owner, revision), {}).get(identifier)
        if record is None:
            raise ResourceIdentityError("Vault record has no server observation; search the owner vault first")
        revision += ":" + record[1]
    return OwnedResource("vault", owner, thread, "vault", identifier, revision)


def _vault_selector(owner, selector):
    from src.tools.vault import _load_vault_config
    revision = _vault_revision(_load_vault_config(), owner)
    rows = _VAULT_RECORDS.get((owner, revision), {})
    if selector in rows:
        return selector
    matches = [identifier for identifier, (name, _) in rows.items()
               if identifier.startswith(selector) or name == selector]
    if not selector or len(matches) != 1:
        raise ResourceIdentityError("Vault selector is missing or ambiguous")
    return matches[0]


def _memory_record(identifier, owner, thread, *, prefix=False):
    from src.ai_interaction import _memory_manager
    if _memory_manager is None:
        raise ResourceIdentityError("Memory store is unavailable")
    rows = [row for row in _memory_manager.load(owner=owner) if isinstance(row, dict)
            and row.get("owner") == owner and isinstance(row.get("id"), str)
            and (row["id"].startswith(identifier) if prefix else row["id"] == identifier)]
    if len(rows) != 1 or not identifier or rows[0].get("timestamp") is None or rows[0]["id"] in {"", "*"}:
        raise ResourceIdentityError("Memory selector is missing or ambiguous")
    from src.agent_runtime.remote_resources import configuration_incarnation
    row = rows[0]
    # A same-second edit still changes the private revision without serializing content.
    revision = configuration_incarnation(json.dumps(row, sort_keys=True, allow_nan=False))
    return OwnedResource("memory", owner, thread, "memory", row["id"], revision)


@dataclass(frozen=True)
class BoundOwnedOperation:
    operation: "ExactOperation"
    execution_input: str
    request_id: str
    owner: str
    thread_id: str
    resources: tuple[OwnedResource, ...]
    attachments: tuple[AttachmentResource, ...] = ()
    document_id: str = ""
    document_version: int | None = None
    document_digest: str = ""

    def __post_init__(self):
        from src.agent_runtime.authority import ExactOperation
        if (not isinstance(self.operation, ExactOperation) or not self.owner or not self.thread_id
                or any(not isinstance(v, str) for v in (self.execution_input, self.request_id, self.owner, self.thread_id, self.document_id, self.document_digest))
                or not isinstance(self.resources, tuple) or not self.resources
                or any(not isinstance(r, OwnedResource) or (r.owner, r.thread_id) != (self.owner, self.thread_id) for r in self.resources)
                or not isinstance(self.attachments, tuple) or any(not isinstance(a, AttachmentResource) for a in self.attachments)):
            raise ValueError("Malformed owned resource operation")
        namespace = OWNED_TOOL_NAMESPACES.get(self.operation.tool)
        if (any(r.namespace != namespace or r.collection != namespace or (r.record_id != "*" and not r.revision) for r in self.resources)
                or tuple(a.record for a in self.attachments) != tuple(r for r in self.resources if r.namespace == "attachments")):
            raise ValueError("Malformed owned resource identity")
        if self.document_id:
            if (type(self.document_version) is not int or self.document_version < 1
                    or not re.fullmatch(r"[0-9a-f]{64}", self.document_digest)
                    or not any(r.namespace == "documents" and r.record_id == self.document_id for r in self.resources)):
                raise ValueError("Malformed document binding")
        elif any(r.namespace == "documents" and r.record_id != "*" for r in self.resources):
            raise ValueError("Missing document binding")

    def to_dict(self):
        from src.agent_runtime.remote_resources import configuration_incarnation
        return {"request_id": self.request_id, "owner": self.owner, "thread_id": self.thread_id,
                "tool": self.operation.transport_tool,
                "execution_input_digest": configuration_incarnation(self.execution_input),
                "resources": [r.to_dict() for r in self.resources],
                "attachments": [a.to_dict() for a in self.attachments],
                "document_id": self.document_id, "document_version": self.document_version,
                "document_digest": self.document_digest}

    def validate(self):
        for resource in self.resources:
            if resource.record_id == "*":
                if resource.namespace == "vault" and _vault_resource(self.owner, self.thread_id, "*") != resource:
                    raise ResourceIdentityError("Vault identity changed")
                continue
            if resource.namespace == "attachments":
                expected = next((a for a in self.attachments if a.record == resource), None)
                if expected is None or _attachment(resource.record_id, self.owner, self.thread_id) != expected:
                    raise ResourceIdentityError("Attachment identity changed")
                expected.file.validate()
            elif resource.namespace == "vault":
                if _vault_resource(self.owner, self.thread_id, resource.record_id) != resource:
                    raise ResourceIdentityError("Vault identity changed")
            elif resource.namespace == "memory":
                if _memory_record(resource.record_id, self.owner, self.thread_id) != resource:
                    raise ResourceIdentityError("Memory identity changed")
            elif _record(resource.namespace, self.owner, self.thread_id,
                         _row(resource.namespace, resource.record_id, self.owner)) != resource:
                raise ResourceIdentityError("Owned record identity changed")


def needs_owned_binding(operation):
    if operation.tool == "app_api":
        # The generic internal-token bridge must not bypass migrated owner
        # namespaces. Dedicated tools carry their typed record operations.
        from urllib.parse import unquote, urlsplit
        import posixpath
        args = _args(operation.input)
        path = args.get("path", "")
        if not isinstance(path, str):
            raise ResourceIdentityError("Malformed internal resource selector")
        for _ in range(4):
            decoded = unquote(path)
            if decoded == path:
                break
            path = decoded
        if "%" in path or "\\" in path:
            raise ResourceIdentityError("Unresolved internal resource selector")
        path = posixpath.normpath(urlsplit(path).path)
        private = {"document", "documents", "session", "sessions", "history", "chat", "chats",
                   "notes", "memory", "vault", "upload", "uploads", "attachments",
                   "shell", "model", "cookbook"}
        segments = path.strip("/").split("/")
        if len(segments) >= 3 and segments[:3] == ["api", "codex", "cookbook"]:
            raise ResourceIdentityError("Cookbook wrappers require a dedicated resource-bound tool")
        if len(segments) >= 2 and segments[0] == "api" and segments[1].casefold() in private:
            raise ResourceIdentityError("Owned records require a dedicated resource-bound tool")
        return False
    if operation.tool not in OWNED_TOOL_NAMESPACES:
        return False
    if operation.tool in {"extract_text", "inspect_media", "transcribe_media"}:
        return "odysseus://attachment/" in operation.input
    return True


def resolve_owned_operation(operation, *, owner, thread_id, request_id="", document_id=None):
    if not owner or not thread_id:
        raise ResourceIdentityError("Owned operations require an owner and invocation thread")
    if document_id is not None and (not isinstance(document_id, str) or not document_id.strip()):
        raise ResourceIdentityError("Malformed server document selector")
    namespace = OWNED_TOOL_NAMESPACES[operation.tool]
    args = _args(operation.input) if operation.tool not in {"create_document", "edit_document", "update_document", "suggest_document", "send_to_session", "create_session", "list_sessions", "search_chats", "manage_session", "manage_memory"} else {}
    execution_input = operation.input
    resources = []
    attachments = []
    doc_id = ""
    doc_version = None
    doc_digest = ""
    collection = lambda: OwnedResource(namespace, owner, thread_id, namespace, "*")
    if namespace == "documents":
        action = str(args.get("action") or "list").strip().lower()
        if operation.tool == "create_document" or (operation.tool == "manage_documents" and action in {"list", "search", "find", "tidy"}):
            resources.append(collection())
        else:
            identifier = _selector(args, ("document_id", "id", "uid")) or document_id or ""
            if identifier in {"active", "current"}:
                if not document_id or document_id in {"active", "current", "latest"}:
                    raise ResourceIdentityError("Active document selector is unresolved")
                identifier = document_id
            if not identifier and operation.tool == "manage_documents" and action != "delete":
                raise ResourceIdentityError("Document selector is required")
            if not identifier or identifier == "latest":
                from core.database import SessionLocal, Document
                db = SessionLocal()
                try:
                    row = db.query(Document).filter(Document.owner == owner, Document.is_active == True).order_by(Document.updated_at.desc(), Document.id).first()
                    identifier = row.id if row is not None else ""
                finally:
                    db.close()
            if not identifier:
                raise ResourceIdentityError("Document selector is unresolved")
            row = _row(namespace, identifier, owner)
            resources.append(_record(namespace, owner, thread_id, row))
            doc_id, doc_version = row.id, row.version_count
            from src.tool_approvals import document_content_digest
            doc_digest = document_content_digest(row.current_content)
            if operation.tool == "manage_documents":
                for key in ("id", "uid"):
                    args.pop(key, None)
                args["document_id"] = doc_id
                execution_input = json.dumps(args, sort_keys=True)
    elif namespace == "threads":
        if operation.tool in {"list_sessions", "search_chats", "create_session"}:
            resources.append(collection())
        else:
            if operation.tool == "send_to_session":
                identifier, _, message = operation.input.partition("\n")
                identifier = identifier.strip()
            else:
                if operation.input.lstrip().startswith("{"):
                    args = _args(operation.input)
                else:
                    lines = operation.input.strip().split("\n", 2)
                    args = {"action": lines[0], "session_id": lines[1] if len(lines) > 1 else ""}
                    if len(lines) > 2:
                        args["value"] = lines[2]
                if args.get("action") == "list":
                    resources.append(collection())
                identifier = _selector(args, ("session_id", "session", "id"))
            if not resources:
                identifier = thread_id if identifier == "current" else identifier
                row = _row(namespace, identifier, owner)
                resources.append(_record(namespace, owner, thread_id, row))
                if operation.tool == "send_to_session":
                    execution_input = row.id + "\n" + message
                else:
                    args.pop("id", None)
                    args.pop("session", None)
                    args["session_id"] = row.id
                    execution_input = json.dumps(args, sort_keys=True)
    elif namespace == "notes":
        action = str(args.get("action") or "").strip().lower().replace("-", "_")
        if action in {"list", "search", "find", "add", "create", "new", "save", "remind"}:
            resources.append(collection())
        else:
            identifier = _selector(args, ("id", "note_id", "noteId"))
            from core.database import SessionLocal, Note
            db = SessionLocal()
            try:
                q = db.query(Note).filter(Note.owner == owner)
                if identifier:
                    rows = q.filter(Note.id.startswith(identifier, autoescape=True)).limit(2).all()
                else:
                    title = _selector(args, ("title", "query", "text"))
                    rows = q.filter(Note.title == title).limit(2).all() if title else []
                if len(rows) != 1:
                    raise ResourceIdentityError("Note selector is missing or ambiguous")
                identifier = rows[0].id
            finally:
                db.close()
            row = _row(namespace, identifier, owner)
            resources.append(_record(namespace, owner, thread_id, row))
            args.pop("note_id", None)
            args.pop("noteId", None)
            args["id"] = identifier
            execution_input = json.dumps(args, sort_keys=True)
    elif namespace == "attachments":
        selector = args.get("path")
        match = re.fullmatch(r"odysseus://attachment/([A-Za-z0-9_-]+(?:\.[A-Za-z0-9]+)?)", selector or "")
        if match is None:
            raise ResourceIdentityError("Malformed attachment selector")
        attachment = _attachment(match[1], owner, thread_id)
        resources.append(attachment.record)
        attachments.append(attachment)
    elif namespace == "memory":
        from src.ai_interaction import _manage_memory_lines
        lines = _manage_memory_lines(operation.input)
        if not lines:
            raise ResourceIdentityError("Memory action is unresolved")
        action = lines[0].strip().lower()
        if action in {"list", "search", "add"}:
            resources.append(collection())
        elif action in {"edit", "delete"} and len(lines) >= 2:
            resource = _memory_record(lines[1].strip(), owner, thread_id, prefix=True)
            resources.append(resource)
            lines[1] = resource.record_id
            execution_input = "\n".join(lines)
        else:
            raise ResourceIdentityError("Memory operation is unresolved")
    elif namespace == "vault":
        identifier = "*"
        if operation.tool == "vault_get":
            identifier = _vault_selector(owner, _selector(args, ("item_id",)))
            args["item_id"] = identifier
            execution_input = json.dumps(args, sort_keys=True)
        resources.append(_vault_resource(owner, thread_id, identifier))
    bound = BoundOwnedOperation(operation, execution_input, request_id, owner, thread_id,
                                tuple(resources), tuple(attachments), doc_id, doc_version, doc_digest)
    bound.validate()
    return bound


def admit_owned_operation(authority, operation, *, document_id=None, approved=None, exact_admission=False):
    bound = (approved if approved is not None else resolve_owned_operation(operation, owner=authority.owner,
             thread_id=authority.session_id, request_id=authority.request_id, document_id=document_id))
    if (not isinstance(bound, BoundOwnedOperation) or bound.operation != operation
            or (bound.owner, bound.thread_id) != (authority.owner, authority.session_id)
            or (bound.request_id and bound.request_id != authority.request_id)):
        raise ResourceIdentityError("Owned operation approval binding changed")
    if not all(any(scope.permits(r) for scope in authority.owned_scopes) for r in bound.resources):
        if not (approved is not None and exact_admission and not authority.inherited and not authority.owned_scopes):
            raise ResourceIdentityError("Owned resource exceeds parent/request scope")
    bound.validate()
    return bound


_ACTIVE = ContextVar("owned_resource_operation", default=None)


def active_owned_operation():
    return _ACTIVE.get()


@contextmanager
def bind_owned_operation(operation):
    if operation is not None:
        if not isinstance(operation, BoundOwnedOperation):
            raise TypeError("Owned operation must be server-owned")
        operation.validate()
    token = _ACTIVE.set(operation)
    try:
        yield operation
    finally:
        _ACTIVE.reset(token)


def bound_attachment_path(owner, selector):
    operation = active_owned_operation()
    if operation is None:
        return None
    operation.validate()
    for attachment in operation.attachments:
        if owner == operation.owner and selector == "odysseus://attachment/" + attachment.record.record_id:
            return attachment.file.path
    raise ResourceIdentityError("Attachment is not declared by this operation")
