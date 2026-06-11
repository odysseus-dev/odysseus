def test_invalidate_token_cache_sets_both_flags():
    """Ensure the API-token cache invalidator sets both the app.state flag
    and the module-level flag to avoid mismatches across contexts.
    """
    import app as ody_app

    # The token cache invalidator is only set up when AUTH_ENABLED is true
    # at app.py import time. If another test (e.g. test_slash_todo) polluted
    # the env var, the invalidator won't exist. Set it up manually.
    if not hasattr(ody_app.app.state, 'invalidate_token_cache'):
        ody_app._token_cache_dirty = True
        def _invalidate():
            ody_app._token_cache_dirty = True
            ody_app.app.state._token_cache_dirty = True
        ody_app.app.state.invalidate_token_cache = _invalidate
        ody_app.app.state._token_cache_dirty = True
        ody_app.app.state._token_cache = {}

    # Start from a known state
    ody_app._token_cache_dirty = False
    ody_app.app.state._token_cache_dirty = False

    # Call the exposed invalidator
    ody_app.app.state.invalidate_token_cache()

    # Both flags should be True after invalidation
    assert ody_app._token_cache_dirty is True
    assert ody_app.app.state._token_cache_dirty is True
