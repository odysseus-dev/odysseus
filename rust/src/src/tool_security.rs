// src/tool_security.rs  <- src/tool_security.py
//! Server-side tool safety policy.

use once_cell::sync::Lazy;
use std::collections::HashSet;

// Tools regular/public users must not execute directly. These either expose
// server/runtime access, sensitive user data, external messaging, persistent
// state changes, or generic loopback/integration surfaces.
pub static NON_ADMIN_BLOCKED_TOOLS: Lazy<HashSet<&'static str>> = Lazy::new(|| {
    HashSet::from([
        "bash",
        "python",
        "read_file",
        "write_file",
        "search_chats",
        "manage_memory",
        "manage_skills",
        "manage_tasks",
        "manage_endpoints",
        "manage_mcp",
        "manage_webhooks",
        "manage_tokens",
        "manage_documents",
        "manage_settings",
        "api_call",
        "app_api",
        "send_email",
        "reply_to_email",
        "list_emails",
        "read_email",
        "resolve_contact",
        "manage_contact",
        "manage_calendar",
        "vault_search",
        "vault_get",
        "vault_unlock",
        "download_model",
        "serve_model",
        "serve_preset",
        "stop_served_model",
        "cancel_download",
        "adopt_served_model",
    ])
});

/// Return True when a non-admin/public user must not execute this tool.
pub fn is_public_blocked_tool(tool_name: Option<&str>) -> bool {
    let tool_name = match tool_name {
        Some(t) if !t.is_empty() => t,
        _ => return false,
    };
    NON_ADMIN_BLOCKED_TOOLS.contains(tool_name) || tool_name.starts_with("mcp__")
}

/// Return True for admins, or when auth is not configured yet.
pub fn owner_is_admin_or_single_user(owner: Option<&str>) -> bool {
    // The Python wraps the AuthManager lookup in try/except so that any failure
    // (import error, corrupt config, …) is logged via `logger.warning(...)` and
    // treated as "not admin". The Rust `AuthManager::new()` is infallible and the
    // body below cannot raise, so the `except` branch is unreachable here.
    let auth = crate::core::auth::AuthManager::new();
    if !auth.is_configured() {
        return true;
    }
    match owner {
        Some(o) if !o.is_empty() => auth.is_admin(o),
        _ => false,
    }
}

/// Tools to hide/disable for this owner under public-user policy.
pub fn blocked_tools_for_owner(owner: Option<&str>) -> HashSet<String> {
    if owner_is_admin_or_single_user(owner) {
        return HashSet::new();
    }
    NON_ADMIN_BLOCKED_TOOLS
        .iter()
        .map(|s| s.to_string())
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn serve_preset_is_blocked() {
        // A public/non-admin user must not be able to launch a model server.
        assert!(NON_ADMIN_BLOCKED_TOOLS.contains("serve_preset"));
        assert!(is_public_blocked_tool(Some("serve_preset")));
    }

    #[test]
    fn model_server_tools_are_all_blocked() {
        for tool in [
            "download_model",
            "serve_model",
            "serve_preset",
            "stop_served_model",
            "cancel_download",
            "adopt_served_model",
        ] {
            assert!(
                NON_ADMIN_BLOCKED_TOOLS.contains(tool),
                "expected {tool} to be in the non-admin blocklist",
            );
        }
    }
}
