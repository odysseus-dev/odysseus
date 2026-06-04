// mcp_servers/image_gen_server.rs  <- mcp_servers/image_gen_server.py
//! MCP server exposing image generation via OpenAI-compatible APIs.
//!
//! Faithful port of the Python `mcp_servers/image_gen_server.py`. The single
//! `generate_image` tool validates the prompt + settings gate, resolves an image
//! model, POSTs to the OpenAI-compatible `/images/generations` endpoint, writes
//! the returned base64 PNG under `data/generated_images/` and records it in the
//! `gallery_images` table.
//!
//! The server-shaped plumbing (`Server("image_gen")`, `@server.list_tools()`,
//! `@server.call_tool()`, `stdio_server()`) is provided by [`super::harness`];
//! here we expose only [`tools`] (the `list_tools` body) and [`call_tool`] (the
//! `call_tool` body), which [`super::run_server`] wires into `harness::serve`.
//!
//! ## The `_resolve_model` seam (the one honest defer)
//!
//! The Python happy path is entirely gated behind
//! `src.ai_interaction._resolve_model(spec) -> (url, model_id, headers)`, which is
//! NOT ported anywhere in the Rust tree (the same gap is recorded in
//! `gallery_routes::ai_tag_image`, `preset_routes::expand_character_prompt`, and
//! `teacher_escalation`). Rather than fabricate an image, we reproduce the
//! Python's OWN failure path:
//!   * auto-detect (`model_spec` empty): each candidate `_resolve_model(...)`
//!     raises `ValueError`, so `model_spec` stays `""` and we return
//!     `"Error: No image model found. Configure one in Admin."`.
//!   * explicit/configured `model_spec`: `_resolve_model(model_spec)` raises
//!     `ValueError`, caught by `except ValueError as e: return f"Error: {e}"`.
//! The reqwest POST + base64 decode + DB insert ARE written below (behind the
//! resolve seam) so the faithful happy-path logic is present, but they are
//! unreachable on the live no-resolver path — exactly as the Python behaves when
//! `_resolve_model` cannot find a configured endpoint.

use base64::Engine;
use indexmap::IndexMap;
use serde_json::{json, Value};

use crate::pylog as logger;

use super::harness::{text_content, CallToolHandler, ToolDef};

/// `Path("data/generated_images")` — the on-disk directory image bytes are
/// written to (matches `gallery_routes::IMG_DIR`).
const IMG_DIR: &str = "data/generated_images";

/// The `@server.list_tools()` body: the single `generate_image` tool.
///
/// ```python
/// @server.list_tools()
/// async def list_tools() -> list[Tool]:
///     return [Tool(name="generate_image", ...)]
/// ```
pub fn tools() -> Vec<ToolDef> {
    vec![ToolDef::new(
        "generate_image",
        "Generate an image using an image-capable model (e.g. gpt-image-1)",
        json!({
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Image description prompt"},
                "model": {"type": "string", "description": "Model name (auto-detects if omitted)"},
                "size": {"type": "string", "description": "Image size (default 1024x1024)"},
                "quality": {"type": "string", "description": "Quality: low, medium, high, auto (default medium)"},
            },
            "required": ["prompt"],
        }),
    )]
}

/// Build the [`CallToolHandler`] the dispatcher hands to `harness::serve_stdio`.
///
/// The `@server.call_tool()` analogue: a boxed closure that forwards each
/// `(name, arguments)` to [`call_tool`]. It captures nothing (the image-gen
/// server holds no long-lived manager — it opens settings/DB/HTTP per call), so
/// the closure is a thin async adapter, mirroring the single decorated
/// `call_tool` coroutine of the Python server.
pub fn handler() -> CallToolHandler {
    Box::new(|name: String, arguments: Value| Box::pin(call_tool(name, arguments)))
}

/// The `@server.call_tool()` body for the image-gen server.
///
/// Mirrors the Python `call_tool(name, arguments)` coroutine. Returns the list of
/// `TextContent` blocks (here always a single block) the harness wraps into the
/// `tools/call` `content` array.
pub async fn call_tool(name: String, arguments: Value) -> Vec<Value> {
    // `if name != "generate_image": return [TextContent(text=f"Unknown tool: {name}")]`
    if name != "generate_image" {
        return vec![text_content(format!("Unknown tool: {name}"))];
    }

    // `prompt = arguments.get("prompt", "")`
    // `model_spec = arguments.get("model", "")`
    // `size = arguments.get("size", "1024x1024")`
    // `quality = arguments.get("quality", "medium")`
    let prompt = arg_str(&arguments, "prompt", "");
    let mut model_spec = arg_str(&arguments, "model", "");
    let mut size = arg_str(&arguments, "size", "1024x1024");
    let mut quality = arg_str(&arguments, "quality", "medium");

    // `if not prompt: return [TextContent(text="Error: Image prompt is required")]`
    if prompt.is_empty() {
        return vec![text_content("Error: Image prompt is required")];
    }

    // The Python wraps the remainder in `try: ... except (TimeoutException |
    // ValueError | Exception)`. `generate()` returns the success/error string and
    // never panics, so the explicit `except ValueError as e: return f"Error:
    // {e}"` lives inside `generate()` itself (the resolve seam).
    let text = generate(&prompt, &mut model_spec, &mut size, &mut quality).await;
    vec![text_content(text)]
}

/// Read a string argument with a default, mirroring `arguments.get(key, default)`.
///
/// A present non-string value is treated like the Python `.get` returning a
/// non-`str` would NOT be (the Python servers always receive JSON strings here),
/// so we coerce only `Value::String`; anything else falls back to `default`.
fn arg_str(arguments: &Value, key: &str, default: &str) -> String {
    arguments
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or(default)
        .to_string()
}

/// The body of the Python `try:` block — the resolve/POST/persist pipeline.
///
/// Returns the result string (success summary or an `Error: ...` message).
/// `model_spec` / `size` / `quality` are passed by `&mut` because the Python
/// mutates them in place (settings fallback, auto-detect, size clamp).
// Mirrors Python image_gen_server.py:90-93: a deliberate if/elif whose two
// distinct conditions intentionally clamp to the same "1024x1024" size.
#[allow(clippy::if_same_then_else)]
async fn generate(
    prompt: &str,
    model_spec: &mut String,
    size: &mut String,
    quality: &mut String,
) -> String {
    use crate::src::settings::{get_setting, load_settings};

    // `if not get_setting("image_gen_enabled", True): return Error: ...disabled...`
    if !get_setting("image_gen_enabled", json!(true))
        .as_bool()
        .unwrap_or(true)
    {
        return "Error: Image generation is disabled by the administrator.".to_string();
    }

    // `_settings = load_settings()`
    let settings = load_settings();

    // `if not model_spec: model_spec = _settings.get("image_model", "")`
    if model_spec.is_empty() {
        *model_spec = settings
            .get("image_model")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
    }
    // `if quality == "medium" and _settings.get("image_quality"): quality = _settings["image_quality"]`
    if quality == "medium" {
        if let Some(q) = settings.get("image_quality").and_then(Value::as_str) {
            if !q.is_empty() {
                *quality = q.to_string();
            }
        }
    }

    // Auto-detect best available image model.
    // `if not model_spec: for candidate in (...): try: _resolve_model(candidate);
    //   model_spec = candidate; break; except ValueError: continue`
    if model_spec.is_empty() {
        for candidate in ["gpt-image-1.5", "gpt-image-1", "dall-e-3"] {
            match resolve_model(candidate).await {
                Ok(_) => {
                    *model_spec = candidate.to_string();
                    break;
                }
                Err(_) => continue,
            }
        }
        // `if not model_spec: return Error: No image model found. Configure one in Admin.`
        if model_spec.is_empty() {
            return "Error: No image model found. Configure one in Admin.".to_string();
        }
    }

    // `url, model_id, headers = _resolve_model(model_spec)` — on the live no-resolver
    // path this raises `ValueError`, caught below as `except ValueError as e:
    // return f"Error: {e}"`.
    let (url, model_id, headers) = match resolve_model(model_spec).await {
        Ok(triple) => triple,
        Err(e) => return format!("Error: {e}"),
    };

    // ---- Beyond the resolve seam: the faithful happy path (unreachable while
    // `_resolve_model` is deferred, but written so the logic is present). ----

    // `is_gpt_image = "gpt-image" in model_id.lower()`
    let is_gpt_image = model_id.to_lowercase().contains("gpt-image");
    // `base_url = url.replace("/chat/completions","").replace("/v1/messages","").rstrip("/")`
    let base_url = url
        .replace("/chat/completions", "")
        .replace("/v1/messages", "");
    let base_url = base_url.trim_end_matches('/');
    // `images_url = base_url + "/images/generations"`
    let images_url = format!("{base_url}/images/generations");

    // `valid_gpt_sizes = {...}` / `valid_dalle3_sizes = {...}`
    let valid_gpt_sizes = ["1024x1024", "1024x1536", "1536x1024", "auto"];
    let valid_dalle3_sizes = ["1024x1024", "1024x1792", "1792x1024"];
    // `if is_gpt_image and size not in valid_gpt_sizes: size = "1024x1024"`
    // `elif not is_gpt_image and size not in valid_dalle3_sizes: size = "1024x1024"`
    if is_gpt_image && !valid_gpt_sizes.contains(&size.as_str()) {
        *size = "1024x1024".to_string();
    } else if !is_gpt_image && !valid_dalle3_sizes.contains(&size.as_str()) {
        *size = "1024x1024".to_string();
    }

    // `payload = {"model": model_id, "prompt": prompt, "n": 1, "size": size}`
    let mut payload = serde_json::Map::new();
    payload.insert("model".to_string(), json!(model_id));
    payload.insert("prompt".to_string(), json!(prompt));
    payload.insert("n".to_string(), json!(1));
    payload.insert("size".to_string(), json!(*size));
    // `if is_gpt_image: payload["quality"] = quality if quality in (...) else "medium"`
    if is_gpt_image {
        let q = if matches!(quality.as_str(), "low" | "medium" | "high" | "auto") {
            quality.clone()
        } else {
            "medium".to_string()
        };
        payload.insert("quality".to_string(), json!(q));
    }

    // `async with httpx.AsyncClient(timeout=Timeout(connect=30, read=300, write=30,
    //   pool=30)) as client: resp = await client.post(images_url, json=payload,
    //   headers=headers)`.
    //
    // reqwest exposes a single overall timeout rather than httpx's per-phase
    // splits; we use the longest (the 300s read), so a hung request still raises
    // the same user-facing "timed out (300s)" message the Python emits on
    // `httpx.TimeoutException`.
    let client = match reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(300))
        .connect_timeout(std::time::Duration::from_secs(30))
        .build()
    {
        Ok(c) => c,
        // A client-builder failure is the generic `except Exception as e` arm.
        Err(e) => return format!("Error: {e}"),
    };

    // Build the request headers from the resolved `headers` dict (insertion-ordered).
    let mut req = client.post(&images_url);
    for (k, v) in &headers {
        req = req.header(k.as_str(), v.as_str());
    }
    req = req.json(&Value::Object(payload.clone()));

    let resp = match req.send().await {
        Ok(r) => r,
        // `except httpx.TimeoutException: return Error: ...timed out (300s)`
        Err(e) if e.is_timeout() => {
            return "Error: Image generation timed out (300s)".to_string();
        }
        // Any other transport error is the generic `except Exception as e`.
        Err(e) => return format!("Error: {e}"),
    };

    // `if resp.status_code != 200: ...`
    let status = resp.status();
    if status.as_u16() != 200 {
        // `error_text = resp.text[:500]`
        let body_text = resp.text().await.unwrap_or_default();
        let mut error_text: String = body_text.chars().take(500).collect();
        // `try: err_json = resp.json(); error_text = err_json["error"]["message"]
        //   if isinstance(err_json["error"], dict) else str(err_json["error"])`
        if let Ok(err_json) = serde_json::from_str::<Value>(&body_text) {
            if let Some(err_val) = err_json.get("error") {
                if let Some(obj) = err_val.as_object() {
                    if let Some(msg) = obj.get("message").and_then(Value::as_str) {
                        error_text = msg.to_string();
                    }
                } else {
                    // `str(err_json.get("error", error_text))` — Python `str()` of
                    // the (non-dict) value.
                    error_text = py_str(err_val);
                }
            }
        }
        // `return Error: Image generation failed ({status}): {error_text}`
        return format!(
            "Error: Image generation failed ({}): {error_text}",
            status.as_u16()
        );
    }

    // `data = resp.json()`
    let data: Value = match resp.json().await {
        Ok(v) => v,
        // A non-JSON 200 body is the generic `except Exception as e`.
        Err(e) => return format!("Error: {e}"),
    };
    // `images = data.get("data", [])`
    let images = data.get("data").and_then(Value::as_array);
    // `if not images: return Error: No images returned from API`
    let images = match images {
        Some(arr) if !arr.is_empty() => arr,
        _ => return "Error: No images returned from API".to_string(),
    };

    // `img = images[0]`
    // Python initializes `image_url = None` then sets it in one of the two
    // branches (the `else` returns early); here the whole if/elif/else is an
    // expression yielding the `String`, so the dead initial `None` is not needed.
    let img = &images[0];

    // `if img.get("b64_json"): ... elif img.get("url"): ... else: return ...`
    let image_url: String = if let Some(b64) = img.get("b64_json").and_then(Value::as_str) {
        // `img_dir = Path("data/generated_images"); img_dir.mkdir(parents=True, exist_ok=True)`
        if let Err(e) = std::fs::create_dir_all(IMG_DIR) {
            // Directory creation failing is the generic `except Exception as e`.
            return format!("Error: {e}");
        }
        // `filename = f"{uuid.uuid4().hex[:12]}.png"`
        let hex = uuid::Uuid::new_v4().simple().to_string();
        let filename = format!("{}.png", &hex[..12]);
        // `img_path = img_dir / filename; img_path.write_bytes(base64.b64decode(...))`
        let img_path = std::path::Path::new(IMG_DIR).join(&filename);
        let decoded = match base64::engine::general_purpose::STANDARD.decode(b64) {
            Ok(bytes) => bytes,
            Err(e) => return format!("Error: {e}"),
        };
        if let Err(e) = std::fs::write(&img_path, &decoded) {
            return format!("Error: {e}");
        }

        // Save to gallery. `try: ... except Exception: pass` — the DB insert is
        // best-effort; any failure is swallowed exactly like the Python.
        let quality_val = payload
            .get("quality")
            .and_then(Value::as_str)
            .unwrap_or("medium")
            .to_string();
        save_to_gallery(&filename, prompt, &model_id, size, &quality_val);

        // `image_url = f"/api/generated-image/{filename}"`
        format!("/api/generated-image/{filename}")
    } else if let Some(u) = img.get("url").and_then(Value::as_str) {
        // `elif img.get("url"): image_url = img["url"]`
        u.to_string()
    } else {
        // `else: return Error: Unexpected image API response format`
        return "Error: Unexpected image API response format".to_string();
    };

    // `result = f"Generated image for: {prompt[:100]}\nimage_url: {image_url}\n
    //   model: {model_id}\nsize: {size}"`
    let prompt_head: String = prompt.chars().take(100).collect();
    format!("Generated image for: {prompt_head}\nimage_url: {image_url}\nmodel: {model_id}\nsize: {size}")
}

/// Persist a generated image to `gallery_images` — the Python's inner
/// `try: db.add(GalleryImage(...)); db.commit() except Exception: pass`.
///
/// Best-effort: any DB error (or an unavailable engine) is logged at debug and
/// swallowed, matching the bare `except Exception: pass`.
fn save_to_gallery(filename: &str, prompt: &str, model_id: &str, size: &str, quality: &str) {
    // `db = SessionLocal()` — open the DB; a failure here is swallowed.
    let conn = match crate::core::database::session_local() {
        Ok(c) => c,
        Err(e) => {
            logger::debug(&format!("image_gen gallery insert skipped: {e}"));
            return;
        }
    };
    // `db.add(GalleryImage(id=uuid4, filename, prompt, model=model_id, size,
    //   quality=payload.get("quality","medium"))); db.commit()`.
    //
    // The SQLAlchemy `GalleryImage` defaults `is_active=1`/`favorite=0` and a
    // `created_at`/`updated_at`; the gallery route module sets these explicitly on
    // its own inserts, so we mirror that to keep the row well-formed.
    let id = uuid::Uuid::new_v4().to_string();
    let now = crate::pydatetime::utcnow_naive_iso();
    if let Err(e) = conn.execute(
        "INSERT INTO gallery_images \
           (id, filename, prompt, model, size, quality, is_active, favorite, created_at, updated_at) \
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, 1, 0, ?7, ?7)",
        rusqlite::params![id, filename, prompt, model_id, size, quality, now],
    ) {
        // `except Exception: pass`
        logger::debug(&format!("image_gen gallery insert failed: {e}"));
    }
}

/// `src.ai_interaction._resolve_model(spec) -> (url, model_id, headers)` —
/// delegates to the ported [`crate::src::ai_interaction::resolve_model_spec`]: the
/// `model@endpoint` parsing, the enabled-`ModelEndpoint` ilike filter, the
/// anthropic-vs-`/models` probe, exact-then-partial matching, and the
/// `"No enabled endpoints found"` error when none match. (`Err(String)` mirrors
/// the Python `ValueError`, surfaced by the caller as `Error: {e}`.)
async fn resolve_model(spec: &str) -> Result<(String, String, IndexMap<String, String>), String> {
    crate::src::ai_interaction::resolve_model_spec(spec.trim(), None)
        .await
        .map_err(|e| e.to_string())
}

/// `str(value)` for a JSON value, matching Python's `str()` of the decoded body
/// in the error-extraction branch (`str(err_json.get("error", ...))`).
///
/// Python `str()` of a JSON string is the string itself (no quotes); of other
/// JSON types it is the `repr`-ish form. The error branch only reaches here for a
/// non-`dict` `error` value, almost always a plain string, so a string passes
/// through verbatim; other types fall back to the compact JSON form.
fn py_str(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// `tools()` advertises exactly the one tool with the Python inputSchema.
    #[test]
    fn lists_generate_image_tool() {
        let t = tools();
        assert_eq!(t.len(), 1);
        assert_eq!(t[0].name, "generate_image");
        assert_eq!(
            t[0].description,
            "Generate an image using an image-capable model (e.g. gpt-image-1)"
        );
        let schema = &t[0].input_schema;
        assert_eq!(schema["type"], json!("object"));
        assert_eq!(schema["required"], json!(["prompt"]));
        // All four documented properties present.
        let props = &schema["properties"];
        assert_eq!(props["prompt"]["type"], json!("string"));
        assert_eq!(props["model"]["type"], json!("string"));
        assert_eq!(props["size"]["type"], json!("string"));
        assert_eq!(props["quality"]["type"], json!("string"));
    }

    /// Unknown tool name -> `Unknown tool: {name}` (the Python guard).
    #[tokio::test]
    async fn unknown_tool_name_is_rejected() {
        let out = call_tool("nope".to_string(), json!({})).await;
        assert_eq!(out.len(), 1);
        assert_eq!(out[0]["text"], json!("Unknown tool: nope"));
    }

    /// Empty/missing prompt -> the required-prompt error, before any settings read.
    #[tokio::test]
    async fn empty_prompt_is_rejected() {
        let out = call_tool("generate_image".to_string(), json!({})).await;
        assert_eq!(out[0]["text"], json!("Error: Image prompt is required"));

        let out = call_tool("generate_image".to_string(), json!({"prompt": ""})).await;
        assert_eq!(out[0]["text"], json!("Error: Image prompt is required"));
    }

    // NOTE: `resolve_model` now delegates to `ai_interaction::resolve_model_spec`
    // (the real DB-backed `_resolve_model`), which is exercised by its own
    // `resolve_model_spec_branches` test (a real temp-`DATABASE_URL` SQLite — the
    // codebase keeps DB-`set_var` tests to ONE to avoid the process-global
    // `DATABASE_URL` parallel race). The earlier image_gen tests asserted the
    // since-deleted local stub's parse messages, so they are dropped rather than
    // duplicated here (the delegate is trivial).

    /// `py_str` passes strings through verbatim and renders other JSON compactly.
    #[test]
    fn py_str_matches_python_str_for_error_values() {
        assert_eq!(py_str(&json!("boom")), "boom");
        assert_eq!(py_str(&json!(42)), "42");
        assert_eq!(py_str(&json!(null)), "null");
    }
}
