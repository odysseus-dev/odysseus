# Google Gemini Provider Shape

Last updated: dev@e71f8ce | 2026-08-25

## Scope

Canonical provider ID `google`; native GenerateContent plus optional Google
OpenAI-compatible chat; readers `google.py` and
`google_ai_studio_mapping.py`; catalog/probe ownership in
`routes/model_routes.py`.

## Catalog Shape

Use the native paginated `GET /v1beta/models` endpoint, including
`nextPageToken`, with `x-goog-api-key` when configured. `models[]` can contain:

- `name`, `baseModelId`, `version`, and `displayName`;
- `inputTokenLimit` and `outputTokenLimit`;
- `supportedGenerationMethods`;
- `thinking`, `temperature`, `maxTemperature`, `topP`, and `topK`.

Embedding-only methods map to embedding. Generation methods prove a native
method, not chat/image/video/audio modality, so those records remain unknown
unless stronger structured evidence exists. `thinking: true` and explicit
sampling fields map to a reasoning claim and controls. Model IDs such as
Imagen, Veo, or TTS names are not parsed.

## Request And Response Shape

Native generation uses `contents`, `systemInstruction`,
`generationConfig`, `tools[].functionDeclarations`, and
`models/{model}:generateContent|streamGenerateContent`. Responses use
`candidates[].content.parts[]` for `text`, `functionCall`, `functionResponse`,
`thought`, and `thoughtSignature`; token accounting is in `usageMetadata`.
Native Google tool/thought continuity must not be flattened through an
OpenAI-only history shape.

## Fallback And Safety

Prefer native model metadata even when chat is configured through Google's
OpenAI compatibility URL. Pagination parameters must remain stable between
pages. The route probe activates only for the exact
`generativelanguage.googleapis.com` hostname, filters the picker list to
content-generation methods, returns no curated fallback after probe failure,
and defaults those endpoints to manual catalog refresh unless explicitly
overridden. The canonical Google reader is not yet called by that probe.
Unknown methods and fields stay raw; unrecognized prediction models remain
unknown.

## Current Gaps

- The Models resource does not expose full modalities for every Google media
  family.
- Native Gemini request/response support is not yet the only runtime path.
