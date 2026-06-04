// src/setup.rs  <- setup.py
//! Odysseus — first-time setup script.
//!
//! Creates data directories, initializes the database, and sets up an
//! initial admin user. Safe to re-run (skips what already exists).
//!
//! The Python `setup.py` is a standalone first-run script
//! (`python setup.py`). The Rust analogue is the `odysseus setup`
//! SUBCOMMAND: `main.rs` dispatches it (parallel to `mcp-server <id>`),
//! runs `setup::run()`, and exits — the no-subcommand path runs the web
//! server unchanged.
//!
//! Faithful mapping of the source:
//!   * Python `BASE_DIR = dirname(abspath(__file__))` (the repo root) ->
//!     `core::constants::BASE_DIR` (the same repo root; trailing `/`).
//!   * Python `DATA_DIR = join(BASE_DIR, "data")` -> `core::constants::DATA_DIR`.
//!     DOCUMENTED IMPROVEMENT: the Rust `DATA_DIR` honors `ODYSSEUS_DATA_DIR`
//!     (the rest of the port does too, so setup, the DB, and auth.json stay
//!     consistent); the Python had no such override.
//!   * Python `Base.metadata.create_all(bind=engine)` -> `core::database::init_db()`.
//!     DOCUMENTED IMPROVEMENT: `init_db` is a SUPERSET of `create_all` — it also
//!     runs every schema migration, so a setup'd DB is fully up to date.
//!   * `check_deps`'s Python-package import probe is N/A for a compiled binary
//!     (the deps are linked in); only the OS-environment `tmux` presence check is
//!     kept (it is still meaningful for the cookbook background-download tooling).

use crate::core::constants;
use crate::pyos as os;

/// The 11 directories `setup.py` creates (`DIRS`), in source order.
fn dirs() -> Vec<String> {
    let data = &*constants::DATA_DIR;
    let base = &*constants::BASE_DIR;
    vec![
        data.clone(),
        os::path::join(data, "uploads"),
        os::path::join(data, "personal_docs"),
        os::path::join(data, "personal_uploads"),
        os::path::join(data, "tts_cache"),
        os::path::join(data, "generated_images"),
        os::path::join(data, "deep_research"),
        os::path::join(data, "chroma"),
        os::path::join(data, "rag"),
        os::path::join(data, "memory_vectors"),
        os::path::join(base, "logs"),
    ]
}

/// `os.path.relpath(path, start)` for the narrow case `setup.py` uses (printing
/// each created dir relative to `BASE_DIR`). `BASE_DIR` ends with `/`, so a
/// child path starts with it; strip that prefix. Falls back to the basename
/// when `path` is not under `base` (it always is here).
fn relpath(path: &str, base: &str) -> String {
    if let Some(rest) = path.strip_prefix(base) {
        // `base` already carries the trailing separator.
        return rest.to_string();
    }
    // Fallback (shouldn't happen with the source dirs).
    os::path::basename(path)
}

/// `setup.py:create_dirs` — `os.makedirs(d, exist_ok=True)` for each dir.
fn create_dirs() {
    let base = &*constants::BASE_DIR;
    for d in dirs() {
        let _ = os::makedirs(&d, true);
        println!("  [ok] {}/", relpath(&d, base));
    }
}

/// `setup.py:create_env` — copy `.env.example` to `.env` if `.env` is absent.
fn create_env() {
    let base = &*constants::BASE_DIR;
    let env_path = os::path::join(base, ".env");
    let example_path = os::path::join(base, ".env.example");
    if os::path::exists(&env_path) {
        println!("  [skip] .env already exists");
        return;
    }
    if os::path::exists(&example_path) {
        // `shutil.copy2` -> `std::fs::copy` (copies contents; metadata copy is
        // not faithfully required here and is platform-dependent in Python too).
        match std::fs::copy(&example_path, &env_path) {
            Ok(_) => {
                println!("  [ok] .env created from .env.example");
                println!("        ** Edit .env with your LLM host and API keys **");
            }
            Err(e) => {
                println!("  [warn] could not create .env: {e}");
            }
        }
    } else {
        println!("  [warn] .env.example not found — create .env manually");
    }
}

/// `shutil.which(cmd)` — locate an executable on `PATH`. Minimal local copy so
/// this module does not reach into `builtin_mcp`'s private `shutil_which`.
fn which(cmd: &str) -> Option<String> {
    let path = std::env::var_os("PATH")?;
    for dir in std::env::split_paths(&path) {
        let candidate = dir.join(cmd);
        if candidate.is_file() {
            return Some(candidate.to_string_lossy().into_owned());
        }
    }
    None
}

/// `setup.py:check_deps` — the Python-package import probe is N/A for a compiled
/// Rust binary (those deps are linked in), so only the OS-environment `tmux`
/// presence check is ported (Python: `os.name != "nt"` -> non-Windows).
fn check_deps() {
    // The Python `for mod in [...]: __import__(mod)` import check has no Rust
    // analogue — the equivalent crates are compiled into this binary.
    println!("  [ok] Core dependencies compiled in");

    #[cfg(not(windows))]
    {
        if which("tmux").is_none() {
            println!("\n  [warn] tmux not found");
            println!("         Cookbook uses tmux for background downloads and model serves.");
            println!("         Install it with your OS package manager, for example:");
            println!("           sudo apt install tmux");
            println!("           sudo pacman -S tmux");
            println!("           sudo dnf install tmux");
        } else {
            println!("  [ok] tmux installed");
        }
    }
}

/// `setup.py:init_database` — `os.environ.setdefault("DATABASE_URL", ...)` then
/// create all tables. The Python's `Base.metadata.create_all(bind=engine)` is
/// superseded by `core::database::init_db()` (create_all + every migration).
fn init_database() {
    // `os.environ.setdefault("DATABASE_URL", f"sqlite:///{DATA_DIR}/app.db")`.
    if std::env::var("DATABASE_URL").is_err() {
        let db_path = os::path::join(&constants::DATA_DIR, "app.db");
        os::set_var("DATABASE_URL", &format!("sqlite:///{db_path}"));
    }
    crate::core::database::init_db();
    println!("  [ok] Database initialized");
}

/// `setup.py:create_default_admin` — write the initial admin to
/// `<DATA_DIR>/auth.json` if it does not yet exist. The byte-shape matches the
/// Python verbatim: `{"users": {<user>: {"password_hash": ..., "is_admin": true}}}`
/// with `indent=2`. We deliberately write this minimal JSON directly rather than
/// going through `AuthManager::create_user` (which would add a `privileges`
/// block `setup.py` does not). `AuthManager` reads `users[u].password_hash` +
/// `is_admin`, which this shape provides.
fn create_default_admin() {
    let auth_path = os::path::join(&constants::DATA_DIR, "auth.json");
    if os::path::exists(&auth_path) {
        println!("  [skip] auth.json already exists");
        return;
    }

    // `os.getenv("ODYSSEUS_ADMIN_USER", "admin").strip() or "admin"`.
    let mut username = os::getenv("ODYSSEUS_ADMIN_USER", "admin").trim().to_string();
    if username.is_empty() {
        username = "admin".to_string();
    }
    // Admin creation is OPT-IN: only mint one here when ODYSSEUS_ADMIN_PASSWORD is
    // set (for headless deploys). Otherwise leave it to the browser first-run flow —
    // the login page shows "Create Admin Account" whenever no admin exists. (This
    // diverges from setup.py, which always minted a random-password admin; the
    // browser flow is the friendlier default and what the SPA was built for.)
    let password = match os::getenv_opt("ODYSSEUS_ADMIN_PASSWORD") {
        Some(p) if !p.is_empty() => p,
        _ => {
            println!("  [skip] No admin created — start the server and open /login to");
            println!("         create your admin account in the browser. (Set");
            println!("         ODYSSEUS_ADMIN_PASSWORD to pre-create one for headless runs.)");
            return;
        }
    };
    // `bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()`.
    let hashed = match bcrypt::hash(&password, bcrypt::DEFAULT_COST) {
        Ok(h) => h,
        Err(e) => {
            println!("  [warn] Admin creation failed: {e}");
            return;
        }
    };

    let auth_data = serde_json::json!({
        "users": {
            &username: {
                "password_hash": hashed,
                "is_admin": true,
            }
        }
    });

    // `json.dump(auth_data, f, indent=2)`.
    let serialized = match serde_json::to_string_pretty(&auth_data) {
        Ok(s) => s,
        Err(e) => {
            println!("  [warn] Admin creation failed: {e}");
            return;
        }
    };
    if let Err(e) = std::fs::write(&auth_path, serialized) {
        println!("  [warn] Admin creation failed: {e}");
        return;
    }

    println!("  [ok] Initial admin user '{username}' created (password from ODYSSEUS_ADMIN_PASSWORD)");
}

/// `setup.py:main` — the numbered setup flow. Run once, then the caller exits.
pub fn run() {
    println!("\n=== Odysseus Setup ===\n");

    println!("1. Creating directories...");
    create_dirs();

    println!("\n2. Environment file...");
    create_env();

    println!("\n3. Checking dependencies...");
    check_deps();

    println!("\n4. Initializing database...");
    // Python wraps this in `try/except` and prints a [warn]; `init_db` swallows
    // its own per-step errors (logs warnings), so there is no fallible Result to
    // catch here — the faithful behavior (continue on a non-fatal failure) holds.
    init_database();

    println!("\n5. Creating initial admin...");
    create_default_admin();

    println!("\n=== Setup complete ===");
    println!("\nStart the server with:");
    println!("  odysseus serve            # or: odysseus serve --port 7070");
    println!("\nThen open http://localhost:7000/login — on first run the page");
    println!("prompts you to create your admin account.\n");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dirs_match_setup_py_count_and_suffixes() {
        // setup.py's DIRS has exactly 11 entries.
        let d = dirs();
        assert_eq!(d.len(), 11);
        // The first is DATA_DIR itself; the last is <BASE>/logs.
        assert!(d[0].ends_with("data") || d[0] == *constants::DATA_DIR);
        assert!(d[10].ends_with("logs"));
        // The data-relative children, in source order.
        let suffixes = [
            "uploads",
            "personal_docs",
            "personal_uploads",
            "tts_cache",
            "generated_images",
            "deep_research",
            "chroma",
            "rag",
            "memory_vectors",
        ];
        for (i, suf) in suffixes.iter().enumerate() {
            assert!(
                d[i + 1].ends_with(suf),
                "dir {} should end with {suf}, got {}",
                i + 1,
                d[i + 1]
            );
        }
    }

    #[test]
    fn relpath_strips_base_prefix() {
        // BASE_DIR ends with '/', so a child path strips to the tail.
        assert_eq!(relpath("/repo/data/uploads", "/repo/"), "data/uploads");
        assert_eq!(relpath("/repo/logs", "/repo/"), "logs");
        // Not under base -> basename fallback.
        assert_eq!(relpath("/other/x", "/repo/"), "x");
    }

    #[test]
    fn which_finds_a_known_binary() {
        // `sh` exists on every unix test host; on Windows this is skipped.
        #[cfg(unix)]
        {
            assert!(which("sh").is_some());
            assert!(which("definitely-not-a-real-binary-xyz").is_none());
        }
    }
}
