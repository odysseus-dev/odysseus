from routes.document_helpers import _resolve_user_upload_path


class _FakeHandler:
    upload_dir = "/tmp/uploads"

    def __init__(self, resolved):
        self._resolved = resolved

    def resolve_upload(self, upload_id, owner=None, auth_manager=None):
        return self._resolved


def test_resolve_user_upload_path_handles_non_dict_resolution():
    # resolve_upload normally returns a dict or None; a corrupt store could
    # hand back a list/str, and the old resolved.get(...) then crashed.
    assert _resolve_user_upload_path(_FakeHandler(["not", "a", "dict"]), "id1", None) is None
    assert _resolve_user_upload_path(_FakeHandler("oops"), "id1", None) is None


def test_resolve_user_upload_path_tolerates_dict_without_path():
    # a well-formed dict still flows through and returns None when no path
    assert _resolve_user_upload_path(_FakeHandler({"other": 1}), "id1", None) is None
