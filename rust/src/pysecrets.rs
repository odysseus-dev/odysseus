//! `pysecrets` — the slice of Python's `secrets` module the translation uses.

use base64::Engine;
use rand::RngCore;

/// `secrets.token_bytes(n)`.
pub fn token_bytes(n: usize) -> Vec<u8> {
    let mut buf = vec![0u8; n];
    rand::thread_rng().fill_bytes(&mut buf);
    buf
}

/// `secrets.token_hex(n)` — `2*n` hex chars from `n` random bytes.
pub fn token_hex(n: usize) -> String {
    hex::encode(token_bytes(n))
}

/// `secrets.token_urlsafe(n)` — url-safe base64 of `n` random bytes, no padding.
pub fn token_urlsafe(n: usize) -> String {
    base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(token_bytes(n))
}

/// `secrets.choice(seq)` — a uniformly random element.
pub fn choice<T: Copy>(seq: &[T]) -> T {
    use rand::Rng;
    let i = rand::thread_rng().gen_range(0..seq.len());
    seq[i]
}
