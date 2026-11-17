"""Resolve native filesystem selectors once, after operation admission.

Resolution produces inert bindings; the dispatcher still owns authority,
TurnContract, security and approval gates. No remote filesystem is resolved here.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import json
import os

from src.agent_runtime.authority import ExactOperation
from src.agent_runtime.resources import FilesystemResource, FilesystemRoot
from src.path_confinement import canonical_root, confine


NATIVE_FILESYSTEM_TOOLS = frozenset({
    "read_file", "write_file", "edit_file", "apply_patch", "ls", "glob", "grep",
})


@dataclass(frozen=True)
class ResourceBinding:
    role: str
    resource: FilesystemResource

    def __post_init__(self):
        if self.role not in {"source", "target", "destination", "search_root"} or not isinstance(self.resource, FilesystemResource):
            raise ValueError("Malformed operation resource binding")


@dataclass(frozen=True)
class BoundFilesystemOperation:
    operation: ExactOperation
    execution_input: str
    bindings: tuple[ResourceBinding, ...]
    # Empty only for inert proposal resolution without an originating request.
    request_id: str = ""

    def __post_init__(self):
        if (not isinstance(self.operation, ExactOperation)
                or not isinstance(self.execution_input, str)
                or not isinstance(self.bindings, tuple) or not self.bindings
                or any(not isinstance(b, ResourceBinding) for b in self.bindings)):
            raise ValueError("Malformed resource-bound operation")
        if not isinstance(self.request_id, str) or any(c in self.request_id for c in ("\0", "\n", "\r")):
            raise ValueError("Malformed resource operation request identity")
        if (self.operation.action in {"move", "rename"}
                and (len(self.bindings) != 2 or {b.role for b in self.bindings} != {"source", "destination"}
                     or len({b.resource.path for b in self.bindings}) != 2
                     or next(b for b in self.bindings if b.role == "source").resource.identity is None)):
            raise ValueError("Move/rename must bind distinct source and destination")

    @property
    def write_intent(self):
        """Trusted original intent; execution_input only normalizes the path."""
        if self.operation.tool != "write_file":
            return None
        from src.agent_tools.filesystem_tools import _parse_write_intent
        return _parse_write_intent(self.operation.input)

    def validate(self):
        for binding in self.bindings:
            binding.resource.validate()

    def to_dict(self):
        return {"request_id": self.request_id, "tool": self.operation.transport_tool, "input": self.operation.input,
                "execution_input": self.execution_input,
                "bindings": [{"role": b.role, "resource": b.resource.to_dict()} for b in self.bindings]}

    def resolve_path(self, selector, *, search=False):
        """Consume declared canonical targets; permit bounded search descendants."""
        if not isinstance(selector, str):
            raise ValueError("Resource selector must be a string")
        value = selector.strip()
        for binding in self.bindings:
            resource = binding.resource
            if value == resource.path or (search and not value and binding.role == "search_root"):
                resource.validate()
                return resource.path
        if not search:
            for binding in self.bindings:
                resource = binding.resource
                if binding.role == "search_root" and resource.identity.kind == "directory":
                    resource.validate()
                    try:
                        path = confine(resource.path, value)
                        return FilesystemResource.resolve(resource.root, path).path
                    except (ValueError, OSError, RuntimeError):
                        continue
        raise ValueError("Path is not declared by the resource-bound operation")


def _resolve(roots, selector, *, workspace, allow_missing):
    if not isinstance(selector, str) or not selector.strip():
        raise ValueError("Resource path is required and must be a string")
    value = selector.strip()
    # The virtual alias belongs to the request workspace, even when a child
    # narrows its root to a subdirectory of that workspace.
    if value == "/workspace" or value.startswith("/workspace/"):
        if not workspace:
            raise ValueError("Workspace alias has no server-owned workspace")
        base = canonical_root(workspace)
        value = base if value == "/workspace" else os.path.join(base, value[len("/workspace/"):])
    elif not os.path.isabs(os.path.expanduser(value)):
        if workspace:
            value = os.path.join(canonical_root(workspace), value)
        elif len(roots) == 1:
            value = os.path.join(roots[0].path, value)
        else:
            raise ValueError("Relative resource path has no unambiguous server root")
    for root in roots:
        try:
            return FilesystemResource.resolve(root, value, allow_missing=allow_missing)
        except (ValueError, OSError, RuntimeError):
            continue
    boundary = "the workspace" if workspace else "the sealed roots"
    raise ValueError(f"Resource path is outside {boundary}, sensitive, missing or changed")


def resolve_filesystem_operation(operation, *, roots, workspace="", request_id=""):
    """Server adapter. This does not grant the operation or authorize its roots."""
    if not isinstance(operation, ExactOperation) or operation.tool not in NATIVE_FILESYSTEM_TOOLS:
        raise ValueError("Operation has no native filesystem adapter")
    if (not isinstance(roots, tuple) or not roots
            or any(not isinstance(r, FilesystemRoot) for r in roots)):
        raise ValueError("Native filesystem operation requires a sealed resource root")
    content = operation.input
    if operation.tool == "write_file":
        from src.agent_tools.filesystem_tools import _parse_write_intent
        original_path, _, section, _ = _parse_write_intent(content)
        if not section:
            raise ValueError("write_file: content required; missing content section")
        if original_path.endswith(("/", "\\")):
            raise ValueError("write_file: target is a directory")
    args = json.loads(content) if content.lstrip().startswith("{") else None
    if args is not None and not isinstance(args, dict):
        raise ValueError("Filesystem input must be an object")
    bindings = []

    def bind(selector, role, *, missing=False):
        resource = _resolve(roots, selector, workspace=workspace, allow_missing=missing)
        bindings.append(ResourceBinding(role, resource))
        return resource.path

    tool = operation.tool
    if tool == "apply_patch":
        from src.agent_tools.filesystem_tools import _parse_agent_patch
        if args is None:
            patch = content
        else:
            variants = [args[k] for k in ("patch_text", "patchText", "patch") if k in args]
            if not variants or any(not isinstance(p, str) or p != variants[0] for p in variants):
                raise ValueError("Patch requires one unambiguous patch_text")
            patch = variants[0]
        ops = _parse_agent_patch(patch)
        paths = [bind(op["path"], "destination" if op["kind"] == "add" else "target",
                      missing=op["kind"] == "add") for op in ops]
        objects = [b.resource.identity for b in bindings if b.resource.identity is not None]
        if len(set(paths)) != len(paths) or len(set(objects)) != len(objects):
            raise ValueError("Patch targets resolve to the same resource")
        path_iter = iter(paths)
        lines = patch.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        for i, line in enumerate(lines):
            for marker in ("*** Add File: ", "*** Update File: ", "*** Delete File: "):
                if line.startswith(marker):
                    lines[i] = marker + next(path_iter)
                    break
        execution_input = json.dumps({"patch_text": "\n".join(lines)}, sort_keys=True)
    else:
        search = tool in {"ls", "glob", "grep"}
        if args is None:
            if tool == "write_file":
                path, _, body = content.partition("\n")
                args = {"path": path.strip(), "content": body}
            elif tool == "edit_file":
                raise ValueError("edit_file requires a JSON object")
            elif tool in {"glob", "grep"}:
                args = {"pattern": content.strip()}
            else:
                args = {"path": content.split("\n", 1)[0].strip()}
        selector = args.get("path", "" if search else None)
        if search and selector == "":
            if workspace:
                selector = canonical_root(workspace)
            elif len(roots) == 1:
                selector = roots[0].path
            else:
                raise ValueError("Search root is unresolved")
        args["path"] = bind(selector, "search_root" if search else
                            "source" if tool == "read_file" else "destination" if tool == "write_file" else "target",
                            missing=tool in {"write_file", "read_file"})
        execution_input = json.dumps(args, sort_keys=True, allow_nan=False)
    bound = BoundFilesystemOperation(operation, execution_input, tuple(bindings), request_id)
    bound.validate()
    return bound


_ACTIVE: ContextVar[BoundFilesystemOperation | None] = ContextVar("resource_operation", default=None)


def active_resource_operation():
    return _ACTIVE.get()


@contextmanager
def bind_resource_operation(operation):
    if operation is not None and not isinstance(operation, BoundFilesystemOperation):
        raise TypeError("Resource operation must be server-owned")
    if operation is not None:
        operation.validate()
    token = _ACTIVE.set(operation)
    try:
        yield operation
    finally:
        _ACTIVE.reset(token)
