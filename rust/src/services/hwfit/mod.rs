// services/hwfit/mod.rs  <- the Python `services/hwfit/__init__.py`
//! Mirrors the Python `services/hwfit/` package.
//!
//! The Python `services/hwfit/__init__.py` is **empty** (0 bytes); it exposes
//! no symbols and only marks the directory as a package. This Rust package
//! root therefore re-exports nothing — it merely declares the four submodules
//! so callers reach them via `crate::services::hwfit::{models, fit,
//! image_models, hardware}`, exactly as Python code imports
//! `services.hwfit.models`, `services.hwfit.fit`, etc.
//!
//! ## Feature gates
//!
//! Every submodule is **default-buildable**. Despite the `hwfit` name, none of
//! this code needs `web` (no HTTP/LLM) or `db`:
//!   * `models` / `fit` / `image_models` are pure math + a JSON catalog.
//!   * `hardware` shells out (`nvidia-smi`/`ssh`/`uname`/PowerShell via
//!     `std::process::Command`) and reads `/proc` + `/sys` directly
//!     (`std::fs`). `psutil` is NOT used by the Python; the only non-portable
//!     bit is the local-RAM `os.sysconf(SC_PHYS_PAGES)` fallback on non-Linux
//!     hosts, which the port substitutes with the `sysinfo` crate (still a
//!     default dependency). So `hardware` stays default-gated too.
//!
//! ## Model-row loose-typing convention (shared by `models`/`fit`/`image_models`)
//!
//! The Python code is *vibecoded* around untyped `dict` rows: a "model" and a
//! "system" are plain dicts read with `.get(key, default)` (e.g.
//! `model.get("quantization", "")`, `model.get("is_moe")`,
//! `system.get("gpus", [])`). Rows flow loosely between the submodules — the
//! catalog rows built by [`models::get_models`] and the system dict produced
//! by [`hardware::detect_system`] are both consumed by [`fit::rank_models`] and
//! [`image_models::rank_image_models`].
//!
//! To preserve that shape faithfully (and the JSON key ordering callers depend
//! on), the Rust port keeps these rows as [`serde_json::Value`] (objects backed
//! by an order-preserving `Map`) rather than introducing rigid structs. Each
//! submodule reads fields through small `Value`-backed `.get()`-style helper
//! accessors that mirror Python's `dict.get(key, default)` — returning the
//! requested default when the key is absent or the wrong JSON type, never
//! panicking. This is the deliberate convention across the package; do not
//! "tighten" it into structs without porting the dynamic field set first.

pub mod fit;
pub mod hardware;
pub mod image_models;
pub mod models;
// `profiles` <- services/hwfit/profiles.py — compute_serve_profiles + the
// GET /api/hwfit/profiles handler's backing logic.
pub mod profiles;
