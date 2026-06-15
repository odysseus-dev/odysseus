package com.odysseus.simplesignal;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.inputmethod.EditorInfo;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

public class StandaloneChatActivity extends Activity {
    private static final String PREFS_NAME = "odysseus_android";
    private static final String PREF_MODE = "app_mode";
    private static final String MODE_REMOTE = "remote";
    private static final String PREF_ENDPOINT = "standalone_endpoint";
    private static final String PREF_MODEL = "standalone_model";
    private static final String PREF_API_KEY = "standalone_api_key";
    private static final String PREF_MESSAGES = "standalone_messages";

    private final List<MessageItem> messages = new ArrayList<>();
    private LinearLayout messageList;
    private ScrollView scrollView;
    private EditText composer;
    private TextView statusText;
    private ProgressBar progressBar;
    private Button sendButton;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        configureSystemBars();
        loadMessages();
        buildLayout();
        renderMessages();
        if (getEndpoint().isEmpty() || getModel().isEmpty()) {
            showSettingsDialog();
        }
    }

    private void configureSystemBars() {
        getWindow().setStatusBarColor(Color.rgb(5, 8, 5));
        getWindow().setNavigationBarColor(Color.rgb(5, 8, 5));
    }

    private void buildLayout() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.rgb(5, 8, 5));

        root.addView(buildTopBar(), new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        ));

        FrameLayout body = new FrameLayout(this);
        scrollView = new ScrollView(this);
        scrollView.setFillViewport(false);
        messageList = new LinearLayout(this);
        messageList.setOrientation(LinearLayout.VERTICAL);
        messageList.setPadding(dp(12), dp(12), dp(12), dp(18));
        scrollView.addView(messageList, new ScrollView.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        ));
        body.addView(scrollView, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
        ));

        progressBar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progressBar.setVisibility(View.GONE);
        body.addView(progressBar, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                dp(3),
                Gravity.TOP
        ));

        root.addView(body, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                0,
                1
        ));

        root.addView(buildComposer(), new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        ));

        setContentView(root);
    }

    private LinearLayout buildTopBar() {
        LinearLayout top = new LinearLayout(this);
        top.setOrientation(LinearLayout.VERTICAL);
        top.setPadding(dp(12), dp(10), dp(12), dp(8));
        top.setBackgroundColor(Color.rgb(12, 16, 12));

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);

        TextView title = new TextView(this);
        title.setText("Odysseus");
        title.setTextColor(Color.rgb(34, 255, 34));
        title.setTypeface(Typeface.MONOSPACE, Typeface.BOLD);
        title.setTextSize(22);
        row.addView(title, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));

        Button pcButton = new Button(this);
        pcButton.setText("PC");
        row.addView(pcButton, new LinearLayout.LayoutParams(dp(72), ViewGroup.LayoutParams.WRAP_CONTENT));

        Button settingsButton = new Button(this);
        settingsButton.setText("Settings");
        LinearLayout.LayoutParams settingsParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        );
        settingsParams.setMargins(dp(8), 0, 0, 0);
        row.addView(settingsButton, settingsParams);

        top.addView(row, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        ));

        statusText = new TextView(this);
        statusText.setText(getStatusLabel());
        statusText.setTextColor(Color.rgb(140, 160, 140));
        statusText.setTextSize(12);
        statusText.setSingleLine(false);
        top.addView(statusText, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        ));

        pcButton.setOnClickListener(v -> switchToPcMode());
        settingsButton.setOnClickListener(v -> showSettingsDialog());
        return top;
    }

    private LinearLayout buildComposer() {
        LinearLayout wrapper = new LinearLayout(this);
        wrapper.setOrientation(LinearLayout.VERTICAL);
        wrapper.setPadding(dp(12), dp(8), dp(12), dp(12));
        wrapper.setBackgroundColor(Color.rgb(12, 16, 12));

        LinearLayout actions = new LinearLayout(this);
        actions.setOrientation(LinearLayout.HORIZONTAL);
        actions.setGravity(Gravity.CENTER_VERTICAL);

        Button clearButton = new Button(this);
        clearButton.setText("Clear");
        actions.addView(clearButton, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        ));

        TextView mode = new TextView(this);
        mode.setText("Standalone Mobile");
        mode.setTextColor(Color.rgb(130, 150, 130));
        mode.setGravity(Gravity.RIGHT | Gravity.CENTER_VERTICAL);
        actions.addView(mode, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));

        wrapper.addView(actions, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        ));

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.BOTTOM);

        composer = new EditText(this);
        composer.setMinLines(1);
        composer.setMaxLines(5);
        composer.setHint("Message Odysseus...");
        composer.setHintTextColor(Color.rgb(105, 125, 105));
        composer.setTextColor(Color.rgb(235, 244, 235));
        composer.setSingleLine(false);
        composer.setImeOptions(EditorInfo.IME_ACTION_SEND);
        composer.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE | InputType.TYPE_TEXT_FLAG_CAP_SENTENCES);
        row.addView(composer, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));

        sendButton = new Button(this);
        sendButton.setText("Send");
        LinearLayout.LayoutParams sendParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        );
        sendParams.setMargins(dp(8), 0, 0, 0);
        row.addView(sendButton, sendParams);

        wrapper.addView(row, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        ));

        sendButton.setOnClickListener(v -> sendMessage());
        composer.setOnEditorActionListener((v, actionId, event) -> {
            if (actionId == EditorInfo.IME_ACTION_SEND && !eventHasShift(event)) {
                sendMessage();
                return true;
            }
            return false;
        });
        clearButton.setOnClickListener(v -> confirmClearChat());
        return wrapper;
    }

    private boolean eventHasShift(android.view.KeyEvent event) {
        return event != null && event.isShiftPressed();
    }

    private void renderMessages() {
        messageList.removeAllViews();
        if (messages.isEmpty()) {
            TextView empty = new TextView(this);
            empty.setText("Standalone mode is ready. Add an endpoint in Settings, then start a chat.");
            empty.setTextColor(Color.rgb(130, 150, 130));
            empty.setTextSize(15);
            empty.setGravity(Gravity.CENTER);
            empty.setPadding(dp(18), dp(80), dp(18), dp(18));
            messageList.addView(empty, new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT
            ));
            return;
        }
        for (MessageItem message : messages) {
            addMessageBubble(message);
        }
        scrollToBottom();
    }

    private void addMessageBubble(MessageItem message) {
        boolean isUser = "user".equals(message.role);

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.VERTICAL);
        row.setGravity(isUser ? Gravity.RIGHT : Gravity.LEFT);
        LinearLayout.LayoutParams rowParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        );
        rowParams.setMargins(0, dp(6), 0, dp(6));

        LinearLayout bubble = new LinearLayout(this);
        bubble.setOrientation(LinearLayout.VERTICAL);
        bubble.setPadding(dp(12), dp(10), dp(12), dp(10));
        bubble.setBackground(makeBubbleBackground(isUser));

        TextView label = new TextView(this);
        label.setText(isUser ? "You" : getModel());
        label.setTextColor(isUser ? Color.rgb(165, 210, 235) : Color.rgb(115, 230, 160));
        label.setTypeface(Typeface.MONOSPACE, Typeface.BOLD);
        label.setTextSize(13);
        bubble.addView(label, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        ));

        TextView body = new TextView(this);
        body.setText(message.content);
        body.setTextColor(Color.rgb(225, 232, 225));
        body.setTextSize(15);
        body.setTextIsSelectable(true);
        body.setMaxWidth(Math.max(dp(220), getResources().getDisplayMetrics().widthPixels - dp(64)));
        LinearLayout.LayoutParams bodyParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        );
        bodyParams.setMargins(0, dp(5), 0, 0);
        bubble.addView(body, bodyParams);

        row.addView(bubble, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        ));
        messageList.addView(row, rowParams);
    }

    private GradientDrawable makeBubbleBackground(boolean isUser) {
        GradientDrawable drawable = new GradientDrawable();
        drawable.setColor(isUser ? Color.rgb(12, 20, 24) : Color.rgb(18, 20, 18));
        drawable.setStroke(dp(1), isUser ? Color.rgb(54, 86, 102) : Color.rgb(45, 92, 70));
        drawable.setCornerRadius(dp(14));
        return drawable;
    }

    private void sendMessage() {
        String text = composer.getText().toString().trim();
        if (text.isEmpty()) return;

        String endpoint = getEndpoint();
        String model = getModel();
        if (endpoint.isEmpty() || model.isEmpty()) {
            Toast.makeText(this, "Add an endpoint and model first", Toast.LENGTH_SHORT).show();
            showSettingsDialog();
            return;
        }

        messages.add(new MessageItem("user", text));
        saveMessages();
        renderMessages();
        composer.setText("");
        setBusy(true, "Calling " + model + "...");

        new Thread(() -> {
            String reply;
            try {
                reply = callChatCompletions(endpoint, model, getApiKey(), buildRequestMessages());
                if (reply.trim().isEmpty()) {
                    reply = "The model returned an empty response. Try another model or endpoint.";
                }
            } catch (Exception ex) {
                reply = "Request failed: " + ex.getMessage();
            }
            String finalReply = reply;
            runOnUiThread(() -> {
                messages.add(new MessageItem("assistant", finalReply));
                saveMessages();
                renderMessages();
                setBusy(false, getStatusLabel());
            });
        }).start();
    }

    private JSONArray buildRequestMessages() throws JSONException {
        JSONArray array = new JSONArray();
        int start = Math.max(0, messages.size() - 24);
        for (int i = start; i < messages.size(); i++) {
            MessageItem item = messages.get(i);
            JSONObject msg = new JSONObject();
            msg.put("role", item.role);
            msg.put("content", item.content);
            array.put(msg);
        }
        return array;
    }

    private String callChatCompletions(String endpoint, String model, String apiKey, JSONArray chatMessages) throws Exception {
        String base = normalizeEndpoint(endpoint);
        URL url = new URL(base + "/chat/completions");
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("POST");
        conn.setConnectTimeout(15000);
        conn.setReadTimeout(120000);
        conn.setDoOutput(true);
        conn.setRequestProperty("Content-Type", "application/json");
        conn.setRequestProperty("Accept", "application/json");
        if (!apiKey.trim().isEmpty()) {
            conn.setRequestProperty("Authorization", "Bearer " + apiKey.trim());
        }

        JSONObject payload = new JSONObject();
        payload.put("model", model);
        payload.put("messages", chatMessages);
        payload.put("stream", false);
        payload.put("temperature", 0.7);

        byte[] bytes = payload.toString().getBytes(StandardCharsets.UTF_8);
        conn.setFixedLengthStreamingMode(bytes.length);
        try (OutputStream out = conn.getOutputStream()) {
            out.write(bytes);
        }

        int status = conn.getResponseCode();
        String body = readBody(status >= 400 ? conn.getErrorStream() : conn.getInputStream());
        if (status < 200 || status >= 300) {
            throw new Exception(formatError(status, body));
        }
        return parseAssistantText(body);
    }

    private String parseAssistantText(String body) throws JSONException {
        JSONObject json = new JSONObject(body);
        JSONArray choices = json.optJSONArray("choices");
        if (choices == null || choices.length() == 0) return "";
        JSONObject choice = choices.optJSONObject(0);
        if (choice == null) return "";
        JSONObject message = choice.optJSONObject("message");
        if (message != null) {
            String content = message.optString("content", "");
            if (!content.isEmpty()) return content;
        }
        return choice.optString("text", "");
    }

    private String formatError(int status, String body) {
        String detail = "";
        try {
            JSONObject json = new JSONObject(body);
            Object error = json.opt("error");
            if (error instanceof JSONObject) {
                detail = ((JSONObject) error).optString("message", "");
            } else if (error instanceof String) {
                detail = (String) error;
            }
        } catch (Exception ignored) {
            detail = body == null ? "" : body;
        }
        if (detail == null || detail.trim().isEmpty()) {
            detail = "HTTP " + status;
        }
        if (detail.length() > 500) {
            detail = detail.substring(0, 500) + "...";
        }
        return "HTTP " + status + ": " + detail;
    }

    private String readBody(InputStream stream) throws Exception {
        if (stream == null) return "";
        StringBuilder builder = new StringBuilder();
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(stream, StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) {
                builder.append(line).append('\n');
            }
        }
        return builder.toString();
    }

    private String normalizeEndpoint(String raw) {
        String url = raw == null ? "" : raw.trim();
        if (url.isEmpty()) return "";
        if (!url.startsWith("http://") && !url.startsWith("https://")) {
            url = "https://" + url;
        }
        while (url.endsWith("/")) {
            url = url.substring(0, url.length() - 1);
        }
        String[] suffixes = {
                "/chat/completions",
                "/completions",
                "/models"
        };
        for (String suffix : suffixes) {
            if (url.endsWith(suffix)) {
                url = url.substring(0, url.length() - suffix.length());
                break;
            }
        }
        return url;
    }

    private void showSettingsDialog() {
        LinearLayout form = new LinearLayout(this);
        form.setOrientation(LinearLayout.VERTICAL);
        int pad = dp(16);
        form.setPadding(pad, dp(8), pad, 0);

        EditText endpointInput = new EditText(this);
        endpointInput.setHint("https://api.deepseek.com/v1");
        endpointInput.setSingleLine(true);
        endpointInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        endpointInput.setText(getEndpoint());
        form.addView(labeledField("Endpoint base URL", endpointInput));

        EditText modelInput = new EditText(this);
        modelInput.setHint("deepseek-chat");
        modelInput.setSingleLine(true);
        modelInput.setInputType(InputType.TYPE_CLASS_TEXT);
        modelInput.setText(getModel());
        form.addView(labeledField("Model", modelInput));

        EditText apiKeyInput = new EditText(this);
        apiKeyInput.setHint("Optional API key or token");
        apiKeyInput.setSingleLine(true);
        apiKeyInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        apiKeyInput.setText(getApiKey());
        form.addView(labeledField("API key / token", apiKeyInput));

        new AlertDialog.Builder(this)
                .setTitle("Standalone Endpoint")
                .setView(form)
                .setPositiveButton("Save", (dialog, which) -> {
                    saveStandaloneSettings(
                            normalizeEndpoint(endpointInput.getText().toString()),
                            modelInput.getText().toString().trim(),
                            apiKeyInput.getText().toString().trim()
                    );
                    statusText.setText(getStatusLabel());
                })
                .setNegativeButton("Cancel", null)
                .show();
    }

    private LinearLayout labeledField(String label, EditText input) {
        LinearLayout wrapper = new LinearLayout(this);
        wrapper.setOrientation(LinearLayout.VERTICAL);
        TextView text = new TextView(this);
        text.setText(label);
        text.setTextColor(Color.rgb(220, 225, 220));
        text.setTextSize(13);
        wrapper.addView(text, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        ));
        wrapper.addView(input, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        ));
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        );
        params.setMargins(0, 0, 0, dp(10));
        wrapper.setLayoutParams(params);
        return wrapper;
    }

    private void confirmClearChat() {
        new AlertDialog.Builder(this)
                .setTitle("Clear chat?")
                .setMessage("This clears only the standalone chat stored on this phone.")
                .setPositiveButton("Clear", (dialog, which) -> {
                    messages.clear();
                    saveMessages();
                    renderMessages();
                })
                .setNegativeButton("Cancel", null)
                .show();
    }

    private void switchToPcMode() {
        getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                .edit()
                .putString(PREF_MODE, MODE_REMOTE)
                .apply();
        Intent intent = new Intent(this, MainActivity.class);
        intent.addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_NEW_TASK);
        startActivity(intent);
        finish();
    }

    private void setBusy(boolean busy, String status) {
        progressBar.setVisibility(busy ? View.VISIBLE : View.GONE);
        sendButton.setEnabled(!busy);
        composer.setEnabled(!busy);
        statusText.setText(status);
    }

    private String getStatusLabel() {
        String endpoint = getEndpoint();
        String model = getModel();
        if (endpoint.isEmpty() || model.isEmpty()) {
            return "Standalone endpoint not configured.";
        }
        return model + " at " + endpoint;
    }

    private String getEndpoint() {
        return getPrefs().getString(PREF_ENDPOINT, "");
    }

    private String getModel() {
        return getPrefs().getString(PREF_MODEL, "");
    }

    private String getApiKey() {
        return getPrefs().getString(PREF_API_KEY, "");
    }

    private SharedPreferences getPrefs() {
        return getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
    }

    private void saveStandaloneSettings(String endpoint, String model, String apiKey) {
        getPrefs().edit()
                .putString(PREF_ENDPOINT, endpoint)
                .putString(PREF_MODEL, model)
                .putString(PREF_API_KEY, apiKey)
                .apply();
    }

    private void loadMessages() {
        messages.clear();
        String raw = getPrefs().getString(PREF_MESSAGES, "[]");
        try {
            JSONArray array = new JSONArray(raw);
            for (int i = 0; i < array.length(); i++) {
                JSONObject obj = array.optJSONObject(i);
                if (obj == null) continue;
                String role = obj.optString("role", "");
                String content = obj.optString("content", "");
                if (!role.isEmpty() && !content.isEmpty()) {
                    messages.add(new MessageItem(role, content));
                }
            }
        } catch (JSONException ignored) {
            messages.clear();
        }
    }

    private void saveMessages() {
        JSONArray array = new JSONArray();
        for (MessageItem item : messages) {
            JSONObject obj = new JSONObject();
            try {
                obj.put("role", item.role);
                obj.put("content", item.content);
                array.put(obj);
            } catch (JSONException ignored) {
                // Skip malformed local message.
            }
        }
        getPrefs().edit().putString(PREF_MESSAGES, array.toString()).apply();
    }

    private void scrollToBottom() {
        scrollView.post(() -> scrollView.fullScroll(View.FOCUS_DOWN));
    }

    private int dp(int value) {
        return (int) (value * getResources().getDisplayMetrics().density + 0.5f);
    }

    private static class MessageItem {
        final String role;
        final String content;

        MessageItem(String role, String content) {
            this.role = role;
            this.content = content;
        }
    }
}
