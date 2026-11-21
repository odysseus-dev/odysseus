"""GET /api/notes answers a repeated read with 304 when nothing changed.

The Notes panel re-reads the list every 20 s. The response carries an ETag
over its exact body, so an unchanged list costs a 304 with no body, and any
change a reader would see (an edit, a deletion, a share added or revoked, a
collaborator's edit) gives a new tag and the full list.
"""
import httpx

from tests.test_notes_sharing import _PEER, _Identity, env  # noqa: F401  (fixture)


async def _list(env, user="alice", etag=None):
    headers = {"x-test-user": user}
    if etag:
        headers["If-None-Match"] = etag
    transport = httpx.ASGITransport(app=_Identity(env.app), client=_PEER)
    async with httpx.AsyncClient(transport=transport, base_url="http://notes.test") as c:
        return await c.get("/api/notes", headers=headers)


async def test_unchanged_list_is_a_304_with_no_body(env):
    env.seed("n1", content="- [ ] one\n- [x] two")
    first = await _list(env)
    assert first.status_code == 200
    tag = first.headers["etag"]
    assert tag.startswith('"') and tag.endswith('"')
    again = await _list(env, etag=tag)
    assert again.status_code == 304
    assert again.content == b""
    assert again.headers["etag"] == tag
    # A weak form of the same tag (some proxies rewrite it) still matches.
    weak = await _list(env, etag="W/" + tag)
    assert weak.status_code == 304


async def test_an_edit_or_a_deletion_changes_the_tag(env):
    env.seed("n1", content="hello")
    env.seed("n2", content="world")
    tag = (await _list(env)).headers["etag"]
    edit = await env.call("PUT", "/api/notes/n1", json={"body_revision": env.revision("n1"), "content": "hello again"})
    assert edit.status_code == 200
    after_edit = await _list(env, etag=tag)
    assert after_edit.status_code == 200
    assert [n["content"] for n in after_edit.json()["notes"] if n["id"] == "n1"] == ["hello again"]
    tag = after_edit.headers["etag"]
    assert (await env.call("DELETE", "/api/notes/n2")).status_code == 200
    after_delete = await _list(env, etag=tag)
    assert after_delete.status_code == 200
    assert [n["id"] for n in after_delete.json()["notes"]] == ["n1"]


async def test_a_reader_sees_a_new_tag_for_a_share_and_an_edit_by_the_owner(env):
    env.seed("mine", owner="bob", content="bob's own")
    tag = (await _list(env, user="bob")).headers["etag"]
    # alice shares her note with bob: bob's list changes, his tag with it.
    env.seed("shared", owner="alice", content="v1", shares=[("bob", "edit")])
    shared = await _list(env, user="bob", etag=tag)
    assert shared.status_code == 200
    assert "shared" in [n["id"] for n in shared.json()["notes"]]
    tag = shared.headers["etag"]
    assert (await _list(env, user="bob", etag=tag)).status_code == 304
    # The owner edits it: the reader's copy changed.
    assert (await env.call("PUT", "/api/notes/shared", json={"body_revision": env.revision("shared"), "content": "v2"})).status_code == 200
    edited = await _list(env, user="bob", etag=tag)
    assert edited.status_code == 200
    tag = edited.headers["etag"]
    # The owner revokes the share: the note leaves the reader's list.
    assert (await env.call("DELETE", "/api/notes/shared/share/bob")).status_code == 200
    revoked = await _list(env, user="bob", etag=tag)
    assert revoked.status_code == 200
    assert [n["id"] for n in revoked.json()["notes"]] == ["mine"]


async def test_tags_are_per_reader(env):
    env.seed("shared", owner="alice", content="same note", shares=[("bob", "view")])
    alice = await _list(env, user="alice")
    bob = await _list(env, user="bob")
    # Same note, different payloads (roster, permission, me): different tags.
    assert alice.headers["etag"] != bob.headers["etag"]
    assert (await _list(env, user="bob", etag=alice.headers["etag"])).status_code == 200


async def test_list_body_is_unchanged_json(env):
    env.seed("n1", content="café naïve and \"quotes\"", label="x y")
    res = await _list(env)
    assert res.headers["content-type"].startswith("application/json")
    body = res.json()
    assert body["me"] == "alice"
    assert body["notes"][0]["content"] == "café naïve and \"quotes\""
    # Non-ASCII is sent as UTF-8, the way FastAPI's JSONResponse sends it.
    assert "naïve".encode() in res.content
