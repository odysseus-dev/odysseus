package com.odysseus.simplesignal;

import android.content.Context;
import android.content.SharedPreferences;
import android.content.res.AssetManager;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.os.Environment;
import android.util.Base64;
import android.util.Log;
import android.webkit.MimeTypeMap;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.URL;
import java.net.URLDecoder;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.Comparator;
import java.util.Date;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Random;
import java.util.TimeZone;
import java.util.UUID;

public class MobileBackendServer {
    private static final String TAG = "OdysseusMobileBackend";
    private static final String PREFS_NAME = "odysseus_mobile_backend";
    private static final String PREF_ENDPOINTS = "endpoints";
    private static final String PREF_SESSIONS = "sessions";
    private static final String PREF_DOCUMENTS = "documents";
    private static final String PREF_NOTES = "notes";
    private static final String PREF_GALLERY_IMAGES = "gallery_images";
    private static final String PREF_GALLERY_ALBUMS = "gallery_albums";
    private static final String PREF_CALENDAR_CALS = "calendar_cals";
    private static final String PREF_CALENDAR_EVENTS = "calendar_events";
    private static final String PREF_COOKBOOK_STATE = "cookbook_state";
    private static final String PREF_RESEARCH_ITEMS = "research_items";
    private static final String PREF_PERSONAL_DIRECTORIES = "personal_directories";
    private static final String PREF_SETTINGS = "settings";
    private static final String PREF_DEFAULT_ENDPOINT = "default_endpoint_id";
    private static final String LOCAL_REMBG_MODEL_ASSET = "models/u2netp.onnx";
    private static final String LOCAL_REMBG_MODEL_URL = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2netp.onnx";
    private static final String U2NETP_REMBG_MODEL = "u2netp";
    private static final String SILUETA_REMBG_MODEL = "silueta";
    private static final String ISNET_REMBG_MODEL = "isnet-general-use";
    private static final String U2NETP_REMBG_FILENAME = "u2netp.onnx";
    private static final String SILUETA_REMBG_FILENAME = "silueta.onnx";
    private static final String ISNET_REMBG_FILENAME = "isnet-general-use.onnx";
    private static final String SILUETA_REMBG_MODEL_URL = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/silueta.onnx";
    private static final String ISNET_REMBG_MODEL_URL = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/isnet-general-use.onnx";
    private static final long SILUETA_REMBG_EXPECTED_BYTES = 44173029L;
    private static final long ISNET_REMBG_EXPECTED_BYTES = 178648008L;
    private static final int FIRST_PORT = 7019;
    private static final int LAST_PORT = 7039;
    private static final int MASK_OPACITY_THRESHOLD = 12;
    private static final int MAX_IMAGE_TOOL_BODY_BYTES = 32 * 1024 * 1024;
    private static final int MAX_MOBILE_WORKSPACE_DIRS = 500;
    private static final int DEFAULT_MOBILE_WORKSPACE_FILE_ENTRIES = 250;
    private static final int MAX_MOBILE_WORKSPACE_FILE_ENTRIES = 500;
    private static final int MAX_MOBILE_WORKSPACE_TEXT_BYTES = 1024 * 1024;
    private static final int MAX_MOBILE_WORKSPACE_CONTEXT_ENTRIES = 60;
    private static final int MAX_MOBILE_WORKSPACE_CONTEXT_DEPTH = 3;
    private static final int MAX_MOBILE_WORKSPACE_CONTEXT_PREVIEW_FILES = 6;
    private static final int MAX_MOBILE_WORKSPACE_CONTEXT_PREVIEW_BYTES = 3072;
    private static final String MOBILE_PUBLIC_WORKSPACE_ACCESS_DETAIL =
            "Android cannot read this public folder yet. Grant Odysseus All files access in Android settings, or choose App Workspace.";
    private static final String MOBILE_DEPRECATED_PRIVATE_WORKSPACE_DETAIL =
            "The old private Android Documents/Downloads workspace folder is no longer used. Choose Documents, Downloads, App Workspace, or Scratch.";
    private static final String CHATGPT_SUBSCRIPTION_PROVIDER = "chatgpt-subscription";
    private static final String CHATGPT_SUBSCRIPTION_LABEL = "Codex Subscription";
    private static final String CHATGPT_SUBSCRIPTION_BASE_URL = "https://chatgpt.com/backend-api/codex";
    private static final String CHATGPT_OAUTH_ISSUER = "https://auth.openai.com";
    private static final String CHATGPT_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann";
    private static final String CHATGPT_OAUTH_REDIRECT_URI = CHATGPT_OAUTH_ISSUER + "/deviceauth/callback";
    private static final String CHATGPT_OAUTH_TOKEN_URL = CHATGPT_OAUTH_ISSUER + "/oauth/token";
    private static final int CHATGPT_ACCESS_TOKEN_REFRESH_SKEW_SECONDS = 120;

    private static MobileBackendServer instance;

    private Context appContext;
    private ServerSocket serverSocket;
    private Thread serverThread;
    private int port;
    private final Object rembgSessionLock = new Object();
    private DynamicOnnxRuntime rembgRuntime;
    private String lastBgRemoveSource = "";
    private String lastBgRemoveError = "";
    private final Object chatGptDeviceFlowLock = new Object();
    private final Map<String, JSONObject> chatGptDeviceFlows = new HashMap<>();

    public static synchronized MobileBackendServer getInstance() {
        if (instance == null) {
            instance = new MobileBackendServer();
        }
        return instance;
    }

    public synchronized String start(Context context) throws IOException {
        appContext = context.getApplicationContext();
        if (serverSocket != null && !serverSocket.isClosed()) {
            return baseUrl();
        }
        IOException last = null;
        for (int p = FIRST_PORT; p <= LAST_PORT; p++) {
            try {
                serverSocket = new ServerSocket(p, 16, InetAddress.getByName("127.0.0.1"));
                port = p;
                break;
            } catch (IOException ex) {
                last = ex;
            }
        }
        if (serverSocket == null) {
            throw last == null ? new IOException("No mobile backend port available") : last;
        }
        serverThread = new Thread(this::acceptLoop, "OdysseusMobileBackend");
        serverThread.setDaemon(true);
        serverThread.start();
        return baseUrl();
    }

    public synchronized void stop() {
        if (serverSocket != null) {
            try {
                serverSocket.close();
            } catch (IOException ignored) {
            }
            serverSocket = null;
        }
        if (serverThread != null) {
            serverThread.interrupt();
            serverThread = null;
        }
        synchronized (rembgSessionLock) {
            if (rembgRuntime != null) {
                rembgRuntime.close();
                rembgRuntime = null;
            }
        }
        port = 0;
    }

    private String baseUrl() {
        return "http://127.0.0.1:" + port;
    }

    private void acceptLoop() {
        while (serverSocket != null && !serverSocket.isClosed()) {
            try {
                Socket socket = serverSocket.accept();
                Thread client = new Thread(() -> handleSocket(socket), "OdysseusMobileRequest");
                client.setDaemon(true);
                client.start();
            } catch (IOException ignored) {
                break;
            }
        }
    }

    private void handleSocket(Socket socket) {
        try (Socket s = socket) {
            Request request = readRequest(s.getInputStream());
            if (request == null) return;
            route(request, s.getOutputStream());
        } catch (Exception ignored) {
            // Local mobile backend: keep the server alive if one request fails.
        }
    }

    private Request readRequest(InputStream in) throws IOException {
        ByteArrayOutputStream headerBytes = new ByteArrayOutputStream();
        int b;
        int state = 0;
        while ((b = in.read()) != -1) {
            headerBytes.write(b);
            if ((state == 0 || state == 2) && b == '\r') state++;
            else if ((state == 1 || state == 3) && b == '\n') state++;
            else state = 0;
            if (state == 4) break;
        }
        if (headerBytes.size() == 0) return null;
        String headersText = headerBytes.toString(StandardCharsets.ISO_8859_1.name());
        String[] lines = headersText.split("\\r?\\n");
        if (lines.length == 0) return null;
        String[] first = lines[0].split(" ", 3);
        if (first.length < 2) return null;
        Request request = new Request();
        request.method = first[0].trim().toUpperCase(Locale.US);
        request.rawPath = first[1].trim();
        int q = request.rawPath.indexOf('?');
        request.path = q >= 0 ? request.rawPath.substring(0, q) : request.rawPath;
        request.query = q >= 0 ? parseQuery(request.rawPath.substring(q + 1)) : new HashMap<>();
        for (int i = 1; i < lines.length; i++) {
            int idx = lines[i].indexOf(':');
            if (idx > 0) {
                request.headers.put(
                        lines[i].substring(0, idx).trim().toLowerCase(Locale.US),
                        lines[i].substring(idx + 1).trim()
                );
            }
        }
        int contentLength = parseInt(request.headers.get("content-length"), 0);
        if (contentLength > 0) {
            if (request.path.startsWith("/api/image/") && contentLength > MAX_IMAGE_TOOL_BODY_BYTES) {
                request.bodyTooLarge = true;
                request.body = new byte[0];
                return request;
            }
            request.body = readExact(in, contentLength);
        } else {
            request.body = new byte[0];
        }
        return request;
    }

    private byte[] readExact(InputStream in, int length) throws IOException {
        byte[] out = new byte[length];
        int offset = 0;
        while (offset < length) {
            int n = in.read(out, offset, length - offset);
            if (n == -1) break;
            offset += n;
        }
        return out;
    }

    private void route(Request request, OutputStream out) throws Exception {
        String path = request.path;
        if ("/".equals(path)) {
            sendRedirect(out, "/static/index.html");
            return;
        }
        if (path.startsWith("/static/")) {
            serveAsset(path.substring("/static/".length()), out);
            return;
        }
        if (path.startsWith("/api/")) {
            routeApi(request, out);
            return;
        }
        sendJson(out, 404, new JSONObject().put("detail", "Not found"));
    }

    private void routeApi(Request request, OutputStream out) throws Exception {
        String path = request.path;
        if ("GET".equals(request.method) && "/api/version".equals(path)) {
            sendJson(out, 200, new JSONObject()
                    .put("version", "mobile-standalone")
                    .put("mode", "android")
                    .put("standalone", true));
            return;
        }
        if ("GET".equals(request.method) && "/api/runtime".equals(path)) {
            sendJson(out, 200, new JSONObject()
                    .put("platform", "android")
                    .put("standalone", true)
                    .put("backend", "mobile"));
            return;
        }
        if ("GET".equals(request.method) && "/api/auth/status".equals(path)) {
            sendJson(out, 200, new JSONObject()
                    .put("authenticated", true)
                    .put("auth_enabled", false)
                    .put("user", "mobile")
                    .put("is_admin", true));
            return;
        }
        if ("GET".equals(request.method) && "/api/auth/features".equals(path)) {
            sendJson(out, 200, new JSONObject()
                    .put("auth_enabled", false)
                    .put("signup_enabled", false)
                    .put("single_user", true));
            return;
        }
        if ("GET".equals(request.method) && "/api/auth/settings".equals(path)) {
            sendJson(out, 200, mobileAuthSettings());
            return;
        }
        if ("POST".equals(request.method) && "/api/auth/settings".equals(path)) {
            sendJson(out, 200, saveMobileAuthSettings(requestJson(request)));
            return;
        }
        if (path.startsWith("/api/email")) {
            routeEmail(request, out, path.substring("/api/email".length()));
            return;
        }
        if ("GET".equals(request.method) && "/api/sessions".equals(path)) {
            sendJson(out, 200, listSessionSummaries());
            return;
        }
        if ("POST".equals(request.method) && "/api/sessions/bulk-delete".equals(path)) {
            sendJson(out, 200, bulkDeleteSessions(request));
            return;
        }
        if ("POST".equals(request.method) && "/api/session".equals(path)) {
            sendJson(out, 200, createSession(parseForm(request)));
            return;
        }
        if (path.startsWith("/api/session/")) {
            routeSession(request, out, path.substring("/api/session/".length()));
            return;
        }
        if ("GET".equals(request.method) && path.startsWith("/api/history/")) {
            sendJson(out, 200, sessionHistory(path.substring("/api/history/".length())));
            return;
        }
        if ("POST".equals(request.method) && "/api/document".equals(path)) {
            sendJson(out, 200, createDocument(parseForm(request)));
            return;
        }
        if ("GET".equals(request.method) && "/api/documents/library".equals(path)) {
            sendJson(out, 200, documentsLibrary(request));
            return;
        }
        if ("GET".equals(request.method) && path.startsWith("/api/documents/")) {
            sendJson(out, 200, listDocumentsForSession(path.substring("/api/documents/".length())));
            return;
        }
        if (path.startsWith("/api/document/")) {
            routeDocument(request, out, path.substring("/api/document/".length()));
            return;
        }
        if (path.equals("/api/notes") || path.startsWith("/api/notes/")) {
            routeNotes(request, out, path.substring("/api/notes".length()));
            return;
        }
        if ("GET".equals(request.method) && path.startsWith("/api/generated-image/")) {
            serveMobileGeneratedImage(path.substring("/api/generated-image/".length()), out);
            return;
        }
        if (path.equals("/api/image") || path.startsWith("/api/image/")) {
            routeImageTool(request, out, path.substring("/api/image".length()));
            return;
        }
        if (path.equals("/api/gallery") || path.startsWith("/api/gallery/")) {
            routeGallery(request, out, path.substring("/api/gallery".length()));
            return;
        }
        if ("GET".equals(request.method) && "/api/default-chat".equals(path)) {
            sendJson(out, 200, defaultChat());
            return;
        }
        if ("GET".equals(request.method) && "/api/models".equals(path)) {
            sendJson(out, 200, modelsList());
            return;
        }
        if ("GET".equals(request.method) && "/api/model-endpoints".equals(path)) {
            sendJson(out, 200, endpointList());
            return;
        }
        if ("POST".equals(request.method) && "/api/model-endpoints".equals(path)) {
            sendJson(out, 200, addEndpoint(parseForm(request)));
            return;
        }
        if ("POST".equals(request.method) && "/api/model-endpoints/test".equals(path)) {
            sendJson(out, 200, testEndpoint(parseForm(request)));
            return;
        }
        if ("POST".equals(request.method) && "/api/model-endpoints/unload-all".equals(path)) {
            sendJson(out, 200, unloadAllModels());
            return;
        }
        if ("POST".equals(request.method) && "/api/chatgpt-subscription/device/start".equals(path)) {
            sendJson(out, 200, startChatGptSubscriptionDeviceFlow());
            return;
        }
        if ("POST".equals(request.method) && "/api/chatgpt-subscription/device/poll".equals(path)) {
            sendJson(out, 200, pollChatGptSubscriptionDeviceFlow(parseForm(request)));
            return;
        }
        if ("POST".equals(request.method) && "/api/chatgpt-subscription/device/cancel".equals(path)) {
            sendJson(out, 200, cancelChatGptSubscriptionDeviceFlow(parseForm(request)));
            return;
        }
        if ("GET".equals(request.method) && "/api/model-endpoints/probe-local".equals(path)) {
            sendJson(out, 200, new JSONObject());
            return;
        }
        if (path.startsWith("/api/hwfit/")) {
            routeHwfit(request, out, path.substring("/api/hwfit/".length()));
            return;
        }
        if (path.equals("/api/model/cached") || path.equals("/api/model/download") || path.equals("/api/model/serve")) {
            routeMobileModel(request, out, path.substring("/api/model/".length()));
            return;
        }
        if (path.startsWith("/api/cookbook/")) {
            routeCookbook(request, out, path.substring("/api/cookbook/".length()));
            return;
        }
        if ("POST".equals(request.method) && "/api/probe-selected".equals(path)) {
            sendJson(out, 200, probeSelected(request));
            return;
        }
        if (path.startsWith("/api/model-endpoints/")) {
            routeEndpoint(request, out, path.substring("/api/model-endpoints/".length()));
            return;
        }
        if ("POST".equals(request.method) && "/api/compare/record".equals(path)) {
            sendJson(out, 200, new JSONObject().put("ok", true));
            return;
        }
        if ("POST".equals(request.method) && "/api/chat_stream".equals(path)) {
            streamChat(request, out);
            return;
        }
        if ("POST".equals(request.method) && path.startsWith("/api/chat/stop/")) {
            sendJson(out, 200, new JSONObject().put("ok", true));
            return;
        }
        if ("GET".equals(request.method) && "/api/tools".equals(path)) {
            sendJson(out, 200, new JSONObject().put("tools", new JSONArray()));
            return;
        }
        if ("GET".equals(request.method) && "/api/presets".equals(path)) {
            sendJson(out, 200, new JSONObject().put("presets", new JSONArray()));
            return;
        }
        if ("GET".equals(request.method) && "/api/presets/templates".equals(path)) {
            sendJson(out, 200, new JSONArray());
            return;
        }
        if ("GET".equals(request.method) && "/api/mcp/servers".equals(path)) {
            sendJson(out, 200, mobileMcpServers());
            return;
        }
        if ("GET".equals(request.method) && "/api/mcp/tools".equals(path)) {
            sendJson(out, 200, mobileMcpTools());
            return;
        }
        if ("GET".equals(request.method) && "/api/mcp/servers/android_rag/tools".equals(path)) {
            sendJson(out, 200, mobileMcpTools());
            return;
        }
        if (path.equals("/api/personal") || path.startsWith("/api/personal/")) {
            routePersonal(request, out, path.substring("/api/personal".length()));
            return;
        }
        if (path.equals("/api/workspace") || path.startsWith("/api/workspace/")) {
            routeWorkspace(request, out, path.substring("/api/workspace".length()));
            return;
        }
        if (path.equals("/api/search") || path.startsWith("/api/search/")) {
            routeSearch(request, out, path.substring("/api/search".length()));
            return;
        }
        if (path.equals("/api/calendar") || path.startsWith("/api/calendar/")) {
            routeCalendar(request, out, path.substring("/api/calendar".length()));
            return;
        }
        if (path.equals("/api/research") || path.startsWith("/api/research/")) {
            routeResearch(request, out, path.substring("/api/research".length()));
            return;
        }
        sendJson(out, 404, new JSONObject().put("detail", "Mobile standalone route not implemented"));
    }

    private void routeEmail(Request request, OutputStream out, String tail) throws Exception {
        String contentType = valueOr(request.headers.get("content-type"), "");
        Map<String, String> form = contentType.startsWith("multipart/form-data")
                ? new HashMap<>()
                : parseForm(request);
        MobileEmailBackend.Response response = new MobileEmailBackend(prefs(), appContext.getFilesDir())
                .route(request.method, tail, request.query, form, request.headers, request.body);
        sendJson(out, response.status, response.body);
    }

    private JSONObject mobileAuthSettings() throws Exception {
        JSONObject settings = loadMobileSettings();
        settings.put("auth_enabled", false)
                .put("signup_enabled", false)
                .put("single_user", true)
                .put("mobile_standalone", true);
        return settings;
    }

    private JSONObject saveMobileAuthSettings(JSONObject incoming) throws Exception {
        JSONObject settings = loadMobileSettings();
        java.util.Iterator<String> keys = incoming.keys();
        while (keys.hasNext()) {
            String key = keys.next();
            settings.put(key, incoming.isNull(key) ? JSONObject.NULL : incoming.get(key));
        }
        saveMobileSettings(settings);
        return mobileAuthSettings().put("ok", true);
    }

    private JSONObject loadMobileSettings() {
        JSONObject settings;
        try {
            settings = new JSONObject(prefs().getString(PREF_SETTINGS, "{}"));
        } catch (Exception ignored) {
            settings = new JSONObject();
        }
        try {
            if (!settings.has("search_provider")) settings.put("search_provider", "duckduckgo");
            if (!settings.has("search_result_count")) settings.put("search_result_count", 8);
            if (!settings.has("research_search_provider")) settings.put("research_search_provider", "");
            if (!settings.has("search_safesearch")) settings.put("search_safesearch", "moderate");
        } catch (Exception ignored) {
        }
        return settings;
    }

    private void saveMobileSettings(JSONObject settings) {
        prefs().edit().putString(PREF_SETTINGS, settings.toString()).apply();
    }

    private void routeSearch(Request request, OutputStream out, String tail) throws Exception {
        if (tail == null) tail = "";
        if (tail.startsWith("/")) tail = tail.substring(1);

        if ("GET".equals(request.method) && "config".equals(tail)) {
            JSONObject settings = loadMobileSettings();
            sendJson(out, 200, new JSONObject()
                    .put("primary_provider", mobileNormalizeSearchProvider(settings.optString("search_provider", "duckduckgo")))
                    .put("provider", mobileNormalizeSearchProvider(settings.optString("search_provider", "duckduckgo")))
                    .put("search_provider", mobileNormalizeSearchProvider(settings.optString("search_provider", "duckduckgo")))
                    .put("search_url", settings.optString("search_url", ""))
                    .put("search_result_count", settings.optInt("search_result_count", 8))
                    .put("research_search_provider", mobileNormalizeSearchProvider(settings.optString("research_search_provider", "")))
                    .put("mobile_standalone", true));
            return;
        }
        if ("GET".equals(request.method) && "providers".equals(tail)) {
            sendJson(out, 200, mobileSearchProviders());
            return;
        }
        if ("POST".equals(request.method) && ("query".equals(tail) || tail.isEmpty())) {
            Map<String, String> form = parseForm(request);
            JSONObject body = requestJson(request);
            String query = valueOr(form.get("query"), jsonString(body, "query", jsonString(body, "q", ""))).trim();
            String provider = valueOr(form.get("provider"), jsonString(body, "provider", "")).trim();
            int count = parseInt(valueOr(form.get("count"), jsonString(body, "count", jsonString(body, "limit", "8"))), 8);
            JSONArray results = mobileSearchWithProvider(query, provider, Math.max(1, Math.min(20, count)));
            if ("query".equals(tail)) {
                sendJson(out, 200, new JSONObject()
                        .put("results", results)
                        .put("provider", mobileEffectiveSearchProvider(provider))
                        .put("mobile_standalone", true));
            } else {
                sendJson(out, 200, new JSONObject()
                        .put("context", mobileSearchContext(results))
                        .put("sources", results)
                        .put("mobile_standalone", true));
            }
            return;
        }
        sendJson(out, 404, new JSONObject().put("detail", "Mobile search route not implemented"));
    }

    private JSONArray mobileSearchProviders() throws Exception {
        JSONObject settings = loadMobileSettings();
        JSONArray out = new JSONArray();
        String[][] providers = {
                {"searxng", "SearXNG", "url"},
                {"duckduckgo", "DuckDuckGo", ""},
                {"brave", "Brave Search", "key"},
                {"google_pse", "Google PSE", "key"},
                {"tavily", "Tavily", "key"},
                {"serper", "Serper", "key"}
        };
        for (String[] p : providers) {
            boolean available = true;
            if ("url".equals(p[2])) available = !settings.optString("search_url", "").trim().isEmpty();
            if ("key".equals(p[2])) available = !mobileSearchApiKey(settings, p[0]).isEmpty();
            out.put(new JSONObject()
                    .put("id", p[0])
                    .put("label", p[1])
                    .put("available", available));
        }
        return out;
    }

    private JSONArray mobileSearchWithProvider(String query, String provider, int count) throws Exception {
        JSONArray empty = new JSONArray();
        if (valueOr(query, "").trim().isEmpty()) return empty;
        String primary = mobileEffectiveSearchProvider(provider);
        if ("disabled".equals(primary)) return empty;
        List<String> chain = mobileSearchProviderChain(primary);
        for (String candidate : chain) {
            JSONArray results = mobileSearchProvider(candidate, query, count);
            if (results.length() > 0) {
                return mobileEnrichSearchResults(results, Math.min(count, 8));
            }
        }
        return empty;
    }

    private List<String> mobileSearchProviderChain(String primary) {
        List<String> chain = new ArrayList<>();
        String normalized = mobileNormalizeSearchProvider(primary);
        if (!normalized.isEmpty() && !"disabled".equals(normalized)) chain.add(normalized);
        if (!chain.contains("duckduckgo")) chain.add("duckduckgo");
        return chain;
    }

    private String mobileEffectiveSearchProvider(String provider) {
        String normalized = mobileNormalizeSearchProvider(provider);
        JSONObject settings = loadMobileSettings();
        if (normalized.isEmpty()) normalized = mobileNormalizeSearchProvider(settings.optString("research_search_provider", ""));
        if (normalized.isEmpty()) normalized = mobileNormalizeSearchProvider(settings.optString("search_provider", "duckduckgo"));
        return normalized.isEmpty() ? "duckduckgo" : normalized;
    }

    private String mobileNormalizeSearchProvider(String provider) {
        String p = valueOr(provider, "").trim().toLowerCase(Locale.US);
        if ("google".equals(p) || "google-pse".equals(p) || "googlepse".equals(p)) return "google_pse";
        if ("default".equals(p) || "auto".equals(p)) return "";
        return p;
    }

    private JSONArray mobileSearchProvider(String provider, String query, int count) throws Exception {
        switch (mobileNormalizeSearchProvider(provider)) {
            case "searxng":
                return mobileSearxngSearch(query, count);
            case "brave":
                return mobileBraveSearch(query, count);
            case "google_pse":
                return mobileGooglePseSearch(query, count);
            case "tavily":
                return mobileTavilySearch(query, count);
            case "serper":
                return mobileSerperSearch(query, count);
            case "duckduckgo":
            default:
                return mobileDuckDuckGoSearch(query, count);
        }
    }

    private JSONArray mobileSearxngSearch(String query, int count) throws Exception {
        JSONObject settings = loadMobileSettings();
        String base = settings.optString("search_url", "").trim();
        if (base.isEmpty()) return new JSONArray();
        while (base.endsWith("/")) base = base.substring(0, base.length() - 1);
        String url = base + "/search?q=" + urlEncode(query)
                + "&format=json&language=en&categories=general&safesearch=1";
        JSONObject data = httpGetJson(url, null);
        JSONArray items = data.optJSONArray("results");
        JSONArray out = new JSONArray();
        if (items == null) return out;
        for (int i = 0; i < items.length() && out.length() < count; i++) {
            JSONObject item = items.optJSONObject(i);
            if (item == null) continue;
            String itemUrl = item.optString("url", "");
            if (itemUrl.isEmpty()) continue;
            out.put(mobileSearchResult(
                    item.optString("title", itemUrl),
                    itemUrl,
                    item.optString("content", ""),
                    "searxng",
                    item.optString("img_src", "")));
        }
        return out;
    }

    private JSONArray mobileBraveSearch(String query, int count) throws Exception {
        JSONObject settings = loadMobileSettings();
        String apiKey = mobileSearchApiKey(settings, "brave");
        if (apiKey.isEmpty()) return new JSONArray();
        Map<String, String> headers = new HashMap<>();
        headers.put("X-Subscription-Token", apiKey);
        headers.put("Accept", "application/json");
        String url = "https://api.search.brave.com/res/v1/web/search?q=" + urlEncode(query)
                + "&count=" + Math.min(20, count)
                + "&safesearch=moderate";
        JSONObject data = httpGetJson(url, headers);
        JSONObject web = data.optJSONObject("web");
        JSONArray items = web == null ? null : web.optJSONArray("results");
        JSONArray out = new JSONArray();
        if (items == null) return out;
        for (int i = 0; i < items.length() && out.length() < count; i++) {
            JSONObject item = items.optJSONObject(i);
            if (item == null) continue;
            String itemUrl = item.optString("url", "");
            if (itemUrl.isEmpty()) continue;
            JSONObject thumb = item.optJSONObject("thumbnail");
            out.put(mobileSearchResult(
                    item.optString("title", itemUrl),
                    itemUrl,
                    item.optString("description", item.optString("content", "")),
                    "brave",
                    thumb == null ? "" : thumb.optString("src", "")));
        }
        return out;
    }

    private JSONArray mobileGooglePseSearch(String query, int count) throws Exception {
        JSONObject settings = loadMobileSettings();
        String apiKey = mobileSearchApiKey(settings, "google_pse");
        String cx = settings.optString("google_pse_cx", "").trim();
        if (apiKey.isEmpty() || cx.isEmpty()) return new JSONArray();
        Map<String, String> headers = new HashMap<>();
        headers.put("X-Goog-Api-Key", apiKey);
        String url = "https://www.googleapis.com/customsearch/v1?cx=" + urlEncode(cx)
                + "&q=" + urlEncode(query)
                + "&num=" + Math.min(10, count)
                + "&safe=active";
        JSONObject data = httpGetJson(url, headers);
        JSONArray items = data.optJSONArray("items");
        JSONArray out = new JSONArray();
        if (items == null) return out;
        for (int i = 0; i < items.length() && out.length() < count; i++) {
            JSONObject item = items.optJSONObject(i);
            if (item == null) continue;
            String itemUrl = item.optString("link", "");
            if (itemUrl.isEmpty()) continue;
            out.put(mobileSearchResult(
                    item.optString("title", itemUrl),
                    itemUrl,
                    item.optString("snippet", ""),
                    "google_pse",
                    mobileGooglePseImage(item)));
        }
        return out;
    }

    private String mobileGooglePseImage(JSONObject item) {
        JSONObject pagemap = item.optJSONObject("pagemap");
        if (pagemap == null) return "";
        JSONArray thumbs = pagemap.optJSONArray("cse_thumbnail");
        if (thumbs != null && thumbs.length() > 0) {
            JSONObject thumb = thumbs.optJSONObject(0);
            if (thumb != null && !thumb.optString("src", "").isEmpty()) return thumb.optString("src", "");
        }
        JSONArray images = pagemap.optJSONArray("cse_image");
        if (images != null && images.length() > 0) {
            JSONObject image = images.optJSONObject(0);
            if (image != null) return image.optString("src", "");
        }
        return "";
    }

    private JSONArray mobileTavilySearch(String query, int count) throws Exception {
        JSONObject settings = loadMobileSettings();
        String apiKey = mobileSearchApiKey(settings, "tavily");
        if (apiKey.isEmpty()) return new JSONArray();
        Map<String, String> headers = new HashMap<>();
        headers.put("Authorization", "Bearer " + apiKey);
        headers.put("Content-Type", "application/json");
        JSONObject payload = new JSONObject()
                .put("query", query)
                .put("max_results", Math.min(20, count))
                .put("include_answer", false);
        JSONObject data = httpPostJson("https://api.tavily.com/search", headers, payload);
        JSONArray items = data.optJSONArray("results");
        JSONArray out = new JSONArray();
        if (items == null) return out;
        for (int i = 0; i < items.length() && out.length() < count; i++) {
            JSONObject item = items.optJSONObject(i);
            if (item == null) continue;
            String itemUrl = item.optString("url", "");
            if (itemUrl.isEmpty()) continue;
            out.put(mobileSearchResult(
                    item.optString("title", itemUrl),
                    itemUrl,
                    item.optString("content", ""),
                    "tavily",
                    ""));
        }
        return out;
    }

    private JSONArray mobileSerperSearch(String query, int count) throws Exception {
        JSONObject settings = loadMobileSettings();
        String apiKey = mobileSearchApiKey(settings, "serper");
        if (apiKey.isEmpty()) return new JSONArray();
        Map<String, String> headers = new HashMap<>();
        headers.put("X-API-KEY", apiKey);
        headers.put("Content-Type", "application/json");
        JSONObject payload = new JSONObject()
                .put("q", query)
                .put("num", Math.min(20, count))
                .put("safe", "active");
        JSONObject data = httpPostJson("https://google.serper.dev/search", headers, payload);
        JSONArray items = data.optJSONArray("organic");
        JSONArray out = new JSONArray();
        if (items == null) return out;
        for (int i = 0; i < items.length() && out.length() < count; i++) {
            JSONObject item = items.optJSONObject(i);
            if (item == null) continue;
            String itemUrl = item.optString("link", "");
            if (itemUrl.isEmpty()) continue;
            out.put(mobileSearchResult(
                    item.optString("title", itemUrl),
                    itemUrl,
                    item.optString("snippet", ""),
                    "serper",
                    item.optString("imageUrl", "")));
        }
        return out;
    }

    private JSONArray mobileDuckDuckGoSearch(String query, int count) throws Exception {
        String url = "https://duckduckgo.com/html/?q=" + urlEncode(query);
        String html = httpGetText(url, null, 180000);
        JSONArray out = new JSONArray();
        java.util.regex.Matcher matcher = java.util.regex.Pattern
                .compile("(?is)<a[^>]*class=\"[^\"]*result__a[^\"]*\"[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>")
                .matcher(html);
        while (matcher.find() && out.length() < count) {
            String resultUrl = mobileDecodeDuckDuckGoUrl(htmlUnescape(matcher.group(1)));
            if (!resultUrl.startsWith("http://") && !resultUrl.startsWith("https://")) continue;
            String title = stripHtml(matcher.group(2)).trim();
            if (title.isEmpty()) title = resultUrl;
            out.put(mobileSearchResult(title, resultUrl, "", "duckduckgo", ""));
        }
        return out;
    }

    private JSONArray mobileEnrichSearchResults(JSONArray results, int maxPages) throws Exception {
        for (int i = 0; i < results.length() && i < maxPages; i++) {
            JSONObject item = results.optJSONObject(i);
            if (item == null) continue;
            if (!item.optString("image", "").isEmpty() && !item.optString("summary", "").isEmpty()) continue;
            try {
                JSONObject meta = mobileFetchPageMeta(item.optString("url", ""));
                if (item.optString("image", "").isEmpty() && !meta.optString("image", "").isEmpty()) {
                    item.put("image", meta.optString("image"));
                }
                if (item.optString("summary", "").isEmpty() && !meta.optString("description", "").isEmpty()) {
                    item.put("summary", meta.optString("description"));
                    item.put("snippet", meta.optString("description"));
                }
            } catch (Exception ignored) {
            }
        }
        return results;
    }

    private JSONObject mobileFetchPageMeta(String rawUrl) throws Exception {
        String url = valueOr(rawUrl, "").trim();
        if (!url.startsWith("http://") && !url.startsWith("https://")) return new JSONObject();
        String html = httpGetText(url, null, 140000);
        String image = firstNonEmpty(
                extractMetaContent(html, "property", "og:image"),
                extractMetaContent(html, "name", "twitter:image"),
                extractMetaContent(html, "property", "twitter:image"));
        String description = firstNonEmpty(
                extractMetaContent(html, "name", "description"),
                extractMetaContent(html, "property", "og:description"),
                extractMetaContent(html, "name", "twitter:description"));
        if (!image.isEmpty()) {
            try {
                image = new URL(new URL(url), image).toString();
            } catch (Exception ignored) {
            }
        }
        return new JSONObject()
                .put("image", image)
                .put("description", description);
    }

    private String extractMetaContent(String html, String attrName, String attrValue) {
        String pattern = "(?is)<meta\\s+[^>]*" + attrName + "\\s*=\\s*['\"]" + java.util.regex.Pattern.quote(attrValue) + "['\"][^>]*>";
        java.util.regex.Matcher matcher = java.util.regex.Pattern.compile(pattern).matcher(valueOr(html, ""));
        if (!matcher.find()) return "";
        String tag = matcher.group(0);
        java.util.regex.Matcher content = java.util.regex.Pattern
                .compile("(?is)content\\s*=\\s*['\"]([^'\"]+)['\"]")
                .matcher(tag);
        return content.find() ? htmlUnescape(content.group(1)).trim() : "";
    }

    private JSONObject mobileSearchResult(String title, String url, String snippet, String provider, String image) throws Exception {
        String cleanSnippet = stripHtml(valueOr(snippet, "")).trim();
        return new JSONObject()
                .put("title", stripHtml(valueOr(title, "")).trim())
                .put("url", valueOr(url, "").trim())
                .put("snippet", cleanSnippet)
                .put("summary", cleanSnippet)
                .put("provider", provider)
                .put("source_type", "web")
                .put("image", valueOr(image, "").trim());
    }

    private String mobileSearchContext(JSONArray results) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < results.length(); i++) {
            JSONObject item = results.optJSONObject(i);
            if (item == null) continue;
            sb.append('[').append(i + 1).append("] ")
                    .append(item.optString("title", "Source")).append('\n')
                    .append("URL: ").append(item.optString("url", "")).append('\n');
            String summary = item.optString("summary", item.optString("snippet", ""));
            if (!summary.isEmpty()) sb.append("Summary: ").append(summary).append('\n');
            sb.append('\n');
        }
        return sb.toString().trim();
    }

    private String mobileSearchApiKey(JSONObject settings, String provider) {
        String field = "";
        if ("brave".equals(provider)) field = "brave_api_key";
        else if ("google_pse".equals(provider)) field = "google_pse_key";
        else if ("tavily".equals(provider)) field = "tavily_api_key";
        else if ("serper".equals(provider)) field = "serper_api_key";
        String value = field.isEmpty() ? "" : settings.optString(field, "").trim();
        if (value.isEmpty()) value = settings.optString("search_api_key", "").trim();
        return value;
    }

    private JSONObject httpGetJson(String url, Map<String, String> headers) throws Exception {
        return new JSONObject(httpGetText(url, headers, 400000));
    }

    private JSONObject httpPostJson(String url, Map<String, String> headers, JSONObject payload) throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
        conn.setRequestMethod("POST");
        conn.setConnectTimeout(12000);
        conn.setReadTimeout(20000);
        conn.setDoOutput(true);
        conn.setRequestProperty("Accept", "application/json");
        conn.setRequestProperty("User-Agent", "OdysseusAndroid/1.0");
        if (headers != null) {
            for (Map.Entry<String, String> entry : headers.entrySet()) {
                if (!valueOr(entry.getValue(), "").isEmpty()) conn.setRequestProperty(entry.getKey(), entry.getValue());
            }
        }
        byte[] bytes = payload.toString().getBytes(StandardCharsets.UTF_8);
        conn.setFixedLengthStreamingMode(bytes.length);
        try (OutputStream body = conn.getOutputStream()) {
            body.write(bytes);
        }
        int status = conn.getResponseCode();
        String response = readAll(status >= 400 ? conn.getErrorStream() : conn.getInputStream());
        if (status < 200 || status >= 300) throw new IOException("HTTP " + status + ": " + truncateError(response, 180));
        return new JSONObject(response);
    }

    private String httpGetText(String url, Map<String, String> headers, int limit) throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
        conn.setRequestMethod("GET");
        conn.setInstanceFollowRedirects(true);
        conn.setConnectTimeout(12000);
        conn.setReadTimeout(18000);
        conn.setRequestProperty("User-Agent", "Mozilla/5.0 (Linux; Android) AppleWebKit/537.36 OdysseusAndroid/1.0");
        conn.setRequestProperty("Accept", "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8");
        if (headers != null) {
            for (Map.Entry<String, String> entry : headers.entrySet()) {
                if (!valueOr(entry.getValue(), "").isEmpty()) conn.setRequestProperty(entry.getKey(), entry.getValue());
            }
        }
        int status = conn.getResponseCode();
        String response = new String(readLimited(status >= 400 ? conn.getErrorStream() : conn.getInputStream(), limit), StandardCharsets.UTF_8);
        if (status < 200 || status >= 300) throw new IOException("HTTP " + status + ": " + truncateError(response, 180));
        return response;
    }

    private byte[] readLimited(InputStream in, int maxBytes) throws IOException {
        if (in == null) return new byte[0];
        ByteArrayOutputStream buffer = new ByteArrayOutputStream(Math.min(maxBytes, 8192));
        byte[] chunk = new byte[4096];
        int total = 0;
        int n;
        while (total < maxBytes && (n = in.read(chunk, 0, Math.min(chunk.length, maxBytes - total))) != -1) {
            buffer.write(chunk, 0, n);
            total += n;
        }
        return buffer.toByteArray();
    }

    private String urlEncode(String raw) {
        try {
            return URLEncoder.encode(valueOr(raw, ""), "UTF-8");
        } catch (Exception ignored) {
            return "";
        }
    }

    private String mobileDecodeDuckDuckGoUrl(String raw) {
        String url = valueOr(raw, "");
        if (url.startsWith("//")) url = "https:" + url;
        if (url.startsWith("/")) url = "https://duckduckgo.com" + url;
        int idx = url.indexOf("uddg=");
        if (idx >= 0) {
            String encoded = url.substring(idx + 5);
            int amp = encoded.indexOf('&');
            if (amp >= 0) encoded = encoded.substring(0, amp);
            try {
                return URLDecoder.decode(encoded, "UTF-8");
            } catch (Exception ignored) {
            }
        }
        return url;
    }

    private String stripHtml(String raw) {
        return htmlUnescape(valueOr(raw, "")
                .replaceAll("(?is)<script[^>]*>.*?</script>", " ")
                .replaceAll("(?is)<style[^>]*>.*?</style>", " ")
                .replaceAll("(?is)<[^>]+>", " ")
                .replaceAll("\\s+", " "));
    }

    private String htmlUnescape(String raw) {
        String text = valueOr(raw, "");
        text = text.replace("&amp;", "&")
                .replace("&lt;", "<")
                .replace("&gt;", ">")
                .replace("&quot;", "\"")
                .replace("&#39;", "'")
                .replace("&apos;", "'");
        java.util.regex.Matcher dec = java.util.regex.Pattern.compile("&#(\\d+);").matcher(text);
        StringBuffer out = new StringBuffer();
        while (dec.find()) {
            try {
                dec.appendReplacement(out, java.util.regex.Matcher.quoteReplacement(String.valueOf((char) Integer.parseInt(dec.group(1)))));
            } catch (Exception ignored) {
            }
        }
        dec.appendTail(out);
        text = out.toString();
        java.util.regex.Matcher hex = java.util.regex.Pattern.compile("&#x([0-9a-fA-F]+);").matcher(text);
        out = new StringBuffer();
        while (hex.find()) {
            try {
                hex.appendReplacement(out, java.util.regex.Matcher.quoteReplacement(String.valueOf((char) Integer.parseInt(hex.group(1), 16))));
            } catch (Exception ignored) {
            }
        }
        hex.appendTail(out);
        return out.toString();
    }

    private String firstNonEmpty(String... values) {
        for (String value : values) {
            if (!valueOr(value, "").trim().isEmpty()) return value.trim();
        }
        return "";
    }

    private void routeHwfit(Request request, OutputStream out, String tail) throws Exception {
        if ("GET".equals(request.method) && "system".equals(tail)) {
            sendJson(out, 200, mobileHwfitSystem(request));
            return;
        }
        if ("GET".equals(request.method) && "models".equals(tail)) {
            sendJson(out, 200, mobileHwfitModels(request));
            return;
        }
        if ("GET".equals(request.method) && "image-models".equals(tail)) {
            sendJson(out, 200, new JSONObject()
                    .put("system", mobileHwfitSystem(request))
                    .put("models", new JSONArray()));
            return;
        }
        if ("GET".equals(request.method) && "profiles".equals(tail)) {
            sendJson(out, 200, new JSONObject()
                    .put("profiles", new JSONArray())
                    .put("model_ctx_max", 32768)
                    .put("mobile_standalone", true));
            return;
        }
        sendJson(out, 404, new JSONObject().put("detail", "Mobile hardware-fit route not implemented"));
    }

    private void routeMobileModel(Request request, OutputStream out, String tail) throws Exception {
        if ("GET".equals(request.method) && "cached".equals(tail)) {
            sendJson(out, 200, new JSONObject()
                    .put("models", new JSONArray())
                    .put("host", "android")
                    .put("mobile_standalone", true));
            return;
        }
        if ("POST".equals(request.method) && ("download".equals(tail) || "serve".equals(tail))) {
            String action = "download".equals(tail) ? "download" : "serve";
            sendJson(out, 200, new JSONObject()
                    .put("ok", false)
                    .put("mobile_standalone", true)
                    .put("error", "Cookbook can browse models on Android, but model " + action + " needs the PC backend. Use Connect to PC for Cookbook downloads and serves."));
            return;
        }
        sendJson(out, 404, new JSONObject().put("detail", "Mobile model route not implemented"));
    }

    private void routeCookbook(Request request, OutputStream out, String tail) throws Exception {
        if ("GET".equals(request.method) && "state".equals(tail)) {
            sendJson(out, 200, loadCookbookState());
            return;
        }
        if ("POST".equals(request.method) && "state".equals(tail)) {
            JSONObject incoming = requestJson(request);
            JSONObject state = incoming.length() == 0 ? defaultCookbookState() : incoming;
            if (!state.has("env")) state.put("env", defaultCookbookState().getJSONObject("env"));
            if (!state.has("tasks")) state.put("tasks", new JSONArray());
            if (!state.has("presets")) state.put("presets", new JSONArray());
            prefs().edit().putString(PREF_COOKBOOK_STATE, state.toString()).apply();
            sendJson(out, 200, new JSONObject().put("ok", true).put("mobile_standalone", true));
            return;
        }
        if ("GET".equals(request.method) && "tasks/status".equals(tail)) {
            sendJson(out, 200, mobileCookbookTaskStatus());
            return;
        }
        if ("GET".equals(request.method) && "hf-latest".equals(tail)) {
            sendJson(out, 200, mobileHfLatest(request));
            return;
        }
        if ("GET".equals(request.method) && "ollama/library".equals(tail)) {
            sendJson(out, 200, mobileOllamaLibrary());
            return;
        }
        if ("GET".equals(request.method) && "packages".equals(tail)) {
            sendJson(out, 200, new JSONObject()
                    .put("packages", mobileRembgModelPackages())
                    .put("mobile_standalone", true)
                    .put("note", "Android standalone dependencies are limited to local phone assets. PC/server dependencies still run on the PC backend."));
            return;
        }
        if ("POST".equals(request.method) && "rembg-models/install".equals(tail)) {
            sendJson(out, 200, installMobileRembgModel(requestJson(request)));
            return;
        }
        if ("POST".equals(request.method) && "android-runtime/install".equals(tail)) {
            sendJson(out, 200, installMobileOnnxRuntime());
            return;
        }
        if ("GET".equals(request.method) && "gpus".equals(tail)) {
            sendJson(out, 200, new JSONObject()
                    .put("gpus", new JSONArray())
                    .put("mobile_standalone", true));
            return;
        }
        if ("GET".equals(request.method) && "ssh-key".equals(tail)) {
            sendJson(out, 200, new JSONObject()
                    .put("ok", false)
                    .put("public_key", "")
                    .put("mobile_standalone", true));
            return;
        }
        if ("POST".equals(request.method) && ("setup".equals(tail) || "rebuild-engine".equals(tail) || "kill-pid".equals(tail) || "ssh-key".equals(tail))) {
            sendJson(out, 200, new JSONObject()
                    .put("ok", false)
                    .put("mobile_standalone", true)
                    .put("error", "This Cookbook action requires the PC backend."));
            return;
        }
        sendJson(out, 404, new JSONObject().put("detail", "Mobile cookbook route not implemented"));
    }

    private JSONArray mobileRembgModelPackages() throws Exception {
        JSONArray packages = new JSONArray();
        packages.put(mobileOnnxRuntimePackage());
        packages.put(mobileRembgModelPackage(
                "rembg-silueta",
                SILUETA_REMBG_MODEL,
                SILUETA_REMBG_FILENAME,
                SILUETA_REMBG_MODEL_URL,
                SILUETA_REMBG_EXPECTED_BYTES,
                "Higher-quality rembg background-removal model. Better than u2netp, smaller than ISNet."
        ));
        packages.put(mobileRembgModelPackage(
                "rembg-isnet-general-use",
                ISNET_REMBG_MODEL,
                ISNET_REMBG_FILENAME,
                ISNET_REMBG_MODEL_URL,
                ISNET_REMBG_EXPECTED_BYTES,
                "High-quality general rembg background-removal model. Best quality, larger download."
        ));
        return packages;
    }

    private JSONObject mobileOnnxRuntimePackage() throws Exception {
        boolean installed = DynamicOnnxRuntime.isInstalled(appContext);
        return new JSONObject()
                .put("name", "onnxruntime-android")
                .put("pip", "")
                .put("desc", "Installable ONNX Runtime engine for local Android background removal.")
                .put("category", "Image")
                .put("target", "local")
                .put("kind", "file")
                .put("model", "onnxruntime-android")
                .put("url", DynamicOnnxRuntime.AAR_URL)
                .put("install_endpoint", "/api/cookbook/android-runtime/install")
                .put("installed", installed)
                .put("status_note", DynamicOnnxRuntime.statusNote(appContext))
                .put("mobile_standalone", true);
    }

    private JSONObject installMobileOnnxRuntime() throws Exception {
        JSONObject result = DynamicOnnxRuntime.install(appContext);
        synchronized (rembgSessionLock) {
            if (rembgRuntime != null) {
                rembgRuntime.close();
                rembgRuntime = null;
            }
        }
        return result;
    }

    private JSONObject mobileRembgModelPackage(String name, String model, String filename, String url,
                                               long expectedBytes, String desc) throws Exception {
        File file = new File(new File(appContext.getFilesDir(), "models"), filename);
        boolean installed = file.exists() && file.isFile() && file.length() >= Math.max(1L, expectedBytes / 2L);
        String status = installed
                ? file.getAbsolutePath() + " (" + String.format(Locale.US, "%.1f MB", file.length() / 1048576.0) + ")"
                : "Downloads " + String.format(Locale.US, "%.1f MB", expectedBytes / 1048576.0) + " to the phone cache.";
        return new JSONObject()
                .put("name", name)
                .put("pip", "")
                .put("desc", desc)
                .put("category", "Image")
                .put("target", "local")
                .put("kind", "file")
                .put("model", model)
                .put("url", url)
                .put("installed", installed)
                .put("status_note", status)
                .put("mobile_standalone", true);
    }

    private JSONObject installMobileRembgModel(JSONObject body) throws Exception {
        String model = jsonString(body, "model", "").trim();
        String filename;
        String url;
        if (SILUETA_REMBG_MODEL.equals(model)) {
            filename = SILUETA_REMBG_FILENAME;
            url = SILUETA_REMBG_MODEL_URL;
        } else if (ISNET_REMBG_MODEL.equals(model)) {
            filename = ISNET_REMBG_FILENAME;
            url = ISNET_REMBG_MODEL_URL;
        } else {
            return new JSONObject().put("ok", false).put("error", "Unknown rembg model: " + model);
        }
        File dir = new File(appContext.getFilesDir(), "models");
        if (!dir.exists() && !dir.mkdirs()) {
            return new JSONObject().put("ok", false).put("error", "Could not create model cache directory.");
        }
        File out = new File(dir, filename);
        boolean ok = downloadModelFile(url, out);
        return new JSONObject()
                .put("ok", ok)
                .put("model", model)
                .put("path", out.getAbsolutePath())
                .put("bytes", ok && out.exists() ? out.length() : 0)
                .put("error", ok ? "" : "Model download failed.");
    }

    private void routeSession(Request request, OutputStream out, String tail) throws Exception {
        String sid = tail.split("/", 2)[0];
        if ("PATCH".equals(request.method)) {
            sendJson(out, 200, patchSession(sid, parseForm(request)));
            return;
        }
        if ("DELETE".equals(request.method)) {
            deleteSession(sid);
            sendJson(out, 200, new JSONObject().put("deleted", true));
            return;
        }
        if ("POST".equals(request.method) && tail.endsWith("/archive")) {
            sendJson(out, 200, new JSONObject().put("archived", false));
            return;
        }
        if ("POST".equals(request.method) && tail.endsWith("/important")) {
            sendJson(out, 200, new JSONObject().put("ok", true));
            return;
        }
        if ("POST".equals(request.method) && tail.endsWith("/mark-stopped")) {
            sendJson(out, 200, new JSONObject().put("ok", true));
            return;
        }
        sendJson(out, 200, getSessionById(sid));
    }

    private void routeDocument(Request request, OutputStream out, String tail) throws Exception {
        String[] parts = tail.split("/", 2);
        String id = parts[0];
        String action = parts.length > 1 ? parts[1] : "";
        if ("archive".equals(action) && "POST".equals(request.method)) {
            JSONObject doc = updateDocumentFields(id, new HashMap<>(), true, false);
            if (doc == null) {
                sendJson(out, 404, new JSONObject().put("detail", "Document not found"));
                return;
            }
            sendJson(out, 200, new JSONObject().put("ok", true).put("id", id).put("archived", doc.optBoolean("archived", false)));
            return;
        }
        if ("GET".equals(request.method) && action.isEmpty()) {
            JSONObject doc = findDocument(id);
            if (doc == null) {
                sendJson(out, 404, new JSONObject().put("detail", "Document not found"));
                return;
            }
            sendJson(out, 200, doc);
            return;
        }
        if ("PUT".equals(request.method) && action.isEmpty()) {
            JSONObject doc = updateDocumentFields(id, parseForm(request), false, false);
            sendJson(out, doc == null ? 404 : 200, doc == null ? new JSONObject().put("detail", "Document not found") : doc);
            return;
        }
        if ("PATCH".equals(request.method) && action.isEmpty()) {
            JSONObject doc = updateDocumentFields(id, parseForm(request), false, false);
            sendJson(out, doc == null ? 404 : 200, doc == null ? new JSONObject().put("detail", "Document not found") : doc);
            return;
        }
        if ("DELETE".equals(request.method) && action.isEmpty()) {
            JSONObject doc = updateDocumentFields(id, new HashMap<>(), false, true);
            if (doc == null) {
                sendJson(out, 404, new JSONObject().put("detail", "Document not found"));
                return;
            }
            sendJson(out, 200, new JSONObject().put("status", "deleted").put("id", id));
            return;
        }
        sendJson(out, 404, new JSONObject().put("detail", "Mobile document route not implemented"));
    }

    private void routePersonal(Request request, OutputStream out, String tail) throws Exception {
        if (tail == null) tail = "";
        if (tail.startsWith("/")) tail = tail.substring(1);

        if (tail.isEmpty() && "GET".equals(request.method)) {
            sendJson(out, 200, mobilePersonalList());
            return;
        }
        if ("reload".equals(tail) && "POST".equals(request.method)) {
            sendJson(out, 200, new JSONObject()
                    .put("ok", true)
                    .put("count", loadArray(PREF_PERSONAL_DIRECTORIES).length())
                    .put("mobile_standalone", true));
            return;
        }
        if ("add_directory".equals(tail) && "POST".equals(request.method)) {
            sendJson(out, 200, mobileAddPersonalDirectory(request));
            return;
        }
        if ("remove_directory".equals(tail) && "DELETE".equals(request.method)) {
            sendJson(out, 200, mobileRemovePersonalDirectory(valueOr(request.query.get("directory"), "")));
            return;
        }
        sendJson(out, 404, new JSONObject().put("detail", "Mobile personal route not implemented"));
    }

    private void routeNotes(Request request, OutputStream out, String tail) throws Exception {
        if (tail == null) tail = "";
        if (tail.startsWith("/")) tail = tail.substring(1);

        if (tail.isEmpty()) {
            if ("GET".equals(request.method)) {
                sendJson(out, 200, listNotes(request));
                return;
            }
            if ("POST".equals(request.method)) {
                sendJson(out, 200, createNote(requestJson(request)));
                return;
            }
        }

        if ("POST".equals(request.method) && "reorder".equals(tail)) {
            sendJson(out, 200, reorderNotes(requestJson(request)));
            return;
        }
        if ("POST".equals(request.method) && "fire-reminder".equals(tail)) {
            sendJson(out, 200, new JSONObject()
                    .put("synthesis", JSONObject.NULL)
                    .put("email_sent", false)
                    .put("ntfy_sent", false)
                    .put("webhook_sent", false)
                    .put("browser_sent", true));
            return;
        }

        String[] parts = tail.split("/");
        String id = parts.length > 0 ? parts[0] : "";
        String action = parts.length > 1 ? parts[1] : "";

        if ("GET".equals(request.method) && action.isEmpty()) {
            JSONObject note = findNote(id);
            sendJson(out, note == null ? 404 : 200, note == null ? new JSONObject().put("detail", "Note not found") : note);
            return;
        }
        if ("PUT".equals(request.method) && action.isEmpty()) {
            JSONObject note = updateNoteFields(id, requestJson(request));
            sendJson(out, note == null ? 404 : 200, note == null ? new JSONObject().put("detail", "Note not found") : note);
            return;
        }
        if ("DELETE".equals(request.method) && action.isEmpty()) {
            boolean deleted = deleteNote(id);
            sendJson(out, deleted ? 200 : 404, deleted ? new JSONObject().put("ok", true) : new JSONObject().put("detail", "Note not found"));
            return;
        }
        if ("POST".equals(request.method) && "pin".equals(action)) {
            JSONObject note = toggleNoteBoolean(id, "pinned");
            sendJson(out, note == null ? 404 : 200, note == null
                    ? new JSONObject().put("detail", "Note not found")
                    : new JSONObject().put("ok", true).put("pinned", note.optBoolean("pinned", false)));
            return;
        }
        if ("POST".equals(request.method) && "archive".equals(action)) {
            JSONObject note = toggleNoteBoolean(id, "archived");
            sendJson(out, note == null ? 404 : 200, note == null
                    ? new JSONObject().put("detail", "Note not found")
                    : new JSONObject().put("ok", true).put("archived", note.optBoolean("archived", false)));
            return;
        }
        if ("POST".equals(request.method) && "items".equals(action) && parts.length >= 4 && "toggle".equals(parts[3])) {
            JSONObject result = toggleNoteItem(id, parseInt(parts[2], -1));
            int status = result.optInt("_status", 200);
            result.remove("_status");
            sendJson(out, status, result);
            return;
        }

        sendJson(out, 404, new JSONObject().put("detail", "Mobile notes route not implemented"));
    }

    private void routeCalendar(Request request, OutputStream out, String tail) throws Exception {
        if (tail == null) tail = "";
        if (tail.startsWith("/")) tail = tail.substring(1);

        if (tail.isEmpty()) {
            if ("GET".equals(request.method)) {
                sendJson(out, 200, mobileCalendarList());
                return;
            }
        }
        if ("calendars".equals(tail)) {
            if ("GET".equals(request.method)) {
                sendJson(out, 200, mobileCalendarList());
                return;
            }
            if ("POST".equals(request.method)) {
                sendJson(out, 200, mobileCalendarCreate(request));
                return;
            }
        }
        if (tail.startsWith("calendars/")) {
            String id = decodePathPart(tail.substring("calendars/".length()));
            if ("PUT".equals(request.method)) {
                JSONObject result = mobileCalendarUpdate(id, request);
                int status = result.optInt("_status", 200);
                result.remove("_status");
                sendJson(out, status, result);
                return;
            }
            if ("DELETE".equals(request.method)) {
                JSONObject result = mobileCalendarDelete(id);
                int status = result.optInt("_status", 200);
                result.remove("_status");
                sendJson(out, status, result);
                return;
            }
        }
        if ("events".equals(tail)) {
            if ("GET".equals(request.method)) {
                sendJson(out, 200, mobileCalendarEvents(request));
                return;
            }
            if ("POST".equals(request.method)) {
                sendJson(out, 200, mobileCalendarCreateEvent(requestJson(request)));
                return;
            }
        }
        if (tail.startsWith("events/")) {
            String uid = decodePathPart(tail.substring("events/".length()));
            if ("PUT".equals(request.method)) {
                JSONObject result = mobileCalendarUpdateEvent(uid, requestJson(request));
                int status = result.optInt("_status", 200);
                result.remove("_status");
                sendJson(out, status, result);
                return;
            }
            if ("DELETE".equals(request.method)) {
                JSONObject result = mobileCalendarDeleteEvent(uid);
                int status = result.optInt("_status", 200);
                result.remove("_status");
                sendJson(out, status, result);
                return;
            }
        }
        if ("POST".equals(request.method) && "import".equals(tail)) {
            JSONObject result = mobileCalendarImport(request);
            int status = result.optInt("_status", 200);
            result.remove("_status");
            sendJson(out, status, result);
            return;
        }
        if ("GET".equals(request.method) && tail.startsWith("export/")) {
            String calId = decodePathPart(tail.substring("export/".length()));
            JSONObject cal = findMobileCalendar(calId);
            if (cal == null) {
                sendJson(out, 404, new JSONObject().put("detail", "Calendar not found"));
                return;
            }
            sendCalendarDownload(out, safeIcsFilename(cal.optString("name", "calendar")), buildMobileCalendarIcs(cal));
            return;
        }
        if ("POST".equals(request.method) && "sync".equals(tail)) {
            sendJson(out, 200, new JSONObject()
                    .put("ok", true)
                    .put("calendars", 0)
                    .put("events", 0)
                    .put("deleted", 0)
                    .put("mobile_standalone", true));
            return;
        }
        if ("POST".equals(request.method) && "quick-parse".equals(tail)) {
            JSONObject result = mobileCalendarQuickParse(requestJson(request));
            int status = result.optInt("_status", 200);
            result.remove("_status");
            sendJson(out, status, result);
            return;
        }

        sendJson(out, 404, new JSONObject().put("detail", "Mobile calendar route not implemented"));
    }

    private void routeResearch(Request request, OutputStream out, String tail) throws Exception {
        if (tail == null) tail = "";
        if (tail.startsWith("/")) tail = tail.substring(1);

        if ("GET".equals(request.method) && "image".equals(tail)) {
            sendMobileResearchImage(request, out);
            return;
        }
        if ("GET".equals(request.method) && "active".equals(tail)) {
            sendJson(out, 200, mobileResearchActive());
            return;
        }
        if ("GET".equals(request.method) && "library".equals(tail)) {
            sendJson(out, 200, mobileResearchLibrary(request));
            return;
        }
        if ("POST".equals(request.method) && "start".equals(tail)) {
            JSONObject result = mobileResearchStart(requestJson(request));
            int status = result.optInt("_status", 200);
            result.remove("_status");
            sendJson(out, status, result);
            return;
        }
        if ("GET".equals(request.method) && tail.startsWith("stream/")) {
            mobileResearchStream(decodePathPart(tail.substring("stream/".length())), out);
            return;
        }
        if ("GET".equals(request.method) && tail.startsWith("status/")) {
            JSONObject result = mobileResearchStatus(decodePathPart(tail.substring("status/".length())));
            int status = result.optInt("_status", 200);
            result.remove("_status");
            sendJson(out, status, result);
            return;
        }
        if ("POST".equals(request.method) && tail.startsWith("cancel/")) {
            JSONObject result = mobileResearchCancel(decodePathPart(tail.substring("cancel/".length())));
            int status = result.optInt("_status", 200);
            result.remove("_status");
            sendJson(out, status, result);
            return;
        }
        if ("POST".equals(request.method) && tail.startsWith("result-peek/")) {
            JSONObject result = mobileResearchResult(decodePathPart(tail.substring("result-peek/".length())));
            int status = result.optInt("_status", 200);
            result.remove("_status");
            sendJson(out, status, result);
            return;
        }
        if ("POST".equals(request.method) && tail.startsWith("result/")) {
            JSONObject result = mobileResearchResult(decodePathPart(tail.substring("result/".length())));
            int status = result.optInt("_status", 200);
            result.remove("_status");
            sendJson(out, status, result);
            return;
        }
        if ("GET".equals(request.method) && tail.startsWith("report/")) {
            sendMobileResearchReport(decodePathPart(tail.substring("report/".length())), out);
            return;
        }
        if ("GET".equals(request.method) && tail.startsWith("detail/")) {
            JSONObject result = mobileResearchDetail(decodePathPart(tail.substring("detail/".length())));
            int status = result.optInt("_status", 200);
            result.remove("_status");
            sendJson(out, status, result);
            return;
        }
        if ("POST".equals(request.method) && tail.startsWith("spinoff/")) {
            JSONObject result = mobileResearchSpinoff(decodePathPart(tail.substring("spinoff/".length())));
            int status = result.optInt("_status", 200);
            result.remove("_status");
            sendJson(out, status, result);
            return;
        }

        String[] parts = tail.split("/");
        if (parts.length >= 2) {
            String id = decodePathPart(parts[0]);
            String action = parts[1];
            if ("POST".equals(request.method) && "archive".equals(action)) {
                JSONObject result = mobileResearchArchive(id, request);
                int status = result.optInt("_status", 200);
                result.remove("_status");
                sendJson(out, status, result);
                return;
            }
            if ("POST".equals(request.method) && ("hide-image".equals(action) || "unhide-images".equals(action))) {
                sendJson(out, 200, new JSONObject().put("ok", true).put("id", id).put("mobile_standalone", true));
                return;
            }
        }
        if ("DELETE".equals(request.method) && !tail.isEmpty() && !tail.contains("/")) {
            sendJson(out, 200, mobileResearchDelete(decodePathPart(tail)));
            return;
        }

        sendJson(out, 404, new JSONObject().put("detail", "Mobile research route not implemented"));
    }

    private JSONObject mobileResearchStart(JSONObject body) throws Exception {
        String query = jsonString(body, "query", "").trim();
        if (query.isEmpty()) {
            return new JSONObject().put("_status", 400).put("detail", "Research query is required");
        }

        String id = "rp-" + UUID.randomUUID().toString().replace("-", "").substring(0, 12);
        long startedMs = System.currentTimeMillis();
        String depth = mobileResearchDepth(body.optString("depth", body.optString("research_depth", "standard")));
        String reportLayout = mobileResearchReportLayout(body.optString("report_layout", body.optString("layout", "auto")));
        int rounds = jsonInt(body, "max_rounds", 0);
        if (rounds <= 0) rounds = mobileResearchDefaultRounds(depth);
        rounds = Math.max(1, Math.min(8, rounds));
        String category = jsonString(body, "category", "").trim();
        String searchProvider = jsonString(body, "search_provider", jsonString(body, "search_engine", "")).trim();
        searchProvider = mobileEffectiveSearchProvider(searchProvider);
        JSONObject endpoint = mobileResearchEndpoint(body);
        String model = mobileResearchModel(body, endpoint);
        String endpointId = endpoint == null ? "" : endpoint.optString("id");
        String endpointName = endpoint == null ? "" : endpoint.optString("name", hostLabel(endpoint.optString("base_url")));
        String fingerprint = mobileResearchFingerprint(query, category, searchProvider, rounds, endpointId, model, depth, reportLayout);
        JSONObject duplicate = mobileResearchRecentDuplicate(fingerprint, startedMs);
        if (duplicate != null) {
            return new JSONObject()
                    .put("session_id", duplicate.optString("id"))
                    .put("status", "running")
                    .put("query", duplicate.optString("query", query))
                    .put("deduped", true)
                    .put("mobile_standalone", true);
        }

        JSONObject item = new JSONObject()
                .put("id", id)
                .put("session_id", id)
                .put("fingerprint", fingerprint)
                .put("owner", "mobile")
                .put("query", query)
                .put("category", category)
                .put("status", "running")
                .put("started_at", startedMs / 1000L)
                .put("started_iso", isoTimestamp(startedMs))
                .put("completed_at", 0)
                .put("duration", "")
                .put("rounds", rounds)
                .put("depth", depth)
                .put("report_layout", reportLayout)
                .put("source_count", 0)
                .put("sources", new JSONArray())
                .put("raw_findings", new JSONArray())
                .put("result", "")
                .put("raw_report", "")
                .put("archived", false)
                .put("mobile_standalone", true)
                .put("search_provider", searchProvider)
                .put("endpoint_id", endpointId)
                .put("endpoint_name", endpointName)
                .put("model", model)
                .put("progress", new JSONObject()
                        .put("phase", "planning")
                        .put("round", 1)
                        .put("queries", rounds)
                        .put("total_sources", 0)
                        .put("total_findings", 0)
                        .put("model", model));
        mobileResearchUpsert(item);

        final JSONObject workerItem = item;
        final String workerQuery = query;
        final String workerCategory = category;
        final String workerSearchProvider = searchProvider;
        final int workerRounds = rounds;
        final JSONObject workerEndpoint = endpoint;
        final String workerModel = model;
        final String workerDepth = depth;
        final String workerReportLayout = reportLayout;
        final long workerStartedMs = startedMs;
        Thread worker = new Thread(() -> mobileResearchRun(workerItem, workerQuery, workerCategory,
                workerSearchProvider, workerRounds, workerEndpoint, workerModel, workerDepth,
                workerReportLayout, workerStartedMs),
                "OdysseusMobileResearch-" + id);
        worker.setDaemon(true);
        worker.start();

        return new JSONObject()
                .put("session_id", id)
                .put("status", "running")
                .put("query", query)
                .put("mobile_standalone", true);
    }

    private void mobileResearchRun(JSONObject item, String query, String category, String searchProvider,
                                   int rounds, JSONObject endpoint, String model, String depth,
                                   String reportLayout, long startedMs) {
        try {
            item.put("progress", new JSONObject()
                    .put("phase", "searching")
                    .put("round", 1)
                    .put("queries", rounds)
                    .put("total_sources", 0)
                    .put("total_findings", 0)
                    .put("model", model));
            mobileResearchUpsert(item);

            JSONArray sources = mobileResearchCollectSources(query, category, searchProvider, rounds, item, model);
            if (mobileResearchIsCancelled(item.optString("id"))) return;

            JSONArray findings = mobileResearchFindingsFromSources(sources);
            item.put("sources", sources)
                    .put("source_count", sources.length())
                    .put("raw_findings", findings)
                    .put("progress", new JSONObject()
                            .put("phase", "analyzing")
                            .put("round", rounds)
                            .put("queries", rounds)
                            .put("total_sources", sources.length())
                            .put("total_findings", findings.length())
                            .put("model", model));
            mobileResearchUpsert(item);
            if (mobileResearchIsCancelled(item.optString("id"))) return;

            item.put("progress", new JSONObject()
                    .put("phase", "writing")
                    .put("round", rounds)
                    .put("queries", rounds)
                    .put("total_sources", sources.length())
                    .put("total_findings", findings.length())
                    .put("model", model));
            mobileResearchUpsert(item);

            String report;
            if (endpoint == null || model.isEmpty()) {
                report = mobileResearchNoEndpointReport(query, category, searchProvider, depth, reportLayout);
            } else {
                JSONArray messages = mobileResearchMessages(query, category, searchProvider, rounds, sources, depth, reportLayout);
                report = valueOr(callChat(endpoint, model, messages, 8192), "").trim();
                if (report.isEmpty()) report = mobileResearchEmptyModelReport(query, category);
            }
            if (mobileResearchIsCancelled(item.optString("id"))) return;

            long completedMs = System.currentTimeMillis();
            item.put("status", "done")
                    .put("completed_at", completedMs / 1000L)
                    .put("completed_iso", isoTimestamp(completedMs))
                    .put("duration", mobileResearchDuration(startedMs, completedMs))
                    .put("source_count", sources.length())
                    .put("sources", sources)
                    .put("raw_findings", findings)
                    .put("result", report)
                    .put("raw_report", report)
                    .put("stats", new JSONObject()
                            .put("Duration", mobileResearchDuration(startedMs, completedMs))
                            .put("Rounds", String.valueOf(rounds))
                            .put("Depth", mobileResearchDepthLabel(depth))
                            .put("Layout", mobileResearchLayoutLabel(reportLayout)))
                    .put("progress", new JSONObject()
                            .put("phase", "writing")
                            .put("round", rounds)
                            .put("queries", rounds)
                            .put("total_sources", sources.length())
                            .put("total_findings", findings.length())
                            .put("model", model));
            mobileResearchUpsert(item);
            OdysseusNotifications.showResearchComplete(appContext, item.optString("id"), query);
        } catch (Exception ex) {
            long completedMs = System.currentTimeMillis();
            String error = "Mobile research model request failed: " + truncateError(valueOr(ex.getMessage(), "request failed"), 240);
            try {
                item.put("status", "error")
                        .put("completed_at", completedMs / 1000L)
                        .put("completed_iso", isoTimestamp(completedMs))
                        .put("duration", mobileResearchDuration(startedMs, completedMs))
                        .put("error", error)
                        .put("progress", new JSONObject()
                                .put("phase", "writing")
                                .put("round", rounds)
                                .put("queries", rounds)
                                .put("total_sources", item.optInt("source_count", 0))
                                .put("total_findings", item.optJSONArray("raw_findings") == null ? 0 : item.optJSONArray("raw_findings").length())
                                .put("model", model));
                mobileResearchUpsert(item);
            } catch (Exception ignored) {
            }
        }
    }

    private JSONObject mobileResearchEndpoint(JSONObject body) throws Exception {
        String endpointId = jsonString(body, "endpoint_id", "").trim();
        JSONObject endpoint = endpointId.isEmpty() ? null : findEndpoint(endpointId);
        if (endpoint == null) {
            String endpointUrl = jsonString(body, "endpoint_url", jsonString(body, "endpoint", "")).trim();
            if (!endpointUrl.isEmpty()) endpoint = endpointForProbe("", endpointUrl);
        }
        if (endpoint == null) endpoint = firstEnabledEndpoint();
        if (endpoint != null && !endpoint.optBoolean("is_enabled", true)) return null;
        return endpoint;
    }

    private String mobileResearchModel(JSONObject body, JSONObject endpoint) {
        String model = jsonString(body, "model", "").trim();
        if (!model.isEmpty() || endpoint == null) return model;
        JSONArray models = endpoint.optJSONArray("models");
        return models != null && models.length() > 0 ? models.optString(0, "") : "";
    }

    private String mobileResearchDepth(String raw) {
        String depth = valueOr(raw, "").trim().toLowerCase(Locale.US).replace('-', '_');
        if ("quick".equals(depth) || "brief".equals(depth)) return "quick";
        if ("detailed".equals(depth) || "deep".equals(depth)) return "detailed";
        if ("exhaustive".equals(depth) || "maximum".equals(depth) || "max".equals(depth)) return "exhaustive";
        return "standard";
    }

    private int mobileResearchDefaultRounds(String depth) {
        if ("quick".equals(depth)) return 2;
        if ("detailed".equals(depth)) return 6;
        if ("exhaustive".equals(depth)) return 8;
        return 4;
    }

    private String mobileResearchDepthLabel(String depth) {
        if ("quick".equals(depth)) return "Quick";
        if ("detailed".equals(depth)) return "Detailed";
        if ("exhaustive".equals(depth)) return "Exhaustive";
        return "Standard";
    }

    private String mobileResearchReportLayout(String raw) {
        String layout = valueOr(raw, "").trim().toLowerCase(Locale.US).replace('-', '_');
        if ("reader".equals(layout) || "no_index".equals(layout) || "noindex".equals(layout)) return "reader";
        if ("side".equals(layout) || "side_index".equals(layout) || "sidebar".equals(layout)) return "side_index";
        if ("top".equals(layout) || "top_index".equals(layout) || "horizontal".equals(layout)) return "top_index";
        if ("magazine".equals(layout) || "feature".equals(layout) || "editorial".equals(layout)) return "magazine";
        if ("briefing".equals(layout) || "dashboard".equals(layout) || "board".equals(layout)) return "briefing";
        if ("paper".equals(layout) || "academic".equals(layout) || "journal".equals(layout)) return "paper";
        if ("atlas".equals(layout) || "visual".equals(layout) || "gallery".equals(layout)) return "atlas";
        return "auto";
    }

    private String mobileResearchLayoutLabel(String layout) {
        if ("reader".equals(layout)) return "Reader";
        if ("side_index".equals(layout)) return "Side index";
        if ("top_index".equals(layout)) return "Top index";
        if ("magazine".equals(layout)) return "Magazine";
        if ("briefing".equals(layout)) return "Briefing";
        if ("paper".equals(layout)) return "Paper";
        if ("atlas".equals(layout)) return "Atlas";
        return "Auto";
    }

    private String mobileResearchResolveReportLayout(String layout, String category) {
        String normalized = mobileResearchReportLayout(layout);
        if (!"auto".equals(normalized)) return normalized;
        String cat = valueOr(category, "").trim().toLowerCase(Locale.US).replace("-", "");
        if ("product".equals(cat) || "comparison".equals(cat) || "compare".equals(cat)) return "briefing";
        if ("factcheck".equals(cat) || "fact".equals(cat)) return "paper";
        if ("howto".equals(cat) || "guide".equals(cat)) return "atlas";
        if ("landscape".equals(cat) || "market".equals(cat)) return "magazine";
        return "magazine";
    }

    private JSONArray mobileResearchCollectSources(String query, String category, String provider, int rounds) throws Exception {
        return mobileResearchCollectSources(query, category, provider, rounds, null, "");
    }

    private JSONArray mobileResearchCollectSources(String query, String category, String provider, int rounds,
                                                   JSONObject item, String model) throws Exception {
        JSONArray sources = new JSONArray();
        List<String> seen = new ArrayList<>();
        List<String> queries = mobileResearchSearchQueries(query, category, rounds);
        int perQuery = Math.max(4, Math.min(8, 28 / Math.max(1, queries.size())));
        for (int queryIndex = 0; queryIndex < queries.size(); queryIndex++) {
            if (item != null && mobileResearchIsCancelled(item.optString("id"))) return sources;

            String q = queries.get(queryIndex);
            JSONArray results = mobileSearchWithProvider(q, provider, perQuery);
            for (int i = 0; i < results.length(); i++) {
                JSONObject source = results.optJSONObject(i);
                if (source == null) continue;
                String url = source.optString("url", "").trim();
                if (url.isEmpty() || seen.contains(url)) continue;
                seen.add(url);
                source.put("query", q);
                source.put("index", sources.length() + 1);
                sources.put(source);
                if (sources.length() >= 24) {
                    mobileResearchPublishSearchProgress(item, sources, queryIndex + 1, queries.size(), model);
                    return sources;
                }
            }
            mobileResearchPublishSearchProgress(item, sources, queryIndex + 1, queries.size(), model);
        }
        return sources;
    }

    private void mobileResearchPublishSearchProgress(JSONObject item, JSONArray sources, int round,
                                                     int totalQueries, String model) throws Exception {
        if (item == null) return;
        item.put("sources", sources)
                .put("source_count", sources.length())
                .put("progress", new JSONObject()
                        .put("phase", "searching")
                        .put("round", Math.max(1, round))
                        .put("queries", Math.max(1, totalQueries))
                        .put("total_sources", sources.length())
                        .put("total_findings", 0)
                        .put("model", model));
        mobileResearchUpsert(item);
    }

    private List<String> mobileResearchSearchQueries(String query, String category, int rounds) {
        List<String> queries = new ArrayList<>();
        String base = valueOr(query, "").trim();
        if (base.isEmpty()) return queries;
        queries.add(base);
        String cat = mobileResearchCategoryLabel(category);
        if ("fact-check research".equals(cat)) {
            queries.add(base + " evidence review");
            queries.add(base + " fact check sources");
            queries.add(base + " controversy limitations");
        } else if ("product research".equals(cat)) {
            queries.add(base + " reviews specifications");
            queries.add(base + " pricing alternatives");
            queries.add(base + " limitations complaints");
        } else if ("comparison research".equals(cat)) {
            queries.add(base + " comparison evidence");
            queries.add(base + " pros cons alternatives");
            queries.add(base + " expert review");
        } else if ("how-to research".equals(cat)) {
            queries.add(base + " guide steps");
            queries.add(base + " best practices risks");
            queries.add(base + " examples");
        } else {
            queries.add(base + " review evidence");
            queries.add(base + " scientific overview");
            queries.add(base + " recent findings");
            queries.add(base + " risks limitations");
            queries.add(base + " images diagrams");
        }
        int target = Math.max(1, Math.min(8, rounds));
        List<String> out = new ArrayList<>();
        for (String q : queries) {
            String cleaned = q.trim();
            if (!cleaned.isEmpty() && !out.contains(cleaned)) out.add(cleaned);
            if (out.size() >= target) break;
        }
        return out;
    }

    private JSONArray mobileResearchFindingsFromSources(JSONArray sources) throws Exception {
        JSONArray findings = new JSONArray();
        for (int i = 0; i < sources.length(); i++) {
            JSONObject source = sources.optJSONObject(i);
            if (source == null) continue;
            findings.put(new JSONObject()
                    .put("title", source.optString("title", "Source"))
                    .put("url", source.optString("url", ""))
                    .put("summary", source.optString("summary", source.optString("snippet", "")))
                    .put("provider", source.optString("provider", ""))
                    .put("image", source.optString("image", "")));
        }
        return findings;
    }

    private JSONArray mobileResearchMessages(String query, String category, String searchProvider, int rounds,
                                             JSONArray sources, String depth, String reportLayout) throws Exception {
        String cat = mobileResearchCategoryLabel(category);
        String system = "You are Odysseus Android standalone Deep Research. "
                + "The Android backend has already run live web search using the user's selected provider and will give you numbered sources. "
                + "Write a detailed, source-grounded research report in Markdown. "
                + "Cite web sources inline with bracket numbers like [1], [2]. "
                + "Do not invent URLs, fake citations, or imply you inspected pages beyond the provided source snippets. "
                + "Be extensive and structured: include Executive Summary, Background, Detailed Findings, Evidence Review, Open Questions, and Follow-up Searches. "
                + "For scientific topics, include mechanisms, terminology, compounds, limitations, and safety/legal caveats when relevant. "
                + mobileResearchDepthInstruction(depth) + " "
                + mobileResearchLayoutInstruction(reportLayout);
        String sourceContext = mobileSearchContext(sources);
        if (sourceContext.isEmpty()) {
            sourceContext = "No live web results were returned. Be explicit that the report is model-only and list searches the user should retry.";
        }
        String user = "Research topic: " + query + "\n"
                + "Category: " + (cat.isEmpty() ? "general" : cat) + "\n"
                + "Requested rounds: " + rounds + "\n"
                + "Depth preset: " + mobileResearchDepthLabel(depth) + "\n"
                + "Report layout: " + mobileResearchLayoutLabel(reportLayout) + "\n"
                + "Selected search engine: " + (searchProvider.isEmpty() ? "auto" : searchProvider) + "\n"
                + mobileCurrentDateContext() + "\n\n"
                + "Live search sources:\n" + sourceContext + "\n\n"
                + "Write the most complete report you can from these sources plus clearly-labeled background knowledge. "
                + "Use citations for claims that come from the live sources. "
                + "Do not include a metadata header; start with a strong Markdown title.";
        return new JSONArray()
                .put(new JSONObject().put("role", "system").put("content", system))
                .put(new JSONObject().put("role", "user").put("content", user));
    }

    private String mobileResearchDepthInstruction(String depth) {
        if ("quick".equals(depth)) {
            return "Depth preset is Quick: prioritize a concise but useful report with the strongest evidence and skip marginal detail.";
        }
        if ("detailed".equals(depth)) {
            return "Depth preset is Detailed: expand each major section with mechanisms, examples, caveats, competing interpretations, and practical implications.";
        }
        if ("exhaustive".equals(depth)) {
            return "Depth preset is Exhaustive: produce the most complete report possible, with dense subsectioning, definitions, tables where useful, edge cases, uncertainties, and follow-up search paths.";
        }
        return "Depth preset is Standard: balance completeness with readability and cover the main evidence thoroughly.";
    }

    private String mobileResearchLayoutInstruction(String reportLayout) {
        if ("reader".equals(reportLayout)) {
            return "Use clear H2/H3 sections so the reader layout remains scannable even without a visible index.";
        }
        if ("top_index".equals(reportLayout)) {
            return "Use concise section headings because the visual report will present them in a horizontal index.";
        }
        if ("side_index".equals(reportLayout)) {
            return "Use descriptive H2/H3 section headings because the visual report will present them in a side index.";
        }
        if ("magazine".equals(reportLayout)) {
            return "Use strong feature-style H2 section titles and rich narrative transitions; the visual report will use a large editorial magazine layout.";
        }
        if ("briefing".equals(reportLayout)) {
            return "Use short, scannable H2 sections, tables, and evidence bullets where useful; the visual report will use a dashboard-like briefing layout.";
        }
        if ("paper".equals(reportLayout)) {
            return "Use formal sections, careful terminology, and citation-heavy prose; the visual report will use an academic paper layout.";
        }
        if ("atlas".equals(reportLayout)) {
            return "Use image-friendly sections and clear descriptive captions/contexts where useful; the visual report will use a visual atlas layout.";
        }
        return "Use clear H2/H3 headings so the visual report can build a responsive index.";
    }

    private String mobileResearchCategoryLabel(String category) {
        String cat = valueOr(category, "").trim().toLowerCase(Locale.US);
        if ("product".equals(cat)) return "product research";
        if ("comparison".equals(cat) || "compare".equals(cat)) return "comparison research";
        if ("howto".equals(cat) || "how-to".equals(cat)) return "how-to research";
        if ("factcheck".equals(cat) || "fact-check".equals(cat)) return "fact-check research";
        if ("landscape".equals(cat)) return "landscape research";
        return cat;
    }

    private JSONObject mobileResearchStandaloneSource(JSONObject endpoint, String model) throws Exception {
        boolean hasEndpoint = endpoint != null && !valueOr(model, "").trim().isEmpty();
        String endpointName = endpoint == null ? "" : endpoint.optString("name", hostLabel(endpoint.optString("base_url")));
        return new JSONObject()
                .put("title", hasEndpoint ? "Android standalone synthesis" : "Android standalone setup")
                .put("url", "")
                .put("summary", hasEndpoint
                        ? "Generated on Android through " + endpointName + " / " + model + " with standalone provider-backed web search."
                        : "No model endpoint is configured on this Android install yet.")
                .put("source_type", "mobile")
                .put("mobile_standalone", true);
    }

    private String mobileResearchNoEndpointReport(String query, String category, String searchProvider,
                                                  String depth, String reportLayout) {
        String cat = mobileResearchCategoryLabel(category);
        return "# " + query + "\n\n"
                + "## Executive Summary\n"
                + "Deep Research is now reachable in Android standalone mode, but this install does not have a model endpoint configured. "
                + "Add or enable an endpoint in Settings, then run this research again to generate a model-backed brief on the phone.\n\n"
                + "## Current Android Context\n"
                + "- Date/time: " + mobileCurrentDateTimeLabel() + "\n"
                + "- Category: " + (cat.isEmpty() ? "general" : cat) + "\n"
                + "- Depth preset: " + mobileResearchDepthLabel(depth) + "\n"
                + "- Report layout: " + mobileResearchLayoutLabel(reportLayout) + "\n"
                + "- Selected search engine: " + (valueOr(searchProvider, "").isEmpty() ? "auto" : searchProvider) + "\n\n"
                + "## What Works Here\n"
                + "- The Android app can now start Deep Research jobs.\n"
                + "- Android standalone can query the selected search provider for web sources.\n"
                + "- Completed research saves locally and appears in the Research Library.\n"
                + "- Visual reports, result previews, delete/archive, and follow-up chat creation are routed in Android standalone.\n\n"
                + "## Caveat\n"
                + "A model endpoint is still required for synthesis. Add one in Settings, then rerun this research.";
    }

    private String mobileResearchEmptyModelReport(String query, String category) {
        String cat = mobileResearchCategoryLabel(category);
        return "# " + query + "\n\n"
                + "The configured model returned an empty response. The Android Deep Research route worked, but there was no report text to display.\n\n"
                + "Category: " + (cat.isEmpty() ? "general" : cat) + "\n\n"
                + "Try again, choose another model, or check the selected search provider and endpoint settings.";
    }

    private String mobileResearchDuration(long startedMs, long completedMs) {
        long seconds = Math.max(1L, (completedMs - startedMs + 999L) / 1000L);
        return seconds + "s";
    }

    private JSONObject mobileResearchActive() throws Exception {
        JSONArray items = loadArray(PREF_RESEARCH_ITEMS);
        JSONArray active = new JSONArray();
        for (int i = 0; i < items.length(); i++) {
            JSONObject item = items.optJSONObject(i);
            if (item == null || !"running".equals(item.optString("status"))) continue;
            active.put(new JSONObject()
                    .put("session_id", item.optString("id"))
                    .put("query", item.optString("query"))
                    .put("status", "running")
                    .put("progress", mobileResearchProgress(item))
                    .put("started_at", item.optLong("started_at", 0)));
        }
        return new JSONObject().put("active", active).put("mobile_standalone", true);
    }

    private JSONObject mobileResearchLibrary(Request request) throws Exception {
        String search = valueOr(request.query.get("search"), "").trim().toLowerCase(Locale.US);
        String sort = valueOr(request.query.get("sort"), "recent").trim().toLowerCase(Locale.US);
        int limit = Math.max(1, Math.min(200, parseInt(valueOr(request.query.get("limit"), "50"), 50)));
        boolean archived = "true".equalsIgnoreCase(valueOr(request.query.get("archived"), "false"))
                || "1".equals(valueOr(request.query.get("archived"), "false"));
        JSONArray items = loadArray(PREF_RESEARCH_ITEMS);
        List<JSONObject> filtered = new ArrayList<>();
        for (int i = 0; i < items.length(); i++) {
            JSONObject item = items.optJSONObject(i);
            if (item == null) continue;
            if (item.optBoolean("archived", false) != archived) continue;
            if (!search.isEmpty() && !item.optString("query", "").toLowerCase(Locale.US).contains(search)) continue;
            filtered.add(mobileResearchSummaryItem(item));
        }
        Collections.sort(filtered, (a, b) -> {
            if ("oldest".equals(sort)) return Long.compare(mobileResearchSortTime(a), mobileResearchSortTime(b));
            if ("alpha".equals(sort)) return a.optString("query", "").compareToIgnoreCase(b.optString("query", ""));
            if ("most-messages".equals(sort)) return Integer.compare(b.optInt("source_count", 0), a.optInt("source_count", 0));
            return Long.compare(mobileResearchSortTime(b), mobileResearchSortTime(a));
        });
        JSONArray out = new JSONArray();
        for (int i = 0; i < Math.min(limit, filtered.size()); i++) out.put(filtered.get(i));
        return new JSONObject()
                .put("research", out)
                .put("total", filtered.size())
                .put("mobile_standalone", true);
    }

    private long mobileResearchSortTime(JSONObject item) {
        long completed = item.optLong("completed_at", 0);
        return completed > 0 ? completed : item.optLong("started_at", 0);
    }

    private JSONObject mobileResearchSummaryItem(JSONObject item) throws Exception {
        JSONArray sources = item.optJSONArray("sources");
        int sourceCount = item.optInt("source_count", sources == null ? 0 : sources.length());
        return new JSONObject()
                .put("id", item.optString("id"))
                .put("query", item.optString("query"))
                .put("category", item.optString("category", ""))
                .put("source_count", sourceCount)
                .put("status", item.optString("status", "done"))
                .put("duration", item.optString("duration", ""))
                .put("rounds", item.optInt("rounds", 1))
                .put("depth", item.optString("depth", "standard"))
                .put("report_layout", item.optString("report_layout", "auto"))
                .put("started_at", item.optLong("started_at", 0))
                .put("completed_at", item.optLong("completed_at", 0))
                .put("archived", item.optBoolean("archived", false))
                .put("model", item.optString("model", ""))
                .put("endpoint_name", item.optString("endpoint_name", ""))
                .put("mobile_standalone", true);
    }

    private JSONObject mobileResearchStatus(String id) throws Exception {
        JSONObject item = mobileResearchFind(id);
        if (item == null) return new JSONObject().put("_status", 404).put("detail", "No research found for this session");
        return new JSONObject()
                .put("status", item.optString("status", "done"))
                .put("progress", mobileResearchProgress(item))
                .put("started_at", item.optLong("started_at", 0))
                .put("avg_duration", item.optString("duration", ""))
                .put("mobile_standalone", true);
    }

    private void mobileResearchStream(String id, OutputStream out) throws Exception {
        writeHeaders(out, 200, "text/event-stream; charset=utf-8", -1);
        String last = "";
        for (int i = 0; i < 240; i++) {
            JSONObject item = mobileResearchFind(id);
            if (item == null) {
                writeSse(out, new JSONObject().put("status", "not_found"));
                return;
            }
            String status = item.optString("status", "done");
            JSONObject event = new JSONObject(mobileResearchProgress(item).toString())
                    .put("status", status)
                    .put("model", item.optString("model", ""));
            String serialized = event.toString();
            if (!serialized.equals(last)) {
                writeSse(out, event);
                last = serialized;
            }
            if (!"running".equals(status)) {
                JSONObject finalEvent = new JSONObject()
                        .put("status", status)
                        .put("final", true)
                        .put("model", item.optString("model", ""));
                if ("error".equals(status)) finalEvent.put("error", item.optString("error", "Research failed"));
                writeSse(out, finalEvent);
                return;
            }
            try {
                Thread.sleep(1500L);
            } catch (InterruptedException ex) {
                Thread.currentThread().interrupt();
                writeSse(out, new JSONObject().put("status", "error").put("final", true).put("error", "Research stream interrupted"));
                return;
            }
        }
        writeSse(out, new JSONObject().put("status", "error").put("final", true).put("error", "Research stream timed out"));
    }

    private JSONObject mobileResearchProgress(JSONObject item) throws Exception {
        JSONObject progress = item.optJSONObject("progress");
        if (progress == null) progress = new JSONObject();
        if (!progress.has("phase")) progress.put("phase", "writing");
        if (!progress.has("total_sources")) progress.put("total_sources", item.optInt("source_count", 0));
        if (!progress.has("total_findings")) {
            JSONArray findings = item.optJSONArray("raw_findings");
            progress.put("total_findings", findings == null ? 0 : findings.length());
        }
        if (!progress.has("model")) progress.put("model", item.optString("model", ""));
        return progress;
    }

    private JSONObject mobileResearchResult(String id) throws Exception {
        JSONObject item = mobileResearchFind(id);
        if (item == null) return new JSONObject().put("_status", 404).put("detail", "No research result available");
        JSONArray sources = item.optJSONArray("sources");
        JSONArray findings = item.optJSONArray("raw_findings");
        return new JSONObject()
                .put("result", item.optString("result", ""))
                .put("sources", sources == null ? new JSONArray() : sources)
                .put("raw_findings", findings == null ? new JSONArray() : findings)
                .put("category", item.optString("category", ""))
                .put("mobile_standalone", true);
    }

    private JSONObject mobileResearchDetail(String id) throws Exception {
        JSONObject item = mobileResearchFind(id);
        if (item == null) return new JSONObject().put("_status", 404).put("detail", "Research not found");
        return new JSONObject(item.toString());
    }

    private JSONObject mobileResearchCancel(String id) throws Exception {
        JSONObject item = mobileResearchFind(id);
        if (item == null) return new JSONObject().put("_status", 404).put("detail", "No research found for this session");
        boolean cancelled = false;
        if ("running".equals(item.optString("status"))) {
            item.put("status", "cancelled")
                    .put("completed_at", System.currentTimeMillis() / 1000L)
                    .put("duration", mobileResearchDuration(item.optLong("started_at", 0) * 1000L, System.currentTimeMillis()));
            mobileResearchUpsert(item);
            cancelled = true;
        }
        return new JSONObject().put("cancelled", cancelled).put("mobile_standalone", true);
    }

    private JSONObject mobileResearchDelete(String id) throws Exception {
        JSONArray items = loadArray(PREF_RESEARCH_ITEMS);
        JSONArray kept = new JSONArray();
        boolean deleted = false;
        for (int i = 0; i < items.length(); i++) {
            JSONObject item = items.optJSONObject(i);
            if (item != null && id.equals(item.optString("id"))) {
                deleted = true;
                continue;
            }
            if (item != null) kept.put(item);
        }
        saveArray(PREF_RESEARCH_ITEMS, kept);
        return new JSONObject().put("deleted", deleted).put("mobile_standalone", true);
    }

    private JSONObject mobileResearchArchive(String id, Request request) throws Exception {
        JSONObject item = mobileResearchFind(id);
        if (item == null) return new JSONObject().put("_status", 404).put("detail", "Research not found");
        String raw = valueOr(request.query.get("archived"), "true");
        boolean archived = !("false".equalsIgnoreCase(raw) || "0".equals(raw));
        item.put("archived", archived);
        mobileResearchUpsert(item);
        return new JSONObject().put("ok", true).put("id", id).put("archived", archived).put("mobile_standalone", true);
    }

    private JSONObject mobileResearchSpinoff(String id) throws Exception {
        JSONObject item = mobileResearchFind(id);
        if (item == null) return new JSONObject().put("_status", 404).put("detail", "Research not found");
        JSONObject endpoint = findEndpoint(item.optString("endpoint_id", ""));
        if (endpoint == null) endpoint = firstEnabledEndpoint();
        String model = item.optString("model", "");
        if (model.isEmpty() && endpoint != null) model = mobileResearchModel(new JSONObject(), endpoint);
        long now = System.currentTimeMillis();
        String sid = UUID.randomUUID().toString();
        JSONArray history = new JSONArray()
                .put(new JSONObject()
                        .put("role", "system")
                        .put("content", mobileResearchSpinoffContext(item)));
        JSONObject session = new JSONObject()
                .put("id", sid)
                .put("name", mobileResearchTrim("Research: " + item.optString("query", "Follow-up"), 80))
                .put("endpoint_url", endpoint == null ? "" : chatUrl(endpoint.optString("base_url")))
                .put("endpoint_id", endpoint == null ? "" : endpoint.optString("id"))
                .put("model", model)
                .put("rag", false)
                .put("archived", false)
                .put("folder", JSONObject.NULL)
                .put("message_count", history.length())
                .put("created_at", String.valueOf(now))
                .put("updated_at", String.valueOf(now))
                .put("last_message_at", String.valueOf(now))
                .put("history", history);
        JSONArray sessions = loadArray(PREF_SESSIONS);
        sessions.put(session);
        saveArray(PREF_SESSIONS, sessions);
        return new JSONObject().put("session_id", sid).put("id", sid).put("mobile_standalone", true);
    }

    private String mobileResearchSpinoffContext(JSONObject item) {
        return "You are continuing a follow-up chat about this Android standalone Deep Research report.\n\n"
                + "Original query: " + item.optString("query", "") + "\n"
                + "Category: " + item.optString("category", "") + "\n"
                + "Generated: " + item.optString("completed_iso", "") + "\n\n"
                + "Report:\n" + item.optString("result", "") + "\n\n"
                + "Sources:\n" + mobileResearchSourcesText(item);
    }

    private String mobileResearchSourcesText(JSONObject item) {
        JSONArray sources = item.optJSONArray("sources");
        if (sources == null || sources.length() == 0) return "- None";
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < sources.length(); i++) {
            JSONObject source = sources.optJSONObject(i);
            if (source == null) continue;
            sb.append("- ").append(source.optString("title", "Source"));
            String url = source.optString("url", "");
            if (!url.isEmpty()) sb.append(" (").append(url).append(")");
            String summary = source.optString("summary", "");
            if (!summary.isEmpty()) sb.append(": ").append(summary);
            sb.append('\n');
        }
        return sb.toString().trim();
    }

    private void sendMobileResearchReport(String id, OutputStream out) throws Exception {
        JSONObject item = mobileResearchFind(id);
        if (item == null) {
            sendJson(out, 404, new JSONObject().put("detail", "No visual report available for this session"));
            return;
        }
        String html = mobileResearchReportHtml(item);
        byte[] data = html.getBytes(StandardCharsets.UTF_8);
        writeHeaders(out, 200, "text/html; charset=utf-8", data.length);
        out.write(data);
    }

    private void sendMobileResearchImage(Request request, OutputStream out) throws Exception {
        String imageUrl = valueOr(request.query.get("url"), valueOr(request.query.get("u"), "")).trim();
        if (!(imageUrl.startsWith("http://") || imageUrl.startsWith("https://"))) {
            sendPlain(out, 404, "Image not found");
            return;
        }

        HttpURLConnection conn = null;
        try {
            URL target = new URL(imageUrl);
            conn = (HttpURLConnection) target.openConnection();
            conn.setRequestMethod("GET");
            conn.setInstanceFollowRedirects(true);
            conn.setConnectTimeout(12000);
            conn.setReadTimeout(18000);
            conn.setRequestProperty("User-Agent", "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Mobile Safari/537.36");
            conn.setRequestProperty("Accept", "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8");
            conn.setRequestProperty("Referer", target.getProtocol() + "://" + target.getHost() + "/");

            int status = conn.getResponseCode();
            if (status < 200 || status >= 300) {
                sendPlain(out, 404, "Image not found");
                return;
            }

            int maxBytes = 5 * 1024 * 1024;
            long length = conn.getContentLengthLong();
            if (length > maxBytes) {
                sendPlain(out, 404, "Image too large");
                return;
            }

            byte[] data = readLimited(conn.getInputStream(), maxBytes);
            if (data.length == 0) {
                sendPlain(out, 404, "Image not found");
                return;
            }

            String type = mobileResearchImageContentType(conn.getContentType(), data);
            if (type.isEmpty()) {
                sendPlain(out, 404, "Unsupported image");
                return;
            }

            writeHeaders(out, 200, type, data.length);
            out.write(data);
        } catch (Exception ex) {
            sendPlain(out, 404, "Image not found");
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    private String mobileResearchImageContentType(String rawType, byte[] data) {
        String type = valueOr(rawType, "").split(";", 2)[0].trim().toLowerCase(Locale.US);
        if (type.startsWith("image/")) return type;
        if (data == null || data.length < 12) return "";
        if ((data[0] & 0xff) == 0xff && (data[1] & 0xff) == 0xd8) return "image/jpeg";
        if ((data[0] & 0xff) == 0x89 && data[1] == 'P' && data[2] == 'N' && data[3] == 'G') return "image/png";
        if (data[0] == 'G' && data[1] == 'I' && data[2] == 'F') return "image/gif";
        if (data[0] == 'R' && data[1] == 'I' && data[2] == 'F' && data[3] == 'F'
                && data[8] == 'W' && data[9] == 'E' && data[10] == 'B' && data[11] == 'P') return "image/webp";
        String prefix = new String(data, 0, Math.min(data.length, 160), StandardCharsets.UTF_8).trim().toLowerCase(Locale.US);
        if (prefix.startsWith("<svg") || prefix.contains("<svg")) return "image/svg+xml";
        Bitmap bitmap = BitmapFactory.decodeByteArray(data, 0, data.length);
        return bitmap == null ? "" : "image/png";
    }

    private String mobileResearchReportHtml(JSONObject item) {
        String query = item.optString("query", "Research");
        String status = item.optString("status", "done");
        JSONArray sources = item.optJSONArray("sources");
        boolean isError = "error".equals(status);
        String reportMarkdown = isError ? item.optString("error", "Research failed") : item.optString("result", "");
        String title = isError ? "Research failed" : mobileResearchReportTitle(reportMarkdown, query);
        String reportBody = isError ? reportMarkdown : mobileResearchReportBodyMarkdown(reportMarkdown);
        String reportHtml = isError
                ? "<div class=\"error-box\"><pre>" + htmlEscape(reportBody) + "</pre></div>"
                : mobileResearchMarkdownToHtml(reportBody);
        String tocHtml = isError ? "" : mobileResearchTocHtml(reportBody);
        String reportLayout = mobileResearchResolveReportLayout(item.optString("report_layout", "auto"), item.optString("category", ""));
        String heroImageHtml = mobileResearchHeroImageHtml(sources);
        String imageStripHtml = mobileResearchImageStripHtml(sources);
        StringBuilder html = new StringBuilder();
        html.append("<!doctype html><html data-layout=\"").append(htmlEscape(reportLayout)).append("\"><head><meta charset=\"utf-8\">")
                .append("<meta name=\"viewport\" content=\"width=device-width,initial-scale=1,viewport-fit=cover\">")
                .append("<title>").append(htmlEscape(title)).append("</title>")
                .append("<script>window.reportImageFailed=function(img){try{img.onerror=null;img.classList.add('image-failed');var fig=img.closest('figure');if(fig)fig.classList.add('image-failed');var hero=img.closest('.hero-media');if(hero)hero.classList.add('image-failed');var inner=img.closest('.hero-inner');if(inner)inner.classList.add('no-image');}catch(e){}};window.reportImageLoaded=function(img){try{if(!img.naturalWidth||!img.naturalHeight||img.naturalWidth<96||img.naturalHeight<72)window.reportImageFailed(img);}catch(e){}};</script>")
                .append("<style>")
                .append(":root{color-scheme:dark light;font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#120f10;color:#f6f2ed;--bg:#120f10;--hero:#151112;--panel:#1d1819;--panel2:#261f20;--line:#3b3132;--text:#f6f2ed;--body:#eee6df;--muted:#b8aaa3;--accent:#39e66b;--gold:#d6a84f;--code:#b7ffc8;--shadow:rgba(0,0,0,.35)}")
                .append("html[data-theme='light']{background:#f6f1ea;color:#1f1a17;--bg:#f6f1ea;--hero:#fffaf4;--panel:#fffdf8;--panel2:#f0e7dc;--line:#dccfc3;--text:#1f1a17;--body:#302823;--muted:#725f55;--accent:#107a38;--gold:#9a6a1f;--code:#0d6b35;--shadow:rgba(80,57,38,.18)}")
                .append("*{box-sizing:border-box}html{scroll-behavior:smooth;scrollbar-gutter:stable}body{margin:0;background:var(--bg);color:var(--text);overflow-x:hidden}a{color:#77e59a;text-decoration:none}a:hover{text-decoration:underline}")
                .append("html[data-theme='light'] a{color:#0b7133}.toolbar{position:fixed;right:14px;top:14px;z-index:20;display:flex;gap:8px}.toolbar button{border:1px solid var(--line);background:var(--panel);color:var(--text);border-radius:8px;padding:9px 12px;font:600 13px system-ui;box-shadow:0 10px 30px var(--shadow)}")
                .append(".hero{padding:74px 18px 42px;border-bottom:1px solid var(--line);background:var(--hero)}.hero-inner{max-width:1120px;margin:0 auto;display:grid;grid-template-columns:minmax(0,1fr) minmax(260px,420px);gap:30px;align-items:end}.hero-inner.no-image{display:block}.hero-copy{min-width:0}.eyebrow{font-size:12px;text-transform:uppercase;letter-spacing:.12em;color:var(--accent);font-weight:800;margin:0 0 16px}")
                .append("h1{max-width:980px;font-size:clamp(32px,7vw,66px);line-height:1.02;letter-spacing:0;margin:0 0 18px;font-weight:850}.subtitle{max-width:900px;color:var(--muted);font-size:16px;line-height:1.55;margin:0}.hero-media{width:100%;aspect-ratio:4/3;border-radius:8px;overflow:hidden;border:1px solid var(--line);box-shadow:0 22px 70px var(--shadow);background:var(--panel2)}.hero-media img{width:100%;height:100%;object-fit:cover;display:block}.hero-media.image-failed{display:none}.image-strip figure.image-failed{display:none}.source-card img.image-failed{display:none}")
                .append(".layout{max-width:1120px;margin:0 auto;display:grid;grid-template-columns:240px minmax(0,1fr);gap:34px;padding:34px 18px 58px}.toc{position:sticky;top:72px;align-self:start;border-left:2px solid var(--line);padding-left:14px;padding-right:8px;color:var(--muted);max-height:calc(100dvh - 96px);overflow-y:auto;overflow-x:hidden;overscroll-behavior:contain;scrollbar-width:thin;scrollbar-color:var(--line) transparent}.toc::-webkit-scrollbar{width:6px;height:6px}.toc::-webkit-scrollbar-track{background:transparent}.toc::-webkit-scrollbar-thumb{background:var(--line);border-radius:999px}.toc:hover::-webkit-scrollbar-thumb{background:var(--accent)}.toc-title{font-size:12px;text-transform:uppercase;letter-spacing:.08em;margin:0 0 10px;color:#dfd5cf}.toc a{display:block;color:var(--muted);font-size:14px;line-height:1.35;padding:7px 0;overflow-wrap:anywhere}.toc-l3{padding-left:12px}.toc-l4{padding-left:24px;font-size:13px}.toc a:hover{color:var(--text)}html[data-theme='light'] .toc,html[data-theme='light'] .toc-title,html[data-theme='light'] .toc a{color:var(--text)}")
                .append(".content{min-width:0}.content h2{font-size:clamp(24px,5vw,36px);line-height:1.15;margin:34px 0 12px;padding-top:8px}.content h3{font-size:clamp(20px,4vw,26px);line-height:1.2;margin:28px 0 10px}.content h4{font-size:18px;margin:22px 0 8px}.content p,.content li{font-size:17px;line-height:1.72;color:var(--body)}.content p{margin:0 0 16px}.content ul,.content ol{padding-left:24px;margin:0 0 20px}.content li{margin:8px 0}.content strong{color:var(--text)}.content em{color:var(--gold)}.content code{background:var(--panel2);border:1px solid var(--line);border-radius:6px;padding:1px 5px;font:14px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:var(--code)}")
                .append(".content pre,.error-box pre{white-space:pre-wrap;overflow-wrap:anywhere;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:16px;font:14px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:var(--body);overflow:auto}.content blockquote{margin:18px 0;padding:2px 0 2px 18px;border-left:3px solid var(--gold);color:var(--body)}.content hr{border:0;border-top:1px solid var(--line);margin:30px 0}")
                .append(".table-wrap{overflow-x:auto;margin:20px 0;border:1px solid var(--line);border-radius:8px}.content table{width:100%;border-collapse:collapse;background:var(--panel)}.content th,.content td{padding:11px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}.content th{color:var(--text);background:var(--panel2);font-size:13px;text-transform:uppercase;letter-spacing:.05em}.content tr:last-child td{border-bottom:0}")
                .append(".image-strip{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:0 0 30px}.image-strip figure{margin:0;border:1px solid var(--line);border-radius:8px;overflow:hidden;background:var(--panel)}.image-strip img{display:block;width:100%;aspect-ratio:16/10;object-fit:cover}.image-strip figcaption{font-size:12px;line-height:1.35;color:var(--muted);padding:9px}")
                .append(".sources{border-top:1px solid var(--line);margin-top:42px;padding-top:28px}.source-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.source-card{border:1px solid var(--line);border-radius:8px;background:var(--panel);overflow:hidden}.source-card img{display:block;width:100%;aspect-ratio:16/9;object-fit:cover;background:var(--panel2)}.source-card-body{padding:14px}.source-title{font-weight:800;margin-bottom:7px}.source-summary{color:var(--muted);font-size:14px;line-height:1.5}.footer{border-top:1px solid var(--line);color:var(--muted);font-size:13px;padding:22px 18px 34px;text-align:center}")
                .append("html[data-layout='reader'] .layout{display:block;max-width:860px}html[data-layout='reader'] .toc{display:none}html[data-layout='top_index'] .layout{display:block;max-width:980px}html[data-layout='top_index'] .toc{position:sticky;top:0;z-index:10;margin:0 0 28px;border-left:0;border-bottom:1px solid var(--line);padding:10px 0;max-height:none;overflow-x:auto;overflow-y:hidden;background:var(--bg);display:flex;align-items:center;gap:8px;white-space:nowrap}html[data-layout='top_index'] .toc-title{margin:0;flex:0 0 auto}html[data-layout='top_index'] .toc a{display:inline-flex;flex:0 0 auto;align-items:center;max-width:360px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding:7px 10px;border:1px solid var(--line);border-radius:999px;background:var(--panel)}html[data-layout='top_index'] .toc-l3,html[data-layout='top_index'] .toc-l4{padding-left:10px;font-size:13px}@media(min-width:821px){html[data-layout='side_index'] .layout{grid-template-columns:260px minmax(0,1fr)}}")
                .append("html[data-layout='magazine'] .hero{min-height:min(76vh,760px);display:grid;align-items:end;padding:96px clamp(18px,6vw,72px) 58px;background:radial-gradient(circle at 16% 16%,color-mix(in srgb,var(--accent) 18%,transparent),transparent 46%),var(--hero)}html[data-layout='magazine'] .hero-inner{display:block;max-width:1180px}html[data-layout='magazine'] h1{font-size:clamp(44px,12vw,92px);line-height:.92;max-width:960px}html[data-layout='magazine'] .hero-media{margin-top:28px;max-width:920px;aspect-ratio:16/9}html[data-layout='magazine'] .layout{max-width:1180px;grid-template-columns:minmax(160px,240px) minmax(0,1fr)}html[data-layout='magazine'] .content h2{font-size:clamp(34px,6vw,62px);line-height:1;margin-top:56px}html[data-layout='magazine'] .content>p:first-of-type{font-size:19px}")
                .append("html[data-layout='briefing']{--hero:var(--bg);--panel2:color-mix(in srgb,var(--panel) 78%,var(--accent))}html[data-layout='briefing'] .hero{padding:70px 18px 22px;text-align:left}html[data-layout='briefing'] .hero-inner{display:block;max-width:1080px}html[data-layout='briefing'] h1{font-size:clamp(32px,6vw,58px);font-family:Inter,system-ui,sans-serif;font-weight:850;letter-spacing:0}html[data-layout='briefing'] .hero-media{margin-top:20px;max-width:980px;aspect-ratio:21/8}html[data-layout='briefing'] .layout{max-width:1240px;grid-template-columns:260px minmax(0,1fr);gap:20px}html[data-layout='briefing'] .toc{border:1px solid var(--line);border-radius:8px;padding:14px;background:var(--panel);height:auto}html[data-layout='briefing'] .content h2{font-family:Inter,system-ui,sans-serif;font-size:clamp(22px,4vw,30px);padding:14px 16px;border:1px solid var(--line);border-radius:8px;background:var(--panel)}html[data-layout='briefing'] .content h2:before{content:'';display:inline-block;width:9px;height:9px;border-radius:50%;background:var(--accent);margin-right:10px;vertical-align:middle}")
                .append("html[data-layout='paper']{background:color-mix(in srgb,var(--panel2) 38%,var(--bg));font-family:Georgia,'Times New Roman',serif}html[data-layout='paper'] .hero,html[data-layout='paper'] .layout,html[data-layout='paper'] .footer{max-width:860px;background:var(--panel);border-left:1px solid var(--line);border-right:1px solid var(--line)}html[data-layout='paper'] .hero{margin:36px auto 0;padding:62px 56px 26px;border-top:1px solid var(--line);text-align:left}html[data-layout='paper'] .hero-inner{display:block}html[data-layout='paper'] h1{font-size:clamp(32px,6vw,54px);line-height:1.05}html[data-layout='paper'] .hero-media{max-width:860px;margin:0 auto;padding:0 56px 24px;background:var(--panel);border-left:1px solid var(--line);border-right:1px solid var(--line);aspect-ratio:auto;box-shadow:none}html[data-layout='paper'] .hero-media img{height:auto;max-height:240px;object-fit:contain}html[data-layout='paper'] .layout{display:block;padding:28px 56px 60px}html[data-layout='paper'] .toc{display:none}html[data-layout='paper'] .content h2{font-size:28px;border-bottom:1px solid var(--line);padding-bottom:8px}")
                .append("html[data-layout='atlas'] .hero{padding:72px 18px 28px;text-align:left}html[data-layout='atlas'] .hero-inner{display:block;max-width:1180px}html[data-layout='atlas'] h1{font-size:clamp(38px,9vw,82px)}html[data-layout='atlas'] .hero-media{max-width:min(1180px,calc(100vw - 28px));margin:0 auto 24px;aspect-ratio:16/9}html[data-layout='atlas'] .layout{max-width:1220px;grid-template-columns:minmax(0,1fr) 220px}html[data-layout='atlas'] .toc{grid-column:2;grid-row:1;border-left:1px solid var(--line);border-right:0}html[data-layout='atlas'] .content{grid-column:1;grid-row:1}html[data-layout='atlas'] .image-strip{grid-template-columns:repeat(3,minmax(0,1fr));margin-bottom:36px}html[data-layout='atlas'] .content h2{font-size:clamp(28px,5vw,44px)}")
                .append("@media(max-width:820px){.toolbar{position:static;justify-content:flex-end;padding:10px 12px;background:var(--bg);border-bottom:1px solid var(--line);overflow-x:auto}.toolbar button{white-space:nowrap}.hero{padding:34px 18px 30px}.hero-inner{display:block}.hero-media{margin-top:22px}.layout{display:block;padding-top:24px}.toc{position:sticky;top:0;z-index:10;margin:0 -18px 24px;border-left:0;border-bottom:1px solid var(--line);padding:10px 18px;max-height:none;overflow-x:auto;overflow-y:hidden;background:var(--bg);display:flex;align-items:center;gap:8px;white-space:nowrap}.toc-title{margin:0;flex:0 0 auto}.toc a{display:inline-flex;flex:0 0 auto;align-items:center;max-width:72vw;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding:7px 10px;border:1px solid var(--line);border-radius:999px;background:var(--panel)}.toc-l3,.toc-l4{padding-left:10px;font-size:13px}.source-grid,.image-strip{grid-template-columns:1fr}.content p,.content li{font-size:16px}.content table{display:block;max-width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch}.content th,.content td{min-width:8rem;overflow-wrap:anywhere}}")
                .append("@media(max-width:920px) and (orientation:landscape){.toolbar{position:fixed;right:10px;top:10px;background:transparent;border-bottom:0;padding:0}.hero{padding-top:54px}.layout{display:grid;grid-template-columns:minmax(172px,220px) minmax(0,1fr);gap:22px;padding-top:22px}.toc{position:sticky;top:62px;display:block;margin:0;border-left:2px solid var(--line);border-bottom:0;padding:0 8px 0 14px;max-height:calc(100dvh - 78px);overflow-y:auto;overflow-x:hidden;background:transparent;white-space:normal}.toc-title{margin:0 0 10px}.toc a{display:block;max-width:none;white-space:normal;overflow:visible;text-overflow:clip;padding:7px 0;border:0;border-radius:0;background:transparent}.toc-l3{padding-left:12px}.toc-l4{padding-left:24px;font-size:13px}.content p,.content li{font-size:16px;line-height:1.66}}")
                .append("@page{margin:12mm}")
                .append("@media print{:root{--bg:#fff!important;--hero:#fff!important;--panel:#fff!important;--panel2:#f3f5f4!important;--line:#cfd6d2!important;--text:#111!important;--body:#222!important;--muted:#555!important;--accent:#107a38!important;--gold:#7c5a18!important;--code:#064f22!important;--shadow:transparent!important}*,*::before,*::after{box-shadow:none!important;text-shadow:none!important;filter:none!important;animation:none!important;transition:none!important}html,body{background:#fff!important;color:#111!important;-webkit-print-color-adjust:exact;print-color-adjust:exact}.toolbar,.toc{display:none!important}.hero{padding:0 0 10mm!important;margin:0 0 7mm!important;border-bottom:1px solid #d8ddd9!important;background:#fff!important;break-inside:avoid!important;page-break-inside:avoid!important}.hero-inner{display:block!important;max-width:none!important}.eyebrow{color:#107a38!important;font-size:8pt!important;margin:0 0 4mm!important}h1{font-size:22pt!important;line-height:1.18!important;color:#111!important;max-width:none!important;margin:0 0 5mm!important;letter-spacing:0!important}.subtitle{font-size:11pt!important;color:#444!important;max-width:none!important}.hero-media{width:100%!important;aspect-ratio:auto!important;max-height:none!important;margin:7mm 0 0!important;border:1px solid #d8ddd9!important;background:#fff!important;overflow:visible!important;break-inside:avoid!important;page-break-inside:avoid!important}.hero-media img{display:block!important;width:auto!important;max-width:100%!important;height:auto!important;max-height:92mm!important;object-fit:contain!important;margin:0 auto!important}.layout{display:block!important;max-width:none!important;padding:8mm 0 0!important;margin:0!important}.content{min-width:0!important}.content h2,.content h3,.content h4{color:#111!important;break-after:avoid!important;page-break-after:avoid!important}.content h2{font-size:17pt!important;margin:9mm 0 4mm!important}.content h3{font-size:13pt!important;margin:7mm 0 3mm!important}.content p,.content li{color:#222!important;font-size:10.5pt!important;line-height:1.48!important;orphans:3;widows:3}.content pre,.error-box pre,.content blockquote,.sources,.source-card,.image-strip figure{break-inside:avoid!important;page-break-inside:avoid!important}.content code,.content pre,.error-box pre{background:#f5f7f6!important;color:#111!important;border-color:#cfd6d2!important;white-space:pre-wrap!important;overflow-wrap:anywhere!important}.content blockquote{color:#222!important;border-left-color:#7c5a18!important}.table-wrap{overflow:visible!important;border:0!important;margin:5mm 0!important}.content table{width:100%!important;table-layout:fixed!important;border-collapse:collapse!important;background:#fff!important;color:#111!important;border:1px solid #bcc7c0!important;font-size:8.5pt!important;break-inside:auto!important;page-break-inside:auto!important}.content thead{display:table-header-group!important}.content tr{break-inside:avoid!important;page-break-inside:avoid!important}.content th,.content td{background:#fff!important;color:#111!important;border:1px solid #cfd6d2!important;padding:5px 6px!important;overflow-wrap:anywhere!important;word-break:normal!important;vertical-align:top!important}.content th{background:#eef2ef!important;font-size:8pt!important;letter-spacing:0!important;text-transform:none!important}.image-strip{display:block!important;margin:0 0 6mm!important}.image-strip figure,.source-card{background:#fff!important;border:1px solid #d8ddd9!important;margin:0 0 5mm!important}.image-strip img,.source-card img{display:block!important;width:auto!important;max-width:100%!important;height:auto!important;max-height:70mm!important;object-fit:contain!important;margin:0 auto!important}.source-grid{display:block!important}.source-summary,.footer{color:#555!important}.footer{border-top:1px solid #d8ddd9!important}}")
                .append("</style></head><body><main>");
        html.append("<div class=\"toolbar\"><button id=\"btn-theme\">Light</button><button id=\"btn-print\">Print/PDF</button><button id=\"btn-html\">Save HTML</button></div>");
        html.append("<section class=\"hero\"><div class=\"hero-inner").append(heroImageHtml.isEmpty() ? " no-image" : "").append("\"><div class=\"hero-copy\">")
                .append("<div class=\"eyebrow\">Deep Research Report</div>")
                .append("<h1>").append(htmlEscape(title)).append("</h1>")
                .append("<p class=\"subtitle\">").append(htmlEscape(query)).append("</p></div>")
                .append(heroImageHtml)
                .append("</div></section>");
        html.append("<div class=\"layout\"><aside class=\"toc\"><div class=\"toc-title\">Contents</div>")
                .append(tocHtml.isEmpty() ? "<span class=\"toc-empty\">Report</span>" : tocHtml)
                .append("</aside><article class=\"content\">")
                .append(imageStripHtml)
                .append(reportHtml);
        html.append("<section class=\"sources\"><h2>Sources</h2>");
        if (sources == null || sources.length() == 0) {
            html.append("<p>No sources saved.</p>");
        } else {
            html.append("<div class=\"source-grid\">");
            for (int i = 0; i < sources.length(); i++) {
                JSONObject source = sources.optJSONObject(i);
                if (source == null) continue;
                String sourceTitle = source.optString("title", "Source");
                String url = source.optString("url", "");
                html.append("<div class=\"source-card\">");
                String sourceImage = source.optString("image", "");
                if (mobileResearchIsReportImageCandidate(source)) {
                    html.append("<img ").append(mobileResearchImageAttrs(sourceImage, "")).append(">");
                }
                html.append("<div class=\"source-card-body\"><div class=\"source-title\">");
                if (url.startsWith("http://") || url.startsWith("https://")) {
                    html.append("<a href=\"").append(htmlEscape(url)).append("\" target=\"_blank\" rel=\"noopener\">").append(htmlEscape(sourceTitle)).append("</a>");
                } else {
                    html.append(htmlEscape(sourceTitle));
                }
                html.append("</div>");
                String summary = source.optString("summary", "");
                if (!summary.isEmpty()) html.append("<div class=\"source-summary\">").append(htmlEscape(summary)).append("</div>");
                html.append("</div></div>");
            }
            html.append("</div>");
        }
        html.append("</section></article></div>");
        html.append("<div class=\"footer\">Generated by Odysseus Deep Research - ").append(htmlEscape(mobileCurrentDateTimeLabel())).append("</div>");
        html.append("<script>(function(){")
                .append("var root=document.documentElement;var saved=localStorage.getItem('odysseus-report-theme');var prefersLight=window.matchMedia&&window.matchMedia('(prefers-color-scheme: light)').matches;function apply(t){root.setAttribute('data-theme',t);var b=document.getElementById('btn-theme');if(b)b.textContent=t==='light'?'Dark':'Light';try{localStorage.setItem('odysseus-report-theme',t);}catch(e){}}apply(saved||(prefersLight?'light':'dark'));")
                .append("function bridge(){return window.OdysseusAndroid||null;}function htmlForSave(){var c=document.documentElement.cloneNode(true);c.querySelectorAll('img[data-original-src]').forEach(function(img){img.setAttribute('src',img.getAttribute('data-original-src'));img.removeAttribute('onload');img.removeAttribute('onerror');});return '<!doctype html>'+c.outerHTML;}")
                .append("var t=document.getElementById('btn-theme');if(t)t.addEventListener('click',function(){apply(root.getAttribute('data-theme')==='light'?'dark':'light');});")
                .append("var p=document.getElementById('btn-print');if(p)p.addEventListener('click',function(){var b=bridge();if(b&&b.printReport){b.printReport(document.title||'Research report');return;}window.print();});")
                .append("var h=document.getElementById('btn-html');if(h)h.addEventListener('click',function(){var name=(document.title||'research-report').replace(/[^a-z0-9]+/gi,'-').replace(/^-+|-+$/g,'').substring(0,80);var html=htmlForSave();var br=bridge();if(br&&br.saveHtml){br.saveHtml(html,name||'research-report');return;}var b=new Blob([html],{type:'text/html'});var a=document.createElement('a');a.href=URL.createObjectURL(b);a.download=(name||'research-report')+'.html';a.click();setTimeout(function(){URL.revokeObjectURL(a.href);},1000);});")
                .append("})();</script></main></body></html>");
        return html.toString();
    }

    private String mobileResearchHeroImageHtml(JSONArray sources) {
        JSONObject imageSource = mobileResearchFirstImageSource(sources, 0);
        if (imageSource == null) return "";
        return "<div class=\"hero-media\"><img "
                + mobileResearchImageAttrs(imageSource.optString("image", ""), imageSource.optString("title", ""))
                + "></div>";
    }

    private String mobileResearchImageAttrs(String image, String alt) {
        String src = "/api/research/image?url=" + urlEncode(image);
        return "src=\"" + htmlEscape(src)
                + "\" alt=\"" + htmlEscape(valueOr(alt, ""))
                + "\" data-original-src=\"" + htmlEscape(image)
                + "\" loading=\"lazy\" decoding=\"async\""
                + " onload=\"reportImageLoaded(this)\""
                + " onerror=\"reportImageFailed(this)\"";
    }

    private String mobileResearchImageStripHtml(JSONArray sources) {
        if (sources == null) return "";
        StringBuilder html = new StringBuilder();
        int count = 0;
        for (int i = 0; i < sources.length() && count < 3; i++) {
            JSONObject source = sources.optJSONObject(i);
            if (source == null) continue;
            String image = source.optString("image", "");
            if (!mobileResearchIsReportImageCandidate(source)) continue;
            if (count == 0) html.append("<section class=\"image-strip\">");
            html.append("<figure><img ").append(mobileResearchImageAttrs(image, "")).append(">")
                    .append("<figcaption>").append(htmlEscape(mobileResearchTrim(source.optString("title", "Source image"), 110))).append("</figcaption></figure>");
            count++;
        }
        if (count > 0) html.append("</section>");
        return html.toString();
    }

    private JSONObject mobileResearchFirstImageSource(JSONArray sources, int start) {
        if (sources == null) return null;
        for (int i = Math.max(0, start); i < sources.length(); i++) {
            JSONObject source = sources.optJSONObject(i);
            if (source == null) continue;
            if (mobileResearchIsReportImageCandidate(source)) return source;
        }
        return null;
    }

    private boolean mobileResearchIsReportImageCandidate(JSONObject source) {
        if (source == null) return false;
        String image = source.optString("image", "").trim();
        if (!(image.startsWith("http://") || image.startsWith("https://"))) return false;

        String title = source.optString("title", "");
        String url = source.optString("url", "");
        String summary = source.optString("summary", source.optString("snippet", ""));
        String provider = source.optString("provider", "");
        String haystack = (title + " " + url + " " + image + " " + summary + " " + provider).toLowerCase(Locale.US);

        String[] sourceReject = {
                ".pdf", "(pdf", " pdf ", "/pdf/", "download_pdf",
                "essay", "homework", "assignment", "research titles", "topic ideas",
                "studycorgi", "coursehero", "chegg", "scribd", "slideshare",
                "pinterest", "facebook.com", "instagram.com", "x.com/", "twitter.com/",
                "login", "sign in", "subscribe"
        };
        for (String token : sourceReject) {
            if (haystack.contains(token)) return false;
        }

        String imageLower = image.toLowerCase(Locale.US);
        String[] imageReject = {
                "logo", "favicon", "sprite", "icon-", "/icon", "avatar",
                "placeholder", "default-image", "blank", "transparent",
                "tracking", "pixel", "1x1", "spacer", "loading",
                ".svg", ".ico", ".pdf"
        };
        for (String token : imageReject) {
            if (imageLower.contains(token)) return false;
        }

        return true;
    }

    private String mobileResearchReportTitle(String markdown, String fallback) {
        String[] lines = valueOr(markdown, "").replace("\r\n", "\n").replace('\r', '\n').split("\n");
        for (String line : lines) {
            String trimmed = line.trim();
            if (trimmed.startsWith("# ")) {
                return mobileResearchPlainInline(trimmed.substring(2).trim(), fallback);
            }
        }
        return mobileResearchTrim(fallback, 120);
    }

    private String mobileResearchReportBodyMarkdown(String markdown) {
        String[] lines = valueOr(markdown, "").replace("\r\n", "\n").replace('\r', '\n').split("\n", -1);
        StringBuilder out = new StringBuilder();
        boolean removedTitle = false;
        for (String line : lines) {
            if (!removedTitle && line.trim().startsWith("# ")) {
                removedTitle = true;
                continue;
            }
            out.append(line).append('\n');
        }
        return out.toString().trim();
    }

    private String mobileResearchTocHtml(String markdown) {
        String[] lines = valueOr(markdown, "").replace("\r\n", "\n").replace('\r', '\n').split("\n");
        StringBuilder html = new StringBuilder();
        for (String line : lines) {
            String trimmed = line.trim();
            int level = mobileResearchHeadingLevel(trimmed);
            if (level < 2 || level > 4) continue;
            String text = trimmed.substring(level).trim();
            if (text.isEmpty()) continue;
            html.append("<a class=\"toc-l").append(level).append("\" href=\"#")
                    .append(htmlEscape(mobileResearchHeadingId(text))).append("\">")
                    .append(htmlEscape(mobileResearchPlainInline(text, text))).append("</a>");
        }
        return html.toString();
    }

    private String mobileResearchMarkdownToHtml(String markdown) {
        String src = valueOr(markdown, "").replace("\r\n", "\n").replace('\r', '\n').trim();
        if (src.isEmpty()) return "<p>No report text saved.</p>";

        String[] lines = src.split("\n", -1);
        StringBuilder html = new StringBuilder();
        StringBuilder paragraph = new StringBuilder();
        boolean inUl = false;
        boolean inOl = false;

        for (int i = 0; i < lines.length; i++) {
            String line = lines[i];
            String trimmed = line.trim();

            if (trimmed.startsWith("```")) {
                if (paragraph.length() > 0) {
                    html.append("<p>").append(mobileResearchInlineMarkdown(paragraph.toString().trim())).append("</p>");
                    paragraph.setLength(0);
                }
                if (inUl) { html.append("</ul>"); inUl = false; }
                if (inOl) { html.append("</ol>"); inOl = false; }
                String lang = trimmed.length() > 3 ? trimmed.substring(3).trim() : "";
                StringBuilder code = new StringBuilder();
                i++;
                while (i < lines.length && !lines[i].trim().startsWith("```")) {
                    code.append(lines[i]).append('\n');
                    i++;
                }
                html.append("<pre");
                if ("mermaid".equalsIgnoreCase(lang)) html.append(" class=\"mermaid\"");
                html.append("><code>").append(htmlEscape(code.toString().replaceAll("\\s+$", ""))).append("</code></pre>");
                continue;
            }

            if (mobileResearchIsTableStart(lines, i)) {
                if (paragraph.length() > 0) {
                    html.append("<p>").append(mobileResearchInlineMarkdown(paragraph.toString().trim())).append("</p>");
                    paragraph.setLength(0);
                }
                if (inUl) { html.append("</ul>"); inUl = false; }
                if (inOl) { html.append("</ol>"); inOl = false; }
                List<String> headers = mobileResearchSplitTableRow(lines[i]);
                html.append("<div class=\"table-wrap\"><table><thead><tr>");
                for (String cell : headers) html.append("<th>").append(mobileResearchInlineMarkdown(cell.trim())).append("</th>");
                html.append("</tr></thead><tbody>");
                i += 2;
                while (i < lines.length && mobileResearchLooksLikeTableRow(lines[i])) {
                    List<String> cells = mobileResearchSplitTableRow(lines[i]);
                    html.append("<tr>");
                    for (int c = 0; c < Math.max(headers.size(), cells.size()); c++) {
                        String cell = c < cells.size() ? cells.get(c) : "";
                        html.append("<td>").append(mobileResearchInlineMarkdown(cell.trim())).append("</td>");
                    }
                    html.append("</tr>");
                    i++;
                }
                i--;
                html.append("</tbody></table></div>");
                continue;
            }

            if (trimmed.isEmpty()) {
                if (paragraph.length() > 0) {
                    html.append("<p>").append(mobileResearchInlineMarkdown(paragraph.toString().trim())).append("</p>");
                    paragraph.setLength(0);
                }
                if (inUl) { html.append("</ul>"); inUl = false; }
                if (inOl) { html.append("</ol>"); inOl = false; }
                continue;
            }

            int headingLevel = mobileResearchHeadingLevel(trimmed);
            if (headingLevel > 0) {
                if (paragraph.length() > 0) {
                    html.append("<p>").append(mobileResearchInlineMarkdown(paragraph.toString().trim())).append("</p>");
                    paragraph.setLength(0);
                }
                if (inUl) { html.append("</ul>"); inUl = false; }
                if (inOl) { html.append("</ol>"); inOl = false; }
                String heading = trimmed.substring(headingLevel).trim();
                int tagLevel = Math.min(4, Math.max(2, headingLevel));
                html.append("<h").append(tagLevel).append(" id=\"").append(htmlEscape(mobileResearchHeadingId(heading))).append("\">")
                        .append(mobileResearchInlineMarkdown(heading))
                        .append("</h").append(tagLevel).append(">");
                continue;
            }

            if (trimmed.matches("[-*_]{3,}")) {
                if (paragraph.length() > 0) {
                    html.append("<p>").append(mobileResearchInlineMarkdown(paragraph.toString().trim())).append("</p>");
                    paragraph.setLength(0);
                }
                if (inUl) { html.append("</ul>"); inUl = false; }
                if (inOl) { html.append("</ol>"); inOl = false; }
                html.append("<hr>");
                continue;
            }

            String bullet = mobileResearchBulletText(trimmed);
            if (bullet != null) {
                if (paragraph.length() > 0) {
                    html.append("<p>").append(mobileResearchInlineMarkdown(paragraph.toString().trim())).append("</p>");
                    paragraph.setLength(0);
                }
                if (inOl) { html.append("</ol>"); inOl = false; }
                if (!inUl) { html.append("<ul>"); inUl = true; }
                html.append("<li>").append(mobileResearchInlineMarkdown(bullet)).append("</li>");
                continue;
            }

            String ordered = mobileResearchOrderedText(trimmed);
            if (ordered != null) {
                if (paragraph.length() > 0) {
                    html.append("<p>").append(mobileResearchInlineMarkdown(paragraph.toString().trim())).append("</p>");
                    paragraph.setLength(0);
                }
                if (inUl) { html.append("</ul>"); inUl = false; }
                if (!inOl) { html.append("<ol>"); inOl = true; }
                html.append("<li>").append(mobileResearchInlineMarkdown(ordered)).append("</li>");
                continue;
            }

            if (inUl) { html.append("</ul>"); inUl = false; }
            if (inOl) { html.append("</ol>"); inOl = false; }
            if (trimmed.startsWith(">")) {
                if (paragraph.length() > 0) {
                    html.append("<p>").append(mobileResearchInlineMarkdown(paragraph.toString().trim())).append("</p>");
                    paragraph.setLength(0);
                }
                html.append("<blockquote><p>").append(mobileResearchInlineMarkdown(trimmed.substring(1).trim())).append("</p></blockquote>");
                continue;
            }

            if (paragraph.length() > 0) paragraph.append(' ');
            paragraph.append(trimmed);
        }

        if (paragraph.length() > 0) html.append("<p>").append(mobileResearchInlineMarkdown(paragraph.toString().trim())).append("</p>");
        if (inUl) html.append("</ul>");
        if (inOl) html.append("</ol>");
        return html.toString();
    }

    private int mobileResearchHeadingLevel(String trimmed) {
        if (!trimmed.startsWith("#")) return 0;
        int level = 0;
        while (level < trimmed.length() && trimmed.charAt(level) == '#') level++;
        if (level == 0 || level > 6 || level >= trimmed.length() || trimmed.charAt(level) != ' ') return 0;
        return level;
    }

    private String mobileResearchBulletText(String trimmed) {
        if (trimmed.length() < 3) return null;
        char first = trimmed.charAt(0);
        if ((first == '-' || first == '*' || first == '+') && Character.isWhitespace(trimmed.charAt(1))) {
            return trimmed.substring(2).trim();
        }
        return null;
    }

    private String mobileResearchOrderedText(String trimmed) {
        int i = 0;
        while (i < trimmed.length() && Character.isDigit(trimmed.charAt(i))) i++;
        if (i == 0 || i + 1 >= trimmed.length()) return null;
        char marker = trimmed.charAt(i);
        if ((marker == '.' || marker == ')') && Character.isWhitespace(trimmed.charAt(i + 1))) {
            return trimmed.substring(i + 2).trim();
        }
        return null;
    }

    private boolean mobileResearchIsTableStart(String[] lines, int index) {
        return index + 1 < lines.length
                && mobileResearchLooksLikeTableRow(lines[index])
                && mobileResearchIsTableSeparator(lines[index + 1]);
    }

    private boolean mobileResearchLooksLikeTableRow(String line) {
        String trimmed = valueOr(line, "").trim();
        return trimmed.startsWith("|") && trimmed.endsWith("|") && trimmed.indexOf('|', 1) > 0;
    }

    private boolean mobileResearchIsTableSeparator(String line) {
        if (!mobileResearchLooksLikeTableRow(line)) return false;
        for (String cell : mobileResearchSplitTableRow(line)) {
            if (!cell.trim().matches(":?-{3,}:?")) return false;
        }
        return true;
    }

    private List<String> mobileResearchSplitTableRow(String line) {
        String trimmed = valueOr(line, "").trim();
        if (trimmed.startsWith("|")) trimmed = trimmed.substring(1);
        if (trimmed.endsWith("|")) trimmed = trimmed.substring(0, trimmed.length() - 1);
        return Arrays.asList(trimmed.split("\\|", -1));
    }

    private String mobileResearchInlineMarkdown(String raw) {
        String escaped = htmlEscape(raw);
        escaped = escaped.replaceAll("`([^`]+)`", "<code>$1</code>");
        escaped = escaped.replaceAll("\\*\\*([^*]+)\\*\\*", "<strong>$1</strong>");
        escaped = escaped.replaceAll("__([^_]+)__", "<strong>$1</strong>");
        escaped = escaped.replaceAll("(?<!\\*)\\*([^*]+)\\*(?!\\*)", "<em>$1</em>");
        escaped = escaped.replaceAll("(?<!_)_([^_]+)_(?!_)", "<em>$1</em>");
        return mobileResearchLinksToHtml(escaped);
    }

    private String mobileResearchLinksToHtml(String escaped) {
        java.util.regex.Pattern pattern = java.util.regex.Pattern.compile("\\[([^\\]]+)]\\((https?://[^\\s)]+)\\)");
        java.util.regex.Matcher matcher = pattern.matcher(escaped);
        StringBuffer out = new StringBuffer();
        while (matcher.find()) {
            String text = matcher.group(1);
            String url = matcher.group(2);
            String replacement = "<a href=\"" + htmlEscape(url) + "\" target=\"_blank\" rel=\"noopener\">" + text + "</a>";
            matcher.appendReplacement(out, java.util.regex.Matcher.quoteReplacement(replacement));
        }
        matcher.appendTail(out);
        return out.toString();
    }

    private String mobileResearchHeadingId(String heading) {
        String id = mobileResearchPlainInline(heading, "section")
                .toLowerCase(Locale.US)
                .replaceAll("[^a-z0-9]+", "-")
                .replaceAll("^-+|-+$", "");
        return id.isEmpty() ? "section" : id;
    }

    private String mobileResearchPlainInline(String raw, String fallback) {
        String text = valueOr(raw, "")
                .replaceAll("^#+\\s*", "")
                .replaceAll("`([^`]+)`", "$1")
                .replaceAll("\\*\\*([^*]+)\\*\\*", "$1")
                .replaceAll("__([^_]+)__", "$1")
                .replaceAll("\\[([^\\]]+)]\\(([^)]+)\\)", "$1")
                .trim();
        return text.isEmpty() ? fallback : text;
    }

    private JSONObject mobileResearchFind(String id) throws Exception {
        JSONArray items = loadArray(PREF_RESEARCH_ITEMS);
        for (int i = 0; i < items.length(); i++) {
            JSONObject item = items.optJSONObject(i);
            if (item != null && id.equals(item.optString("id"))) return item;
        }
        return null;
    }

    private boolean mobileResearchIsCancelled(String id) {
        try {
            JSONObject item = mobileResearchFind(id);
            return item != null && "cancelled".equals(item.optString("status"));
        } catch (Exception ignored) {
            return false;
        }
    }

    private JSONObject mobileResearchRecentDuplicate(String fingerprint, long nowMs) throws Exception {
        if (valueOr(fingerprint, "").isEmpty()) return null;
        JSONArray items = loadArray(PREF_RESEARCH_ITEMS);
        for (int i = items.length() - 1; i >= 0; i--) {
            JSONObject item = items.optJSONObject(i);
            if (item == null || item.optBoolean("archived", false)) continue;
            if (!fingerprint.equals(item.optString("fingerprint", ""))) continue;
            String status = item.optString("status", "");
            if ("running".equals(status)) return item;
            long completedMs = item.optLong("completed_at", 0) * 1000L;
            long startedMs = item.optLong("started_at", 0) * 1000L;
            long referenceMs = completedMs > 0 ? completedMs : startedMs;
            if (referenceMs > 0 && nowMs - referenceMs <= 15000L) return item;
        }
        return null;
    }

    private String mobileResearchFingerprint(String query, String category, String searchProvider, int rounds,
                                             String endpointId, String model, String depth, String reportLayout) {
        return mobileResearchNormalizeKey(query) + "|"
                + mobileResearchNormalizeKey(category) + "|"
                + mobileResearchNormalizeKey(searchProvider) + "|"
                + rounds + "|"
                + mobileResearchNormalizeKey(endpointId) + "|"
                + mobileResearchNormalizeKey(model) + "|"
                + mobileResearchNormalizeKey(depth) + "|"
                + mobileResearchNormalizeKey(reportLayout);
    }

    private String mobileResearchNormalizeKey(String raw) {
        String text = valueOr(raw, "").trim().replaceAll("\\s+", " ").toLowerCase(Locale.US);
        return text;
    }

    private void mobileResearchUpsert(JSONObject item) throws Exception {
        JSONArray items = loadArray(PREF_RESEARCH_ITEMS);
        String id = item.optString("id");
        for (int i = 0; i < items.length(); i++) {
            JSONObject existing = items.optJSONObject(i);
            if (existing != null && id.equals(existing.optString("id"))) {
                items.put(i, item);
                saveArray(PREF_RESEARCH_ITEMS, items);
                return;
            }
        }
        items.put(item);
        saveArray(PREF_RESEARCH_ITEMS, items);
    }

    private String mobileResearchTrim(String raw, int max) {
        String text = valueOr(raw, "").replace('\n', ' ').replace('\r', ' ').trim();
        while (text.contains("  ")) text = text.replace("  ", " ");
        return text.length() > max ? text.substring(0, Math.max(0, max - 3)) + "..." : text;
    }

    private String htmlEscape(String raw) {
        return valueOr(raw, "")
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace("\"", "&quot;")
                .replace("'", "&#39;");
    }

    private void routeGallery(Request request, OutputStream out, String tail) throws Exception {
        if (tail == null) tail = "";
        if (tail.startsWith("/")) tail = tail.substring(1);

        if (tail.isEmpty() || "library".equals(tail)) {
            if ("GET".equals(request.method)) {
                sendJson(out, 200, listGalleryImages(request));
                return;
            }
        }
        if ("GET".equals(request.method) && "tags".equals(tail)) {
            sendJson(out, 200, listGalleryTags());
            return;
        }
        if ("GET".equals(request.method) && "stats".equals(tail)) {
            sendJson(out, 200, galleryStats());
            return;
        }
        if ("POST".equals(request.method) && "ai-tag-batch".equals(tail)) {
            sendJson(out, 200, new JSONObject()
                    .put("ok", true)
                    .put("queued", 0)
                    .put("total_untagged", 0)
                    .put("image_ids", new JSONArray()));
            return;
        }
        if ("POST".equals(request.method) && "upload".equals(tail)) {
            JSONObject result = galleryUpload(request, "file", null);
            int status = result.optInt("_status", 200);
            result.remove("_status");
            sendJson(out, status, result);
            return;
        }
        if ("albums".equals(tail)) {
            if ("GET".equals(request.method)) {
                sendJson(out, 200, listGalleryAlbums());
                return;
            }
            if ("POST".equals(request.method)) {
                JSONObject result = createGalleryAlbum(requestJson(request));
                int status = result.optInt("_status", 200);
                result.remove("_status");
                sendJson(out, status, result);
                return;
            }
        }
        if (tail.startsWith("albums/")) {
            routeGalleryAlbum(request, out, tail.substring("albums/".length()));
            return;
        }

        String[] parts = tail.split("/", 2);
        String imageId = decodePathPart(parts.length > 0 ? parts[0] : "");
        String action = parts.length > 1 ? parts[1] : "";
        if (imageId.isEmpty()) {
            sendJson(out, 404, new JSONObject().put("detail", "Mobile gallery route not implemented"));
            return;
        }

        if ("GET".equals(request.method) && action.isEmpty()) {
            JSONObject image = findGalleryImage(imageId);
            sendJson(out, image == null ? 404 : 200, image == null
                    ? new JSONObject().put("detail", "Image not found")
                    : galleryImageToClient(image));
            return;
        }
        if ("PATCH".equals(request.method) && action.isEmpty()) {
            JSONObject result = updateGalleryImage(imageId, requestJson(request));
            int status = result.optInt("_status", 200);
            result.remove("_status");
            sendJson(out, status, result);
            return;
        }
        if ("DELETE".equals(request.method) && action.isEmpty()) {
            JSONObject result = deleteGalleryImage(imageId);
            int status = result.optInt("_status", 200);
            result.remove("_status");
            sendJson(out, status, result);
            return;
        }
        if ("POST".equals(request.method) && "favorite".equals(action)) {
            JSONObject result = toggleGalleryFavorite(imageId);
            int status = result.optInt("_status", 200);
            result.remove("_status");
            sendJson(out, status, result);
            return;
        }
        if ("POST".equals(request.method) && "rename".equals(action)) {
            JSONObject result = renameGalleryImage(imageId, requestJson(request));
            int status = result.optInt("_status", 200);
            result.remove("_status");
            sendJson(out, status, result);
            return;
        }
        if ("POST".equals(request.method) && "replace".equals(action)) {
            JSONObject result = galleryUpload(request, "image", imageId);
            int status = result.optInt("_status", 200);
            result.remove("_status");
            sendJson(out, status, result);
            return;
        }
        if ("POST".equals(request.method) && "ai-tag".equals(action)) {
            JSONObject image = findGalleryImage(imageId);
            sendJson(out, image == null ? 404 : 200, image == null
                    ? new JSONObject().put("detail", "Image not found")
                    : new JSONObject().put("ok", true).put("ai_tags", image.optString("ai_tags", "")));
            return;
        }

        sendJson(out, 404, new JSONObject().put("detail", "Mobile gallery route not implemented"));
    }

    private void routeImageTool(Request request, OutputStream out, String tail) throws Exception {
        if (tail == null) tail = "";
        if (tail.startsWith("/")) tail = tail.substring(1);
        String action = tail.split("/", 2)[0];
        if (!"POST".equals(request.method)) {
            sendJson(out, 405, new JSONObject().put("detail", "Method not allowed"));
            return;
        }
        if (request.bodyTooLarge) {
            sendJson(out, 413, new JSONObject()
                    .put("error", "This inpaint request is too large for the app backend. Try a smaller mask area or resize the canvas."));
            return;
        }

        Bitmap edited;
        try {
            JSONObject body = requestJson(request);
            lastBgRemoveSource = "";
            lastBgRemoveError = "";
            Bitmap source = decodeJsonBitmap(body, "image");
            if (source == null) {
                sendJson(out, 400, new JSONObject().put("error", "No image provided"));
                return;
            }

            if ("inpaint".equals(action)) {
                Bitmap mask = decodeJsonBitmap(body, "mask");
                if (mask == null) {
                    sendJson(out, 400, new JSONObject().put("error", "Inpaint requires a mask"));
                    return;
                }
                JSONObject provider = null;
                try {
                    provider = runProviderInpaint(source, mask, body);
                } catch (Exception ex) {
                    if (hasExplicitImageProvider(body)) {
                        sendJson(out, 502, new JSONObject().put("error", "AI inpaint failed: " + truncateError(ex.getMessage(), 260)));
                        return;
                    }
                }
                if (provider != null && !provider.optString("image", "").isEmpty()) {
                    sendJson(out, 200, provider);
                    return;
                }
                sendJson(out, 400, new JSONObject()
                        .put("error", "Inpaint needs an image-edit model, not only an image-generation or vision model. Select or add gpt-image-1, dall-e-2, SDXL inpaint, Flux fill, or another endpoint that supports image+mask edits."));
                return;
            } else if ("remove-bg".equals(action) || "remove_bg".equals(action) || "rembg".equals(action) || "bgremove".equals(action)) {
                Bitmap hint = decodeJsonBitmap(body, "hint_mask");
                if (hint == null) hint = decodeJsonBitmap(body, "mask");
                Bitmap backgroundHint = decodeJsonBitmap(body, "background_mask");
                if (backgroundHint == null) backgroundHint = decodeJsonBitmap(body, "bg_hint_mask");
                if (backgroundHint == null) backgroundHint = decodeJsonBitmap(body, "background_hint_mask");
                double bgRemoveStrength = body.has("strength")
                        ? normalizedStrength(body, "strength", 0.7)
                        : normalizedStrength(body, "bg_strength", 0.7);
                String rembgModel = requestedRembgModel(body);
                String bgRemovePipeline = requestedBgRemovePipeline(body);
                JSONObject provider = null;
                boolean constrainedBgRemove = hint != null || backgroundHint != null;
                boolean forceProviderBgRemove = "model".equals(bgRemovePipeline);
                boolean forceRembg = "rembg".equals(bgRemovePipeline);
                boolean forceHeuristic = "heuristic".equals(bgRemovePipeline);
                if (!forceHeuristic && (forceProviderBgRemove || (!forceRembg && !constrainedBgRemove && rembgModel.isEmpty()))) {
                    try {
                        provider = runProviderBackgroundRemove(source, hint, backgroundHint, body);
                    } catch (Exception ex) {
                        if (forceProviderBgRemove) {
                            sendJson(out, 502, new JSONObject().put("error", "Selected image model failed: "
                                    + truncateError(ex.getMessage(), 260)));
                            return;
                        }
                        provider = null;
                    }
                }
                if (provider != null && providerResultHasMeaningfulTransparency(provider)) {
                    provider.put("source", "provider");
                    sendJson(out, 200, provider);
                    return;
                }
                if (forceProviderBgRemove) {
                    sendJson(out, 400, new JSONObject().put("error", "Selected image model did not return a transparent background."));
                    return;
                }
                edited = removeBackgroundBitmap(source, hint, backgroundHint, bgRemoveStrength, rembgModel, bgRemovePipeline);
                if (forceRembg && !lastBgRemoveSource.startsWith("onnx:")) {
                    String detail = lastBgRemoveError.isEmpty()
                            ? "Local rembg ONNX model is not available."
                            : lastBgRemoveError;
                    sendJson(out, 400, new JSONObject().put("error", detail));
                    return;
                }
            } else if ("sharpen".equals(action)) {
                JSONObject provider = null;
                try {
                    provider = runProviderSharpen(source, body);
                } catch (Exception ignored) {
                    provider = null;
                }
                if (provider != null && !provider.optString("image", "").isEmpty()) {
                    sendJson(out, 200, provider);
                    return;
                }
                edited = sharpenBitmap(source, jsonInt(body, "amount", 50));
            } else if ("denoise".equals(action)) {
                edited = denoiseBitmap(source, jsonInt(body, "amount", 35));
            } else if ("upscale-local".equals(action) || "upscale".equals(action)) {
                edited = upscaleBitmap(source, jsonInt(body, "scale", 2));
            } else {
                sendJson(out, 501, new JSONObject()
                        .put("error", "Android standalone does not support this image edit yet."));
                return;
            }
        } catch (OutOfMemoryError oom) {
            sendJson(out, 413, new JSONObject()
                    .put("error", "This image is too large for Android standalone editing. Try a smaller canvas or connect Android to the PC backend."));
            return;
        } catch (Exception ex) {
            sendJson(out, 500, new JSONObject()
                    .put("error", "Android image edit failed: " + truncateError(ex.getMessage(), 220)));
            return;
        }

        JSONObject response = new JSONObject()
                .put("image", encodeBitmapPng(edited))
                .put("width", edited.getWidth())
                .put("height", edited.getHeight());
        if (!lastBgRemoveSource.isEmpty()) response.put("source", lastBgRemoveSource);
        if (!lastBgRemoveError.isEmpty()) response.put("warning", lastBgRemoveError);
        sendJson(out, 200, response);
    }

    private Bitmap decodeJsonBitmap(JSONObject body) {
        return decodeJsonBitmap(body, "image");
    }

    private Bitmap decodeJsonBitmap(JSONObject body, String key) {
        try {
            String raw = jsonString(body, key, "").trim();
            if (raw.isEmpty()) return null;
            int comma = raw.indexOf(',');
            if (comma >= 0) raw = raw.substring(comma + 1);
            byte[] bytes = Base64.decode(raw, Base64.DEFAULT);
            Bitmap decoded = BitmapFactory.decodeByteArray(bytes, 0, bytes.length);
            return decoded == null ? null : decoded.copy(Bitmap.Config.ARGB_8888, true);
        } catch (Exception ignored) {
            return null;
        }
    }

    private Bitmap loadGalleryBitmap(JSONObject image) {
        try {
            String filename = image == null ? "" : image.optString("filename", "");
            if (filename.isEmpty()) return null;
            File file = new File(galleryDir(), filename);
            if (!file.exists() || !file.isFile()) return null;
            Bitmap decoded = BitmapFactory.decodeFile(file.getAbsolutePath());
            return decoded == null ? null : decoded.copy(Bitmap.Config.ARGB_8888, true);
        } catch (Exception ignored) {
            return null;
        }
    }

    private String encodeBitmapPng(Bitmap bitmap) throws IOException {
        ByteArrayOutputStream baos = new ByteArrayOutputStream();
        bitmap.compress(Bitmap.CompressFormat.PNG, 100, baos);
        return Base64.encodeToString(baos.toByteArray(), Base64.NO_WRAP);
    }

    private byte[] encodeBitmapPngBytes(Bitmap bitmap) throws IOException {
        ByteArrayOutputStream baos = new ByteArrayOutputStream();
        bitmap.compress(Bitmap.CompressFormat.PNG, 100, baos);
        return baos.toByteArray();
    }

    private JSONObject runProviderInpaint(Bitmap source, Bitmap mask, JSONObject body) throws Exception {
        JSONObject choice = resolveImageEndpoint(body);
        if (choice == null) return null;
        String prompt = jsonString(body, "prompt", "").trim();
        prompt = detailedInpaintPrompt(prompt);
        if (isOpenAICompatibleImageEditBase(choice.optString("base_url"))) {
            return postOpenAiImageEdit(choice, source, mask, prompt, true, null);
        }
        JSONObject payload = new JSONObject()
                .put("image", encodeBitmapPng(source))
                .put("mask", encodeBitmapPng(ensureBitmapSize(mask, source.getWidth(), source.getHeight())))
                .put("prompt", prompt)
                .put("width", source.getWidth())
                .put("height", source.getHeight());
        if (!choice.optString("model", "").isEmpty()) payload.put("model", choice.optString("model"));
        return postJsonImageEndpoint(choice, new String[]{"/images/inpaint", "/images/edits", "/images/edit", "/api/image/inpaint"}, payload, 240000);
    }

    private String detailedInpaintPrompt(String userPrompt) {
        String request = valueOr(userPrompt, "").trim();
        if (request.isEmpty()) request = "Fill the masked area naturally.";
        return "Use the provided image as the source of truth. Edit only the masked area. "
                + "Look at the surrounding pixels and continue the same subject, perspective, lighting, shadows, colors, texture, grain, and fine detail. "
                + "Blend the boundary cleanly with no black fill, no flat paint, no obvious patch, and no changes outside the mask. "
                + "User edit request: " + request;
    }

    private JSONObject runProviderBackgroundRemove(Bitmap source, Bitmap hint, Bitmap backgroundHint, JSONObject body) throws Exception {
        JSONObject choice = resolveImageEndpoint(body);
        if (choice == null) return null;
        String model = choice.optString("model", "").trim();
        boolean openAiHosted = isOpenAIBase(choice.optString("base_url"));
        boolean openAiCompatible = isOpenAICompatibleImageEditBase(choice.optString("base_url"));
        if (openAiCompatible || isImageEditModel(model)) {
            Bitmap fullMask = solidMask(source.getWidth(), source.getHeight(), 255);
            String prompt = jsonString(body, "prompt", "").trim();
            if (prompt.isEmpty()) {
                prompt = "Remove the background from this image. Preserve the foreground subject exactly, including people, faces, bodies, hair strands, clothing edges, clean edges, and return a transparent PNG.";
            }
            Bitmap openAiSource = source;
            if (backgroundHint != null) {
                prompt += " Cyan strokes on the image mark background samples. Remove the connected background regions matching those cyan-marked pixels. Preserve any person, face, body, hair, and fine hair edges unless the cyan mark directly crosses them; do not keep or redraw the cyan guide marks.";
                openAiSource = overlayBackgroundHint(source, backgroundHint);
            }
            try {
                return postOpenAiImageEdit(choice, openAiSource, fullMask, prompt, false, hint);
            } catch (Exception ex) {
                if (openAiHosted) throw ex;
                Log.w(TAG, "OpenAI-style background remove route failed", ex);
                throw ex;
            }
        }
        JSONObject payload = new JSONObject().put("image", encodeBitmapPng(source));
        if (hint != null) payload.put("hint_mask", encodeBitmapPng(ensureBitmapSize(hint, source.getWidth(), source.getHeight())));
        if (backgroundHint != null) payload.put("background_mask", encodeBitmapPng(ensureBitmapSize(backgroundHint, source.getWidth(), source.getHeight())));
        if (body.has("strength") || body.has("bg_strength")) {
            payload.put("strength", body.has("strength")
                    ? normalizedStrength(body, "strength", 0.7)
                    : normalizedStrength(body, "bg_strength", 0.7));
        }
        if (!choice.optString("model", "").isEmpty()) payload.put("model", choice.optString("model"));
        return postJsonImageEndpoint(choice, new String[]{"/images/remove-bg", "/images/background-remove", "/images/rembg"}, payload, 180000);
    }

    private JSONObject runProviderSharpen(Bitmap source, JSONObject body) throws Exception {
        JSONObject choice = resolveImageEndpoint(body);
        if (choice == null) return null;
        int amount = jsonInt(body, "amount", 50);
        if (isOpenAIBase(choice.optString("base_url"))) {
            Bitmap fullMask = solidMask(source.getWidth(), source.getHeight(), 255);
            String prompt = "Sharpen and clarify this image while preserving the exact composition, colors, text, and subject identity. "
                    + "Remove blur and improve fine detail without adding new objects. Strength: " + amount + "%.";
            return postOpenAiImageEdit(choice, source, fullMask, prompt, false, null);
        }
        JSONObject payload = new JSONObject()
                .put("image", encodeBitmapPng(source))
                .put("amount", amount);
        if (!choice.optString("model", "").isEmpty()) payload.put("model", choice.optString("model"));
        return postJsonImageEndpoint(choice, new String[]{"/images/sharpen", "/images/enhance", "/images/img2img"}, payload, 180000);
    }

    private JSONObject resolveImageEndpoint(JSONObject body) throws Exception {
        String requestedEndpointId = jsonString(body, "_endpoint_id", "").trim();
        String requestedBase = jsonString(body, "_endpoint", "").trim();
        String requestedModel = jsonString(body, "_model", "").trim();
        JSONObject ep;
        if (!requestedEndpointId.isEmpty()) {
            ep = findEndpointForId(requestedEndpointId);
            if (ep == null || !ep.optBoolean("is_enabled", true) || !looksLikeImageEndpoint(ep)) {
                throw new IOException("Choose a registered image endpoint");
            }
        } else if (!requestedBase.isEmpty()) {
            ep = findEndpointForBase(requestedBase);
            if (ep == null) {
                ep = new JSONObject()
                        .put("id", "")
                        .put("name", hostLabel(requestedBase))
                        .put("base_url", normalizeBase(requestedBase))
                        .put("api_key", "")
                        .put("model_type", "image")
                        .put("models", new JSONArray());
            }
        } else {
            ep = firstEnabledImageEndpoint();
        }
        if (ep == null) return null;
        String model = requestedModel.isEmpty() ? firstImageEditModel(ep) : requestedModel;
        String base = normalizeBase(ep.optString("base_url"));
        if (isOpenAIBase(base) && model.isEmpty()) model = "gpt-image-1";
        return new JSONObject(ep.toString())
                .put("base_url", base)
                .put("model", model);
    }

    private boolean hasExplicitImageProvider(JSONObject body) {
        String model = jsonString(body, "_model", "").trim();
        return !jsonString(body, "_endpoint_id", "").trim().isEmpty()
                || !jsonString(body, "_endpoint", "").trim().isEmpty()
                || (!model.isEmpty() && !isKnownRembgModel(model));
    }

    private JSONObject firstEnabledImageEndpoint() throws Exception {
        JSONArray endpoints = loadArray(PREF_ENDPOINTS);
        for (int i = 0; i < endpoints.length(); i++) {
            JSONObject ep = endpoints.optJSONObject(i);
            if (ep == null || !ep.optBoolean("is_enabled", true)) continue;
            if (looksLikeImageEndpoint(ep)) return ep;
        }
        return null;
    }

    private JSONObject findEndpointForBase(String baseUrl) throws Exception {
        String target = comparableBase(baseUrl);
        JSONArray endpoints = loadArray(PREF_ENDPOINTS);
        for (int i = 0; i < endpoints.length(); i++) {
            JSONObject ep = endpoints.optJSONObject(i);
            if (ep != null && target.equals(comparableBase(ep.optString("base_url")))) return ep;
        }
        return null;
    }

    private JSONObject findEndpointForId(String endpointId) throws Exception {
        String target = valueOr(endpointId, "").trim();
        if (target.isEmpty()) return null;
        JSONArray endpoints = loadArray(PREF_ENDPOINTS);
        for (int i = 0; i < endpoints.length(); i++) {
            JSONObject ep = endpoints.optJSONObject(i);
            if (ep != null && target.equals(ep.optString("id", ""))) return ep;
        }
        return null;
    }

    private boolean looksLikeImageEndpoint(JSONObject ep) {
        String type = ep.optString("model_type", "").toLowerCase(Locale.US);
        if ("image".equals(type)) return true;
        String base = ep.optString("base_url", "").toLowerCase(Locale.US);
        if (base.contains("api.openai.com")) return true;
        String name = ep.optString("name", "").toLowerCase(Locale.US);
        if (name.contains("image") || name.contains("diffusion") || name.contains("inpaint") || name.contains("flux") || name.contains("sdxl") || name.contains("kontext")) return true;
        JSONArray models = ep.optJSONArray("models");
        if (models != null) {
            for (int i = 0; i < models.length(); i++) {
                if (isImageEditModel(models.optString(i, ""))) return true;
            }
        }
        return false;
    }

    private String firstImageEditModel(JSONObject ep) {
        JSONArray models = ep.optJSONArray("models");
        if (models != null) {
            for (int i = 0; i < models.length(); i++) {
                String model = models.optString(i, "").trim();
                if (isImageEditModel(model)) return model;
            }
            if ("image".equals(ep.optString("model_type", "").toLowerCase(Locale.US)) && models.length() > 0) {
                return models.optString(0, "").trim();
            }
        }
        if (isOpenAIBase(ep.optString("base_url"))) return "gpt-image-1";
        return "";
    }

    private boolean isImageEditModel(String model) {
        String m = valueOr(model, "").toLowerCase(Locale.US);
        if (m.contains("dall-e-3")) return false;
        boolean editCue = m.contains("edit")
                || m.contains("inpaint")
                || m.contains("outpaint")
                || m.contains("fill")
                || m.contains("kontext")
                || m.contains("img2img")
                || m.contains("image-to-image")
                || m.contains("image2image")
                || m.contains("i2i")
                || m.contains("mask")
                || m.contains("paint-by-example")
                || m.contains("pix2pix")
                || m.contains("variation");
        return m.contains("gpt-image")
                || m.contains("chatgpt-image")
                || m.contains("dall-e-2")
                || (m.contains("qwen") && m.contains("image")
                    && editCue)
                || (m.contains("seedream")
                    && editCue)
                || editCue;
    }

    private boolean isOpenAIBase(String baseUrl) {
        return valueOr(baseUrl, "").toLowerCase(Locale.US).contains("api.openai.com");
    }

    private boolean isOpenAICompatibleImageEditBase(String baseUrl) {
        String base = valueOr(baseUrl, "").toLowerCase(Locale.US);
        return isOpenAIBase(base)
                || base.contains("compatible-mode")
                || base.contains("dashscope")
                || base.contains("aliyuncs.com");
    }

    private String comparableBase(String raw) {
        String base = normalizeBase(raw).trim();
        while (base.endsWith("/")) base = base.substring(0, base.length() - 1);
        if (base.endsWith("/v1")) base = base.substring(0, base.length() - 3);
        return base.toLowerCase(Locale.US);
    }

    private JSONObject postJsonImageEndpoint(JSONObject choice, String[] paths, JSONObject payload, int readTimeoutMs) throws Exception {
        String base = normalizeBase(choice.optString("base_url"));
        while (base.endsWith("/")) base = base.substring(0, base.length() - 1);
        String baseRoot = base.endsWith("/v1") ? base.substring(0, base.length() - 3) : base;
        while (baseRoot.endsWith("/")) baseRoot = baseRoot.substring(0, baseRoot.length() - 1);
        if (!base.endsWith("/v1")) base += "/v1";
        String apiKey = choice.optString("api_key", "");
        IOException last = null;
        for (String path : paths) {
            HttpURLConnection conn = null;
            try {
                String target = path.startsWith("/api/") ? baseRoot + path : base + path;
                conn = (HttpURLConnection) new URL(target).openConnection();
                conn.setRequestMethod("POST");
                conn.setConnectTimeout(15000);
                conn.setReadTimeout(readTimeoutMs);
                conn.setDoOutput(true);
                conn.setRequestProperty("Content-Type", "application/json");
                conn.setRequestProperty("Accept", "application/json");
                if (!apiKey.isEmpty()) conn.setRequestProperty("Authorization", "Bearer " + apiKey);
                byte[] data = payload.toString().getBytes(StandardCharsets.UTF_8);
                conn.setFixedLengthStreamingMode(data.length);
                try (OutputStream body = conn.getOutputStream()) {
                    body.write(data);
                }
                int status = conn.getResponseCode();
                String response = readAll(status >= 400 ? conn.getErrorStream() : conn.getInputStream());
                if (status < 200 || status >= 300) {
                    last = new IOException(path + ": " + formatProviderError(status, response));
                    continue;
                }
                JSONObject normalized = normalizeImageResponse(response);
                if (!normalized.optString("image", "").isEmpty()) return normalized;
                last = new IOException(path + ": " + providerNoImageDetail(response));
            } finally {
                if (conn != null) conn.disconnect();
            }
        }
        throw last == null ? new IOException("No compatible image edit route found") : last;
    }

    private JSONObject postOpenAiImageEdit(JSONObject choice, Bitmap source, Bitmap editMask, String prompt,
                                          boolean compositeToMask, Bitmap alphaHint) throws Exception {
        String base = normalizeBase(choice.optString("base_url"));
        while (base.endsWith("/")) base = base.substring(0, base.length() - 1);
        if (!base.endsWith("/v1")) base += "/v1";
        String apiKey = choice.optString("api_key", "");
        if (apiKey.isEmpty() && isOpenAIBase(base)) throw new IOException("OpenAI endpoint has no API key stored in Settings.");
        boolean openAiHosted = isOpenAIBase(base);
        String model = choice.optString("model", "").trim();
        if (model.isEmpty()) {
            if (openAiHosted) {
                model = "gpt-image-1";
            } else {
                throw new IOException("Select an image-edit model for this endpoint.");
            }
        }
        if (model.toLowerCase(Locale.US).contains("dall-e-3")) {
            throw new IOException("dall-e-3 does not support image edits. Pick gpt-image-1, dall-e-2, SDXL inpaint, or Flux fill.");
        }

        Bitmap src = source.getConfig() == Bitmap.Config.ARGB_8888 ? source : source.copy(Bitmap.Config.ARGB_8888, true);
        Bitmap mask = ensureBitmapSize(editMask, src.getWidth(), src.getHeight());
        Bitmap openAiMask = openAiEditMask(mask, src.getWidth(), src.getHeight());
        String boundary = "----OdysseusAndroid" + UUID.randomUUID().toString().replace("-", "");

        HttpURLConnection conn = (HttpURLConnection) new URL(base + "/images/edits").openConnection();
        conn.setRequestMethod("POST");
        conn.setConnectTimeout(15000);
        conn.setReadTimeout(240000);
        conn.setDoOutput(true);
        conn.setRequestProperty("Accept", "application/json");
        if (!apiKey.isEmpty()) conn.setRequestProperty("Authorization", "Bearer " + apiKey);
        conn.setRequestProperty("Content-Type", "multipart/form-data; boundary=" + boundary);
        conn.setChunkedStreamingMode(0);
        try (OutputStream os = conn.getOutputStream()) {
            writeMultipartField(os, boundary, "model", model);
            writeMultipartField(os, boundary, "prompt", prompt);
            writeMultipartField(os, boundary, "size", openAiEditSize(src.getWidth(), src.getHeight()));
            writeMultipartField(os, boundary, "n", "1");
            writeMultipartFile(os, boundary, "image", "source.png", "image/png", encodeBitmapPngBytes(src));
            writeMultipartFile(os, boundary, "mask", "mask.png", "image/png", encodeBitmapPngBytes(openAiMask));
            os.write(("--" + boundary + "--\r\n").getBytes(StandardCharsets.UTF_8));
        }
        int status = conn.getResponseCode();
        String response = readAll(status >= 400 ? conn.getErrorStream() : conn.getInputStream());
        conn.disconnect();
        if (status < 200 || status >= 300) {
            if (!openAiHosted) {
                boolean likelyJsonOnly = shouldRetryJsonImageEdit(status, response);
                String previousError = (likelyJsonOnly ? "" : "Multipart /v1/images/edits failed: "
                        + formatProviderError(status, response) + ". ");
                try {
                    return postJsonOpenAiImageEdit(choice, src, openAiMask, mask, prompt, compositeToMask, alphaHint);
                } catch (Exception jsonEx) {
                    Log.w(TAG, "JSON image edit retry failed; trying chat-completions image edit", jsonEx);
                    return postChatImageEdit(choice, src, mask, prompt, compositeToMask, alphaHint, previousError + jsonEx.getMessage());
                }
            }
            throw new IOException("Image edit failed at /v1/images/edits: " + formatProviderError(status, response));
        }

        try {
            return finishImageEditResponse(response, src, mask, compositeToMask, alphaHint, "OpenAI returned no image");
        } catch (Exception ex) {
            if (!openAiHosted) {
                Log.w(TAG, "Multipart image edit returned no image; trying chat-completions image edit", ex);
                return postChatImageEdit(choice, src, mask, prompt, compositeToMask, alphaHint, ex.getMessage());
            }
            throw ex;
        }
    }

    private JSONObject postJsonOpenAiImageEdit(JSONObject choice, Bitmap src, Bitmap openAiMask, Bitmap editMask,
                                              String prompt, boolean compositeToMask, Bitmap alphaHint) throws Exception {
        String base = normalizeBase(choice.optString("base_url"));
        while (base.endsWith("/")) base = base.substring(0, base.length() - 1);
        if (!base.endsWith("/v1")) base += "/v1";
        String apiKey = choice.optString("api_key", "");
        String model = choice.optString("model", "gpt-image-1").trim();
        if (model.isEmpty()) model = "gpt-image-1";
        String imageB64 = encodeBitmapPng(src);
        String maskB64 = encodeBitmapPng(openAiMask);

        JSONObject payload = new JSONObject()
                .put("model", model)
                .put("prompt", prompt)
                .put("image", imageB64)
                .put("mask_image", maskB64)
                .put("size", openAiEditSize(src.getWidth(), src.getHeight()))
                .put("n", 1)
                .put("response_format", "b64_json")
                .put("output_format", "png");

        HttpURLConnection conn = (HttpURLConnection) new URL(base + "/images/edits").openConnection();
        conn.setRequestMethod("POST");
        conn.setConnectTimeout(15000);
        conn.setReadTimeout(240000);
        conn.setDoOutput(true);
        conn.setRequestProperty("Content-Type", "application/json");
        conn.setRequestProperty("Accept", "application/json");
        if (!apiKey.isEmpty()) conn.setRequestProperty("Authorization", "Bearer " + apiKey);
        byte[] data = payload.toString().getBytes(StandardCharsets.UTF_8);
        conn.setFixedLengthStreamingMode(data.length);
        try (OutputStream body = conn.getOutputStream()) {
            body.write(data);
        }
        int status = conn.getResponseCode();
        String response = readAll(status >= 400 ? conn.getErrorStream() : conn.getInputStream());
        conn.disconnect();
        if (status < 200 || status >= 300) {
            throw new IOException("Image edit JSON retry failed at /v1/images/edits: " + formatProviderError(status, response));
        }

        return finishImageEditResponse(response, src, editMask, compositeToMask, alphaHint, "Image edit JSON retry returned no image");
    }

    private JSONObject postChatImageEdit(JSONObject choice, Bitmap src, Bitmap editMask, String prompt,
                                         boolean compositeToMask, Bitmap alphaHint, String previousError) throws Exception {
        String base = normalizeBase(choice.optString("base_url"));
        while (base.endsWith("/")) base = base.substring(0, base.length() - 1);
        if (!base.endsWith("/v1")) base += "/v1";
        String apiKey = choice.optString("api_key", "");
        String model = choice.optString("model", "").trim();

        String chatPrompt = valueOr(prompt, "").trim();
        chatPrompt += "\n\nUse the first image as the source. The second image is a mask: white pixels mark the area to edit, black pixels should stay unchanged.";
        JSONArray content = new JSONArray()
                .put(new JSONObject()
                        .put("type", "text")
                        .put("text", chatPrompt))
                .put(new JSONObject()
                        .put("type", "image_url")
                        .put("image_url", new JSONObject()
                                .put("url", "data:image/png;base64," + encodeBitmapPng(src))))
                .put(new JSONObject()
                        .put("type", "image_url")
                        .put("image_url", new JSONObject()
                                .put("url", "data:image/png;base64," + encodeBitmapPng(editMask))));
        JSONObject payload = new JSONObject()
                .put("messages", new JSONArray()
                        .put(new JSONObject()
                                .put("role", "user")
                                .put("content", content)))
                .put("stream", false)
                .put("extra_body", new JSONObject()
                        .put("num_inference_steps", 50)
                        .put("guidance_scale", 1)
                        .put("size", openAiEditSize(src.getWidth(), src.getHeight()))
                        .put("output_format", "png"));
        if (!model.isEmpty()) payload.put("model", model);

        HttpURLConnection conn = (HttpURLConnection) new URL(base + "/chat/completions").openConnection();
        conn.setRequestMethod("POST");
        conn.setConnectTimeout(15000);
        conn.setReadTimeout(240000);
        conn.setDoOutput(true);
        conn.setRequestProperty("Content-Type", "application/json");
        conn.setRequestProperty("Accept", "application/json");
        if (!apiKey.isEmpty()) conn.setRequestProperty("Authorization", "Bearer " + apiKey);
        byte[] data = payload.toString().getBytes(StandardCharsets.UTF_8);
        conn.setFixedLengthStreamingMode(data.length);
        try (OutputStream body = conn.getOutputStream()) {
            body.write(data);
        }
        int status = conn.getResponseCode();
        String response = readAll(status >= 400 ? conn.getErrorStream() : conn.getInputStream());
        conn.disconnect();
        if (status < 200 || status >= 300) {
            String prefix = valueOr(previousError, "").isEmpty() ? "" : " Previous /v1/images/edits error: " + truncateError(previousError, 160);
            throw new IOException("Image edit chat fallback failed at /v1/chat/completions: " + formatProviderError(status, response) + prefix);
        }
        try {
            return finishImageEditResponse(response, src, editMask, compositeToMask, alphaHint, "Image edit chat fallback returned no image");
        } catch (Exception ex) {
            String prefix = valueOr(previousError, "").isEmpty() ? "" : " Previous /v1/images/edits error: " + truncateError(previousError, 160);
            throw new IOException(ex.getMessage() + prefix);
        }
    }

    private JSONObject finishImageEditResponse(String response, Bitmap src, Bitmap mask, boolean compositeToMask,
                                               Bitmap alphaHint, String noImageMessage) throws Exception {
        JSONObject normalized;
        try {
            normalized = normalizeImageResponse(response);
        } catch (Exception parseEx) {
            throw new IOException(noImageMessage + ": invalid JSON response: " + truncateError(response, 220));
        }
        Bitmap generated = decodeBase64Bitmap(normalized.optString("image", ""));
        if (generated == null) {
            String detail = providerNoImageDetail(response);
            throw new IOException(noImageMessage + (detail.isEmpty() ? "" : ": " + detail));
        }
        generated = ensureBitmapSize(generated, src.getWidth(), src.getHeight());
        Bitmap result = compositeToMask ? compositeWithMask(src, generated, mask) : generated;
        if (alphaHint != null) result = applyAlphaHint(result, ensureBitmapSize(alphaHint, result.getWidth(), result.getHeight()));
        return new JSONObject()
                .put("image", encodeBitmapPng(result))
                .put("width", result.getWidth())
                .put("height", result.getHeight())
                .put("method", "image-edit");
    }

    private boolean shouldRetryJsonImageEdit(int status, String response) {
        String text = valueOr(response, "").toLowerCase(Locale.US);
        return status == 415
                || (text.contains("unsupported media type") && text.contains("application/json"))
                || text.contains("post requests must use 'application/json'")
                || text.contains("post requests must use application/json");
    }

    private JSONObject normalizeImageResponse(String response) throws Exception {
        JSONObject json = new JSONObject(valueOr(response, "{}"));
        String image = firstProviderImageValue(json, 0);
        if (!image.isEmpty()) return new JSONObject().put("image", providerImageValueToBase64(image));
        return new JSONObject();
    }

    private String firstProviderImageValue(Object node, int depth) {
        if (node == null || node == JSONObject.NULL || depth > 5) return "";
        if (node instanceof String) {
            String value = ((String) node).trim();
            if (looksLikeProviderImageValue(value)) return value;
            String embedded = providerImageValueFromText(value);
            if (!embedded.isEmpty()) return embedded;
            if ((value.startsWith("{") && value.endsWith("}")) || (value.startsWith("[") && value.endsWith("]"))) {
                try {
                    Object parsed = value.startsWith("{") ? new JSONObject(value) : new JSONArray(value);
                    return firstProviderImageValue(parsed, depth + 1);
                } catch (Exception ignored) {
                }
            }
            return "";
        }
        if (node instanceof JSONArray) {
            JSONArray arr = (JSONArray) node;
            for (int i = 0; i < arr.length(); i++) {
                String found = firstProviderImageValue(arr.opt(i), depth + 1);
                if (!found.isEmpty()) return found;
            }
            return "";
        }
        if (!(node instanceof JSONObject)) return "";

        JSONObject json = (JSONObject) node;
        String[] preferred = new String[]{
                "image", "b64_json", "base64", "image_base64", "imageBase64",
                "url", "image_url", "imageUrl", "data", "images",
                "content", "message", "choices", "output", "outputs", "result", "results", "artifact", "artifacts"
        };
        for (String key : preferred) {
            if (!json.has(key) || json.isNull(key)) continue;
            String found = firstProviderImageValue(json.opt(key), depth + 1);
            if (!found.isEmpty()) return found;
        }
        java.util.Iterator<String> keys = json.keys();
        while (keys.hasNext()) {
            String key = keys.next();
            String found = firstProviderImageValue(json.opt(key), depth + 1);
            if (!found.isEmpty()) return found;
        }
        return "";
    }

    private String providerImageValueFromText(String raw) {
        String text = valueOr(raw, "");
        java.util.regex.Matcher dataUri = java.util.regex.Pattern
                .compile("data:image/(?:png|jpe?g|webp);base64,[A-Za-z0-9+/=\\r\\n]+", java.util.regex.Pattern.CASE_INSENSITIVE)
                .matcher(text);
        if (dataUri.find()) return dataUri.group();
        java.util.regex.Matcher jsonImage = java.util.regex.Pattern
                .compile("\\\"(?:b64_json|image|base64|image_base64|url)\\\"\\s*:\\s*\\\"([^\\\"]{128,})\\\"", java.util.regex.Pattern.CASE_INSENSITIVE)
                .matcher(text);
        if (jsonImage.find()) {
            String candidate = jsonImage.group(1)
                    .replace("\\/", "/")
                    .replace("\\n", "")
                    .replace("\\r", "");
            return looksLikeProviderImageValue(candidate) ? candidate : "";
        }
        return "";
    }

    private boolean looksLikeProviderImageValue(String raw) {
        String value = valueOr(raw, "").trim();
        if (value.isEmpty()) return false;
        String lower = value.toLowerCase(Locale.US);
        if (lower.startsWith("data:image/")) return true;
        if (lower.startsWith("http://") || lower.startsWith("https://")) return true;
        String b64 = stripDataUrl(value);
        if (b64.startsWith("iVBOR") || b64.startsWith("/9j/") || b64.startsWith("UklGR")) return true;
        if (b64.length() < 128) return false;
        try {
            byte[] bytes = Base64.decode(b64, Base64.DEFAULT);
            Bitmap decoded = BitmapFactory.decodeByteArray(bytes, 0, bytes.length);
            return decoded != null;
        } catch (Exception ignored) {
            return false;
        }
    }

    private String providerImageValueToBase64(String raw) throws Exception {
        String value = valueOr(raw, "").trim();
        String lower = value.toLowerCase(Locale.US);
        if (lower.startsWith("http://") || lower.startsWith("https://")) {
            return downloadImageAsBase64(value);
        }
        return stripDataUrl(value);
    }

    private String providerNoImageDetail(String response) {
        try {
            JSONObject json = new JSONObject(valueOr(response, "{}"));
            String err = providerErrorText(json, 0);
            if (!err.isEmpty()) return err;
            StringBuilder keys = new StringBuilder();
            java.util.Iterator<String> it = json.keys();
            while (it.hasNext() && keys.length() < 180) {
                if (keys.length() > 0) keys.append(", ");
                keys.append(it.next());
            }
            return keys.length() > 0
                    ? "server returned no image (keys: " + keys + ")"
                    : "server returned no image";
        } catch (Exception ignored) {
            String body = truncateError(response, 180);
            return body.isEmpty() ? "server returned no image" : "server returned no image: " + body;
        }
    }

    private String providerErrorText(Object node, int depth) {
        if (node == null || node == JSONObject.NULL || depth > 4) return "";
        if (node instanceof String) {
            String text = valueOr((String) node, "").replace('\n', ' ').trim();
            return looksLikeProviderImageValue(text) ? "" : truncateError(text, 220);
        }
        if (node instanceof JSONArray) {
            JSONArray arr = (JSONArray) node;
            for (int i = 0; i < arr.length(); i++) {
                String found = providerErrorText(arr.opt(i), depth + 1);
                if (!found.isEmpty()) return found;
            }
            return "";
        }
        if (!(node instanceof JSONObject)) return "";
        JSONObject json = (JSONObject) node;
        for (String key : new String[]{"error", "detail", "message", "reason", "status_message"}) {
            if (!json.has(key) || json.isNull(key)) continue;
            String found = providerErrorText(json.opt(key), depth + 1);
            if (!found.isEmpty()) return found;
        }
        return "";
    }

    private String stripDataUrl(String raw) {
        String value = valueOr(raw, "").trim();
        int comma = value.indexOf(',');
        return comma >= 0 ? value.substring(comma + 1) : value;
    }

    private String downloadImageAsBase64(String imageUrl) throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(imageUrl).openConnection();
        conn.setConnectTimeout(15000);
        conn.setReadTimeout(60000);
        conn.setRequestProperty("Accept", "image/png,image/*,*/*");
        int status = conn.getResponseCode();
        if (status < 200 || status >= 300) throw new IOException("Image download failed: HTTP " + status);
        byte[] data = readBytes(conn.getInputStream());
        conn.disconnect();
        return Base64.encodeToString(data, Base64.NO_WRAP);
    }

    private void writeMultipartField(OutputStream os, String boundary, String name, String value) throws IOException {
        os.write(("--" + boundary + "\r\n").getBytes(StandardCharsets.UTF_8));
        os.write(("Content-Disposition: form-data; name=\"" + name + "\"\r\n\r\n").getBytes(StandardCharsets.UTF_8));
        os.write(valueOr(value, "").getBytes(StandardCharsets.UTF_8));
        os.write("\r\n".getBytes(StandardCharsets.UTF_8));
    }

    private void writeMultipartFile(OutputStream os, String boundary, String name, String filename,
                                    String contentType, byte[] data) throws IOException {
        os.write(("--" + boundary + "\r\n").getBytes(StandardCharsets.UTF_8));
        os.write(("Content-Disposition: form-data; name=\"" + name + "\"; filename=\"" + filename + "\"\r\n").getBytes(StandardCharsets.UTF_8));
        os.write(("Content-Type: " + contentType + "\r\n\r\n").getBytes(StandardCharsets.UTF_8));
        os.write(data);
        os.write("\r\n".getBytes(StandardCharsets.UTF_8));
    }

    private String openAiEditSize(int width, int height) {
        if (width > height * 1.15f) return "1536x1024";
        if (height > width * 1.15f) return "1024x1536";
        return "1024x1024";
    }

    private Bitmap decodeBase64Bitmap(String raw) {
        try {
            String b64 = stripDataUrl(raw);
            byte[] bytes = Base64.decode(b64, Base64.DEFAULT);
            Bitmap decoded = BitmapFactory.decodeByteArray(bytes, 0, bytes.length);
            return decoded == null ? null : decoded.copy(Bitmap.Config.ARGB_8888, true);
        } catch (Exception ignored) {
            return null;
        }
    }

    private boolean providerResultHasMeaningfulTransparency(JSONObject provider) {
        Bitmap bitmap = decodeBase64Bitmap(provider == null ? "" : provider.optString("image", ""));
        if (bitmap == null) return false;
        int w = bitmap.getWidth();
        int h = bitmap.getHeight();
        int count = w * h;
        if (count <= 0) return false;
        int[] pixels = new int[count];
        bitmap.getPixels(pixels, 0, w, 0, 0, w, h);
        int transparentish = 0;
        int minAlpha = 255;
        for (int p : pixels) {
            int alpha = (p >>> 24) & 0xff;
            if (alpha < minAlpha) minAlpha = alpha;
            if (alpha < 245) transparentish++;
        }
        return minAlpha < 245 && transparentish >= Math.max(16, count / 1000);
    }

    private Bitmap solidMask(int width, int height, int value) {
        int v = Math.max(0, Math.min(255, value));
        int color = 0xff000000 | (v << 16) | (v << 8) | v;
        Bitmap mask = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888);
        int[] pixels = new int[width * height];
        Arrays.fill(pixels, color);
        mask.setPixels(pixels, 0, width, 0, 0, width, height);
        return mask;
    }

    private Bitmap openAiEditMask(Bitmap editMask, int width, int height) {
        Bitmap mask = ensureBitmapSize(editMask, width, height);
        int[] src = new int[width * height];
        int[] dst = new int[width * height];
        mask.getPixels(src, 0, width, 0, 0, width, height);
        for (int i = 0; i < src.length; i++) {
            int regenerate = maskOpacity(src[i]);
            int keepAlpha = 255 - regenerate;
            dst[i] = (keepAlpha << 24) | 0x00ffffff;
        }
        Bitmap out = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888);
        out.setPixels(dst, 0, width, 0, 0, width, height);
        return out;
    }

    private Bitmap compositeWithMask(Bitmap original, Bitmap generated, Bitmap mask) {
        int w = original.getWidth();
        int h = original.getHeight();
        Bitmap gen = ensureBitmapSize(generated, w, h);
        Bitmap m = ensureBitmapSize(mask, w, h);
        int[] src = new int[w * h];
        int[] ai = new int[w * h];
        int[] mp = new int[w * h];
        int[] out = new int[w * h];
        original.getPixels(src, 0, w, 0, 0, w, h);
        gen.getPixels(ai, 0, w, 0, 0, w, h);
        m.getPixels(mp, 0, w, 0, 0, w, h);
        for (int i = 0; i < out.length; i++) {
            float t = maskOpacity(mp[i]) / 255f;
            out[i] = blendArgb(src[i], ai[i], t);
        }
        Bitmap result = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888);
        result.setPixels(out, 0, w, 0, 0, w, h);
        return result;
    }

    private Bitmap applyAlphaHint(Bitmap source, Bitmap hint) {
        int w = source.getWidth();
        int h = source.getHeight();
        Bitmap mask = ensureBitmapSize(hint, w, h);
        int[] src = new int[w * h];
        int[] hp = new int[w * h];
        source.getPixels(src, 0, w, 0, 0, w, h);
        mask.getPixels(hp, 0, w, 0, 0, w, h);
        for (int i = 0; i < src.length; i++) {
            int a = (src[i] >>> 24) & 0xff;
            int hintA = maskOpacity(hp[i]);
            int outA = Math.round(a * (hintA / 255f));
            src[i] = (src[i] & 0x00ffffff) | (outA << 24);
        }
        Bitmap result = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888);
        result.setPixels(src, 0, w, 0, 0, w, h);
        return result;
    }

    private Bitmap overlayBackgroundHint(Bitmap source, Bitmap backgroundHint) {
        int w = source.getWidth();
        int h = source.getHeight();
        Bitmap hint = ensureBitmapSize(backgroundHint, w, h);
        if (hint == null) return source;
        int[] src = new int[w * h];
        int[] hp = new int[w * h];
        source.getPixels(src, 0, w, 0, 0, w, h);
        hint.getPixels(hp, 0, w, 0, 0, w, h);
        for (int i = 0; i < src.length; i++) {
            if (maskOpacity(hp[i]) <= MASK_OPACITY_THRESHOLD) continue;
            src[i] = blendArgb(src[i], 0xff00d2ff, 0.82f);
        }
        Bitmap result = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888);
        result.setPixels(src, 0, w, 0, 0, w, h);
        return result;
    }

    private int blendArgb(int from, int to, float t) {
        if (t <= 0f) return from;
        if (t >= 1f) return to;
        int fa = (from >>> 24) & 0xff;
        int fr = (from >> 16) & 0xff;
        int fg = (from >> 8) & 0xff;
        int fb = from & 0xff;
        int ta = (to >>> 24) & 0xff;
        int tr = (to >> 16) & 0xff;
        int tg = (to >> 8) & 0xff;
        int tb = to & 0xff;
        int a = clampColor(fa + (ta - fa) * t);
        int r = clampColor(fr + (tr - fr) * t);
        int g = clampColor(fg + (tg - fg) * t);
        int b = clampColor(fb + (tb - fb) * t);
        return (a << 24) | (r << 16) | (g << 8) | b;
    }

    private Bitmap ensureBitmapSize(Bitmap bitmap, int width, int height) {
        if (bitmap == null) return null;
        if (bitmap.getWidth() == width && bitmap.getHeight() == height) return bitmap;
        return Bitmap.createScaledBitmap(bitmap, width, height, false);
    }

    private Bitmap runLocalRembgModel(Bitmap source, Bitmap hintMask, String requestedModel) {
        try {
            if (appContext == null || !DynamicOnnxRuntime.isInstalled(appContext)) {
                lastBgRemoveError = "ONNX Runtime Android is not installed.";
                return null;
            }
            RembgModelChoice choice = resolveLocalRembgModel(requestedModel);
            if (choice == null) {
                lastBgRemoveError = "No rembg ONNX model is available.";
                return null;
            }
            File model = cachedModelFile(choice.assetName, choice.url, choice.filename);
            if (model == null || !model.exists() || model.length() <= 0) {
                lastBgRemoveError = "Could not load rembg model file: " + choice.model;
                return null;
            }

            int inputSize = Math.max(64, choice.inputSize);
            Bitmap src = source.getConfig() == Bitmap.Config.ARGB_8888
                    ? source
                    : source.copy(Bitmap.Config.ARGB_8888, false);
            Bitmap scaled = Bitmap.createScaledBitmap(src, inputSize, inputSize, true);
            int[] scaledPixels = new int[inputSize * inputSize];
            scaled.getPixels(scaledPixels, 0, inputSize, 0, 0, inputSize, inputSize);

            float[] input = new float[3 * inputSize * inputSize];
            int plane = inputSize * inputSize;
            for (int i = 0; i < scaledPixels.length; i++) {
                int p = scaledPixels[i];
                float r = ((p >> 16) & 0xff) / 255f;
                float g = ((p >> 8) & 0xff) / 255f;
                float b = (p & 0xff) / 255f;
                input[i] = (r - 0.485f) / 0.229f;
                input[plane + i] = (g - 0.456f) / 0.224f;
                input[plane * 2 + i] = (b - 0.406f) / 0.225f;
            }

            float[] mask;
            synchronized (rembgSessionLock) {
                if (rembgRuntime == null) rembgRuntime = new DynamicOnnxRuntime();
                mask = rembgRuntime.runMask(appContext, model, input, inputSize);
            }
            if (mask == null) {
                lastBgRemoveError = "ONNX model returned no mask: " + choice.model;
                return null;
            }

            Bitmap alphaMask = softMaskBitmap(mask, inputSize, inputSize);
            Bitmap fullMask = ensureBitmapSize(alphaMask, source.getWidth(), source.getHeight());
            int w = source.getWidth();
            int h = source.getHeight();
            int[] srcPixels = new int[w * h];
            int[] maskPixels = new int[w * h];
            source.getPixels(srcPixels, 0, w, 0, 0, w, h);
            fullMask.getPixels(maskPixels, 0, w, 0, 0, w, h);
            int transparentish = 0;
            for (int i = 0; i < srcPixels.length; i++) {
                int srcAlpha = (srcPixels[i] >>> 24) & 0xff;
                int matte = maskOpacity(maskPixels[i]);
                if (matte < 245) transparentish++;
                srcPixels[i] = (srcPixels[i] & 0x00ffffff) | (Math.min(srcAlpha, matte) << 24);
            }
            if (transparentish < Math.max(16, srcPixels.length / 1000)) {
                lastBgRemoveError = "ONNX model returned an opaque mask: " + choice.model;
                return null;
            }
            Bitmap result = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888);
            result.setPixels(srcPixels, 0, w, 0, 0, w, h);
            if (hintMask != null) result = applyAlphaHint(result, hintMask);
            lastBgRemoveSource = "onnx:" + choice.model;
            lastBgRemoveError = "";
            return result;
        } catch (Throwable ex) {
            lastBgRemoveError = "ONNX background removal failed: " + truncateError(ex.getMessage(), 180);
            Log.w(TAG, lastBgRemoveError, ex);
            return null;
        }
    }

    private static class RembgModelChoice {
        final String model;
        final String filename;
        final String url;
        final String assetName;
        final long expectedBytes;
        final int inputSize;

        RembgModelChoice(String model, String filename, String url, String assetName, long expectedBytes, int inputSize) {
            this.model = model;
            this.filename = filename;
            this.url = url;
            this.assetName = assetName;
            this.expectedBytes = expectedBytes;
            this.inputSize = inputSize;
        }
    }

    private RembgModelChoice resolveLocalRembgModel(String requestedModel) {
        String requested = valueOr(requestedModel, "").trim();
        if (ISNET_REMBG_MODEL.equals(requested)) {
            return new RembgModelChoice(ISNET_REMBG_MODEL, ISNET_REMBG_FILENAME, ISNET_REMBG_MODEL_URL, "", ISNET_REMBG_EXPECTED_BYTES, 1024);
        }
        if (SILUETA_REMBG_MODEL.equals(requested)) {
            return new RembgModelChoice(SILUETA_REMBG_MODEL, SILUETA_REMBG_FILENAME, SILUETA_REMBG_MODEL_URL, "", SILUETA_REMBG_EXPECTED_BYTES, 320);
        }
        if (U2NETP_REMBG_MODEL.equals(requested)) {
            return new RembgModelChoice(U2NETP_REMBG_MODEL, U2NETP_REMBG_FILENAME, LOCAL_REMBG_MODEL_URL, LOCAL_REMBG_MODEL_ASSET, 0L, 320);
        }
        if (installedRembgModel(ISNET_REMBG_FILENAME, ISNET_REMBG_EXPECTED_BYTES)) {
            return new RembgModelChoice(ISNET_REMBG_MODEL, ISNET_REMBG_FILENAME, ISNET_REMBG_MODEL_URL, "", ISNET_REMBG_EXPECTED_BYTES, 1024);
        }
        if (installedRembgModel(SILUETA_REMBG_FILENAME, SILUETA_REMBG_EXPECTED_BYTES)) {
            return new RembgModelChoice(SILUETA_REMBG_MODEL, SILUETA_REMBG_FILENAME, SILUETA_REMBG_MODEL_URL, "", SILUETA_REMBG_EXPECTED_BYTES, 320);
        }
        return new RembgModelChoice(U2NETP_REMBG_MODEL, U2NETP_REMBG_FILENAME, LOCAL_REMBG_MODEL_URL, LOCAL_REMBG_MODEL_ASSET, 0L, 320);
    }

    private boolean installedRembgModel(String filename, long expectedBytes) {
        File file = new File(new File(appContext.getFilesDir(), "models"), filename);
        return file.exists()
                && file.isFile()
                && file.length() >= Math.max(1L, expectedBytes / 2L);
    }

    private File cachedModelFile(String assetName, String modelUrl, String filename) throws IOException {
        File dir = new File(appContext.getFilesDir(), "models");
        if (!dir.exists() && !dir.mkdirs()) return null;
        File out = new File(dir, filename);
        if (out.exists() && out.length() > 0) return out;
        if (!valueOr(assetName, "").isEmpty() && copyAssetIfAvailable(assetName, out)) return out;
        return downloadModelFile(modelUrl, out) ? out : null;
    }

    private boolean copyAssetIfAvailable(String assetName, File out) {
        AssetManager assets = appContext.getAssets();
        try (InputStream input = assets.open(assetName);
             FileOutputStream output = new FileOutputStream(out)) {
            byte[] buf = new byte[8192];
            int n;
            while ((n = input.read(buf)) >= 0) {
                output.write(buf, 0, n);
            }
            return out.exists() && out.length() > 0;
        } catch (Exception ignored) {
            if (out.exists() && out.length() == 0) {
                try {
                    //noinspection ResultOfMethodCallIgnored
                    out.delete();
                } catch (Exception ignoredDelete) {
                }
            }
            return false;
        }
    }

    private boolean downloadModelFile(String modelUrl, File out) {
        File tmp = new File(out.getParentFile(), out.getName() + ".download");
        HttpURLConnection conn = null;
        try {
            conn = (HttpURLConnection) new URL(modelUrl).openConnection();
            conn.setConnectTimeout(20000);
            conn.setReadTimeout(300000);
            conn.setRequestProperty("Accept", "application/octet-stream,*/*");
            int status = conn.getResponseCode();
            if (status < 200 || status >= 300) return false;
            try (InputStream input = conn.getInputStream();
                 FileOutputStream output = new FileOutputStream(tmp)) {
                byte[] buf = new byte[32768];
                int n;
                while ((n = input.read(buf)) >= 0) {
                    output.write(buf, 0, n);
                }
            }
            if (!tmp.exists() || tmp.length() <= 0) return false;
            if (out.exists() && !out.delete()) return false;
            return tmp.renameTo(out);
        } catch (Exception ignored) {
            return false;
        } finally {
            if (conn != null) conn.disconnect();
            if (tmp.exists()) {
                //noinspection ResultOfMethodCallIgnored
                tmp.delete();
            }
        }
    }

    private Bitmap softMaskBitmap(float[] values, int width, int height) {
        float min = Float.POSITIVE_INFINITY;
        float max = Float.NEGATIVE_INFINITY;
        for (float v : values) {
            if (Float.isNaN(v) || Float.isInfinite(v)) continue;
            if (v < min) min = v;
            if (v > max) max = v;
        }
        float range = Math.max(0.0001f, max - min);
        int[] pixels = new int[width * height];
        for (int i = 0; i < values.length; i++) {
            float v = values[i];
            if (Float.isNaN(v) || Float.isInfinite(v)) v = min;
            int a = clampColor(((v - min) / range) * 255f);
            pixels[i] = 0xff000000 | (a << 16) | (a << 8) | a;
        }
        Bitmap out = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888);
        out.setPixels(pixels, 0, width, 0, 0, width, height);
        return out;
    }

    private Bitmap removeBackgroundBitmap(Bitmap source, Bitmap hintMask, Bitmap backgroundHint, double strength) {
        return removeBackgroundBitmap(source, hintMask, backgroundHint, strength, "");
    }

    private Bitmap removeBackgroundBitmap(Bitmap source, Bitmap hintMask, Bitmap backgroundHint, double strength, String requestedModel) {
        return removeBackgroundBitmap(source, hintMask, backgroundHint, strength, requestedModel, "auto");
    }

    private Bitmap removeBackgroundBitmap(Bitmap source, Bitmap hintMask, Bitmap backgroundHint, double strength, String requestedModel, String pipeline) {
        String mode = valueOr(pipeline, "auto").trim().toLowerCase(Locale.US);
        boolean forceHeuristic = "heuristic".equals(mode);
        boolean forceRembg = "rembg".equals(mode);
        if (!forceHeuristic) {
            Bitmap modelCutout = runLocalRembgModel(source, hintMask, requestedModel);
            if (modelCutout != null) {
                Bitmap sampledCutout = applyBackgroundSampleToRembgResult(
                        source, modelCutout, hintMask, backgroundHint, strength);
                if (sampledCutout != null) return sampledCutout;
                return modelCutout;
            }
            if (forceRembg) return source.copy(Bitmap.Config.ARGB_8888, true);
        }

        int w = source.getWidth();
        int h = source.getHeight();
        int count = w * h;
        int[] pixels = new int[count];
        source.getPixels(pixels, 0, w, 0, 0, w, h);

        Bitmap sizedHint = ensureBitmapSize(hintMask, w, h);
        boolean onnxInstalled = appContext != null && DynamicOnnxRuntime.isInstalled(appContext);
        Bitmap sizedBackgroundHint = forceHeuristic || !onnxInstalled ? ensureBitmapSize(backgroundHint, w, h) : null;
        boolean usedBackgroundSample = sizedBackgroundHint != null
                && applySampledBackgroundCutout(pixels, w, h, sizedBackgroundHint, sizedHint, strength);
        if (sizedHint != null) {
            int[] mask = new int[count];
            sizedHint.getPixels(mask, 0, w, 0, 0, w, h);
            for (int i = 0; i < count; i++) {
                int alpha = (pixels[i] >>> 24) & 0xff;
                int matte = maskOpacity(mask[i]);
                pixels[i] = (pixels[i] & 0x00ffffff) | (Math.min(alpha, matte) << 24);
            }
        } else if (!usedBackgroundSample) {
            applyHeuristicBackgroundCutout(pixels, w, h, strength);
        }
        if (lastBgRemoveSource.isEmpty()) {
            lastBgRemoveSource = usedBackgroundSample ? "fallback:sampled-background" : "fallback:portrait-heuristic";
        }

        Bitmap out = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888);
        out.setPixels(pixels, 0, w, 0, 0, w, h);
        return out;
    }

    private Bitmap applyBackgroundSampleToRembgResult(Bitmap source, Bitmap modelCutout, Bitmap hintMask,
                                                     Bitmap backgroundHint, double strength) {
        if (source == null || modelCutout == null || backgroundHint == null) return null;
        int w = source.getWidth();
        int h = source.getHeight();
        Bitmap sizedBackgroundHint = ensureBitmapSize(backgroundHint, w, h);
        if (sizedBackgroundHint == null) return null;

        int count = w * h;
        int[] pixels = new int[count];
        source.getPixels(pixels, 0, w, 0, 0, w, h);
        Bitmap keepMask = ensureBitmapSize(modelCutout, w, h);
        Bitmap sizedHint = ensureBitmapSize(hintMask, w, h);
        String modelSource = lastBgRemoveSource;
        boolean changed = applySampledBackgroundCutout(pixels, w, h, sizedBackgroundHint, keepMask, strength);
        if (!changed) return null;

        Bitmap out = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888);
        out.setPixels(pixels, 0, w, 0, 0, w, h);
        if (sizedHint != null) out = applyAlphaHint(out, sizedHint);
        lastBgRemoveSource = (modelSource == null || modelSource.isEmpty() ? "onnx" : modelSource) + "+sampled-background";
        lastBgRemoveError = "";
        return out;
    }

    private boolean applySampledBackgroundCutout(int[] pixels, int w, int h, Bitmap backgroundHint, Bitmap keepHint, double strength) {
        int count = w * h;
        int[] hintPixels = new int[count];
        backgroundHint.getPixels(hintPixels, 0, w, 0, 0, w, h);

        double s = clampStrength(strength);
        long sumR = 0, sumG = 0, sumB = 0;
        int minR = 255, minG = 255, minB = 255;
        int maxR = 0, maxG = 0, maxB = 0;
        int samples = 0;
        byte[] sampleSeed = new byte[count];
        for (int i = 0; i < count; i++) {
            if (maskOpacity(hintPixels[i]) <= MASK_OPACITY_THRESHOLD) continue;
            int p = pixels[i];
            if (((p >>> 24) & 0xff) <= 8) continue;
            sampleSeed[i] = 1;
            int r = (p >> 16) & 0xff;
            int g = (p >> 8) & 0xff;
            int b = p & 0xff;
            sumR += r;
            sumG += g;
            sumB += b;
            minR = Math.min(minR, r);
            minG = Math.min(minG, g);
            minB = Math.min(minB, b);
            maxR = Math.max(maxR, r);
            maxG = Math.max(maxG, g);
            maxB = Math.max(maxB, b);
            samples++;
        }
        if (samples == 0) return false;

        int bgR = (int) (sumR / samples);
        int bgG = (int) (sumG / samples);
        int bgB = (int) (sumB / samples);
        double distSum = 0;
        for (int i = 0; i < count; i++) {
            if (maskOpacity(hintPixels[i]) <= MASK_OPACITY_THRESHOLD) continue;
            int p = pixels[i];
            if (((p >>> 24) & 0xff) <= 8) continue;
            distSum += colorDistance(p, bgR, bgG, bgB);
        }
        double meanDistance = distSum / Math.max(1, samples);
        int meanThreshold = (int) Math.max(28, Math.min(220, meanDistance * (1.35 + s * 1.65) + 26 + 92 * s));
        int localThreshold = (int) Math.round(18 + 70 * s);
        int rangeMargin = (int) Math.max(22, Math.min(150, meanDistance * 0.55 + 18 + 82 * s));
        int loR = Math.max(0, minR - rangeMargin);
        int loG = Math.max(0, minG - rangeMargin);
        int loB = Math.max(0, minB - rangeMargin);
        int hiR = Math.min(255, maxR + rangeMargin);
        int hiG = Math.min(255, maxG + rangeMargin);
        int hiB = Math.min(255, maxB + rangeMargin);
        byte[] workArea = buildEnclosedStrokeRegion(hintPixels, w, h);
        byte[] subjectKeep = buildPortraitProtectionMask(pixels, hintPixels, w, h);
        byte[] edgeWall = dilateByteMask(buildEdgeWallMask(pixels, w, h, (int) Math.round(44 + 36 * s)), w, h, 1);
        byte[] expandedSeed = expandSampledBackgroundSeed(
                pixels, sampleSeed, w, h, bgR, bgG, bgB, meanThreshold, loR, loG, loB, hiR, hiG, hiB);
        clearMaskWhere(edgeWall, expandedSeed);

        int[] keep = null;
        if (keepHint != null) {
            keep = new int[count];
            keepHint.getPixels(keep, 0, w, 0, 0, w, h);
        }
        byte[] background = new byte[count];
        int[] queue = new int[count];
        int head = 0, tail = 0;
        for (int i = 0; i < count; i++) {
            if (expandedSeed[i] == 0) continue;
            if (background[i] != 0) continue;
            background[i] = 1;
            queue[tail++] = i;
        }

        while (head < tail) {
            int idx = queue[head++];
            int x = idx % w;
            int y = idx / w;
            if (x > 0) tail = enqueueSampledBackgroundCandidate(pixels, background, keep, subjectKeep, hintPixels, workArea, edgeWall, queue, tail, idx - 1, idx, bgR, bgG, bgB, meanThreshold, localThreshold, loR, loG, loB, hiR, hiG, hiB);
            if (x < w - 1) tail = enqueueSampledBackgroundCandidate(pixels, background, keep, subjectKeep, hintPixels, workArea, edgeWall, queue, tail, idx + 1, idx, bgR, bgG, bgB, meanThreshold, localThreshold, loR, loG, loB, hiR, hiG, hiB);
            if (y > 0) tail = enqueueSampledBackgroundCandidate(pixels, background, keep, subjectKeep, hintPixels, workArea, edgeWall, queue, tail, idx - w, idx, bgR, bgG, bgB, meanThreshold, localThreshold, loR, loG, loB, hiR, hiG, hiB);
            if (y < h - 1) tail = enqueueSampledBackgroundCandidate(pixels, background, keep, subjectKeep, hintPixels, workArea, edgeWall, queue, tail, idx + w, idx, bgR, bgG, bgB, meanThreshold, localThreshold, loR, loG, loB, hiR, hiG, hiB);
        }

        background = recoverSampledBackgroundIslands(
                pixels, background, keep, subjectKeep, hintPixels, workArea, edgeWall,
                w, h, bgR, bgG, bgB, meanThreshold, loR, loG, loB, hiR, hiG, hiB);

        int growIterations = s < 0.55 ? 0 : (s < 0.85 ? 1 : 2);
        if (growIterations > 0) background = dilateByteMask(background, w, h, growIterations);

        boolean changed = false;
        for (int i = 0; i < count; i++) {
            if (background[i] == 0) continue;
            boolean directlySampled = maskOpacity(hintPixels[i]) > MASK_OPACITY_THRESHOLD;
            if (!directlySampled && keep != null && maskOpacity(keep[i]) > MASK_OPACITY_THRESHOLD) continue;
            if (!directlySampled && subjectKeep != null && protectedSubjectBlocksRemoval(pixels[i], subjectKeep[i], bgR, bgG, bgB, meanThreshold, loR, loG, loB, hiR, hiG, hiB)) continue;
            if (((pixels[i] >>> 24) & 0xff) == 0) continue;
            pixels[i] &= 0x00ffffff;
            changed = true;
        }
        return changed;
    }

    private byte[] expandSampledBackgroundSeed(int[] pixels, byte[] sampleSeed,
                                               int w, int h, int bgR, int bgG, int bgB,
                                               int meanThreshold,
                                               int loR, int loG, int loB, int hiR, int hiG, int hiB) {
        byte[] footprint = dilateByteMask(sampleSeed, w, h, 3);
        byte[] expanded = sampleSeed.clone();
        for (int i = 0; i < pixels.length; i++) {
            if (footprint[i] == 0 || expanded[i] != 0) continue;
            if (((pixels[i] >>> 24) & 0xff) <= 8) continue;
            if (!isGloballySampledBackgroundCandidate(pixels[i], bgR, bgG, bgB, meanThreshold, loR, loG, loB, hiR, hiG, hiB)) continue;
            expanded[i] = 1;
        }
        return expanded;
    }

    private void clearMaskWhere(byte[] mask, byte[] clear) {
        if (mask == null || clear == null) return;
        int count = Math.min(mask.length, clear.length);
        for (int i = 0; i < count; i++) {
            if (clear[i] != 0) mask[i] = 0;
        }
    }

    private byte[] recoverSampledBackgroundIslands(int[] pixels, byte[] background,
                                                   int[] keep, byte[] subjectKeep, int[] hintPixels,
                                                   byte[] workArea, byte[] edgeWall,
                                                   int w, int h, int bgR, int bgG, int bgB,
                                                   int meanThreshold,
                                                   int loR, int loG, int loB, int hiR, int hiG, int hiB) {
        int count = w * h;
        byte[] eligible = new byte[count];
        byte[] strict = new byte[count];
        for (int i = 0; i < count; i++) {
            if (background[i] != 0) continue;
            boolean directlySampled = hintPixels != null && maskOpacity(hintPixels[i]) > MASK_OPACITY_THRESHOLD;
            if (!directlySampled && keep != null && maskOpacity(keep[i]) > MASK_OPACITY_THRESHOLD) continue;
            if (!directlySampled && subjectKeep != null && protectedSubjectBlocksRemoval(pixels[i], subjectKeep[i], bgR, bgG, bgB, meanThreshold, loR, loG, loB, hiR, hiG, hiB)) continue;
            if (!directlySampled && edgeWall != null && edgeWall[i] != 0) continue;
            if (!directlySampled && workArea != null && workArea[i] == 0) continue;
            if (!isGloballySampledBackgroundCandidate(pixels[i], bgR, bgG, bgB, meanThreshold, loR, loG, loB, hiR, hiG, hiB)) continue;
            eligible[i] = 1;
            if (stronglyMatchesSampledBackground(pixels[i], bgR, bgG, bgB, meanThreshold, loR, loG, loB, hiR, hiG, hiB)) {
                strict[i] = 1;
            }
        }

        byte[] recovered = background.clone();
        byte[] visited = new byte[count];
        int[] queue = new int[count];
        int[] component = new int[count];
        int minIslandArea = Math.max(64, Math.round(count * 0.0012f));
        for (int i = 0; i < count; i++) {
            if (eligible[i] == 0 || visited[i] != 0) continue;

            int head = 0;
            int tail = 0;
            int compCount = 0;
            int strictCount = 0;
            boolean touchesBackground = false;
            boolean touchesEdge = false;
            visited[i] = 1;
            queue[tail++] = i;

            while (head < tail) {
                int idx = queue[head++];
                component[compCount++] = idx;
                int x = idx % w;
                int y = idx / w;
                if (x == 0 || y == 0 || x == w - 1 || y == h - 1) touchesEdge = true;
                if (strict[idx] != 0) strictCount++;

                if (x > 0) {
                    int ni = idx - 1;
                    if (recovered[ni] != 0) touchesBackground = true;
                    else if (eligible[ni] != 0 && visited[ni] == 0) {
                        visited[ni] = 1;
                        queue[tail++] = ni;
                    }
                }
                if (x < w - 1) {
                    int ni = idx + 1;
                    if (recovered[ni] != 0) touchesBackground = true;
                    else if (eligible[ni] != 0 && visited[ni] == 0) {
                        visited[ni] = 1;
                        queue[tail++] = ni;
                    }
                }
                if (y > 0) {
                    int ni = idx - w;
                    if (recovered[ni] != 0) touchesBackground = true;
                    else if (eligible[ni] != 0 && visited[ni] == 0) {
                        visited[ni] = 1;
                        queue[tail++] = ni;
                    }
                }
                if (y < h - 1) {
                    int ni = idx + w;
                    if (recovered[ni] != 0) touchesBackground = true;
                    else if (eligible[ni] != 0 && visited[ni] == 0) {
                        visited[ni] = 1;
                        queue[tail++] = ni;
                    }
                }
            }

            boolean stronglySameIsland = compCount >= minIslandArea
                    && strictCount >= Math.round(compCount * 0.82f);
            if (!touchesBackground && !touchesEdge && !stronglySameIsland) continue;
            for (int c = 0; c < compCount; c++) {
                recovered[component[c]] = 1;
            }
        }
        return recovered;
    }

    private byte[] buildEnclosedStrokeRegion(int[] hintPixels, int w, int h) {
        int count = w * h;
        byte[] stroke = new byte[count];
        int strokeCount = 0;
        for (int i = 0; i < count; i++) {
            if (maskOpacity(hintPixels[i]) <= MASK_OPACITY_THRESHOLD) continue;
            stroke[i] = 1;
            strokeCount++;
        }
        if (strokeCount < 16) return null;

        byte[] barrier = dilateByteMask(stroke, w, h, 4);
        byte[] outside = new byte[count];
        int[] queue = new int[count];
        int head = 0, tail = 0;
        for (int x = 0; x < w; x++) {
            tail = enqueueOutsideStrokeFill(barrier, outside, queue, tail, x);
            tail = enqueueOutsideStrokeFill(barrier, outside, queue, tail, (h - 1) * w + x);
        }
        for (int y = 0; y < h; y++) {
            tail = enqueueOutsideStrokeFill(barrier, outside, queue, tail, y * w);
            tail = enqueueOutsideStrokeFill(barrier, outside, queue, tail, y * w + w - 1);
        }
        while (head < tail) {
            int idx = queue[head++];
            int x = idx % w;
            int y = idx / w;
            if (x > 0) tail = enqueueOutsideStrokeFill(barrier, outside, queue, tail, idx - 1);
            if (x < w - 1) tail = enqueueOutsideStrokeFill(barrier, outside, queue, tail, idx + 1);
            if (y > 0) tail = enqueueOutsideStrokeFill(barrier, outside, queue, tail, idx - w);
            if (y < h - 1) tail = enqueueOutsideStrokeFill(barrier, outside, queue, tail, idx + w);
        }

        byte[] enclosed = new byte[count];
        int enclosedCount = 0;
        for (int i = 0; i < count; i++) {
            if (outside[i] != 0 || barrier[i] != 0) continue;
            enclosed[i] = 1;
            enclosedCount++;
        }
        if (enclosedCount < Math.max(64, strokeCount * 2)) return null;
        if (enclosedCount > (int) (count * 0.96)) return null;
        for (int i = 0; i < count; i++) {
            if (stroke[i] != 0) enclosed[i] = 1;
        }
        return enclosed;
    }

    private int enqueueOutsideStrokeFill(byte[] barrier, byte[] outside, int[] queue, int tail, int idx) {
        if (idx < 0 || idx >= barrier.length) return tail;
        if (barrier[idx] != 0 || outside[idx] != 0) return tail;
        outside[idx] = 1;
        queue[tail++] = idx;
        return tail;
    }

    private byte[] buildEdgeWallMask(int[] pixels, int w, int h, int threshold) {
        int count = w * h;
        byte[] wall = new byte[count];
        for (int y = 0; y < h; y++) {
            int row = y * w;
            for (int x = 0; x < w; x++) {
                int idx = row + x;
                if (x < w - 1 && colorDistance(pixels[idx], pixels[idx + 1]) > threshold) {
                    wall[idx] = 1;
                    wall[idx + 1] = 1;
                }
                if (y < h - 1 && colorDistance(pixels[idx], pixels[idx + w]) > threshold) {
                    wall[idx] = 1;
                    wall[idx + w] = 1;
                }
            }
        }
        return wall;
    }

    private byte[] buildPortraitProtectionMask(int[] pixels, int[] sampleMask, int w, int h) {
        int count = w * h;
        byte[] protect = new byte[count];
        byte[] skin = new byte[count];
        for (int i = 0; i < count; i++) {
            if (maskOpacity(sampleMask[i]) > MASK_OPACITY_THRESHOLD) continue;
            if (!looksLikeSkinPixel(pixels[i])) continue;
            skin[i] = 1;
        }

        byte[] visited = new byte[count];
        int[] queue = new int[count];
        int[] component = new int[count];
        int[] bestComponent = null;
        int bestCount = 0;
        int minX = w, minY = h, maxX = -1, maxY = -1;
        double bestScore = 0;

        for (int i = 0; i < count; i++) {
            if (skin[i] == 0 || visited[i] != 0) continue;
            int head = 0, tail = 0, compCount = 0;
            int cMinX = w, cMinY = h, cMaxX = -1, cMaxY = -1;
            visited[i] = 1;
            queue[tail++] = i;
            while (head < tail) {
                int idx = queue[head++];
                component[compCount++] = idx;
                int x = idx % w;
                int y = idx / w;
                if (x < cMinX) cMinX = x;
                if (x > cMaxX) cMaxX = x;
                if (y < cMinY) cMinY = y;
                if (y > cMaxY) cMaxY = y;
                for (int dy = -1; dy <= 1; dy++) {
                    int ny = y + dy;
                    if (ny < 0 || ny >= h) continue;
                    for (int dx = -1; dx <= 1; dx++) {
                        if (dx == 0 && dy == 0) continue;
                        int nx = x + dx;
                        if (nx < 0 || nx >= w) continue;
                        int ni = ny * w + nx;
                        if (skin[ni] == 0 || visited[ni] != 0) continue;
                        visited[ni] = 1;
                        queue[tail++] = ni;
                    }
                }
            }
            if (compCount < 24) continue;
            int compW = Math.max(1, cMaxX - cMinX + 1);
            int compH = Math.max(1, cMaxY - cMinY + 1);
            boolean touchesEdge = cMinX <= 1 || cMaxX >= w - 2 || cMinY <= 1 || cMaxY >= h - 2;
            double score = compCount;
            if (touchesEdge) score *= 0.08;
            if (compW > w * 0.58 || compH > h * 0.72) score *= 0.18;
            if ((double) compW * compH > count * 0.32) score *= 0.15;
            double cx = (cMinX + cMaxX) / 2.0;
            double centerFavor = 1.0 - Math.min(1.0, Math.abs(cx - w / 2.0) / Math.max(1.0, w / 2.0));
            score *= 0.55 + centerFavor * 0.45;
            if (score > bestScore) {
                bestScore = score;
                bestCount = compCount;
                bestComponent = Arrays.copyOf(component, compCount);
                minX = cMinX;
                minY = cMinY;
                maxX = cMaxX;
                maxY = cMaxY;
            }
        }

        int skinCount = bestCount;
        if (skinCount < 24) return null;
        for (int idx : bestComponent) protect[idx] = 2;

        int skinW = Math.max(1, maxX - minX + 1);
        int skinH = Math.max(1, maxY - minY + 1);
        int padX = Math.max(12, Math.round(skinW * 0.80f));
        int padTop = Math.max(16, Math.round(skinH * 1.10f));
        int padBottom = Math.max(12, Math.round(skinH * 1.15f));
        int roiLeft = Math.max(0, minX - padX);
        int roiRight = Math.min(w - 1, maxX + padX);
        int roiTop = Math.max(0, minY - padTop);
        int roiBottom = Math.min(h - 1, maxY + padBottom);

        int protectedCount = skinCount;
        int maxProtectedCount = skinCount + Math.max(256, Math.round(skinCount * 5.0f));
        int[] hairQueue = new int[count];
        int head = 0, tail = 0;
        for (int i = 0; i < count; i++) {
            if (protect[i] != 0) hairQueue[tail++] = i;
        }
        while (head < tail) {
            int idx = hairQueue[head++];
            int x = idx % w;
            int y = idx / w;
            for (int dy = -1; dy <= 1; dy++) {
                if (protectedCount >= maxProtectedCount) break;
                int ny = y + dy;
                if (ny < roiTop || ny > roiBottom) continue;
                for (int dx = -1; dx <= 1; dx++) {
                    if (protectedCount >= maxProtectedCount) break;
                    if (dx == 0 && dy == 0) continue;
                    int nx = x + dx;
                    if (nx < roiLeft || nx > roiRight) continue;
                    int ni = ny * w + nx;
                    if (protect[ni] != 0) continue;
                    if (maskOpacity(sampleMask[ni]) > MASK_OPACITY_THRESHOLD) continue;
                    if (!looksLikeHairPixel(pixels[ni])) continue;
                    protect[ni] = 2;
                    hairQueue[tail++] = ni;
                    protectedCount++;
                }
            }
        }
        int bodyTop = Math.max(0, maxY - Math.round(skinH * 0.10f));
        int bodyBottom = Math.min(h - 1, maxY + Math.max(Math.round(skinH * 5.0f), Math.round(h * 0.35f)));
        float centerX = (minX + maxX) / 2.0f;
        int bodyCap = protectedCount + Math.max(384, Math.round(skinCount * 4.5f));
        for (int y = bodyTop; y <= bodyBottom && protectedCount < bodyCap; y++) {
            float t = bodyBottom == bodyTop ? 0f : (y - bodyTop) / (float) Math.max(1, bodyBottom - bodyTop);
            int halfWidth = Math.max(Math.round(skinW * 0.90f), Math.round(skinW * (0.95f + 1.35f * t)));
            int left = Math.max(0, Math.round(centerX - halfWidth));
            int right = Math.min(w - 1, Math.round(centerX + halfWidth));
            for (int x = left; x <= right && protectedCount < bodyCap; x++) {
                int idx = y * w + x;
                if (protect[idx] != 0) continue;
                if (maskOpacity(sampleMask[idx]) > MASK_OPACITY_THRESHOLD) continue;
                if (!looksLikePortraitBodyPixel(pixels[idx])) continue;
                protect[idx] = 1;
                protectedCount++;
            }
        }
        if (protectedCount < 24) return null;
        return protect;
    }

    private byte[] dilateByteMask(byte[] mask, int w, int h, int iterations) {
        byte[] cur = mask;
        for (int iter = 0; iter < iterations; iter++) {
            byte[] next = cur.clone();
            for (int y = 0; y < h; y++) {
                int row = y * w;
                for (int x = 0; x < w; x++) {
                    int idx = row + x;
                    if (cur[idx] != 0) continue;
                    boolean near = false;
                    for (int dy = -1; dy <= 1 && !near; dy++) {
                        int ny = y + dy;
                        if (ny < 0 || ny >= h) continue;
                        for (int dx = -1; dx <= 1; dx++) {
                            int nx = x + dx;
                            if (nx < 0 || nx >= w) continue;
                            if (cur[ny * w + nx] != 0) {
                                near = true;
                                break;
                            }
                        }
                    }
                    if (near) next[idx] = 1;
                }
            }
            cur = next;
        }
        return cur;
    }

    private boolean looksLikeSkinPixel(int p) {
        int alpha = (p >>> 24) & 0xff;
        if (alpha <= 8) return false;
        int r = (p >> 16) & 0xff;
        int g = (p >> 8) & 0xff;
        int b = p & 0xff;
        int max = Math.max(r, Math.max(g, b));
        int min = Math.min(r, Math.min(g, b));
        return r > 55 && g > 35 && b > 18
                && (max - min) > 12
                && r > b
                && (r - g) > -8;
    }

    private boolean looksLikeHairPixel(int p) {
        int alpha = (p >>> 24) & 0xff;
        if (alpha <= 8) return false;
        int r = (p >> 16) & 0xff;
        int g = (p >> 8) & 0xff;
        int b = p & 0xff;
        int max = Math.max(r, Math.max(g, b));
        int min = Math.min(r, Math.min(g, b));
        int chroma = max - min;
        int brightness = (r + g + b) / 3;
        boolean darkHair = brightness < 95 && chroma < 70;
        boolean brownHair = r >= g - 12 && g >= b - 18 && brightness >= 45 && brightness < 150 && chroma > 12;
        boolean blondHair = r > 115 && g > 90 && b < 135 && r >= g - 22;
        return darkHair || brownHair || blondHair;
    }

    private boolean looksLikePortraitBodyPixel(int p) {
        int alpha = (p >>> 24) & 0xff;
        if (alpha <= 8) return false;
        if (looksLikeSkinPixel(p) || looksLikeHairPixel(p)) return true;
        int r = (p >> 16) & 0xff;
        int g = (p >> 8) & 0xff;
        int b = p & 0xff;
        int max = Math.max(r, Math.max(g, b));
        int min = Math.min(r, Math.min(g, b));
        int chroma = max - min;
        int brightness = (r + g + b) / 3;
        boolean lightClothing = brightness > 135 && chroma < 95;
        boolean darkClothing = brightness < 92 && chroma < 88;
        boolean coloredClothing = brightness > 35 && brightness < 230 && chroma > 18;
        return lightClothing || darkClothing || coloredClothing;
    }

    private int enqueueSampledBackgroundCandidate(int[] pixels, byte[] background,
                                                  int[] keep, byte[] subjectKeep, int[] hintPixels, byte[] workArea, byte[] edgeWall,
                                                  int[] queue, int tail,
                                                  int idx, int parentIdx, int bgR, int bgG, int bgB,
                                                  int meanThreshold, int localThreshold,
                                                  int loR, int loG, int loB, int hiR, int hiG, int hiB) {
        if (idx < 0 || idx >= pixels.length || background[idx] != 0) return tail;
        boolean directlySampled = hintPixels != null && maskOpacity(hintPixels[idx]) > MASK_OPACITY_THRESHOLD;
        if (!directlySampled && keep != null && maskOpacity(keep[idx]) > MASK_OPACITY_THRESHOLD) return tail;
        if (!directlySampled && subjectKeep != null && protectedSubjectBlocksRemoval(pixels[idx], subjectKeep[idx], bgR, bgG, bgB, meanThreshold, loR, loG, loB, hiR, hiG, hiB)) return tail;
        if (!directlySampled && edgeWall != null && edgeWall[idx] != 0) return tail;
        if (!directlySampled && workArea != null && workArea[idx] == 0) return tail;
        if (isSampledBackgroundCandidate(pixels[idx], pixels[parentIdx], bgR, bgG, bgB,
                meanThreshold, localThreshold, loR, loG, loB, hiR, hiG, hiB)) {
            background[idx] = 1;
            queue[tail++] = idx;
        }
        return tail;
    }

    private boolean protectedSubjectBlocksRemoval(int pixel, byte subjectFlag, int bgR, int bgG, int bgB,
                                                  int meanThreshold,
                                                  int loR, int loG, int loB, int hiR, int hiG, int hiB) {
        if (subjectFlag == 0) return false;
        return true;
    }

    private boolean stronglyMatchesSampledBackground(int pixel, int bgR, int bgG, int bgB, int meanThreshold,
                                                     int loR, int loG, int loB, int hiR, int hiG, int hiB) {
        int alpha = (pixel >>> 24) & 0xff;
        if (alpha <= 8) return true;
        int r = (pixel >> 16) & 0xff;
        int g = (pixel >> 8) & 0xff;
        int b = pixel & 0xff;
        if (r >= loR && r <= hiR && g >= loG && g <= hiG && b >= loB && b <= hiB) return true;
        return colorDistance(pixel, bgR, bgG, bgB) <= Math.max(32, meanThreshold * 0.72);
    }

    private boolean isGloballySampledBackgroundCandidate(int pixel, int bgR, int bgG, int bgB,
                                                         int meanThreshold,
                                                         int loR, int loG, int loB, int hiR, int hiG, int hiB) {
        int alpha = (pixel >>> 24) & 0xff;
        if (alpha <= 8) return true;
        int r = (pixel >> 16) & 0xff;
        int g = (pixel >> 8) & 0xff;
        int b = pixel & 0xff;
        if (r >= loR && r <= hiR && g >= loG && g <= hiG && b >= loB && b <= hiB) return true;
        return colorDistance(pixel, bgR, bgG, bgB) <= meanThreshold;
    }

    private boolean isSampledBackgroundCandidate(int pixel, int parentPixel, int bgR, int bgG, int bgB,
                                                 int meanThreshold, int localThreshold,
                                                 int loR, int loG, int loB, int hiR, int hiG, int hiB) {
        int alpha = (pixel >>> 24) & 0xff;
        if (alpha <= 8) return true;
        int r = (pixel >> 16) & 0xff;
        int g = (pixel >> 8) & 0xff;
        int b = pixel & 0xff;
        if (r >= loR && r <= hiR && g >= loG && g <= hiG && b >= loB && b <= hiB) return true;
        if (colorDistance(pixel, bgR, bgG, bgB) <= meanThreshold) return true;
        int pr = (parentPixel >> 16) & 0xff;
        int pg = (parentPixel >> 8) & 0xff;
        int pb = parentPixel & 0xff;
        return colorDistance(pixel, pr, pg, pb) <= localThreshold;
    }

    private void applyHeuristicBackgroundCutout(int[] pixels, int w, int h, double strength) {
        int count = w * h;
        if (count == 0) return;
        double s = clampStrength(strength);

        long sumR = 0, sumG = 0, sumB = 0;
        int samples = 0;
        for (int x = 0; x < w; x++) {
            int top = pixels[x];
            int bottom = pixels[(h - 1) * w + x];
            if (((top >>> 24) & 0xff) > 8) {
                sumR += (top >> 16) & 0xff;
                sumG += (top >> 8) & 0xff;
                sumB += top & 0xff;
                samples++;
            }
            if (((bottom >>> 24) & 0xff) > 8) {
                sumR += (bottom >> 16) & 0xff;
                sumG += (bottom >> 8) & 0xff;
                sumB += bottom & 0xff;
                samples++;
            }
        }
        for (int y = 1; y < h - 1; y++) {
            int left = pixels[y * w];
            int right = pixels[y * w + w - 1];
            if (((left >>> 24) & 0xff) > 8) {
                sumR += (left >> 16) & 0xff;
                sumG += (left >> 8) & 0xff;
                sumB += left & 0xff;
                samples++;
            }
            if (((right >>> 24) & 0xff) > 8) {
                sumR += (right >> 16) & 0xff;
                sumG += (right >> 8) & 0xff;
                sumB += right & 0xff;
                samples++;
            }
        }
        if (samples == 0) return;

        int bgR = (int) (sumR / samples);
        int bgG = (int) (sumG / samples);
        int bgB = (int) (sumB / samples);
        int stepX = Math.max(1, w / 512);
        int stepY = Math.max(1, h / 512);
        double distSum = 0;
        int distSamples = 0;
        for (int x = 0; x < w; x += stepX) {
            distSum += colorDistance(pixels[x], bgR, bgG, bgB);
            distSum += colorDistance(pixels[(h - 1) * w + x], bgR, bgG, bgB);
            distSamples += 2;
        }
        for (int y = 0; y < h; y += stepY) {
            distSum += colorDistance(pixels[y * w], bgR, bgG, bgB);
            distSum += colorDistance(pixels[y * w + w - 1], bgR, bgG, bgB);
            distSamples += 2;
        }
        double meanBorderDistance = distSamples == 0 ? 0 : distSum / distSamples;
        int threshold = (int) Math.max(28, Math.min(150, meanBorderDistance * (1.6 + s * 1.2) + 18 + 42 * s));
        int[] protectSampleMask = new int[count];
        byte[] subjectKeep = buildPortraitProtectionMask(pixels, protectSampleMask, w, h);
        byte[] edgeWall = dilateByteMask(
                buildEdgeWallMask(pixels, w, h, (int) Math.max(44, Math.min(110, threshold * 0.85))),
                w,
                h,
                1);

        byte[] background = new byte[count];
        int[] queue = new int[count];
        int head = 0, tail = 0;
        for (int x = 0; x < w; x++) {
            tail = enqueueBackgroundCandidate(pixels, background, subjectKeep, edgeWall, queue, tail, x, bgR, bgG, bgB, threshold);
            tail = enqueueBackgroundCandidate(pixels, background, subjectKeep, edgeWall, queue, tail, (h - 1) * w + x, bgR, bgG, bgB, threshold);
        }
        for (int y = 1; y < h - 1; y++) {
            tail = enqueueBackgroundCandidate(pixels, background, subjectKeep, edgeWall, queue, tail, y * w, bgR, bgG, bgB, threshold);
            tail = enqueueBackgroundCandidate(pixels, background, subjectKeep, edgeWall, queue, tail, y * w + w - 1, bgR, bgG, bgB, threshold);
        }

        while (head < tail) {
            int idx = queue[head++];
            int x = idx % w;
            int y = idx / w;
            if (x > 0) tail = enqueueBackgroundCandidate(pixels, background, subjectKeep, edgeWall, queue, tail, idx - 1, bgR, bgG, bgB, threshold);
            if (x < w - 1) tail = enqueueBackgroundCandidate(pixels, background, subjectKeep, edgeWall, queue, tail, idx + 1, bgR, bgG, bgB, threshold);
            if (y > 0) tail = enqueueBackgroundCandidate(pixels, background, subjectKeep, edgeWall, queue, tail, idx - w, bgR, bgG, bgB, threshold);
            if (y < h - 1) tail = enqueueBackgroundCandidate(pixels, background, subjectKeep, edgeWall, queue, tail, idx + w, bgR, bgG, bgB, threshold);
        }

        for (int i = 0; i < count; i++) {
            if (background[i] != 0) pixels[i] &= 0x00ffffff;
        }
    }

    private int enqueueBackgroundCandidate(int[] pixels, byte[] background, byte[] subjectKeep, byte[] edgeWall,
                                           int[] queue, int tail,
                                           int idx, int bgR, int bgG, int bgB, int threshold) {
        if (idx < 0 || idx >= pixels.length || background[idx] != 0) return tail;
        if (subjectKeep != null && subjectKeep[idx] != 0) return tail;
        if (edgeWall != null && edgeWall[idx] != 0) return tail;
        int p = pixels[idx];
        int alpha = (p >>> 24) & 0xff;
        if (alpha <= 8 || colorDistance(p, bgR, bgG, bgB) <= threshold) {
            background[idx] = 1;
            queue[tail++] = idx;
        }
        return tail;
    }

    private Bitmap inpaintBitmap(Bitmap source, Bitmap maskBitmap) {
        int w = source.getWidth();
        int h = source.getHeight();
        int count = w * h;
        int[] pixels = new int[count];
        source.getPixels(pixels, 0, w, 0, 0, w, h);

        Bitmap sizedMask = ensureBitmapSize(maskBitmap, w, h);
        int[] maskPixels = new int[count];
        sizedMask.getPixels(maskPixels, 0, w, 0, 0, w, h);

        byte[] masked = new byte[count];
        byte[] known = new byte[count];
        byte[] queued = new byte[count];
        int maskCount = 0;
        long sumA = 0, sumR = 0, sumG = 0, sumB = 0;
        int knownCount = 0;
        for (int i = 0; i < count; i++) {
            if (maskOpacity(maskPixels[i]) > MASK_OPACITY_THRESHOLD) {
                masked[i] = 1;
                maskCount++;
            } else {
                known[i] = 1;
                int p = pixels[i];
                int alpha = (p >>> 24) & 0xff;
                if (alpha > 8) {
                    sumA += alpha;
                    sumR += (p >> 16) & 0xff;
                    sumG += (p >> 8) & 0xff;
                    sumB += p & 0xff;
                    knownCount++;
                }
            }
        }
        if (maskCount == 0) return source.copy(Bitmap.Config.ARGB_8888, true);

        int fallback = knownCount > 0
                ? (((int) (sumA / knownCount)) << 24)
                    | (((int) (sumR / knownCount)) << 16)
                    | (((int) (sumG / knownCount)) << 8)
                    | ((int) (sumB / knownCount))
                : 0xff202020;

        int[] queue = new int[maskCount];
        int head = 0, tail = 0;
        for (int i = 0; i < count; i++) {
            if (masked[i] == 0 || !hasKnownNeighbor(known, w, h, i)) continue;
            queued[i] = 1;
            queue[tail++] = i;
        }

        if (tail == 0) {
            for (int i = 0; i < count; i++) {
                if (masked[i] != 0) pixels[i] = fallback;
            }
        } else {
            while (head < tail) {
                int idx = queue[head++];
                if (known[idx] != 0) continue;
                int x = idx % w;
                int y = idx / w;
                pixels[idx] = averageKnownPixel(pixels, known, w, h, x, y, fallback);
                known[idx] = 1;
                for (int dy = -1; dy <= 1; dy++) {
                    int ny = y + dy;
                    if (ny < 0 || ny >= h) continue;
                    for (int dx = -1; dx <= 1; dx++) {
                        if (dx == 0 && dy == 0) continue;
                        int nx = x + dx;
                        if (nx < 0 || nx >= w) continue;
                        int ni = ny * w + nx;
                        if (masked[ni] == 0 || known[ni] != 0 || queued[ni] != 0) continue;
                        queued[ni] = 1;
                        queue[tail++] = ni;
                    }
                }
            }
            for (int i = 0; i < count; i++) {
                if (masked[i] != 0 && known[i] == 0) pixels[i] = fallback;
            }
        }

        addSubtleInpaintDetail(pixels, masked, w, h);
        Bitmap out = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888);
        out.setPixels(pixels, 0, w, 0, 0, w, h);
        return out;
    }

    private boolean hasKnownNeighbor(byte[] known, int w, int h, int idx) {
        int x = idx % w;
        int y = idx / w;
        for (int dy = -1; dy <= 1; dy++) {
            int ny = y + dy;
            if (ny < 0 || ny >= h) continue;
            for (int dx = -1; dx <= 1; dx++) {
                if (dx == 0 && dy == 0) continue;
                int nx = x + dx;
                if (nx < 0 || nx >= w) continue;
                if (known[ny * w + nx] != 0) return true;
            }
        }
        return false;
    }

    private int averageKnownPixel(int[] pixels, byte[] known, int w, int h, int x, int y, int fallback) {
        float total = 0f;
        float a = 0f, r = 0f, g = 0f, b = 0f;
        for (int radius = 1; radius <= 4 && total <= 0f; radius++) {
            for (int dy = -radius; dy <= radius; dy++) {
                int ny = y + dy;
                if (ny < 0 || ny >= h) continue;
                for (int dx = -radius; dx <= radius; dx++) {
                    if (dx == 0 && dy == 0) continue;
                    int nx = x + dx;
                    if (nx < 0 || nx >= w) continue;
                    int ni = ny * w + nx;
                    if (known[ni] == 0) continue;
                    int p = pixels[ni];
                    int alpha = (p >>> 24) & 0xff;
                    float weight = (alpha <= 8 ? 0.25f : 1f) / (1f + Math.abs(dx) + Math.abs(dy));
                    total += weight;
                    a += alpha * weight;
                    r += ((p >> 16) & 0xff) * weight;
                    g += ((p >> 8) & 0xff) * weight;
                    b += (p & 0xff) * weight;
                }
            }
        }
        if (total <= 0f) return fallback;
        return (clampColor(a / total) << 24)
                | (clampColor(r / total) << 16)
                | (clampColor(g / total) << 8)
                | clampColor(b / total);
    }

    private void addSubtleInpaintDetail(int[] pixels, byte[] masked, int w, int h) {
        int[] copy = pixels.clone();
        for (int y = 1; y < h - 1; y++) {
            for (int x = 1; x < w - 1; x++) {
                int idx = y * w + x;
                if (masked[idx] == 0) continue;
                int c = copy[idx];
                int blurR = 0, blurG = 0, blurB = 0, n = 0;
                for (int dy = -1; dy <= 1; dy++) {
                    for (int dx = -1; dx <= 1; dx++) {
                        int p = copy[(y + dy) * w + x + dx];
                        blurR += (p >> 16) & 0xff;
                        blurG += (p >> 8) & 0xff;
                        blurB += p & 0xff;
                        n++;
                    }
                }
                int alpha = (c >>> 24) & 0xff;
                int r = (c >> 16) & 0xff;
                int g = (c >> 8) & 0xff;
                int b = c & 0xff;
                int nr = clampColor(r + (r - blurR / (float) n) * 0.35f);
                int ng = clampColor(g + (g - blurG / (float) n) * 0.35f);
                int nb = clampColor(b + (b - blurB / (float) n) * 0.35f);
                pixels[idx] = (alpha << 24) | (nr << 16) | (ng << 8) | nb;
            }
        }
    }

    private int maskOpacity(int pixel) {
        int alpha = (pixel >>> 24) & 0xff;
        if (alpha <= 4) return 0;
        int r = (pixel >> 16) & 0xff;
        int g = (pixel >> 8) & 0xff;
        int b = pixel & 0xff;
        if (r > 120 && r > g * 1.35f && r > b * 1.35f) return alpha;
        int lum = (r * 299 + g * 587 + b * 114) / 1000;
        return Math.max(0, Math.min(255, Math.round(lum * (alpha / 255f))));
    }

    private double colorDistance(int pixel, int r, int g, int b) {
        int dr = ((pixel >> 16) & 0xff) - r;
        int dg = ((pixel >> 8) & 0xff) - g;
        int db = (pixel & 0xff) - b;
        return Math.sqrt(dr * dr + dg * dg + db * db);
    }

    private double colorDistance(int a, int b) {
        int dr = ((a >> 16) & 0xff) - ((b >> 16) & 0xff);
        int dg = ((a >> 8) & 0xff) - ((b >> 8) & 0xff);
        int db = (a & 0xff) - (b & 0xff);
        return Math.sqrt(dr * dr + dg * dg + db * db);
    }

    private Bitmap sharpenBitmap(Bitmap source, int amount) {
        float strength = Math.max(0f, Math.min(1.75f, amount / 100f * 1.35f));
        int w = source.getWidth();
        int h = source.getHeight();
        int[] src = new int[w * h];
        int[] dst = new int[w * h];
        source.getPixels(src, 0, w, 0, 0, w, h);
        System.arraycopy(src, 0, dst, 0, src.length);
        for (int y = 1; y < h - 1; y++) {
            for (int x = 1; x < w - 1; x++) {
                int idx = y * w + x;
                int c = src[idx];
                if (((c >>> 24) & 0xff) == 0) continue;
                int p00 = src[(y - 1) * w + x - 1];
                int p01 = src[(y - 1) * w + x];
                int p02 = src[(y - 1) * w + x + 1];
                int p10 = src[y * w + x - 1];
                int p12 = src[y * w + x + 1];
                int p20 = src[(y + 1) * w + x - 1];
                int p21 = src[(y + 1) * w + x];
                int p22 = src[(y + 1) * w + x + 1];
                int blurR = (((p00 >> 16) & 0xff) + (((p01 >> 16) & 0xff) * 2) + ((p02 >> 16) & 0xff)
                        + (((p10 >> 16) & 0xff) * 2) + (((c >> 16) & 0xff) * 4) + (((p12 >> 16) & 0xff) * 2)
                        + ((p20 >> 16) & 0xff) + (((p21 >> 16) & 0xff) * 2) + ((p22 >> 16) & 0xff)) / 16;
                int blurG = (((p00 >> 8) & 0xff) + (((p01 >> 8) & 0xff) * 2) + ((p02 >> 8) & 0xff)
                        + (((p10 >> 8) & 0xff) * 2) + (((c >> 8) & 0xff) * 4) + (((p12 >> 8) & 0xff) * 2)
                        + ((p20 >> 8) & 0xff) + (((p21 >> 8) & 0xff) * 2) + ((p22 >> 8) & 0xff)) / 16;
                int blurB = ((p00 & 0xff) + ((p01 & 0xff) * 2) + (p02 & 0xff)
                        + ((p10 & 0xff) * 2) + ((c & 0xff) * 4) + ((p12 & 0xff) * 2)
                        + (p20 & 0xff) + ((p21 & 0xff) * 2) + (p22 & 0xff)) / 16;
                int r = (c >> 16) & 0xff;
                int g = (c >> 8) & 0xff;
                int b = c & 0xff;
                int nr = clampColor(r + strength * (r - blurR));
                int ng = clampColor(g + strength * (g - blurG));
                int nb = clampColor(b + strength * (b - blurB));
                dst[idx] = (c & 0xff000000) | (nr << 16) | (ng << 8) | nb;
            }
        }
        Bitmap out = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888);
        out.setPixels(dst, 0, w, 0, 0, w, h);
        return out;
    }

    private Bitmap denoiseBitmap(Bitmap source, int amount) {
        float blend = Math.max(0f, Math.min(1f, amount / 100f));
        int w = source.getWidth();
        int h = source.getHeight();
        int[] src = new int[w * h];
        int[] dst = new int[w * h];
        source.getPixels(src, 0, w, 0, 0, w, h);
        System.arraycopy(src, 0, dst, 0, src.length);
        for (int y = 1; y < h - 1; y++) {
            for (int x = 1; x < w - 1; x++) {
                int idx = y * w + x;
                int c = src[idx];
                int sumR = 0, sumG = 0, sumB = 0;
                for (int dy = -1; dy <= 1; dy++) {
                    for (int dx = -1; dx <= 1; dx++) {
                        int p = src[(y + dy) * w + (x + dx)];
                        sumR += (p >> 16) & 0xff;
                        sumG += (p >> 8) & 0xff;
                        sumB += p & 0xff;
                    }
                }
                int r = (c >> 16) & 0xff;
                int g = (c >> 8) & 0xff;
                int b = c & 0xff;
                int nr = clampColor(Math.round(r * (1f - blend) + (sumR / 9f) * blend));
                int ng = clampColor(Math.round(g * (1f - blend) + (sumG / 9f) * blend));
                int nb = clampColor(Math.round(b * (1f - blend) + (sumB / 9f) * blend));
                dst[idx] = (c & 0xff000000) | (nr << 16) | (ng << 8) | nb;
            }
        }
        Bitmap out = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888);
        out.setPixels(dst, 0, w, 0, 0, w, h);
        return out;
    }

    private Bitmap upscaleBitmap(Bitmap source, int scale) {
        int safeScale = Math.max(2, Math.min(4, scale));
        return Bitmap.createScaledBitmap(source, source.getWidth() * safeScale, source.getHeight() * safeScale, true);
    }

    private int clampColor(float value) {
        if (value < 0) return 0;
        if (value > 255) return 255;
        return Math.round(value);
    }

    private double clampStrength(double value) {
        if (Double.isNaN(value) || Double.isInfinite(value)) return 0.7;
        return Math.max(0.1, Math.min(1.0, value));
    }

    private void routeGalleryAlbum(Request request, OutputStream out, String tail) throws Exception {
        String[] parts = tail.split("/", 2);
        String albumId = decodePathPart(parts.length > 0 ? parts[0] : "");
        String action = parts.length > 1 ? parts[1] : "";
        if (albumId.isEmpty()) {
            sendJson(out, 404, new JSONObject().put("detail", "Album not found"));
            return;
        }
        if ("PUT".equals(request.method) && action.isEmpty()) {
            JSONObject result = updateGalleryAlbum(albumId, requestJson(request));
            int status = result.optInt("_status", 200);
            result.remove("_status");
            sendJson(out, status, result);
            return;
        }
        if ("DELETE".equals(request.method) && action.isEmpty()) {
            JSONObject result = deleteGalleryAlbum(albumId);
            int status = result.optInt("_status", 200);
            result.remove("_status");
            sendJson(out, status, result);
            return;
        }
        if ("POST".equals(request.method) && ("add".equals(action) || "remove".equals(action))) {
            JSONObject result = moveGalleryImagesForAlbum(albumId, requestJson(request), "add".equals(action));
            int status = result.optInt("_status", 200);
            result.remove("_status");
            sendJson(out, status, result);
            return;
        }
        sendJson(out, 404, new JSONObject().put("detail", "Mobile gallery album route not implemented"));
    }

    private JSONObject galleryUpload(Request request, String preferredField, String replaceImageId) throws Exception {
        MultipartData parts = parseMultipartData(request);
        MultipartFile file = parts.file;
        if (file == null && !"file".equals(preferredField)) file = parts.files.get("file");
        if (file == null) file = parts.files.get(preferredField);
        if (file == null || file.data == null || file.data.length == 0) {
            return new JSONObject().put("_status", 400).put("detail", "No file provided");
        }

        String ext = safeGalleryExtension(file.filename, file.contentType);
        if (ext.isEmpty()) {
            return new JSONObject().put("_status", 400).put("detail", "Unsupported file type");
        }
        String fileHash = sha256Hex(file.data);
        JSONArray images = loadArray(PREF_GALLERY_IMAGES);
        if (replaceImageId == null) {
            for (int i = 0; i < images.length(); i++) {
                JSONObject existing = images.optJSONObject(i);
                if (existing == null || !existing.optBoolean("is_active", true)) continue;
                if (!fileHash.equals(existing.optString("file_hash", ""))) continue;
                return new JSONObject()
                        .put("ok", false)
                        .put("duplicate", true)
                        .put("filename", existing.optString("filename"))
                        .put("id", existing.optString("id"))
                        .put("message", "Duplicate photo skipped");
            }
        }

        File dir = galleryDir();
        String filename = UUID.randomUUID().toString().replace("-", "").substring(0, 12) + "." + ext;
        File dst = new File(dir, filename);
        try (FileOutputStream fos = new FileOutputStream(dst)) {
            fos.write(file.data);
        }

        long now = System.currentTimeMillis();
        String iso = isoTimestamp(now);
        String baseName = file.filename == null || file.filename.trim().isEmpty()
                ? filename
                : file.filename.trim();
        int dot = baseName.lastIndexOf('.');
        if (dot > 0) baseName = baseName.substring(0, dot);
        String albumId = valueOr(parts.fields.get("album_id"), "").trim();
        Object albumValue = albumId.isEmpty() ? JSONObject.NULL : albumId;

        if (replaceImageId != null) {
            for (int i = 0; i < images.length(); i++) {
                JSONObject image = images.optJSONObject(i);
                if (image == null || !replaceImageId.equals(image.optString("id"))) continue;
                deleteGalleryFile(image.optString("filename", ""));
                image.put("filename", filename);
                image.put("url", "/api/generated-image/" + filename);
                image.put("file_hash", fileHash);
                image.put("file_size", file.data.length);
                image.put("updated_at", iso);
                images.put(i, image);
                saveArray(PREF_GALLERY_IMAGES, images);
                return new JSONObject().put("ok", true).put("filename", filename).put("id", replaceImageId);
            }
            deleteGalleryFile(filename);
            return new JSONObject().put("_status", 404).put("detail", "Image not found");
        }

        String id = UUID.randomUUID().toString();
        JSONObject image = new JSONObject()
                .put("id", id)
                .put("filename", filename)
                .put("url", "/api/generated-image/" + filename)
                .put("prompt", baseName)
                .put("model", "imported")
                .put("size", JSONObject.NULL)
                .put("quality", JSONObject.NULL)
                .put("tags", "")
                .put("ai_tags", "")
                .put("user_tags", "")
                .put("session_id", JSONObject.NULL)
                .put("session_name", JSONObject.NULL)
                .put("album_id", albumValue)
                .put("is_active", true)
                .put("favorite", false)
                .put("taken_at", JSONObject.NULL)
                .put("camera", JSONObject.NULL)
                .put("gps", JSONObject.NULL)
                .put("width", JSONObject.NULL)
                .put("height", JSONObject.NULL)
                .put("file_size", file.data.length)
                .put("created_at", iso)
                .put("updated_at", iso)
                .put("owner", "mobile")
                .put("file_hash", fileHash);
        images.put(image);
        saveArray(PREF_GALLERY_IMAGES, images);
        return new JSONObject().put("ok", true).put("filename", filename).put("id", id);
    }

    private JSONObject listGalleryImages(Request request) throws Exception {
        String search = valueOr(request.query.get("search"), "").trim().toLowerCase(Locale.US);
        String tag = valueOr(request.query.get("tag"), "").trim().toLowerCase(Locale.US);
        String model = valueOr(request.query.get("model"), "").trim();
        String album = valueOr(request.query.get("album"), "").trim();
        boolean favorites = "true".equalsIgnoreCase(valueOr(request.query.get("favorites"), ""));
        String sort = valueOr(request.query.get("sort"), "recent");
        int offset = Math.max(0, parseInt(request.query.get("offset"), 0));
        int limit = Math.max(1, Math.min(100, parseInt(request.query.get("limit"), 24)));

        JSONArray images = loadArray(PREF_GALLERY_IMAGES);
        List<JSONObject> filtered = new ArrayList<>();
        List<String> tags = new ArrayList<>();
        List<String> models = new ArrayList<>();
        for (int i = 0; i < images.length(); i++) {
            JSONObject image = images.optJSONObject(i);
            if (image == null || !image.optBoolean("is_active", true)) continue;
            collectGalleryTokens(tags, image.optString("tags", ""));
            collectGalleryTokens(tags, image.optString("ai_tags", ""));
            String imageModel = image.optString("model", "");
            if (!imageModel.isEmpty() && !models.contains(imageModel)) models.add(imageModel);
            if (favorites && !image.optBoolean("favorite", false)) continue;
            if (!model.isEmpty() && !model.equals(imageModel)) continue;
            if (!album.isEmpty() && !album.equals(image.optString("album_id", ""))) continue;
            if (!search.isEmpty()) {
                String haystack = (
                        image.optString("prompt", "") + " " +
                        image.optString("tags", "") + " " +
                        image.optString("ai_tags", "")
                ).toLowerCase(Locale.US);
                if (!haystack.contains(search)) continue;
            }
            if (!tag.isEmpty()) {
                String haystack = (image.optString("tags", "") + "," + image.optString("ai_tags", "")).toLowerCase(Locale.US);
                boolean allMatch = true;
                for (String one : tag.split(",")) {
                    one = one.trim();
                    if (!one.isEmpty() && !haystack.contains(one)) {
                        allMatch = false;
                        break;
                    }
                }
                if (!allMatch) continue;
            }
            filtered.add(image);
        }

        if ("shuffle".equals(sort)) {
            long seed = parseLong(request.query.get("seed"), 0L);
            Collections.shuffle(filtered, new Random(seed));
        } else {
            Collections.sort(filtered, (a, b) -> {
                int cmp = Long.compare(galleryTimestamp(a), galleryTimestamp(b));
                return "oldest".equals(sort) ? cmp : -cmp;
            });
        }

        JSONArray items = new JSONArray();
        for (int i = offset; i < Math.min(filtered.size(), offset + limit); i++) {
            items.put(galleryImageToClient(filtered.get(i)));
        }
        Collections.sort(tags);
        Collections.sort(models);
        return new JSONObject()
                .put("items", items)
                .put("total", filtered.size())
                .put("total_tagged", countGalleryTagged(filtered))
                .put("tags", new JSONArray(tags))
                .put("models", new JSONArray(models));
    }

    private JSONObject listGalleryTags() throws Exception {
        JSONArray images = loadArray(PREF_GALLERY_IMAGES);
        List<String> tags = new ArrayList<>();
        for (int i = 0; i < images.length(); i++) {
            JSONObject image = images.optJSONObject(i);
            if (image == null || !image.optBoolean("is_active", true)) continue;
            collectGalleryTokens(tags, image.optString("tags", ""));
            collectGalleryTokens(tags, image.optString("ai_tags", ""));
        }
        Collections.sort(tags);
        return new JSONObject().put("tags", new JSONArray(tags));
    }

    private JSONObject listGalleryAlbums() throws Exception {
        JSONArray albums = loadArray(PREF_GALLERY_ALBUMS);
        JSONArray images = loadArray(PREF_GALLERY_IMAGES);
        JSONArray out = new JSONArray();
        for (int i = albums.length() - 1; i >= 0; i--) {
            JSONObject album = albums.optJSONObject(i);
            if (album == null) continue;
            String id = album.optString("id", "");
            int count = 0;
            String coverUrl = null;
            String coverId = album.optString("cover_id", "");
            for (int j = 0; j < images.length(); j++) {
                JSONObject image = images.optJSONObject(j);
                if (image == null || !image.optBoolean("is_active", true)) continue;
                if (!id.equals(image.optString("album_id", ""))) continue;
                count++;
                if (coverUrl == null || (!coverId.isEmpty() && coverId.equals(image.optString("id", "")))) {
                    coverUrl = "/api/generated-image/" + image.optString("filename", "");
                }
            }
            out.put(new JSONObject()
                    .put("id", id)
                    .put("name", album.optString("name", "Album"))
                    .put("description", album.optString("description", ""))
                    .put("cover_url", coverUrl == null ? JSONObject.NULL : coverUrl)
                    .put("count", count)
                    .put("created_at", album.opt("created_at")));
        }
        return new JSONObject().put("albums", out);
    }

    private JSONObject createGalleryAlbum(JSONObject body) throws Exception {
        String name = jsonString(body, "name", "").trim();
        if (name.isEmpty()) return new JSONObject().put("_status", 400).put("detail", "Album name required");
        String id = UUID.randomUUID().toString();
        JSONObject album = new JSONObject()
                .put("id", id)
                .put("name", name)
                .put("description", jsonString(body, "description", ""))
                .put("cover_id", JSONObject.NULL)
                .put("created_at", isoTimestamp(System.currentTimeMillis()))
                .put("owner", "mobile");
        JSONArray albums = loadArray(PREF_GALLERY_ALBUMS);
        albums.put(album);
        saveArray(PREF_GALLERY_ALBUMS, albums);
        return new JSONObject().put("ok", true).put("id", id).put("name", name);
    }

    private JSONObject updateGalleryAlbum(String albumId, JSONObject body) throws Exception {
        JSONArray albums = loadArray(PREF_GALLERY_ALBUMS);
        for (int i = 0; i < albums.length(); i++) {
            JSONObject album = albums.optJSONObject(i);
            if (album == null || !albumId.equals(album.optString("id", ""))) continue;
            if (body.has("name")) album.put("name", jsonString(body, "name", album.optString("name", "Album")).trim());
            if (body.has("description")) album.put("description", jsonString(body, "description", ""));
            if (body.has("cover_id")) album.put("cover_id", body.isNull("cover_id") ? JSONObject.NULL : body.optString("cover_id", ""));
            albums.put(i, album);
            saveArray(PREF_GALLERY_ALBUMS, albums);
            return new JSONObject().put("ok", true).put("id", albumId).put("name", album.optString("name", "Album"));
        }
        return new JSONObject().put("_status", 404).put("detail", "Album not found");
    }

    private JSONObject deleteGalleryAlbum(String albumId) throws Exception {
        JSONArray albums = loadArray(PREF_GALLERY_ALBUMS);
        JSONArray kept = new JSONArray();
        boolean deleted = false;
        for (int i = 0; i < albums.length(); i++) {
            JSONObject album = albums.optJSONObject(i);
            if (album != null && albumId.equals(album.optString("id", ""))) {
                deleted = true;
                continue;
            }
            kept.put(album == null ? albums.opt(i) : album);
        }
        if (!deleted) return new JSONObject().put("_status", 404).put("detail", "Album not found");
        saveArray(PREF_GALLERY_ALBUMS, kept);

        JSONArray images = loadArray(PREF_GALLERY_IMAGES);
        for (int i = 0; i < images.length(); i++) {
            JSONObject image = images.optJSONObject(i);
            if (image != null && albumId.equals(image.optString("album_id", ""))) {
                image.put("album_id", JSONObject.NULL);
                images.put(i, image);
            }
        }
        saveArray(PREF_GALLERY_IMAGES, images);
        return new JSONObject().put("ok", true).put("deleted", true);
    }

    private JSONObject moveGalleryImagesForAlbum(String albumId, JSONObject body, boolean add) throws Exception {
        JSONArray ids = body.optJSONArray("ids");
        if (ids == null) return new JSONObject().put("_status", 400).put("detail", "ids must be a list");
        JSONArray images = loadArray(PREF_GALLERY_IMAGES);
        int count = 0;
        for (int i = 0; i < images.length(); i++) {
            JSONObject image = images.optJSONObject(i);
            if (image == null) continue;
            for (int j = 0; j < ids.length(); j++) {
                if (!image.optString("id", "").equals(ids.optString(j, ""))) continue;
                image.put("album_id", add ? albumId : JSONObject.NULL);
                image.put("updated_at", isoTimestamp(System.currentTimeMillis()));
                images.put(i, image);
                count++;
                break;
            }
        }
        saveArray(PREF_GALLERY_IMAGES, images);
        return new JSONObject().put("ok", true).put("count", count);
    }

    private JSONObject findGalleryImage(String id) throws Exception {
        JSONArray images = loadArray(PREF_GALLERY_IMAGES);
        for (int i = 0; i < images.length(); i++) {
            JSONObject image = images.optJSONObject(i);
            if (image != null && id.equals(image.optString("id", "")) && image.optBoolean("is_active", true)) return image;
        }
        return null;
    }

    private JSONObject updateGalleryImage(String id, JSONObject body) throws Exception {
        JSONArray images = loadArray(PREF_GALLERY_IMAGES);
        for (int i = 0; i < images.length(); i++) {
            JSONObject image = images.optJSONObject(i);
            if (image == null || !id.equals(image.optString("id", ""))) continue;
            if (body.has("tags")) {
                String tags = body.isNull("tags") ? "" : body.optString("tags", "");
                image.put("tags", tags);
                image.put("user_tags", tags);
            }
            if (body.has("favorite")) image.put("favorite", jsonBoolean(body, "favorite", image.optBoolean("favorite", false)));
            if (body.has("album_id")) {
                String albumId = body.isNull("album_id") ? "" : body.optString("album_id", "");
                image.put("album_id", albumId.trim().isEmpty() ? JSONObject.NULL : albumId.trim());
            }
            image.put("updated_at", isoTimestamp(System.currentTimeMillis()));
            images.put(i, image);
            saveArray(PREF_GALLERY_IMAGES, images);
            return galleryImageToClient(image);
        }
        return new JSONObject().put("_status", 404).put("detail", "Image not found");
    }

    private JSONObject toggleGalleryFavorite(String id) throws Exception {
        JSONArray images = loadArray(PREF_GALLERY_IMAGES);
        for (int i = 0; i < images.length(); i++) {
            JSONObject image = images.optJSONObject(i);
            if (image == null || !id.equals(image.optString("id", ""))) continue;
            boolean favorite = !image.optBoolean("favorite", false);
            image.put("favorite", favorite);
            image.put("updated_at", isoTimestamp(System.currentTimeMillis()));
            images.put(i, image);
            saveArray(PREF_GALLERY_IMAGES, images);
            return new JSONObject().put("ok", true).put("favorite", favorite);
        }
        return new JSONObject().put("_status", 404).put("detail", "Image not found");
    }

    private JSONObject renameGalleryImage(String id, JSONObject body) throws Exception {
        String name = jsonString(body, "name", "").trim();
        if (name.isEmpty()) return new JSONObject().put("_status", 400).put("detail", "Name cannot be empty");
        JSONArray images = loadArray(PREF_GALLERY_IMAGES);
        for (int i = 0; i < images.length(); i++) {
            JSONObject image = images.optJSONObject(i);
            if (image == null || !id.equals(image.optString("id", ""))) continue;
            image.put("prompt", name);
            image.put("updated_at", isoTimestamp(System.currentTimeMillis()));
            images.put(i, image);
            saveArray(PREF_GALLERY_IMAGES, images);
            return new JSONObject().put("ok", true).put("name", name);
        }
        return new JSONObject().put("_status", 404).put("detail", "Image not found");
    }

    private JSONObject deleteGalleryImage(String id) throws Exception {
        JSONArray images = loadArray(PREF_GALLERY_IMAGES);
        for (int i = 0; i < images.length(); i++) {
            JSONObject image = images.optJSONObject(i);
            if (image == null || !id.equals(image.optString("id", ""))) continue;
            image.put("is_active", false);
            image.put("updated_at", isoTimestamp(System.currentTimeMillis()));
            deleteGalleryFile(image.optString("filename", ""));
            images.put(i, image);
            saveArray(PREF_GALLERY_IMAGES, images);
            return new JSONObject().put("status", "deleted").put("id", id);
        }
        return new JSONObject().put("_status", 404).put("detail", "Image not found");
    }

    private JSONObject galleryStats() throws Exception {
        JSONArray images = loadArray(PREF_GALLERY_IMAGES);
        long size = 0;
        int total = 0;
        int favorites = 0;
        for (int i = 0; i < images.length(); i++) {
            JSONObject image = images.optJSONObject(i);
            if (image == null || !image.optBoolean("is_active", true)) continue;
            total++;
            size += Math.max(0, image.optLong("file_size", 0));
            if (image.optBoolean("favorite", false)) favorites++;
        }
        return new JSONObject()
                .put("total_photos", total)
                .put("total_size", size)
                .put("total_size_human", humanSize(size))
                .put("favorites", favorites)
                .put("albums", loadArray(PREF_GALLERY_ALBUMS).length());
    }

    private JSONObject galleryImageToClient(JSONObject image) throws Exception {
        String filename = image.optString("filename", "");
        return new JSONObject()
                .put("id", image.optString("id", ""))
                .put("filename", filename)
                .put("url", "/api/generated-image/" + filename)
                .put("prompt", image.optString("prompt", ""))
                .put("model", image.optString("model", "imported"))
                .put("size", jsonValueOrNull(image, "size"))
                .put("quality", jsonValueOrNull(image, "quality"))
                .put("tags", image.optString("tags", ""))
                .put("ai_tags", image.optString("ai_tags", ""))
                .put("user_tags", image.optString("user_tags", image.optString("tags", "")))
                .put("session_id", jsonValueOrNull(image, "session_id"))
                .put("session_name", jsonValueOrNull(image, "session_name"))
                .put("album_id", jsonValueOrNull(image, "album_id"))
                .put("is_active", image.optBoolean("is_active", true))
                .put("favorite", image.optBoolean("favorite", false))
                .put("taken_at", jsonValueOrNull(image, "taken_at"))
                .put("camera", jsonValueOrNull(image, "camera"))
                .put("gps", jsonValueOrNull(image, "gps"))
                .put("width", jsonValueOrNull(image, "width"))
                .put("height", jsonValueOrNull(image, "height"))
                .put("file_size", image.optLong("file_size", 0))
                .put("created_at", jsonValueOrNull(image, "created_at"))
                .put("updated_at", jsonValueOrNull(image, "updated_at"));
    }

    private void routeEndpoint(Request request, OutputStream out, String tail) throws Exception {
        String[] parts = tail.split("/");
        String id = parts[0];
        if ("DELETE".equals(request.method)) {
            deleteEndpoint(id);
            sendJson(out, 200, new JSONObject().put("deleted", true));
            return;
        }
        if ("POST".equals(request.method) && parts.length > 1 && "unload".equals(parts[1])) {
            JSONObject ep = findEndpoint(id);
            if (ep == null) {
                sendJson(out, 404, new JSONObject().put("detail", "Endpoint not found"));
                return;
            }
            JSONObject result = unloadEndpointModel(ep, parseForm(request));
            int status = result.optBoolean("ok")
                    ? 200
                    : (result.optBoolean("supported", true) ? 502 : 400);
            sendJson(out, status, result);
            return;
        }
        if ("GET".equals(request.method) && parts.length > 1 && "models".equals(parts[1])) {
            JSONObject ep = findEndpoint(id);
            sendJson(out, 200, endpointModels(ep));
            return;
        }
        if ("PATCH".equals(request.method) && parts.length > 1 && "models".equals(parts[1])) {
            sendJson(out, 200, new JSONObject().put("id", id).put("hidden_count", 0).put("pinned_count", 0));
            return;
        }
        if ("PATCH".equals(request.method)) {
            sendJson(out, 200, toggleEndpoint(id));
            return;
        }
        if ("GET".equals(request.method) && parts.length > 1 && "dependents".equals(parts[1])) {
            sendJson(out, 200, new JSONObject().put("dependents", new JSONArray()));
            return;
        }
        sendJson(out, 404, new JSONObject().put("detail", "Endpoint not found"));
    }

    private JSONObject createSession(Map<String, String> form) throws Exception {
        String id = UUID.randomUUID().toString();
        long now = System.currentTimeMillis();
        JSONObject session = new JSONObject()
                .put("id", id)
                .put("name", valueOr(form.get("name"), "New Chat"))
                .put("endpoint_url", valueOr(form.get("endpoint_url"), ""))
                .put("endpoint_id", valueOr(form.get("endpoint_id"), ""))
                .put("model", valueOr(form.get("model"), ""))
                .put("rag", false)
                .put("archived", false)
                .put("folder", JSONObject.NULL)
                .put("message_count", 0)
                .put("created_at", String.valueOf(now))
                .put("updated_at", String.valueOf(now))
                .put("last_message_at", String.valueOf(now))
                .put("history", new JSONArray());
        JSONArray sessions = loadArray(PREF_SESSIONS);
        sessions.put(session);
        saveArray(PREF_SESSIONS, sessions);
        return sessionSummary(session);
    }

    private JSONObject patchSession(String sid, Map<String, String> form) throws Exception {
        JSONArray sessions = loadArray(PREF_SESSIONS);
        for (int i = 0; i < sessions.length(); i++) {
            JSONObject s = sessions.getJSONObject(i);
            if (!sid.equals(s.optString("id"))) continue;
            if (form.containsKey("name")) s.put("name", valueOr(form.get("name"), s.optString("name")));
            if (form.containsKey("folder")) {
                String folder = valueOr(form.get("folder"), "");
                s.put("folder", folder.isEmpty() ? JSONObject.NULL : folder);
            }
            if (form.containsKey("model")) s.put("model", valueOr(form.get("model"), s.optString("model")));
            if (form.containsKey("endpoint_url")) s.put("endpoint_url", valueOr(form.get("endpoint_url"), s.optString("endpoint_url")));
            if (form.containsKey("endpoint_id")) s.put("endpoint_id", valueOr(form.get("endpoint_id"), s.optString("endpoint_id")));
            s.put("updated_at", String.valueOf(System.currentTimeMillis()));
            sessions.put(i, s);
            saveArray(PREF_SESSIONS, sessions);
            return sessionSummary(s);
        }
        return new JSONObject().put("id", sid);
    }

    private void deleteSession(String sid) throws Exception {
        JSONArray sessions = loadArray(PREF_SESSIONS);
        JSONArray kept = new JSONArray();
        for (int i = 0; i < sessions.length(); i++) {
            JSONObject s = sessions.getJSONObject(i);
            if (!sid.equals(s.optString("id"))) kept.put(s);
        }
        saveArray(PREF_SESSIONS, kept);
    }

    private JSONObject bulkDeleteSessions(Request request) throws Exception {
        JSONObject body = requestJson(request);
        JSONArray ids = body.optJSONArray("ids");
        int deleted = 0;
        if (ids != null) {
            for (int i = 0; i < ids.length(); i++) {
                String sid = ids.optString(i, "");
                if (sid.isEmpty()) continue;
                deleteSession(sid);
                deleted++;
            }
        }
        return new JSONObject().put("deleted", deleted);
    }

    private JSONObject sessionHistory(String sid) throws Exception {
        JSONObject session = getSessionById(sid);
        return new JSONObject()
                .put("id", sid)
                .put("model", session.optString("model"))
                .put("history", session.optJSONArray("history") == null ? new JSONArray() : session.optJSONArray("history"));
    }

    private JSONArray listSessionSummaries() throws Exception {
        JSONArray sessions = loadArray(PREF_SESSIONS);
        JSONArray out = new JSONArray();
        for (int i = sessions.length() - 1; i >= 0; i--) {
            out.put(sessionSummary(sessions.getJSONObject(i)));
        }
        return out;
    }

    private JSONObject sessionSummary(JSONObject s) throws Exception {
        return new JSONObject()
                .put("id", s.optString("id"))
                .put("name", s.optString("name", "New Chat"))
                .put("model", s.optString("model"))
                .put("endpoint_url", s.optString("endpoint_url"))
                .put("endpoint_id", s.optString("endpoint_id"))
                .put("rag", false)
                .put("archived", s.optBoolean("archived", false))
                .put("folder", s.opt("folder"))
                .put("total_tokens", 0)
                .put("is_important", false)
                .put("created_at", s.optString("created_at"))
                .put("updated_at", s.optString("updated_at"))
                .put("last_message_at", s.optString("last_message_at"))
                .put("has_documents", false)
                .put("has_images", false)
                .put("mode", "chat")
                .put("message_count", s.optInt("message_count", history(s).length()));
    }

    private JSONObject getSessionById(String sid) throws Exception {
        JSONArray sessions = loadArray(PREF_SESSIONS);
        for (int i = 0; i < sessions.length(); i++) {
            JSONObject s = sessions.getJSONObject(i);
            if (sid.equals(s.optString("id"))) return s;
        }
        return new JSONObject()
                .put("id", sid)
                .put("name", "New Chat")
                .put("history", new JSONArray());
    }

    private JSONObject documentsLibrary(Request request) throws Exception {
        String sort = valueOr(request.query.get("sort"), "recent").trim().toLowerCase(Locale.US);
        String search = valueOr(request.query.get("search"), "").trim().toLowerCase(Locale.US);
        String language = valueOr(request.query.get("language"), "").trim().toLowerCase(Locale.US);
        boolean archived = "true".equalsIgnoreCase(valueOr(request.query.get("archived"), "false"))
                || "1".equals(valueOr(request.query.get("archived"), "false"));
        int offset = Math.max(0, parseInt(request.query.get("offset"), 0));
        int limit = Math.max(1, Math.min(50, parseInt(valueOr(request.query.get("limit"), "50"), 50)));

        JSONArray documents = loadArray(PREF_DOCUMENTS);
        JSONArray sessions = loadArray(PREF_SESSIONS);
        List<JSONObject> filtered = new ArrayList<>();
        JSONObject languages = new JSONObject();
        List<String> sessionIds = new ArrayList<>();

        for (int i = 0; i < documents.length(); i++) {
            JSONObject doc = documents.optJSONObject(i);
            if (doc == null) continue;
            if (!doc.optBoolean("is_active", true)) continue;
            if (doc.optBoolean("archived", false) != archived) continue;
            String docLanguage = doc.optString("language", "text");
            if (!language.isEmpty() && !language.equals(docLanguage.toLowerCase(Locale.US))) continue;
            if (!search.isEmpty()) {
                String haystack = (doc.optString("title", "") + "\n" + doc.optString("current_content", "")).toLowerCase(Locale.US);
                boolean matches = true;
                for (String token : search.split("\\s+")) {
                    if (!token.isEmpty() && !haystack.contains(token)) {
                        matches = false;
                        break;
                    }
                }
                if (!matches) continue;
            }
            filtered.add(documentLibrarySummary(doc, sessions));
            languages.put(docLanguage, languages.optInt(docLanguage, 0) + 1);
            String sid = doc.optString("session_id", "");
            if (!sid.isEmpty() && !sessionIds.contains(sid)) sessionIds.add(sid);
        }

        Collections.sort(filtered, (a, b) -> {
            if ("oldest".equals(sort)) return Long.compare(documentTimestamp(a, "updated_at"), documentTimestamp(b, "updated_at"));
            if ("edits".equals(sort)) return Integer.compare(b.optInt("version_count", 1), a.optInt("version_count", 1));
            if ("alpha".equals(sort)) return a.optString("title", "").compareToIgnoreCase(b.optString("title", ""));
            return Long.compare(documentTimestamp(b, "updated_at"), documentTimestamp(a, "updated_at"));
        });

        JSONArray out = new JSONArray();
        int end = Math.min(filtered.size(), offset + limit);
        for (int i = offset; i < end; i++) out.put(filtered.get(i));
        return new JSONObject()
                .put("documents", out)
                .put("total", filtered.size())
                .put("offset", offset)
                .put("limit", limit)
                .put("languages", languages)
                .put("session_count", sessionIds.size())
                .put("mobile_standalone", true);
    }

    private JSONObject documentLibrarySummary(JSONObject doc, JSONArray sessions) throws Exception {
        String content = doc.optString("current_content", "");
        String preview = content.length() > 320 ? content.substring(0, 320) : content;
        String sid = doc.optString("session_id", "");
        return new JSONObject()
                .put("id", doc.optString("id"))
                .put("session_id", sid.isEmpty() ? JSONObject.NULL : sid)
                .put("session_name", mobileSessionName(sessions, sid))
                .put("title", doc.optString("title", "Untitled"))
                .put("language", doc.optString("language", "text"))
                .put("preview", preview)
                .put("version_count", doc.optInt("version_count", 1))
                .put("created_at", doc.optString("created_at", ""))
                .put("updated_at", doc.optString("updated_at", ""))
                .put("archived", doc.optBoolean("archived", false));
    }

    private String mobileSessionName(JSONArray sessions, String sid) {
        if (sid == null || sid.isEmpty()) return "";
        for (int i = 0; i < sessions.length(); i++) {
            JSONObject s = sessions.optJSONObject(i);
            if (s != null && sid.equals(s.optString("id"))) return s.optString("name", "");
        }
        return "";
    }

    private long documentTimestamp(JSONObject doc, String field) {
        Object rawValue = doc == null ? null : doc.opt(field);
        if (rawValue instanceof Number) return ((Number) rawValue).longValue();
        String raw = String.valueOf(rawValue == null || rawValue == JSONObject.NULL ? "" : rawValue);
        long numeric = parseLong(raw, Long.MIN_VALUE);
        if (numeric != Long.MIN_VALUE) return numeric;
        try {
            SimpleDateFormat fmt = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US);
            fmt.setTimeZone(TimeZone.getTimeZone("UTC"));
            Date parsed = fmt.parse(raw);
            return parsed == null ? 0L : parsed.getTime();
        } catch (Exception ignored) {
            return 0L;
        }
    }

    private JSONObject mobilePersonalList() throws Exception {
        JSONArray dirs = loadArray(PREF_PERSONAL_DIRECTORIES);
        return new JSONObject()
                .put("directories", dirs)
                .put("files", new JSONArray())
                .put("base_directory", appContext == null ? "" : appContext.getFilesDir().getAbsolutePath())
                .put("allowed_directory_root", "Connect to PC for PC filesystem folders")
                .put("rag_available", false)
                .put("mcp_available", true)
                .put("message", "Android standalone stores folder paths locally. Connect to the PC backend to index PC folder contents.")
                .put("mobile_standalone", true);
    }

    private JSONObject mobileAddPersonalDirectory(Request request) throws Exception {
        Map<String, String> form = parseForm(request);
        String directory = valueOr(form.get("directory"), jsonString(requestJson(request), "directory", "")).trim();
        if (directory.isEmpty()) {
            return new JSONObject().put("success", false).put("detail", "Directory path is required").put("mobile_standalone", true);
        }
        if (directory.length() > 500) directory = directory.substring(0, 500);
        JSONArray dirs = loadArray(PREF_PERSONAL_DIRECTORIES);
        for (int i = 0; i < dirs.length(); i++) {
            if (directory.equals(dirs.optString(i))) {
                return new JSONObject()
                        .put("success", true)
                        .put("indexed_count", 0)
                        .put("directory", directory)
                        .put("message", "Folder path already saved on Android. Connect to PC for RAG indexing.")
                        .put("mobile_standalone", true);
            }
        }
        dirs.put(directory);
        saveArray(PREF_PERSONAL_DIRECTORIES, dirs);
        return new JSONObject()
                .put("success", true)
                .put("indexed_count", 0)
                .put("directory", directory)
                .put("message", "Folder path saved on Android. Connect to PC for RAG indexing.")
                .put("mobile_standalone", true);
    }

    private JSONObject mobileRemovePersonalDirectory(String directory) throws Exception {
        String target = valueOr(directory, "").trim();
        JSONArray dirs = loadArray(PREF_PERSONAL_DIRECTORIES);
        JSONArray kept = new JSONArray();
        boolean removed = false;
        for (int i = 0; i < dirs.length(); i++) {
            String path = dirs.optString(i);
            if (!removed && path.equals(target)) {
                removed = true;
                continue;
            }
            kept.put(path);
        }
        saveArray(PREF_PERSONAL_DIRECTORIES, kept);
        return new JSONObject()
                .put("success", true)
                .put("removed", removed)
                .put("directory", target)
                .put("message", removed ? "Folder path removed" : "Folder path was not saved")
                .put("mobile_standalone", true);
    }

    private void routeWorkspace(Request request, OutputStream out, String tail) throws Exception {
        if (tail == null) tail = "";
        if (tail.startsWith("/")) tail = tail.substring(1);
        try {
            if ("roots".equals(tail) && "GET".equals(request.method)) {
                sendJson(out, 200, mobileWorkspaceRoots());
                return;
            }
            if ("browse".equals(tail) && "GET".equals(request.method)) {
                sendJson(out, 200, mobileWorkspaceBrowse(valueOr(request.query.get("path"), "")));
                return;
            }
            if ("vet".equals(tail) && "GET".equals(request.method)) {
                File resolved = mobileVetWorkspace(valueOr(request.query.get("path"), ""));
                sendJson(out, 200, new JSONObject()
                        .put("ok", resolved != null)
                        .put("path", resolved == null ? JSONObject.NULL : resolved.getCanonicalPath())
                        .put("mobile_standalone", true));
                return;
            }
            if ("files/list".equals(tail) && "GET".equals(request.method)) {
                sendJson(out, 200, mobileWorkspaceListFiles(request));
                return;
            }
            if ("files/read".equals(tail) && "GET".equals(request.method)) {
                sendJson(out, 200, mobileWorkspaceReadFile(request));
                return;
            }
            if ("files/write".equals(tail) && "POST".equals(request.method)) {
                sendJson(out, 200, mobileWorkspaceWriteFile(requestJson(request)));
                return;
            }
            if ("files/mkdir".equals(tail) && "POST".equals(request.method)) {
                sendJson(out, 200, mobileWorkspaceMkdir(requestJson(request)));
                return;
            }
            if ("files/rename".equals(tail) && "POST".equals(request.method)) {
                sendJson(out, 200, mobileWorkspaceRename(requestJson(request)));
                return;
            }
            if ("files/delete".equals(tail) && "DELETE".equals(request.method)) {
                sendJson(out, 200, mobileWorkspaceDelete(request));
                return;
            }
            sendJson(out, 404, new JSONObject().put("detail", "Mobile workspace route not implemented"));
        } catch (MobileHttpException ex) {
            sendJson(out, ex.status, new JSONObject()
                    .put("detail", ex.getMessage())
                    .put("mobile_standalone", true));
        }
    }

    private JSONObject mobileWorkspaceRoots() throws Exception {
        File documents = mobilePublicWorkspaceDir(Environment.DIRECTORY_DOCUMENTS);
        File downloads = mobilePublicWorkspaceDir(Environment.DIRECTORY_DOWNLOADS);
        File base = mobileWorkspaceBaseDir();
        JSONArray roots = new JSONArray();
        if (documents != null) roots.put(mobileWorkspaceRootEntry("documents", "Documents", documents));
        if (downloads != null) roots.put(mobileWorkspaceRootEntry("downloads", "Downloads", downloads));
        roots.put(mobileWorkspaceRootEntry("workspace", "App Workspace", base));
        roots.put(mobileWorkspaceRootEntry("scratch", "Scratch", mobileWorkspaceChildDir("Scratch")));
        return new JSONObject()
                .put("default_path", mobileDefaultWorkspaceRoot().getCanonicalPath())
                .put("roots", roots)
                .put("mobile_standalone", true);
    }

    private JSONObject mobileWorkspaceRootEntry(String key, String label, File dir) throws Exception {
        return new JSONObject()
                .put("key", key)
                .put("label", label)
                .put("path", dir.getCanonicalPath())
                .put("selectable", true)
                .put("public_android_storage", mobileIsPublicExternalWorkspace(dir))
                .put("mobile_standalone", true);
    }

    private JSONObject mobileWorkspaceBrowse(String rawPath) throws Exception {
        File target = mobileResolveAllowedWorkspacePath(rawPath, true);
        if (!target.isDirectory()) target = mobileDefaultWorkspaceRoot();

        JSONArray dirs = new JSONArray();
        boolean truncated = false;
        File[] children = target.listFiles();
        if (children == null) throw mobileUnreadableWorkspace(target);
        if (children != null) {
            List<File> sorted = new ArrayList<>(Arrays.asList(children));
            Collections.sort(sorted, (a, b) -> a.getName().compareToIgnoreCase(b.getName()));
            for (File child : sorted) {
                if (dirs.length() >= MAX_MOBILE_WORKSPACE_DIRS) {
                    truncated = true;
                    break;
                }
                if (!child.isDirectory() || child.getName().startsWith(".")) continue;
                if (mobileIsDeprecatedPrivateWorkspaceFolder(child)) continue;
                dirs.put(new JSONObject()
                        .put("name", child.getName())
                        .put("path", child.getCanonicalPath()));
            }
        }

        File containingRoot = mobileContainingWorkspaceRoot(target);
        File parent = target.getParentFile();
        String parentPath = null;
        if (containingRoot != null
                && parent != null
                && mobileIsInside(containingRoot, parent)
                && !target.getCanonicalPath().equals(containingRoot.getCanonicalPath())) {
            parentPath = parent.getCanonicalPath();
        }
        return new JSONObject()
                .put("path", target.getCanonicalPath())
                .put("parent", parentPath == null ? JSONObject.NULL : parentPath)
                .put("dirs", dirs)
                .put("truncated", truncated)
                .put("selectable", true)
                .put("mobile_standalone", true);
    }

    private JSONObject mobileWorkspaceListFiles(Request request) throws Exception {
        File root = mobileWorkspaceRootOrThrow(valueOr(request.query.get("workspace"), ""));
        String rawPath = valueOr(request.query.get("path"), "");
        int limit = Math.max(1, Math.min(MAX_MOBILE_WORKSPACE_FILE_ENTRIES,
                parseInt(valueOr(request.query.get("limit"), String.valueOf(DEFAULT_MOBILE_WORKSPACE_FILE_ENTRIES)),
                        DEFAULT_MOBILE_WORKSPACE_FILE_ENTRIES)));
        File target = mobileResolveInside(root, rawPath, true);
        if (!target.isDirectory()) throw mobileHttp(400, "Path is not a folder");

        JSONArray entries = new JSONArray();
        boolean truncated = false;
        File[] children = target.listFiles();
        if (children == null) throw mobileUnreadableWorkspace(target);
        if (children != null) {
            List<File> sorted = new ArrayList<>(Arrays.asList(children));
            Collections.sort(sorted, (a, b) -> {
                if (a.isDirectory() != b.isDirectory()) return a.isDirectory() ? -1 : 1;
                return a.getName().compareToIgnoreCase(b.getName());
            });
            for (File child : sorted) {
                if (entries.length() >= limit) {
                    truncated = true;
                    break;
                }
                if (child.getName().startsWith(".")) continue;
                if (mobileIsDeprecatedPrivateWorkspaceFolder(child)) continue;
                entries.put(mobileWorkspaceEntryInfo(root, child));
            }
        }

        String parent = "";
        if (!target.getCanonicalPath().equals(root.getCanonicalPath())) {
            File parentFile = target.getParentFile();
            if (parentFile != null && mobileIsInside(root, parentFile)) {
                parent = mobileWorkspaceRelPath(root, parentFile);
            }
        }
        return new JSONObject()
                .put("workspace", root.getCanonicalPath())
                .put("path", mobileWorkspaceRelPath(root, target))
                .put("parent", parent)
                .put("entries", entries)
                .put("truncated", truncated)
                .put("max_entries", limit)
                .put("mobile_standalone", true);
    }

    private JSONObject mobileWorkspaceReadFile(Request request) throws Exception {
        File root = mobileWorkspaceRootOrThrow(valueOr(request.query.get("workspace"), ""));
        File target = mobileResolveInside(root, valueOr(request.query.get("path"), ""), false);
        if (!target.isFile()) throw mobileHttp(400, "Path is not a file");
        if (target.length() > MAX_MOBILE_WORKSPACE_TEXT_BYTES) {
            throw mobileHttp(413, "File is larger than " + MAX_MOBILE_WORKSPACE_TEXT_BYTES + " bytes");
        }
        if (!mobileWorkspaceEditableText(target)) {
            throw mobileHttp(415, "File is not an editable text file");
        }
        byte[] data;
        try (InputStream in = new FileInputStream(target)) {
            data = readBytes(in);
        }
        return new JSONObject()
                .put("workspace", root.getCanonicalPath())
                .put("path", mobileWorkspaceRelPath(root, target))
                .put("name", target.getName())
                .put("content", new String(data, StandardCharsets.UTF_8))
                .put("size", target.length())
                .put("modified", mobileWorkspaceModifiedSeconds(target))
                .put("truncated", false)
                .put("mobile_standalone", true);
    }

    private JSONObject mobileWorkspaceWriteFile(JSONObject body) throws Exception {
        File root = mobileWorkspaceRootOrThrow(jsonString(body, "workspace", ""));
        File target = mobileResolveInside(root, jsonString(body, "path", ""), false);
        if (target.isDirectory()) throw mobileHttp(400, "Path is a folder");

        String content = jsonString(body, "content", "");
        byte[] encoded = content.getBytes(StandardCharsets.UTF_8);
        if (encoded.length > MAX_MOBILE_WORKSPACE_TEXT_BYTES) {
            throw mobileHttp(413, "Content is larger than " + MAX_MOBILE_WORKSPACE_TEXT_BYTES + " bytes");
        }
        File parent = target.getParentFile();
        if (parent == null || !mobileIsInside(root, parent)) throw mobileHttp(400, "Parent folder is outside the workspace");
        if (!parent.isDirectory()) {
            if (jsonBoolean(body, "create_parents", false)) {
                if (!parent.mkdirs() && !parent.isDirectory()) throw mobileHttp(400, "Could not create parent folder");
            } else {
                throw mobileHttp(400, "Parent folder does not exist");
            }
        }
        if (body.has("previous_mtime") && !body.isNull("previous_mtime") && target.exists()) {
            double previous = body.optDouble("previous_mtime", -1);
            if (previous >= 0 && Math.abs(mobileWorkspaceModifiedSeconds(target) - previous) > 0.01) {
                throw mobileHttp(409, "File changed on disk; reload before saving");
            }
        }
        try (FileOutputStream fos = new FileOutputStream(target)) {
            fos.write(encoded);
        }
        return new JSONObject()
                .put("ok", true)
                .put("workspace", root.getCanonicalPath())
                .put("path", mobileWorkspaceRelPath(root, target))
                .put("name", target.getName())
                .put("size", target.length())
                .put("modified", mobileWorkspaceModifiedSeconds(target))
                .put("mobile_standalone", true);
    }

    private JSONObject mobileWorkspaceMkdir(JSONObject body) throws Exception {
        File root = mobileWorkspaceRootOrThrow(jsonString(body, "workspace", ""));
        File target = mobileResolveInside(root, jsonString(body, "path", ""), false);
        if (target.exists() && !target.isDirectory()) throw mobileHttp(400, "A file already exists at that path");
        if (!target.mkdirs() && !target.isDirectory()) throw mobileHttp(400, "Could not create folder");
        return new JSONObject()
                .put("ok", true)
                .put("workspace", root.getCanonicalPath())
                .put("path", mobileWorkspaceRelPath(root, target))
                .put("mobile_standalone", true);
    }

    private JSONObject mobileWorkspaceRename(JSONObject body) throws Exception {
        File root = mobileWorkspaceRootOrThrow(jsonString(body, "workspace", ""));
        File source = mobileResolveInside(root, jsonString(body, "path", ""), false);
        File target = mobileResolveInside(root, jsonString(body, "new_path", ""), false);
        if (!source.exists()) throw mobileHttp(404, "Path not found");
        if (source.getCanonicalPath().equals(root.getCanonicalPath())) throw mobileHttp(400, "Cannot rename the workspace root");
        if (target.exists()) throw mobileHttp(409, "Target already exists");
        File parent = target.getParentFile();
        if (parent == null || !parent.isDirectory()) throw mobileHttp(400, "Target parent folder does not exist");
        if (!source.renameTo(target)) throw mobileHttp(400, "Could not rename path");
        return new JSONObject()
                .put("ok", true)
                .put("workspace", root.getCanonicalPath())
                .put("path", mobileWorkspaceRelPath(root, target))
                .put("mobile_standalone", true);
    }

    private JSONObject mobileWorkspaceDelete(Request request) throws Exception {
        File root = mobileWorkspaceRootOrThrow(valueOr(request.query.get("workspace"), ""));
        File target = mobileResolveInside(root, valueOr(request.query.get("path"), ""), false);
        if (target.getCanonicalPath().equals(root.getCanonicalPath())) throw mobileHttp(400, "Cannot delete the workspace root");
        if (!target.exists()) throw mobileHttp(404, "Path not found");
        boolean recursive = "true".equalsIgnoreCase(valueOr(request.query.get("recursive"), ""))
                || "1".equals(valueOr(request.query.get("recursive"), ""));
        if (target.isDirectory() && !recursive) {
            String[] children = target.list();
            if (children != null && children.length > 0) throw mobileHttp(400, "Folder is not empty");
        }
        mobileDeleteRecursively(target);
        return new JSONObject()
                .put("ok", true)
                .put("workspace", root.getCanonicalPath())
                .put("path", mobileWorkspaceRelPath(root, target))
                .put("mobile_standalone", true);
    }

    private File mobileWorkspaceBaseDir() throws Exception {
        File dir = new File(appContext.getFilesDir(), "workspace").getCanonicalFile();
        if (!dir.exists() && !dir.mkdirs()) throw mobileHttp(500, "Could not create Android workspace folder");
        mobileWorkspaceChildDir("Scratch");
        return dir;
    }

    private File mobileWorkspaceChildDir(String name) throws Exception {
        File dir = new File(new File(appContext.getFilesDir(), "workspace"), name).getCanonicalFile();
        if (!dir.exists() && !dir.mkdirs()) throw mobileHttp(500, "Could not create Android workspace folder: " + name);
        return dir;
    }

    private File mobilePrivateWorkspaceChildPath(String name) throws Exception {
        return new File(new File(appContext.getFilesDir(), "workspace"), name).getCanonicalFile();
    }

    private File mobilePublicWorkspaceDir(String type) throws Exception {
        File dir = Environment.getExternalStoragePublicDirectory(type);
        if (dir == null) return null;
        File resolved = dir.getCanonicalFile();
        if (!resolved.exists()) {
            resolved.mkdirs();
        }
        return resolved.isDirectory() ? resolved : null;
    }

    private File mobileDefaultWorkspaceRoot() throws Exception {
        File documents = mobilePublicWorkspaceDir(Environment.DIRECTORY_DOCUMENTS);
        return documents != null ? documents : mobileWorkspaceBaseDir();
    }

    private List<File> mobileAllowedWorkspaceRoots() throws Exception {
        List<File> roots = new ArrayList<>();
        mobileAddAllowedWorkspaceRoot(roots, mobilePublicWorkspaceDir(Environment.DIRECTORY_DOCUMENTS));
        mobileAddAllowedWorkspaceRoot(roots, mobilePublicWorkspaceDir(Environment.DIRECTORY_DOWNLOADS));
        mobileAddAllowedWorkspaceRoot(roots, mobileWorkspaceBaseDir());
        mobileAddAllowedWorkspaceRoot(roots, mobileWorkspaceChildDir("Scratch"));
        return roots;
    }

    private void mobileAddAllowedWorkspaceRoot(List<File> roots, File root) throws IOException {
        if (root == null || !root.isDirectory()) return;
        File resolved = root.getCanonicalFile();
        for (File existing : roots) {
            if (existing.getCanonicalPath().equals(resolved.getCanonicalPath())) return;
        }
        roots.add(resolved);
    }

    private File mobileResolveAllowedWorkspacePath(String rawPath, boolean allowDefaultRoot) throws Exception {
        String raw = valueOr(rawPath, "").trim();
        if (raw.isEmpty()) {
            if (allowDefaultRoot) return mobileDefaultWorkspaceRoot();
            throw mobileHttp(400, "Path is required");
        }
        raw = raw.replace('\\', File.separatorChar);
        File candidate = new File(raw);
        if (!candidate.isAbsolute()) candidate = new File(mobileDefaultWorkspaceRoot(), raw);
        File resolved = candidate.getCanonicalFile();
        if (mobileIsDeprecatedPrivateWorkspaceFolder(resolved)) {
            throw mobileHttp(410, MOBILE_DEPRECATED_PRIVATE_WORKSPACE_DETAIL);
        }
        if (mobileContainingWorkspaceRoot(resolved) == null) throw mobileHttp(403, "Path is outside the Android workspace");
        return resolved;
    }

    private File mobileContainingWorkspaceRoot(File target) throws Exception {
        if (target == null) return null;
        File resolved = target.getCanonicalFile();
        File best = null;
        for (File root : mobileAllowedWorkspaceRoots()) {
            if (!mobileIsInside(root, resolved)) continue;
            if (best == null || root.getCanonicalPath().length() > best.getCanonicalPath().length()) {
                best = root;
            }
        }
        return best;
    }

    private boolean mobileIsDeprecatedPrivateWorkspaceFolder(File target) {
        try {
            if (target == null) return false;
            File documents = mobilePrivateWorkspaceChildPath("Documents");
            File downloads = mobilePrivateWorkspaceChildPath("Downloads");
            return mobileIsInside(documents, target) || mobileIsInside(downloads, target);
        } catch (Exception ignored) {
            return false;
        }
    }

    private boolean mobileIsPublicExternalWorkspace(File target) {
        try {
            if (target == null) return false;
            File external = Environment.getExternalStorageDirectory();
            if (external == null) return false;
            File externalRoot = external.getCanonicalFile();
            File appExternal = appContext.getExternalFilesDir(null);
            File resolved = target.getCanonicalFile();
            if (!mobileIsInside(externalRoot, resolved)) return false;
            return appExternal == null || !mobileIsInside(appExternal.getCanonicalFile(), resolved);
        } catch (Exception ignored) {
            return false;
        }
    }

    private MobileHttpException mobileUnreadableWorkspace(File target) {
        if (mobileIsPublicExternalWorkspace(target)) return mobileHttp(403, MOBILE_PUBLIC_WORKSPACE_ACCESS_DETAIL);
        return mobileHttp(403, "Android cannot read this workspace folder.");
    }

    private File mobileVetWorkspace(String rawPath) throws Exception {
        String raw = valueOr(rawPath, "").trim();
        if (raw.isEmpty()) return null;
        File target = mobileResolveAllowedWorkspacePath(raw, false);
        return target.isDirectory() ? target.getCanonicalFile() : null;
    }

    private File mobileWorkspaceRootOrThrow(String rawWorkspace) throws Exception {
        File root = mobileVetWorkspace(rawWorkspace);
        if (root == null) throw mobileHttp(400, "Workspace is not usable");
        return root;
    }

    private File mobileResolveInside(File root, String rawPath, boolean allowRoot) throws Exception {
        String raw = valueOr(rawPath, "").trim();
        if (raw.isEmpty()) {
            if (allowRoot) return root.getCanonicalFile();
            throw mobileHttp(400, "Path is required");
        }
        raw = raw.replace('\\', File.separatorChar);
        File candidate = new File(raw);
        if (!candidate.isAbsolute()) candidate = new File(root, raw);
        File resolved = candidate.getCanonicalFile();
        if (!mobileIsInside(root, resolved)) throw mobileHttp(403, "Path is outside the Android workspace");
        if (mobileIsDeprecatedPrivateWorkspaceFolder(resolved)) throw mobileHttp(410, MOBILE_DEPRECATED_PRIVATE_WORKSPACE_DETAIL);
        return resolved;
    }

    private boolean mobileIsInside(File root, File target) throws IOException {
        String rootPath = root.getCanonicalFile().getPath();
        String targetPath = target.getCanonicalFile().getPath();
        return targetPath.equals(rootPath) || targetPath.startsWith(rootPath + File.separator);
    }

    private String mobileWorkspaceRelPath(File root, File target) throws IOException {
        String rootPath = root.getCanonicalFile().getPath();
        String targetPath = target.getCanonicalFile().getPath();
        if (targetPath.equals(rootPath)) return "";
        if (!targetPath.startsWith(rootPath + File.separator)) return "";
        return targetPath.substring(rootPath.length() + 1).replace(File.separatorChar, '/');
    }

    private JSONObject mobileWorkspaceEntryInfo(File root, File target) throws Exception {
        boolean isDir = target.isDirectory();
        boolean isFile = target.isFile();
        long size = isDir ? 0 : target.length();
        return new JSONObject()
                .put("name", target.getName())
                .put("path", mobileWorkspaceRelPath(root, target))
                .put("type", isDir ? "directory" : isFile ? "file" : "other")
                .put("size", size)
                .put("modified", mobileWorkspaceModifiedSeconds(target))
                .put("editable", isFile && size <= MAX_MOBILE_WORKSPACE_TEXT_BYTES && mobileWorkspaceTextName(target.getName()))
                .put("text_hint", isFile && mobileWorkspaceTextName(target.getName()));
    }

    private double mobileWorkspaceModifiedSeconds(File file) {
        return file.lastModified() / 1000.0;
    }

    private boolean mobileWorkspaceTextName(String rawName) {
        String name = valueOr(rawName, "").toLowerCase(Locale.US);
        if (name.equals("dockerfile") || name.equals("makefile") || name.equals("license")
                || name.equals("readme") || name.equals("requirements")) return true;
        String[] exts = {
                ".bat", ".c", ".cfg", ".conf", ".cpp", ".cs", ".css", ".csv", ".env.example",
                ".go", ".h", ".hpp", ".htm", ".html", ".ini", ".java", ".js", ".json",
                ".jsx", ".kt", ".kts", ".log", ".lua", ".md", ".mjs", ".ps1", ".py",
                ".rb", ".rs", ".sh", ".sql", ".svelte", ".toml", ".ts", ".tsx", ".txt",
                ".vue", ".xml", ".yaml", ".yml"
        };
        for (String ext : exts) {
            if (name.endsWith(ext)) return true;
        }
        return false;
    }

    private boolean mobileWorkspaceEditableText(File file) throws IOException {
        if (!file.isFile() || file.length() > MAX_MOBILE_WORKSPACE_TEXT_BYTES) return false;
        if (mobileWorkspaceIsProbablyBinary(file)) return false;
        return true;
    }

    private boolean mobileWorkspaceIsProbablyBinary(File file) throws IOException {
        byte[] sample = new byte[(int) Math.min(4096, Math.max(0, file.length()))];
        if (sample.length == 0) return false;
        int read;
        try (InputStream in = new FileInputStream(file)) {
            read = in.read(sample);
        }
        if (read <= 0) return false;
        int control = 0;
        for (int i = 0; i < read; i++) {
            int b = sample[i] & 0xff;
            if (b == 0) return true;
            if (b < 32 && b != 9 && b != 10 && b != 12 && b != 13) control++;
        }
        return control / Math.max(1.0, read) > 0.08;
    }

    private void mobileDeleteRecursively(File target) throws Exception {
        if (target.isDirectory()) {
            File[] children = target.listFiles();
            if (children != null) {
                for (File child : children) mobileDeleteRecursively(child);
            }
        }
        if (!target.delete() && target.exists()) throw mobileHttp(400, "Could not delete path");
    }

    private MobileHttpException mobileHttp(int status, String detail) {
        return new MobileHttpException(status, detail);
    }

    private JSONArray mobileMcpServers() throws Exception {
        JSONObject server = new JSONObject()
                .put("id", "android_rag")
                .put("name", "Android Folders")
                .put("transport", "android")
                .put("command", "")
                .put("args", new JSONArray())
                .put("env", new JSONObject())
                .put("url", "")
                .put("is_enabled", true)
                .put("status", "connected")
                .put("tool_count", 1)
                .put("disabled_tool_count", 0)
                .put("enabled_tool_count", 1)
                .put("error", JSONObject.NULL)
                .put("auth_url", JSONObject.NULL)
                .put("has_oauth", false)
                .put("needs_oauth", false)
                .put("mobile_standalone", true);
        return new JSONArray().put(server);
    }

    private JSONArray mobileMcpTools() throws Exception {
        JSONObject inputSchema = new JSONObject()
                .put("type", "object")
                .put("properties", new JSONObject()
                        .put("action", new JSONObject()
                                .put("type", "string")
                                .put("enum", new JSONArray().put("list").put("add_directory").put("remove_directory")))
                        .put("directory", new JSONObject().put("type", "string")))
                .put("required", new JSONArray().put("action"));
        JSONObject tool = new JSONObject()
                .put("server_id", "android_rag")
                .put("server_name", "Android Folders")
                .put("name", "manage_rag")
                .put("qualified_name", "mcp__android_rag__manage_rag")
                .put("description", "List, save, and remove folder paths in Android standalone. Connect to PC for actual PC folder indexing.")
                .put("input_schema", inputSchema)
                .put("is_disabled", false)
                .put("mobile_standalone", true);
        return new JSONArray().put(tool);
    }

    private JSONObject createDocument(Map<String, String> form) throws Exception {
        long now = System.currentTimeMillis();
        JSONObject doc = new JSONObject()
                .put("id", UUID.randomUUID().toString())
                .put("session_id", nullableString(valueOr(form.get("session_id"), "")))
                .put("title", valueOr(form.get("title"), "Untitled"))
                .put("language", valueOr(form.get("language"), "markdown"))
                .put("current_content", valueOr(form.get("content"), ""))
                .put("version_count", 1)
                .put("is_active", true)
                .put("archived", false)
                .put("created_at", String.valueOf(now))
                .put("updated_at", String.valueOf(now))
                .put("source_email_uid", JSONObject.NULL)
                .put("source_email_folder", JSONObject.NULL)
                .put("source_email_account_id", JSONObject.NULL)
                .put("source_email_message_id", JSONObject.NULL);
        JSONArray documents = loadArray(PREF_DOCUMENTS);
        documents.put(doc);
        saveArray(PREF_DOCUMENTS, documents);
        return doc;
    }

    private JSONArray listDocumentsForSession(String sessionId) throws Exception {
        JSONArray documents = loadArray(PREF_DOCUMENTS);
        JSONArray out = new JSONArray();
        for (int i = documents.length() - 1; i >= 0; i--) {
            JSONObject doc = documents.getJSONObject(i);
            if (!doc.optBoolean("is_active", true)) continue;
            if (!sessionId.equals(doc.optString("session_id"))) continue;
            out.put(doc);
        }
        return out;
    }

    private JSONObject findDocument(String id) throws Exception {
        JSONArray documents = loadArray(PREF_DOCUMENTS);
        for (int i = 0; i < documents.length(); i++) {
            JSONObject doc = documents.getJSONObject(i);
            if (id.equals(doc.optString("id"))) return doc;
        }
        return null;
    }

    private JSONObject updateDocumentFields(String id, Map<String, String> form, boolean toggleArchived, boolean softDelete) throws Exception {
        JSONArray documents = loadArray(PREF_DOCUMENTS);
        for (int i = 0; i < documents.length(); i++) {
            JSONObject doc = documents.getJSONObject(i);
            if (!id.equals(doc.optString("id"))) continue;
            if (form.containsKey("content")) {
                String nextContent = valueOr(form.get("content"), "");
                if (!nextContent.equals(doc.optString("current_content"))) {
                    doc.put("current_content", nextContent);
                    doc.put("version_count", Math.max(1, doc.optInt("version_count", 1) + 1));
                }
            }
            if (form.containsKey("title")) doc.put("title", valueOr(form.get("title"), doc.optString("title")));
            if (form.containsKey("language")) doc.put("language", valueOr(form.get("language"), doc.optString("language")));
            if (form.containsKey("session_id")) doc.put("session_id", nullableString(valueOr(form.get("session_id"), "")));
            if (toggleArchived) doc.put("archived", !doc.optBoolean("archived", false));
            if (softDelete) doc.put("is_active", false);
            doc.put("updated_at", String.valueOf(System.currentTimeMillis()));
            documents.put(i, doc);
            saveArray(PREF_DOCUMENTS, documents);
            return doc;
        }
        return null;
    }

    private JSONObject listNotes(Request request) throws Exception {
        JSONArray notes = loadArray(PREF_NOTES);
        boolean archived = "true".equalsIgnoreCase(valueOr(request.query.get("archived"), ""));
        String label = valueOr(request.query.get("label"), "").trim();
        List<JSONObject> filtered = new ArrayList<>();
        for (int i = 0; i < notes.length(); i++) {
            JSONObject note = notes.optJSONObject(i);
            if (note == null) continue;
            if (note.optBoolean("archived", false) != archived) continue;
            if (!label.isEmpty() && !label.equals(note.optString("label", ""))) continue;
            filtered.add(note);
        }
        Collections.sort(filtered, noteComparator(archived));
        JSONArray out = new JSONArray();
        for (JSONObject note : filtered) out.put(note);
        return new JSONObject().put("notes", out);
    }

    private JSONObject createNote(JSONObject body) throws Exception {
        long now = System.currentTimeMillis();
        JSONObject note = new JSONObject()
                .put("id", UUID.randomUUID().toString())
                .put("owner", "mobile")
                .put("title", jsonString(body, "title", ""))
                .put("content", nullableJsonValue(body, "content"))
                .put("items", nullableJsonValue(body, "items"))
                .put("note_type", jsonString(body, "note_type", "note"))
                .put("color", nullableJsonValue(body, "color"))
                .put("label", nullableJsonValue(body, "label"))
                .put("pinned", jsonBoolean(body, "pinned", false))
                .put("archived", false)
                .put("due_date", nullableJsonValue(body, "due_date"))
                .put("source", jsonString(body, "source", "user"))
                .put("session_id", nullableJsonValue(body, "session_id"))
                .put("sort_order", jsonInt(body, "sort_order", 0))
                .put("image_url", nullableJsonValue(body, "image_url"))
                .put("repeat", jsonString(body, "repeat", "none"))
                .put("ai_classification", JSONObject.NULL)
                .put("ai_content_hash", JSONObject.NULL)
                .put("agent_session_id", nullableJsonValue(body, "agent_session_id"))
                .put("created_at", String.valueOf(now))
                .put("updated_at", String.valueOf(now));
        JSONArray notes = loadArray(PREF_NOTES);
        notes.put(note);
        saveArray(PREF_NOTES, notes);
        return note;
    }

    private JSONObject findNote(String id) throws Exception {
        JSONArray notes = loadArray(PREF_NOTES);
        for (int i = 0; i < notes.length(); i++) {
            JSONObject note = notes.optJSONObject(i);
            if (note != null && id.equals(note.optString("id"))) return note;
        }
        return null;
    }

    private JSONObject updateNoteFields(String id, JSONObject body) throws Exception {
        JSONArray notes = loadArray(PREF_NOTES);
        for (int i = 0; i < notes.length(); i++) {
            JSONObject note = notes.optJSONObject(i);
            if (note == null || !id.equals(note.optString("id"))) continue;

            if (body.has("title")) note.put("title", body.isNull("title") ? "" : body.optString("title", ""));
            if (body.has("note_type")) note.put("note_type", body.isNull("note_type") ? "note" : body.optString("note_type", "note"));
            if (body.has("pinned")) note.put("pinned", jsonBoolean(body, "pinned", note.optBoolean("pinned", false)));
            if (body.has("archived")) note.put("archived", jsonBoolean(body, "archived", note.optBoolean("archived", false)));
            if (body.has("sort_order")) note.put("sort_order", body.isNull("sort_order") ? 0 : body.optInt("sort_order", note.optInt("sort_order", 0)));
            if (body.has("repeat")) note.put("repeat", body.isNull("repeat") ? "none" : body.optString("repeat", "none"));

            copyNullableNoteField(note, body, "content");
            copyNullableNoteField(note, body, "items");
            copyNullableNoteField(note, body, "color");
            copyNullableNoteField(note, body, "label");
            copyNullableNoteField(note, body, "due_date");
            copyNullableNoteField(note, body, "source");
            copyNullableNoteField(note, body, "session_id");
            copyNullableNoteField(note, body, "image_url");
            copyNullableNoteField(note, body, "agent_session_id");

            note.put("updated_at", String.valueOf(System.currentTimeMillis()));
            notes.put(i, note);
            saveArray(PREF_NOTES, notes);
            return note;
        }
        return null;
    }

    private boolean deleteNote(String id) throws Exception {
        JSONArray notes = loadArray(PREF_NOTES);
        JSONArray kept = new JSONArray();
        boolean deleted = false;
        for (int i = 0; i < notes.length(); i++) {
            JSONObject note = notes.optJSONObject(i);
            if (note != null && id.equals(note.optString("id"))) {
                deleted = true;
                continue;
            }
            kept.put(note == null ? notes.opt(i) : note);
        }
        if (deleted) saveArray(PREF_NOTES, kept);
        return deleted;
    }

    private JSONObject toggleNoteBoolean(String id, String key) throws Exception {
        JSONArray notes = loadArray(PREF_NOTES);
        for (int i = 0; i < notes.length(); i++) {
            JSONObject note = notes.optJSONObject(i);
            if (note == null || !id.equals(note.optString("id"))) continue;
            note.put(key, !note.optBoolean(key, false));
            note.put("updated_at", String.valueOf(System.currentTimeMillis()));
            notes.put(i, note);
            saveArray(PREF_NOTES, notes);
            return note;
        }
        return null;
    }

    private JSONObject toggleNoteItem(String id, int index) throws Exception {
        JSONArray notes = loadArray(PREF_NOTES);
        for (int i = 0; i < notes.length(); i++) {
            JSONObject note = notes.optJSONObject(i);
            if (note == null || !id.equals(note.optString("id"))) continue;
            JSONArray items = note.optJSONArray("items");
            if (items == null) {
                return new JSONObject().put("_status", 400).put("detail", "Note has no checklist items");
            }
            if (index < 0 || index >= items.length()) {
                return new JSONObject().put("_status", 400).put("detail", "Item index out of range");
            }
            JSONObject item = items.optJSONObject(index);
            if (item == null) {
                return new JSONObject().put("_status", 400).put("detail", "Invalid checklist item");
            }
            item.put("done", !item.optBoolean("done", false));
            items.put(index, item);
            note.put("items", items);
            note.put("updated_at", String.valueOf(System.currentTimeMillis()));
            notes.put(i, note);
            saveArray(PREF_NOTES, notes);
            return new JSONObject().put("_status", 200).put("ok", true).put("items", items);
        }
        return new JSONObject().put("_status", 404).put("detail", "Note not found");
    }

    private JSONObject reorderNotes(JSONObject body) throws Exception {
        JSONArray ids = body.optJSONArray("ids");
        if (ids == null) return new JSONObject().put("detail", "ids must be a list");
        JSONArray notes = loadArray(PREF_NOTES);
        for (int order = 0; order < ids.length(); order++) {
            String id = ids.optString(order, "");
            if (id.isEmpty()) continue;
            for (int i = 0; i < notes.length(); i++) {
                JSONObject note = notes.optJSONObject(i);
                if (note == null || !id.equals(note.optString("id"))) continue;
                note.put("sort_order", order);
                note.put("updated_at", String.valueOf(System.currentTimeMillis()));
                notes.put(i, note);
                break;
            }
        }
        saveArray(PREF_NOTES, notes);
        return new JSONObject().put("ok", true).put("count", ids.length());
    }

    private Comparator<JSONObject> noteComparator(boolean archived) {
        return (a, b) -> {
            if (archived) {
                return Long.compare(noteTimestamp(b), noteTimestamp(a));
            }
            int pinned = Boolean.compare(b.optBoolean("pinned", false), a.optBoolean("pinned", false));
            if (pinned != 0) return pinned;
            int sort = Integer.compare(a.optInt("sort_order", 0), b.optInt("sort_order", 0));
            if (sort != 0) return sort;
            return Long.compare(noteTimestamp(b), noteTimestamp(a));
        };
    }

    private long noteTimestamp(JSONObject note) {
        try {
            return Long.parseLong(note.optString("updated_at", "0"));
        } catch (Exception ignored) {
            return 0L;
        }
    }

    private JSONObject mobileCalendarList() throws Exception {
        return new JSONObject().put("calendars", loadArray(PREF_CALENDAR_CALS));
    }

    private JSONObject mobileCalendarCreate(Request request) throws Exception {
        Map<String, String> form = parseForm(request);
        String name = firstNonEmpty(request.query.get("name"), form.get("name"), "New calendar").trim();
        String color = firstNonEmpty(request.query.get("color"), form.get("color"), "#5b8abf").trim();
        if (name.isEmpty()) name = "New calendar";
        if (color.isEmpty()) color = "#5b8abf";

        JSONObject cal = new JSONObject()
                .put("name", name)
                .put("href", UUID.randomUUID().toString())
                .put("color", color)
                .put("source", "local");
        JSONArray cals = loadArray(PREF_CALENDAR_CALS);
        cals.put(cal);
        saveArray(PREF_CALENDAR_CALS, cals);
        return new JSONObject()
                .put("ok", true)
                .put("id", cal.optString("href"))
                .put("name", name)
                .put("color", color);
    }

    private JSONObject mobileCalendarUpdate(String id, Request request) throws Exception {
        JSONArray cals = loadArray(PREF_CALENDAR_CALS);
        Map<String, String> form = parseForm(request);
        String nextName = request.query.containsKey("name") ? request.query.get("name") : form.get("name");
        String nextColor = request.query.containsKey("color") ? request.query.get("color") : form.get("color");
        for (int i = 0; i < cals.length(); i++) {
            JSONObject cal = cals.optJSONObject(i);
            if (cal == null || !id.equals(cal.optString("href"))) continue;
            if (nextName != null) cal.put("name", nextName.trim().isEmpty() ? "Calendar" : nextName.trim());
            if (nextColor != null) cal.put("color", nextColor.trim().isEmpty() ? "#5b8abf" : nextColor.trim());
            cals.put(i, cal);
            saveArray(PREF_CALENDAR_CALS, cals);
            refreshCalendarEventDenorm(cal);
            return new JSONObject().put("ok", true);
        }
        return new JSONObject().put("_status", 404).put("detail", "Calendar not found");
    }

    private JSONObject mobileCalendarDelete(String id) throws Exception {
        JSONArray cals = loadArray(PREF_CALENDAR_CALS);
        JSONArray keptCals = new JSONArray();
        boolean deleted = false;
        for (int i = 0; i < cals.length(); i++) {
            JSONObject cal = cals.optJSONObject(i);
            if (cal != null && id.equals(cal.optString("href"))) {
                deleted = true;
                continue;
            }
            keptCals.put(cal == null ? cals.opt(i) : cal);
        }
        if (!deleted) return new JSONObject().put("_status", 404).put("detail", "Calendar not found");

        JSONArray events = loadArray(PREF_CALENDAR_EVENTS);
        JSONArray keptEvents = new JSONArray();
        for (int i = 0; i < events.length(); i++) {
            JSONObject event = events.optJSONObject(i);
            if (event != null && id.equals(event.optString("calendar_href"))) continue;
            keptEvents.put(event == null ? events.opt(i) : event);
        }
        saveArray(PREF_CALENDAR_CALS, keptCals);
        saveArray(PREF_CALENDAR_EVENTS, keptEvents);
        return new JSONObject().put("ok", true);
    }

    private JSONObject mobileCalendarEvents(Request request) throws Exception {
        String start = valueOr(request.query.get("start"), "");
        String end = valueOr(request.query.get("end"), "");
        String calendar = valueOr(request.query.get("calendar"), "").trim();
        JSONArray events = loadArray(PREF_CALENDAR_EVENTS);
        List<JSONObject> filtered = new ArrayList<>();
        for (int i = 0; i < events.length(); i++) {
            JSONObject event = events.optJSONObject(i);
            if (event == null) continue;
            if ("cancelled".equalsIgnoreCase(event.optString("status", ""))) continue;
            if (!calendar.isEmpty()
                    && !calendar.equals(event.optString("calendar_href"))
                    && !calendar.equals(event.optString("calendar"))) continue;
            if (!calendarEventOverlaps(event, start, end)) continue;
            filtered.add(mobileCalendarClientEvent(event));
        }
        Collections.sort(filtered, (a, b) -> a.optString("dtstart", "").compareTo(b.optString("dtstart", "")));
        JSONArray out = new JSONArray();
        for (JSONObject event : filtered) out.put(event);
        return new JSONObject().put("events", out);
    }

    private JSONObject mobileCalendarCreateEvent(JSONObject body) throws Exception {
        String dtstart = jsonString(body, "dtstart", "").trim();
        if (dtstart.isEmpty()) return new JSONObject().put("_status", 400).put("detail", "dtstart is required");
        boolean allDay = jsonBoolean(body, "all_day", false);
        String dtend = jsonString(body, "dtend", "").trim();
        if (dtend.isEmpty()) dtend = allDay ? addDaysToDate(calendarDateKey(dtstart), 1) : addMinutesToIso(dtstart, 60);

        JSONObject cal = ensureMobileCalendarForEvent(jsonString(body, "calendar_href", ""));
        String eventColor = jsonString(body, "color", "").trim();
        long now = System.currentTimeMillis();
        JSONObject event = new JSONObject()
                .put("uid", UUID.randomUUID().toString())
                .put("summary", jsonString(body, "summary", ""))
                .put("dtstart", allDay ? calendarDateKey(dtstart) : dtstart)
                .put("dtend", allDay ? calendarDateKey(dtend) : dtend)
                .put("all_day", allDay)
                .put("description", jsonString(body, "description", ""))
                .put("location", jsonString(body, "location", ""))
                .put("rrule", jsonString(body, "rrule", ""))
                .put("calendar", cal.optString("name", "Personal"))
                .put("calendar_href", cal.optString("href"))
                .put("calendar_color", cal.optString("color", "#5b8abf"))
                .put("event_color", eventColor)
                .put("color", eventColor.isEmpty() ? cal.optString("color", "#5b8abf") : eventColor)
                .put("event_type", JSONObject.NULL)
                .put("importance", "normal")
                .put("status", "confirmed")
                .put("owner", "mobile")
                .put("created_at", isoTimestamp(now))
                .put("updated_at", isoTimestamp(now));
        JSONArray events = loadArray(PREF_CALENDAR_EVENTS);
        events.put(event);
        saveArray(PREF_CALENDAR_EVENTS, events);
        return new JSONObject().put("ok", true).put("uid", event.optString("uid"));
    }

    private JSONObject mobileCalendarUpdateEvent(String uid, JSONObject body) throws Exception {
        String baseUid = resolveCalendarBaseUid(uid);
        JSONArray events = loadArray(PREF_CALENDAR_EVENTS);
        for (int i = 0; i < events.length(); i++) {
            JSONObject event = events.optJSONObject(i);
            if (event == null || !baseUid.equals(event.optString("uid"))) continue;

            if (body.has("summary")) event.put("summary", jsonString(body, "summary", ""));
            if (body.has("description")) event.put("description", jsonString(body, "description", ""));
            if (body.has("location")) event.put("location", jsonString(body, "location", ""));
            if (body.has("rrule")) event.put("rrule", jsonString(body, "rrule", ""));
            if (body.has("all_day")) event.put("all_day", jsonBoolean(body, "all_day", event.optBoolean("all_day", false)));
            boolean allDay = event.optBoolean("all_day", false);
            if (body.has("dtstart")) {
                String value = jsonString(body, "dtstart", "");
                event.put("dtstart", allDay ? calendarDateKey(value) : value);
            }
            if (body.has("dtend")) {
                String value = jsonString(body, "dtend", "");
                event.put("dtend", allDay ? calendarDateKey(value) : value);
            }
            if (body.has("calendar_href")) {
                JSONObject cal = ensureMobileCalendarForEvent(jsonString(body, "calendar_href", ""));
                event.put("calendar", cal.optString("name", "Personal"));
                event.put("calendar_href", cal.optString("href"));
                event.put("calendar_color", cal.optString("color", "#5b8abf"));
            }
            if (body.has("color")) event.put("event_color", jsonString(body, "color", "").trim());
            if (event.optString("dtend", "").isEmpty()) {
                event.put("dtend", event.optBoolean("all_day", false)
                        ? addDaysToDate(calendarDateKey(event.optString("dtstart", "")), 1)
                        : addMinutesToIso(event.optString("dtstart", ""), 60));
            }
            event.put("updated_at", isoTimestamp(System.currentTimeMillis()));
            event = mobileCalendarStoredEvent(event);
            events.put(i, event);
            saveArray(PREF_CALENDAR_EVENTS, events);
            return new JSONObject().put("ok", true).put("uid", baseUid);
        }
        return new JSONObject().put("_status", 404).put("detail", "Event not found");
    }

    private JSONObject mobileCalendarDeleteEvent(String uid) throws Exception {
        String baseUid = resolveCalendarBaseUid(uid);
        JSONArray events = loadArray(PREF_CALENDAR_EVENTS);
        JSONArray kept = new JSONArray();
        boolean deleted = false;
        for (int i = 0; i < events.length(); i++) {
            JSONObject event = events.optJSONObject(i);
            if (event != null && baseUid.equals(event.optString("uid"))) {
                deleted = true;
                continue;
            }
            kept.put(event == null ? events.opt(i) : event);
        }
        if (deleted) {
            saveArray(PREF_CALENDAR_EVENTS, kept);
            return new JSONObject().put("ok", true);
        }
        return new JSONObject().put("_status", 404).put("detail", "Event not found");
    }

    private JSONObject mobileCalendarImport(Request request) throws Exception {
        MultipartData parts = parseMultipartData(request);
        MultipartFile file = parts.file;
        if (file == null) file = parts.files.get("file");
        if (file == null || file.data == null || file.data.length == 0) {
            return new JSONObject().put("_status", 400).put("detail", "No ICS file provided");
        }
        if (file.data.length > 10 * 1024 * 1024) {
            return new JSONObject().put("_status", 413).put("detail", "ICS file is too large");
        }

        String text = new String(file.data, StandardCharsets.UTF_8);
        if (text.startsWith("\uFEFF")) text = text.substring(1);
        String rawName = firstNonEmpty(parts.fields.get("calendar_name"), file.filename, "Imported");
        JSONObject cal = findOrCreateMobileCalendarByName(sanitizeCalendarName(rawName), "#7c4dff", "import");
        JSONArray events = loadArray(PREF_CALENDAR_EVENTS);
        List<Map<String, String>> parsedEvents = parseIcsEvents(text);
        int imported = 0;
        int skipped = 0;

        for (Map<String, String> props : parsedEvents) {
            JSONObject event = mobileCalendarEventFromIcs(cal, props, events);
            if (event == null) {
                skipped++;
                continue;
            }
            events.put(event);
            imported++;
        }
        saveArray(PREF_CALENDAR_EVENTS, events);
        return new JSONObject()
                .put("ok", true)
                .put("imported", imported)
                .put("skipped", skipped)
                .put("calendar", cal.optString("name"))
                .put("calendar_id", cal.optString("href"))
                .put("mobile_standalone", true);
    }

    private JSONObject mobileCalendarQuickParse(JSONObject body) throws Exception {
        String text = jsonString(body, "text", "").trim();
        if (text.isEmpty()) return new JSONObject().put("_status", 400).put("detail", "text is required");
        String lower = text.toLowerCase(Locale.US);
        String date = todayDateString();
        java.util.regex.Matcher isoDate = java.util.regex.Pattern.compile("\\b(\\d{4}-\\d{2}-\\d{2})\\b").matcher(text);
        if (isoDate.find()) date = isoDate.group(1);
        else if (lower.contains("tomorrow") || lower.contains("tmrw")) date = addDaysToDate(date, 1);

        int hour = -1;
        int minute = 0;
        java.util.regex.Matcher ampm = java.util.regex.Pattern
                .compile("\\b(\\d{1,2})(?::(\\d{2}))?\\s*([ap])\\.?\\s*m?\\.?\\b", java.util.regex.Pattern.CASE_INSENSITIVE)
                .matcher(text);
        if (ampm.find()) {
            hour = parseInt(ampm.group(1), -1);
            minute = parseInt(ampm.group(2), 0);
            boolean pm = "p".equalsIgnoreCase(ampm.group(3));
            if (hour >= 1 && hour <= 12 && minute >= 0 && minute <= 59) {
                if (pm && hour != 12) hour += 12;
                if (!pm && hour == 12) hour = 0;
            } else {
                hour = -1;
            }
        }
        if (hour < 0) {
            java.util.regex.Matcher h24 = java.util.regex.Pattern.compile("\\b([01]?\\d|2[0-3]):([0-5]\\d)\\b").matcher(text);
            if (h24.find()) {
                hour = parseInt(h24.group(1), -1);
                minute = parseInt(h24.group(2), 0);
            }
        }

        boolean allDay = hour < 0;
        String dtstart = allDay ? date : String.format(Locale.US, "%sT%02d:%02d:00", date, hour, minute);
        String dtend = allDay ? date : addMinutesToIso(dtstart, 60);
        return new JSONObject()
                .put("ok", true)
                .put("mobile_standalone", true)
                .put("confidence", allDay ? 0.35 : 0.55)
                .put("event", new JSONObject()
                        .put("summary", text)
                        .put("dtstart", dtstart)
                        .put("dtend", dtend)
                        .put("all_day", allDay)
                        .put("location", "")
                        .put("description", ""));
    }

    private JSONObject findMobileCalendar(String id) throws Exception {
        if (id == null || id.isEmpty()) return null;
        JSONArray cals = loadArray(PREF_CALENDAR_CALS);
        for (int i = 0; i < cals.length(); i++) {
            JSONObject cal = cals.optJSONObject(i);
            if (cal != null && id.equals(cal.optString("href"))) return cal;
        }
        return null;
    }

    private JSONObject ensureMobileCalendarForEvent(String id) throws Exception {
        JSONObject existing = findMobileCalendar(valueOr(id, ""));
        if (existing != null) return existing;
        JSONArray cals = loadArray(PREF_CALENDAR_CALS);
        if (cals.length() > 0) {
            JSONObject first = cals.optJSONObject(0);
            if (first != null) return first;
        }
        return findOrCreateMobileCalendarByName("Personal", "#5b8abf", "local");
    }

    private JSONObject findOrCreateMobileCalendarByName(String rawName, String color, String source) throws Exception {
        String name = sanitizeCalendarName(rawName);
        JSONArray cals = loadArray(PREF_CALENDAR_CALS);
        for (int i = 0; i < cals.length(); i++) {
            JSONObject cal = cals.optJSONObject(i);
            if (cal != null && name.equalsIgnoreCase(cal.optString("name", ""))) return cal;
        }
        JSONObject cal = new JSONObject()
                .put("name", name)
                .put("href", UUID.randomUUID().toString())
                .put("color", valueOr(color, "").trim().isEmpty() ? "#5b8abf" : color.trim())
                .put("source", valueOr(source, "").trim().isEmpty() ? "local" : source.trim());
        cals.put(cal);
        saveArray(PREF_CALENDAR_CALS, cals);
        return cal;
    }

    private void refreshCalendarEventDenorm(JSONObject cal) throws Exception {
        JSONArray events = loadArray(PREF_CALENDAR_EVENTS);
        boolean changed = false;
        String calId = cal.optString("href");
        for (int i = 0; i < events.length(); i++) {
            JSONObject event = events.optJSONObject(i);
            if (event == null || !calId.equals(event.optString("calendar_href"))) continue;
            event.put("calendar", cal.optString("name", ""));
            event.put("calendar_color", cal.optString("color", ""));
            event = mobileCalendarStoredEvent(event);
            events.put(i, event);
            changed = true;
        }
        if (changed) saveArray(PREF_CALENDAR_EVENTS, events);
    }

    private JSONObject mobileCalendarStoredEvent(JSONObject event) throws Exception {
        JSONObject cal = findMobileCalendar(event.optString("calendar_href", ""));
        String calColor = cal == null ? event.optString("calendar_color", "") : cal.optString("color", "");
        String calName = cal == null ? event.optString("calendar", "") : cal.optString("name", "");
        String eventColor = event.optString("event_color", "");
        event.put("calendar", calName);
        event.put("calendar_color", calColor);
        event.put("color", eventColor.isEmpty() ? calColor : eventColor);
        return event;
    }

    private JSONObject mobileCalendarClientEvent(JSONObject event) throws Exception {
        return mobileCalendarStoredEvent(new JSONObject(event.toString()));
    }

    private boolean calendarEventOverlaps(JSONObject event, String start, String end) {
        String evStart = calendarDateKey(event.optString("dtstart", ""));
        String evEnd = calendarDateKey(event.optString("dtend", ""));
        if (evEnd.isEmpty()) evEnd = evStart;
        boolean startsBeforeEnd = end == null || end.isEmpty() || evStart.compareTo(end) < 0;
        boolean endsAfterStart = start == null || start.isEmpty() || evEnd.compareTo(start) >= 0;
        return startsBeforeEnd && endsAfterStart;
    }

    private String resolveCalendarBaseUid(String uid) {
        String value = valueOr(uid, "");
        int idx = value.indexOf("::");
        return idx >= 0 ? value.substring(0, idx) : value;
    }

    private JSONObject mobileCalendarEventFromIcs(JSONObject cal, Map<String, String> props, JSONArray existingEvents) throws Exception {
        String rawStart = valueOr(props.get("DTSTART"), "").trim();
        if (rawStart.isEmpty()) return null;
        String startParam = valueOr(props.get("DTSTART_PARAM"), "");
        boolean allDay = startParam.contains("VALUE=DATE") || rawStart.matches("\\d{8}");
        String dtstart = parseIcsDateValue(rawStart, startParam, allDay);
        if (dtstart.isEmpty()) return null;
        String rawEnd = valueOr(props.get("DTEND"), "").trim();
        String dtend = rawEnd.isEmpty()
                ? (allDay ? addDaysToDate(calendarDateKey(dtstart), 1) : addMinutesToIso(dtstart, 60))
                : parseIcsDateValue(rawEnd, valueOr(props.get("DTEND_PARAM"), ""), allDay);
        if (dtend.isEmpty()) dtend = allDay ? addDaysToDate(calendarDateKey(dtstart), 1) : addMinutesToIso(dtstart, 60);

        String summary = icsUnescapeText(valueOr(props.get("SUMMARY"), ""));
        String sourceUid = icsUnescapeText(valueOr(props.get("UID"), ""));
        String calId = cal.optString("href");
        for (int i = 0; i < existingEvents.length(); i++) {
            JSONObject existing = existingEvents.optJSONObject(i);
            if (existing == null || !calId.equals(existing.optString("calendar_href"))) continue;
            boolean sameSource = !sourceUid.isEmpty()
                    && sourceUid.equals(existing.optString("source_uid", ""))
                    && dtstart.equals(existing.optString("dtstart", ""));
            boolean sameSummary = summary.equals(existing.optString("summary", ""))
                    && dtstart.equals(existing.optString("dtstart", ""));
            if (sameSource || sameSummary) return null;
        }

        long now = System.currentTimeMillis();
        return new JSONObject()
                .put("uid", UUID.randomUUID().toString())
                .put("source_uid", sourceUid)
                .put("summary", summary)
                .put("dtstart", allDay ? calendarDateKey(dtstart) : dtstart)
                .put("dtend", allDay ? calendarDateKey(dtend) : dtend)
                .put("all_day", allDay)
                .put("description", icsUnescapeText(valueOr(props.get("DESCRIPTION"), "")))
                .put("location", icsUnescapeText(valueOr(props.get("LOCATION"), "")))
                .put("rrule", valueOr(props.get("RRULE"), ""))
                .put("calendar", cal.optString("name"))
                .put("calendar_href", calId)
                .put("calendar_color", cal.optString("color", "#7c4dff"))
                .put("event_color", "")
                .put("color", cal.optString("color", "#7c4dff"))
                .put("event_type", JSONObject.NULL)
                .put("importance", "normal")
                .put("status", "confirmed")
                .put("owner", "mobile")
                .put("created_at", isoTimestamp(now))
                .put("updated_at", isoTimestamp(now));
    }

    private List<Map<String, String>> parseIcsEvents(String text) {
        List<Map<String, String>> events = new ArrayList<>();
        List<String> lines = unfoldIcsLines(text);
        Map<String, String> current = null;
        for (String line : lines) {
            String upper = line.toUpperCase(Locale.US);
            if ("BEGIN:VEVENT".equals(upper)) {
                current = new HashMap<>();
                continue;
            }
            if ("END:VEVENT".equals(upper)) {
                if (current != null) events.add(current);
                current = null;
                continue;
            }
            if (current == null) continue;
            int colon = line.indexOf(':');
            if (colon <= 0) continue;
            String left = line.substring(0, colon);
            String value = line.substring(colon + 1);
            String name = left.split(";", 2)[0].trim().toUpperCase(Locale.US);
            if (name.isEmpty()) continue;
            current.put(name, value);
            current.put(name + "_PARAM", left.toUpperCase(Locale.US));
        }
        return events;
    }

    private List<String> unfoldIcsLines(String text) {
        List<String> lines = new ArrayList<>();
        String normalized = valueOr(text, "").replace("\r\n", "\n").replace('\r', '\n');
        StringBuilder current = null;
        for (String raw : normalized.split("\n", -1)) {
            if ((raw.startsWith(" ") || raw.startsWith("\t")) && current != null) {
                current.append(raw.substring(1));
                continue;
            }
            if (current != null) lines.add(current.toString());
            current = new StringBuilder(raw);
        }
        if (current != null) lines.add(current.toString());
        return lines;
    }

    private String parseIcsDateValue(String rawValue, String params, boolean allDay) {
        String raw = valueOr(rawValue, "").trim();
        if (raw.isEmpty()) return "";
        boolean utc = raw.endsWith("Z") || raw.endsWith("z");
        if (utc) raw = raw.substring(0, raw.length() - 1);
        if (raw.matches("\\d{8}")) {
            return raw.substring(0, 4) + "-" + raw.substring(4, 6) + "-" + raw.substring(6, 8);
        }
        if (raw.length() >= 15 && raw.charAt(8) == 'T') {
            String date = raw.substring(0, 4) + "-" + raw.substring(4, 6) + "-" + raw.substring(6, 8);
            String time = raw.substring(9);
            while (time.length() < 6) time += "0";
            String out = date + "T" + time.substring(0, 2) + ":" + time.substring(2, 4) + ":" + time.substring(4, 6);
            return out + (utc ? "Z" : "");
        }
        if (raw.length() >= 10 && raw.charAt(4) == '-' && raw.charAt(7) == '-') {
            return allDay ? raw.substring(0, 10) : raw;
        }
        return "";
    }

    private String buildMobileCalendarIcs(JSONObject cal) throws Exception {
        String calId = cal.optString("href");
        JSONArray events = loadArray(PREF_CALENDAR_EVENTS);
        List<String> lines = new ArrayList<>();
        lines.add("BEGIN:VCALENDAR");
        lines.add("VERSION:2.0");
        lines.add("PRODID:-//Odysseus//Calendar//EN");
        lines.add("X-WR-CALNAME:" + icsEscapeText(cal.optString("name", "Calendar")));
        for (int i = 0; i < events.length(); i++) {
            JSONObject event = events.optJSONObject(i);
            if (event == null || !calId.equals(event.optString("calendar_href"))) continue;
            if ("cancelled".equalsIgnoreCase(event.optString("status", ""))) continue;
            boolean allDay = event.optBoolean("all_day", false);
            String start = event.optString("dtstart", "");
            String end = event.optString("dtend", "");
            if (allDay && calendarDateKey(start).equals(calendarDateKey(end))) {
                end = addDaysToDate(calendarDateKey(start), 1);
            }
            String exportUid = event.optString("source_uid", "").trim();
            if (exportUid.isEmpty()) exportUid = event.optString("uid", UUID.randomUUID().toString());
            lines.add("BEGIN:VEVENT");
            lines.add("UID:" + exportUid);
            lines.add("SUMMARY:" + icsEscapeText(event.optString("summary", "")));
            if (allDay) {
                lines.add("DTSTART;VALUE=DATE:" + icsDateValue(start));
                lines.add("DTEND;VALUE=DATE:" + icsDateValue(end));
            } else {
                lines.add("DTSTART:" + icsDateTimeValue(start));
                lines.add("DTEND:" + icsDateTimeValue(end.isEmpty() ? addMinutesToIso(start, 60) : end));
            }
            if (!event.optString("description", "").isEmpty()) lines.add("DESCRIPTION:" + icsEscapeText(event.optString("description", "")));
            if (!event.optString("location", "").isEmpty()) lines.add("LOCATION:" + icsEscapeText(event.optString("location", "")));
            if (!event.optString("rrule", "").isEmpty()) lines.add("RRULE:" + event.optString("rrule", ""));
            lines.add("END:VEVENT");
        }
        lines.add("END:VCALENDAR");
        return String.join("\r\n", lines) + "\r\n";
    }

    private void sendCalendarDownload(OutputStream out, String filename, String text) throws IOException {
        byte[] data = valueOr(text, "").getBytes(StandardCharsets.UTF_8);
        String headers = "HTTP/1.1 200 OK\r\n" +
                "Content-Type: text/calendar; charset=utf-8\r\n" +
                "Content-Disposition: attachment; filename=\"" + filename + "\"\r\n" +
                "Access-Control-Allow-Origin: *\r\n" +
                "Cache-Control: no-store\r\n" +
                "Content-Length: " + data.length + "\r\n" +
                "Connection: close\r\n\r\n";
        out.write(headers.getBytes(StandardCharsets.UTF_8));
        out.write(data);
    }

    private String sanitizeCalendarName(String raw) {
        String name = valueOr(raw, "").trim();
        int slash = Math.max(name.lastIndexOf('/'), name.lastIndexOf('\\'));
        if (slash >= 0) name = name.substring(slash + 1);
        String lower = name.toLowerCase(Locale.US);
        if (lower.endsWith(".ics")) name = name.substring(0, name.length() - 4);
        else if (lower.endsWith(".ical")) name = name.substring(0, name.length() - 5);
        name = name.replace('_', ' ').trim();
        StringBuilder cleaned = new StringBuilder();
        for (int i = 0; i < name.length(); i++) {
            char c = name.charAt(i);
            if (!Character.isISOControl(c)) cleaned.append(c);
        }
        String out = cleaned.toString().trim();
        if (out.isEmpty()) out = "Imported";
        return out.length() > 120 ? out.substring(0, 120) : out;
    }

    private String safeIcsFilename(String raw) {
        String base = sanitizeCalendarName(raw).replaceAll("[^A-Za-z0-9._-]", "_");
        while (base.startsWith(".")) base = base.substring(1);
        if (base.isEmpty()) base = "calendar";
        if (base.length() > 80) base = base.substring(0, 80);
        return base + ".ics";
    }

    private String icsUnescapeText(String raw) {
        String text = valueOr(raw, "");
        StringBuilder out = new StringBuilder(text.length());
        boolean escaped = false;
        for (int i = 0; i < text.length(); i++) {
            char c = text.charAt(i);
            if (escaped) {
                if (c == 'n' || c == 'N') out.append('\n');
                else out.append(c);
                escaped = false;
            } else if (c == '\\') {
                escaped = true;
            } else {
                out.append(c);
            }
        }
        if (escaped) out.append('\\');
        return out.toString();
    }

    private String icsEscapeText(String raw) {
        String text = valueOr(raw, "");
        StringBuilder out = new StringBuilder(text.length());
        for (int i = 0; i < text.length(); i++) {
            char c = text.charAt(i);
            if (c == '\\') out.append("\\\\");
            else if (c == ';') out.append("\\;");
            else if (c == ',') out.append("\\,");
            else if (c == '\n' || c == '\r') out.append("\\n");
            else out.append(c);
        }
        return out.toString();
    }

    private String icsDateValue(String date) {
        String key = calendarDateKey(date);
        return key.length() == 10 ? key.replace("-", "") : "";
    }

    private String icsDateTimeValue(String dt) {
        String value = valueOr(dt, "");
        if (value.length() < 16) return icsDateValue(value);
        String date = value.substring(0, 10).replace("-", "");
        String time = value.substring(11, Math.min(value.length(), 19)).replace(":", "");
        while (time.length() < 6) time += "0";
        return date + "T" + time.substring(0, 6) + (value.endsWith("Z") || value.endsWith("z") ? "Z" : "");
    }

    private String calendarDateKey(String dt) {
        String value = valueOr(dt, "").trim();
        if (value.length() >= 10 && value.charAt(4) == '-' && value.charAt(7) == '-') return value.substring(0, 10);
        if (value.matches("\\d{8}")) return value.substring(0, 4) + "-" + value.substring(4, 6) + "-" + value.substring(6, 8);
        return "";
    }

    private String todayDateString() {
        SimpleDateFormat fmt = new SimpleDateFormat("yyyy-MM-dd", Locale.US);
        return fmt.format(new Date());
    }

    private String addDaysToDate(String date, int days) {
        try {
            SimpleDateFormat fmt = new SimpleDateFormat("yyyy-MM-dd", Locale.US);
            java.util.Calendar cal = java.util.Calendar.getInstance();
            cal.setTime(fmt.parse(calendarDateKey(date)));
            cal.add(java.util.Calendar.DATE, days);
            return fmt.format(cal.getTime());
        } catch (Exception ignored) {
            return calendarDateKey(date);
        }
    }

    private String addMinutesToIso(String dt, int minutes) {
        String value = valueOr(dt, "");
        if (value.length() < 16) return value;
        String base = value.length() >= 19 ? value.substring(0, 19) : value.substring(0, 16) + ":00";
        String suffix = value.length() > 19 ? value.substring(19) : "";
        try {
            SimpleDateFormat fmt = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US);
            java.util.Calendar cal = java.util.Calendar.getInstance();
            cal.setTime(fmt.parse(base));
            cal.add(java.util.Calendar.MINUTE, minutes);
            return fmt.format(cal.getTime()) + suffix;
        } catch (Exception ignored) {
            return value;
        }
    }

    private String firstNonEmpty(String a, String b, String fallback) {
        String av = valueOr(a, "").trim();
        if (!av.isEmpty()) return av;
        String bv = valueOr(b, "").trim();
        return bv.isEmpty() ? valueOr(fallback, "") : bv;
    }

    private JSONObject mobileHwfitSystem(Request request) throws Exception {
        String manualMode = valueOr(request.query.get("manual_mode"), "").trim();
        int manualGpuCount = Math.max(0, parseInt(request.query.get("manual_gpu_count"), 0));
        double manualVram = parseDouble(request.query.get("manual_vram_gb"), 0.0);
        double manualRam = parseDouble(request.query.get("manual_ram_gb"), 8.0);
        String manualBackend = valueOr(request.query.get("manual_backend"), "cuda").trim().toLowerCase(Locale.US);

        boolean hasManualGpu = "gpu".equals(manualMode) && manualGpuCount > 0 && manualVram > 0.0;
        double totalRam = Math.max(4.0, manualRam);
        JSONObject sys = new JSONObject()
                .put("platform", "android")
                .put("backend", hasManualGpu ? (manualBackend.isEmpty() ? "cuda" : manualBackend) : "cpu")
                .put("mobile_standalone", true)
                .put("manual_hardware", !manualMode.isEmpty())
                .put("total_ram_gb", round1(totalRam))
                .put("available_ram_gb", round1(Math.max(2.0, totalRam * 0.65)))
                .put("gpu_count", hasManualGpu ? manualGpuCount : 0)
                .put("detected_gpu_count", hasManualGpu ? manualGpuCount : 0)
                .put("has_gpu", hasManualGpu)
                .put("gpu_name", hasManualGpu ? "Simulated Android GPU" : JSONObject.NULL)
                .put("gpu_vram_gb", hasManualGpu ? round1(manualGpuCount * manualVram) : 0.0)
                .put("gpus", new JSONArray())
                .put("gpu_groups", new JSONArray());
        if (hasManualGpu) {
            JSONArray gpus = new JSONArray();
            for (int i = 0; i < manualGpuCount; i++) {
                gpus.put(new JSONObject()
                        .put("index", i)
                        .put("name", "Simulated Android GPU")
                        .put("vram_gb", round1(manualVram)));
            }
            JSONArray groups = new JSONArray().put(new JSONObject()
                    .put("name", "Simulated Android GPU")
                    .put("count", manualGpuCount)
                    .put("vram_each", round1(manualVram))
                    .put("vram_total", round1(manualGpuCount * manualVram)));
            sys.put("gpus", gpus);
            sys.put("gpu_groups", groups);
        }
        return sys;
    }

    private JSONObject mobileHwfitModels(Request request) throws Exception {
        JSONObject system = mobileHwfitSystem(request);
        double vramGb = system.optDouble("gpu_vram_gb", 0.0);
        double ramGb = system.optDouble("available_ram_gb", 6.0);
        boolean fitOnly = "1".equals(valueOr(request.query.get("fit_only"), ""));
        String search = valueOr(request.query.get("search"), "").trim().toLowerCase(Locale.US);
        String quantFilter = valueOr(request.query.get("quant"), "").trim().toLowerCase(Locale.US);
        String useCase = valueOr(request.query.get("use_case"), "").trim().toLowerCase(Locale.US);
        String sort = valueOr(request.query.get("sort"), "score").trim().toLowerCase(Locale.US);
        int limit = Math.max(1, Math.min(120, parseInt(request.query.get("limit"), 80)));

        List<JSONObject> rows = new ArrayList<>();
        addMobileHwfitModel(rows, "TinyLlama/TinyLlama-1.1B-Chat-v1.0", "TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF", 1.1, "Q4_K_M", 0.8, 32768, "chat", vramGb, ramGb);
        addMobileHwfitModel(rows, "Qwen/Qwen2.5-1.5B-Instruct", "Qwen/Qwen2.5-1.5B-Instruct-GGUF", 1.5, "Q4_K_M", 1.0, 32768, "chat", vramGb, ramGb);
        addMobileHwfitModel(rows, "Qwen/Qwen2.5-Coder-3B-Instruct", "Qwen/Qwen2.5-Coder-3B-Instruct-GGUF", 3.0, "Q4_K_M", 2.1, 32768, "coding", vramGb, ramGb);
        addMobileHwfitModel(rows, "microsoft/Phi-3.5-mini-instruct", "bartowski/Phi-3.5-mini-instruct-GGUF", 3.8, "Q4_K_M", 2.7, 131072, "chat", vramGb, ramGb);
        addMobileHwfitModel(rows, "google/gemma-2-2b-it", "bartowski/gemma-2-2b-it-GGUF", 2.0, "Q4_K_M", 1.5, 8192, "chat", vramGb, ramGb);
        addMobileHwfitModel(rows, "mistralai/Mistral-7B-Instruct-v0.3", "bartowski/Mistral-7B-Instruct-v0.3-GGUF", 7.0, "Q4_K_M", 4.8, 32768, "chat", vramGb, ramGb);
        addMobileHwfitModel(rows, "Qwen/Qwen2.5-7B-Instruct", "Qwen/Qwen2.5-7B-Instruct-GGUF", 7.0, "Q4_K_M", 4.8, 32768, "chat", vramGb, ramGb);
        addMobileHwfitModel(rows, "Qwen/Qwen2.5-Coder-7B-Instruct", "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF", 7.0, "Q4_K_M", 4.8, 32768, "coding", vramGb, ramGb);
        addMobileHwfitModel(rows, "meta-llama/Llama-3.2-3B-Instruct", "bartowski/Llama-3.2-3B-Instruct-GGUF", 3.0, "Q4_K_M", 2.1, 131072, "chat", vramGb, ramGb);
        addMobileHwfitModel(rows, "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B", "bartowski/DeepSeek-R1-Distill-Qwen-7B-GGUF", 7.0, "Q4_K_M", 4.9, 32768, "reasoning", vramGb, ramGb);

        JSONArray filtered = new JSONArray();
        Collections.sort(rows, (a, b) -> Double.compare(sortMetric(b, sort), sortMetric(a, sort)));
        int count = 0;
        for (JSONObject row : rows) {
            String haystack = (row.optString("name") + " " + row.optString("repo_id") + " " + row.optString("quant")).toLowerCase(Locale.US);
            if (!search.isEmpty() && !haystack.contains(search)) continue;
            if (!quantFilter.isEmpty() && !row.optString("quant").toLowerCase(Locale.US).contains(quantFilter)) continue;
            if (!useCase.isEmpty() && !"standard".equals(useCase) && !haystack.contains(useCase) && !row.optString("use_case").equals(useCase)) {
                if (!("reasoning".equals(useCase) && row.optString("name").toLowerCase(Locale.US).contains("deepseek"))) continue;
            }
            if (fitOnly && ("too_tight".equals(row.optString("fit_level")) || "no_fit".equals(row.optString("fit_level")))) continue;
            filtered.put(row);
            count++;
            if (count >= limit) break;
        }
        return new JSONObject()
                .put("system", system)
                .put("models", filtered)
                .put("mobile_standalone", true);
    }

    private void addMobileHwfitModel(List<JSONObject> out, String repoId, String quantRepo, double paramsB, String quant,
                                     double requiredGb, int context, String useCase, double vramGb, double ramGb) throws Exception {
        double budget = vramGb > 0.0 ? vramGb : Math.max(0.0, ramGb * 0.45);
        String fit = requiredGb <= budget * 0.70 ? "good" : requiredGb <= budget ? "marginal" : "too_tight";
        double fitBoost = "good".equals(fit) ? 25.0 : "marginal".equals(fit) ? 12.0 : -15.0;
        double speed = Math.max(2.0, 38.0 / Math.max(1.0, paramsB));
        double score = Math.max(1.0, 55.0 - (paramsB * 2.2) + fitBoost);
        JSONObject row = new JSONObject()
                .put("name", repoId)
                .put("repo_id", repoId)
                .put("quant_repo", quantRepo)
                .put("parameter_count", paramsB == Math.rint(paramsB) ? ((int) paramsB) + "B" : String.format(Locale.US, "%.1fB", paramsB))
                .put("params_b", paramsB)
                .put("required_gb", round1(requiredGb))
                .put("speed_tps", round1(speed))
                .put("context", context)
                .put("score", round1(score))
                .put("fit_level", fit)
                .put("run_mode", "local")
                .put("quant", quant)
                .put("is_gguf", true)
                .put("use_case", useCase)
                .put("gguf_sources", new JSONArray().put(new JSONObject()
                        .put("repo", quantRepo)
                        .put("filename", "")));
        out.add(row);
    }

    private JSONObject mobileOllamaLibrary() throws Exception {
        JSONArray models = new JSONArray();
        addOllamaLibraryModel(models, "qwen2.5", "Qwen2.5 general chat models.", new String[]{"0.5b", "1.5b", "3b", "7b", "14b", "32b"});
        addOllamaLibraryModel(models, "qwen2.5-coder", "Code-specialized Qwen2.5 models.", new String[]{"0.5b", "1.5b", "3b", "7b", "14b"});
        addOllamaLibraryModel(models, "llama3.2", "Meta Llama 3.2 instruct family.", new String[]{"1b", "3b"});
        addOllamaLibraryModel(models, "gemma2", "Google Gemma 2 instruct.", new String[]{"2b", "9b", "27b"});
        addOllamaLibraryModel(models, "phi4", "Microsoft Phi-4.", new String[]{"14b"});
        addOllamaLibraryModel(models, "deepseek-r1", "DeepSeek R1 distilled reasoning models.", new String[]{"1.5b", "7b", "8b", "14b", "32b"});
        addOllamaLibraryModel(models, "mistral", "Mistral 7B instruct.", new String[]{"7b"});
        addOllamaLibraryModel(models, "nomic-embed-text", "Text embedding model.", new String[]{"latest"});
        return new JSONObject()
                .put("models", models)
                .put("fetched_at", System.currentTimeMillis() / 1000.0)
                .put("error", JSONObject.NULL)
                .put("mobile_standalone", true);
    }

    private void addOllamaLibraryModel(JSONArray out, String name, String description, String[] sizes) throws Exception {
        JSONArray arr = new JSONArray();
        for (String size : sizes) arr.put(size);
        out.put(new JSONObject()
                .put("name", name)
                .put("description", description)
                .put("sizes", arr));
    }

    private JSONObject mobileHfLatest(Request request) throws Exception {
        int limit = Math.max(1, Math.min(20, parseInt(request.query.get("limit"), 10)));
        double vram = parseDouble(request.query.get("vram_gb"), 0.0);
        JSONArray raw = new JSONArray();
        addHfLatest(raw, "Qwen/Qwen2.5-1.5B-Instruct", 1.3, 2100000, "text-generation");
        addHfLatest(raw, "Qwen/Qwen2.5-Coder-3B-Instruct", 2.4, 1450000, "text-generation");
        addHfLatest(raw, "microsoft/Phi-3.5-mini-instruct", 3.2, 1800000, "text-generation");
        addHfLatest(raw, "google/gemma-2-2b-it", 2.1, 2600000, "text-generation");
        addHfLatest(raw, "meta-llama/Llama-3.2-3B-Instruct", 2.7, 3200000, "text-generation");
        addHfLatest(raw, "mistralai/Mistral-7B-Instruct-v0.3", 5.2, 2900000, "text-generation");
        addHfLatest(raw, "Qwen/Qwen2.5-7B-Instruct", 5.1, 2500000, "text-generation");
        addHfLatest(raw, "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B", 5.3, 1700000, "text-generation");
        JSONArray out = new JSONArray();
        for (int i = 0; i < raw.length() && out.length() < limit; i++) {
            JSONObject item = raw.getJSONObject(i);
            if (vram > 0.0 && item.optDouble("needed_vram_gb", 0.0) > vram) continue;
            out.put(item);
        }
        return new JSONObject().put("models", out).put("mobile_standalone", true);
    }

    private void addHfLatest(JSONArray out, String repoId, double neededVram, int downloads, String pipeline) throws Exception {
        out.put(new JSONObject()
                .put("repo_id", repoId)
                .put("downloads", downloads)
                .put("likes", Math.max(1, downloads / 2500))
                .put("createdAt", "2026-01-01T00:00:00Z")
                .put("pipeline_tag", pipeline)
                .put("tags", new JSONArray().put(pipeline))
                .put("est_vram_gb", round1(neededVram / 1.3))
                .put("needed_vram_gb", round1(neededVram)));
    }

    private JSONObject defaultCookbookState() throws Exception {
        JSONObject localServer = new JSONObject()
                .put("id", "local")
                .put("name", "Local")
                .put("host", "")
                .put("port", "")
                .put("env", "none")
                .put("envPath", "")
                .put("platform", "android")
                .put("downloadDir", "")
                .put("modelDirs", new JSONArray().put("~/.cache/huggingface/hub"));
        JSONObject env = new JSONObject()
                .put("env", "none")
                .put("envPath", "")
                .put("hfTokenConfigured", false)
                .put("hfTokenMasked", "")
                .put("gpus", "")
                .put("remoteHost", "")
                .put("remoteServerKey", "")
                .put("servers", new JSONArray().put(localServer))
                .put("modelPaths", new JSONArray().put("~/.cache/huggingface/hub"))
                .put("platform", "android")
                .put("defaultServer", "local");
        return new JSONObject()
                .put("tasks", new JSONArray())
                .put("presets", new JSONArray())
                .put("env", env)
                .put("serveState", JSONObject.NULL)
                .put("mobile_standalone", true);
    }

    private JSONObject loadCookbookState() throws Exception {
        String raw = prefs().getString(PREF_COOKBOOK_STATE, "");
        if (raw == null || raw.trim().isEmpty()) return defaultCookbookState();
        try {
            JSONObject state = new JSONObject(raw);
            if (!state.has("env")) state.put("env", defaultCookbookState().getJSONObject("env"));
            if (!state.has("tasks")) state.put("tasks", new JSONArray());
            if (!state.has("presets")) state.put("presets", new JSONArray());
            state.put("mobile_standalone", true);
            return state;
        } catch (Exception ignored) {
            return defaultCookbookState();
        }
    }

    private JSONObject mobileCookbookTaskStatus() throws Exception {
        JSONArray tasks = loadCookbookState().optJSONArray("tasks");
        JSONArray out = new JSONArray();
        if (tasks != null) {
            for (int i = 0; i < tasks.length(); i++) {
                JSONObject task = tasks.optJSONObject(i);
                if (task == null) continue;
                String sid = task.optString("sessionId", task.optString("id", ""));
                if (sid.isEmpty()) continue;
                out.put(new JSONObject()
                        .put("session_id", sid)
                        .put("status", task.optString("status", "stopped"))
                        .put("output_tail", task.optString("output", ""))
                        .put("progress", task.optString("progress", ""))
                        .put("cmd", task.optJSONObject("payload") == null ? "" : task.optJSONObject("payload").optString("_cmd", "")));
            }
        }
        return new JSONObject()
                .put("tasks", out)
                .put("mobile_standalone", true);
    }

    private JSONObject defaultChat() throws Exception {
        JSONObject ep = firstEnabledEndpoint();
        if (ep == null) {
            return new JSONObject().put("endpoint_id", "").put("endpoint_url", "").put("model", "");
        }
        JSONArray models = ep.optJSONArray("models");
        String model = models != null && models.length() > 0 ? models.optString(0) : "";
        return new JSONObject()
                .put("endpoint_id", ep.optString("id"))
                .put("endpoint_url", chatUrl(ep.optString("base_url")))
                .put("model", model);
    }

    private JSONObject modelsList() throws Exception {
        JSONArray endpoints = loadArray(PREF_ENDPOINTS);
        JSONArray items = new JSONArray();
        for (int i = 0; i < endpoints.length(); i++) {
            JSONObject ep = endpoints.getJSONObject(i);
            if (!ep.optBoolean("is_enabled", true)) continue;
            JSONArray models = ep.optJSONArray("models");
            items.put(new JSONObject()
                    .put("endpoint_id", ep.optString("id"))
                    .put("endpoint_name", ep.optString("name"))
                    .put("url", chatUrl(ep.optString("base_url")))
                    .put("endpoint_url", chatUrl(ep.optString("base_url")))
                    .put("base_url", ep.optString("base_url"))
                    .put("models", models == null ? new JSONArray() : models)
                    .put("models_display", models == null ? new JSONArray() : models)
                    .put("models_extra", new JSONArray())
                    .put("models_extra_display", new JSONArray())
                    .put("category", ep.optString("endpoint_kind", "api"))
                    .put("host", hostLabel(ep.optString("base_url")))
                    .put("offline", false));
        }
        return new JSONObject()
                .put("hosts", new JSONArray())
                .put("items", items);
    }

    private JSONArray endpointList() throws Exception {
        JSONArray endpoints = loadArray(PREF_ENDPOINTS);
        JSONArray out = new JSONArray();
        for (int i = 0; i < endpoints.length(); i++) {
            JSONObject ep = endpoints.getJSONObject(i);
            out.put(publicEndpoint(ep));
        }
        return out;
    }

    private JSONObject publicEndpoint(JSONObject ep) throws Exception {
        JSONArray models = ep.optJSONArray("models");
        return new JSONObject()
                .put("id", ep.optString("id"))
                .put("name", ep.optString("name"))
                .put("base_url", ep.optString("base_url"))
                .put("url", chatUrl(ep.optString("base_url")))
                .put("is_enabled", ep.optBoolean("is_enabled", true))
                .put("model_type", ep.optString("model_type", "llm"))
                .put("endpoint_kind", ep.optString("endpoint_kind", "api"))
                .put("models", models == null ? new JSONArray() : models)
                .put("cached_models", models == null ? new JSONArray() : models)
                .put("model_count", models == null ? 0 : models.length())
                .put("has_api_key", !ep.optString("api_key").isEmpty()
                        || !ep.optString("access_token").isEmpty()
                        || !ep.optString("refresh_token").isEmpty())
                .put("online", true)
                .put("status", models != null && models.length() > 0 ? "ok" : "empty");
    }

    private JSONObject addEndpoint(Map<String, String> form) throws Exception {
        String baseUrl = normalizeBase(valueOr(form.get("base_url"), form.get("endpoint_url")));
        String apiKey = valueOr(form.get("api_key"), "");
        String name = valueOr(form.get("name"), hostLabel(baseUrl));
        String kind = valueOr(form.get("endpoint_kind"), baseUrl.startsWith("http://") ? "local" : "api");
        JSONArray models = fetchModels(baseUrl, apiKey);
        JSONObject ep = new JSONObject()
                .put("id", shortId())
                .put("name", name)
                .put("base_url", baseUrl)
                .put("api_key", apiKey)
                .put("endpoint_kind", kind)
                .put("model_type", valueOr(form.get("model_type"), "llm"))
                .put("is_enabled", true)
                .put("models", models);
        JSONArray endpoints = loadArray(PREF_ENDPOINTS);
        endpoints.put(ep);
        saveArray(PREF_ENDPOINTS, endpoints);
        if (prefs().getString(PREF_DEFAULT_ENDPOINT, "").isEmpty()) {
            prefs().edit().putString(PREF_DEFAULT_ENDPOINT, ep.optString("id")).apply();
        }
        JSONObject result = publicEndpoint(ep);
        result.put("endpoint_id", ep.optString("id"));
        result.put("endpoint_url", chatUrl(baseUrl));
        return result;
    }

    private JSONObject testEndpoint(Map<String, String> form) throws Exception {
        String baseUrl = normalizeBase(valueOr(form.get("base_url"), form.get("endpoint_url")));
        String apiKey = valueOr(form.get("api_key"), "");
        JSONArray models = fetchModels(baseUrl, apiKey);
        return new JSONObject()
                .put("ok", true)
                .put("online", true)
                .put("status", models.length() > 0 ? "ok" : "empty")
                .put("models", models)
                .put("count", models.length());
    }

    private JSONObject startChatGptSubscriptionDeviceFlow() throws Exception {
        Map<String, String> headers = new HashMap<>();
        headers.put("Content-Type", "application/json");
        JSONObject data = httpPostJson(
                CHATGPT_OAUTH_ISSUER + "/api/accounts/deviceauth/usercode",
                headers,
                new JSONObject().put("client_id", CHATGPT_OAUTH_CLIENT_ID));

        String deviceAuthId = data.optString("device_auth_id", "").trim();
        String userCode = data.optString("user_code", "").trim();
        if (deviceAuthId.isEmpty() || userCode.isEmpty()) {
            throw new IOException("ChatGPT did not return a complete device code.");
        }

        int interval = Math.max(1, data.optInt("interval", 5));
        int expiresIn = Math.max(1, data.optInt("expires_in", 900));
        String pollId = UUID.randomUUID().toString().replace("-", "");
        long now = System.currentTimeMillis();
        synchronized (chatGptDeviceFlowLock) {
            pruneExpiredChatGptDeviceFlowsLocked(now);
            chatGptDeviceFlows.put(pollId, new JSONObject()
                    .put("device_auth_id", deviceAuthId)
                    .put("user_code", userCode)
                    .put("interval", interval)
                    .put("expires_at", now + expiresIn * 1000L)
                    .put("next_poll_at", 0L));
        }

        return new JSONObject()
                .put("poll_id", pollId)
                .put("user_code", userCode)
                .put("verification_uri", data.optString("verification_uri", CHATGPT_OAUTH_ISSUER + "/codex/device"))
                .put("interval", interval)
                .put("expires_in", expiresIn);
    }

    private JSONObject pollChatGptSubscriptionDeviceFlow(Map<String, String> form) throws Exception {
        String pollId = valueOr(form.get("poll_id"), "").trim();
        if (pollId.isEmpty()) {
            return new JSONObject().put("status", "failed").put("error", "missing_poll_id");
        }

        JSONObject pending;
        long now = System.currentTimeMillis();
        synchronized (chatGptDeviceFlowLock) {
            pruneExpiredChatGptDeviceFlowsLocked(now);
            pending = chatGptDeviceFlows.get(pollId);
            if (pending == null) {
                return new JSONObject().put("status", "failed").put("error", "expired");
            }
            if (now < pending.optLong("next_poll_at", 0L)) {
                return new JSONObject().put("status", "pending");
            }
        }

        JSONObject data = pollChatGptDeviceAuth(
                pending.optString("device_auth_id", ""),
                pending.optString("user_code", ""));
        String authorizationCode = data.optString("authorization_code", "").trim();
        String codeVerifier = data.optString("code_verifier", "").trim();
        if (!authorizationCode.isEmpty() && !codeVerifier.isEmpty()) {
            JSONObject tokens = exchangeChatGptAuthorizationCode(authorizationCode, codeVerifier);
            JSONObject endpoint = provisionChatGptSubscriptionEndpoint(tokens);
            synchronized (chatGptDeviceFlowLock) {
                chatGptDeviceFlows.remove(pollId);
            }
            return new JSONObject().put("status", "authorized").put("endpoint", endpoint);
        }

        String err = data.optString("error", data.optString("status", "pending"));
        if ("slow_down".equals(err)) {
            int interval = Math.max(1, data.optInt("interval", pending.optInt("interval", 5) + 5));
            synchronized (chatGptDeviceFlowLock) {
                JSONObject current = chatGptDeviceFlows.get(pollId);
                if (current != null) {
                    current.put("interval", interval);
                    current.put("next_poll_at", System.currentTimeMillis() + interval * 1000L);
                }
            }
            return new JSONObject().put("status", "pending").put("interval", interval);
        }
        if ("expired_token".equals(err) || "access_denied".equals(err) || "denied".equals(err)) {
            synchronized (chatGptDeviceFlowLock) {
                chatGptDeviceFlows.remove(pollId);
            }
            return new JSONObject().put("status", "failed").put("error", err);
        }

        scheduleNextChatGptDevicePoll(pollId);
        return new JSONObject().put("status", "pending");
    }

    private JSONObject cancelChatGptSubscriptionDeviceFlow(Map<String, String> form) throws Exception {
        String pollId = valueOr(form.get("poll_id"), "").trim();
        synchronized (chatGptDeviceFlowLock) {
            if (!pollId.isEmpty()) chatGptDeviceFlows.remove(pollId);
        }
        return new JSONObject().put("status", "cancelled");
    }

    private void pruneExpiredChatGptDeviceFlowsLocked(long now) {
        List<String> expired = new ArrayList<>();
        for (Map.Entry<String, JSONObject> entry : chatGptDeviceFlows.entrySet()) {
            if (entry.getValue().optLong("expires_at", 0L) < now) expired.add(entry.getKey());
        }
        for (String key : expired) chatGptDeviceFlows.remove(key);
    }

    private void scheduleNextChatGptDevicePoll(String pollId) throws Exception {
        synchronized (chatGptDeviceFlowLock) {
            JSONObject current = chatGptDeviceFlows.get(pollId);
            if (current != null) {
                int interval = Math.max(1, current.optInt("interval", 5));
                current.put("next_poll_at", System.currentTimeMillis() + interval * 1000L);
            }
        }
    }

    private JSONObject pollChatGptDeviceAuth(String deviceAuthId, String userCode) throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(
                CHATGPT_OAUTH_ISSUER + "/api/accounts/deviceauth/token").openConnection();
        conn.setRequestMethod("POST");
        conn.setConnectTimeout(12000);
        conn.setReadTimeout(20000);
        conn.setDoOutput(true);
        conn.setRequestProperty("Accept", "application/json");
        conn.setRequestProperty("Content-Type", "application/json");
        byte[] bytes = new JSONObject()
                .put("device_auth_id", deviceAuthId)
                .put("user_code", userCode)
                .toString()
                .getBytes(StandardCharsets.UTF_8);
        conn.setFixedLengthStreamingMode(bytes.length);
        try (OutputStream body = conn.getOutputStream()) {
            body.write(bytes);
        }
        int status = conn.getResponseCode();
        String response = readAll(status >= 400 ? conn.getErrorStream() : conn.getInputStream());
        if (status == 403 || status == 404) {
            return new JSONObject().put("status", "pending").put("error", "authorization_pending");
        }
        if (status < 200 || status >= 300) {
            throw new IOException("ChatGPT device poll failed: " + formatProviderError(status, response));
        }
        return new JSONObject(response);
    }

    private JSONObject exchangeChatGptAuthorizationCode(String authorizationCode, String codeVerifier) throws Exception {
        Map<String, String> form = new HashMap<>();
        form.put("grant_type", "authorization_code");
        form.put("code", authorizationCode);
        form.put("redirect_uri", CHATGPT_OAUTH_REDIRECT_URI);
        form.put("client_id", CHATGPT_OAUTH_CLIENT_ID);
        form.put("code_verifier", codeVerifier);
        JSONObject tokens = httpPostFormJson(CHATGPT_OAUTH_TOKEN_URL, form);
        if (tokens.optString("access_token", "").isEmpty()) {
            throw new IOException("Codex token exchange did not return an access token.");
        }
        return tokens;
    }

    private JSONObject provisionChatGptSubscriptionEndpoint(JSONObject tokens) throws Exception {
        String accessToken = tokens.optString("access_token", "").trim();
        String refreshToken = tokens.optString("refresh_token", "").trim();
        if (accessToken.isEmpty() || refreshToken.isEmpty()) {
            throw new IOException("Codex token response was missing access_token or refresh_token.");
        }

        JSONArray models = fetchChatGptSubscriptionModels(accessToken);
        if (models.length() == 0) {
            throw new IOException(CHATGPT_SUBSCRIPTION_LABEL + " connected, but no usable Codex models were discovered for this account.");
        }

        JSONArray endpoints = loadArray(PREF_ENDPOINTS);
        JSONObject ep = null;
        int epIndex = -1;
        for (int i = 0; i < endpoints.length(); i++) {
            JSONObject candidate = endpoints.optJSONObject(i);
            if (candidate == null) continue;
            if (CHATGPT_SUBSCRIPTION_PROVIDER.equals(candidate.optString("provider"))
                    || isChatGptSubscriptionBase(candidate.optString("base_url"))) {
                ep = candidate;
                epIndex = i;
                break;
            }
        }
        if (ep == null) {
            ep = new JSONObject().put("id", shortId());
        }
        ep.put("name", CHATGPT_SUBSCRIPTION_LABEL)
                .put("base_url", CHATGPT_SUBSCRIPTION_BASE_URL)
                .put("api_key", "")
                .put("access_token", accessToken)
                .put("refresh_token", refreshToken)
                .put("provider", CHATGPT_SUBSCRIPTION_PROVIDER)
                .put("auth_mode", "chatgpt")
                .put("endpoint_kind", "api")
                .put("model_type", "llm")
                .put("is_enabled", true)
                .put("supports_tools", false)
                .put("model_refresh_mode", "manual")
                .put("last_refresh", String.valueOf(System.currentTimeMillis()))
                .put("models", models);

        if (epIndex >= 0) endpoints.put(epIndex, ep);
        else endpoints.put(ep);
        saveArray(PREF_ENDPOINTS, endpoints);
        if (prefs().getString(PREF_DEFAULT_ENDPOINT, "").isEmpty()) {
            prefs().edit().putString(PREF_DEFAULT_ENDPOINT, ep.optString("id")).apply();
        }

        JSONObject result = publicEndpoint(ep);
        result.put("endpoint_id", ep.optString("id"));
        result.put("endpoint_url", chatUrl(CHATGPT_SUBSCRIPTION_BASE_URL));
        return result;
    }

    private JSONArray fetchChatGptSubscriptionModels(String accessToken) throws Exception {
        JSONObject json = httpGetJson(
                CHATGPT_SUBSCRIPTION_BASE_URL + "/models?client_version=1.0.0",
                chatGptSubscriptionHeaders(accessToken));
        JSONArray entries = json.optJSONArray("models");
        JSONArray out = new JSONArray();
        if (entries == null) return out;

        List<JSONObject> sortable = new ArrayList<>();
        for (int i = 0; i < entries.length(); i++) {
            JSONObject item = entries.optJSONObject(i);
            if (item == null) continue;
            String slug = item.optString("slug", "").trim();
            if (slug.isEmpty()) continue;
            String visibility = item.optString("visibility", "").trim().toLowerCase(Locale.US);
            if ("hide".equals(visibility) || "hidden".equals(visibility)) continue;
            sortable.add(item);
        }
        Collections.sort(sortable, (a, b) -> {
            int rank = Integer.compare(a.optInt("priority", 10000), b.optInt("priority", 10000));
            if (rank != 0) return rank;
            return a.optString("slug", "").compareTo(b.optString("slug", ""));
        });
        List<String> seen = new ArrayList<>();
        for (JSONObject item : sortable) {
            String slug = item.optString("slug", "").trim();
            if (!slug.isEmpty() && !seen.contains(slug)) {
                out.put(slug);
                seen.add(slug);
            }
        }
        return out;
    }

    private Map<String, String> chatGptSubscriptionHeaders(String accessToken) {
        Map<String, String> headers = new HashMap<>();
        headers.put("Accept", "application/json, text/event-stream");
        headers.put("Origin", "https://chatgpt.com");
        headers.put("Referer", "https://chatgpt.com/codex");
        headers.put("User-Agent", "Odysseus Codex Subscription");
        if (!valueOr(accessToken, "").trim().isEmpty()) {
            headers.put("Authorization", "Bearer " + accessToken.trim());
        }
        return headers;
    }

    private JSONObject httpPostFormJson(String url, Map<String, String> form) throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
        conn.setRequestMethod("POST");
        conn.setConnectTimeout(12000);
        conn.setReadTimeout(20000);
        conn.setDoOutput(true);
        conn.setRequestProperty("Accept", "application/json");
        conn.setRequestProperty("Content-Type", "application/x-www-form-urlencoded");
        StringBuilder encoded = new StringBuilder();
        for (Map.Entry<String, String> entry : form.entrySet()) {
            if (encoded.length() > 0) encoded.append('&');
            encoded.append(urlEncode(entry.getKey())).append('=').append(urlEncode(entry.getValue()));
        }
        byte[] bytes = encoded.toString().getBytes(StandardCharsets.UTF_8);
        conn.setFixedLengthStreamingMode(bytes.length);
        try (OutputStream body = conn.getOutputStream()) {
            body.write(bytes);
        }
        int status = conn.getResponseCode();
        String response = readAll(status >= 400 ? conn.getErrorStream() : conn.getInputStream());
        if (status < 200 || status >= 300) {
            throw new IOException("HTTP " + status + ": " + truncateError(response, 180));
        }
        return new JSONObject(response);
    }

    private JSONObject probeSelected(Request request) throws Exception {
        JSONObject body = requestJson(request);
        JSONArray requested = body.optJSONArray("models");
        JSONArray results = new JSONArray();
        if (requested == null) {
            return new JSONObject().put("results", results);
        }

        for (int i = 0; i < requested.length(); i++) {
            JSONObject item = requested.optJSONObject(i);
            if (item == null) continue;

            String model = item.optString("model", "").trim();
            String endpointId = item.optString("endpoint_id", "").trim();
            String endpointUrl = item.optString("endpoint", "").trim();
            JSONObject result = new JSONObject()
                    .put("model", model)
                    .put("endpoint_id", endpointId);

            if (model.isEmpty()) {
                results.put(result.put("status", "fail").put("error", "No model specified"));
                continue;
            }

            JSONObject endpoint = endpointForProbe(endpointId, endpointUrl);
            if (endpoint == null) {
                results.put(result.put("status", "fail").put("error", "Endpoint not found"));
                continue;
            }

            long start = System.currentTimeMillis();
            try {
                probeChat(endpoint, model);
                results.put(result
                        .put("status", "ok")
                        .put("latency_ms", System.currentTimeMillis() - start));
            } catch (Exception ex) {
                results.put(result
                        .put("status", "fail")
                        .put("latency_ms", System.currentTimeMillis() - start)
                        .put("error", truncateError(valueOr(ex.getMessage(), "request failed"), 120)));
            }
        }

        return new JSONObject().put("results", results);
    }

    private JSONArray endpointModels(JSONObject ep) throws Exception {
        JSONArray models = ep == null ? new JSONArray() : ep.optJSONArray("models");
        JSONArray out = new JSONArray();
        if (models == null) return out;
        for (int i = 0; i < models.length(); i++) {
            String model = models.optString(i);
            out.put(new JSONObject()
                    .put("id", model)
                    .put("display", model.contains("/") ? model.substring(model.lastIndexOf('/') + 1) : model)
                    .put("is_hidden", false)
                    .put("is_pinned", false));
        }
        return out;
    }

    private JSONObject toggleEndpoint(String id) throws Exception {
        JSONArray endpoints = loadArray(PREF_ENDPOINTS);
        for (int i = 0; i < endpoints.length(); i++) {
            JSONObject ep = endpoints.getJSONObject(i);
            if (!id.equals(ep.optString("id"))) continue;
            ep.put("is_enabled", !ep.optBoolean("is_enabled", true));
            endpoints.put(i, ep);
            saveArray(PREF_ENDPOINTS, endpoints);
            return publicEndpoint(ep);
        }
        return new JSONObject().put("id", id).put("is_enabled", false);
    }

    private void deleteEndpoint(String id) throws Exception {
        JSONArray endpoints = loadArray(PREF_ENDPOINTS);
        JSONArray kept = new JSONArray();
        for (int i = 0; i < endpoints.length(); i++) {
            JSONObject ep = endpoints.getJSONObject(i);
            if (!id.equals(ep.optString("id"))) kept.put(ep);
        }
        saveArray(PREF_ENDPOINTS, kept);
    }

    private JSONObject unloadEndpointModel(JSONObject ep, Map<String, String> form) throws Exception {
        String model = valueOr(form.get("model"), "").trim();
        if (model.isEmpty()) {
            JSONArray models = ep.optJSONArray("models");
            if (models != null && models.length() == 1) {
                model = models.optString(0, "").trim();
            }
        }
        if (model.isEmpty()) {
            return new JSONObject()
                    .put("ok", false)
                    .put("supported", false)
                    .put("detail", "Pick a model to unload from this endpoint.");
        }

        String baseUrl = ep.optString("base_url");
        if (!isOllamaUnloadSupported(baseUrl)) {
            return new JSONObject()
                    .put("ok", false)
                    .put("supported", false)
                    .put("model", model)
                    .put("detail", "Unload is currently supported for local Ollama endpoints only.");
        }

        try {
            return postOllamaUnload(baseUrl, ep.optString("api_key"), model);
        } catch (Exception ex) {
            return new JSONObject()
                    .put("ok", false)
                    .put("supported", true)
                    .put("model", model)
                    .put("detail", "Unload request failed: " + valueOr(ex.getMessage(), "request failed"));
        }
    }

    private JSONObject unloadAllModels() throws Exception {
        int supportedEndpoints = 0;
        int skippedEndpoints = 0;
        int requested = 0;
        int unloaded = 0;
        int failed = 0;
        JSONArray results = new JSONArray();
        JSONArray errors = new JSONArray();
        boolean androidRuntimeAvailable = appContext != null && DynamicOnnxRuntime.isInstalled(appContext);

        synchronized (rembgSessionLock) {
            if (rembgRuntime != null) {
                rembgRuntime.close();
                rembgRuntime = null;
                unloaded++;
                results.put(new JSONObject()
                        .put("provider", "android-onnx")
                        .put("model", valueOr(lastBgRemoveSource, "rembg"))
                        .put("message", "Android ONNX runtime released"));
            }
            lastBgRemoveSource = "";
            lastBgRemoveError = "";
        }

        JSONArray endpoints = loadArray(PREF_ENDPOINTS);
        for (int i = 0; i < endpoints.length(); i++) {
            JSONObject ep = endpoints.optJSONObject(i);
            if (ep == null) continue;
            String baseUrl = ep.optString("base_url", "");
            if (!isOllamaUnloadSupported(baseUrl)) {
                skippedEndpoints++;
                continue;
            }
            supportedEndpoints++;
            JSONArray models = ep.optJSONArray("models");
            if (models == null) continue;
            for (int j = 0; j < models.length(); j++) {
                String model = models.optString(j, "").trim();
                if (model.isEmpty()) continue;
                requested++;
                JSONObject result = postOllamaUnload(baseUrl, ep.optString("api_key"), model);
                if (result.optBoolean("ok")) {
                    unloaded++;
                    results.put(new JSONObject()
                            .put("endpoint_id", ep.optString("id", ""))
                            .put("endpoint", ep.optString("name", ep.optString("id", baseUrl)))
                            .put("provider", "ollama")
                            .put("model", model));
                } else {
                    failed++;
                    errors.put(new JSONObject()
                            .put("endpoint_id", ep.optString("id", ""))
                            .put("endpoint", ep.optString("name", ep.optString("id", baseUrl)))
                            .put("model", model)
                            .put("detail", result.optString("detail", "Unload failed")));
                }
            }
        }

        String message;
        if (unloaded > 0 && failed > 0) {
            message = "Unloaded " + unloaded + " loaded model" + (unloaded == 1 ? "" : "s") + "; " + failed + " failed.";
        } else if (unloaded > 0) {
            message = "Unloaded " + unloaded + " loaded model" + (unloaded == 1 ? "" : "s") + ".";
        } else if (failed > 0) {
            message = "Unload failed for " + failed + " runtime/model request" + (failed == 1 ? "" : "s") + ".";
        } else if (supportedEndpoints > 0 || androidRuntimeAvailable) {
            message = "No loaded Android or Ollama models found.";
        } else {
            message = "No supported local model runtimes found.";
        }

        return new JSONObject()
                .put("ok", failed == 0)
                .put("supported", supportedEndpoints > 0 || androidRuntimeAvailable || results.length() > 0)
                .put("platform", "android")
                .put("requested", requested)
                .put("unloaded", unloaded)
                .put("failed", failed)
                .put("supported_endpoints", supportedEndpoints)
                .put("skipped_endpoints", skippedEndpoints)
                .put("results", results)
                .put("errors", errors)
                .put("message", message)
                .put("mobile_standalone", true);
    }

    private boolean isOllamaUnloadSupported(String baseUrl) {
        try {
            URL parsed = new URL(normalizeBase(baseUrl));
            String host = valueOr(parsed.getHost(), "").toLowerCase(Locale.US);
            if ("ollama.com".equals(host) || host.endsWith(".ollama.com")) return false;
            return parsed.getPort() == 11434 || host.contains("ollama");
        } catch (Exception ignored) {
            return false;
        }
    }

    private String ollamaGenerateUrl(String baseUrl) {
        String base = normalizeBase(baseUrl).trim();
        while (base.endsWith("/")) base = base.substring(0, base.length() - 1);
        if (base.endsWith("/api/generate")) return base;
        if (base.endsWith("/api/chat") || base.endsWith("/api/tags")) {
            base = base.substring(0, base.length() - 5);
        }
        if (base.endsWith("/v1")) {
            base = base.substring(0, base.length() - 3);
        }
        if (base.endsWith("/api")) return base + "/generate";
        return base + "/api/generate";
    }

    private JSONObject postOllamaUnload(String baseUrl, String apiKey, String model) throws Exception {
        URL url = new URL(ollamaGenerateUrl(baseUrl));
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("POST");
        conn.setConnectTimeout(12000);
        conn.setReadTimeout(30000);
        conn.setDoOutput(true);
        conn.setRequestProperty("Accept", "application/json");
        conn.setRequestProperty("Content-Type", "application/json");
        if (!valueOr(apiKey, "").isEmpty()) conn.setRequestProperty("Authorization", "Bearer " + apiKey);
        byte[] bytes = new JSONObject()
                .put("model", model)
                .put("prompt", "")
                .put("stream", false)
                .put("keep_alive", 0)
                .toString()
                .getBytes(StandardCharsets.UTF_8);
        conn.setFixedLengthStreamingMode(bytes.length);
        try (OutputStream requestBody = conn.getOutputStream()) {
            requestBody.write(bytes);
        }
        int status = conn.getResponseCode();
        String responseBody = readAll(status >= 400 ? conn.getErrorStream() : conn.getInputStream());
        if (status >= 200 && status < 300) {
            return new JSONObject()
                    .put("ok", true)
                    .put("supported", true)
                    .put("provider", "ollama")
                    .put("model", model)
                    .put("message", "Unload requested for " + model);
        }
        return new JSONObject()
                .put("ok", false)
                .put("supported", true)
                .put("model", model)
                .put("detail", "Ollama unload failed: " + formatProviderError(status, responseBody));
    }

    private void streamChat(Request request, OutputStream out) throws Exception {
        Map<String, String> form = parseForm(request);
        String sid = form.get("session");
        String userText = valueOr(form.get("message"), "");
        String rawWorkspace = valueOr(form.get("workspace"), "").trim();
        File activeWorkspace = null;
        String workspaceRejected = "";
        if (!rawWorkspace.isEmpty()) {
            try {
                activeWorkspace = mobileWorkspaceRootOrThrow(rawWorkspace);
            } catch (Exception ex) {
                workspaceRejected = rawWorkspace;
            }
        }
        JSONObject session = getSessionById(sid);
        String model = session.optString("model");
        JSONArray history = history(session);
        if (!userText.isEmpty()) {
            history.put(new JSONObject().put("role", "user").put("content", userText));
        }
        String localWorkspaceReply = tryHandleMobileWorkspaceRequest(userText, activeWorkspace, workspaceRejected);
        if (!localWorkspaceReply.isEmpty()) {
            streamMobileImmediateReply(out, sid, history, "mobile-workspace", localWorkspaceReply, workspaceRejected);
            return;
        }
        String localDateTimeReply = tryHandleMobileDateTimeRequest(userText);
        if (!localDateTimeReply.isEmpty()) {
            streamMobileImmediateReply(out, sid, history, "mobile-date", localDateTimeReply, workspaceRejected);
            return;
        }
        String localGalleryReply = tryHandleMobileGalleryEditRequest(userText);
        if (!localGalleryReply.isEmpty()) {
            streamMobileImmediateReply(out, sid, history, "mobile-gallery", localGalleryReply, workspaceRejected);
            return;
        }
        String localCalendarReply = tryHandleMobileCalendarReadRequest(userText);
        if (!localCalendarReply.isEmpty()) {
            streamMobileImmediateReply(out, sid, history, "mobile-calendar", localCalendarReply, workspaceRejected);
            return;
        }
        JSONObject endpoint = endpointForSession(session);
        JSONArray modelMessages = history;
        String appContext = mobileAppContextForPrompt(userText, activeWorkspace, workspaceRejected);
        if (!appContext.isEmpty()) {
            modelMessages = new JSONArray();
            modelMessages.put(new JSONObject()
                    .put("role", "system")
                    .put("content", appContext));
            for (int i = 0; i < history.length(); i++) {
                modelMessages.put(history.get(i));
            }
        }

        writeHeaders(out, 200, "text/event-stream; charset=utf-8", -1);
        if (!workspaceRejected.isEmpty()) writeSse(out, workspaceRejectedEvent(workspaceRejected));
        writeSse(out, new JSONObject().put("type", "model_info").put("model", model));
        String reply;
        try {
            reply = callChat(endpoint, model, modelMessages);
            if (reply.trim().isEmpty()) reply = "The model returned an empty response.";
        } catch (Exception ex) {
            reply = "Mobile backend request failed (" + mobileProviderLabel(endpoint, model) + "): " + ex.getMessage();
        }
        history.put(new JSONObject().put("role", "assistant").put("content", reply).put("metadata", new JSONObject().put("model", model)));
        saveSessionHistory(sid, history);
        writeSse(out, new JSONObject().put("delta", reply));
        writeSse(out, new JSONObject().put("type", "metrics").put("data", new JSONObject().put("total_time", 0).put("model", model)));
        out.write("data: [DONE]\n\n".getBytes(StandardCharsets.UTF_8));
        out.flush();
    }

    private void streamMobileImmediateReply(OutputStream out, String sid, JSONArray history,
                                            String modelName, String reply, String workspaceRejected) throws Exception {
        history.put(new JSONObject()
                .put("role", "assistant")
                .put("content", reply)
                .put("metadata", new JSONObject().put("model", modelName)));
        saveSessionHistory(sid, history);
        writeHeaders(out, 200, "text/event-stream; charset=utf-8", -1);
        if (!workspaceRejected.isEmpty()) writeSse(out, workspaceRejectedEvent(workspaceRejected));
        writeSse(out, new JSONObject().put("type", "model_info").put("model", modelName));
        writeSse(out, new JSONObject().put("delta", reply));
        writeSse(out, new JSONObject().put("type", "metrics").put("data", new JSONObject().put("total_time", 0).put("model", modelName)));
        out.write("data: [DONE]\n\n".getBytes(StandardCharsets.UTF_8));
        out.flush();
    }

    private JSONObject workspaceRejectedEvent(String path) throws Exception {
        return new JSONObject()
                .put("type", "workspace_rejected")
                .put("data", new JSONObject().put("path", path));
    }

    private String mobileAppContextForPrompt(String userText, File activeWorkspace, String workspaceRejected) throws Exception {
        List<String> contexts = new ArrayList<>();
        contexts.add(mobileCurrentDateContext());
        String workspaceContext = mobileWorkspaceContextForPrompt(userText, activeWorkspace, workspaceRejected);
        if (!workspaceContext.isEmpty()) contexts.add(workspaceContext);
        String personalContext = mobilePersonalContextForPrompt(userText);
        if (!personalContext.isEmpty()) contexts.add(personalContext);
        String galleryContext = mobileGalleryContextForPrompt(userText);
        if (!galleryContext.isEmpty()) contexts.add(galleryContext);
        String calendarContext = mobileCalendarContextForPrompt(userText);
        if (!calendarContext.isEmpty()) contexts.add(calendarContext);
        return String.join("\n\n", contexts);
    }

    private String mobileCurrentDateContext() {
        return "Current Android local date/time: " + mobileCurrentDateTimeLabel() + ". "
                + "For questions about the current date, current day, or current time, answer from this value. "
                + "Do not infer today's date from Calendar event dates or prior chat messages.";
    }

    private String tryHandleMobileDateTimeRequest(String userText) {
        String q = valueOr(userText, "").toLowerCase(Locale.US).trim();
        q = q.replaceAll("[?!.,]+", " ").replaceAll("\\s+", " ");
        boolean asksDate = q.contains("what day is it")
                || q.contains("what date is it")
                || q.contains("what day is today")
                || q.contains("what is today's date")
                || q.contains("what is todays date")
                || q.contains("what's today's date")
                || q.contains("whats todays date")
                || q.contains("today's date")
                || q.contains("todays date")
                || q.contains("current date")
                || q.contains("current day")
                || q.contains("day of the week");
        boolean asksTime = q.contains("what time is it")
                || q.contains("current time")
                || q.contains("time right now")
                || q.contains("what's the time")
                || q.contains("whats the time");
        if (!asksDate && !asksTime) return "";
        if (asksDate && asksTime) {
            return "Today is " + mobileCurrentDateLabel() + ". The current Android local time is " + mobileCurrentTimeLabel() + ".";
        }
        if (asksTime) {
            return "The current Android local time is " + mobileCurrentTimeLabel() + " on " + mobileCurrentDateLabel() + ".";
        }
        return "Today is " + mobileCurrentDateLabel() + ".";
    }

    private String tryHandleMobileWorkspaceRequest(String userText, File activeWorkspace, String workspaceRejected) throws Exception {
        if (!mobileIsWorkspaceStatusRequest(userText)) return "";
        StringBuilder sb = new StringBuilder();
        sb.append("Android standalone workspace check:\n");
        if (!workspaceRejected.isEmpty()) {
            sb.append("- The previously saved workspace path `").append(workspaceRejected).append("` is not usable in Android standalone and has been cleared. ");
            sb.append("That looks like a PC or saved Folders/RAG path, not an Android-local workspace.\n");
        }
        if (activeWorkspace == null) {
            sb.append("- No active Android workspace is selected right now.\n");
            sb.append("- Open the Workspace picker and choose one of the Android-local roots: Documents, Downloads, App Workspace, or Scratch.\n");
        } else {
            sb.append("- Active Android workspace: `").append(activeWorkspace.getCanonicalPath()).append("`.\n");
            sb.append("- This is local to this Android device, not your PC filesystem.\n");
            sb.append("- Current contents: ").append(mobileWorkspaceOneLineSummary(activeWorkspace, 10)).append("\n");
            sb.append("- Visible tree:\n").append(mobileWorkspaceTreeSummary(
                    activeWorkspace,
                    MAX_MOBILE_WORKSPACE_CONTEXT_ENTRIES,
                    MAX_MOBILE_WORKSPACE_CONTEXT_DEPTH
            )).append("\n");
        }
        JSONArray dirs = loadArray(PREF_PERSONAL_DIRECTORIES);
        if (dirs.length() > 0) {
            sb.append("- Saved Folders/RAG entries are separate from Workspace. They are saved reference paths in standalone mode, not the active Android workspace. ");
            sb.append("Saved entry examples: ");
            int max = Math.min(dirs.length(), 3);
            for (int i = 0; i < max; i++) {
                if (i > 0) sb.append(", ");
                sb.append("`").append(dirs.optString(i)).append("`");
            }
            if (dirs.length() > max) sb.append(", ...");
            sb.append(".");
        }
        return sb.toString().trim();
    }

    private String mobileWorkspaceContextForPrompt(String userText, File activeWorkspace, String workspaceRejected) throws Exception {
        if (!mobileMentionsWorkspaceOrFiles(userText) && activeWorkspace == null && workspaceRejected.isEmpty()) return "";
        StringBuilder sb = new StringBuilder();
        sb.append("You are running inside Odysseus Android standalone. ");
        sb.append("Workspace is Android-local device storage, separate from saved Folders/RAG path entries. ");
        sb.append("Do not say Android standalone needs Connect to PC for the active Android workspace.\n");
        if (!workspaceRejected.isEmpty()) {
            sb.append("A posted workspace path was rejected and will be cleared client-side: ").append(workspaceRejected).append(". ");
            sb.append("Treat it as stale or PC-only, not active.\n");
        }
        if (activeWorkspace == null) {
            sb.append("Active Android workspace: none selected. The user can select Documents, Downloads, App Workspace, or Scratch from the Workspace picker.");
            return sb.toString();
        }
        sb.append("Active Android workspace: ").append(activeWorkspace.getCanonicalPath()).append("\n");
        sb.append("Workspace summary: ").append(mobileWorkspaceOneLineSummary(activeWorkspace, 14)).append("\n");
        sb.append("Visible workspace tree, bounded for prompt size:\n");
        sb.append(mobileWorkspaceTreeSummary(
                activeWorkspace,
                MAX_MOBILE_WORKSPACE_CONTEXT_ENTRIES,
                MAX_MOBILE_WORKSPACE_CONTEXT_DEPTH
        )).append("\n");
        String previews = mobileWorkspaceTextPreviews(
                activeWorkspace,
                MAX_MOBILE_WORKSPACE_CONTEXT_PREVIEW_FILES,
                MAX_MOBILE_WORKSPACE_CONTEXT_PREVIEW_BYTES
        );
        if (!previews.isEmpty()) {
            sb.append("Small text file previews from the visible workspace:\n").append(previews).append("\n");
        } else {
            sb.append("No readable text file previews were available from the visible workspace.\n");
        }
        sb.append("If asked about saved Folders/RAG entries such as PC paths, explain that those are separate from the active Android workspace.");
        return sb.toString();
    }

    private boolean mobileIsWorkspaceStatusRequest(String userText) {
        String q = valueOr(userText, "").toLowerCase(Locale.US).trim();
        q = q.replaceAll("[?!.,]+", " ").replaceAll("\\s+", " ");
        if (!mobileMentionsWorkspace(userText)) return false;
        return q.contains("workspace check")
                || q.contains("check workspace")
                || q.contains("can you see")
                || q.contains("can u see")
                || q.contains("do you see")
                || q.contains("what files")
                || q.contains("which files")
                || q.contains("list files")
                || q.contains("show files")
                || q.equals("workspace")
                || q.equals("my workspace");
    }

    private boolean mobileMentionsWorkspace(String userText) {
        String q = valueOr(userText, "").toLowerCase(Locale.US);
        return q.contains("workspace")
                || q.contains("work space")
                || q.contains("active folder")
                || q.contains("selected folder")
                || q.contains("check workspace")
                || q.contains("use this folder");
    }

    private boolean mobileMentionsWorkspaceOrFiles(String userText) {
        String q = valueOr(userText, "").toLowerCase(Locale.US);
        return mobileMentionsWorkspace(userText)
                || q.contains("folder")
                || q.contains("folders")
                || q.contains("file")
                || q.contains("files")
                || q.contains("document")
                || q.contains("documents")
                || q.contains("path")
                || q.contains("paths");
    }

    private String mobileWorkspaceOneLineSummary(File root, int maxEntries) throws Exception {
        if (root == null || !root.isDirectory()) return "workspace folder is not available";
        File[] children = root.listFiles();
        if (children == null) {
            return mobileIsPublicExternalWorkspace(root)
                    ? MOBILE_PUBLIC_WORKSPACE_ACCESS_DETAIL
                    : "workspace folder is not readable";
        }
        if (children.length == 0) return "empty";
        List<File> sorted = new ArrayList<>(Arrays.asList(children));
        Collections.sort(sorted, (a, b) -> {
            if (a.isDirectory() != b.isDirectory()) return a.isDirectory() ? -1 : 1;
            return a.getName().compareToIgnoreCase(b.getName());
        });
        StringBuilder sb = new StringBuilder();
        int shown = 0;
        for (File child : sorted) {
            if (child.getName().startsWith(".")) continue;
            if (shown >= maxEntries) break;
            if (shown > 0) sb.append(", ");
            sb.append(child.isDirectory() ? "[folder] " : "[file] ").append(child.getName());
            shown++;
        }
        if (shown == 0) return "empty";
        int hidden = sorted.size() - shown;
        if (hidden > 0) sb.append(", ... ").append(hidden).append(" more");
        return sb.toString();
    }

    private String mobileWorkspaceTreeSummary(File root, int maxEntries, int maxDepth) throws Exception {
        if (root == null || !root.isDirectory()) return "workspace folder is not available";
        File[] children = root.listFiles();
        if (children == null) {
            return mobileIsPublicExternalWorkspace(root)
                    ? MOBILE_PUBLIC_WORKSPACE_ACCESS_DETAIL
                    : "workspace folder is not readable";
        }
        if (children.length == 0) return "(empty)";
        StringBuilder sb = new StringBuilder();
        int[] shown = new int[]{0};
        int[] hidden = new int[]{0};
        appendMobileWorkspaceTree(root, root, 0, Math.max(0, maxDepth), Math.max(1, maxEntries), shown, hidden, sb);
        if (shown[0] == 0) return "(empty)";
        if (hidden[0] > 0) sb.append("... ").append(hidden[0]).append(" more not shown\n");
        return sb.toString().trim();
    }

    private void appendMobileWorkspaceTree(File root, File dir, int depth, int maxDepth,
                                           int maxEntries, int[] shown, int[] hidden,
                                           StringBuilder sb) throws Exception {
        File[] children = dir.listFiles();
        if (children == null) {
            if (shown[0] < maxEntries) {
                String rel = mobileWorkspaceRelPath(root, dir);
                if (!rel.isEmpty()) {
                    sb.append("[unreadable folder] ").append(rel).append("\n");
                    shown[0]++;
                }
            }
            return;
        }
        List<File> sorted = new ArrayList<>(Arrays.asList(children));
        Collections.sort(sorted, (a, b) -> {
            if (a.isDirectory() != b.isDirectory()) return a.isDirectory() ? -1 : 1;
            return a.getName().compareToIgnoreCase(b.getName());
        });
        for (File child : sorted) {
            if (child.getName().startsWith(".")) continue;
            if (mobileIsDeprecatedPrivateWorkspaceFolder(child)) continue;
            if (shown[0] >= maxEntries) {
                hidden[0]++;
                continue;
            }
            sb.append(child.isDirectory() ? "[folder] " : child.isFile() ? "[file] " : "[other] ")
                    .append(mobileWorkspaceRelPath(root, child))
                    .append("\n");
            shown[0]++;
            if (child.isDirectory() && depth < maxDepth) {
                appendMobileWorkspaceTree(root, child, depth + 1, maxDepth, maxEntries, shown, hidden, sb);
            }
        }
    }

    private String mobileWorkspaceTextPreviews(File root, int maxFiles, int maxBytes) throws Exception {
        if (root == null || !root.isDirectory()) return "";
        List<File> files = new ArrayList<>();
        collectMobileWorkspacePreviewFiles(root, root, 0, MAX_MOBILE_WORKSPACE_CONTEXT_DEPTH, Math.max(1, maxFiles), files);
        StringBuilder sb = new StringBuilder();
        for (File file : files) {
            String preview = mobileWorkspaceReadTextPreview(file, maxBytes);
            if (preview.isEmpty()) continue;
            sb.append("--- ").append(mobileWorkspaceRelPath(root, file)).append(" (")
                    .append(file.length()).append(" bytes) ---\n")
                    .append(preview).append("\n");
        }
        return sb.toString().trim();
    }

    private void collectMobileWorkspacePreviewFiles(File root, File dir, int depth, int maxDepth,
                                                    int maxFiles, List<File> out) throws Exception {
        if (out.size() >= maxFiles) return;
        File[] children = dir.listFiles();
        if (children == null) return;
        List<File> sorted = new ArrayList<>(Arrays.asList(children));
        Collections.sort(sorted, (a, b) -> {
            if (a.isFile() != b.isFile()) return a.isFile() ? -1 : 1;
            return a.getName().compareToIgnoreCase(b.getName());
        });
        for (File child : sorted) {
            if (out.size() >= maxFiles) return;
            if (child.getName().startsWith(".")) continue;
            if (mobileIsDeprecatedPrivateWorkspaceFolder(child)) continue;
            if (child.isFile() && child.length() <= MAX_MOBILE_WORKSPACE_TEXT_BYTES && mobileWorkspaceTextName(child.getName())) {
                try {
                    if (!mobileWorkspaceIsProbablyBinary(child)) out.add(child);
                } catch (IOException ignored) {
                    // Ignore unreadable preview candidates; the tree still reports their names.
                }
            }
        }
        if (depth >= maxDepth) return;
        for (File child : sorted) {
            if (out.size() >= maxFiles) return;
            if (child.getName().startsWith(".")) continue;
            if (mobileIsDeprecatedPrivateWorkspaceFolder(child)) continue;
            if (child.isDirectory()) {
                collectMobileWorkspacePreviewFiles(root, child, depth + 1, maxDepth, maxFiles, out);
            }
        }
    }

    private String mobileWorkspaceReadTextPreview(File file, int maxBytes) throws IOException {
        long size = Math.max(0, file.length());
        if (size == 0) return "";
        int cap = (int) Math.min(size, Math.max(256, maxBytes));
        byte[] buffer = new byte[cap];
        int read;
        try (FileInputStream in = new FileInputStream(file)) {
            read = in.read(buffer);
        }
        if (read <= 0) return "";
        String text = new String(buffer, 0, read, StandardCharsets.UTF_8);
        if (text.indexOf('\u0000') >= 0) return "";
        text = text.replace("\r\n", "\n").replace('\r', '\n').trim();
        if (text.isEmpty()) return "";
        if (size > read) text += "\n...";
        return text;
    }

    private String mobilePersonalContextForPrompt(String userText) throws Exception {
        String q = valueOr(userText, "").toLowerCase(Locale.US);
        boolean wantsPersonalDocs = q.contains("rag")
                || q.contains("mcp")
                || q.contains("saved folder")
                || q.contains("saved folders")
                || q.contains("indexed folder")
                || q.contains("indexed folders")
                || (!mobileMentionsWorkspace(userText) && (
                        q.contains("folder")
                        || q.contains("folders")
                        || q.contains("document")
                        || q.contains("documents")));
        if (!wantsPersonalDocs) return "";

        JSONArray dirs = loadArray(PREF_PERSONAL_DIRECTORIES);
        StringBuilder sb = new StringBuilder();
        sb.append("You are running inside Odysseus Android standalone. ");
        sb.append("Saved Folders/RAG path entries are separate from the active Android Workspace picker. ");
        sb.append("They may include PC paths such as D:/, but those are reference/indexing entries in standalone mode and are not the active Android workspace. ");
        sb.append("For PC folder content indexing, use Connect to PC so the Python backend and RAG MCP server run on the computer.\n");
        if (dirs.length() == 0) {
            sb.append("Saved Folders/RAG entries: none.");
            return sb.toString();
        }
        sb.append("Saved Folders/RAG entries:\n");
        int max = Math.min(dirs.length(), 12);
        for (int i = 0; i < max; i++) {
            sb.append("- ").append(dirs.optString(i)).append("\n");
        }
        if (dirs.length() > max) sb.append("... ").append(dirs.length() - max).append(" more folder paths not shown.");
        return sb.toString().trim();
    }

    private String mobileCurrentDateTimeLabel() {
        return new SimpleDateFormat("EEEE, MMMM d, yyyy 'at' h:mm a z", Locale.US).format(new Date());
    }

    private String mobileCurrentDateLabel() {
        return new SimpleDateFormat("EEEE, MMMM d, yyyy", Locale.US).format(new Date());
    }

    private String mobileCurrentTimeLabel() {
        return new SimpleDateFormat("h:mm a z", Locale.US).format(new Date());
    }

    private String mobileGalleryContextForPrompt(String userText) throws Exception {
        String q = valueOr(userText, "").toLowerCase(Locale.US);
        boolean wantsGallery = q.contains("gallery")
                || q.contains("photo")
                || q.contains("photos")
                || q.contains("image")
                || q.contains("images")
                || q.contains("picture")
                || q.contains("pictures")
                || q.contains("camera roll")
                || q.contains("upload");
        if (!wantsGallery) return "";

        JSONArray images = loadArray(PREF_GALLERY_IMAGES);
        List<JSONObject> active = new ArrayList<>();
        for (int i = 0; i < images.length(); i++) {
            JSONObject image = images.optJSONObject(i);
            if (image != null && image.optBoolean("is_active", true)) {
                active.add(image);
            }
        }
        Collections.sort(active, (a, b) -> -Long.compare(galleryTimestamp(a), galleryTimestamp(b)));

        StringBuilder sb = new StringBuilder();
        sb.append("You are running inside the Odysseus Android app. The user's local Gallery metadata is available below. ");
        sb.append("Use it to answer questions about saved Gallery photos/images instead of saying you cannot see the Gallery. ");
        sb.append("These are local app URLs; refer to image IDs and names when useful. ");
        sb.append("Android standalone chat can list and reason from this metadata. Full AI image transforms are handled by the Gallery editor or the server Agent tools.\n");
        if (active.isEmpty()) {
            sb.append("Gallery: no saved images.");
            return sb.toString();
        }
        sb.append("Gallery images, newest first:\n");
        int max = Math.min(active.size(), 8);
        for (int i = 0; i < max; i++) {
            JSONObject image = active.get(i);
            String id = image.optString("id", "");
            String filename = image.optString("filename", "");
            String name = image.optString("prompt", "");
            if (name.trim().isEmpty()) name = filename;
            sb.append("- id: ").append(id)
                    .append("; name: ").append(name)
                    .append("; url: /api/generated-image/").append(filename);
            String model = image.optString("model", "");
            if (!model.isEmpty()) sb.append("; model: ").append(model);
            String tags = (image.optString("tags", "") + "," + image.optString("ai_tags", "")).replaceAll("^,+|,+$", "");
            if (!tags.trim().isEmpty()) sb.append("; tags: ").append(tags);
            Object width = image.opt("width");
            Object height = image.opt("height");
            if (width != null && height != null && width != JSONObject.NULL && height != JSONObject.NULL) {
                sb.append("; size: ").append(width).append("x").append(height);
            }
            sb.append("\n");
        }
        if (active.size() > max) {
            sb.append("... ").append(active.size() - max).append(" more images not shown.");
        }
        return sb.toString();
    }

    private String mobileCalendarContextForPrompt(String userText) throws Exception {
        if (!mentionsCalendarIntent(userText)) return "";

        JSONArray cals = loadArray(PREF_CALENDAR_CALS);
        List<JSONObject> events = activeCalendarEventsSorted();
        String today = todayDateString();
        String tomorrow = addDaysToDate(today, 1);
        String upcomingEnd = addDaysToDate(today, 30);

        StringBuilder sb = new StringBuilder();
        sb.append("You are running inside the Odysseus Android app. The user's local in-app Calendar metadata is available below. ");
        sb.append("Use it to answer questions about the user's in-app calendar, schedule, meetings, appointments, and events instead of saying you cannot access the calendar. ");
        sb.append("If the requested detail is not present below, say what calendar data is available and what is missing. ");
        sb.append("Current Android local date: ").append(mobileCurrentDateLabel()).append(" (").append(today).append(").\n");
        sb.append("Calendar summary: ").append(cals.length()).append(" calendars, ").append(events.size()).append(" active saved events total.\n");

        if (cals.length() == 0) {
            sb.append("Calendars: none saved.\n");
        } else {
            sb.append("Calendars:\n");
            int maxCals = Math.min(cals.length(), 8);
            for (int i = 0; i < maxCals; i++) {
                JSONObject cal = cals.optJSONObject(i);
                if (cal == null) continue;
                sb.append("- id: ").append(cal.optString("href", ""))
                        .append("; name: ").append(shortPromptText(cal.optString("name", "Calendar"), 80))
                        .append("; color: ").append(cal.optString("color", ""))
                        .append("; source: ").append(cal.optString("source", "local"))
                        .append("\n");
            }
            if (cals.length() > maxCals) sb.append("... ").append(cals.length() - maxCals).append(" more calendars not shown.\n");
        }

        if (events.isEmpty()) {
            sb.append("Calendar events: none saved.");
            return sb.toString();
        }

        appendCalendarPromptEvents(sb, "Events today (" + today + ")", filterCalendarEventsOnDate(events, today), 8);
        appendCalendarPromptEvents(sb, "Events tomorrow (" + tomorrow + ")", filterCalendarEventsOnDate(events, tomorrow), 8);
        appendCalendarPromptEvents(sb, "Upcoming events through " + upcomingEnd, filterCalendarEventsBetween(events, today, upcomingEnd), 16);
        appendCalendarPromptEvents(sb, "Recent past events", recentPastCalendarEvents(events, today, 6), 6);
        return sb.toString();
    }

    private String tryHandleMobileCalendarReadRequest(String userText) throws Exception {
        if (!mentionsCalendarIntent(userText)) return "";
        String q = valueOr(userText, "").toLowerCase(Locale.US).trim();
        boolean compactCalendarFollowup = q.matches("^(the\\s+)?(in[- ]app\\s+)?calendar[?.!\\s]*$");
        boolean accessQuestion = q.contains("can you see")
                || q.contains("can you access")
                || q.contains("do you see")
                || q.contains("do you have access")
                || q.contains("are you able to see")
                || q.contains("read my calendar")
                || q.contains("use my calendar");
        boolean agendaQuestion = q.contains("what's on")
                || q.contains("what is on")
                || q.contains("whats on")
                || q.contains("agenda")
                || q.contains("schedule")
                || q.contains("show")
                || q.contains("list")
                || q.contains("check")
                || q.contains("do i have")
                || q.contains("anything today")
                || q.contains("anything tomorrow")
                || q.contains("next event")
                || q.contains("next meeting")
                || q.contains("next appointment");
        if (!compactCalendarFollowup && !accessQuestion && !agendaQuestion) return "";

        JSONArray cals = loadArray(PREF_CALENDAR_CALS);
        List<JSONObject> events = activeCalendarEventsSorted();
        String today = todayDateString();
        StringBuilder sb = new StringBuilder();
        sb.append("Yes - I can read the in-app Calendar in Android standalone now. ");
        sb.append("I can see ").append(cals.length()).append(cals.length() == 1 ? " calendar" : " calendars")
                .append(" and ").append(events.size()).append(events.size() == 1 ? " active saved event" : " active saved events").append(" total.");
        if (events.isEmpty()) {
            sb.append(" There are no saved calendar events yet.");
            return sb.toString();
        }

        if (q.contains("tomorrow")) {
            appendCalendarReplyEvents(sb, "Tomorrow", filterCalendarEventsOnDate(events, addDaysToDate(today, 1)), 8);
        } else if (q.contains("today")) {
            appendCalendarReplyEvents(sb, "Today", filterCalendarEventsOnDate(events, today), 8);
        } else if (q.contains("next")) {
            appendCalendarReplyEvents(sb, "Next events", filterCalendarEventsBetween(events, today, addDaysToDate(today, 365)), 8);
        } else {
            List<JSONObject> todayEvents = filterCalendarEventsOnDate(events, today);
            if (!todayEvents.isEmpty()) appendCalendarReplyEvents(sb, "Today", todayEvents, 8);
            appendCalendarReplyEvents(sb, "Upcoming", filterCalendarEventsBetween(events, today, addDaysToDate(today, 30)), 8);
        }
        return sb.toString();
    }

    private boolean mentionsCalendarIntent(String userText) {
        String q = valueOr(userText, "").toLowerCase(Locale.US);
        return q.contains("calendar")
                || q.contains("schedule")
                || q.contains("agenda")
                || q.contains("event")
                || q.contains("events")
                || q.contains("meeting")
                || q.contains("meetings")
                || q.contains("appointment")
                || q.contains("appointments")
                || q.contains("class")
                || q.contains("classes")
                || q.contains("what do i have today")
                || q.contains("what do i have tomorrow");
    }

    private List<JSONObject> activeCalendarEventsSorted() throws Exception {
        JSONArray events = loadArray(PREF_CALENDAR_EVENTS);
        List<JSONObject> out = new ArrayList<>();
        for (int i = 0; i < events.length(); i++) {
            JSONObject event = events.optJSONObject(i);
            if (event == null) continue;
            if ("cancelled".equalsIgnoreCase(event.optString("status", ""))) continue;
            out.add(mobileCalendarClientEvent(event));
        }
        Collections.sort(out, (a, b) -> a.optString("dtstart", "").compareTo(b.optString("dtstart", "")));
        return out;
    }

    private List<JSONObject> filterCalendarEventsOnDate(List<JSONObject> events, String date) {
        List<JSONObject> out = new ArrayList<>();
        for (JSONObject event : events) {
            if (calendarEventOccursOn(event, date)) out.add(event);
        }
        return out;
    }

    private List<JSONObject> filterCalendarEventsBetween(List<JSONObject> events, String start, String end) {
        List<JSONObject> out = new ArrayList<>();
        for (JSONObject event : events) {
            String evStart = calendarDateKey(event.optString("dtstart", ""));
            String evEnd = calendarDateKey(event.optString("dtend", ""));
            if (evEnd.isEmpty()) evEnd = evStart;
            boolean startsBeforeEnd = end == null || end.isEmpty() || evStart.compareTo(end) <= 0;
            boolean endsAfterStart = start == null || start.isEmpty() || evEnd.compareTo(start) >= 0;
            if (startsBeforeEnd && endsAfterStart) out.add(event);
        }
        return out;
    }

    private List<JSONObject> recentPastCalendarEvents(List<JSONObject> events, String today, int limit) {
        List<JSONObject> past = new ArrayList<>();
        for (JSONObject event : events) {
            String evEnd = calendarDateKey(event.optString("dtend", event.optString("dtstart", "")));
            if (evEnd.compareTo(today) < 0) past.add(event);
        }
        Collections.sort(past, (a, b) -> b.optString("dtstart", "").compareTo(a.optString("dtstart", "")));
        if (past.size() <= limit) return past;
        return new ArrayList<>(past.subList(0, limit));
    }

    private boolean calendarEventOccursOn(JSONObject event, String date) {
        String evStart = calendarDateKey(event.optString("dtstart", ""));
        String evEnd = calendarDateKey(event.optString("dtend", ""));
        if (evStart.isEmpty()) return false;
        if (!event.optBoolean("all_day", false)) return evStart.equals(date);
        if (evEnd.isEmpty() || evEnd.equals(evStart)) return evStart.equals(date);
        return evStart.compareTo(date) <= 0 && evEnd.compareTo(date) > 0;
    }

    private void appendCalendarPromptEvents(StringBuilder sb, String label, List<JSONObject> events, int max) {
        sb.append(label).append(":\n");
        if (events.isEmpty()) {
            sb.append("- none\n");
            return;
        }
        int count = Math.min(events.size(), max);
        for (int i = 0; i < count; i++) {
            sb.append("- ").append(calendarEventPromptLine(events.get(i))).append("\n");
        }
        if (events.size() > max) sb.append("... ").append(events.size() - max).append(" more events not shown.\n");
    }

    private void appendCalendarReplyEvents(StringBuilder sb, String label, List<JSONObject> events, int max) {
        sb.append("\n\n").append(label).append(":");
        if (events.isEmpty()) {
            sb.append("\n- None.");
            return;
        }
        int count = Math.min(events.size(), max);
        for (int i = 0; i < count; i++) {
            sb.append("\n- ").append(calendarEventReplyLine(events.get(i)));
        }
        if (events.size() > max) sb.append("\n- Plus ").append(events.size() - max).append(" more.");
    }

    private String calendarEventPromptLine(JSONObject event) {
        StringBuilder sb = new StringBuilder();
        sb.append("uid: ").append(event.optString("uid", ""));
        sb.append("; title: ").append(shortPromptText(event.optString("summary", ""), 120));
        sb.append("; calendar: ").append(shortPromptText(event.optString("calendar", ""), 80));
        sb.append("; start: ").append(event.optString("dtstart", ""));
        sb.append("; end: ").append(event.optString("dtend", ""));
        sb.append("; all_day: ").append(event.optBoolean("all_day", false));
        String location = event.optString("location", "");
        if (!location.trim().isEmpty()) sb.append("; location: ").append(shortPromptText(location, 100));
        String description = event.optString("description", "");
        if (!description.trim().isEmpty()) sb.append("; description: ").append(shortPromptText(description, 160));
        String rrule = event.optString("rrule", "");
        if (!rrule.trim().isEmpty()) sb.append("; rrule: ").append(shortPromptText(rrule, 120));
        return sb.toString();
    }

    private String calendarEventReplyLine(JSONObject event) {
        String title = event.optString("summary", "").trim();
        if (title.isEmpty()) title = "Untitled event";
        String time = event.optBoolean("all_day", false)
                ? event.optString("dtstart", "")
                : event.optString("dtstart", "") + " to " + event.optString("dtend", "");
        String line = title + " - " + time;
        String location = event.optString("location", "").trim();
        if (!location.isEmpty()) line += " @ " + location;
        return line;
    }

    private String shortPromptText(String raw, int max) {
        String text = valueOr(raw, "").replace('\n', ' ').replace('\r', ' ').trim();
        while (text.contains("  ")) text = text.replace("  ", " ");
        return text.length() > max ? text.substring(0, Math.max(0, max - 3)) + "..." : text;
    }

    private String tryHandleMobileGalleryEditRequest(String userText) throws Exception {
        String q = valueOr(userText, "").toLowerCase(Locale.US);
        boolean mentionsGallery = q.contains("gallery")
                || q.contains("photo")
                || q.contains("photos")
                || q.contains("image")
                || q.contains("images")
                || q.contains("picture")
                || q.contains("pictures")
                || q.contains("camera roll");
        if (!mentionsGallery) return "";

        String action = "";
        if (q.contains("sharpen")) {
            action = "sharpen";
        } else if (q.contains("denoise") || q.contains("de-noise") || q.contains("smooth")) {
            action = "denoise";
        } else if (q.contains("upscale") || q.contains("enlarge") || q.contains("increase resolution")) {
            action = "upscale";
        } else if (q.contains("remove background")
                || q.contains("background remove")
                || q.contains("remove bg")
                || q.contains("bg remove")
                || q.contains("rembg")
                || q.contains("bgremove")) {
            action = "remove-bg";
        }
        if (action.isEmpty()) {
            boolean unsupportedEdit = q.contains("inpaint")
                    || q.contains("outpaint")
                    || q.contains("harmonize");
            if (unsupportedEdit) {
                return "Android standalone can edit Gallery images locally with sharpen, denoise, upscale, and background removal. Inpaint and outpaint need a drawn mask in the Gallery editor.";
            }
            return "";
        }

        JSONObject source = newestGalleryImage();
        if (source == null) {
            return "I can edit Gallery images, but your Android Gallery is empty right now.";
        }
        Bitmap bitmap = loadGalleryBitmap(source);
        if (bitmap == null) {
            return "I found a Gallery image record, but the image file is missing on this device.";
        }

        Bitmap edited;
        if ("sharpen".equals(action)) {
            edited = sharpenBitmap(bitmap, 65);
        } else if ("denoise".equals(action)) {
            edited = denoiseBitmap(bitmap, 35);
        } else if ("remove-bg".equals(action)) {
            edited = removeBackgroundBitmap(bitmap, null, null, 0.7);
        } else {
            edited = upscaleBitmap(bitmap, q.contains("4x") || q.contains("4 x") ? 4 : 2);
        }
        JSONObject saved = saveEditedGalleryBitmap(source, edited, action);
        return "Done - I " + galleryActionPastTense(action) + " the newest Gallery image and saved it as a new Gallery copy. Image ID: "
                + saved.optString("id") + ".";
    }

    private JSONObject newestGalleryImage() throws Exception {
        JSONArray images = loadArray(PREF_GALLERY_IMAGES);
        JSONObject newest = null;
        for (int i = 0; i < images.length(); i++) {
            JSONObject image = images.optJSONObject(i);
            if (image == null || !image.optBoolean("is_active", true)) continue;
            if (newest == null || galleryTimestamp(image) > galleryTimestamp(newest)) newest = image;
        }
        return newest;
    }

    private JSONObject saveEditedGalleryBitmap(JSONObject source, Bitmap bitmap, String action) throws Exception {
        ByteArrayOutputStream baos = new ByteArrayOutputStream();
        bitmap.compress(Bitmap.CompressFormat.PNG, 100, baos);
        byte[] data = baos.toByteArray();
        String filename = UUID.randomUUID().toString().replace("-", "").substring(0, 12) + ".png";
        File dst = new File(galleryDir(), filename);
        try (FileOutputStream fos = new FileOutputStream(dst)) {
            fos.write(data);
        }

        String now = isoTimestamp(System.currentTimeMillis());
        String prompt = source.optString("prompt", "Gallery image");
        String tags = source.optString("tags", "");
        String actionTag = action;
        if (!tags.contains("edited")) tags = tags.isEmpty() ? "edited" : tags + ",edited";
        if (!tags.contains(actionTag)) tags = tags.isEmpty() ? actionTag : tags + "," + actionTag;

        JSONObject image = new JSONObject()
                .put("id", UUID.randomUUID().toString())
                .put("filename", filename)
                .put("url", "/api/generated-image/" + filename)
                .put("prompt", prompt + " (" + galleryActionLabel(action) + ")")
                .put("model", "mobile-edit:" + action)
                .put("size", bitmap.getWidth() + "x" + bitmap.getHeight())
                .put("quality", "mobile")
                .put("tags", tags)
                .put("ai_tags", source.optString("ai_tags", ""))
                .put("user_tags", tags)
                .put("session_id", source.has("session_id") ? source.opt("session_id") : JSONObject.NULL)
                .put("session_name", source.has("session_name") ? source.opt("session_name") : JSONObject.NULL)
                .put("album_id", source.has("album_id") ? source.opt("album_id") : JSONObject.NULL)
                .put("is_active", true)
                .put("favorite", false)
                .put("taken_at", JSONObject.NULL)
                .put("camera", source.has("camera") ? source.opt("camera") : JSONObject.NULL)
                .put("gps", source.has("gps") ? source.opt("gps") : JSONObject.NULL)
                .put("width", bitmap.getWidth())
                .put("height", bitmap.getHeight())
                .put("file_size", data.length)
                .put("created_at", now)
                .put("updated_at", now)
                .put("owner", "mobile")
                .put("file_hash", sha256Hex(data));
        JSONArray images = loadArray(PREF_GALLERY_IMAGES);
        images.put(image);
        saveArray(PREF_GALLERY_IMAGES, images);
        return image;
    }

    private String galleryActionLabel(String action) {
        if ("denoise".equals(action)) return "Denoised";
        if ("upscale".equals(action)) return "Upscaled";
        if ("remove-bg".equals(action)) return "Background Removed";
        return "Sharpened";
    }

    private String galleryActionPastTense(String action) {
        if ("denoise".equals(action)) return "denoised";
        if ("upscale".equals(action)) return "upscaled";
        if ("remove-bg".equals(action)) return "removed the background from";
        return "sharpened";
    }

    private JSONObject endpointForSession(JSONObject session) throws Exception {
        String model = session.optString("model", "").trim();
        String endpointId = session.optString("endpoint_id");
        JSONObject ep = findEndpoint(endpointId);
        if (ep != null && (model.isEmpty() || endpointHasModel(ep, model))) return ep;
        String endpointUrl = session.optString("endpoint_url");
        JSONObject urlMatch = endpointForChatUrl(endpointUrl);
        if (urlMatch != null && (model.isEmpty() || endpointCanServeModel(urlMatch, model))) {
            repairSessionEndpoint(session, urlMatch);
            return urlMatch;
        }
        JSONObject modelMatch = model.isEmpty() ? null : endpointForModel(model);
        if (modelMatch != null) {
            repairSessionEndpoint(session, modelMatch);
            return modelMatch;
        }
        if (ep != null) return ep;
        if (urlMatch != null) return urlMatch;
        throw new IOException("No endpoint configured for this session");
    }

    private JSONObject endpointForChatUrl(String endpointUrl) throws Exception {
        if (endpointUrl == null || endpointUrl.trim().isEmpty()) return null;
        String normalized = comparableBase(endpointUrl);
        JSONArray endpoints = loadArray(PREF_ENDPOINTS);
        for (int i = 0; i < endpoints.length(); i++) {
            JSONObject cand = endpoints.getJSONObject(i);
            if (normalized.equals(comparableBase(cand.optString("base_url")))
                    || chatUrl(cand.optString("base_url")).equals(endpointUrl)) return cand;
        }
        return null;
    }

    private JSONObject endpointForModel(String model) throws Exception {
        JSONArray endpoints = loadArray(PREF_ENDPOINTS);
        for (int i = 0; i < endpoints.length(); i++) {
            JSONObject cand = endpoints.getJSONObject(i);
            if (!cand.optBoolean("is_enabled", true)) continue;
            if (endpointHasModel(cand, model)) return cand;
        }
        return null;
    }

    private boolean endpointCanServeModel(JSONObject endpoint, String model) {
        JSONArray models = endpoint == null ? null : endpoint.optJSONArray("models");
        return models == null || models.length() == 0 || endpointHasModel(endpoint, model);
    }

    private boolean endpointHasModel(JSONObject endpoint, String model) {
        if (endpoint == null) return false;
        String requested = valueOr(model, "").trim();
        if (requested.isEmpty()) return true;
        String providerRequested = providerModelId(endpoint, requested);
        JSONArray models = endpoint.optJSONArray("models");
        for (int i = 0; models != null && i < models.length(); i++) {
            String candidate = models.optString(i, "").trim();
            if (candidate.equals(requested) || candidate.equals(providerRequested)) return true;
            if (providerModelId(endpoint, candidate).equals(providerRequested)) return true;
        }
        return false;
    }

    private void repairSessionEndpoint(JSONObject session, JSONObject endpoint) throws Exception {
        if (session == null || endpoint == null) return;
        String sid = session.optString("id", "");
        String endpointId = endpoint.optString("id", "");
        String endpointUrl = chatUrl(endpoint.optString("base_url"));
        session.put("endpoint_id", endpointId);
        session.put("endpoint_url", endpointUrl);
        if (sid.isEmpty()) return;
        JSONArray sessions = loadArray(PREF_SESSIONS);
        for (int i = 0; i < sessions.length(); i++) {
            JSONObject s = sessions.optJSONObject(i);
            if (s == null || !sid.equals(s.optString("id"))) continue;
            s.put("endpoint_id", endpointId);
            s.put("endpoint_url", endpointUrl);
            sessions.put(i, s);
            saveArray(PREF_SESSIONS, sessions);
            return;
        }
    }

    private JSONObject endpointForProbe(String endpointId, String endpointUrl) throws Exception {
        JSONObject ep = findEndpoint(endpointId);
        if (ep != null) return ep;
        if (endpointUrl == null || endpointUrl.trim().isEmpty()) return null;
        return endpointForChatUrl(endpointUrl);
    }

    private String providerModelId(JSONObject endpoint, String model) {
        String out = valueOr(model, "").trim();
        if (out.isEmpty()) return out;
        String host = "";
        try {
            host = new URL(normalizeBase(endpoint == null ? "" : endpoint.optString("base_url"))).getHost().toLowerCase(Locale.US);
        } catch (Exception ignored) {
        }
        int at = out.lastIndexOf('@');
        if (at > 0 && !host.isEmpty()) {
            String suffix = out.substring(at + 1).trim().toLowerCase(Locale.US);
            if (suffix.equals(host) || suffix.endsWith("." + host) || host.endsWith("." + suffix)) {
                out = out.substring(0, at).trim();
            }
        }
        return out;
    }

    private boolean isDeepSeekV4(JSONObject endpoint, String model) {
        try {
            String host = new URL(normalizeBase(endpoint == null ? "" : endpoint.optString("base_url"))).getHost().toLowerCase(Locale.US);
            return "api.deepseek.com".equals(host)
                    && valueOr(model, "").toLowerCase(Locale.US).startsWith("deepseek-v4-");
        } catch (Exception ignored) {
            return false;
        }
    }

    private String mobileProviderLabel(JSONObject endpoint, String model) {
        String host = "unknown endpoint";
        try {
            host = providerLabelForBase(endpoint == null ? "" : endpoint.optString("base_url"));
        } catch (Exception ignored) {
        }
        String providerModel = providerModelId(endpoint, model);
        return host + (providerModel.isEmpty() ? "" : " / " + providerModel);
    }

    private String providerLabelForBase(String baseUrl) {
        try {
            URL parsed = new URL(normalizeBase(baseUrl));
            String host = valueOr(parsed.getHost(), "").toLowerCase(Locale.US);
            if (host.equals("api.openai.com")) return "OpenAI";
            if (host.equals("openrouter.ai") || host.endsWith(".openrouter.ai")) return "OpenRouter";
            if (host.equals("api.deepseek.com") || host.endsWith(".deepseek.com")) return "DeepSeek";
            if (host.equals("dashscope.aliyuncs.com") || host.endsWith(".dashscope.aliyuncs.com")) return "DashScope";
            if (host.equals("aliyuncs.com") || host.endsWith(".aliyuncs.com")) return "Alibaba Model Studio";
            if (host.equals("localhost") || host.equals("127.0.0.1") || host.equals("0.0.0.0") || host.equals("::1")) {
                int p = parsed.getPort();
                return p > 0 ? host + ":" + p : "local endpoint";
            }
            return host.isEmpty() ? "unknown endpoint" : host;
        } catch (Exception ignored) {
            return "unknown endpoint";
        }
    }

    private void probeChat(JSONObject endpoint, String model) throws Exception {
        String providerModel = providerModelId(endpoint, model);
        if (isChatGptSubscriptionEndpoint(endpoint)) {
            JSONArray messages = new JSONArray()
                    .put(new JSONObject().put("role", "system").put("content", "You are a helpful assistant."))
                    .put(new JSONObject().put("role", "user").put("content", "Say OK"));
            String reply = callChatGptSubscription(endpoint, providerModel, messages, 0);
            if (reply.trim().isEmpty()) throw new IOException(CHATGPT_SUBSCRIPTION_LABEL + " returned an empty response.");
            return;
        }
        String baseUrl = endpoint.optString("base_url");
        String apiKey = endpoint.optString("api_key");
        URL url = new URL(chatUrl(baseUrl));
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("POST");
        conn.setConnectTimeout(8000);
        conn.setReadTimeout(12000);
        conn.setDoOutput(true);
        conn.setRequestProperty("Content-Type", "application/json");
        conn.setRequestProperty("Accept", "application/json");
        if (!apiKey.isEmpty()) conn.setRequestProperty("Authorization", "Bearer " + apiKey);

        JSONArray messages = new JSONArray()
                .put(new JSONObject().put("role", "system").put("content", "You are a helpful assistant."))
                .put(new JSONObject().put("role", "user").put("content", "Say OK"));
        JSONObject payload = new JSONObject()
                .put("model", providerModel)
                .put("messages", messages)
                .put("stream", false);
        if (isDeepSeekV4(endpoint, providerModel)) {
            payload.put("thinking", new JSONObject().put("type", "disabled"));
        }
        byte[] data = payload.toString().getBytes(StandardCharsets.UTF_8);

        conn.setFixedLengthStreamingMode(data.length);
        try (OutputStream body = conn.getOutputStream()) {
            body.write(data);
        }

        int status = conn.getResponseCode();
        String response = readAll(status >= 400 ? conn.getErrorStream() : conn.getInputStream());
        if (status < 200 || status >= 300) {
            throw new IOException(formatProviderError(status, response) + " | " + mobileRequestSummary(endpoint, providerModel, messages));
        }
    }

    private String callChat(JSONObject endpoint, String model, JSONArray messages) throws Exception {
        return callChat(endpoint, model, messages, 0);
    }

    private String callChat(JSONObject endpoint, String model, JSONArray messages, int maxTokens) throws Exception {
        JSONArray providerMessages = sanitizeProviderMessages(messages);
        String providerModel = providerModelId(endpoint, model);
        if (isChatGptSubscriptionEndpoint(endpoint)) {
            return callChatGptSubscription(endpoint, providerModel, providerMessages, maxTokens);
        }
        String baseUrl = endpoint.optString("base_url");
        String apiKey = endpoint.optString("api_key");
        URL url = new URL(chatUrl(baseUrl));
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("POST");
        conn.setConnectTimeout(15000);
        conn.setReadTimeout(maxTokens > 0 ? 240000 : 120000);
        conn.setDoOutput(true);
        conn.setRequestProperty("Content-Type", "application/json");
        conn.setRequestProperty("Accept", "application/json");
        if (!apiKey.isEmpty()) conn.setRequestProperty("Authorization", "Bearer " + apiKey);
        JSONObject payload = new JSONObject()
                .put("model", providerModel)
                .put("messages", providerMessages)
                .put("stream", false);
        if (isDeepSeekV4(endpoint, providerModel)) {
            payload.put("thinking", new JSONObject().put("type", "disabled"));
        } else {
            payload.put("temperature", 0.7);
        }
        if (maxTokens > 0) payload.put("max_tokens", maxTokens);
        byte[] data = payload.toString().getBytes(StandardCharsets.UTF_8);
        conn.setFixedLengthStreamingMode(data.length);
        try (OutputStream body = conn.getOutputStream()) {
            body.write(data);
        }
        int status = conn.getResponseCode();
        String response = readAll(status >= 400 ? conn.getErrorStream() : conn.getInputStream());
        if (status < 200 || status >= 300) {
            throw new IOException(formatProviderError(status, response) + " | " + mobileRequestSummary(endpoint, providerModel, providerMessages));
        }
        JSONObject json = new JSONObject(response);
        JSONArray choices = json.optJSONArray("choices");
        if (choices == null || choices.length() == 0) return "";
        JSONObject choice = choices.optJSONObject(0);
        if (choice == null) return "";
        JSONObject message = choice.optJSONObject("message");
        if (message != null) return message.optString("content", "");
        return choice.optString("text", "");
    }

    private String mobileRequestSummary(JSONObject endpoint, String model, JSONArray messages) {
        String host = "unknown endpoint";
        try {
            host = new URL(normalizeBase(endpoint == null ? "" : endpoint.optString("base_url"))).getHost();
        } catch (Exception ignored) {
        }
        return "sent host=" + host + ", model=" + valueOr(model, "") + ", messages=" + (messages == null ? 0 : messages.length());
    }

    private JSONArray sanitizeProviderMessages(JSONArray messages) throws Exception {
        JSONArray cleaned = new JSONArray();
        for (int i = 0; messages != null && i < messages.length(); i++) {
            JSONObject msg = messages.optJSONObject(i);
            if (msg == null) continue;
            String role = msg.optString("role", "").trim();
            if (role.isEmpty()) continue;

            JSONObject item = new JSONObject().put("role", role);
            if (msg.has("name") && !msg.isNull("name")) item.put("name", msg.get("name"));
            if (msg.has("content")) item.put("content", msg.isNull("content") ? JSONObject.NULL : msg.get("content"));

            if ("assistant".equals(role)) {
                if (msg.has("tool_calls") && !msg.isNull("tool_calls")) item.put("tool_calls", msg.get("tool_calls"));
                if (msg.has("function_call") && !msg.isNull("function_call")) item.put("function_call", msg.get("function_call"));
                if (item.has("content") || item.has("tool_calls") || item.has("function_call")) {
                    cleaned.put(item);
                }
                continue;
            }

            if ("tool".equals(role)) {
                if (item.has("content") && msg.has("tool_call_id") && !msg.isNull("tool_call_id")) {
                    item.put("tool_call_id", msg.get("tool_call_id"));
                    cleaned.put(item);
                }
                continue;
            }

            if (item.has("content")) cleaned.put(item);
        }
        return cleaned;
    }

    private String callChatGptSubscription(JSONObject endpoint, String model, JSONArray messages, int maxTokens) throws Exception {
        for (int attempt = 0; attempt < 2; attempt++) {
            String accessToken = ensureChatGptAccessToken(endpoint, attempt > 0);
            URL url = new URL(chatUrl(endpoint.optString("base_url", CHATGPT_SUBSCRIPTION_BASE_URL)));
            HttpURLConnection conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod("POST");
            conn.setConnectTimeout(15000);
            conn.setReadTimeout(maxTokens > 0 ? 240000 : 120000);
            conn.setDoOutput(true);
            conn.setRequestProperty("Content-Type", "application/json");
            for (Map.Entry<String, String> header : chatGptSubscriptionHeaders(accessToken).entrySet()) {
                conn.setRequestProperty(header.getKey(), header.getValue());
            }

            JSONObject payload = buildChatGptResponsesPayload(model, messages);
            byte[] data = payload.toString().getBytes(StandardCharsets.UTF_8);
            conn.setFixedLengthStreamingMode(data.length);
            try (OutputStream body = conn.getOutputStream()) {
                body.write(data);
            }
            int status = conn.getResponseCode();
            String response = readAll(status >= 400 ? conn.getErrorStream() : conn.getInputStream());
            if ((status == 401 || status == 403) && attempt == 0 && !endpoint.optString("refresh_token", "").isEmpty()) {
                refreshChatGptEndpointTokens(endpoint);
                continue;
            }
            if (status < 200 || status >= 300) {
                throw new IOException(formatChatGptSubscriptionError(status, response));
            }
            return parseChatGptSubscriptionResponse(response);
        }
        throw new IOException(CHATGPT_SUBSCRIPTION_LABEL + " credentials expired or were rejected. Reconnect the provider.");
    }

    private JSONObject buildChatGptResponsesPayload(String model, JSONArray messages) throws Exception {
        JSONObject payload = new JSONObject()
                .put("model", model)
                .put("instructions", chatGptInstructions(messages))
                .put("input", buildChatGptResponsesInput(messages))
                .put("stream", true)
                .put("store", false);
        if (!modelRestrictsTemperature(model)) {
            payload.put("temperature", 0.7);
        }
        return payload;
    }

    private boolean modelRestrictsTemperature(String model) {
        String m = valueOr(model, "").toLowerCase(Locale.US);
        return m.startsWith("o1") || m.contains("/o1")
                || m.startsWith("o3") || m.contains("/o3")
                || m.startsWith("o4") || m.contains("/o4")
                || m.startsWith("gpt-5") || m.contains("/gpt-5");
    }

    private String chatGptInstructions(JSONArray messages) throws Exception {
        List<String> parts = new ArrayList<>();
        for (int i = 0; messages != null && i < messages.length(); i++) {
            JSONObject msg = messages.optJSONObject(i);
            if (msg == null || !"system".equals(msg.optString("role"))) continue;
            String text = messageContentText(msg.opt("content")).trim();
            if (!text.isEmpty()) parts.add(text);
        }
        return parts.isEmpty() ? "You are a helpful AI assistant." : String.join("\n\n", parts);
    }

    private JSONArray buildChatGptResponsesInput(JSONArray messages) throws Exception {
        JSONArray input = new JSONArray();
        for (int i = 0; messages != null && i < messages.length(); i++) {
            JSONObject msg = messages.optJSONObject(i);
            if (msg == null || "system".equals(msg.optString("role"))) continue;
            String role = msg.optString("role", "user");
            if ("tool".equals(role)) role = "user";
            String type = "assistant".equals(role) ? "output_text" : "input_text";
            input.put(new JSONObject()
                    .put("role", role)
                    .put("content", new JSONArray().put(new JSONObject()
                            .put("type", type)
                            .put("text", messageContentText(msg.opt("content"))))));
        }
        return input;
    }

    private String messageContentText(Object content) {
        if (content == null || content == JSONObject.NULL) return "";
        if (content instanceof JSONArray) {
            JSONArray arr = (JSONArray) content;
            List<String> parts = new ArrayList<>();
            for (int i = 0; i < arr.length(); i++) {
                Object part = arr.opt(i);
                if (part instanceof JSONObject) {
                    JSONObject obj = (JSONObject) part;
                    String text = obj.optString("text", obj.optString("content", ""));
                    if (!text.isEmpty()) parts.add(text);
                } else if (part != null && part != JSONObject.NULL) {
                    String text = String.valueOf(part);
                    if (!text.isEmpty()) parts.add(text);
                }
            }
            return String.join("\n", parts);
        }
        if (content instanceof JSONObject) {
            JSONObject obj = (JSONObject) content;
            return obj.optString("text", obj.optString("content", obj.toString()));
        }
        return String.valueOf(content);
    }

    private String ensureChatGptAccessToken(JSONObject endpoint, boolean forceRefresh) throws Exception {
        String accessToken = endpoint.optString("access_token", endpoint.optString("api_key", "")).trim();
        if (forceRefresh || chatGptAccessTokenExpiring(accessToken)) {
            accessToken = refreshChatGptEndpointTokens(endpoint);
        }
        if (accessToken.isEmpty()) {
            throw new IOException(CHATGPT_SUBSCRIPTION_LABEL + " is missing an access token. Reconnect the provider.");
        }
        return accessToken;
    }

    private String refreshChatGptEndpointTokens(JSONObject endpoint) throws Exception {
        String refreshToken = endpoint.optString("refresh_token", "").trim();
        if (refreshToken.isEmpty()) {
            throw new IOException(CHATGPT_SUBSCRIPTION_LABEL + " is missing a refresh token. Reconnect the provider.");
        }
        Map<String, String> form = new HashMap<>();
        form.put("grant_type", "refresh_token");
        form.put("refresh_token", refreshToken);
        form.put("client_id", CHATGPT_OAUTH_CLIENT_ID);
        JSONObject refreshed = httpPostFormJson(CHATGPT_OAUTH_TOKEN_URL, form);
        String accessToken = refreshed.optString("access_token", "").trim();
        if (accessToken.isEmpty()) {
            throw new IOException("ChatGPT token refresh did not return an access token.");
        }
        endpoint.put("access_token", accessToken);
        endpoint.put("api_key", "");
        if (!refreshed.optString("refresh_token", "").trim().isEmpty()) {
            endpoint.put("refresh_token", refreshed.optString("refresh_token", "").trim());
        }
        endpoint.put("last_refresh", String.valueOf(System.currentTimeMillis()));
        saveEndpointRecord(endpoint);
        return accessToken;
    }

    private boolean chatGptAccessTokenExpiring(String token) {
        try {
            String[] parts = valueOr(token, "").split("\\.");
            if (parts.length < 2) return true;
            String payload = parts[1];
            while (payload.length() % 4 != 0) payload += "=";
            byte[] decoded = Base64.decode(payload, Base64.URL_SAFE | Base64.NO_WRAP);
            JSONObject json = new JSONObject(new String(decoded, StandardCharsets.UTF_8));
            long exp = json.optLong("exp", 0L);
            long now = System.currentTimeMillis() / 1000L;
            return exp <= now + CHATGPT_ACCESS_TOKEN_REFRESH_SKEW_SECONDS;
        } catch (Exception ignored) {
            return true;
        }
    }

    private void saveEndpointRecord(JSONObject updated) throws Exception {
        String id = updated.optString("id", "");
        JSONArray endpoints = loadArray(PREF_ENDPOINTS);
        for (int i = 0; i < endpoints.length(); i++) {
            JSONObject candidate = endpoints.optJSONObject(i);
            if (candidate == null) continue;
            if ((!id.isEmpty() && id.equals(candidate.optString("id")))
                    || (id.isEmpty() && isChatGptSubscriptionEndpoint(candidate))) {
                endpoints.put(i, updated);
                saveArray(PREF_ENDPOINTS, endpoints);
                return;
            }
        }
    }

    private String parseChatGptSubscriptionResponse(String raw) throws Exception {
        String text = valueOr(raw, "").trim();
        if (text.startsWith("{")) {
            JSONObject json = new JSONObject(text);
            return chatGptResponseText(json.optJSONObject("response") == null ? json : json.optJSONObject("response"));
        }
        StringBuilder out = new StringBuilder();
        String eventName = "";
        String[] lines = valueOr(raw, "").split("\\r?\\n");
        for (String line : lines) {
            if (line.startsWith("event:")) {
                eventName = line.substring(6).trim();
                continue;
            }
            if (!line.startsWith("data:")) continue;
            String dataText = line.substring(5).trim();
            if (dataText.isEmpty()) continue;
            if ("[DONE]".equals(dataText)) return out.toString();
            JSONObject data;
            try {
                data = new JSONObject(dataText);
            } catch (Exception ignored) {
                continue;
            }
            String type = data.optString("type", eventName);
            if ("response.output_text.delta".equals(type)) {
                out.append(data.optString("delta", ""));
            } else if ("response.completed".equals(type)) {
                if (out.length() == 0) out.append(chatGptResponseText(data.optJSONObject("response")));
                return out.toString();
            } else if ("response.failed".equals(type) || "error".equals(type)) {
                JSONObject err = data.optJSONObject("error");
                if (err == null) {
                    JSONObject response = data.optJSONObject("response");
                    err = response == null ? null : response.optJSONObject("error");
                }
                String message = err == null ? data.optString("message", CHATGPT_SUBSCRIPTION_LABEL + " request failed") : err.optString("message", CHATGPT_SUBSCRIPTION_LABEL + " request failed");
                throw new IOException(message);
            }
        }
        return out.toString();
    }

    private String chatGptResponseText(JSONObject response) {
        if (response == null) return "";
        String outputText = response.optString("output_text", "");
        if (!outputText.isEmpty()) return outputText;
        StringBuilder out = new StringBuilder();
        JSONArray output = response.optJSONArray("output");
        for (int i = 0; output != null && i < output.length(); i++) {
            JSONObject item = output.optJSONObject(i);
            if (item == null) continue;
            JSONArray content = item.optJSONArray("content");
            for (int j = 0; content != null && j < content.length(); j++) {
                JSONObject part = content.optJSONObject(j);
                if (part == null) continue;
                String text = part.optString("text", "");
                if (!text.isEmpty()) out.append(text);
            }
        }
        return out.toString();
    }

    private String formatChatGptSubscriptionError(int status, String body) {
        if (status == 401 || status == 403) {
            return CHATGPT_SUBSCRIPTION_LABEL + " credentials expired or were rejected. Reconnect the provider.";
        }
        if (status == 429) {
            return CHATGPT_SUBSCRIPTION_LABEL + " quota or rate limit was reached. Retry after the upstream limit resets.";
        }
        return formatProviderError(status, body);
    }

    private JSONArray fetchModels(String baseUrl, String apiKey) throws Exception {
        if (isChatGptSubscriptionBase(baseUrl)) {
            return fetchChatGptSubscriptionModels(apiKey);
        }
        URL url = new URL(modelsUrl(baseUrl));
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("GET");
        conn.setConnectTimeout(12000);
        conn.setReadTimeout(30000);
        conn.setRequestProperty("Accept", "application/json");
        if (!apiKey.isEmpty()) conn.setRequestProperty("Authorization", "Bearer " + apiKey);
        int status = conn.getResponseCode();
        String body = readAll(status >= 400 ? conn.getErrorStream() : conn.getInputStream());
        if (status < 200 || status >= 300) throw new IOException(formatProviderError(status, body));
        JSONObject json = new JSONObject(body);
        JSONArray out = new JSONArray();
        JSONArray data = json.optJSONArray("data");
        if (data != null) {
            for (int i = 0; i < data.length(); i++) {
                JSONObject item = data.optJSONObject(i);
                String id = item == null ? data.optString(i) : item.optString("id", item.optString("name"));
                if (!id.isEmpty() && (isChatModel(id) || isImageEditModel(id))) out.put(id);
            }
        }
        JSONArray models = json.optJSONArray("models");
        if (models != null) {
            for (int i = 0; i < models.length(); i++) {
                JSONObject item = models.optJSONObject(i);
                String id = item == null ? models.optString(i) : item.optString("name", item.optString("model"));
                if (!id.isEmpty() && (isChatModel(id) || isImageEditModel(id))) out.put(id);
            }
        }
        return out;
    }

    private boolean isChatModel(String id) {
        String m = id.toLowerCase(Locale.US);
        return !(m.contains("embed") || m.startsWith("tts-") || m.startsWith("whisper") || m.startsWith("dall-e"));
    }

    private void saveSessionHistory(String sid, JSONArray history) throws Exception {
        JSONArray sessions = loadArray(PREF_SESSIONS);
        long now = System.currentTimeMillis();
        for (int i = 0; i < sessions.length(); i++) {
            JSONObject s = sessions.getJSONObject(i);
            if (!sid.equals(s.optString("id"))) continue;
            s.put("history", history);
            s.put("message_count", history.length());
            s.put("updated_at", String.valueOf(now));
            s.put("last_message_at", String.valueOf(now));
            sessions.put(i, s);
            saveArray(PREF_SESSIONS, sessions);
            return;
        }
    }

    private JSONArray history(JSONObject session) {
        JSONArray history = session.optJSONArray("history");
        return history == null ? new JSONArray() : history;
    }

    private JSONObject firstEnabledEndpoint() throws Exception {
        String preferred = prefs().getString(PREF_DEFAULT_ENDPOINT, "");
        JSONObject match = findEndpoint(preferred);
        if (match != null && match.optBoolean("is_enabled", true)) return match;
        JSONArray endpoints = loadArray(PREF_ENDPOINTS);
        for (int i = 0; i < endpoints.length(); i++) {
            JSONObject ep = endpoints.getJSONObject(i);
            if (ep.optBoolean("is_enabled", true)) return ep;
        }
        return null;
    }

    private JSONObject findEndpoint(String id) throws Exception {
        if (id == null || id.isEmpty()) return null;
        JSONArray endpoints = loadArray(PREF_ENDPOINTS);
        for (int i = 0; i < endpoints.length(); i++) {
            JSONObject ep = endpoints.getJSONObject(i);
            if (id.equals(ep.optString("id"))) return ep;
        }
        return null;
    }

    private String normalizeBase(String raw) {
        String url = valueOr(raw, "").trim();
        if (url.isEmpty()) return "";
        url = url.replace('\\', '/');
        if (!url.startsWith("http://") && !url.startsWith("https://")) url = "https://" + url;
        while (url.endsWith("/")) url = url.substring(0, url.length() - 1);
        if (isChatGptSubscriptionBase(url)) return CHATGPT_SUBSCRIPTION_BASE_URL;
        String[] suffixes = {"/chat/completions", "/completions", "/models"};
        for (String suffix : suffixes) {
            if (url.endsWith(suffix)) return url.substring(0, url.length() - suffix.length());
        }
        String lower = url.toLowerCase(Locale.US);
        if ("https://api.deepseek.com".equals(lower) || "https://api.deepseek.com/v1".equals(lower)) {
            return "https://api.deepseek.com";
        }
        if ("https://api.openai.com".equals(lower)
                || "https://api.x.ai".equals(lower)
                || "https://api.mistral.ai".equals(lower)
                || "https://api.together.xyz".equals(lower)) {
            return url + "/v1";
        }
        return url;
    }

    private String chatUrl(String baseUrl) {
        String base = normalizeBase(baseUrl);
        if (isChatGptSubscriptionBase(base)) return CHATGPT_SUBSCRIPTION_BASE_URL + "/responses";
        if (base.endsWith("/api")) return base + "/chat";
        return base + "/chat/completions";
    }

    private String modelsUrl(String baseUrl) {
        String base = normalizeBase(baseUrl);
        if (isChatGptSubscriptionBase(base)) return CHATGPT_SUBSCRIPTION_BASE_URL + "/models?client_version=1.0.0";
        if (base.endsWith("/api")) return base + "/tags";
        return base + "/models";
    }

    private boolean isChatGptSubscriptionEndpoint(JSONObject endpoint) {
        return endpoint != null
                && (CHATGPT_SUBSCRIPTION_PROVIDER.equals(endpoint.optString("provider"))
                || isChatGptSubscriptionBase(endpoint.optString("base_url")));
    }

    private boolean isChatGptSubscriptionBase(String raw) {
        String url = valueOr(raw, "").trim().replace('\\', '/');
        if (url.isEmpty()) return false;
        if (!url.startsWith("http://") && !url.startsWith("https://")) url = "https://" + url;
        try {
            URL parsed = new URL(url);
            String host = valueOr(parsed.getHost(), "").toLowerCase(Locale.US);
            String path = valueOr(parsed.getPath(), "");
            while (path.endsWith("/") && path.length() > 1) path = path.substring(0, path.length() - 1);
            return "chatgpt.com".equals(host)
                    && ("/backend-api/codex".equals(path) || path.startsWith("/backend-api/codex/"));
        } catch (Exception ignored) {
            return false;
        }
    }

    private String formatProviderError(int status, String body) {
        String detail = "";
        try {
            JSONObject json = new JSONObject(valueOr(body, ""));
            Object error = json.opt("error");
            if (error instanceof JSONObject) detail = ((JSONObject) error).optString("message");
            else if (error != null) detail = String.valueOf(error);
        } catch (Exception ignored) {
            detail = valueOr(body, "");
        }
        detail = detail.replace('\n', ' ').trim();
        if (detail.length() > 260) detail = detail.substring(0, 260) + "...";
        return "HTTP " + status + (detail.isEmpty() ? "" : ": " + detail);
    }

    private JSONObject requestJson(Request request) {
        try {
            String body = new String(request.body, StandardCharsets.UTF_8).trim();
            return body.isEmpty() ? new JSONObject() : new JSONObject(body);
        } catch (Exception ignored) {
            return new JSONObject();
        }
    }

    private String jsonString(JSONObject body, String key, String fallback) {
        if (body == null || !body.has(key) || body.isNull(key)) return fallback;
        return body.optString(key, fallback);
    }

    private String requestedRembgModel(JSONObject body) {
        String explicit = jsonString(body, "_rembg_model", "").trim();
        if (isKnownRembgModel(explicit)) return explicit;
        explicit = jsonString(body, "rembg_model", "").trim();
        if (isKnownRembgModel(explicit)) return explicit;
        explicit = jsonString(body, "_model", "").trim();
        return isKnownRembgModel(explicit) ? explicit : "";
    }

    private String requestedBgRemovePipeline(JSONObject body) {
        String explicit = jsonString(body, "bg_remove_pipeline", "").trim();
        if (explicit.isEmpty()) explicit = jsonString(body, "bgremove_pipeline", "").trim();
        if (explicit.isEmpty()) explicit = jsonString(body, "pipeline", "").trim();
        if (explicit.isEmpty()) explicit = jsonString(body, "_pipeline", "").trim();
        String mode = explicit.toLowerCase(Locale.US).replace('_', '-');
        if (mode.equals("provider") || mode.equals("api") || mode.equals("local")
                || mode.equals("local-model") || mode.equals("local-models")
                || mode.equals("image-model") || mode.equals("image-models")
                || mode.equals("model")) {
            return "model";
        }
        if (mode.equals("natural") || mode.equals("native") || mode.equals("ml")
                || mode.equals("rembg") || mode.equals("rembg-natural")) {
            return "rembg";
        }
        if (mode.equals("heuristic") || mode.equals("sample") || mode.equals("sampled")
                || mode.equals("sampled-background") || mode.equals("color")
                || mode.equals("colour") || mode.equals("color-match")
                || mode.equals("colour-match")) {
            return "heuristic";
        }
        return "auto";
    }

    private boolean isKnownRembgModel(String model) {
        return U2NETP_REMBG_MODEL.equals(model)
                || SILUETA_REMBG_MODEL.equals(model)
                || ISNET_REMBG_MODEL.equals(model);
    }

    private boolean jsonBoolean(JSONObject body, String key, boolean fallback) {
        if (body == null || !body.has(key) || body.isNull(key)) return fallback;
        return body.optBoolean(key, fallback);
    }

    private int jsonInt(JSONObject body, String key, int fallback) {
        if (body == null || !body.has(key) || body.isNull(key)) return fallback;
        return body.optInt(key, fallback);
    }

    private double normalizedStrength(JSONObject body, String key, double fallback) {
        if (body == null || !body.has(key) || body.isNull(key)) return clampStrength(fallback);
        double value = body.optDouble(key, fallback);
        if (Double.isNaN(value) || Double.isInfinite(value)) value = fallback;
        if (value > 1.0) value = value / 100.0;
        return clampStrength(value);
    }

    private Object nullableJsonValue(JSONObject body, String key) throws Exception {
        if (body == null || !body.has(key) || body.isNull(key)) return JSONObject.NULL;
        return body.get(key);
    }

    private void copyNullableNoteField(JSONObject note, JSONObject body, String key) throws Exception {
        if (body == null || !body.has(key)) return;
        note.put(key, body.isNull(key) ? JSONObject.NULL : body.get(key));
    }

    private Object jsonValueOrNull(JSONObject body, String key) {
        if (body == null || !body.has(key) || body.isNull(key)) return JSONObject.NULL;
        return body.opt(key);
    }

    private void serveMobileGeneratedImage(String rawFilename, OutputStream out) throws IOException {
        String filename = valueOr(rawFilename, "");
        try {
            filename = URLDecoder.decode(filename, "UTF-8");
        } catch (Exception ignored) {}
        if (!isStoredGalleryFilenameSafe(filename)) {
            sendPlain(out, 403, "Forbidden");
            return;
        }
        File file = new File(galleryDir(), filename);
        if (!file.isFile()) {
            sendPlain(out, 404, "Not found");
            return;
        }
        try (InputStream in = new FileInputStream(file)) {
            byte[] data = readBytes(in);
            writeHeaders(out, 200, mimeType(filename), data.length);
            out.write(data);
        }
    }

    private File galleryDir() {
        File dir = new File(appContext.getFilesDir(), "mobile_gallery");
        if (!dir.exists()) dir.mkdirs();
        return dir;
    }

    private void deleteGalleryFile(String filename) {
        if (!isStoredGalleryFilenameSafe(filename)) return;
        try {
            File file = new File(galleryDir(), filename);
            if (file.isFile()) file.delete();
        } catch (Exception ignored) {}
    }

    private boolean isStoredGalleryFilenameSafe(String raw) {
        String name = valueOr(raw, "");
        return !name.isEmpty()
                && !name.equals(".")
                && !name.equals("..")
                && name.indexOf('/') < 0
                && name.indexOf('\\') < 0
                && name.equals(safeGalleryBasename(name));
    }

    private String safeGalleryBasename(String raw) {
        String name = valueOr(raw, "upload").trim();
        int slash = Math.max(name.lastIndexOf('/'), name.lastIndexOf('\\'));
        if (slash >= 0) name = name.substring(slash + 1);
        name = name.replaceAll("[^A-Za-z0-9._-]", "_");
        while (name.startsWith(".")) name = name.substring(1);
        if (name.isEmpty()) name = shortId();
        return name.length() > 96 ? name.substring(0, 96) : name;
    }

    private String safeGalleryExtension(String filename, String contentType) {
        String name = safeGalleryBasename(filename);
        String ext = "";
        int dot = name.lastIndexOf('.');
        if (dot >= 0 && dot < name.length() - 1) ext = name.substring(dot + 1).toLowerCase(Locale.US);
        if (ext.isEmpty()) {
            String type = valueOr(contentType, "").toLowerCase(Locale.US);
            if (type.contains("jpeg")) ext = "jpg";
            else if (type.startsWith("image/")) ext = type.substring("image/".length());
            else if (type.startsWith("video/")) ext = type.substring("video/".length());
        }
        if ("quicktime".equals(ext)) ext = "mov";
        return isGalleryMediaExtension(ext) ? ext : "";
    }

    private boolean isGalleryMediaExtension(String ext) {
        return "png".equals(ext) || "jpg".equals(ext) || "jpeg".equals(ext)
                || "webp".equals(ext) || "gif".equals(ext)
                || "mp4".equals(ext) || "mov".equals(ext) || "webm".equals(ext)
                || "mkv".equals(ext) || "m4v".equals(ext);
    }

    private String sha256Hex(byte[] data) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        byte[] hash = digest.digest(data);
        StringBuilder out = new StringBuilder(hash.length * 2);
        for (byte b : hash) out.append(String.format(Locale.US, "%02x", b & 0xff));
        return out.toString();
    }

    private String isoTimestamp(long millis) {
        SimpleDateFormat fmt = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US);
        fmt.setTimeZone(TimeZone.getTimeZone("UTC"));
        return fmt.format(new Date(millis));
    }

    private long galleryTimestamp(JSONObject image) {
        Object rawValue = image == null ? null : image.opt("created_at");
        if (rawValue instanceof Number) return ((Number) rawValue).longValue();
        String raw = String.valueOf(rawValue == null || rawValue == JSONObject.NULL ? "" : rawValue);
        long numeric = parseLong(raw, Long.MIN_VALUE);
        if (numeric != Long.MIN_VALUE) return numeric;
        try {
            SimpleDateFormat fmt = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US);
            fmt.setTimeZone(TimeZone.getTimeZone("UTC"));
            Date parsed = fmt.parse(raw);
            return parsed == null ? 0L : parsed.getTime();
        } catch (Exception ignored) {
            return 0L;
        }
    }

    private void collectGalleryTokens(List<String> out, String raw) {
        for (String token : valueOr(raw, "").split(",")) {
            String t = token.trim();
            if (!t.isEmpty() && !out.contains(t)) out.add(t);
        }
    }

    private int countGalleryTagged(List<JSONObject> images) {
        int count = 0;
        for (JSONObject image : images) {
            if (!valueOr(image.optString("ai_tags", ""), "").trim().isEmpty()) count++;
        }
        return count;
    }

    private String humanSize(long bytes) {
        String[] units = {"B", "KB", "MB", "GB", "TB"};
        double size = Math.max(0, bytes);
        int unit = 0;
        while (size >= 1024 && unit < units.length - 1) {
            size /= 1024.0;
            unit++;
        }
        return String.format(Locale.US, unit == 0 ? "%.0f %s" : "%.1f %s", size, units[unit]);
    }

    private String decodePathPart(String raw) {
        try {
            return URLDecoder.decode(valueOr(raw, ""), "UTF-8");
        } catch (Exception ignored) {
            return valueOr(raw, "");
        }
    }

    private MultipartData parseMultipartData(Request request) {
        MultipartData result = new MultipartData();
        String contentType = valueOr(request.headers.get("content-type"), "");
        String boundary = "";
        for (String part : contentType.split(";")) {
            part = part.trim();
            if (part.startsWith("boundary=")) boundary = part.substring("boundary=".length());
        }
        if (boundary.startsWith("\"") && boundary.endsWith("\"") && boundary.length() > 1) {
            boundary = boundary.substring(1, boundary.length() - 1);
        }
        if (boundary.isEmpty() || request.body == null) return result;

        byte[] marker = ("--" + boundary).getBytes(StandardCharsets.ISO_8859_1);
        byte[] headerEndMarker = "\r\n\r\n".getBytes(StandardCharsets.ISO_8859_1);
        int pos = 0;
        while (true) {
            int boundaryStart = indexOf(request.body, marker, pos);
            if (boundaryStart < 0) break;
            int partStart = boundaryStart + marker.length;
            if (partStart + 1 < request.body.length
                    && request.body[partStart] == '-'
                    && request.body[partStart + 1] == '-') break;
            if (partStart + 1 < request.body.length
                    && request.body[partStart] == '\r'
                    && request.body[partStart + 1] == '\n') partStart += 2;

            int headerEnd = indexOf(request.body, headerEndMarker, partStart);
            if (headerEnd < 0) break;
            String headers = new String(request.body, partStart, headerEnd - partStart, StandardCharsets.ISO_8859_1);
            int dataStart = headerEnd + headerEndMarker.length;
            int nextBoundary = indexOf(request.body, marker, dataStart);
            if (nextBoundary < 0) break;
            int dataEnd = nextBoundary;
            if (dataEnd >= 2 && request.body[dataEnd - 2] == '\r' && request.body[dataEnd - 1] == '\n') dataEnd -= 2;
            byte[] data = Arrays.copyOfRange(request.body, dataStart, Math.max(dataStart, dataEnd));

            String name = multipartHeaderParam(headers, "name");
            String filename = multipartHeaderParam(headers, "filename");
            String partType = multipartContentType(headers);
            if (!name.isEmpty()) {
                if (!filename.isEmpty()) {
                    MultipartFile file = new MultipartFile();
                    file.fieldName = name;
                    file.filename = filename;
                    file.contentType = partType;
                    file.data = data;
                    result.files.put(name, file);
                    if (result.file == null) result.file = file;
                } else {
                    result.fields.put(name, new String(data, StandardCharsets.UTF_8));
                }
            }
            pos = nextBoundary + marker.length;
        }
        return result;
    }

    private int indexOf(byte[] data, byte[] pattern, int start) {
        if (data == null || pattern == null || pattern.length == 0) return -1;
        int max = data.length - pattern.length;
        for (int i = Math.max(0, start); i <= max; i++) {
            boolean match = true;
            for (int j = 0; j < pattern.length; j++) {
                if (data[i + j] != pattern[j]) {
                    match = false;
                    break;
                }
            }
            if (match) return i;
        }
        return -1;
    }

    private String multipartHeaderParam(String headers, String key) {
        for (String line : valueOr(headers, "").split("\\r?\\n")) {
            if (!line.toLowerCase(Locale.US).startsWith("content-disposition:")) continue;
            for (String token : line.split(";")) {
                token = token.trim();
                String prefix = key + "=";
                if (!token.startsWith(prefix)) continue;
                String value = token.substring(prefix.length()).trim();
                if (value.startsWith("\"") && value.endsWith("\"") && value.length() > 1) {
                    value = value.substring(1, value.length() - 1);
                }
                return value;
            }
        }
        return "";
    }

    private String multipartContentType(String headers) {
        for (String line : valueOr(headers, "").split("\\r?\\n")) {
            int idx = line.indexOf(':');
            if (idx <= 0) continue;
            if ("content-type".equalsIgnoreCase(line.substring(0, idx).trim())) {
                return line.substring(idx + 1).trim();
            }
        }
        return "";
    }

    private String truncateError(String raw, int max) {
        String text = valueOr(raw, "").replace('\n', ' ').trim();
        return text.length() > max ? text.substring(0, max) + "..." : text;
    }

    private Map<String, String> parseForm(Request request) throws Exception {
        String contentType = valueOr(request.headers.get("content-type"), "");
        String body = new String(request.body, StandardCharsets.UTF_8);
        if (contentType.startsWith("multipart/form-data")) {
            String boundary = "";
            for (String part : contentType.split(";")) {
                part = part.trim();
                if (part.startsWith("boundary=")) boundary = part.substring("boundary=".length());
            }
            return parseMultipart(body, boundary);
        }
        if (contentType.startsWith("application/x-www-form-urlencoded")) {
            return parseQuery(body);
        }
        if (contentType.startsWith("application/json") && !body.trim().isEmpty()) {
            JSONObject json = new JSONObject(body);
            Map<String, String> out = new HashMap<>();
            JSONArray names = json.names();
            if (names != null) {
                for (int i = 0; i < names.length(); i++) {
                    String key = names.getString(i);
                    out.put(key, json.optString(key));
                }
            }
            return out;
        }
        return new HashMap<>();
    }

    private Map<String, String> parseMultipart(String body, String boundary) {
        Map<String, String> out = new HashMap<>();
        if (boundary == null || boundary.isEmpty()) return out;
        String marker = "--" + boundary;
        String[] parts = body.split(java.util.regex.Pattern.quote(marker));
        for (String part : parts) {
            int headerEnd = part.indexOf("\r\n\r\n");
            if (headerEnd < 0) continue;
            String headers = part.substring(0, headerEnd);
            String value = part.substring(headerEnd + 4);
            while (value.endsWith("\r\n")) value = value.substring(0, value.length() - 2);
            if (value.endsWith("--")) value = value.substring(0, value.length() - 2);
            String name = "";
            for (String line : headers.split("\\r?\\n")) {
                String lower = line.toLowerCase(Locale.US);
                if (!lower.startsWith("content-disposition:")) continue;
                for (String token : line.split(";")) {
                    token = token.trim();
                    if (token.startsWith("name=")) {
                        name = token.substring(5).replace("\"", "");
                    }
                }
            }
            if (!name.isEmpty()) out.put(name, value);
        }
        return out;
    }

    private Map<String, String> parseQuery(String query) {
        Map<String, String> out = new HashMap<>();
        if (query == null || query.isEmpty()) return out;
        for (String pair : query.split("&")) {
            int idx = pair.indexOf('=');
            String key = idx >= 0 ? pair.substring(0, idx) : pair;
            String value = idx >= 0 ? pair.substring(idx + 1) : "";
            try {
                out.put(URLDecoder.decode(key, "UTF-8"), URLDecoder.decode(value, "UTF-8"));
            } catch (Exception ignored) {
                out.put(key, value);
            }
        }
        return out;
    }

    private void serveAsset(String assetPath, OutputStream out) throws IOException {
        if (assetPath.isEmpty()) assetPath = "index.html";
        if (assetPath.contains("..")) {
            sendPlain(out, 403, "Forbidden");
            return;
        }
        AssetManager assets = appContext.getAssets();
        try (InputStream in = assets.open(assetPath)) {
            byte[] data = readBytes(in);
            if ("index.html".equals(assetPath)) {
                String html = new String(data, StandardCharsets.UTF_8)
                        .replace("{{CSP_NONCE}}", "mobile");
                data = html.getBytes(StandardCharsets.UTF_8);
            }
            writeHeaders(out, 200, mimeType(assetPath), data.length);
            out.write(data);
        } catch (IOException ex) {
            sendPlain(out, 404, "Not found");
        }
    }

    private byte[] readBytes(InputStream in) throws IOException {
        ByteArrayOutputStream buffer = new ByteArrayOutputStream();
        byte[] chunk = new byte[8192];
        int n;
        while ((n = in.read(chunk)) != -1) buffer.write(chunk, 0, n);
        return buffer.toByteArray();
    }

    private String mimeType(String path) {
        if (path.endsWith(".js")) return "application/javascript; charset=utf-8";
        if (path.endsWith(".css")) return "text/css; charset=utf-8";
        if (path.endsWith(".html")) return "text/html; charset=utf-8";
        if (path.endsWith(".json")) return "application/json; charset=utf-8";
        if (path.endsWith(".svg")) return "image/svg+xml";
        String ext = "";
        int dot = path.lastIndexOf('.');
        if (dot >= 0) ext = path.substring(dot + 1);
        String type = MimeTypeMap.getSingleton().getMimeTypeFromExtension(ext);
        return type == null ? "application/octet-stream" : type;
    }

    private void sendRedirect(OutputStream out, String location) throws IOException {
        String headers = "HTTP/1.1 302 Found\r\n" +
                "Location: " + location + "\r\n" +
                "Content-Length: 0\r\n" +
                "Connection: close\r\n\r\n";
        out.write(headers.getBytes(StandardCharsets.UTF_8));
    }

    private void sendJson(OutputStream out, int status, Object json) throws IOException {
        byte[] data = String.valueOf(json).getBytes(StandardCharsets.UTF_8);
        writeHeaders(out, status, "application/json; charset=utf-8", data.length);
        out.write(data);
    }

    private void sendPlain(OutputStream out, int status, String text) throws IOException {
        byte[] data = text.getBytes(StandardCharsets.UTF_8);
        writeHeaders(out, status, "text/plain; charset=utf-8", data.length);
        out.write(data);
    }

    private void writeHeaders(OutputStream out, int status, String contentType, int contentLength) throws IOException {
        String reason = status == 200 ? "OK" : status == 302 ? "Found" : status == 403 ? "Forbidden" : status == 404 ? "Not Found" : "Error";
        StringBuilder headers = new StringBuilder();
        headers.append("HTTP/1.1 ").append(status).append(' ').append(reason).append("\r\n");
        headers.append("Content-Type: ").append(contentType).append("\r\n");
        headers.append("Access-Control-Allow-Origin: *\r\n");
        headers.append("Cache-Control: no-store\r\n");
        if (contentLength >= 0) headers.append("Content-Length: ").append(contentLength).append("\r\n");
        headers.append("Connection: close\r\n\r\n");
        out.write(headers.toString().getBytes(StandardCharsets.UTF_8));
    }

    private void writeSse(OutputStream out, JSONObject event) throws IOException {
        out.write(("data: " + event + "\n\n").getBytes(StandardCharsets.UTF_8));
        out.flush();
    }

    private String readAll(InputStream stream) throws IOException {
        if (stream == null) return "";
        return new String(readBytes(stream), StandardCharsets.UTF_8);
    }

    private JSONArray loadArray(String key) {
        try {
            return new JSONArray(prefs().getString(key, "[]"));
        } catch (Exception ignored) {
            return new JSONArray();
        }
    }

    private void saveArray(String key, JSONArray array) {
        prefs().edit().putString(key, array.toString()).apply();
    }

    private SharedPreferences prefs() {
        return appContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
    }

    private int parseInt(String raw, int fallback) {
        try {
            return Integer.parseInt(valueOr(raw, ""));
        } catch (Exception ignored) {
            return fallback;
        }
    }

    private double parseDouble(String raw, double fallback) {
        try {
            return Double.parseDouble(valueOr(raw, "").replace(',', '.'));
        } catch (Exception ignored) {
            return fallback;
        }
    }

    private double round1(double value) {
        return Math.round(value * 10.0) / 10.0;
    }

    private double sortMetric(JSONObject row, String sort) {
        if ("vram".equals(sort)) return row.optDouble("required_gb", 0.0);
        if ("speed".equals(sort)) return row.optDouble("speed_tps", 0.0);
        if ("params".equals(sort)) return row.optDouble("params_b", 0.0);
        if ("context".equals(sort)) return row.optDouble("context", 0.0);
        if ("fit".equals(sort)) {
            String fit = row.optString("fit_level", "");
            if ("good".equals(fit)) return 3.0;
            if ("marginal".equals(fit)) return 2.0;
            if ("too_tight".equals(fit)) return 1.0;
            return 0.0;
        }
        return row.optDouble("score", 0.0);
    }

    private long parseLong(String raw, long fallback) {
        try {
            return Long.parseLong(valueOr(raw, ""));
        } catch (Exception ignored) {
            return fallback;
        }
    }

    private String valueOr(String value, String fallback) {
        return value == null ? fallback : value;
    }

    private Object nullableString(String value) {
        String v = valueOr(value, "").trim();
        return v.isEmpty() ? JSONObject.NULL : v;
    }

    private String shortId() {
        return UUID.randomUUID().toString().replace("-", "").substring(0, 8);
    }

    private String hostLabel(String url) {
        try {
            URL parsed = new URL(normalizeBase(url));
            String host = parsed.getHost();
            int p = parsed.getPort();
            return p > 0 ? host + ":" + p : host;
        } catch (Exception ignored) {
            return url == null || url.isEmpty() ? "Endpoint" : url;
        }
    }

    private static class MultipartData {
        Map<String, String> fields = new HashMap<>();
        Map<String, MultipartFile> files = new HashMap<>();
        MultipartFile file;
    }

    private static class MultipartFile {
        String fieldName;
        String filename;
        String contentType;
        byte[] data;
    }

    private static class MobileHttpException extends Exception {
        final int status;

        MobileHttpException(int status, String detail) {
            super(detail);
            this.status = status;
        }
    }

    private static class Request {
        String method;
        String rawPath;
        String path;
        Map<String, String> query = new HashMap<>();
        Map<String, String> headers = new HashMap<>();
        byte[] body = new byte[0];
        boolean bodyTooLarge = false;
    }
}
