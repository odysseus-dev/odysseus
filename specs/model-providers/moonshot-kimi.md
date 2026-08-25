# Moonshot And Kimi Provider Shape

Last updated: dev@28d27ee | 2026-07-17

## Scope

Provider IDs `moonshot` for official Moonshot API and `kimi_code` for the Kimi
Code surface; OpenAI-compatible transport with provider-specific headers and
model-specific behavior in `src/llm_core.py`.

## Shape And Observations

Model lists use the general OpenAI-compatible identity shape unless a richer
account response is returned. Official Kimi K2.5/K2.6 fixes temperature by
thinking mode, so Odysseus omits `temperature` rather than sending an invalid
value (#3960). Thinking tool-call continuation requires preservation of
assistant `reasoning_content` (#3118). Kimi Code negotiates a small exact
User-Agent set on 403 and caches the accepted value; this is provider transport,
not model capability.

Reports distinguish K2.5/K2.6 multimodality from older K2 variants (#2522).
Promote those claims only through exact structured model IDs/families, not a
`kimi` name match.

## Fallback And Current Gaps

Keep Moonshot and Kimi Code identities distinct even when both use OpenAI Chat.
Self-hosted Kimi checkpoints inherit their serving engine shape, not official
Moonshot sampling rules. The provider catalog does not yet yield a complete
canonical capability card.
