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
    detector = ANDROID_SERVER.split("private boolean isImageGenerationModel", 1)[1].split(
        "private boolean isVideoGenerationModel", 1
    )[0]

    assert 'm.contains("z-image")' in detector
    assert 'm.contains("z_image")' in detector
    assert 'm.contains("zai-image")' in detector
    assert 'm.contains("zai_image")' in detector


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
