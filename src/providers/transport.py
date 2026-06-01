"""Transport protocols.

A `Transport` is the PURE request/response shaping for one provider wire format.
It performs NO I/O and holds NO global state — `llm_core` owns the httpx client,
cache, cooldown, retry, and fallback, and drives transports.

Streaming is decoded by a per-stream, stateful (but still I/O-free)
`StreamDecoder`: `llm_core` reads raw SSE lines off the wire and feeds them in
one at a time; the decoder returns the normalized SSE chunks to forward to the
client and a `stop` flag when the provider signals end-of-stream.
"""
from typing import Dict, List, Optional, Protocol, Tuple, runtime_checkable


class StreamDecoder(Protocol):
    """Stateful, I/O-free decoder for one streaming response."""

    def decode_line(self, line: str) -> Tuple[List[str], bool]:
        """Consume one raw SSE line. Return (chunks_to_yield, stop)."""
        ...

    def finalize(self) -> List[str]:
        """Chunks to emit when the line stream ends without an explicit stop."""
        ...


@runtime_checkable
class Transport(Protocol):
    id: str

    def target_url(self, url: str) -> str:
        """Map the configured endpoint URL to the wire URL for this provider."""
        ...

    def build_payload(
        self,
        model: str,
        messages: List[Dict],
        temperature: float,
        max_tokens: int,
        *,
        stream: bool = False,
        tools: Optional[List[Dict]] = None,
    ) -> Dict:
        ...

    def build_headers(self, headers: Optional[Dict]) -> Dict:
        ...

    def parse_response(self, data: Dict) -> str:
        ...

    def stream_decoder(self, model: str) -> StreamDecoder:
        ...
