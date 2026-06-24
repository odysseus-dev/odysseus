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


def test_android_calendar_intent_does_not_match_classroom_substrings():
    detector = ANDROID_SERVER.split("private boolean mentionsCalendarIntent", 1)[1].split(
        "private List<JSONObject> activeCalendarEventsSorted", 1
    )[0]

    assert "replaceAll(\"[^a-z0-9']+\", \" \")" in detector
    assert 'q.contains(" class ")' in detector
    assert 'q.contains(" classes ")' in detector
    assert 'q.contains("class")' not in detector
    assert 'q.contains("classes")' not in detector
