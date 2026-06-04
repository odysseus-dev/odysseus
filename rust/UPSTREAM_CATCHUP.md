<!-- Generated 2026-06-03 by a 142-file fan-out audit: each changed Python
     server file was diffed 051751a..7c7ac10 and compared to the Rust port. -->
<!-- Base (port was written against): 051751a | Target (current origin/main): 7c7ac10 -->

# Rust server parity catch-up (051751a -> 7c7ac10)

## Summary

Of the ~125 changed Python server files audited, **84 need a Rust change** (`needs_change`), **2 are not ported** (`not_ported`), **2 are out of scope** (`out_of_scope`), and the remainder are **already covered or trivial**. By severity, the `needs_change` items break down as roughly **37 high**, **27 medium**, and **20 low**. The high-severity work is dominated by security/multi-tenant isolation gaps (auth-gate loopback hardening, owner scoping across documents/sessions/email/memory/topics, SSRF validation, search-config secret leak, webhook session-ownership) plus large feature ports (model_routes overhaul, cookbook serve/scan, hwfit ranking/hardware/profiles, llm_core/agent_loop provider support).

---

## High priority

**Auth/Security**
- `rust/src/web/mod.rs` — auth_gate still uses bare `matches!(client, 127.0.0.1|::1)` loopback trust (no proxy-header exclusion), plain `==` token compare, impersonation with no existence check, and no webhook regex; lets remote visitors behind a Cloudflare tunnel inherit local trust -> add `is_trusted_loopback(&req)` (127.0.0.1/::1 AND none of cf-connecting-ip, cf-ray, cf-visitor, x-forwarded-for, x-forwarded-host, x-real-ip, forwarded present) and use it in both the INTERNAL_TOOL_TOKEN bypass and LOCALHOST_BYPASS branch; gate impersonation on owner existing in auth users; add `^/api/tasks/[^/]+/webhook/[^/]+/?$` to is_auth_exempt.
- `rust/src/core/auth.rs` — six AuthManager gaps: TOTP fails open when 2FA enabled but secret missing, no RESERVED_USERNAMES guard (account named "internal-tool" becomes silent admin), missing rename_user/revoke_user_sessions, no setup lock, no username normalization -> totp_verify returns false on missing secret; add RESERVED_USERNAMES + empty-username rejection in create_user; add rename_user/revoke_user_sessions; guard setup() with a mutex; trim-lowercase stored usernames in _load().
- `rust/src/src/auth_helpers.rs` — require_user never returns "" for AUTH_ENABLED=false or LOCALHOST_BYPASS+loopback (so guarded routes 401 in Docker/reverse-proxy, issue #622), and no effective_user attributes Bearer ody_ tokens to their owner -> add the AUTH_ENABLED=false + LOCALHOST_BYPASS branches to require_user, and add effective_user (thread api_token/api_token_owner from web/mod.rs's ApiToken extension).
- `rust/src/routes/diagnostics_routes.rs` — all four diagnostics endpoints (/api/db/stats, /api/rag/stats, /api/test/youtube, /api/test-research) are reachable without admin -> extract HeaderMap+State, call `crate::core::middleware::require_admin(...)`, return 403 on Err.
- `rust/src/routes/embedding_routes.rs` — entire /api/embeddings router is ungated and set_endpoint does no SSRF validation -> add admin gate to all 7 handlers, and port `check_outbound_url` (new url_safety module) into set_endpoint gated on EMBEDDING_BLOCK_PRIVATE_IPS (400 "Rejected endpoint URL: {reason}").
- `rust/src/routes/auth_routes.rs` — change_password does not revoke other sessions, integration create/update leak raw api_key, no rename/open-signup endpoints -> call new revoke_user_sessions after change_password; wrap create/update_integration responses in mask_integration_secret; add admin-only `PUT /api/auth/users/:username/rename` and `PUT /api/auth/open-signup`.
- `rust/src/routes/session_routes.rs` — many gaps: token callers create/see "api"-owned data, no ghost-session fallback, cross-user auto-sort, missing endpoint validation/SSRF guard, stored XSS in HTML export, suffix model filter -> add owner scoping to auto-sort SELECT/UPDATE; html-escape session.name in export + sanitize filename; change archived filter to escaped `%{safe}%`; thread owner into resolve_task_endpoint/pick_endpoint_for_sort/resolve_endpoint; add is_enabled+owner_filter+400+build_chat_url/normalize_base+header-clear to create/update_session plus _reject_raw_endpoint_url_for_non_admin (403); add effective_user; add in-memory ghost fallback to verify_session_owner.
- `rust/src/routes/webhook_routes.rs` — sync_chat skips ownership check on null-owner sessions, uses unscoped first_enabled() fallback, no SSRF validation, missing providers -> strict `_caller_owns_session` gate (404 unless tok_user equals sess.owner, fail closed on null/empty); owner-scope fallback endpoint; add `url_security::validate_public_http_url` on token-supplied base_url (400); add ollama/venice to KNOWN_PROVIDERS; route URL/header building through endpoint_resolver normalize_base/build_chat_url/build_headers (+build_models_url); fall back to models[].name/.model in discover_first_model.
- `rust/src/routes/history_routes.rs` — mark-stopped and update-last-meta always return 500 (faithful AttributeError bug), merge-last-assistant 500s instead of merging, hidden messages leak on DB-fallback path, topics endpoint has no 401 guard -> replace the hard-coded 500s with real DB logic ordered by `timestamp`; filter metadata.hidden rows from the get_history response (keep full set for write-back); gate get_conversation_topics on require_user semantics (401 + owner=None when empty).

**Search**
- `rust/src/src/search/core.rs` — get_search_config leaks decrypted Brave key through unauthenticated GET /api/search/config; invalidate_search_cache hardcodes `|10|None`; comprehensive_web_search numbers content blocks by completion order -> set has_api_key via `_get_provider_key`, strip string-valued secret keys (*_api_key/_key/_token/_secret) before returning, stop inserting brave_api_key in update_search_config; use `_get_result_count()` in the cache key; tag/sort fetched blocks by source index.
- `rust/src/src/search/core.rs` (also surfaced as the src/search/core.py shim) — same three fixes (secret stripping, cache-key `_get_result_count()`, source-index labeling); both Python copies map to this one file.

**Models/LLM**
- `rust/src/routes/model_routes.rs` — entirely at base state, missing the full overhaul: no pinned_models, no Docker loopback rewrite, no _ping_endpoint/status, no /model-endpoints/test, no Ollama curated/_host_match/api-tags, no _restricts_temperature gate, still uses base+"/models" and old suffix-strip, /providers lacks require_admin, models-list lacks _auth_disabled, delete doesn't clear sessions/prefs -> port pinned_models support, _ping_endpoint + status, POST /api/model-endpoints/test, Docker loopback rewrite, Ollama support, _restricts_temperature gate, build_models_url/build_headers in probes, add-endpoint dedup+_normalize_base, require_admin on /providers, _auth_disabled gate, in-place PATCH, session/pref cleanup on delete.
- `rust/src/src/llm_core.rs` — large multi-feature rewrite missing: native Ollama provider, hostname-based _detect_provider (ollama/openrouter/groq) + _host_match, OpenRouter headers, _restricts_temperature, "gemma" thinking pattern, Anthropic temp clamp + prompt caching, multi-text-block parse, _sanitize_llm_messages, _dedupe_candidates, reasoning_content fallback, "data:" no-space SSE, tool_call index=None/extra_content, stream_options gating, timings passthrough, fallback notice -> port all listed behavior (the thread-safety lock part is already covered).
- `rust/src/src/endpoint_resolver.rs` — missing model-selection helpers, hidden-model discard + enabled-model auto-pick (the headline Groq fix), OpenRouter/anthropic-version headers, ollama branch + build_models_url, extra normalize_base suffixes, fallback_url short-circuit, owner threading -> port _endpoint_cached/hidden/enabled_models, apply hidden-discard + first-enabled backfill, add headers/ollama/build_models_url/suffix-strip, thread owner; prerequisite: update llm_core.rs _detect_provider + _host_match.
- `rust/src/src/ai_interaction.rs` — do_send_to_session lacks owner + cross-owner rejection, resolve_model_spec/do_list_models hardcode base+"/models"/Bearer and only parse data[], do_ui_control lacks "rag" toggle -> add owner param + rejection and pass it at dispatch; route through endpoint_resolver build_models_url/build_chat_url/build_headers with ollama models[].name/.model fallback; add "rag" to valid toggles.
- `rust/src/src/model_discovery.rs` — get_hosts omits host.docker.internal and env hosts; discover_models scans only 8000..=8020; no provider fingerprint -> add extra_ports field, inject host.docker.internal + parsed OLLAMA_BASE_URL/OLLAMA_URL/LM_STUDIO_URL hosts in all three branches, scan 1234/11434/extra_ports, add fingerprint_provider probing /api/v1/models.

**HWFit**
- `rust/src/services/hwfit/fit.rs` — ranking/scoring engine entirely at old behavior -> extend GPU_BANDWIDTH (9060, Apple m1-m5) and FALLBACK_K ("metal":150); add offload_frac harmonic blend to estimate_speed; add architecture_bonus + new penalties; extend quant_bits; add native_quant(); add scoring_use_case/target_context/fit_only with multi-GPU BF16+GGUF skip, prequant native gating, native_gpu_only RAM zeroing, version_key tiebreaker, "newest" sort, expanded native-format filters, MLX/ROCm/Apple/consumer-AMD hiding, always-descending sort-then-truncate.
- `rust/src/services/hwfit/hardware.rs` — no Apple Silicon (Metal) detection so Macs misclassify as CPU-only; CACHE_TTL stale, no unified-memory NVIDIA, no AMD arch, no local-Windows, no macOS sysctl fallbacks -> bump CACHE_TTL to 86400, add detect_apple_silicon() first in the GPU chain, carry unified_memory through, add unified-memory NVIDIA handling, classify_amd_gfx + rocminfo arch, local-Windows branch, macOS sysctl fallbacks for ram/cpu name/cpu count, coerce cpu_cores/gpu_count + homogeneous/gpu_error.

**Email/IMAP**
- `rust/src/routes/email_helpers.rs` — attachment extract dir built from unsanitized folder/uid (path traversal), no smtp_security, no env-driven IMAP timeout, require_user missing AUTH_ENABLED=false branch, scheduled_emails missing owner column/index, pre_retrieve_context not owner-scoped, reply regex too strict -> add sanitizing attachment_extract_dir (regex-flatten + containment assert, 400) at the 3 call sites; port _smtp_security_mode + EmailConfig.smtp_security; env IMAP timeout; require_user AUTH_ENABLED=false branch; scheduled_emails owner column/index/backfill; thread account_id/owner through imap_move/fetch_sender_thread_context/pre_retrieve_context with admin-gated plural contacts; loosen REPLY_OPEN_RE/REPLY_CLOSE_RE to `>>+`.
- `rust/src/routes/email_pollers.rs` — no owner scoping (leaks/mutates other tenants' calendar/tags), uses sequence numbers not UID, missing poisoned-socket fallback, wrong reply budgets, no temperature restriction, no prompt-injection guard, no progress/counters -> thread owner via owner_for_email_account into imap/config/endpoint/move/calendar + email_tags + scheduled_poll_once; switch to UID SEARCH/FETCH with reversed ordering + SEARCH-ALL fallback (#1613); change processed>=10 to >=5, max_tokens 16384->1024 / timeout 240->90, skip pre_retrieve_context for background replies; apply restricts_temperature; add calendar prompt-injection guard; add progress callback + counters with finally-style logout.
- `rust/src/routes/email_routes.rs` — owner clause hardcodes `OR owner IS NULL` (cross-tenant leak), scheduled INSERT omits owner, list/cancel/unflag/suggest not owner-filtered, send_at not UTC-normalized, no smtp_security/mark_seen/SUBJECT/AI-reply-fast -> add email_tag_owner_clause helper used at 1189/1219/1317/1364/1641/1672; add owner column to scheduled INSERT + `AND owner = ?` to list/cancel; normalize send_at to naive-UTC; secondary: smtp_security, READ_TTL 1800 + mark_seen + warm-prefetch, SUBJECT term, getaddresses recipients, AI-reply cache/fast mode.

**Calendar/CalDAV**
- `rust/src/routes/calendar_routes.rs` — Rust port lacks the upstream changes -> port recurrence expansion and caldav changes.
- `rust/src/src/caldav_sync.rs` — no validate_caldav_url SSRF guard; sync_caldav feeds raw user URL into HTTP -> add validate_caldav_url (http/https only, require host, reject embedded creds/fragment, validate port, block localhost/*.localhost/ip6-localhost/metadata.google.internal + loopback/link-local/multicast/unspecified, private unless ODYSSEUS_ALLOW_PRIVATE_CALDAV, normalize) and call it in sync_caldav before spawn_blocking. (pending-dedupe + decrypt already covered / out of scope.)

**Chat**
- `rust/src/routes/chat_routes.rs` — resolve_active_document has no owner scoping (cross-user doc injection), web_fetch not gated, ORDER BY created_at (nonexistent column), thinking deltas folded into saved response, no fallback/answered_by handling, gen_tps not mapped in real-usage path, coarse image-model routing, no orphaned-endpoint/empty-model recovery -> add owner scoping to resolve_active_document; add web_fetch to disabled_tools/strip set; fix ORDER BY to timestamp; skip thinking:true deltas; handle type==fallback/answered_by as metrics model; map gen_tps->tokens_per_second + response_time; port _is_image_generation_session; add _clear_orphaned_session_endpoint + _recover_empty_session_model (400 guards).

**Memory/Skills**
- `rust/src/services/memory/memory_extractor.rs` — audit rebuilds vector index from only final_entries (wipes other owners' entries on each per-owner audit); no early-return on empty endpoint/model; no media stripping; vector find_similar/add use `?` and abort the whole batch on failure -> rebuild from saved_entries (final + other); early Ok(()) when endpoint_url/model empty; strip non-text content blocks into stripped_recent; match/Err-log on mv.find_similar/mv.add instead of `?`.
- `rust/src/services/memory/skills.rs` — no owner scoping anywhere (usage sidecar, lookups, dedup pool), no teacher-escalation fail-closed confidence gate, substring tag matching -> add usage_key(name,owner) + thread owner through set_audit/set_necessity/record_use/update_skill/delete_skill/read_skill_md/read_skill_reference (skip owner-mismatched skills), key usage/load by it, filter dedup pool by owner, drop owner from scalar updates, fail closed for source==teacher-escalation with missing/unparseable confidence, switch tag match to whole-token subset.
- `rust/src/src/builtin_actions.rs` — consolidate_memory_inner treats all memories as one list (no per-owner grouping), 600-char limit, no truncation guard; SKIP_PREFIXES has dead support@/info@/admin@; action_test_skills ignores owner; draft uses days_back=1 -> group memories per-owner when owner empty, raise limit 600->2000 with truncation protection, aggregate across groups; fix SKIP_PREFIXES to support/info/admin; thread owner into resolve_endpoint; pass days_back=7.

**Agent/Tools**
- `rust/src/src/agent_loop.rs`, `rust/src/src/agent_loop_web.rs` — many gaps: empty-string content + tool_calls (breaks Ollama/Gemini), no reasoning_content stripping, no extra_content replay, stale supports-tools list / no Ollama-native check, _API_HOSTS missing 5 entries, no gen_tps/tps_source metrics, 6000-token cap on long-context models, skills in trusted system prompt (injection), no fallback SSE branch, web_search output-key extraction, no empty-response fallback -> set assistant content to Null when empty; strip reasoning_content from prior turns; replay extra_content; update model_supports_tools (gemma/deepseek-v/deepseek-chat, drop bare deepseek), add deepseek-r1 blocklist + is_ollama_native_url; add 5 hosts to _API_HOSTS; thread backend gen_tps/prefill_tps + tps_source; replace context cap with compute_input_token_budget; add fallback branch; web_search output-first extraction; add _empty_response_fallback; move skills into untrusted_context_message + pass owner to record_use.

**Uploads/Docs**
- `rust/src/routes/document_helpers.rs` — locate_upload is global with no owner/containment (cross-user PDF read), no pdf-marker ownership assertion -> port UploadHandler.resolve_upload + _upload_path_inside, give locate_upload owner/auth_manager params, add assert_pdf_marker_upload_owned (400) called on create/update.
- `rust/src/routes/document_routes.rs` — global locate_upload at all PDF call sites (IDOR), no pdf-marker assertion, language facet overwrites (undercounts), no clear_active_document, lstrip char-set PDF marker strip -> owner-aware resolver at every call site; add _assert_pdf_marker_upload_owned in create/update; ADD language facet counts per key; add guarded clear_active_document + call from patch (empty session_id) and delete; replace lstrip_chars with exact-prefix strip at lines 412/844.
- `rust/src/src/chat_handler.rs` — attachment resolution reads uploads.json directly indexed by id with no owner check (cross-owner IDOR) -> replace with owner-aware resolve_upload(att_id, owner); route vision detection through model_supports_vision; name->original_name->id fallback; skip/avoid persisting cached vision text starting with '['; pass owner+resolved_uploads into build_user_content.
- `rust/src/src/document_processor.rs` — build_user_content uses old global/walkdir resolution and passes on-disk path to type checks, no inline attachment budget, no Office/markitdown path, lstrip PDF marker, 30s VL timeout -> add owner/resolved_uploads params + resolve_upload, add MAX_INLINE_ATTACHMENT_CHARS budget, add _process_office_document (markitdown), removeprefix PDF strip, VL timeout 30->120.
- `rust/src/src/personal_docs.rs` — remove_directory calls rebuild_index() + re-index (catastrophic shared-collection wipe #1660); duplicate final chunk; narrow extensions; no Office dispatch; prefix-only exclusion bug -> add remove_directory to RagManager trait and call it instead of rebuild; add `if j>=n {break;}` in split_chunks; expand DEFAULT_EXTENSIONS; branch load_personal_index by ext; fix exclusion filter to path-boundary match.
- `rust/src/src/upload_handler.rs` — count_concurrent_uploads counts batch size (>3 files self-reject, #1346) plus several other old behaviors -> count timestamps within last 10s ignoring n_files (count_recent_uploads); optional-extension validate_upload_id regex; alnum-sanitized _build_upload_id; rate limit 5->60; add xlsx/pptx/xls/epub; atomic uploads.json + .bak + Mutex; dedupe stale-entry pruning; add resolve_upload/get_upload_info/find_upload_path/inside_upload_dir helpers.

**Memory/Skills (vector)**
- `rust/src/src/rag_vector.rs` — doc_id ignores owner (cross-owner chunk collision/drop), loose keyword-fallback owner filter (leak), over-deleting remove_directory -> key doc_id_for on `owner\x00text`, tighten keyword filter to strict `meta.owner != owner`, rewrite remove_directory to abspath-normalize + path-boundary match via get-scan.

**Auth/Security (settings)**
- `rust/src/routes/auth_routes.rs` (settings_scrub) — scrub_settings is flat (top-level only) with 5 patterns, so nested secrets (e.g. smtp_password) and _passwd/_pass/_pwd/_credentials leak to non-admins -> make scrub_settings recurse into objects/arrays and expand SECRET_KEY_PATTERNS to the full upstream set (keep google_pse_cx allowlist).

**Misc**
- `rust/src/routes/cookbook_helpers.rs` (and cookbook_routes.rs) — missing _validate_serve_model_id, rewritten SCAN_PY_PRELUDE (ollama/gguf scanning), Ollama-ready parse phase, serve-runner exit-capture/ROCm build, pip helpers -> port _validate_serve_model_id + regexes; rewrite scanner; add Ollama-ready parse branch; add serve-runner builders + pip helpers.
- `rust/src/routes/cookbook_routes.rs` — missing auto_register_llm_endpoint, _validate_serve_model_id, 4 new diag rules, tasks-status exit/Fetching-0/dead-session handling, state-POST stale-done override, est_vram drop removal, gguf/ollama scan passthrough -> port all listed (auto-register on non-pip/non-diffusion serve, switch repo_id validation, add diag rules, update status classification, add anti-poisoning override, keep unknown-size models, thread gguf/backend/is_ollama/gguf_files).
- `rust/src/routes/shell_routes.rs` — list_packages ungated, SSH probes use `sh -c` string (injection), no rebuild-engine endpoint, stale install allow-list -> add admin + sec-fetch-site cross-site guard to list_packages; switch SSH to validated argv; register POST /api/cookbook/rebuild-engine (admin); add diffusers[torch]/vllm to KNOWN.
- `rust/src/services/hwfit/profiles.rs` (missing) — no compute_serve_profiles / GET /api/hwfit/profiles -> create profiles.rs porting compute_serve_profiles + helpers, declare module, add handler. (Severity medium but listed here as the high-priority hardware work depends on it.)
- `rust/src/src/topic_analyzer.rs` — no early-return for empty owner (aggregates all tenants' sessions), substring keyword match -> add early `{"topics":[],"total_topics":0}` return + unconditional in-loop owner check, switch to word-boundary regex matching.
- `rust/src/src/tool_index.rs` — missing web_fetch in ALWAYS_AVAILABLE/descriptions, stale create_document desc, "tell" in email hints (#1707), substring hint matching -> add web_fetch (bump test to 9) + description, update create_document string, remove "tell", switch matcher to word-boundary regex.
- `rust/src/src/integrations.rs` — no encryption-at-rest, no atomic write/chmod/shape validation, no mask_integration_secret -> encrypt api_keys on save via atomic_write_json + chmod 0600; decrypt + re-save plaintext + drop non-dict rows on load; add mask_integration_secret.
- `rust/src/src/tool_execution.rs` — no path confinement for read_file/write_file (can read /etc/shadow, write ~/.ssh), _ADMIN_TOOLS missing app_api/serve_preset (priv-esc), no web_fetch -> add _resolve_tool_path (sensitive deny + allowlist containment) routing read/write_file; add app_api/serve_preset to _ADMIN_TOOLS; add web_fetch map/parser/handler; optionally current_exe python + owner threading + exit_code:1.
- `rust/src/src/tool_implementations/documents.rs` (+ management_service/db, mod, vault) — document tools not owner-scoped (cross-tenant read/update/edit/delete), no clear_active_document, skills calls lack owner, is_secret uses contains("token"), notes ignore checklist_items/raw due_date, calendar uses raw uid + no is_utc refresh, _APP_API_BLOCKLIST trailing slashes (unblocked /api/users), vault unlock passes password in argv -> thread owner through all document tools + owner filtering on fetch helpers; add clear_active_document; thread owner into skills calls; fix is_secret to ends_with("token") + aliases; accept checklist_items + parse due_date; add _resolve_base_uid + is_utc refresh; drop trailing slashes from blocklist prefixes; switch vault unlock to stdin.
- `rust/src/routes/gallery_helpers.rs` — _owner_filter returns match-nothing for user=None (empty sidebars/no-op cleanups in no-auth deployments); extract_exif reads raw dimensions (swapped for rotated photos) -> add OwnerFilter::All (SQL "1") for user=None and update the 6 arms; apply EXIF orientation before storing dimensions.
- `rust/src/routes/gallery_routes.rs` — 8 image-edit endpoints lack require_privilege("can_generate_images"), no SSRF validation on client _endpoint, unsanitized filename, buggy rstrip api_key -> add privilege gate to the 8 handlers; add check_outbound_url on inpaint/harmonize _endpoint (IMAGE_BLOCK_PRIVATE_IPS); sanitize gallery_replace filename; fix api_key_for_rstrip_v1 to removesuffix("/v1").rstrip("/").

---

## Medium priority

**Search**
- `rust/src/src/search/content.rs` — _public_http_url passes empty-IP hosts (SSRF), OG image https-only, thin-content fallback missing (600-char + noise-strip), old stats/quote regexes -> fail closed on empty IPs, accept http:// OG images, add THIN_CONTENT_CHARS=600 body-strip fallback (drop the 8000 truncate), update STATS_RE, manual matching-quote logic for extract_quotes.
- `rust/src/src/search/providers.rs` — no SafeSearch param on any provider request, no DDG /l/?uddg= redirect unwrap -> add _get_safesearch_level/_safesearch_for + inject safesearch/safe/kp into all providers; add resolve_ddg_redirect for the raw href (env-key fallback and JSON-decode hardening already covered).
- `rust/src/src/search/query.rs` — detect_question_type uses bare prefix (mis-flags "whatsapp"/"however"), year regex captures group 1 -> whole-word check, YEAR_RE `\b(?:19|20)\d{2}\b` pushing full match.
- `rust/src/src/search/ranking.rs` — recency_score uses local time (UTC-offset skew), substring sports hints (matches inside "transport") -> use Utc::now().naive_utc(); word-boundary sports regex.

**Email/IMAP**
- `rust/src/mcp_servers/email_server.rs` (+ email_helpers.rs) — list_emails missing the unresponded-only `(UNANSWERED)` branch; no smtp_security plumbing -> add the `else if unresponded_only { "(UNANSWERED)" }` arm; plumb smtp_security (column + PRAGMA fallback, env, port derivation, send_smtp_message selection).

**Calendar/CalDAV**
- `rust/src/routes/calendar_routes.rs` (caldav_writeback) — no remote write-back; CalDAV event create/edit/delete never reach the server -> add caldav_writeback module (build_event_ical/find_remote_calendar/push_event/writeback_event) and call it from create/update/delete_event when source is caldav.

**Models/LLM**
- `rust/src/routes/compare_routes.rs` — old inline endpoint matching + hardcoded Bearer (Anthropic endpoints fail auth) -> use endpoint_resolver normalize_base in endpoint_api_key() and build_headers (selecting the matched row's base_url) for session headers.
- `rust/src/src/context_budget.py -> agent_loop_web.rs` — old min(context_length, soft_budget) caps long-context models at 6000 -> port compute_input_token_budget (85% of window, hard_max default 200000 from new agent_input_token_hard_max), honour explicit budget, fall back when window unknown; add the setting + is_setting_overridden in settings.rs.
- `rust/src/src/model_context.rs` — missing host.docker.internal/gemma-4, no local-endpoint cache bypass, first-match _lookup_known shadowing -> add host.docker.internal + gemma-4=262144; gate cache read/write on !is_local; return longest matching key.
- `rust/src/src/teacher_escalation.rs` — _SOTA_HOSTS missing openrouter/ollama/venice; no prompt-injection trace guard -> add the 3 hosts; add _UNTRUSTED_TRACE_GUARD token to both templates + substitution; wrap _format_trace in untrusted markers.

**Memory/Skills**
- `rust/src/src/memory.rs` (services/memory) — buggy bullet regex raises NoneType error; tokenize keeps empties; ensure_file_exists no parent dir -> fix BULLET_INNER_RE to `^(?:[-*•]|\d+\.)\s*(.*)`, remove the error branch, filter empty tokens, create_dir_all before write.
- `rust/src/src/memory.rs` (src/memory) — same buggy bullet regex/error branch -> fix regex + drop error path so bullet lines extract; update faithful-bug test; optionally skip non-dict validate_entries.
- `rust/src/routes/memory_routes.rs` — import session field required (no utility-endpoint fallback) -> make session optional, branch on presence (sess.* vs resolve_endpoint_triple("utility")), 400 "No LLM model configured"; optionally switch extract_memory inline 401 to require_user.

**Chat**
- `rust/src/routes/chat_helpers.rs` (+ chat_routes.rs) — needs_auto_name hardcodes old AM/PM-mandatory uppercase regex; resolve_session_auth not owner-scoped; no cache-first model id -> change DEFAULT_NAME_RE to `(?i)^.+ \d{1,2}:\d{2}:\d{2}(\s*(AM|PM))?$`; lower priority: owner-scope+is_enabled+exact-URL resolve_session_auth, cache-first build_chat_context, owner threading.
- `rust/src/src/chat_handler.rs` (chat_helpers) — vision detection uses old small keyword list/regex, no LM Studio probe -> expand VISION_KEYWORDS, change VL_NAME_RE to vl/vlm boundary check, add model_supports_vision probing /api/v1/models capabilities.vision (60s TTL, local-host gate).
- `rust/src/routes/chat_routes.rs` (action_intents) — TOOL_INTENT_PATTERNS is verbatim old inline list -> replace with 1:1 port of rewritten src/action_intents.py regexes (action-question/please calendar, notes, email, UI panel toggles, deep-research, prefix-gated shell verbs).

**Agent/Tools**
- `rust/src/src/agent_tools.rs` — TOOL_TAGS missing web_fetch (tag/native call rejected) -> add "web_fetch" to the TOOL_TAGS set.
- `rust/src/src/tool_parsing.rs` — _TOOL_NAME_MAP missing 7 aliases, no web_fetch freeform branch -> add the google_search/web_fetch aliases and an `else if mapped == "web_fetch"` branch returning url.
- `rust/src/src/tool_schemas.rs` — web_search ignores queries[] array, no web_fetch/manage_notes schemas, no rrule param -> prefer args["queries"] in the web_search arm; add web_fetch + manage_notes schemas + rrule.
- `rust/src/src/tool_security.rs` — NON_ADMIN_BLOCKED_TOOLS missing serve_preset (public users can launch a model server) -> add "serve_preset".
- `rust/src/src/builtin_mcp.rs` — old 30s timeout wrap, no npx cache pre-check -> add npx_package_from_args + is_npx_package_cached, skip uncached servers, connect with no timeout (drop the 30s wrapper).
- `rust/src/src/mcp_manager.rs` — no generation counter (stale prompt-description cache), no playwright error formatting -> add generation field incremented on connect/disconnect, include it in the prompt cache key, add _format_mcp_connection_error for @playwright/mcp.

**Uploads/Docs**
- `rust/src/routes/contacts_routes.rs` — parse_vcards matches raw lines, dropping grouped (item1.EMAIL) fields -> strip a leading RFC 6350 group token and use name_part for all checks/extraction.
- `rust/src/routes/document_routes.rs` (markitdown) — no Office/EPUB extraction -> add markitdown_runtime equivalent + wire into document_processor and personal_docs.

**HWFit**
- `rust/src/routes/hwfit_routes.rs` — no metal backend/unified_memory, missing ctx/fit_only params, no /profiles route -> accept "metal" + set/pop unified_memory, add ctx (clamp 1024..1000000)/fit_only to get_models + rank_models, add GET /api/hwfit/profiles handler.
- `rust/src/services/hwfit/models.rs` — quant tables missing many keys + changed penalties; PREQUANTIZED_PREFIXES too small; no infer_quantization/normalize_model_entry; old is_prequantized -> add new quant keys to all four tables + update penalties, expand prefixes to 13, add infer_quantization_from_name + normalize_model_entry, rewrite is_prequantized.

**Misc**
- `rust/src/routes/admin_wipe_routes.rs` — gallery wipe ignores gallery_albums (undercounts, albums survive) -> count + DELETE gallery_albums alongside gallery_images.
- `rust/src/routes/backup_routes.rs` — memory dedup set spans all tenants (importing user loses own data) -> add owner filter so only `e.get("owner") == user` entries seed existing_texts.
- `rust/src/routes/note_routes.rs` — fire_reminder 401s in auth-disabled/loopback modes -> replace manual gate with auth_adapter::require_user (add HeaderMap/host param).
- `rust/src/routes/personal_routes.rs` — resolve_allowed_personal_dir uses lexical abspath (symlink escape) -> canonicalize like realpath before the common_path check.
- `rust/src/routes/prefs_store.rs` — save_for_user(None) flattens and destroys _users map; load() returns non-object verbatim -> write prefs into the first _users slot when present (else flat save); coerce non-object load to {}.
- `rust/src/routes/mcp_routes.rs` — oauth_authorize_page interpolates auth_url/server_id/host raw (XSS) -> apply existing html_escape helper before interpolation.
- `rust/src/routes/vault_routes.rs` — unlock() passes master password in argv (visible via ps) -> call run_bw(["unlock","--raw"], None, Some(pw+"\n")) over stdin.
- `rust/src/routes/task_routes.rs` — missing POST /{task_id}/clear-cache and /{task_id}/stop -> add both handlers + .route()s and a per-task stop_task(task_id) in task_scheduler.rs.
- `rust/src/src/api_key_manager.rs` — load() propagates errors on corrupt store (startup fails) -> fall back to empty map on read/parse error, empty on non-object, skip per-entry decrypt failures.
- `rust/src/src/preset_manager.rs` — load() doesn't return defaults for non-object or heal missing built-ins -> return DEFAULT_PRESETS on non-object, overlay loaded over defaults when built-in keys missing then save.
- `rust/src/src/rag_singleton.rs` — get_rag_manager() hardcoded None (RAG disabled) -> promote the real initializer, resolve async/sync mismatch, remove the unreachable!() assumptions in personal_routes.rs.
- `rust/src/src/research_handler.rs` — synthesize_query researches literal affirmations, no date context, fixed 600s timeout, no extraction params, weak probe error -> add affirmation-aware fallback, current_date_context prefix, settings-resolved hard_timeout (1800/0=unlimited/clamp), extraction_timeout/concurrency fields, richer probe_endpoint (403 + error detail).
- `rust/src/src/settings.rs` — missing 6 new DEFAULT_SETTINGS keys, web_fetch feature, is_setting_overridden -> add the keys + web_fetch:true + is_setting_overridden(key).
- `rust/src/src/task_endpoint.rs` — resolve_task_endpoint has no owner (ignores per-user default/utility prefs) -> add owner param, use get_user_setting, thread owner through all callers.
- `rust/src/src/text_helpers.rs` — anchored THINK_OPEN_RE misses dangling openers; _strip_reasoning_prose strips to last paragraph (destroys answer) -> change regex to `(?i)<think(?:ing)?>[\s\S]*$`; strip only a leading contiguous reasoning run.
- `rust/src/src/visual_report.rs` — naive icon/logo substring filter drops real photos; raw heading text/slug; regex heading-id injection -> add is_icon_or_logo_url regex, plain_heading_text + empty-heading skip + "section" slug fallback, positional heading-id injection.
- `rust/src/web/mod.rs` (readiness) — no /api/ready route -> add readiness handler (DB SELECT 1, DATA_DIR writable probe, local_first, 200/503 + JSON).
- `rust/src/services/stt/stt_service.rs` — available()/transcribe() ignore stt_enabled (transcribes when toggle off) -> add `if !stt_enabled { return false/None; }` to both.
- `rust/src/services/tts/tts_service.rs` — no tts_enabled kill-switch; get_stats hardcodes true; only globs wav; parse_speed allows <=0 -> add tts_enabled field + gate available/synthesize, use real flag + count mp3, return 1.0 for speed <= 0.
- `rust/src/src/url_safety.rs` (missing) — no check_outbound_url module -> create it (http/https scheme, ToSocketAddrs resolve, reject link-local/multicast/reserved/unspecified + private/loopback when block_private, map v4-in-v6) and wire into embedding/gallery endpoints (400).
- `rust/src/src/webhook_manager.rs` — ip_is_private only checks static list (misses ::ffff: v4-mapped, 0.0.0.0, reserved/link-local/multicast) -> unwrap v4-mapped IPv6 and add is_private/loopback/link_local/multicast/unspecified/reserved checks.

---

## Low priority

**Search**
- `rust/src/services/search/content.rs` — same content.rs gaps (thin-content fallback, stats/quote regexes) as the medium item; OG image already ported -> add THIN_CONTENT_CHARS=600 body-strip fallback (drop 8000 truncate), update STATS_RE, matching-quote logic.

**Memory/Skills**
- `rust/src/services/memory/skill_extractor.rs` — maybe_extract_skill lacks empty-model guard and media stripping -> add media-stripping (drop media-only messages, Ok(None) if none remain) + empty-model guard.
- `rust/src/mcp_servers/memory_server.rs` — delete action does prefix bulk-delete instead of single match -> not-found check on full_id.is_none() then retain != full_id.

**Models/LLM**
- `rust/src/core/middleware.rs` — require_admin uses variable-time `==` for internal token -> use a constant-time compare_digest (add to pysecrets.rs via subtle/constant_time_eq), also at web/mod.rs:466.
- `rust/src/src/config.rs` — SearchConfig searxng_instance default still 8888 -> change to 8080 (and the test).
- `rust/src/src/constants.rs` — SEARXNG_INSTANCE default still 8888 (used by providers.rs) -> change to 8080.

**Agent/Tools**
- `rust/src/src/agent_runs.rs` — drain() abnormal-termination path emits only the bare close sentinel -> on a true crash, publish `event: error` + `data: [DONE]` before the (None,None) sentinel.
- `rust/src/src/bg_jobs.rs` — launch() emits bare unquoted paths in the wrapper -> shell-quote POSIX paths (DATA_DIR with spaces); optionally drop non-dict records in load().

**Misc**
- `rust/src/routes/editor_draft_routes.rs` — get_draft returns non-dict stored payloads verbatim -> guard to objects only (`filter(Value::is_object)`, else {}).
- `rust/src/routes/font_routes.rs` — _derive_family over-splits brand names (JetBrainsMono) -> remove whole-name camelCase split, map tokens through split_family_token with FAMILY_SUFFIX_WORDS; update the stale test.
- `rust/src/services/docs/service.rs` — query maps non-dict results into default DocChunks -> insert `.filter(|r| r.is_object())` (defensive parity; currently no non-dict elements occur).
- `rust/src/src/chroma_client.rs -> vector_store.rs` — HTTP Chroma backend blocks ~30s on unreachable host -> add .connect_timeout (CHROMADB_CONNECT_TIMEOUT default 2s) and/or a fast TCP pre-probe with a descriptive error.
- `rust/src/src/email_thread_parser.rs` — ORIG_RE lacks Forwarded-message alternative -> add it at line 71.
- `rust/src/src/embeddings.rs` — no Windows fastembed broken-symlink self-heal / HF_HUB env guard -> add a #[cfg(windows)] symlink-heal block in FastEmbedClient::new (no effect on non-Windows).
- `rust/src/src/pdf_form_doc.rs` — greedy field-label regex drops fields with `*`; find_source_upload_id unvalidated (path traversal) -> change TEXT/CHOICE_VALUE_RE to `.+?`, gate the captured upload_id with validate_upload_id.
- `rust/src/routes/document_routes.rs` (pdf_runtime) — pdfium-unavailable returns 500 instead of 503 -> return 503 with a PDF-backend-missing setup hint from render_pages/render_page_png.
- `rust/src/src/research_utils.rs` — LOW_QUALITY_MARKERS over-filters bare cookie/copyright -> remove those two, add cookie consent/banner/notice, copyright notice/footer, all rights reserved.
- `rust/src/src/youtube_min.rs` — format_comments_for_context emits a degenerate line for non-object entries -> add `if !c.is_object() { continue; }` (load-bearing timeout fix already present).

---

## Not ported / out of scope
- `core/platform_compat.py` (`rust/src/core/platform_compat.rs` does not exist) — Windows-native portability layer; POSIX semantics are already present and unchanged, so no server-behavior change required (add only if Windows-native support becomes a goal).
- `services/memory/service.py` (`rust/src/services/memory/mod.rs`) — only a defensive `isinstance(r, dict)` guard on vector_store.search results; no behavioral change needed (marked not_ported, severity none).
- `services/hwfit/data/hf_models.json` (`rust/src/services/hwfit/models.rs`) — pure data-table edit; the Rust port loads this exact file at runtime, so the new catalog data is picked up automatically (out_of_scope).

## Already covered / trivial
24 files were assessed as `already_covered` (parity already matched, or the change is a no-op in statically-typed/always-UTF-8 Rust) and require no change.