//! `pyotp` — a minimal, behaviour-compatible port of the parts of the `pyotp`
//! library the auth module uses: `random_base32()` and `TOTP` (default SHA1 /
//! 6 digits / 30s period) with `verify()` and `provisioning_uri()`.

use crate::pysecrets;
use crate::pytime;
use hmac::{Hmac, Mac};
use sha1::Sha1;

type HmacSha1 = Hmac<Sha1>;

const BASE32_ALPHABET: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

/// `pyotp.random_base32(length=32)`.
pub fn random_base32() -> String {
    (0..32)
        .map(|_| pysecrets::choice(BASE32_ALPHABET) as char)
        .collect()
}

/// `pyotp.TOTP(secret)` — time-based one-time password generator.
pub struct TOTP {
    secret: String,
    digits: u32,
    interval: u64,
}

impl TOTP {
    pub fn new(secret: &str) -> Self {
        TOTP {
            secret: secret.to_string(),
            digits: 6,
            interval: 30,
        }
    }

    /// Decode the base32 secret to the raw HMAC key (pyotp casefolds + pads).
    fn key(&self) -> Vec<u8> {
        let mut s = self.secret.to_uppercase();
        // Pad up to a multiple of 8 base32 chars so decoding always succeeds.
        while !s.len().is_multiple_of(8) {
            s.push('=');
        }
        base32::decode(base32::Alphabet::Rfc4648 { padding: true }, &s).unwrap_or_default()
    }

    /// HOTP value for `counter` (RFC 4226 dynamic truncation), zero-padded.
    fn generate(&self, counter: u64) -> String {
        let mut mac = HmacSha1::new_from_slice(&self.key()).expect("HMAC accepts any key length");
        mac.update(&counter.to_be_bytes());
        let hs = mac.finalize().into_bytes();
        let offset = (hs[hs.len() - 1] & 0x0f) as usize;
        let bin = ((u32::from(hs[offset]) & 0x7f) << 24)
            | (u32::from(hs[offset + 1]) << 16)
            | (u32::from(hs[offset + 2]) << 8)
            | u32::from(hs[offset + 3]);
        let code = bin % 10u32.pow(self.digits);
        format!("{:0width$}", code, width = self.digits as usize)
    }

    /// `TOTP.at(for_time)` — the code for a given Unix timestamp.
    pub fn at(&self, for_time: i64) -> String {
        let counter = (for_time as u64) / self.interval;
        self.generate(counter)
    }

    /// `TOTP.verify(otp, valid_window=...)` — accept the current step and
    /// `valid_window` steps on either side (clock-skew tolerance).
    pub fn verify(&self, otp: &str, valid_window: i64) -> bool {
        let now = pytime::time() as i64;
        for i in -valid_window..=valid_window {
            let t = now + i * self.interval as i64;
            if self.at(t) == otp {
                return true;
            }
        }
        false
    }

    /// `TOTP.provisioning_uri(name=..., issuer_name=...)` — the otpauth:// URI.
    pub fn provisioning_uri(&self, name: &str, issuer_name: &str) -> String {
        let label = format!("{}:{}", quote(issuer_name), quote(name));
        format!(
            "otpauth://totp/{}?secret={}&issuer={}",
            label,
            self.secret,
            quote(issuer_name)
        )
    }
}

/// `urllib.parse.quote` over the unreserved set (RFC 3986).
fn quote(s: &str) -> String {
    let mut out = String::new();
    for &b in s.as_bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(b as char)
            }
            _ => out.push_str(&format!("%{:02X}", b)),
        }
    }
    out
}
