// mcp_servers/mod.rs  <- mcp_servers/ (the built-in MCP stdio servers package)
//! The built-in MCP stdio servers — the Rust mirror of the Python
//! `mcp_servers/` package (`email_server.py`, `memory_server.py`,
//! `image_gen_server.py`, `rag_server.py`, and the shared `_common.py`).
//!
//! Each Python file is a standalone `mcp.server.Server` driven by
//! `mcp.server.stdio.stdio_server`. Here they share a hand-rolled stdio
//! JSON-RPC server [`harness`] (the `mcp.server.Server` analogue) that speaks the
//! exact newline-delimited protocol [`crate::src::mcp_manager`] (the client)
//! expects, so the manager can spawn these servers as subprocesses unchanged.
//!
//! The whole module is `web`-gated (it needs tokio async stdin/stdout, and the
//! servers reach into the DB-backed managers / the IMAP/SMTP stack), so the
//! default and `db`-only builds never see it.
//!
//! ## Invocation
//!
//! `main.rs` dispatches `odysseus mcp-server <id>` (id in
//! {`email`, `memory`, `image_gen`, `rag`}) to [`run_server`], which runs that
//! server's stdio JSON-RPC loop to EOF and returns. The built-in registration in
//! [`crate::src::builtin_mcp`] spawns each server via the current executable plus
//! `["mcp-server", <id>]` — the faithful analogue of the Python
//! `sys.executable mcp_servers/<id>_server.py` launch (a stdio command the MCP
//! client spawns; the transport is identical).

pub mod common;
pub mod harness;

pub mod email_server;
pub mod image_gen_server;
pub mod memory_server;
pub mod rag_server;

/// Run the built-in MCP stdio server identified by `id`.
///
/// This is the dispatcher `main.rs` calls for `odysseus mcp-server <id>`. It
/// matches `id` against the four built-in server ids — the same ids
/// [`crate::src::builtin_mcp::register_builtin_servers`] launches — constructs
/// that server's [`harness::ToolDef`] list + [`harness::CallToolHandler`], and
/// runs the shared [`harness::serve_stdio`] loop (the `async with stdio_server()
/// ... await server.run(...)` analogue) until the client closes stdin.
///
/// Each Python server is a standalone `Server("<name>")`; the `<name>` passed to
/// the harness here matches the Python `Server(...)` constructor argument
/// (`memory` / `rag` / `image_gen` / `email`), so `serverInfo.name` is identical
/// on the wire.
///
/// Returns the dispatched server's stdio loop result. An unknown `id` yields an
/// [`std::io::Error`] of kind [`std::io::ErrorKind::InvalidInput`] so the caller
/// can surface a usage message (the caller — `main.rs` — turns this into a
/// process exit code).
pub async fn run_server(id: &str) -> std::io::Result<()> {
    match id {
        "memory" => {
            harness::serve_stdio("memory", memory_server::tools(), memory_server::handler()).await
        }
        "rag" => harness::serve_stdio("rag", rag_server::tools(), rag_server::handler()).await,
        "image_gen" => {
            harness::serve_stdio(
                "image_gen",
                image_gen_server::tools(),
                image_gen_server::handler(),
            )
            .await
        }
        "email" => {
            harness::serve_stdio("email", email_server::tools(), email_server::handler()).await
        }
        other => Err(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            format!("unknown MCP server id: {other}"),
        )),
    }
}

/// The four built-in server ids `run_server` knows about, in the order
/// [`crate::src::builtin_mcp::BUILTIN_SERVERS`] registers them. Used by `main.rs`
/// to validate the `mcp-server <id>` subcommand and print a usage line.
pub const SERVER_IDS: [&str; 4] = ["image_gen", "memory", "rag", "email"];

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn server_ids_match_builtin_table() {
        // The dispatcher must understand every id `builtin_mcp` launches.
        assert_eq!(SERVER_IDS.len(), 4);
        for id in SERVER_IDS {
            assert!(matches!(id, "image_gen" | "memory" | "rag" | "email"));
        }
    }

    #[tokio::test]
    async fn run_server_rejects_unknown_id() {
        let err = run_server("does_not_exist").await.unwrap_err();
        assert_eq!(err.kind(), std::io::ErrorKind::InvalidInput);
    }
}
