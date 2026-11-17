"""Bounded GGUF v2/v3 metadata observation; no tensor/model loading.

Format: https://github.com/ggml-org/ggml/blob/master/docs/gguf.md
This is a planning hint reader, not a full GGUF or runtime compatibility validator.
"""
import os
from pathlib import Path
import re
import struct

_FORMATS = {0: 'B', 1: 'b', 2: 'H', 3: 'h', 4: 'I', 5: 'i', 6: 'f',
            7: '?', 10: 'Q', 11: 'q', 12: 'd'}


class _Metadata:
    def __init__(self, handle, budget):
        self.handle = handle
        self.limit = min(os.fstat(handle.fileno()).st_size, budget)
        self.remaining_values = 262144

    def advance(self, size, read=False):
        if size < 0 or self.handle.tell() + size > self.limit:
            raise ValueError('GGUF metadata truncated or exceeds planning budget')
        if read:
            data = self.handle.read(size)
            if len(data) != size:
                raise ValueError('GGUF metadata truncated')
            return data
        self.handle.seek(size, 1)

    def read(self, size) -> bytes:
        data = self.advance(size, read=True)
        assert data is not None
        return data

    def number(self, fmt):
        return struct.unpack('<' + fmt, self.read(struct.calcsize('<' + fmt)))[0]

    def string(self, keep=False, maximum=None):
        size = self.number('Q')
        if maximum is not None and size > maximum:
            raise ValueError('Oversized metadata key')
        if keep:
            return self.read(size).decode('utf-8')
        self.advance(size)

    def value(self, kind, keep=False, depth=0):
        self.remaining_values -= 1
        if self.remaining_values < 0 or depth > 4:
            raise ValueError('GGUF metadata work limit')
        if kind == 8:
            return self.string(keep=keep)
        if kind == 9:
            subtype, count = self.number('I'), self.number('Q')
            if subtype not in {*_FORMATS, 8, 9}:
                raise ValueError('Unsupported or oversized metadata array')
            if subtype in _FORMATS:
                self.advance(struct.calcsize('<' + _FORMATS[subtype]) * count)
            else:
                if count > self.remaining_values:
                    raise ValueError('GGUF metadata work limit')
                for _ in range(count):
                    self.value(subtype, depth=depth + 1)
            return None
        if kind not in _FORMATS:
            raise ValueError('Unknown GGUF metadata type')
        value = self.number(_FORMATS[kind])
        return value if keep else None


# Empirical upper bound for llama.cpp's recurrent (SSM/delta-net) state per recurrent layer,
# measured on a qwen35 9B model (about 7.8 MiB/layer). Deliberately rounded up.
_RECURRENT_STATE_BYTES_PER_LAYER = 8 * 1024 * 1024
_HYBRID_ARCHITECTURES = {'qwen35'}


def _dense_kv_estimate(values, architecture):
    hybrid = architecture in _HYBRID_ARCHITECTURES
    if architecture not in {'llama', 'qwen2', 'qwen3', 'gemma', 'gemma2', 'gemma3'} and not hybrid:
        return None  # Never apply ordinary attention geometry to MLA/unknown SSM.
    def field(name, default=None):
        return values.get(architecture + '.' + name, default)
    layers, embedding, heads = field('block_count'), field('embedding_length'), field('attention.head_count')
    kv_heads = field('attention.head_count_kv', heads)
    if any(type(value) is not int or not 0 < value <= 1048576 for value in (layers, embedding, heads, kv_heads)):
        return None
    if kv_heads > heads or heads % kv_heads:
        return None
    default_dim = embedding // heads if embedding % heads == 0 else None
    key, value = field('attention.key_length', default_dim), field('attention.value_length', default_dim)
    if any(type(size) is not int or not 0 < size <= 1048576 for size in (key, value)):
        return None
    if hybrid:
        interval, mtp = field('full_attention_interval'), field('nextn_predict_layers', 0)
        if any(type(v) is not int or v < 0 for v in (interval, mtp)) or not 0 < (interval or 0) <= layers:
            return None
        repeating = layers - mtp
        attention = repeating // interval
        if repeating <= 0 or attention <= 0:
            return None
        return {'source': 'gguf-hybrid-f16-assumption', 'architecture': architecture,
                'bytes_per_token': 2 * attention * kv_heads * (key + value), 'layers': layers,
                'attention_layers': attention,
                'fixed_bytes': (repeating - attention) * _RECURRENT_STATE_BYTES_PER_LAYER}
    return {'source': 'gguf-dense-f16-assumption', 'architecture': architecture,
            'bytes_per_token': 2 * layers * kv_heads * (key + value), 'layers': layers, 'fixed_bytes': 0}


def planning_metadata(path: Path, *, max_bytes=8 * 1024 * 1024) -> dict:
    """Bounded observations, not a declaration of runtime layout or hardware fit."""
    if type(max_bytes) is not int or not 0 < max_bytes <= 8 * 1024 * 1024:
        return {}
    suffixes = ('.context_length', '.block_count', '.embedding_length', '.attention.head_count',
                '.attention.head_count_kv', '.attention.key_length', '.attention.value_length',
                '.full_attention_interval', '.nextn_predict_layers')
    try:
        with Path(path).open('rb') as handle:
            reader = _Metadata(handle, max_bytes)
            if reader.read(4) != b'GGUF' or reader.number('I') not in (2, 3):
                return {}
            reader.number('Q')
            count = reader.number('Q')
            if count > 4096:
                return {}
            seen, values, architecture = set(), {}, None
            for _ in range(count):
                key = reader.string(keep=True, maximum=65535)
                if not isinstance(key, str) or not key.isascii() or key in seen:
                    return {}
                seen.add(key)
                kind = reader.number('I')
                if key.startswith('tokenizer.') and architecture and kind == 9:
                    # Hyperparameters precede the multi-MB vocabulary; a sane-looking
                    # array header ends the scan, an absurd one makes everything unknown.
                    subtype, count = reader.number('I'), reader.number('Q')
                    if subtype not in {*_FORMATS, 8} or count > 2 ** 24:
                        return {}
                    break
                value = reader.value(kind, keep=key == 'general.architecture' or key.endswith(suffixes))
                if key == 'general.architecture':
                    architecture = value if kind == 8 else None
                elif key.endswith(suffixes):
                    values[key] = value if kind in (4, 10) else None
            if not isinstance(architecture, str) or not re.fullmatch(r'[a-z0-9]{1,64}', architecture):
                return {}
            limit = values.get(architecture + '.context_length')
            return {'declared_context_length': limit if type(limit) is int and 0 < limit <= 2**31 - 1 else None,
                    'kv_cache_estimate': _dense_kv_estimate(values, architecture)}
    except (OSError, ValueError, struct.error):
        return {}


def declared_context_length(path: Path, *, max_bytes=8 * 1024 * 1024) -> int | None:
    return planning_metadata(path, max_bytes=max_bytes).get('declared_context_length')
