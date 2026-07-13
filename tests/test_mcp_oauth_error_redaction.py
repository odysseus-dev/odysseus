from src.mcp_manager import sanitize_mcp_error


def test_oauth_error_redacts_query_and_body_credentials():
    error = RuntimeError(
        "OAuth failed: "
        "https://example.invalid/callback?"
        "client_secret=query-secret&code=auth-code "
        "client_secret=body-secret "
        "access_token=access-secret "
        "refresh_token=refresh-secret"
    )

    sanitized = sanitize_mcp_error(error)

    for secret in (
        "query-secret",
        "auth-code",
        "body-secret",
        "access-secret",
        "refresh-secret",
    ):
        assert secret not in sanitized

    assert "client_secret" in sanitized
    assert "access_token" in sanitized
    assert "refresh_token" in sanitized
