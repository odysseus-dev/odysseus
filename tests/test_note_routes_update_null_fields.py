from types import SimpleNamespace


class _Query:
    def __init__(self, note):
        self.note = note

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.note


class _Db:
    def __init__(self, note):
        self.note = note
        self.committed = False
        self.closed = False

    def query(self, model):
        return _Query(self.note)

    def commit(self):
        self.committed = True

    def refresh(self, note):
        pass

    def close(self):
        self.closed = True


def _endpoint():
    import routes.note_routes as note_routes

    router = note_routes.setup_note_routes()
    return next(
        route.endpoint for route in router.routes
        if route.path == "/api/notes/{note_id}" and "PUT" in route.methods
    )


def test_update_can_clear_nullable_note_fields(monkeypatch):
    import routes.note_routes as note_routes

    note = SimpleNamespace(
        id="note-1",
        owner=None,
        title="Old title",
        content="Old body",
        items='[{"text":"old","done":false}]',
        note_type="todo",
        color="red",
        label="work",
        pinned=False,
        archived=False,
        due_date="2026-06-17T10:00:00",
        source="user",
        session_id="session-1",
        sort_order=4,
        image_url="/api/upload/image",
        repeat="daily",
        ai_classification=None,
        ai_content_hash=None,
        agent_session_id="agent-1",
        created_at=None,
        updated_at=None,
    )
    db = _Db(note)
    monkeypatch.setattr(note_routes, "SessionLocal", lambda: db)
    monkeypatch.setattr(note_routes, "get_current_user", lambda request: None)
    monkeypatch.setattr(note_routes, "flag_modified", lambda *args, **kwargs: None)

    body = note_routes.NoteUpdate(
        content=None,
        items=None,
        color=None,
        label=None,
        due_date=None,
        image_url=None,
        repeat=None,
        agent_session_id=None,
    )

    result = _endpoint()(SimpleNamespace(), "note-1", body)

    assert db.committed is True
    assert db.closed is True
    assert note.content is None
    assert note.items is None
    assert note.color is None
    assert note.label is None
    assert note.due_date is None
    assert note.image_url is None
    assert note.repeat == "none"
    assert note.agent_session_id is None
    assert result["content"] is None
    assert result["items"] is None
    assert result["label"] is None
