// services/shell/mod.rs  <- the Python `services/shell/__init__.py`
//! Shell service — safe command execution.
//!
//! Mirrors `services/shell/__init__.py`:
//! ```python
//! """Shell service — safe command execution."""
//!
//! from .service import ShellService, ShellResult
//!
//! __all__ = ["ShellService", "ShellResult"]
//! ```

pub mod service;

pub use service::{ShellResult, ShellService};
