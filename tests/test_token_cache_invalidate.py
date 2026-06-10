def test_invalidate_token_cache_sets_both_flags():
    """Ensure the API-token cache invalidator sets both the app.state flag
    and the module-level flag to avoid mismatches across contexts.
    """
    import app as ody_app

    # Start from a known state
    ody_app._token_cache_dirty = False
    ody_app.app.state._token_cache_dirty = False

    # Call the exposed invalidator
    ody_app.app.state.invalidate_token_cache()

    # Both flags should be True after invalidation
    assert ody_app._token_cache_dirty is True
    assert ody_app.app.state._token_cache_dirty is True
