package com.odysseus.simplesignal;

import android.content.Context;
import android.content.SharedPreferences;
import android.content.res.AssetManager;
import android.webkit.MimeTypeMap;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
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
import java.util.HashMap;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;

public class MobileBackendServer {
    private static final String PREFS_NAME = "odysseus_mobile_backend";
    private static final String PREF_ENDPOINTS = "endpoints";
    private static final String PREF_SESSIONS = "sessions";
    private static final String PREF_DEFAULT_ENDPOINT = "default_endpoint_id";
    private static final int FIRST_PORT = 7019;
    private static final int LAST_PORT = 7039;

    private static MobileBackendServer instance;

    private Context appContext;
    private ServerSocket serverSocket;
    private Thread serverThread;
    private int port;

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
            sendJson(out, 200, new JSONObject()
                    .put("auth_enabled", false)
                    .put("mobile_standalone", true));
            return;
        }
        if ("GET".equals(request.method) && "/api/sessions".equals(path)) {
            sendJson(out, 200, listSessionSummaries());
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
        if ("GET".equals(request.method) && "/api/model-endpoints/probe-local".equals(path)) {
            sendJson(out, 200, new JSONObject());
            return;
        }
        if (path.startsWith("/api/model-endpoints/")) {
            routeEndpoint(request, out, path.substring("/api/model-endpoints/".length()));
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
            sendJson(out, 200, new JSONObject().put("servers", new JSONArray()));
            return;
        }
        if ("GET".equals(request.method) && "/api/personal".equals(path)) {
            sendJson(out, 200, new JSONObject().put("directories", new JSONArray()).put("files", new JSONArray()));
            return;
        }
        if ("GET".equals(request.method) && path.startsWith("/api/calendar/")) {
            sendJson(out, 200, new JSONObject().put("events", new JSONArray()).put("calendars", new JSONArray()));
            return;
        }
        sendJson(out, 404, new JSONObject().put("detail", "Mobile standalone route not implemented"));
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

    private void routeEndpoint(Request request, OutputStream out, String tail) throws Exception {
        String[] parts = tail.split("/");
        String id = parts[0];
        if ("DELETE".equals(request.method)) {
            deleteEndpoint(id);
            sendJson(out, 200, new JSONObject().put("deleted", true));
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

    private JSONArray modelsList() throws Exception {
        JSONArray endpoints = loadArray(PREF_ENDPOINTS);
        JSONArray out = new JSONArray();
        for (int i = 0; i < endpoints.length(); i++) {
            JSONObject ep = endpoints.getJSONObject(i);
            if (!ep.optBoolean("is_enabled", true)) continue;
            JSONArray models = ep.optJSONArray("models");
            out.put(new JSONObject()
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
        return out;
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
                .put("has_api_key", !ep.optString("api_key").isEmpty())
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

    private void streamChat(Request request, OutputStream out) throws Exception {
        Map<String, String> form = parseForm(request);
        String sid = form.get("session");
        String userText = valueOr(form.get("message"), "");
        JSONObject session = getSessionById(sid);
        JSONObject endpoint = endpointForSession(session);
        String model = session.optString("model");
        JSONArray history = history(session);
        if (!userText.isEmpty()) {
            history.put(new JSONObject().put("role", "user").put("content", userText));
        }

        writeHeaders(out, 200, "text/event-stream; charset=utf-8", -1);
        writeSse(out, new JSONObject().put("type", "model_info").put("model", model));
        String reply;
        try {
            reply = callChat(endpoint, model, history);
            if (reply.trim().isEmpty()) reply = "The model returned an empty response.";
        } catch (Exception ex) {
            reply = "Mobile backend request failed: " + ex.getMessage();
        }
        history.put(new JSONObject().put("role", "assistant").put("content", reply).put("metadata", new JSONObject().put("model", model)));
        saveSessionHistory(sid, history);
        writeSse(out, new JSONObject().put("delta", reply));
        writeSse(out, new JSONObject().put("type", "metrics").put("data", new JSONObject().put("total_time", 0).put("model", model)));
        out.write("data: [DONE]\n\n".getBytes(StandardCharsets.UTF_8));
        out.flush();
    }

    private JSONObject endpointForSession(JSONObject session) throws Exception {
        String endpointId = session.optString("endpoint_id");
        JSONObject ep = findEndpoint(endpointId);
        if (ep != null) return ep;
        String endpointUrl = session.optString("endpoint_url");
        JSONArray endpoints = loadArray(PREF_ENDPOINTS);
        for (int i = 0; i < endpoints.length(); i++) {
            JSONObject cand = endpoints.getJSONObject(i);
            if (chatUrl(cand.optString("base_url")).equals(endpointUrl)) return cand;
        }
        throw new IOException("No endpoint configured for this session");
    }

    private String callChat(JSONObject endpoint, String model, JSONArray messages) throws Exception {
        String baseUrl = endpoint.optString("base_url");
        String apiKey = endpoint.optString("api_key");
        URL url = new URL(chatUrl(baseUrl));
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("POST");
        conn.setConnectTimeout(15000);
        conn.setReadTimeout(120000);
        conn.setDoOutput(true);
        conn.setRequestProperty("Content-Type", "application/json");
        conn.setRequestProperty("Accept", "application/json");
        if (!apiKey.isEmpty()) conn.setRequestProperty("Authorization", "Bearer " + apiKey);
        JSONObject payload = new JSONObject()
                .put("model", model)
                .put("messages", messages)
                .put("stream", false)
                .put("temperature", 0.7);
        byte[] data = payload.toString().getBytes(StandardCharsets.UTF_8);
        conn.setFixedLengthStreamingMode(data.length);
        try (OutputStream body = conn.getOutputStream()) {
            body.write(data);
        }
        int status = conn.getResponseCode();
        String response = readAll(status >= 400 ? conn.getErrorStream() : conn.getInputStream());
        if (status < 200 || status >= 300) {
            throw new IOException(formatProviderError(status, response));
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

    private JSONArray fetchModels(String baseUrl, String apiKey) throws Exception {
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
                if (!id.isEmpty() && isChatModel(id)) out.put(id);
            }
        }
        JSONArray models = json.optJSONArray("models");
        if (models != null) {
            for (int i = 0; i < models.length(); i++) {
                JSONObject item = models.optJSONObject(i);
                String id = item == null ? models.optString(i) : item.optString("name", item.optString("model"));
                if (!id.isEmpty() && isChatModel(id)) out.put(id);
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
        if (!url.startsWith("http://") && !url.startsWith("https://")) url = "https://" + url;
        while (url.endsWith("/")) url = url.substring(0, url.length() - 1);
        String[] suffixes = {"/chat/completions", "/completions", "/models"};
        for (String suffix : suffixes) {
            if (url.endsWith(suffix)) return url.substring(0, url.length() - suffix.length());
        }
        return url;
    }

    private String chatUrl(String baseUrl) {
        String base = normalizeBase(baseUrl);
        if (base.endsWith("/api")) return base + "/chat";
        return base + "/chat/completions";
    }

    private String modelsUrl(String baseUrl) {
        String base = normalizeBase(baseUrl);
        if (base.endsWith("/api")) return base + "/tags";
        return base + "/models";
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

    private String valueOr(String value, String fallback) {
        return value == null ? fallback : value;
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

    private static class Request {
        String method;
        String rawPath;
        String path;
        Map<String, String> query = new HashMap<>();
        Map<String, String> headers = new HashMap<>();
        byte[] body = new byte[0];
    }
}
