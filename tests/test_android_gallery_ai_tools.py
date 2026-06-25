from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ANDROID_SERVER = (
    ROOT
    / "android"
    / "app"
    / "src"
    / "main"
    / "java"
    / "com"
    / "odysseus"
    / "simplesignal"
    / "MobileBackendServer.java"
).read_text(encoding="utf-8")
DYNAMIC_ONNX = (
    ROOT
    / "android"
    / "app"
    / "src"
    / "main"
    / "java"
    / "com"
    / "odysseus"
    / "simplesignal"
    / "DynamicOnnxRuntime.java"
).read_text(encoding="utf-8")
NATIVE_LOADER = (
    ROOT
    / "android"
    / "app"
    / "src"
    / "main"
    / "java"
    / "com"
    / "odysseus"
    / "simplesignal"
    / "NativeRuntimeLoader.java"
).read_text(encoding="utf-8")
NATIVE_LOADER_C = (
    ROOT
    / "android"
    / "app"
    / "src"
    / "main"
    / "cpp"
    / "odysseus_native_loader.c"
).read_text(encoding="utf-8")
PATCH_ONNX_LOADER_SCRIPT = (
    ROOT / "scripts" / "patch_onnxruntime_loader.mjs"
).read_text(encoding="utf-8")


def test_android_gallery_routes_try_provider_backed_ai_tools():
    assert "runProviderInpaint(source, mask, body)" in ANDROID_SERVER
    assert "runProviderBackgroundRemove(source, hint, backgroundHint, body)" in ANDROID_SERVER
    assert "runProviderSharpen(source, body)" in ANDROID_SERVER
    assert "postOpenAiImageEdit" in ANDROID_SERVER
    assert 'new URL(base + "/images/edits")' in ANDROID_SERVER
    assert 'isOpenAICompatibleImageEditBase(choice.optString("base_url"))' in ANDROID_SERVER
    assert 'String requestedEndpointId = jsonString(body, "_endpoint_id", "").trim();' in ANDROID_SERVER
    assert "ep = findEndpointForId(requestedEndpointId);" in ANDROID_SERVER
    assert "private JSONObject findEndpointForId(String endpointId)" in ANDROID_SERVER
    assert 'boolean openAiCompatible = isOpenAICompatibleImageEditBase(choice.optString("base_url"));' in ANDROID_SERVER
    assert "if (openAiCompatible || isImageEditModel(model))" in ANDROID_SERVER
    assert '!jsonString(body, "_endpoint_id", "").trim().isEmpty()' in ANDROID_SERVER
    assert "private boolean isOpenAICompatibleImageEditBase" in ANDROID_SERVER
    assert "private String providerLabelForBase(String baseUrl)" in ANDROID_SERVER
    assert "Alibaba Model Studio" in ANDROID_SERVER
    assert "mobileProviderLabel(endpoint, model)" in ANDROID_SERVER


def test_android_gallery_ai_tools_keep_local_fallbacks():
    assert "Inpaint needs an image-edit model" in ANDROID_SERVER
    assert "Bitmap modelCutout = runLocalRembgModel(source, hintMask, requestedModel);" in ANDROID_SERVER
    assert "edited = removeBackgroundBitmap(source, hint, backgroundHint, bgRemoveStrength, rembgModel, bgRemovePipeline);" in ANDROID_SERVER
    assert "edited = sharpenBitmap(source, jsonInt(body, \"amount\", 50));" in ANDROID_SERVER
    assert "This Android standalone edit is not available locally" not in ANDROID_SERVER


def test_android_bgremove_has_local_onnx_rembg_dependency():
    gradle = (ROOT / "android" / "app" / "build.gradle").read_text(encoding="utf-8")
    assert 'implementation files("libs/onnxruntime-android-1.26.0-patched-loader.jar")' in gradle
    assert '"**/libonnxruntime.so"' in gradle
    assert '"**/libonnxruntime4j_jni.so"' in gradle
    assert 'path = file("src/main/cpp/CMakeLists.txt")' in gradle
    assert "useLegacyPackaging = true" in gradle
    assert 'private static final String LOCAL_REMBG_MODEL_ASSET = "models/u2netp.onnx";' in ANDROID_SERVER
    assert 'private static final String LOCAL_REMBG_MODEL_URL = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2netp.onnx";' in ANDROID_SERVER
    assert 'private static final String SILUETA_REMBG_MODEL_URL = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/silueta.onnx";' in ANDROID_SERVER
    assert 'private static final String ISNET_REMBG_MODEL_URL = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/isnet-general-use.onnx";' in ANDROID_SERVER
    assert "new DynamicOnnxRuntime()" in ANDROID_SERVER
    assert "rembgRuntime.runMask(appContext, model, input, inputSize)" in ANDROID_SERVER
    assert 'static final String PACKAGE_NAME = "onnxruntime-android";' in DYNAMIC_ONNX
    assert 'new File(new File(context.getFilesDir(), "runtime"), PACKAGE_NAME + "-" + VERSION)' in DYNAMIC_ONNX
    assert "NativeRuntimeLoader.dlopen(new File(nativeDir, \"libonnxruntime.so\").getAbsolutePath(), true)" in DYNAMIC_ONNX
    assert "System.load(new File(nativeDir, \"libonnxruntime4j_jni.so\").getAbsolutePath())" in DYNAMIC_ONNX
    assert 'System.setProperty("onnxruntime.native.onnxruntime4j_jni.skip", "true")' in DYNAMIC_ONNX
    assert 'System.loadLibrary("odysseus_native_loader")' in NATIVE_LOADER
    assert "RTLD_GLOBAL" in NATIVE_LOADER_C
    assert "dlopen(path, flags)" in NATIVE_LOADER_C
    assert "patched-loader.jar" in PATCH_ONNX_LOADER_SCRIPT
    assert "Android branch calls System.loadLibrary()" in PATCH_ONNX_LOADER_SCRIPT
    assert 'Class.forName("ai.onnxruntime.OrtEnvironment", true, appLoader)' in DYNAMIC_ONNX
    assert 'getMethod("createSession", String.class, sessionOptionsClass)' in DYNAMIC_ONNX
    assert "RembgModelChoice choice = resolveLocalRembgModel(requestedModel);" in ANDROID_SERVER
    assert "installedRembgModel(ISNET_REMBG_FILENAME, ISNET_REMBG_EXPECTED_BYTES)" in ANDROID_SERVER
    assert "cachedModelFile(choice.assetName, choice.url, choice.filename)" in ANDROID_SERVER
    assert "copyAssetIfAvailable(assetName, out)" in ANDROID_SERVER
    assert "downloadModelFile(modelUrl, out)" in ANDROID_SERVER
    assert "flattenMask(raw, inputSize, inputSize)" in DYNAMIC_ONNX
    assert "softMaskBitmap(mask, inputSize, inputSize)" in ANDROID_SERVER


def test_android_cookbook_lists_rembg_model_dependencies():
    assert ".put(\"packages\", mobileRembgModelPackages())" in ANDROID_SERVER
    assert "private JSONArray mobileRembgModelPackages()" in ANDROID_SERVER
    assert "mobileOnnxRuntimePackage()" in ANDROID_SERVER
    assert '"onnxruntime-android"' in ANDROID_SERVER
    assert '"android-runtime/install"' in ANDROID_SERVER
    assert "DynamicOnnxRuntime.install(appContext)" in ANDROID_SERVER
    assert '"rembg-silueta"' in ANDROID_SERVER
    assert '"rembg-isnet-general-use"' in ANDROID_SERVER
    assert '"silueta.onnx"' in ANDROID_SERVER
    assert '"isnet-general-use.onnx"' in ANDROID_SERVER
    assert 'if ("POST".equals(request.method) && "rembg-models/install".equals(tail))' in ANDROID_SERVER
    assert "private JSONObject installMobileRembgModel(JSONObject body)" in ANDROID_SERVER
    assert "conn.setReadTimeout(300000);" in ANDROID_SERVER


def test_android_bgremove_can_force_or_auto_select_downloaded_rembg_models():
    assert 'private static final String ISNET_REMBG_MODEL = "isnet-general-use";' in ANDROID_SERVER
    assert 'private static final String SILUETA_REMBG_MODEL = "silueta";' in ANDROID_SERVER
    assert "String rembgModel = requestedRembgModel(body);" in ANDROID_SERVER
    assert "String bgRemovePipeline = requestedBgRemovePipeline(body);" in ANDROID_SERVER
    assert "boolean forceProviderBgRemove = \"model\".equals(bgRemovePipeline);" in ANDROID_SERVER
    assert "boolean forceRembg = \"rembg\".equals(bgRemovePipeline);" in ANDROID_SERVER
    assert "boolean forceHeuristic = \"heuristic\".equals(bgRemovePipeline);" in ANDROID_SERVER
    assert "openAiCompatible || isImageEditModel(model)" in ANDROID_SERVER
    assert 'new String[]{"/images/remove-bg", "/images/background-remove", "/images/rembg"}' in ANDROID_SERVER
    bgremove_block = ANDROID_SERVER.split("private JSONObject runProviderBackgroundRemove", 1)[1].split(
        "private JSONObject runProviderSharpen", 1
    )[0]
    assert '"/images/edit"' not in bgremove_block
    assert '"/images/edits"' not in bgremove_block
    assert 'if (apiKey.isEmpty() && isOpenAIBase(base)) throw new IOException("OpenAI endpoint has no API key stored in Settings.");' in ANDROID_SERVER
    assert 'if (!apiKey.isEmpty()) conn.setRequestProperty("Authorization", "Bearer " + apiKey);' in ANDROID_SERVER
    assert "Image edit failed at /v1/images/edits" in ANDROID_SERVER
    assert "boolean likelyJsonOnly = shouldRetryJsonImageEdit(status, response);" in ANDROID_SERVER
    assert "Multipart /v1/images/edits failed" in ANDROID_SERVER
    assert "Image edit JSON retry failed at /v1/images/edits" in ANDROID_SERVER
    assert 'conn.setRequestProperty("Content-Type", "application/json");' in ANDROID_SERVER
    assert "private JSONObject postChatImageEdit" in ANDROID_SERVER
    assert 'new URL(base + "/chat/completions")' in ANDROID_SERVER
    assert '"data:image/png;base64," + encodeBitmapPng(src)' in ANDROID_SERVER
    assert "The second image is a mask" in ANDROID_SERVER
    assert "providerImageValueFromText(value)" in ANDROID_SERVER
    assert '"content", "message", "choices"' in ANDROID_SERVER
    image_edit_block = ANDROID_SERVER.split("private boolean isImageEditModel", 1)[1].split(
        "private boolean isImageGenerationModel", 1
    )[0]
    assert 'm.contains("flux")' not in image_edit_block
    assert "forceProviderBgRemove || (!forceRembg && !constrainedBgRemove && rembgModel.isEmpty())" in ANDROID_SERVER
    assert 'sendJson(out, 502, new JSONObject().put("error", "Selected image model failed: "' in ANDROID_SERVER
    assert 'sendJson(out, 400, new JSONObject().put("error", "Selected image model did not return a transparent background."));' in ANDROID_SERVER
    assert "used local background removal fallback" not in ANDROID_SERVER
    assert "private String requestedRembgModel(JSONObject body)" in ANDROID_SERVER
    assert "private String requestedBgRemovePipeline(JSONObject body)" in ANDROID_SERVER
    assert "return isKnownRembgModel(explicit) ? explicit : \"\";" in ANDROID_SERVER
    assert "return new RembgModelChoice(ISNET_REMBG_MODEL, ISNET_REMBG_FILENAME" in ANDROID_SERVER
    assert "return new RembgModelChoice(SILUETA_REMBG_MODEL, SILUETA_REMBG_FILENAME" in ANDROID_SERVER
    assert "return new RembgModelChoice(U2NETP_REMBG_MODEL, U2NETP_REMBG_FILENAME" in ANDROID_SERVER
    assert "(!model.isEmpty() && !isKnownRembgModel(model))" in ANDROID_SERVER


def test_android_bgremove_uses_strength_and_requires_alpha_from_providers():
    assert "double bgRemoveStrength = body.has(\"strength\")" in ANDROID_SERVER
    assert "normalizedStrength(body, \"bg_strength\", 0.7)" in ANDROID_SERVER
    assert "applySampledBackgroundCutout(pixels, w, h, sizedBackgroundHint, sizedHint, strength)" in ANDROID_SERVER
    assert "applyBackgroundSampleToRembgResult(" in ANDROID_SERVER
    assert "source, modelCutout, hintMask, backgroundHint, strength);" in ANDROID_SERVER
    assert 'lastBgRemoveSource = (modelSource == null || modelSource.isEmpty() ? "onnx" : modelSource) + "+sampled-background";' in ANDROID_SERVER
    assert "providerResultHasMeaningfulTransparency(provider)" in ANDROID_SERVER
    assert "localThreshold = (int) Math.round(18 + 70 * s)" in ANDROID_SERVER
    assert "meanThreshold = (int) Math.max(28, Math.min(220" in ANDROID_SERVER


def test_android_bgremove_sample_strokes_protect_subject_inside_closed_loops():
    assert "byte[] subjectKeep = buildPortraitProtectionMask(pixels, hintPixels, w, h);" in ANDROID_SERVER
    assert "byte[] workArea = buildEnclosedStrokeRegion(hintPixels, w, h);" in ANDROID_SERVER
    assert "byte[] edgeWall = dilateByteMask(buildEdgeWallMask" in ANDROID_SERVER
    assert "workArea != null && workArea[idx] != 0" not in ANDROID_SERVER
    assert "if (!directlySampled && keep != null && maskOpacity(keep[idx]) > MASK_OPACITY_THRESHOLD) return tail;" in ANDROID_SERVER
    assert "if (!directlySampled && keep != null && maskOpacity(keep[i]) > MASK_OPACITY_THRESHOLD) continue;" in ANDROID_SERVER
    assert "protectedSubjectBlocksRemoval(pixels[idx], subjectKeep[idx]" in ANDROID_SERVER
    assert "protectedSubjectBlocksRemoval(pixels[i], subjectKeep[i]" in ANDROID_SERVER
    assert "if (keep != null && maskOpacity(keep[i]) > MASK_OPACITY_THRESHOLD) continue;" not in ANDROID_SERVER
    assert "byte[] sampleSeed = new byte[count];" in ANDROID_SERVER
    assert "byte[] expandedSeed = expandSampledBackgroundSeed(" in ANDROID_SERVER
    assert "clearMaskWhere(edgeWall, expandedSeed);" in ANDROID_SERVER
    assert "if (expandedSeed[i] == 0) continue;" in ANDROID_SERVER
    assert "background = recoverSampledBackgroundIslands(" in ANDROID_SERVER
    assert "private byte[] recoverSampledBackgroundIslands" in ANDROID_SERVER
    assert "isGloballySampledBackgroundCandidate(" in ANDROID_SERVER
    assert "stronglyMatchesSampledBackground(" in ANDROID_SERVER
    assert "return true;" in ANDROID_SERVER.split("private boolean protectedSubjectBlocksRemoval", 1)[1].split("private boolean stronglyMatchesSampledBackground", 1)[0]
    assert "int bodyTop = Math.max(0, maxY - Math.round(skinH * 0.10f));" in ANDROID_SERVER
    assert "private boolean looksLikePortraitBodyPixel" in ANDROID_SERVER


def test_android_bgremove_plain_fallback_protects_portraits():
    plain_fallback = ANDROID_SERVER.split("private void applyHeuristicBackgroundCutout", 1)[1].split(
        "private int enqueueBackgroundCandidate", 1
    )[0]
    assert "byte[] subjectKeep = buildPortraitProtectionMask(pixels, protectSampleMask, w, h);" in plain_fallback
    assert "byte[] edgeWall = dilateByteMask(" in plain_fallback
    assert "enqueueBackgroundCandidate(pixels, background, subjectKeep, edgeWall" in plain_fallback
    enqueue = ANDROID_SERVER.split("private int enqueueBackgroundCandidate", 1)[1].split(
        "private Bitmap inpaintBitmap", 1
    )[0]
    assert "if (subjectKeep != null && subjectKeep[idx] != 0) return tail;" in enqueue
    assert "if (edgeWall != null && edgeWall[idx] != 0) return tail;" in enqueue


def test_pc_bgremove_sample_strokes_bound_search_without_flooding_subject():
    pc_route = (ROOT / "routes" / "gallery_routes.py").read_text(encoding="utf-8")
    assert "requested_rembg_model = str(" in pc_route
    assert "bg_remove_pipeline = \"model\"" in pc_route
    assert "bg_remove_pipeline = \"rembg\"" in pc_route
    assert "bg_remove_pipeline = \"heuristic\"" in pc_route
    assert "await _remove_with_provider()" in pc_route
    assert "def _model_prefers_openai_edit(model_name):" in pc_route
    assert 'openai_style_edit = "api.openai.com" in base or _model_prefers_openai_edit(model)' in pc_route
    assert 'r = await client.post(f"{base}/images/edits", headers=headers, data=data, files=files)' in pc_route
    assert "def _wants_json_image_edit(status_code, text):" in pc_route
    assert 'jr = await client.post(f"{base}/images/edits", headers=headers, json=json_payload)' in pc_route
    assert "async def _chat_image_edit(previous_error=\"\"):" in pc_route
    assert 'cr = await client.post(f"{base}/chat/completions", headers=headers, json=chat_payload)' in pc_route
    assert '"content", "message", "choices"' in pc_route
    assert 'return {"error": f"Selected image model failed: {str(provider_error)[:260]}"}' in pc_route
    assert "used local background removal fallback" not in pc_route
    provider_block = pc_route.split("async def _remove_with_provider():", 1)[1].split(
        "def _subject_keep_mask", 1
    )[0]
    assert "_model_name_prefers_image_edit_endpoint(model)" in provider_block
    assert "_visible_enabled_endpoint_for_id(db, selected_endpoint_id" in provider_block
    assert '"/images/edit"' not in provider_block
    assert "def _preferred_rembg_models():" in pc_route
    assert "for model_name in (\"isnet-general-use\", \"silueta\")" in pc_route
    assert "cut = _remove_with_preferred_rembg(crop)" in pc_route
    assert 'if bg_remove_pipeline in {"auto", "heuristic", "rembg"} and background_hint is not None:' in pc_route
    assert "allow_model=bg_remove_pipeline == \"rembg\"" in pc_route
    assert 'source = "rembg+heuristic" if bg_remove_pipeline == "rembg" else "heuristic"' in pc_route
    assert "sample_seed = (np.array(sample_mask, dtype=np.uint8) > 8) & (alpha > 8)" in pc_route
    assert "ImageFilter.MaxFilter(7)" in pc_route
    assert "seed &= broad_similar & (alpha > 8)" in pc_route
    assert "def _enclosed_stroke_region(mask_img):" in pc_route
    assert "work_area = _enclosed_stroke_region(sample_mask)" in pc_route
    assert "return enclosed | seed" in pc_route
    assert "strict_similar = (" in pc_route
    assert "soft_protected = np.zeros" in pc_route
    assert "protected = hard_protected | (soft_protected & ~strict_similar)" in pc_route
    assert "edge_wall &= ~broad_similar" in pc_route
    assert "if edge_wall[y, x] and not seed[y, x]:" in pc_route
    assert "if work_area is not None:" in pc_route
    work_area_branch = pc_route.split("if work_area is not None:", 1)[1].split("if broad_similar[y, x]:", 1)[0]
    assert "return True" not in work_area_branch
    assert "def _recover_sampled_background_islands(background):" in pc_route
    assert "eligible = broad_similar & ~background & ~protected & ~edge_wall" in pc_route
    assert "strict_ratio = strict_count / max(1, len(component))" in pc_route
    assert "bg = _recover_sampled_background_islands(bg)" in pc_route
    assert "A closed cyan stroke is only a search boundary" in pc_route


def test_pc_inpaint_routes_openai_compatible_endpoints_through_image_edit():
    pc_route = (ROOT / "routes" / "gallery_routes.py").read_text(encoding="utf-8")
    inpaint_block = pc_route.split('@router.post("/api/image/inpaint")', 1)[1].split(
        "# ---- POST /api/image/harmonize", 1
    )[0]
    assert "def _is_openai_compatible_image_edit_base(value: str) -> bool:" in inpaint_block
    assert '"compatible-mode"' in inpaint_block
    assert '"aliyuncs.com"' in inpaint_block
    assert "is_openai_style_edit = _is_openai_compatible_image_edit_base(base)" in inpaint_block
    assert "if is_openai_style_edit:" in inpaint_block
    assert "_model_name_prefers_image_edit_endpoint(chosen_model)" in inpaint_block
    assert "_visible_enabled_endpoint_for_id(db, endpoint_id" in inpaint_block
    assert 'jr = await client.post(f"{base}/images/edits", headers=headers, json=json_payload)' in inpaint_block
    assert 'cr = await client.post(f"{base}/chat/completions", headers=headers, json=chat_payload)' in inpaint_block
    assert "The second image is a mask" in inpaint_block


def test_editor_hides_bgremove_sample_overlay_after_successful_sampled_run():
    ai_rembg = (ROOT / "static" / "js" / "editor" / "ai-rembg.js").read_text(encoding="utf-8")
    success_block = ai_rembg.split("if (state.layers.length > before) {", 1)[1].split("composite();", 1)[0]
    assert "if (backgroundMask)" in success_block
    assert "state.rembgSampleVisible = false;" in success_block
    assert "syncSampleVisButton();" in success_block


def test_editor_bgremove_exposes_pipeline_selector():
    controls = (ROOT / "static" / "js" / "editor" / "build" / "controls.js").read_text(encoding="utf-8")
    ai_rembg = (ROOT / "static" / "js" / "editor" / "ai-rembg.js").read_text(encoding="utf-8")
    state_js = (ROOT / "static" / "js" / "editor" / "state.js").read_text(encoding="utf-8")
    assert 'id="ge-rembg-pipeline"' in controls
    assert '<option value="model">Local/API model</option>' in controls
    assert '<option value="rembg">Natural rembg</option>' in controls
    assert '<option value="heuristic">Heuristic sample</option>' in controls
    assert "payload.bg_remove_pipeline = rembgPipelineValue();" in ai_rembg
    assert "localStorage.setItem('ge-rembg-pipeline'" in ai_rembg
    assert "rembgPipeline: 'auto'" in state_js


def test_android_inpaint_keeps_edit_capable_image_models():
    assert "isChatModel(id) || isImageEditModel(id) || isImageGenerationModel(id)" in ANDROID_SERVER
    assert "m.contains(\"dall-e-2\")" in ANDROID_SERVER
    assert "m.contains(\"dall-e-3\")" in ANDROID_SERVER
    assert "m.contains(\"img2img\")" in ANDROID_SERVER
    assert "m.contains(\"paint-by-example\")" in ANDROID_SERVER
    assert "m.contains(\"pix2pix\")" in ANDROID_SERVER
    assert "dall-e-3 does not support image edits" in ANDROID_SERVER


def test_android_standalone_routes_image_generation_before_chat_backend():
    stream = ANDROID_SERVER.split("private void streamChat", 1)[1].split(
        "private void streamMobileImageGeneration", 1
    )[0]
    assert "String videoPrompt = mobileVideoGenerationPrompt(userText, model);" in stream
    assert "String imagePrompt = mobileImageGenerationPrompt(userText, model);" in stream
    assert "streamMobileVideoGeneration(out, sid, history, videoPrompt, videoEndpoint, model, workspaceRejected);" in stream
    assert "JSONObject imageEndpoint = endpoint;" in stream
    assert "JSONObject modelEndpoint = endpointForImageGenerationModel(model);" in stream
    assert "streamMobileImageGeneration(out, sid, history, imagePrompt, imageEndpoint, model, workspaceRejected);" in stream
    assert "callChat(endpoint, model, modelMessages)" in stream
    assert stream.index("streamMobileVideoGeneration(out, sid, history, videoPrompt, videoEndpoint, model, workspaceRejected);") < stream.index(
        "streamMobileImageGeneration(out, sid, history, imagePrompt, imageEndpoint, model, workspaceRejected);"
    )
    assert stream.index("streamMobileImageGeneration(out, sid, history, imagePrompt, imageEndpoint, model, workspaceRejected);") < stream.index(
        "callChat(endpoint, model, modelMessages)"
    )


def test_android_standalone_image_generation_supports_gemini_openai_and_gallery_save():
    assert "private String mobileImageGenerationPrompt(String userText, String model)" in ANDROID_SERVER
    assert "private boolean looksLikeExistingMobileMediaQuestion(String message)" in ANDROID_SERVER
    assert "private JSONObject generateMobileImageReply" in ANDROID_SERVER
    assert "private JSONArray mobileImageGenerationChoices" in ANDROID_SERVER
    assert "private JSONObject endpointForImageGenerationModel" in ANDROID_SERVER
    assert "private boolean endpointCanServeSelectedImageModel" in ANDROID_SERVER
    assert "private String mobileImageGenerationFailureDetail" in ANDROID_SERVER
    assert "For loaded local PC models, LM Studio, ComfyUI, or Cookbook routing" in ANDROID_SERVER
    assert "private boolean isImageGenerationModel(String model)" in ANDROID_SERVER
    assert "private boolean isGeminiImageModel(String model)" in ANDROID_SERVER
    assert "private boolean isQwenImageModel(String model)" in ANDROID_SERVER
    assert "private boolean isDashScopeImageEndpoint(String baseUrl, JSONObject ep)" in ANDROID_SERVER
    assert "private String qwenDashscopeGenerationUrl(String baseUrl)" in ANDROID_SERVER
    assert "private String qwenDashscopeSize(String model, String size)" in ANDROID_SERVER
    assert "private JSONObject postGeminiImageGeneration" in ANDROID_SERVER
    assert "private JSONObject postQwenDashscopeImageGeneration" in ANDROID_SERVER
    assert '"responseModalities", new JSONArray().put("TEXT").put("IMAGE")' in ANDROID_SERVER
    assert '"imageConfig", new JSONObject()' in ANDROID_SERVER
    assert 'conn.setRequestProperty("x-goog-api-key", apiKey);' in ANDROID_SERVER
    assert 'new URL(base + "/images/generations")' in ANDROID_SERVER
    assert '"watermark", false' in ANDROID_SERVER
    assert "qwenDashscopeSize(model, size)" in ANDROID_SERVER
    assert "multimodal-generation/generation" in ANDROID_SERVER
    assert "private JSONObject saveGeneratedGalleryImage" in ANDROID_SERVER
    assert 'put("url", "/api/generated-image/" + filename)' in ANDROID_SERVER
    assert "private void streamMobileImageGeneration" in ANDROID_SERVER
    assert "private void streamMobileMediaReply" in ANDROID_SERVER
    assert 'put("type", "tool_start")' in ANDROID_SERVER
    assert 'put("type", "tool_output")' in ANDROID_SERVER
    assert 'put("tool", "generate_image")' in ANDROID_SERVER
    assert 'put("image_url", media.optString("image_url", ""))' in ANDROID_SERVER
    assert "private boolean isQuotaOrProviderAvailabilityError" in ANDROID_SERVER
    assert '"inlineData", "inline_data"' in ANDROID_SERVER
    gemini_dispatch = ANDROID_SERVER.split("private JSONObject postMobileImageGeneration", 1)[1].split(
        "private JSONObject postOpenAiCompatibleImageGeneration", 1
    )[0]
    assert "if (isGeminiImageEndpoint(base, choice) || isGeminiImageModel(model))" in gemini_dispatch
    assert "if (isQwenImageModel(model) && isDashScopeImageEndpoint(base, choice))" in gemini_dispatch
    assert "return postQwenDashscopeImageGeneration(choice, prompt, size);" in gemini_dispatch
    openai_generation = ANDROID_SERVER.split("private JSONObject postOpenAiCompatibleImageGeneration", 1)[1].split(
        "private JSONObject postGeminiImageGeneration", 1
    )[0]
    assert "return postGeminiImageGeneration(choice, prompt, size);" in openai_generation
    assert "postOpenAiCompatibleImageGenerationPayload(base, apiKey, payload)" in openai_generation
    assert 'retryPayload.remove("quality");' in openai_generation
    assert 'boolean gptImageModel = modelLower.startsWith("gpt-image") || modelLower.contains("chatgpt-image");' in openai_generation
    assert 'boolean localDiffusionModel = !gptImageModel && !dalleModel;' in openai_generation
    assert 'if (gptImageModel || localDiffusionModel) {' in openai_generation
    assert 'payload.put("response_format", "b64_json");' not in openai_generation
    assert "&& isImageGenerationModel(model)" not in gemini_dispatch


def test_android_standalone_video_generation_supports_dashscope_and_media_output():
    assert "private String mobileVideoGenerationPrompt(String userText, String model)" in ANDROID_SERVER
    assert "private JSONObject generateMobileVideoReply" in ANDROID_SERVER
    assert "private JSONArray mobileVideoGenerationChoices" in ANDROID_SERVER
    assert "private JSONObject endpointForVideoGenerationModel" in ANDROID_SERVER
    assert "private boolean endpointCanServeSelectedVideoModel" in ANDROID_SERVER
    assert "private boolean isVideoGenerationModel(String model)" in ANDROID_SERVER
    assert "private boolean isDashScopeVideoModel(String model)" in ANDROID_SERVER
    assert "private boolean isDashScopeVideoEndpoint(String baseUrl, JSONObject ep)" in ANDROID_SERVER
    assert "private String dashscopeVideoGenerationUrl(String baseUrl)" in ANDROID_SERVER
    assert "private String dashscopeTaskUrl(String baseUrl, String taskId)" in ANDROID_SERVER
    assert "private JSONObject postDashScopeVideoGeneration" in ANDROID_SERVER
    assert 'conn.setRequestProperty("X-DashScope-Async", "enable");' in ANDROID_SERVER
    assert "video-generation/video-synthesis" in ANDROID_SERVER
    assert 'firstJsonStringForKey(created, "task_id", 0)' in ANDROID_SERVER
    assert 'firstJsonStringForKey(status, "task_status", 0)' in ANDROID_SERVER
    assert "normalizeVideoResponse(status.toString())" in ANDROID_SERVER
    assert "private JSONObject saveGeneratedGalleryVideo" in ANDROID_SERVER
    assert 'put("media_type", "video")' in ANDROID_SERVER
    assert 'put("tool", "generate_video")' in ANDROID_SERVER
    assert 'put("media_url", saved.optString("url", ""))' in ANDROID_SERVER
    assert 'put("url", "/api/generated-image/" + filename)' in ANDROID_SERVER

    video_dispatch = ANDROID_SERVER.split("private JSONObject postMobileVideoGeneration", 1)[1].split(
        "private JSONObject postDashScopeVideoGeneration", 1
    )[0]
    assert "if (isDashScopeVideoModel(model) && isDashScopeVideoEndpoint(base, choice))" in video_dispatch
    assert "return postDashScopeVideoGeneration(choice, prompt, durationSeconds, resolution);" in video_dispatch
    assert "return postOpenAiCompatibleVideoGeneration(choice, prompt, durationSeconds, aspectRatio, resolution);" in video_dispatch


def test_android_selected_image_model_resolves_matching_endpoint_before_fallbacks():
    choices = ANDROID_SERVER.split("private JSONArray mobileImageGenerationChoices", 1)[1].split(
        "private void addMobileImageChoice", 1
    )[0]
    assert "boolean requestedImageModel = isImageGenerationModel(requestedModel);" in choices
    assert "addMobileImageChoice(choices, seen, endpointForImageGenerationModel(requestedModel), requestedModel);" in choices
    assert "return choices;" in choices.split("if (requestedImageModel)", 1)[1].split("JSONArray endpoints", 1)[0]

    add_choice = ANDROID_SERVER.split("private void addMobileImageChoice", 1)[1].split(
        "private JSONObject endpointForImageGenerationModel", 1
    )[0]
    assert "boolean strictRequestedModel = !model.isEmpty();" in add_choice
    assert "if (!endpointCanServeSelectedImageModel(endpoint, model)) return;" in add_choice
    assert "model = firstImageGenerationModel(endpoint);" in add_choice

    resolver = ANDROID_SERVER.split("private JSONObject endpointForImageGenerationModel", 1)[1].split(
        "private boolean looksLikeImageGenerationEndpoint", 1
    )[0]
    assert "endpointHasModel(ep, requested) && endpointCanServeSelectedImageModel(ep, requested)" in resolver
    assert "isGeminiImageModel(providerRequested) && isGeminiImageEndpoint(base, endpoint)" in resolver
    assert "isOpenAIBase(base)" in resolver


def test_android_session_endpoint_resolution_prefers_exact_selected_model_match():
    resolver = ANDROID_SERVER.split("private JSONObject endpointForSession", 1)[1].split(
        "private JSONObject endpointForChatUrl", 1
    )[0]
    assert "JSONObject modelMatch = model.isEmpty() ? null : endpointForModel(model);" in resolver
    assert "if (modelMatch != null && (urlMatch == null || !endpointHasModel(urlMatch, model)))" in resolver
    assert resolver.index("JSONObject modelMatch = model.isEmpty() ? null : endpointForModel(model);") < resolver.index(
        "if (urlMatch != null && (model.isEmpty() || endpointCanServeModel(urlMatch, model)))"
    )


def test_android_standalone_squashes_reply_spacing_before_streaming():
    stream = ANDROID_SERVER.split("private void streamChat", 1)[1].split(
        "private void streamMobileMediaReply", 1
    )[0]
    assert "reply = squashMobileReplySpacing(reply);" in stream
    assert stream.index("reply = callChat(endpoint, model, modelMessages);") < stream.index(
        "reply = squashMobileReplySpacing(reply);"
    ) < stream.index("history.put(new JSONObject().put(\"role\", \"assistant\")")
    assert "private String squashMobileReplySpacing(String raw)" in ANDROID_SERVER
    assert ".replaceAll(\"[\\\\u00A0\\\\u1680\\\\u2000-\\\\u200A\\\\u202F\\\\u205F\\\\u3000]\", \" \")" in ANDROID_SERVER
    assert ".append(line.substring(leading).replaceAll(\"[ \\\\t]{2,}\", \" \"))" in ANDROID_SERVER


def test_android_inpaint_accepts_common_provider_response_shapes():
    assert 'new String[]{"/images/inpaint", "/images/edits", "/images/edit", "/api/image/inpaint"}' in ANDROID_SERVER
    assert 'String target = path.startsWith("/api/") ? baseRoot + path : base + path;' in ANDROID_SERVER
    assert "private String firstProviderImageValue(Object node, int depth)" in ANDROID_SERVER
    assert '"image_base64"' in ANDROID_SERVER
    assert '"imageUrl"' in ANDROID_SERVER
    assert '"artifacts"' in ANDROID_SERVER
    assert "providerImageValueToBase64(image)" in ANDROID_SERVER
    assert "providerNoImageDetail(response)" in ANDROID_SERVER
    assert "server returned no image (keys:" in ANDROID_SERVER


def test_pc_inpaint_accepts_common_provider_routes_and_response_shapes():
    pc_route = (ROOT / "routes" / "gallery_routes.py").read_text(encoding="utf-8")
    inpaint_block = pc_route.split("async def inpaint_proxy(request: Request):", 1)[1].split(
        "# ---- POST /api/image/harmonize", 1
    )[0]
    assert 'paths = ("/images/inpaint", "/images/edits", "/images/edit", "/api/image/inpaint")' in inpaint_block
    assert "for idx, path in enumerate(paths):" in inpaint_block
    assert 'target = f"{base_root}{path}" if path.startswith("/api/") else f"{base}{path}"' in inpaint_block
    assert "def _first_provider_image_value" in inpaint_block
    assert '"image_base64", "imageBase64"' in inpaint_block
    assert '"artifact", "artifacts"' in inpaint_block
    assert 'payload["mask_image"] = payload["mask"]' in inpaint_block
    assert "server returned no image (keys:" in inpaint_block


def test_android_inpaint_rejects_oversized_payloads_before_bitmap_decode():
    assert "private static final int MAX_IMAGE_TOOL_BODY_BYTES = 32 * 1024 * 1024;" in ANDROID_SERVER
    assert 'request.path.startsWith("/api/image/") && contentLength > MAX_IMAGE_TOOL_BODY_BYTES' in ANDROID_SERVER
    assert "request.bodyTooLarge = true;" in ANDROID_SERVER
    assert "if (request.bodyTooLarge) {" in ANDROID_SERVER
    image_tool = ANDROID_SERVER.split("private void routeImageTool", 1)[1].split(
        "private Bitmap decodeJsonBitmap", 1
    )[0]
    guarded_block = image_tool.split("try {", 1)[1].split("} catch (OutOfMemoryError oom)", 1)[0]
    assert 'Bitmap source = decodeJsonBitmap(body, "image");' in guarded_block
