//! `pylog` — the slice of Python's `logging` the translation uses.
//!
//! `app.py` calls `logging.basicConfig(level=logging.INFO, ...)`, so `DEBUG`
//! records are suppressed; everything else goes to stderr. We reproduce that:
//! `info`/`warning`/`error` print, `debug` is a no-op.

pub fn info(msg: &str) {
    eprintln!("INFO - {msg}");
}

pub fn warning(msg: &str) {
    eprintln!("WARNING - {msg}");
}

pub fn error(msg: &str) {
    eprintln!("ERROR - {msg}");
}

pub fn debug(_msg: &str) {
    // Suppressed at the INFO level configured in app.py.
}
