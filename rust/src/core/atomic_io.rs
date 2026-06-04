// core/atomic_io.rs  <- core/atomic_io.py
//! Atomic JSON file writes.
//!
//! Use this everywhere a JSON config file is persisted. A plain `open("w") +
//! json.dump` truncates the file on first write and only fills it with new
//! content afterwards — a kill -9 / power loss / OOM in between produces a
//! truncated or empty file. For password DBs (`auth.json`) and live state
//! (`sessions.json`, `settings.json`, `integrations.json`, `cookbook_state.json`),
//! that's a data-loss event.
//!
//! `atomic_write_json` writes to a sibling tmp file, fsyncs, then `os.replace`s
//! into place. On POSIX `os.replace` is atomic on the same filesystem.

use crate::error::PyResult;
use crate::pyos as os;
use serde::Serialize;
use std::fs::File;
use std::io::{self, Write};

/// A `serde_json` formatter that reproduces CPython's `json.dump` *default*
/// separators (`", "` after items, `": "` after keys), so the bytes we write
/// match what the live Python writes when `indent` is `None`.
struct PyCompactFormatter;

impl serde_json::ser::Formatter for PyCompactFormatter {
    fn begin_array_value<W>(&mut self, writer: &mut W, first: bool) -> io::Result<()>
    where
        W: ?Sized + io::Write,
    {
        if first {
            Ok(())
        } else {
            writer.write_all(b", ")
        }
    }

    fn begin_object_key<W>(&mut self, writer: &mut W, first: bool) -> io::Result<()>
    where
        W: ?Sized + io::Write,
    {
        if first {
            Ok(())
        } else {
            writer.write_all(b", ")
        }
    }

    fn begin_object_value<W>(&mut self, writer: &mut W) -> io::Result<()>
    where
        W: ?Sized + io::Write,
    {
        writer.write_all(b": ")
    }
}

/// CPython `json.dump` defaults to `ensure_ascii=True`: every non-ASCII code
/// point is escaped as `\uXXXX` (astral chars as a UTF-16 surrogate pair),
/// lowercase hex. serde_json writes raw UTF-8 instead, so we post-process the
/// serialized text to match byte-for-byte. This is safe because, after
/// serialization, the only non-ASCII bytes live inside JSON string values
/// (serde already escapes `"`, `\`, and control chars as ASCII).
fn ensure_ascii(s: &str) -> String {
    // Fast path: already pure ASCII.
    if s.is_ascii() {
        return s.to_string();
    }
    let mut out = String::with_capacity(s.len());
    let mut buf = [0u16; 2];
    for ch in s.chars() {
        if (ch as u32) < 0x80 {
            out.push(ch);
        } else {
            for unit in ch.encode_utf16(&mut buf) {
                out.push_str(&format!("\\u{:04x}", unit));
            }
        }
    }
    out
}

/// Serialize `data` exactly like `json.dump(data, f, indent=indent)`.
fn json_dump<T: Serialize>(data: &T, indent: Option<usize>) -> PyResult<Vec<u8>> {
    let mut buf = Vec::new();
    match indent {
        None => {
            let mut ser = serde_json::Serializer::with_formatter(&mut buf, PyCompactFormatter);
            data.serialize(&mut ser)?;
        }
        Some(n) => {
            let spaces = " ".repeat(n);
            let fmt = serde_json::ser::PrettyFormatter::with_indent(spaces.as_bytes());
            let mut ser = serde_json::Serializer::with_formatter(&mut buf, fmt);
            data.serialize(&mut ser)?;
        }
    }
    // serde_json always emits valid UTF-8.
    let text = String::from_utf8(buf).expect("serde_json emits valid utf-8");
    Ok(ensure_ascii(&text).into_bytes())
}

/// Atomically persist `data` as JSON at `path`.
///
/// The temp file uses the live PID as a suffix so two processes saving the
/// same file (e.g. unit tests) don't collide on the rename target.
pub fn atomic_write_json<T: Serialize>(path: &str, data: &T, indent: Option<usize>) -> PyResult<()> {
    let dir = os::path::dirname(path);
    let dir = if dir.is_empty() { ".".to_string() } else { dir };
    os::makedirs(&dir, true)?;
    let tmp = format!("{}.tmp.{}", path, os::getpid());
    {
        let mut f = File::create(&tmp)?;
        f.write_all(&json_dump(data, indent)?)?;
        f.flush()?;
        f.sync_all()?; // os.fsync(f.fileno())
    }
    os::replace(&tmp, path)?;
    Ok(())
}

pub fn atomic_write_text(path: &str, text: &str) -> PyResult<()> {
    let dir = os::path::dirname(path);
    let dir = if dir.is_empty() { ".".to_string() } else { dir };
    os::makedirs(&dir, true)?;
    let tmp = format!("{}.tmp.{}", path, os::getpid());
    {
        let mut f = File::create(&tmp)?;
        f.write_all(text.as_bytes())?;
        f.flush()?;
        f.sync_all()?; // os.fsync(f.fileno())
    }
    os::replace(&tmp, path)?;
    Ok(())
}
