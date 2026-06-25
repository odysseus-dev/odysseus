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
MAIN_ACTIVITY = (
    ROOT
    / "android"
    / "app"
    / "src"
    / "main"
    / "java"
    / "com"
    / "odysseus"
    / "simplesignal"
    / "MainActivity.java"
).read_text(encoding="utf-8")


def test_android_implements_chatgpt_subscription_device_flow_routes():
    assert '"/api/chatgpt-subscription/device/start"' in ANDROID_SERVER
    assert '"/api/chatgpt-subscription/device/poll"' in ANDROID_SERVER
    assert '"/api/chatgpt-subscription/device/cancel"' in ANDROID_SERVER
    assert "private JSONObject startChatGptSubscriptionDeviceFlow()" in ANDROID_SERVER
    assert "private JSONObject pollChatGptSubscriptionDeviceFlow" in ANDROID_SERVER
    assert "chatGptDeviceFlows.put(pollId" in ANDROID_SERVER
    assert '"device_auth_id"' in ANDROID_SERVER
    assert '"user_code"' in ANDROID_SERVER
    assert '"verification_uri"' in ANDROID_SERVER
    assert '"poll_id"' in ANDROID_SERVER


def test_android_provisions_chatgpt_subscription_endpoint_without_exposing_tokens():
    assert 'private static final String CHATGPT_SUBSCRIPTION_PROVIDER = "chatgpt-subscription";' in ANDROID_SERVER
    assert 'private static final String CHATGPT_SUBSCRIPTION_LABEL = "Codex Subscription";' in ANDROID_SERVER
    assert 'private static final String CHATGPT_SUBSCRIPTION_BASE_URL = "https://chatgpt.com/backend-api/codex";' in ANDROID_SERVER
    assert "private JSONObject provisionChatGptSubscriptionEndpoint" in ANDROID_SERVER
    assert '.put("access_token", accessToken)' in ANDROID_SERVER
    assert '.put("refresh_token", refreshToken)' in ANDROID_SERVER
    assert '.put("provider", CHATGPT_SUBSCRIPTION_PROVIDER)' in ANDROID_SERVER
    public_endpoint = ANDROID_SERVER.split("private JSONObject publicEndpoint", 1)[1].split(
        "private JSONObject addEndpoint", 1
    )[0]
    assert 'put("access_token"' not in public_endpoint
    assert 'put("refresh_token"' not in public_endpoint
    assert '|| !ep.optString("refresh_token").isEmpty()' in public_endpoint


def test_android_chatgpt_subscription_uses_codex_responses_runtime():
    assert 'CHATGPT_SUBSCRIPTION_BASE_URL + "/responses"' in ANDROID_SERVER
    assert 'CHATGPT_SUBSCRIPTION_BASE_URL + "/models?client_version=1.0.0"' in ANDROID_SERVER
    assert "private boolean isChatGptSubscriptionEndpoint" in ANDROID_SERVER
    assert "private String callChatGptSubscription" in ANDROID_SERVER
    assert "private JSONObject buildChatGptResponsesPayload" in ANDROID_SERVER
    assert '.put("stream", true)' in ANDROID_SERVER
    assert '.put("store", false)' in ANDROID_SERVER
    assert '"response.output_text.delta"' in ANDROID_SERVER
    assert '"response.completed"' in ANDROID_SERVER
    assert "refreshChatGptEndpointTokens(endpoint)" in ANDROID_SERVER
    assert "private boolean chatGptAccessTokenExpiring" in ANDROID_SERVER
    assert "private boolean modelRestrictsTemperature" in ANDROID_SERVER


def test_android_chatgpt_subscription_preserves_responses_image_blocks():
    assert "private JSONArray chatGptResponsesContent" in ANDROID_SERVER
    assert "private void appendChatGptResponsesPart" in ANDROID_SERVER
    assert "private String chatGptResponsesImageUrl" in ANDROID_SERVER
    assert '.put("type", "input_image").put("image_url", imageUrl)' in ANDROID_SERVER
    assert '("image_url".equals(partType) || "input_image".equals(partType) || "image".equals(partType))' in ANDROID_SERVER
    assert '.put("content", chatGptResponsesContent(role, msg.opt("content")))' in ANDROID_SERVER


def test_android_opens_provider_device_auth_links_externally():
    assert "private boolean isExternalAuthUrl(Uri uri)" in MAIN_ACTIVITY
    assert '"auth.openai.com".equals(host)' in MAIN_ACTIVITY
    assert '"github.com".equals(host) && path.startsWith("/login/device")' in MAIN_ACTIVITY
    assert "if (request.isForMainFrame() && isExternalAuthUrl(uri))" in MAIN_ACTIVITY
    assert "openExternalUri(uri);" in MAIN_ACTIVITY
