import logging
import uuid as _uuid

logger = logging.getLogger(__name__)


class EditImageTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        """Edit a gallery image (upscale, rembg, inpaint, harmonize)."""
        import httpx
        from src.tool_implementations import _parse_tool_args, _INTERNAL_BASE

        try:
            args = _parse_tool_args(content)
        except ValueError:
            return {"error": "Invalid JSON arguments", "exit_code": 1}
        image_id = args.get("image_id", "")
        action = args.get("action", "")
        if not image_id or not action:
            return {"error": "image_id and action are required", "exit_code": 1}
        payload = {"image_id": image_id}
        if args.get("prompt"):
            payload["prompt"] = args["prompt"]
        if args.get("scale"):
            payload["scale"] = args["scale"]
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(f"{_INTERNAL_BASE}/api/gallery/{action}", json=payload)
                data = resp.json()
            if data.get("success") or data.get("id"):
                return {"output": f"Image edited ({action}). New image ID: {data.get('id', '?')}", "exit_code": 0}
            return {"error": data.get("error", f"{action} failed"), "exit_code": 1}
        except Exception as e:
            return {"error": str(e), "exit_code": 1}


class GenerateImageTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        """Generate an image using an image-capable model (e.g. gpt-image-1).

        Content format:
          Line 1: prompt describing the image
          Line 2: model name (optional, default auto-detects: prefers gpt-image-1.5 > gpt-image-1)
          Line 3: size (optional, defaults to 1024x1024)
          Line 4: quality (optional, defaults to medium — options: low, medium, high, auto)
        """
        import base64
        import httpx
        from pathlib import Path
        from src.constants import GENERATED_IMAGES_DIR

        owner = ctx.get("owner")
        session_id = ctx.get("session_id")

        lines = content.strip().split("\n")
        prompt = lines[0].strip() if lines else ""
        model_spec = lines[1].strip() if len(lines) > 1 and lines[1].strip() else ""
        size = lines[2].strip() if len(lines) > 2 and lines[2].strip() else "1024x1024"
        quality = lines[3].strip() if len(lines) > 3 and lines[3].strip() else "medium"

        if not prompt:
            return {"error": "Image prompt is required (line 1)"}

        # Load admin settings for defaults
        try:
            from src.settings import load_settings
            _settings = load_settings()
        except Exception:
            _settings = {}

        # Use admin-configured model/quality if not specified by the tool call
        if not model_spec:
            model_spec = _settings.get("image_model", "")
        if quality == "medium" and _settings.get("image_quality"):
            quality = _settings["image_quality"]

        # Auto-detect best available image model if still not set
        if not model_spec:
            from src.ai_interaction import _resolve_model
            for candidate in ("gpt-image-1.5", "gpt-image-1", "dall-e-3"):
                try:
                    _resolve_model(candidate, owner=owner)
                    model_spec = candidate
                    break
                except ValueError:
                    continue
            # Fallback: find any locally registered image-type endpoint
            if not model_spec:
                try:
                    from src.database import SessionLocal, ModelEndpoint
                    from src.auth_helpers import owner_filter
                    import httpx as _req
                    _idb = SessionLocal()
                    try:
                        _img_q = _idb.query(ModelEndpoint).filter(
                            ModelEndpoint.is_enabled == True,
                            ModelEndpoint.model_type == "image",
                        )
                        if owner:
                            _img_q = owner_filter(_img_q, ModelEndpoint, owner)
                        _img_eps = _img_q.all()
                        for _iep in _img_eps:
                            _ibase = _iep.base_url.rstrip("/")
                            if not _ibase.endswith("/v1"):
                                _ibase += "/v1"
                            try:
                                _r = _req.get(_ibase + "/models", timeout=3)
                                _r.raise_for_status()
                                _mids = [m.get("id") for m in (_r.json().get("data") or []) if m.get("id")]
                                if _mids:
                                    model_spec = _mids[0]
                                    break
                            except Exception:
                                continue
                    finally:
                        _idb.close()
                except Exception:
                    pass
            if not model_spec:
                return {"error": "No image model found. Configure one in Admin → Image Generation."}

        # Resolve the model to find the right endpoint
        from src.ai_interaction import _resolve_model
        try:
            url, model_id, headers = _resolve_model(model_spec, owner=owner)
        except ValueError:
            return {"error": f"No endpoint found with image model '{model_spec}'. "
                    "Configure an OpenAI-compatible endpoint with image generation support."}

        # Detect if this is a GPT image model vs DALL-E vs local diffusion
        is_gpt_image = "gpt-image" in model_id.lower()
        is_dalle = "dall-e" in model_id.lower()
        is_local_diffusion = not is_gpt_image and not is_dalle

        # Build the images endpoint URL from the chat completions URL
        base_url = url.replace("/chat/completions", "").replace("/v1/messages", "").rstrip("/")
        images_url = base_url + "/images/generations"

        # Validate size for cloud image models (local diffusion accepts any WxH)
        valid_gpt_sizes = {"1024x1024", "1024x1536", "1536x1024", "auto"}
        valid_dalle3_sizes = {"1024x1024", "1024x1792", "1792x1024"}
        if is_gpt_image and size not in valid_gpt_sizes:
            size = "1024x1024"
        elif is_dalle and size not in valid_dalle3_sizes:
            size = "1024x1024"

        payload = {
            "model": model_id,
            "prompt": prompt,
            "n": 1,
            "size": size,
        }

        # GPT image models and local diffusion support quality; DALL-E does not
        if is_gpt_image or is_local_diffusion:
            if quality in ("low", "medium", "high", "auto"):
                payload["quality"] = quality
            else:
                payload["quality"] = "medium"

        logger.info(f"Image generation: model={model_id}, size={size}, quality={quality}, prompt={prompt[:80]}")

        try:
            # GPT image models can take 30-120s+ depending on quality
            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=30.0, read=300.0, write=30.0, pool=30.0)) as client:
                resp = await client.post(images_url, json=payload, headers=headers)

                if resp.status_code != 200:
                    error_text = resp.text[:500]
                    try:
                        err_json = resp.json()
                        error_text = err_json.get("error", {}).get("message", error_text) if isinstance(err_json.get("error"), dict) else str(err_json.get("error", error_text))
                    except Exception:
                        pass
                    return {"error": f"Image generation failed ({resp.status_code}): {error_text}"}

                data = resp.json()
                images = data.get("data", [])
                if not images:
                    return {"error": "No images returned from API"}

                img = images[0]
                image_url = None
                image_id = None

                def _save_to_gallery(filename: str) -> str:
                    """Insert a GalleryImage row and return the new id (or '')."""
                    try:
                        from src.database import SessionLocal as _GallerySL, GalleryImage
                        new_id = str(_uuid.uuid4())
                        _gdb = _GallerySL()
                        _gdb.add(GalleryImage(
                            id=new_id,
                            filename=filename,
                            prompt=prompt,
                            model=model_id,
                            size=size,
                            quality=payload.get("quality", "medium"),
                            session_id=session_id,
                            owner=owner,
                        ))
                        _gdb.commit()
                        _gdb.close()
                        return new_id
                    except Exception as _ge:
                        logger.warning(f"Failed to save gallery record: {_ge}")
                        return ""

                # GPT image models always return b64_json; DALL-E may return url
                if img.get("b64_json"):
                    img_dir = Path(GENERATED_IMAGES_DIR)
                    img_dir.mkdir(parents=True, exist_ok=True)
                    filename = f"{_uuid.uuid4().hex[:12]}.png"
                    img_path = img_dir / filename
                    img_path.write_bytes(base64.b64decode(img.get("b64_json")))
                    image_url = f"/api/generated-image/{filename}"
                    image_id = _save_to_gallery(filename)

                elif img.get("url"):
                    # Download external URL and save locally (DALL-E returns temp URLs)
                    try:
                        # Validate the provider URL before downloading (SSRF protection)
                        from src.url_safety import check_outbound_url
                        ok, reason = check_outbound_url(img["url"], block_private=False)
                        if not ok:
                            return {"error": f"Image API returned unsafe image URL: {reason}"}

                        dl_resp = httpx.get(img["url"], timeout=60)
                        if dl_resp.status_code == 200:
                            img_dir = Path(GENERATED_IMAGES_DIR)
                            img_dir.mkdir(parents=True, exist_ok=True)
                            filename = f"{_uuid.uuid4().hex[:12]}.png"
                            img_path = img_dir / filename
                            img_path.write_bytes(dl_resp.content)
                            image_url = f"/api/generated-image/{filename}"
                            image_id = _save_to_gallery(filename)
                        else:
                            image_url = img["url"]  # fallback to external URL
                    except Exception as _dl_e:
                        logger.warning(f"Failed to download DALL-E image: {_dl_e}")
                        image_url = img["url"]  # fallback to external URL
                else:
                    return {"error": "Image API returned unexpected format (no b64_json or url)"}

                return {
                    "results": f"Generated image for: {prompt[:100]}",
                    "image_url": image_url,
                    "image_id": image_id,
                    "image_prompt": prompt,
                    "image_model": model_id,
                    "image_size": size,
                    "image_quality": payload.get("quality", "medium"),
                }

        except httpx.TimeoutException:
            return {"error": "Image generation timed out (300s). The model may be overloaded — try again or use quality=low."}
        except Exception as e:
            return {"error": f"Image generation error: {str(e)}"}
