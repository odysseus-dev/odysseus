"""Explicit trusted producer fixtures; no production authority fallback."""
from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
from uuid import uuid4

from src.agent_runtime.authority import ExactOperation, OperationGrant, RequestAuthority, bind_request_authority
from src.agent_runtime.resources import BackgroundJobResource, NativeBackendResource, ProcessResource
from src.agent_runtime.process_resources import bind_process_operation, resolve_process_operation, publish_launch
from src.process_lifecycle import ProcessIdentity


@contextmanager
def launch_authority(content, workspace, *, tool="bash", owner="", session_id="chat", authority=None):
    authority = authority or RequestAuthority("producer-test", owner, session_id, str(workspace), (OperationGrant(tool),))
    bound = resolve_process_operation(authority, ExactOperation.normalize(tool, content), NativeBackendResource(tool))
    with bind_request_authority(authority), bind_process_operation(bound):
        yield authority, bound


def launch(command, session_id="chat", *, cwd, **kwargs):
    from src import bg_jobs
    with launch_authority(command, cwd, session_id=session_id):
        return bg_jobs.launch(command, session_id, cwd=cwd, **kwargs)


def identity(job_id):
    from src import bg_jobs
    from src.agent_runtime.process_resources import job_from_record
    return job_from_record(bg_jobs.peek(job_id))


def get(job_id):
    from src import bg_jobs
    return bg_jobs.get(job_id, expected=identity(job_id))


def kill(job_id):
    from src import bg_jobs
    return bg_jobs.kill(job_id, expected=identity(job_id))


def seed_linkage(record, workspace, *, owner="", request_id="producer-test"):
    """A fake server spawn record, with an explicit fake lifecycle observation."""
    from src import bg_jobs, containment
    from src.agent_runtime.authority import save_background_authority
    from src.agent_runtime.process_resources import resolve_process_operation
    authority = RequestAuthority(request_id, owner, record["session_id"], str(workspace), (OperationGrant("bash"),))
    bound = resolve_process_operation(authority, ExactOperation.normalize("bash", record["command"]), NativeBackendResource("bash"))
    receipt = uuid4().hex
    record.update(containment_id=receipt, start_token="test-boot:start", pgid=record["pid"])
    process = ProcessResource("native:bg_jobs", owner, request_id, record["session_id"],
        ProcessIdentity(record["pid"], record["start_token"], record["pgid"]), "supervisor", record["id"], receipt)
    resource = BackgroundJobResource("native:bg_jobs", record["id"], bound.launch.generation,
        owner, request_id, record["session_id"], receipt, (process,))
    record.update(resource_identity=resource.to_dict(), launch_resource=bound.launch.to_dict())
    from core.atomic_io import atomic_write_json
    receipts = containment._load_records()
    receipts[receipt] = {"id": receipt, "launch_generation": resource.generation,
        "owner": "bg:" + resource.thread_id, "supervisor_pid": process.identity.pid,
        "supervisor_token": process.identity.start_token, "mechanism": "process_group"}
    atomic_write_json(containment._store_path(), receipts)
    publish_launch(bound.launch, authority, receipt, job=resource, processes=(process,))
    save_background_authority(record["id"], authority, resource=resource)
    return resource


def authorized_handler(handler, workspace):
    async def execute(content, ctx):
        from src.agent_runtime.process_resources import active_process_operation
        from src.agent_runtime.authority import active_request_authority
        if active_process_operation() is not None or active_request_authority() is not None:
            return await handler(content, ctx)
        tool = "python" if handler.__qualname__.startswith("PythonTool") else "bash"
        from src.agent_runtime.resources import FilesystemRoot
        from src.agent_runtime.process_resources import seal_launch_scope
        owner = str(ctx.get("owner") or "").casefold()
        authority = RequestAuthority("producer-test", owner, str(ctx.get("session_id") or ""), str(workspace), (OperationGrant(tool),))
        authority = replace(authority, launch_scopes=(seal_launch_scope(NativeBackendResource(tool),
            FilesystemRoot.seal(workspace, owner=owner), env=ctx.get("subproc_env")),))
        with launch_authority(content, workspace, tool=tool, authority=authority):
            return await handler(content, ctx)
    return execute


def install_native_authority(monkeypatch, workspace):
    from src.agent_tools import subprocess_tools
    from src import tool_execution
    from src.constants import DATA_DIR
    for cls in (subprocess_tools.BashTool, subprocess_tools.PythonTool):
        original = cls.execute
        async def execute(self, content, ctx, _original=original):
            selected = Path(tool_execution.agent_cwd())
            if selected == Path(DATA_DIR):
                selected = Path(workspace)
            return await authorized_handler(_original.__get__(self), selected)(content, ctx)
        monkeypatch.setattr(cls, "execute", execute)
