# Gallery, Editor, And Media

Last updated: dev@c74f475 | 2026-10-15

## Scope

This spec covers media surfaces in:

- app route registration and generated-file serving in `app.py`;
- canonical models in `core/database.py`, with `src.database` as a compatibility import path;
- canonical route package `routes/gallery/gallery_routes.py` and `routes/gallery/gallery_helpers.py`, with top-level `routes/gallery_routes.py` and `routes/gallery_helpers.py` compatibility shims;
- generated-image writers in `src/ai_interaction.py` and `mcp_servers/image_gen_server.py`;
- local MLX image compatibility server `scripts/mlx_image_server.py`;
- image tool schemas/dispatch/implementations in `src/tool_schemas.py`, `src/tool_execution.py`, and `src/tool_implementations.py`;
- `routes/editor_draft_routes.py`;
- `routes/signature_routes.py` and document signature consumers in canonical `routes/document/document_routes.py`;
- `routes/emoji_routes.py`;
- `routes/font_routes.py`;
- `src/generated_images.py`;
- `src/visual_report.py` plus research image hide/unhide routes;
- database models `GalleryImage`, `GalleryAlbum`, `EditorDraft`, and `Signature`;
- generated files under `data/generated_images`;
- frontend modules `static/js/gallery.js`, `static/js/galleryEditor.js`, `static/js/editor/*`, `static/js/signature.js`, `static/js/emojiPicker.js`, `static/js/chatRenderer.js`, `static/js/document.js`, `static/js/markdown.js`, and `static/js/theme.js`;
- CLI surfaces `scripts/odysseus-gallery` and `scripts/odysseus-signature`;
- tests covering gallery helpers/routes, generated-image serving, editor drafts, signatures, visual reports, fonts, upload limits, and image endpoint security.

## Current Call Sites Include

- gallery upload, library, album, tag, favorite, ZIP, delete, and saved-project views;
- chat-generated image rendering/edit/delete bubbles;
- agent `generate_image` and owner-scoped `edit_image` tool paths;
- MCP image-generation rows/files;
- image editor AI tools and model endpoint pickers;
- document PDF signing with stored signatures;
- visual-report hero/section image insertion and research hide/unhide controls;
- emoji picker/markdown emoji SVG proxy calls;
- theme custom-font loading;
- local gallery/signature CLI inspection.

## Gallery

`routes.gallery.gallery_routes` owns gallery upload/import/library/editor transform behavior: upload dedupe, image/video extension handling, EXIF extraction for images, albums, favorites, tags, generated media metadata, search/filter/sort, owner filtering, ZIP downloads, soft delete, disk cleanup, and chat-history cleanup after image delete. Top-level `routes.gallery_routes` is a `sys.modules` compatibility shim to the canonical module.

Frontend gallery behavior includes upload progress, folder-drop album import, stale-while-revalidate cards, saved editor projects, detail actions, bulk delete/download, and cache-busted image refreshes.

Album assignment and gallery image detail/update endpoints enforce owner scope and fail closed when no authenticated owner is available instead of falling back to broad access.

Generated media provenance:

- generated filenames are opaque hex-like media names, not trusted content hashes;
- upload `file_hash` is a separate metadata field;
- generated files live under `data/generated_images`;
- chat image generation writes files and inserts `GalleryImage` rows through `src.ai_interaction`;
- MCP image generation stamps the trusted dispatched owner when present, trims empty owner values to null, and can still create ownerless rows/files without that context;
- generated-but-not-yet-imported images can have no gallery row;
- once a gallery row exists, owner checks decide visibility where the route enforces them.

`app.py` owns direct `/api/generated-image/{filename}` serving through `src.generated_images.resolve_generated_image_path()`. It validates filename syntax before ownership and disk probing. With auth enabled, `effective_user()` resolves cookie/API-token owners. A live registry entry is checked first: a matching owner authorizes, while a mismatch returns 404 without database fallback. Only absent registration reaches the gallery lookup: a nonempty row owner must match; a row without an owner instead requires a matching linked-session owner. If that lookup finds no gallery row, it requires a chat metadata reference in an owned session. Unattributed/mismatched files return 404 and database verification failures return 503. Disabled auth skips ownership verification. Confined path/existence checks follow authorization, and responses use `private, no-cache, must-revalidate` plus nosniff headers. Gallery replace/rotate/save/delete/ZIP paths also resolve filenames through a shared generated-image path helper so database filenames cannot escape `data/generated_images`. Replace/rotate/save-over-original flows can mutate bytes under the same filename, so frontend cache busting matters.

`src/generated_images.py` owns a lock-protected, process-local one-hour recent-owner registry. Image generation/edit writers in `src/ai_interaction.py` register trusted owners after writing bytes; the image MCP server stamps gallery ownership and registers after commit when an owner exists. Registry entries are ephemeral, not durable gallery ownership, and matching registry access can survive a database outage. Tool dispatch strips caller-reserved attribution keys before injecting verified owner context, including qualified image MCP and direct fallback paths.

## Image Tools And Providers

Gallery/editor image transforms are split across:

- `/api/gallery/ai-upscale` and `/api/gallery/style-transfer`;
- `/api/image/inpaint`;
- `/api/image/harmonize`;
- `/api/image/sharpen`;
- `/api/image/denoise`;
- `/api/image/upscale-local`;
- `/api/image/remove-bg`;
- `/api/image/enhance-face`.

AI image endpoints mostly require image-generation privilege in the gallery route layer. The sharpen route is explicitly auth-gated; utility routes that live outside gallery still need their own route-level gate checks rather than assuming a shared decorator. The chat image-generation session path calls `do_generate_image()` separately and has its own privilege/tool-listing behavior.

Provider behavior:

- OpenAI image edits use multipart `/images/edits`, mask conversion, size coercion, model restrictions, and source compositing where needed;
- diffusion/self-hosted paths use JSON APIs such as inpaint, img2img, variations, harmonize, or A1111-compatible fallbacks;
- client-supplied endpoint URLs on selected routes must pass outbound endpoint validation; DB-selected image endpoints should be resolved through owner-visible endpoint queries before decrypted headers/keys are used;
- provider-returned image result URLs are validated with `src.url_safety.check_outbound_url()` before server-side download, with private-IP blocking controlled by image-route settings;
- AI endpoint path suffixes are allowlisted before proxy/download use so arbitrary endpoint paths cannot be selected through gallery/editor requests;
- editor model pickers load `/api/model-endpoints` and classify image-capable endpoints.

Optional dependency behavior:

- Pillow-backed paths are effectively core for EXIF, rotate, sharpen, and image preparation;
- Real-ESRGAN powers denoise/upscale when installed and otherwise returns install guidance; import-time torchvision compatibility patches run before Real-ESRGAN imports;
- remove-bg tries `rembg`, then transformers-style fallback, then an error;
- face enhancement falls back from GFPGAN/OpenCV toward PIL behavior;
- video uploads intentionally skip EXIF/ffprobe metadata today.
- grounding and mask model inputs cast only `float64` tensors to `float32` before transfer to Apple's MPS backend, because MPS rejects float64; integer/other tensors and non-tensor processor values preserve their normal device-transfer behavior.

## Editor Drafts

`routes.editor_draft_routes` owns server-backed image editor project payloads. `EditorDraft` rows store name, dimensions, payload JSON, thumbnail, source image, timestamps, and owner.

Frontend editor behavior is split across `static/js/editor/*` and `static/js/galleryEditor.js`: canvas state, layer panel, masks, history, snapping, stroke pipeline, inpaint/rembg/harmonize tools, AI tool runner, model pickers, an AI edit command box that routes natural-language edit requests into existing inpaint/remove/upscale/background/style actions where possible, import wiring, topbar controls, auto-save, resume by draft ID or source image, draft-only open, and cleanup after close. `static/js/panels.js` loads this module graph on first editor use, shares concurrent imports, retries failed loads, and `static/sw.js` keeps the lazy graph in a separate offline panel precache.

Draft compatibility behavior:

- v2 server drafts store payloads and thumbnails server-side;
- legacy/local raw payloads can still be restored by the frontend;
- PUT 404 can recreate a missing draft row;
- broken image drafts can fall back to the source image;
- final close persist is best-effort.

## Signatures, Emoji, Fonts

`routes.signature_routes` owns reusable signature/stamp rows. Signature image payloads are normalized to bounded PNG base64, encrypted at rest, and owner-filtered; SVG signature input is not preserved. Document PDF render/export paths owner-filter signature IDs before stamping.

`routes.emoji_routes` owns same-origin OpenMoji black SVG proxy/caching. It validates codepoint filenames, caches SVGs under `data/emoji_cache`, and returns transparent no-store SVGs for invalid, unknown, or unreachable codepoints. `static/js/emojiPicker.js` is a curated inline monochrome picker.

`routes.font_routes` owns deriving available custom font family names from static font files under `static/fonts/custom`.

## Visual Reports

`src.visual_report` owns generated research/report HTML image behavior: HTTPS Open Graph image filtering, hero images, section images, icon/logo filtering, hide/reroll client controls, and inline JSON escaping for scripts.

Research routes and handler code own hidden-image persistence. Visual reports render model/source-influenced Markdown to HTML, so raw HTML/link/image sanitization remains security-sensitive.

## Security Policy

Media routes are cookie/current-user surfaces unless they explicitly implement token owner/scope handling. Bearer-token callers that arrive as synthetic `api` users should not be treated as owner-scoped media API clients without explicit policy.

Known boundaries:

- image-generation routes require `can_generate_images`;
- image proxy/editor endpoints currently resolve client-selected, DB-selected, or fallback image model endpoints without full owner-scoped endpoint-key policy or uniform outbound revalidation;
- rowless generated files require an owned chat reference or recent registration; null-owner gallery rows require a matching owned session;
- uploads are byte-limited and extension-gated, with content sniffing available through `UploadHandler.detect_content_type()` when `python-magic`/`libmagic` is installed;
- several base64 JSON editor routes accept large decoded image payloads and need route-level size discipline;
- gallery DB filenames should be joined through shared generated-media path helpers before filesystem operations;
- editor drafts owner-scope rows for identified users and cap serialized payload bytes, but source-gallery ownership and thumbnail size still need separate validation;
- emoji proxy constrains codepoint filenames and degrades invalid, unknown, or unreachable SVGs to transparent no-store placeholders, but remote SVG content still deserves security review;
- visual report Markdown HTML/link/image output needs continued sanitization coverage.
- `scripts/mlx_image_server.py` pins generation/edit routing to the process-start model and ignores request-selected model names, preventing unauthenticated callers from selecting a local model directory/repository whose model-specific script or bridge would execute.

## Degraded And Compatibility Behavior

- Uploaded images record display dimensions with EXIF orientation when possible; EXIF failures warn/degrade.
- Video uploads skip EXIF and have no metadata extraction yet.
- Missing generated files are skipped in ZIP downloads; if all are missing, the route returns no files found.
- Soft delete commits the gallery row state before removing the disk file, so a failed DB write does not orphan a missing image row.
- AI tagging can fail when disk files are missing.
- Static assets revalidate; most editor modules remain static ES modules, while `frontend/modules.json` and the committed generated-output freshness gate govern the manifest-selected native ES modules published by byte-for-byte copying to committed deployment outputs.
- Gallery/editor frontend state includes stale-while-revalidate and listener cleanup to avoid stale handlers.
- `src/tools/image.py` implements `edit_image` in-process: prompt edits call the model adapter, upscale creates a 2x/4x Lanczos copy, and rembg creates a copy when the optional package is installed. It checks exact gallery owner and confined source path; uploaded attachment URIs support prompt edits after owner-aware resolution.

## Testing Coverage

Existing tests cover EXIF dimensions, owner-filter helper behavior, direct upload limits, image-generation privilege source shape, sharpen auth, gallery null-user denial, endpoint SSRF/source checks, editor draft payload validation, lazy editor loading/offline precache, MLX request-model pinning, font family derivation, visual-report helper behavior, gallery CLI previews, and selected security regressions.

Route-level coverage is thin for full gallery CRUD/album/tag/download/delete flows, generated-image serving, editor draft owner CRUD, signature owner CRUD, emoji proxy/cache behavior, image-tool degraded responses, optional dependency fallbacks, and full browser editor integration. Focused codec, layer/mask/group, transform, history, worker, and draft-budget regressions are present; this maintenance pass did not execute them.

## Versioned Editor Documents And Budgets

`static/js/editor/document-codec.js` is the shared project-file/autosave serialization boundary, currently document version 15. It migrates older versions, rejects future versions, validates layer/group/mask/selection and geometry data, and bounds canvas pixels, layer counts, total surfaces, and embedded image data. Masks, adjustments, effects, groups, vector text/shapes, placed layers and view/guide state pass through this codec rather than a second hand-written draft shape, subject to serialization/decode and history limitations below; this is not lossless persistence of every mask/effect property.

Editor responsibilities now include `document-geometry.js` and layer geometry for canvas transforms, `layer-groups.js`/`layer-selection.js` for grouping/multi-selection, `selection-mask.js` for selections, `text-layer.js`/`shape-layer.js` for vector content, and `adjustment-layer.js`/`effects.js` with worker paths for compositing operations. `history-budget.js` bounds raw ImageData history adaptively; rendering cancellation prevents stale asynchronous results from applying after newer edits.

`routes/editor_draft_routes.py` uses `EDITOR_DRAFT_MAX_BYTES` both for early rejection of oversized declared Content-Length on writes and for the authoritative serialized-payload byte check. Missing/malformed Content-Length still reaches the latter check after parsing. This cap does not supply source-gallery ownership or a separate thumbnail budget.

`src/media_ingress.py` separately builds bounded local multimodal attachments for supported image/video/audio/document paths with source hashes, encoding/dimension/frame limits, and artifact metadata. Agent evidence/trace consumers record ingress metadata; it is not the browser upload storage or editor serialization owner.

## Editor Persistence And Operation Limits

Codec limits count canvas/surface allocations and embedded strings rather than universally bounding decoded bytes. Failed canvas encoding or image restoration can omit/drop data; effect-mask normalization and history snapshots do not preserve every mask density/feather property. Async capture can span intervening edits, so it is not an immutable atomic pixel snapshot. History deduplicates shared pixel buffers and trims older snapshots to a budget while retaining the newest even when oversized; it does not serialize the entire history into a draft.

Draft autosave reserves one in-flight capture and uses session/generation checks for assigning returned ids, with PUT404 recreation. Those checks do not cancel an old server request. The current persist chain can clear an earlier error and display Saved after catch/early return; the badge is not a durable-write receipt. Project state uses server draft ids and a compact session descriptor rather than a full browser-sessionStorage payload. Restore can drop failed raster/mask/placed sources and is not a lossless project recovery promise.

Placed transforms, crop/resize/flip, linked versus document-space masks, grouped effects and rasterization retain distinct geometry paths. Duplicate/merge/copy operations do not guarantee independent preservation of every effect-mask/retained metadata field; raw layer copying differs from flattened canonical output. Pixel/position locks are checked on selected mutation paths rather than proving every control respects every lock. Escape cancellation is operation-specific (including crop/transform/filter prompts), not a universal rollback guarantee. Filter/worker generations prevent selected stale results, but shared intermediate canvases and Quick Edit busy-end polling do not establish atomic pixels or successful downstream AI work.

## Current Gaps

- Owner-scoped endpoint-key resolution is needed for image proxy/editor routes.
- Media routes need a clear API-token policy: reject token callers, or implement owner/scope handling.
- Direct handler ownership/cache tests cover generated-image denial and fallback branches; middleware-mounted serving, MIME/account-switch and live browser cache behavior still need broader checks.
- Private revalidation replaces immutable generated-media caching; replace/save-over-original and account-switch behavior still warrant browser cache tests.
- Base64 JSON editor payload size limits need hardening; upload content sniffing should keep native/Docker parity coverage as dependencies change.
- Image MCP now receives trusted server-dispatch ownership; direct out-of-band MCP callers and cross-process recent-registry behavior remain separate trust/availability limits.
- Emoji SVG proxy/cache and visual-report raw HTML/link sanitization need stronger tests.
- Optional image dependency fallbacks are mostly untested.
