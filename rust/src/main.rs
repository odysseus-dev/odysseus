// main.rs  <- app.py / setup.py (the binary entry point)
//! The Odysseus demo server binary. The entire crate compiles
//! unconditionally; `web::run()` is the translation of `app.py`'s
//! module-level startup + `uvicorn.run`.
//!
//! ## `mcp-server <id>` subcommand
//!
//! Before starting the web server, `main` checks for the `mcp-server <id>`
//! subcommand (id in {`email`, `memory`, `image_gen`, `rag`}). When present it
//! runs that built-in MCP stdio server's JSON-RPC loop to EOF and exits — the
//! faithful analogue of the Python `sys.executable mcp_servers/<id>_server.py`
//! launch. The built-in registration in `src::builtin_mcp` spawns the current
//! executable with these args, so a single binary is both the web server and the
//! four stdio MCP servers. Any other argv (including no subcommand) runs the
//! existing `web::run()` path unchanged.
//!
//! ## `setup` subcommand
//!
//! `odysseus setup` is the translation of `setup.py`'s `main()` — the
//! first-time setup script. It creates the data directories, copies
//! `.env.example` to `.env`, checks for `tmux`, initializes the database
//! (`core::database::init_db`), and writes an initial admin user to
//! `<DATA_DIR>/auth.json` (skipping anything that already exists). It runs once
//! and exits (synchronously), mirroring `python setup.py`.
//!
//! ## `chroma [start|stop|status]` subcommand
//!
//! `odysseus chroma` manages a standalone REAL ChromaDB server (`crate::src::chroma_launch`).
//! It has TWO launcher backends, picked by `ODYSSEUS_CHROMA_LAUNCHER`
//! ({`auto`, `local`, `docker`}, default `auto`):
//!   * LOCAL (no docker) — the pip-installed full `chromadb` package's own server,
//!     launched via `python -c "from chromadb.cli.cli import app; app()" run --path
//!     <DATA_DIR>/chroma --host localhost --port <CHROMADB_PORT>` and DETACHED so it
//!     survives this subcommand exiting (PID file + log under `<DATA_DIR>/chroma`).
//!   * DOCKER — replicates `docker-compose.yml`'s `chromadb` service via a host
//!     `docker run -d` of `chromadb/chroma:latest`.
//! `auto` prefers LOCAL when a chromadb-capable python is found, else DOCKER, else an
//! honest error naming both. `start` (the default) reuses a server already answering
//! the heartbeat (local) / a running `odysseus-chromadb` (docker), else starts one,
//! polls `/api/v2/heartbeat` until ready, prints the reachable
//! `http://localhost:<CHROMADB_PORT>`, and exits 0 leaving the server UP for the app
//! to connect to. `stop`/`status` handle BOTH backends (a local `launcher.pid` AND
//! the docker container). It runs synchronously and exits; when neither launcher is
//! available (or a launch fails) it exits non-zero with an honest stderr message.
//! This is the HTTP-backend counterpart of the default SQLite vector store (selected
//! via `ODYSSEUS_VECTOR_BACKEND=chroma` or explicit `CHROMADB_HOST`/`CHROMADB_PORT`).
//!
//! ## `serve` (the default) + `--host`/`--port`
//!
//! `odysseus`, `odysseus serve`, and `odysseus serve --host H --port P` all run
//! the web server. The `--host`/`--port` flags mirror uvicorn's, defaulting to
//! `0.0.0.0:7000` — the Rust equivalent of `uvicorn app:app --host 0.0.0.0 --port
//! 7000`. They set `ODYSSEUS_BIND` (which is otherwise honored as-is). `--help`
//! prints the full usage.

// Cosmetic markdown-indent lints on a doc-heavy 1:1 Python port: the binary's
// `//!` docstrings mirror the Python module docs verbatim, so reflowing the list
// items would only diverge the Rust docs from the Python source for zero gain.
// (The library crate carries the same two allows in `lib.rs`.)
#![allow(clippy::doc_overindented_list_items)]
#![allow(clippy::doc_lazy_continuation)]

#[tokio::main]
async fn main() {
    let args: Vec<String> = std::env::args().collect();
    // `odysseus mcp-server <id>` — run a built-in MCP stdio server and exit.
    // stdout is the JSON-RPC channel, so nothing else may be printed to it.
    if args.get(1).map(String::as_str) == Some("mcp-server") {
        match args.get(2).map(String::as_str) {
            Some(id) if odysseus::mcp_servers::SERVER_IDS.contains(&id) => {
                match odysseus::mcp_servers::run_server(id).await {
                    Ok(()) => std::process::exit(0),
                    Err(e) => {
                        // Transport errors go to stderr (never stdout); a clean EOF
                        // is Ok(()) above, so this is a genuine IO failure.
                        eprintln!("mcp-server {id} exited with error: {e}");
                        std::process::exit(1);
                    }
                }
            }
            other => {
                let got = other.unwrap_or("(missing)");
                eprintln!(
                    "unknown MCP server id: {got}\nusage: odysseus mcp-server <{}>",
                    odysseus::mcp_servers::SERVER_IDS.join("|")
                );
                std::process::exit(2);
            }
        }
    }

    // `odysseus setup` — run the first-time setup script and exit (the
    // translation of `python setup.py`). Synchronous like the source.
    if args.get(1).map(String::as_str) == Some("setup") {
        odysseus::src::setup::run();
        std::process::exit(0);
    }

    // `odysseus chroma [start|stop|status]` — manage a standalone REAL ChromaDB
    // server (the launch capability in `crate::src::chroma_launch`). Sibling of the
    // `mcp-server`/`setup` arms: runs synchronously and exits, never falling through
    // to the server. Always compiled (the crate has no cargo feature flags), and it
    // leans on `chroma_launch` which is likewise always compiled.
    //
    //   start  (default) -> `chroma_launch::ensure_chroma_running()` picks the
    //          launcher backend per ODYSSEUS_CHROMA_LAUNCHER ({auto,local,docker}):
    //          LOCAL = the pip-installed full `chromadb` python server (no docker),
    //          DETACHED so it survives this process; DOCKER = the `chromadb/chroma`
    //          container. Reuse a running server, else start one, poll
    //          `/api/v2/heartbeat` until ready, print the reachable URL
    //          `http://localhost:<CHROMADB_PORT>`, and EXIT 0 leaving it up.
    //   stop   -> SIGTERM a local `launcher.pid` AND/OR `docker stop` the container.
    //   status -> the docker container line, the local launcher.pid, + the heartbeat.
    //
    // Neither-launcher-available / launch-failure paths exit non-zero with an honest
    // stderr message (never a fabricated success), mirroring `chroma_launch`'s posture.
    if args.get(1).map(String::as_str) == Some("chroma") {
        let sub = args.get(2).cloned().unwrap_or_else(|| "start".to_string());
        // `chroma_launch`'s readiness poll uses `reqwest::blocking`, which builds its
        // OWN current-thread runtime per call and PANICS if dropped inside an existing
        // tokio runtime (and `main` is `#[tokio::main]`). Run the whole subcommand on a
        // dedicated OS thread OUTSIDE the async runtime so the blocking client lives and
        // dies on a thread with no ambient tokio runtime. The thread owns the exit code.
        let code = std::thread::spawn(move || run_chroma_subcommand(&sub))
            .join()
            .unwrap_or_else(|_| {
                eprintln!("chroma subcommand thread panicked");
                1
            });
        std::process::exit(code);
    }

    // `diffusion-server [--host H] [--port N] [--model REPO]` — the local
    // Stable-Diffusion txt2img server (scripts/diffusion_server.py port), serving
    // the OpenAI `/v1/images/generations` + `/v1/models` contract via candle.
    if args.get(1).map(String::as_str) == Some("diffusion-server") {
        let flag = |name: &str, default: &str| -> String {
            args.iter()
                .position(|a| a == name)
                .and_then(|i| args.get(i + 1))
                .cloned()
                .unwrap_or_else(|| default.to_string())
        };
        let host = flag("--host", "127.0.0.1");
        let port: u16 = flag("--port", "8100").parse().unwrap_or(8100);
        let model = flag(
            "--model",
            odysseus::src::diffusion_server::DEFAULT_SD15_REPO,
        );
        let version = flag("--version", "v1.5");
        match odysseus::src::diffusion_server::run_diffusion_server(&host, port, &model, &version)
            .await
        {
            Ok(()) => std::process::exit(0),
            Err(e) => {
                eprintln!("diffusion-server: {e}");
                std::process::exit(1);
            }
        }
    }

    // `--help` / `-h` — usage and exit.
    if args.iter().any(|a| a == "--help" || a == "-h") {
        print_usage();
        std::process::exit(0);
    }

    // `odysseus [serve] [--host H] [--port P]` — run the web server. `serve` is an
    // optional, uvicorn-style verb (the bare `odysseus` runs the server too). The
    // `--host`/`--port` flags use uvicorn's names and set the bind, defaulting the
    // unspecified half to 0.0.0.0 / 7000 — i.e. the equivalent of
    // `uvicorn app:app --host 0.0.0.0 --port 7000`. They override `ODYSSEUS_BIND`.
    let (host, port) = parse_serve_flags(&args);
    if host.is_some() || port.is_some() {
        let h = host.unwrap_or_else(|| "0.0.0.0".to_string());
        let p = port.unwrap_or_else(|| "7000".to_string());
        std::env::set_var("ODYSSEUS_BIND", format!("{h}:{p}"));
    }

    odysseus::web::run().await;
}

/// Parse uvicorn-style `--host H` / `--port P` (and the `--host=H` / `--port=P`
/// forms) from argv. The optional `serve` verb and any other tokens are ignored.
fn parse_serve_flags(args: &[String]) -> (Option<String>, Option<String>) {
    let mut host = None;
    let mut port = None;
    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--host" => {
                host = args.get(i + 1).cloned();
                i += 1;
            }
            "--port" => {
                port = args.get(i + 1).cloned();
                i += 1;
            }
            s if s.starts_with("--host=") => host = Some(s["--host=".len()..].to_string()),
            s if s.starts_with("--port=") => port = Some(s["--port=".len()..].to_string()),
            _ => {}
        }
        i += 1;
    }
    (host, port)
}

/// `odysseus chroma <sub>` dispatch. Returns the process exit code (0 = ok,
/// non-zero = honest failure with a stderr message). Synchronous — every arm runs
/// a blocking docker/HTTP operation and returns, never touching the async server.
/// Each arm delegates to `chroma_launch` (the launcher owns the container name,
/// docker detection, and honest error messages — main only maps to an exit code).
/// The reachable host port is `CHROMADB_PORT` (default `8100`, matching
/// `chroma_client.py`), so the printed URL is the exact target the server's
/// ChromaHttp backend connects to.
fn run_chroma_subcommand(sub: &str) -> i32 {
    use odysseus::src::chroma_launch;

    match sub {
        "start" => {
            // Pick the launcher backend (ODYSSEUS_CHROMA_LAUNCHER: auto/local/docker),
            // reuse/start + readiness poll. On success leave the server UP (the local
            // backend is DETACHED so it survives this subcommand exiting) and print
            // the URL the server uses.
            match chroma_launch::ensure_chroma_running() {
                Ok(handle) => {
                    // `CHROMADB_PORT` default 8100 (chroma_client.py) — the host
                    // port the launcher published; the URL is `localhost:<port>`.
                    let port = std::env::var("CHROMADB_PORT").unwrap_or_else(|_| "8100".to_string());
                    let how = if handle.started_by_us { "started" } else { "reused" };
                    println!("ChromaDB {how}: http://localhost:{port}  ({})", handle.name);
                    0
                }
                Err(e) => {
                    // Honest failure — neither launcher available, launch failed, or
                    // readiness timed out. The launcher already carries the detail.
                    eprintln!("chroma start failed: {e}");
                    1
                }
            }
        }
        // Explicit user request — stops regardless of who started it (unlike
        // `ChromaHandle::stop`'s started-by-us guard). Handles BOTH backends: a
        // recorded local `launcher.pid` (SIGTERM) and/or the docker container.
        "stop" => match chroma_launch::stop_container() {
            Ok(()) => 0,
            Err(e) => {
                eprintln!("chroma stop failed: {e}");
                1
            }
        },
        // Status of BOTH backends (docker container line, local launcher.pid, and the
        // heartbeat — the ultimate reachability truth).
        "status" => match chroma_launch::status() {
            Ok(()) => 0,
            Err(e) => {
                eprintln!("chroma status failed: {e}");
                1
            }
        },
        other => {
            eprintln!("unknown chroma subcommand: {other}\nusage: odysseus chroma [start|stop|status]");
            2
        }
    }
}

fn print_usage() {
    println!("Odysseus — self-hosted AI workspace (Rust port)\n");
    println!("USAGE:");
    println!("  odysseus [serve] [--host H] [--port P]   run the web server (default 0.0.0.0:7000)");
    println!("  odysseus setup                           first-time setup: data dirs + DB + admin user");
    println!("  odysseus mcp-server <id>                 run a built-in MCP stdio server (email|memory|image_gen|rag)");
    println!("  odysseus chroma [start|stop|status]      manage a standalone ChromaDB server, local or docker (default: start)");
    println!();
    println!("ENV:");
    println!("  ODYSSEUS_BIND       host:port to bind (overridden by --host/--port)");
    println!("  ODYSSEUS_DATA_DIR   data dir (default <repo>/data); setup + server share it");
    println!("  AUTH_ENABLED        '1' (default) / '0'");
    println!("  LOCALHOST_BYPASS    'true' to trust loopback requests (skip login)");
    println!("  ODYSSEUS_ADMIN_USER / ODYSSEUS_ADMIN_PASSWORD   used by `setup`");
    println!();
    println!("  VECTOR STORE (RAG + memory):");
    println!("  ODYSSEUS_VECTOR_BACKEND   'chroma' -> real ChromaDB over HTTP; 'sqlite' (default) -> built-in SQLite store");
    println!("  CHROMADB_HOST / CHROMADB_PORT   Chroma server (default localhost:8100); setting EITHER also selects the chroma backend");
    println!("  ODYSSEUS_LAUNCH_CHROMA    '1'/'true'/'yes' -> auto-launch a ChromaDB server at server startup (default off)");
    println!("  ODYSSEUS_CHROMA_LAUNCHER  'auto' (default) | 'local' | 'docker' — how to launch Chroma:");
    println!("                            'local' uses the pip-installed full `chromadb` package's own server (NO docker required);");
    println!("                            'docker' uses the chromadb/chroma container; 'auto' prefers local, else docker");
    println!("  ODYSSEUS_CHROMA_PYTHON    explicit python interpreter for the local launcher (else venv/python3/python are probed)");
}
