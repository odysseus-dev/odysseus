import asyncio
from pathlib import Path
import subprocess
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException


@pytest.fixture
def conversion(monkeypatch, tmp_path):
    import routes.document.document_routes as routes
    import src.auth_helpers as auth
    monkeypatch.setattr(auth, "require_privilege", lambda *a: "alice")
    doc = SimpleNamespace(owner="alice", session_id=None, current_content='<!-- docx_source upload_id="' + 'a'*32 + '" -->')
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = doc
    monkeypatch.setattr(routes, "SessionLocal", lambda: db)
    source = tmp_path / "original.docx"
    source.write_bytes(b"original-file")
    handler = MagicMock()
    handler.upload_dir = tmp_path
    handler.resolve_upload.return_value = {"path": str(source)}
    monkeypatch.setattr("shutil.which", lambda executable: "/fake/soffice")
    router = routes.setup_document_routes(MagicMock(), handler)
    endpoint = next(r.endpoint for r in router.routes if r.path.endswith("/convert-original/{target}"))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)))
    return endpoint, request, doc, handler


@pytest.mark.asyncio
async def test_conversion_does_not_block_loop_and_cleans_output(monkeypatch, conversion):
    endpoint, request, _, _ = conversion
    entered, release = threading.Event(), threading.Event()
    outputs = []
    loop_thread = threading.get_ident()

    def run(command, **kwargs):
        assert threading.get_ident() != loop_thread
        output_dir = Path(command[command.index("--outdir") + 1])
        outputs.append(output_dir)
        assert any(arg.startswith("-env:UserInstallation=") for arg in command)
        assert Path(command[-1]).read_bytes() == b"original-file"
        entered.set()
        assert release.wait(3)
        (output_dir / "original.pdf").write_bytes(b"%PDF-test")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", run)
    task = asyncio.create_task(endpoint("doc", "pdf", request))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        # This coroutine executes while conversion is still blocked in a worker.
        assert not task.done()
    finally:
        release.set()
    response = await task
    assert response.body == b"%PDF-test"
    assert all(not path.exists() for path in outputs)


@pytest.mark.asyncio
async def test_timeout_returns_504_and_cleans_worker_directory(monkeypatch, conversion):
    endpoint, request, _, _ = conversion
    outputs = []
    def run(command, **kwargs):
        outputs.append(Path(command[command.index("--outdir") + 1]))
        raise subprocess.TimeoutExpired(command, 120)
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(HTTPException) as error:
        await endpoint("doc", "pdf", request)
    assert error.value.status_code == 504
    assert all(not path.exists() for path in outputs)


@pytest.mark.asyncio
async def test_form_pdf_with_fields_reaches_original_conversion(monkeypatch, conversion, tmp_path):
    endpoint, request, doc, handler = conversion
    doc.current_content = '<!-- pdf_form_source upload_id="' + 'a'*32 + '" fields="3" -->'
    source = tmp_path / "original.pdf"
    source.write_bytes(b"%PDF-original")
    handler.resolve_upload.return_value = {"path": str(source)}
    def run(command, **kwargs):
        assert command[-1] == str(source)
        output = Path(command[command.index("--outdir") + 1]) / "original.docx"
        output.write_bytes(b"PK-converted")
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(subprocess, "run", run)
    assert (await endpoint("doc", "docx", request)).body == b"PK-converted"


@pytest.mark.asyncio
async def test_docx_preview_runs_off_loop_and_checks_owner(monkeypatch, conversion):
    import sys
    import routes.document.document_routes as routes
    _, request, doc, handler = conversion
    main_thread = threading.get_ident()
    rendered = []
    def render(path):
        assert threading.get_ident() != main_thread
        rendered.append(path)
        return SimpleNamespace(value="<p>Preview</p>", messages=[])
    monkeypatch.setitem(sys.modules, "mammoth", SimpleNamespace(convert_to_html=render))
    router = routes.setup_document_routes(MagicMock(), handler)
    endpoint = next(r.endpoint for r in router.routes if r.path.endswith("/render-docx"))
    assert (await endpoint("doc", request))["html"] == "<p>Preview</p>"
    doc.owner = "bob"
    with pytest.raises(HTTPException) as error:
        await endpoint("doc", request)
    assert error.value.status_code in {403, 404}
    assert len(rendered) == 1


def test_imported_office_document_is_owned_at_first_commit(monkeypatch, tmp_path):
    import importlib
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    import src.database as database
    from src.office_doc import create_office_document
    engine = create_engine(f"sqlite:///{tmp_path / 'documents.db'}")
    database.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", factory)
    document_tools = importlib.import_module("src.agent_tools.document_tools")
    monkeypatch.setattr(document_tools, "set_active_document", lambda doc_id: None)
    owners_at_commit = []
    def inspect_new_rows(session):
        owners_at_commit.extend(row.owner for row in session.new if isinstance(row, database.Document))
    event.listen(factory, "before_commit", inspect_new_rows)
    try:
        doc_id = create_office_document(None, "upload", "Standalone", "Content", owner="alice")
        assert doc_id
        assert owners_at_commit == ["alice"]
        with factory() as db:
            assert db.get(database.Document, doc_id).owner == "alice"
    finally:
        engine.dispose()
