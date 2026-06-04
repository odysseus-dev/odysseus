// routes/gallery_routes.rs::pyrandom  <- CPython `random.Random` (Mersenne Twister)
//! A faithful, byte-for-byte reimplementation of the slice of CPython's
//! `random.Random` that `gallery_routes.py::gallery_library` depends on for the
//! seeded-`shuffle` pagination:
//!
//! ```python
//! rng = random.Random(seed if seed is not None else 0)
//! rng.shuffle(all_ids)
//! ```
//!
//! The shuffled order MUST match CPython exactly, otherwise the `?sort=shuffle`
//! pages would differ from the Python server for the same `seed`. CPython's
//! `random` is the MT19937 Mersenne Twister with the *exact* seeding and
//! `getrandbits` algorithms in `Modules/_randommodule.c`; we port those three
//! pieces:
//!
//! 1. `init_genrand` / `init_by_array` — the MT19937 key-array seeding CPython
//!    uses for an integer seed. For an integer seed CPython takes
//!    `abs(seed)` (so `-5` and `5` give the same stream) and converts it to an
//!    array of 32-bit little-endian words (the empty array for `0` becomes
//!    `[0]`), then calls `init_by_array`.
//! 2. `genrand_uint32` — the standard MT19937 tempered-output generator.
//! 3. `getrandbits(k)` — CPython's `random_getrandbits`: produce `k` bits by
//!    taking `ceil(k/32)` words, masking the top word to the needed bits, and
//!    assembling them little-endian (word 0 is the low 32 bits).
//! 4. `_randbelow_with_getrandbits(n)` and `shuffle` — the pure-Python
//!    algorithms in `Lib/random.py`.
//!
//! Verified against `python3` reference values in the unit tests below.

/// MT19937 state + CPython's integer-seed `getrandbits`/`shuffle`.
pub struct PyRandom {
    mt: [u32; N],
    mti: usize,
}

const N: usize = 624;
const M: usize = 397;
const MATRIX_A: u32 = 0x9908_b0df;
const UPPER_MASK: u32 = 0x8000_0000;
const LOWER_MASK: u32 = 0x7fff_ffff;

impl PyRandom {
    /// `random.Random(seed)` for an integer seed.
    ///
    /// CPython's `random_seed`: for an integer argument it uses the absolute
    /// value, splits it into 32-bit little-endian words, and seeds via
    /// `init_by_array`. A zero seed yields the single-word key `[0]`.
    pub fn new(seed: i64) -> Self {
        let mut r = PyRandom {
            mt: [0u32; N],
            mti: N + 1,
        };
        let key = int_to_key(seed);
        r.init_by_array(&key);
        r
    }

    /// `init_genrand(s)` — MT19937 base seeding from a single 32-bit value.
    fn init_genrand(&mut self, s: u32) {
        self.mt[0] = s;
        for i in 1..N {
            // mt[i] = 1812433253 * (mt[i-1] ^ (mt[i-1] >> 30)) + i
            let prev = self.mt[i - 1];
            self.mt[i] = (1_812_433_253u32
                .wrapping_mul(prev ^ (prev >> 30)))
            .wrapping_add(i as u32);
        }
        self.mti = N;
    }

    /// `init_by_array(init_key, key_length)` — MT19937 seeding from a key array,
    /// exactly as CPython does for an integer seed.
    fn init_by_array(&mut self, init_key: &[u32]) {
        self.init_genrand(19_650_218);
        let key_length = init_key.len();
        let mut i = 1usize;
        let mut j = 0usize;
        let mut k = if N > key_length { N } else { key_length };
        while k > 0 {
            let prev = self.mt[i - 1];
            // mt[i] = (mt[i] ^ ((prev ^ (prev>>30)) * 1664525)) + init_key[j] + j
            self.mt[i] = (self.mt[i]
                ^ ((prev ^ (prev >> 30)).wrapping_mul(1_664_525)))
            .wrapping_add(init_key[j])
            .wrapping_add(j as u32);
            i += 1;
            j += 1;
            if i >= N {
                self.mt[0] = self.mt[N - 1];
                i = 1;
            }
            if j >= key_length {
                j = 0;
            }
            k -= 1;
        }
        let mut k = N - 1;
        while k > 0 {
            let prev = self.mt[i - 1];
            // mt[i] = (mt[i] ^ ((prev ^ (prev>>30)) * 1566083941)) - i
            self.mt[i] = (self.mt[i]
                ^ ((prev ^ (prev >> 30)).wrapping_mul(1_566_083_941)))
            .wrapping_sub(i as u32);
            i += 1;
            if i >= N {
                self.mt[0] = self.mt[N - 1];
                i = 1;
            }
            k -= 1;
        }
        self.mt[0] = 0x8000_0000; // MSB is 1; assuring non-zero initial array
    }

    /// `genrand_uint32` — the tempered MT19937 output.
    fn genrand_uint32(&mut self) -> u32 {
        if self.mti >= N {
            // generate N words at one time
            for kk in 0..(N - M) {
                let y = (self.mt[kk] & UPPER_MASK) | (self.mt[kk + 1] & LOWER_MASK);
                self.mt[kk] = self.mt[kk + M] ^ (y >> 1) ^ mag01(y);
            }
            for kk in (N - M)..(N - 1) {
                let y = (self.mt[kk] & UPPER_MASK) | (self.mt[kk + 1] & LOWER_MASK);
                self.mt[kk] = self.mt[kk + M - N] ^ (y >> 1) ^ mag01(y);
            }
            let y = (self.mt[N - 1] & UPPER_MASK) | (self.mt[0] & LOWER_MASK);
            self.mt[N - 1] = self.mt[M - 1] ^ (y >> 1) ^ mag01(y);
            self.mti = 0;
        }
        let mut y = self.mt[self.mti];
        self.mti += 1;
        // Tempering.
        y ^= y >> 11;
        y ^= (y << 7) & 0x9d2c_5680;
        y ^= (y << 15) & 0xefc6_0000;
        y ^= y >> 18;
        y
    }

    /// `getrandbits(k)` for `k >= 1` — CPython's `random_getrandbits`.
    ///
    /// Returns a non-negative big integer represented as little-endian 32-bit
    /// words (word 0 = low 32 bits). For the gallery shuffle `k = n.bit_length()`
    /// where `n` is at most the image count, so `k` fits comfortably, but we
    /// implement the general multi-word form for faithfulness.
    fn getrandbits_words(&mut self, k: u32) -> Vec<u32> {
        debug_assert!(k >= 1);
        if k <= 32 {
            // genrand_uint32() >> (32 - k)
            return vec![self.genrand_uint32() >> (32 - k)];
        }
        let words = (k as usize).div_ceil(32);
        let mut out = Vec::with_capacity(words);
        let mut remaining = k;
        for _ in 0..words {
            let take = remaining.min(32);
            let mut r = self.genrand_uint32();
            if take < 32 {
                r >>= 32 - take;
            }
            out.push(r);
            remaining = remaining.saturating_sub(32);
        }
        out
    }

    /// `getrandbits(k)` as a `u64` (sufficient for the shuffle's `_randbelow`,
    /// where `n <= list_len` keeps `k` well under 64 bits).
    pub fn getrandbits(&mut self, k: u32) -> u64 {
        let words = self.getrandbits_words(k);
        let mut v: u64 = 0;
        for (i, w) in words.iter().enumerate() {
            v |= (*w as u64) << (32 * i);
        }
        v
    }

    /// `_randbelow_with_getrandbits(n)` for `n > 0` — CPython `Lib/random.py`:
    /// ```python
    /// k = n.bit_length()
    /// r = getrandbits(k)
    /// while r >= n:
    ///     r = getrandbits(k)
    /// return r
    /// ```
    fn randbelow(&mut self, n: u64) -> u64 {
        if n == 0 {
            return 0;
        }
        let k = bit_length(n);
        let mut r = self.getrandbits(k);
        while r >= n {
            r = self.getrandbits(k);
        }
        r
    }

    /// `random.shuffle(x)` (no `random` arg) — the modern Fisher-Yates:
    /// ```python
    /// for i in reversed(range(1, len(x))):
    ///     j = self._randbelow(i + 1)
    ///     x[i], x[j] = x[j], x[i]
    /// ```
    pub fn shuffle<T>(&mut self, x: &mut [T]) {
        let len = x.len();
        if len <= 1 {
            return;
        }
        for i in (1..len).rev() {
            let j = self.randbelow((i + 1) as u64) as usize;
            x.swap(i, j);
        }
    }
}

/// `mag01[y & 0x1]` — `0` if `y` is even, `MATRIX_A` if odd.
#[inline]
fn mag01(y: u32) -> u32 {
    if y & 1 == 0 {
        0
    } else {
        MATRIX_A
    }
}

/// `int.bit_length()` for a non-negative integer.
fn bit_length(n: u64) -> u32 {
    if n == 0 {
        0
    } else {
        64 - n.leading_zeros()
    }
}

/// Convert an integer seed to CPython's MT19937 key array.
///
/// CPython uses `abs(seed)` and converts it to little-endian 32-bit words. A zero
/// magnitude yields the single-word key `[0]` (CPython's `init_by_array` is always
/// called with at least one word).
fn int_to_key(seed: i64) -> Vec<u32> {
    let mag = (seed as i128).unsigned_abs();
    if mag == 0 {
        return vec![0];
    }
    let mut words = Vec::new();
    let mut v = mag;
    while v > 0 {
        words.push((v & 0xffff_ffff) as u32);
        v >>= 32;
    }
    words
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Reference values captured from `python3`:
    /// `[random.Random(s).getrandbits(32) for _ in range(3)]`.
    #[test]
    fn getrandbits_matches_cpython() {
        let cases: &[(i64, [u32; 3])] = &[
            (0, [3626764237, 1654615998, 3255389356]),
            (1, [577090037, 2444712010, 3639700191]),
            (42, [2746317213, 478163327, 107420369]),
            (123456789, [2754794679, 1899526012, 2328685183]),
            (4294967296, [485306839, 1508871100, 1794561286]),
            (99999999999, [4150322677, 370721564, 3136426470]),
            (1099511627781, [2166296868, 2220160828, 1153647273]),
            (-5, [2675342405, 1097127993, 3185950873]),
        ];
        for (seed, expected) in cases {
            let mut r = PyRandom::new(*seed);
            let got = [r.getrandbits(32) as u32, r.getrandbits(32) as u32, r.getrandbits(32) as u32];
            assert_eq!(&got, expected, "seed {seed}");
        }
    }

    /// Sequential `getrandbits` widths on ONE rng (CPython, same instance):
    /// `r = random.Random(0); r.getrandbits(32) == 3626764237; r.getrandbits(1) == 0`
    /// (the second call reads the next MT word, `1654615998 >> 31 == 0`). A fresh
    /// rng's first `getrandbits(1)` is `1` (`3626764237 >> 31 == 1`).
    #[test]
    fn getrandbits_mixed_widths() {
        let mut r = PyRandom::new(0);
        assert_eq!(r.getrandbits(32) as u32, 3626764237);
        assert_eq!(r.getrandbits(1), 0);
        let mut r2 = PyRandom::new(0);
        assert_eq!(r2.getrandbits(1), 1);
    }

    /// Negative seed equals its absolute value (CPython `abs(seed)`).
    #[test]
    fn negative_seed_equals_abs() {
        let mut a = PyRandom::new(5);
        let mut b = PyRandom::new(-5);
        for _ in 0..5 {
            assert_eq!(a.getrandbits(32), b.getrandbits(32));
        }
    }

    /// `r = random.Random(0); ids = [f"id{i}" for i in range(10)]; r.shuffle(ids)`
    /// → ['id7','id8','id1','id5','id3','id4','id2','id0','id9','id6'].
    #[test]
    fn shuffle_matches_cpython_seed0() {
        let mut ids: Vec<String> = (0..10).map(|i| format!("id{i}")).collect();
        let mut r = PyRandom::new(0);
        r.shuffle(&mut ids);
        assert_eq!(
            ids,
            vec![
                "id7", "id8", "id1", "id5", "id3", "id4", "id2", "id0", "id9", "id6"
            ]
        );
    }

    /// `r = random.Random(42); ids = list(range(15)); r.shuffle(ids)`
    /// → [8, 13, 7, 6, 14, 12, 5, 2, 9, 3, 4, 11, 0, 1, 10].
    #[test]
    fn shuffle_matches_cpython_seed42() {
        let mut ids: Vec<i32> = (0..15).collect();
        let mut r = PyRandom::new(42);
        r.shuffle(&mut ids);
        assert_eq!(
            ids,
            vec![8, 13, 7, 6, 14, 12, 5, 2, 9, 3, 4, 11, 0, 1, 10]
        );
    }

    /// `[random.Random(7).getrandbits(32) for _ in range(5)]`.
    #[test]
    fn getrandbits_seed7_stream() {
        let mut r = PyRandom::new(7);
        let got: Vec<u32> = (0..5).map(|_| r.getrandbits(32) as u32).collect();
        assert_eq!(
            got,
            vec![1390851128, 4071050724, 647892279, 1695753998, 2795742288]
        );
    }

    #[test]
    fn bit_length_matches_python() {
        // (0).bit_length()==0, (1)==1, (2)==2, (255)==8, (256)==9
        assert_eq!(bit_length(0), 0);
        assert_eq!(bit_length(1), 1);
        assert_eq!(bit_length(2), 2);
        assert_eq!(bit_length(255), 8);
        assert_eq!(bit_length(256), 9);
    }

    #[test]
    fn shuffle_trivial_lengths() {
        let mut empty: Vec<i32> = vec![];
        PyRandom::new(0).shuffle(&mut empty);
        assert!(empty.is_empty());
        let mut one = vec![42];
        PyRandom::new(0).shuffle(&mut one);
        assert_eq!(one, vec![42]);
    }
}
