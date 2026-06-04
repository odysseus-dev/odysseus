# Gallery, Editor, And Media

Last updated: dev@a3cb15d | 2026-06-06

## Scope

This spec covers media surfaces in:

- app route registration and generated-file serving in `app.py`;
- canonical models in `core/database.py`, with `src.database` as a compatibility import path;
- `routes/gallery_routes.py` and `routes/gallery_helpers.py`;
- generated-image writers in `src/ai_interaction.py` and `mcp_servers/image_gen_server.py`;
- image tool schemas/dispatch/implementations in `src/tool_schemas.py`, `src/tool_execution.py`, and `src/tool_implementations.py`;
- `routes/editor_draft_routes.py`;
- `routes/signature_routes.py` and document signature consumers in `routes/document_routes.py`;
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
- agent `generate_image` and stale `edit_image` tool paths;
- MCP image-generation rows/files;
- image editor AI tools and model endpoint pickers;
- document PDF signing with stored signatures;
- visual-report hero/section image insertion and research hide/unhide controls;
- emoji picker/markdown emoji SVG proxy calls;
- theme custom-font loading;
- local gallery/signature CLI inspection.

## Gallery

`routes.gallery_routes` owns gallery upload/import/library/editor transform behavior: upload dedupe, image/video extension handling, EXIF extraction for images, albums, favorites, tags, generated media metadata, search/filter/sort, owner filtering, ZIP downloads, soft delete, disk cleanup, and chat-history cleanup after image delete.

Frontend gallery behavior includes upload progress, folder-drop album import, stale-while-revalidate cards, saved editor projects, detail actions, bulk delete/download, and cache-busted image refreshes.

Generated media provenance:

- generated filenames are opaque hex-like media names, not trusted content hashes;
- upload `file_hash` is a separate metadata field;
- generated files live under `data/generated_images`;
- chat image generation writes files and inserts `GalleryImage` rows through `src.ai_interaction`;
- MCP image generation can create ownerless rows/files;
- generated-but-not-yet-imported images can have no gallery row;
- once a gallery row exists, owner checks decide visibility where the route enforces them.

`app.py` owns direct `/api/generated-image/{filename}` serving through `src.generated_images.resolve_generated_image_path()`. It validates hex-like image/video filenames, rejects path escape and missing files, serves rowless generated files, checks row owner when a row exists, allows null-owner compatibility rows, and uses immutable/nosniff cache headers. Replace/rotate/save-over-original flows can mutate bytes under the same filename, so frontend cache busting matters.

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

AI image endpoints mostly require image-generation privilege in the gallery route layer. Utility routes such as sharpen have separate coverage and should not be assumed to share that gate. The chat image-generation session path calls `do_generate_image()` separately and has its own privilege/tool-listing behavior.

Provider behavior:

- OpenAI image edits use multipart `/images/edits`, mask conversion, size coercion, model restrictions, and source compositing where needed;
- diffusion/self-hosted paths use JSON APIs such as inpaint, img2img, variations, harmonize, or A1111-compatible fallbacks;
- client-supplied endpoint URLs on selected routes must pass outbound endpoint validation; DB-selected image endpoints are admin-trusted today and do not all re-run outbound validation at call time;
- editor model pickers load `/api/model-endpoints` and classify image-capable endpoints.

Optional dependency behavior:

- Pillow-backed paths are effectively core for EXIF, rotate, sharpen, and image preparation;
- Real-ESRGAN powers denoise/upscale when installed and otherwise returns install guidance;
- remove-bg tries `rembg`, then transformers-style fallback, then an error;
- face enhancement falls back from GFPGAN/OpenCV toward PIL behavior;
- video uploads intentionally skip EXIF/ffprobe metadata today.

## Editor Drafts

`routes.editor_draft_routes` owns server-backed image editor project payloads. `EditorDraft` rows store title, payload JSON, thumbnail, source image, timestamps, and owner.

Frontend editor behavior is split across `static/js/editor/*` and `static/js/galleryEditor.js`: canvas state, layer panel, masks, history, snapping, stroke pipeline, inpaint/rembg/harmonize tools, AI tool runner, model pickers, import wiring, topbar controls, auto-save, resume by draft ID or source image, draft-only open, and cleanup after close.

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
- generated-file serving allows rowless files and null-owner compatibility rows;
- uploads are byte-limited and extension-gated, but content sniffing is partial;
- several base64 JSON editor routes accept large decoded image payloads and need route-level size discipline;
- gallery DB filenames should be joined through shared generated-media path helpers before filesystem operations;
- editor draft source image IDs, payloads, and thumbnails are owner-scoped by draft owner but do not fully validate source-gallery ownership or payload size;
- emoji proxy constrains codepoint filenames and degrades invalid, unknown, or unreachable SVGs to transparent no-store placeholders, but remote SVG content still deserves security review;
- visual report Markdown HTML/link/image output needs continued sanitization coverage.

## Degraded And Compatibility Behavior

- Uploaded images record display dimensions with EXIF orientation when possible; EXIF failures warn/degrade.
- Video uploads skip EXIF and have no metadata extraction yet.
- Missing generated files are skipped in ZIP downloads; if all are missing, the route returns no files found.
- AI tagging can fail when disk files are missing.
- Static JS/CSS/HTML assets revalidate because there is no frontend build/versioning.
- Gallery/editor frontend state includes stale-while-revalidate and listener cleanup to avoid stale handlers.
- `edit_image` tool schema/implementation currently appears stale against implemented `/api/image/*` and `/api/gallery/*` routes.

## Testing Coverage

Existing tests cover EXIF dimensions, owner-filter helper behavior, direct upload limits, image-generation privilege source shape, endpoint SSRF/source checks, editor draft payload validation, font family derivation, visual-report helper behavior, gallery CLI previews, and selected security regressions.

Route-level coverage is thin for full gallery CRUD/album/tag/download/delete flows, generated-image serving, editor draft owner CRUD, signature owner CRUD, emoji proxy/cache behavior, image-tool degraded responses, optional dependency fallbacks, and frontend editor behavior.

## Current Gaps

- Owner-scoped endpoint-key resolution is needed for image proxy/editor routes.
- Media routes need a clear API-token policy: reject token callers, or implement owner/scope handling.
- Generated-image serving needs live route tests for invalid filenames, rowless files, owned rows, null-owner rows, MIME/cache headers, and cross-owner behavior.
- Mutable generated filenames plus immutable cache headers need cache-busting tests for replace/save-over-original flows.
- Media upload/content sniffing and base64 JSON payload size limits need hardening.
- MCP image generation needs an owner attribution decision or explicit admin-only documentation.
- `edit_image` tool route mapping appears stale.
- Emoji SVG proxy/cache and visual-report raw HTML/link sanitization need stronger tests.
- Optional image dependency fallbacks are mostly untested.
