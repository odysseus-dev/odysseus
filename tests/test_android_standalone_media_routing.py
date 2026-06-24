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


def test_android_standalone_media_generation_bypasses_local_shortcuts():
    stream = ANDROID_SERVER.split("private void streamChat", 1)[1].split(
        "private void streamMobileImageGeneration", 1
    )[0]

    assert "String videoPrompt = mobileVideoGenerationPrompt(userText, model);" in stream
    assert "String imagePrompt = mobileImageGenerationPrompt(userText, model);" in stream
    assert "if (videoPrompt.isEmpty() && imagePrompt.isEmpty()) {" in stream
    assert stream.index("String imagePrompt = mobileImageGenerationPrompt(userText, model);") < stream.index(
        "String localCalendarReply = tryHandleMobileCalendarReadRequest(userText);"
    )
    assert stream.index("String imagePrompt = mobileImageGenerationPrompt(userText, model);") < stream.index(
        "String localGalleryReply = tryHandleMobileGalleryEditRequest(userText);"
    )
    assert stream.index("streamMobileImageGeneration(out, sid, history, imagePrompt, imageEndpoint, model, workspaceRejected);") < stream.index(
        "callChat(endpoint, model, modelMessages)"
    )


def test_android_selected_image_model_accepts_bare_descriptive_prompt():
    image_prompt = ANDROID_SERVER.split("private String mobileImageGenerationPrompt", 1)[1].split(
        "private String mobileVideoGenerationPrompt", 1
    )[0]

    assert "boolean selectedImageModel = isImageGenerationModel(model);" in image_prompt
    assert "if (!looksLikeMobileNonGenerationQuestion(prompt)) return prompt;" in image_prompt
    assert "looksLikeExistingMobileMediaQuestion(prompt)" in image_prompt


def test_android_recognizes_z_image_turbo_as_image_generation_model():
    detector = ANDROID_SERVER.split("private boolean isZImageModel", 1)[1].split(
        "private boolean isGeminiImageModel", 1
    )[0]
    image_generation = ANDROID_SERVER.split("private boolean isImageGenerationModel", 1)[1].split(
        "private boolean isVideoGenerationModel", 1
    )[0]

    assert "isZImageModel(m)" in image_generation
    assert 'm.contains("z-image")' in detector
    assert 'm.contains("z_image")' in detector
    assert 'm.contains("zai-image")' in detector
    assert 'm.contains("zai_image")' in detector


def test_android_adapts_aimlapi_z_image_payload_shape():
    detector = ANDROID_SERVER.split("private boolean isZImageModel", 1)[1].split(
        "private boolean isGeminiImageModel", 1
    )[0]
    openai_generation = ANDROID_SERVER.split("private JSONObject postOpenAiCompatibleImageGeneration", 1)[1].split(
        "private JSONObject postOpenAiCompatibleImageGenerationPayload", 1
    )[0]
    helper = ANDROID_SERVER.split("private boolean isAimlApiEndpoint", 1)[1].split(
        "private JSONObject postQwenDashscopeImageGeneration", 1
    )[0]

    assert 'm.contains("z/image")' in detector
    assert "if (zImageModel && isModelScopeEndpoint(base, choice))" in openai_generation
    assert 'put("model", zImageModel ? hostedZImageModel(base, choice, model) : model)' in openai_generation
    assert 'payload.put("image_size", aimlApiZImageSize(size));' in openai_generation
    assert 'payload.put("image_size", zImagePixelSize(size));' in openai_generation
    assert 'payload.put("quality"' not in openai_generation.split("if (!zImageModel", 1)[0]
    assert "postOpenAiCompatibleImageGenerationPayload(base, apiKey, payload, 90000)" in openai_generation
    assert '"alibaba/z-image-turbo"' in helper
    assert '"Tongyi-MAI/Z-Image-Turbo"' in helper
    assert '"landscape_16_9"' in helper
    assert '"portrait_9_16"' in helper
    assert '"square"' in helper


def test_android_supports_modelscope_z_image_async_tasks():
    openai_generation = ANDROID_SERVER.split("private JSONObject postOpenAiCompatibleImageGeneration", 1)[1].split(
        "private JSONObject postOpenAiCompatibleImageGenerationPayload", 1
    )[0]
    helper = ANDROID_SERVER.split("private boolean isModelScopeEndpoint", 1)[1].split(
        "private JSONObject postQwenDashscopeImageGeneration", 1
    )[0]
    parser = ANDROID_SERVER.split("private String firstProviderImageValue", 1)[1].split(
        "private String firstProviderVideoValue", 1
    )[0]

    assert "postModelScopeZImageGeneration(choice, prompt, size)" in openai_generation
    assert 'conn.setRequestProperty("X-ModelScope-Async-Mode", "true");' in helper
    assert 'conn.setRequestProperty("X-ModelScope-Task-Type", "image_generation");' in helper
    assert 'firstJsonStringForKey(new JSONObject(valueOr(response, "{}")), "task_id", 0)' in helper
    assert '"output_images"' in parser


def test_android_routes_dashscope_z_image_to_native_multimodal_endpoint():
    dispatch = ANDROID_SERVER.split("private JSONObject postMobileImageGeneration", 1)[1].split(
        "private JSONObject postOpenAiCompatibleImageGeneration", 1
    )[0]
    openai_generation = ANDROID_SERVER.split("private JSONObject postOpenAiCompatibleImageGeneration", 1)[1].split(
        "private JSONObject postOpenAiCompatibleImageGenerationPayload", 1
    )[0]
    endpoint_guard = ANDROID_SERVER.split("private boolean endpointCanServeSelectedImageModel", 1)[1].split(
        "private boolean endpointCanServeSelectedVideoModel", 1
    )[0]
    helper = ANDROID_SERVER.split("private String dashscopeZImageModel", 1)[1].split(
        "private JSONObject postQwenDashscopeImageGeneration", 1
    )[0]

    assert "isZImageModel(providerRequested) && isDashScopeImageEndpoint(base, endpoint)" in endpoint_guard
    assert "postDashScopeZImageGeneration(choice, prompt, size)" in dispatch
    assert "postDashScopeZImageGeneration(choice, prompt, size)" in openai_generation
    assert '"/services/aigc/multimodal-generation/generation"' in ANDROID_SERVER
    assert '"z-image-turbo"' in helper
    assert '"alibaba/z-image-turbo"' in helper
    assert '"tongyi-mai/z-image-turbo"' in helper
    assert '"input", new JSONObject()' in helper
    assert '"messages", new JSONArray()' in helper
    assert '"parameters", new JSONObject()' in helper
    assert '"prompt_extend", false' in helper
    assert 'return width + "*" + height;' in helper
    assert "postOpenAiCompatibleImageGenerationPayload(base, apiKey, payload, 90000)" in openai_generation


def test_android_recognizes_gemini_banana_and_imagen_image_models():
    canonical = ANDROID_SERVER.split("private String canonicalGeminiImageModel", 1)[1].split(
        "private boolean isImagenModel", 1
    )[0]
    detector = ANDROID_SERVER.split("private boolean isImageGenerationModel", 1)[1].split(
        "private boolean isVideoGenerationModel", 1
    )[0]
    imagen = ANDROID_SERVER.split("private boolean isImagenModel", 1)[1].split(
        "private boolean isGeminiImageModel", 1
    )[0]

    assert '"nano-banana".equals(lower)' in canonical
    assert '"nano-banana-2".equals(lower)' in canonical
    assert '"nano-banana-pro-preview".equals(lower)' in canonical
    assert '"nano-banana-pro".equals(lower)' in canonical
    assert 'return "gemini-2.5-flash-image";' in canonical
    assert 'return "gemini-3.1-flash-image";' in canonical
    assert 'return "gemini-3-pro-image-preview";' in canonical
    assert 'return "gemini-3-pro-image";' in canonical
    assert "isImagenModel(m)" in detector
    assert 'm.startsWith("imagen-")' in imagen


def test_android_routes_imagen_through_predict_endpoint():
    dispatch = ANDROID_SERVER.split("private JSONObject postMobileImageGeneration", 1)[1].split(
        "private JSONObject postOpenAiCompatibleImageGeneration", 1
    )[0]
    imagen_call = ANDROID_SERVER.split("private JSONObject postImagenImageGeneration", 1)[1].split(
        "private JSONObject postOpenAiImageEdit", 1
    )[0]

    assert "if (isImagenModel(model) && isGeminiImageEndpoint(base, choice))" in dispatch
    assert "return postImagenImageGeneration(choice, prompt, size);" in dispatch
    assert "new URL(imagenPredictUrl(base, model))" in imagen_call
    assert '"instances", new JSONArray()' in imagen_call
    assert '"sampleCount", 1' in imagen_call
    assert '"aspectRatio", aspectRatioFromSize(size)' in imagen_call
    assert '"imageSize", "1K"' in imagen_call


def test_android_imagen_response_shapes_are_parsed():
    helper = ANDROID_SERVER.split("private String imagenPredictUrl", 1)[1].split(
        "private String aspectRatioFromSize", 1
    )[0]
    parser = ANDROID_SERVER.split("private String firstProviderImageValue", 1)[1].split(
        "private String firstProviderVideoValue", 1
    )[0]

    assert '":predict"' in helper
    assert '"imageBytes"' in parser
    assert '"image_bytes"' in parser
    assert '"generatedImages"' in parser
    assert '"predictions"' in parser


def test_android_selected_video_model_accepts_bare_descriptive_prompt():
    video_prompt = ANDROID_SERVER.split("private String mobileVideoGenerationPrompt", 1)[1].split(
        "private String requestedMobileMediaGenerationKind", 1
    )[0]

    assert "boolean selectedVideoModel = isVideoGenerationModel(model);" in video_prompt
    assert "if (!looksLikeMobileNonGenerationQuestion(prompt)) return prompt;" in video_prompt


def test_android_bare_media_prompt_guard_preserves_questions_and_local_tools():
    guard = ANDROID_SERVER.split("private boolean looksLikeMobileNonGenerationQuestion", 1)[1].split(
        "private String requestedMobileMediaGenerationKind", 1
    )[0]

    assert "raw.endsWith(\"?\")" in guard
    assert "what|why|how|when|where|who|which" in guard
    assert "will|would|should)\\\\b" in guard
    assert "mentionsCalendarIntent(message)" in guard
    assert "mobileMentionsWorkspaceOrFiles(message)" in guard
    assert 'text.contains(" gallery ")' in guard


def test_android_calendar_intent_does_not_match_classroom_substrings():
    detector = ANDROID_SERVER.split("private boolean mentionsCalendarIntent", 1)[1].split(
        "private List<JSONObject> activeCalendarEventsSorted", 1
    )[0]

    assert "replaceAll(\"[^a-z0-9']+\", \" \")" in detector
    assert 'q.contains(" class ")' in detector
    assert 'q.contains(" classes ")' in detector
    assert 'q.contains("class")' not in detector
    assert 'q.contains("classes")' not in detector
