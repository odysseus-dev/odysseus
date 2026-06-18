package com.odysseus.simplesignal;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.ActivityNotFoundException;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Rect;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.print.PrintAttributes;
import android.print.PrintDocumentAdapter;
import android.print.PrintManager;
import android.provider.MediaStore;
import android.view.DisplayCutout;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.view.WindowManager;
import android.view.inputmethod.EditorInfo;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.webkit.WebViewDatabase;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class MainActivity extends Activity {
    private static final String PREFS_NAME = "odysseus_android";
    private static final String PREF_URL = "server_url";
    private static final String PREF_MODE = "app_mode";
    private static final String MODE_REMOTE = "remote";
    private static final String MODE_STANDALONE = "standalone";
    private static final int FILE_CHOOSER_REQUEST = 1001;
    private static final int NOTIFICATION_PERMISSION_REQUEST = 1002;

    private WebView webView;
    private LinearLayout fallbackView;
    private EditText urlInput;
    private ProgressBar progressBar;
    private ValueCallback<Uri[]> filePathCallback;
    private volatile String cutoutSide = "none";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        configureSystemBars();
        String mode = getSavedMode();
        if (MODE_STANDALONE.equals(mode)) {
            startStandaloneMode();
            return;
        }
        if (mode.isEmpty()) {
            showModeChooser();
            return;
        }
        startRemoteMode();
    }

    private void startRemoteMode() {
        ensureNotificationPermission();
        buildLayout();
        configureWebView();
        loadConfiguredUrl();
    }

    private void configureSystemBars() {
        getWindow().setStatusBarColor(Color.TRANSPARENT);
        getWindow().setNavigationBarColor(Color.TRANSPARENT);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            getWindow().setStatusBarContrastEnforced(false);
            getWindow().setNavigationBarContrastEnforced(false);
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            WindowManager.LayoutParams attributes = getWindow().getAttributes();
            attributes.layoutInDisplayCutoutMode =
                    WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES;
            getWindow().setAttributes(attributes);
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            getWindow().setDecorFitsSystemWindows(false);
        } else {
            getWindow().getDecorView().setSystemUiVisibility(
                    View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                            | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                            | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
            );
        }
    }

    private void buildLayout() {
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.rgb(5, 8, 5));
        applySystemBarPadding(root);

        webView = new WebView(this);
        root.addView(webView, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
        ));

        progressBar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progressBar.setMax(100);
        progressBar.setVisibility(View.GONE);
        FrameLayout.LayoutParams progressParams = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                dp(3)
        );
        progressParams.gravity = Gravity.TOP;
        root.addView(progressBar, progressParams);

        fallbackView = new LinearLayout(this);
        fallbackView.setOrientation(LinearLayout.VERTICAL);
        fallbackView.setGravity(Gravity.CENTER_HORIZONTAL);
        fallbackView.setPadding(dp(24), dp(40), dp(24), dp(24));
        fallbackView.setBackgroundColor(Color.rgb(5, 8, 5));
        fallbackView.setVisibility(View.GONE);

        TextView title = new TextView(this);
        title.setText("Open Odysseus");
        title.setTextColor(Color.rgb(34, 255, 34));
        title.setTextSize(26);
        title.setGravity(Gravity.CENTER);
        fallbackView.addView(title, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        ));

        TextView help = new TextView(this);
        help.setText("Start the Odysseus backend, then point this app at its URL.\n\n" +
                "Emulator: http://10.0.2.2:7000\n" +
                "Physical phone: use your computer's LAN or Tailscale URL.");
        help.setTextColor(Color.rgb(237, 244, 237));
        help.setTextSize(14);
        help.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams helpParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        );
        helpParams.setMargins(0, dp(16), 0, dp(18));
        fallbackView.addView(help, helpParams);

        urlInput = new EditText(this);
        urlInput.setSingleLine(true);
        urlInput.setTextColor(Color.rgb(237, 244, 237));
        urlInput.setHintTextColor(Color.rgb(130, 150, 130));
        urlInput.setText(getConfiguredUrl());
        urlInput.setHint("http://10.0.2.2:7000");
        urlInput.setInputType(android.text.InputType.TYPE_CLASS_TEXT | android.text.InputType.TYPE_TEXT_VARIATION_URI);
        urlInput.setImeOptions(EditorInfo.IME_ACTION_GO);
        fallbackView.addView(urlInput, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        ));

        LinearLayout actions = new LinearLayout(this);
        actions.setOrientation(LinearLayout.HORIZONTAL);
        actions.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams actionsParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        );
        actionsParams.setMargins(0, dp(14), 0, 0);
        fallbackView.addView(actions, actionsParams);

        Button retry = new Button(this);
        retry.setText("Open");
        actions.addView(retry, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));

        Button reset = new Button(this);
        reset.setText(BuildConfig.ODYSSEUS_DEFAULT_URL.contains("10.0.2.2") ? "Emulator" : "Default");
        LinearLayout.LayoutParams resetParams = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1);
        resetParams.setMargins(dp(8), 0, 0, 0);
        actions.addView(reset, resetParams);

        Button standalone = new Button(this);
        standalone.setText("Standalone");
        LinearLayout.LayoutParams standaloneParams = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1);
        standaloneParams.setMargins(dp(8), 0, 0, 0);
        actions.addView(standalone, standaloneParams);

        retry.setOnClickListener(v -> {
            String normalized = normalizeUrl(urlInput.getText().toString());
            if (normalized.isEmpty()) {
                Toast.makeText(this, "Enter an Odysseus URL", Toast.LENGTH_SHORT).show();
                return;
            }
            saveConfiguredUrl(normalized);
            loadUrl(normalized);
        });
        reset.setOnClickListener(v -> {
            urlInput.setText(BuildConfig.ODYSSEUS_DEFAULT_URL);
            saveConfiguredUrl(BuildConfig.ODYSSEUS_DEFAULT_URL);
            loadUrl(BuildConfig.ODYSSEUS_DEFAULT_URL);
        });
        standalone.setOnClickListener(v -> startStandaloneMode());
        urlInput.setOnEditorActionListener((v, actionId, event) -> {
            if (actionId == EditorInfo.IME_ACTION_GO) {
                retry.performClick();
                return true;
            }
            return false;
        });

        root.addView(fallbackView, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
        ));

        setContentView(root);
    }

    @SuppressWarnings("deprecation")
    private void applySystemBarPadding(View root) {
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            int top;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                android.graphics.Insets statusBars = insets.getInsets(WindowInsets.Type.statusBars());
                android.graphics.Insets cutout = insets.getInsets(WindowInsets.Type.displayCutout());
                top = Math.max(statusBars.top, cutout.top);
            } else {
                top = insets.getSystemWindowInsetTop();
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                    DisplayCutout cutout = insets.getDisplayCutout();
                    if (cutout != null) {
                        top = Math.max(top, cutout.getSafeInsetTop());
                    }
                }
            }
            updateCutoutSide(view, insets);
            view.setPadding(0, top, 0, 0);
            return insets;
        });
        if (root.isAttachedToWindow()) {
            root.requestApplyInsets();
        } else {
            root.addOnAttachStateChangeListener(new View.OnAttachStateChangeListener() {
                @Override
                public void onViewAttachedToWindow(View view) {
                    view.removeOnAttachStateChangeListener(this);
                    view.requestApplyInsets();
                }

                @Override
                public void onViewDetachedFromWindow(View view) {}
            });
        }
    }

    private void updateCutoutSide(View view, WindowInsets insets) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.P || insets == null) {
            setCutoutSide("none");
            return;
        }
        DisplayCutout cutout = insets.getDisplayCutout();
        if (cutout == null) {
            setCutoutSide("none");
            return;
        }
        int safeLeft = cutout.getSafeInsetLeft();
        int safeRight = cutout.getSafeInsetRight();
        if (safeLeft > safeRight && safeLeft > 0) {
            setCutoutSide("left");
            return;
        }
        if (safeRight > safeLeft && safeRight > 0) {
            setCutoutSide("right");
            return;
        }
        int width = view == null ? 0 : view.getWidth();
        if (width <= 0) width = getResources().getDisplayMetrics().widthPixels;
        List<Rect> rects = cutout.getBoundingRects();
        if (rects == null || rects.isEmpty() || width <= 0) {
            setCutoutSide("none");
            return;
        }
        int leftHits = 0;
        int rightHits = 0;
        for (Rect rect : rects) {
            if (rect == null || rect.isEmpty()) continue;
            int centerX = rect.left + rect.width() / 2;
            if (centerX < width / 2) {
                leftHits++;
            } else {
                rightHits++;
            }
        }
        if (leftHits > rightHits) {
            setCutoutSide("left");
        } else if (rightHits > leftHits) {
            setCutoutSide("right");
        } else {
            setCutoutSide("none");
        }
    }

    private void setCutoutSide(String side) {
        String normalized = ("left".equals(side) || "right".equals(side)) ? side : "none";
        if (normalized.equals(cutoutSide)) return;
        cutoutSide = normalized;
        WebView currentWebView = webView;
        if (currentWebView == null) return;
        currentWebView.post(() -> currentWebView.evaluateJavascript(
                "window.dispatchEvent(new CustomEvent('odysseus:cutoutchange',{detail:{side:'"
                        + normalized
                        + "'}}));",
                null
        ));
    }

    private void showModeChooser() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setGravity(Gravity.CENTER_HORIZONTAL);
        root.setPadding(dp(24), dp(42), dp(24), dp(24));
        root.setBackgroundColor(Color.rgb(5, 8, 5));

        TextView title = new TextView(this);
        title.setText("Odysseus");
        title.setTextColor(Color.rgb(34, 255, 34));
        title.setTextSize(30);
        title.setGravity(Gravity.CENTER);
        root.addView(title, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        ));

        TextView subtitle = new TextView(this);
        subtitle.setText("Choose how this phone should run Odysseus.");
        subtitle.setTextColor(Color.rgb(205, 215, 205));
        subtitle.setTextSize(15);
        subtitle.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams subtitleParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        );
        subtitleParams.setMargins(0, dp(12), 0, dp(24));
        root.addView(subtitle, subtitleParams);

        Button standalone = new Button(this);
        standalone.setText("Standalone Mobile");
        root.addView(standalone, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        ));

        TextView standaloneHelp = new TextView(this);
        standaloneHelp.setText("Chat from the phone with OpenAI-compatible APIs. No PC server required.");
        standaloneHelp.setTextColor(Color.rgb(145, 165, 145));
        standaloneHelp.setTextSize(13);
        standaloneHelp.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams standaloneHelpParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        );
        standaloneHelpParams.setMargins(0, dp(8), 0, dp(18));
        root.addView(standaloneHelp, standaloneHelpParams);

        Button remote = new Button(this);
        remote.setText("Connect to PC");
        root.addView(remote, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        ));

        TextView remoteHelp = new TextView(this);
        remoteHelp.setText("Use the full Python Odysseus backend running on your computer.");
        remoteHelp.setTextColor(Color.rgb(145, 165, 145));
        remoteHelp.setTextSize(13);
        remoteHelp.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams remoteHelpParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        );
        remoteHelpParams.setMargins(0, dp(8), 0, 0);
        root.addView(remoteHelp, remoteHelpParams);

        standalone.setOnClickListener(v -> startStandaloneMode());
        remote.setOnClickListener(v -> {
            saveMode(MODE_REMOTE);
            startRemoteMode();
        });

        setContentView(root);
    }

    @SuppressLint("SetJavaScriptEnabled")
    @SuppressWarnings("deprecation")
    private void configureWebView() {
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setAllowContentAccess(true);
        settings.setAllowFileAccess(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setUseWideViewPort(true);
        settings.setLoadWithOverviewMode(false);
        settings.setSupportMultipleWindows(false);
        settings.setSaveFormData(false);

        // Custom User-Agent to help backend/frontend detect the Odysseus Android App
        String originalAgent = settings.getUserAgentString();
        settings.setUserAgentString(originalAgent + " OdysseusAndroid/1.0");

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            settings.setMixedContentMode(WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE);
            CookieManager.getInstance().setAcceptThirdPartyCookies(webView, true);
        }
        CookieManager.getInstance().setAcceptCookie(true);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            settings.setSafeBrowsingEnabled(true);
        }

        webView.setWebViewClient(new OdysseusWebViewClient());
        webView.setWebChromeClient(new OdysseusChromeClient());
        webView.addJavascriptInterface(new OdysseusAndroidBridge(), "OdysseusAndroid");
        webView.clearFormData();
        WebViewDatabase.getInstance(this).clearFormData();
    }

    private void loadConfiguredUrl() {
        loadUrl(getConfiguredUrl());
    }

    private void loadUrl(String url) {
        fallbackView.setVisibility(View.GONE);
        webView.setVisibility(View.VISIBLE);
        webView.loadUrl(url);
    }

    private String getConfiguredUrl() {
        SharedPreferences prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
        return prefs.getString(PREF_URL, BuildConfig.ODYSSEUS_DEFAULT_URL);
    }

    private String getSavedMode() {
        SharedPreferences prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
        return prefs.getString(PREF_MODE, "");
    }

    private void saveMode(String mode) {
        getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                .edit()
                .putString(PREF_MODE, mode)
                .apply();
    }

    private void saveConfiguredUrl(String url) {
        getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                .edit()
                .putString(PREF_URL, url)
                .apply();
    }

    private String normalizeUrl(String raw) {
        String url = raw == null ? "" : raw.trim();
        if (url.isEmpty()) return "";
        if (!url.startsWith("http://") && !url.startsWith("https://")) {
            url = "http://" + url;
        }
        return url;
    }

    private void showFallback() {
        urlInput.setText(getConfiguredUrl());
        webView.setVisibility(View.GONE);
        fallbackView.setVisibility(View.VISIBLE);
        progressBar.setVisibility(View.GONE);
    }

    private void startStandaloneMode() {
        saveMode(MODE_STANDALONE);
        buildLayout();
        configureWebView();
        ensureStandaloneBackgroundService();
        try {
            String baseUrl = MobileBackendServer.getInstance().start(this);
            loadUrl(baseUrl + "/static/index.html?mobile=standalone");
        } catch (Exception ex) {
            Toast.makeText(this, "Mobile backend failed: " + ex.getMessage(), Toast.LENGTH_LONG).show();
            showFallback();
        }
    }

    private void ensureStandaloneBackgroundService() {
        ensureNotificationPermission();
        MobileBackendService.start(this);
    }

    private void ensureNotificationPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
                && checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(
                    new String[]{android.Manifest.permission.POST_NOTIFICATIONS},
                    NOTIFICATION_PERMISSION_REQUEST
            );
        }
    }

    @Override
    public void onBackPressed() {
        if (fallbackView.getVisibility() == View.VISIBLE) {
            super.onBackPressed();
            return;
        }
        if (webView.canGoBack()) {
            webView.goBack();
            return;
        }
        super.onBackPressed();
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != FILE_CHOOSER_REQUEST || filePathCallback == null) return;

        Uri[] results = null;
        if (resultCode == RESULT_OK && data != null) {
            if (data.getClipData() != null) {
                int count = data.getClipData().getItemCount();
                results = new Uri[count];
                for (int i = 0; i < count; i++) {
                    results[i] = data.getClipData().getItemAt(i).getUri();
                }
            } else if (data.getData() != null) {
                results = new Uri[]{data.getData()};
            }
        }
        filePathCallback.onReceiveValue(results);
        filePathCallback = null;
    }

    private int dp(int value) {
        return (int) (value * getResources().getDisplayMetrics().density + 0.5f);
    }

    private void printCurrentWebView(String rawTitle) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.KITKAT) {
            Toast.makeText(this, "Android print is not available on this device", Toast.LENGTH_LONG).show();
            return;
        }
        PrintManager printManager = (PrintManager) getSystemService(Context.PRINT_SERVICE);
        if (printManager == null) {
            Toast.makeText(this, "Android print service is not available", Toast.LENGTH_LONG).show();
            return;
        }
        String title = safeReportFileName(rawTitle, "odysseus-report").replace(".html", "");
        PrintDocumentAdapter adapter;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            adapter = webView.createPrintDocumentAdapter(title);
        } else {
            adapter = webView.createPrintDocumentAdapter();
        }
        PrintAttributes attrs = new PrintAttributes.Builder()
                .setMediaSize(PrintAttributes.MediaSize.ISO_A4)
                .setColorMode(PrintAttributes.COLOR_MODE_COLOR)
                .build();
        printManager.print(title, adapter, attrs);
    }

    private void saveHtmlToDownloads(String html, String rawTitle) {
        String filename = safeReportFileName(rawTitle, "odysseus-report") + ".html";
        byte[] bytes = formatSavedHtml(html).getBytes(StandardCharsets.UTF_8);
        try {
            String location;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                ContentResolver resolver = getContentResolver();
                ContentValues values = new ContentValues();
                values.put(MediaStore.Downloads.DISPLAY_NAME, filename);
                values.put(MediaStore.Downloads.MIME_TYPE, "text/html");
                values.put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/Odysseus");
                values.put(MediaStore.Downloads.IS_PENDING, 1);
                Uri uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values);
                if (uri == null) throw new IllegalStateException("Could not create Downloads file");
                try (OutputStream output = resolver.openOutputStream(uri)) {
                    if (output == null) throw new IllegalStateException("Could not open Downloads file");
                    output.write(bytes);
                }
                values.clear();
                values.put(MediaStore.Downloads.IS_PENDING, 0);
                resolver.update(uri, values, null, null);
                location = "Downloads/Odysseus/" + filename;
            } else {
                File dir = new File(getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS), "Odysseus");
                if (!dir.exists() && !dir.mkdirs()) throw new IllegalStateException("Could not create report folder");
                File file = new File(dir, filename);
                try (FileOutputStream output = new FileOutputStream(file)) {
                    output.write(bytes);
                }
                location = file.getAbsolutePath();
            }
            String message = "Saved HTML to " + location;
            runOnUiThread(() -> Toast.makeText(MainActivity.this, message, Toast.LENGTH_LONG).show());
        } catch (Exception ex) {
            String message = "Save HTML failed: " + ex.getMessage();
            runOnUiThread(() -> Toast.makeText(MainActivity.this, message, Toast.LENGTH_LONG).show());
        }
    }

    private String formatSavedHtml(String html) {
        String text = valueOr(html, "<!doctype html><html><body></body></html>").trim();
        if (!text.toLowerCase().startsWith("<!doctype")) {
            text = "<!doctype html>\n" + text;
        }
        text = text.replace("><", ">\n<");
        text = formatTagBlocks(text, "style", true);
        text = formatTagBlocks(text, "script", false);
        return text.trim() + "\n";
    }

    private String formatTagBlocks(String html, String tag, boolean formatAsCss) {
        Pattern pattern = Pattern.compile("(?is)<" + tag + "([^>]*)>(.*?)</" + tag + ">");
        Matcher matcher = pattern.matcher(html);
        StringBuffer output = new StringBuffer();
        while (matcher.find()) {
            String attrs = matcher.group(1);
            String body = matcher.group(2);
            String formattedBody = formatAsCss ? formatCssBlock(body) : formatScriptBlock(body);
            String replacement = "<" + tag + attrs + ">\n" + formattedBody + "\n</" + tag + ">";
            matcher.appendReplacement(output, Matcher.quoteReplacement(replacement));
        }
        matcher.appendTail(output);
        return output.toString();
    }

    private String formatCssBlock(String css) {
        return valueOr(css, "").trim()
                .replace("/*", "\n/*")
                .replace("*/", "*/\n")
                .replace("{", " {\n  ")
                .replace(";", ";\n  ")
                .replace("}", "\n}\n")
                .replaceAll("[ \\t]+\\n", "\n")
                .replaceAll("\\n{3,}", "\n\n")
                .trim();
    }

    private String formatScriptBlock(String script) {
        return valueOr(script, "").trim()
                .replace("{", "{\n  ")
                .replace(";", ";\n")
                .replace("}", "\n}\n")
                .replaceAll("[ \\t]+\\n", "\n")
                .replaceAll("\\n{3,}", "\n\n")
                .trim();
    }

    private String safeReportFileName(String rawTitle, String fallback) {
        String title = rawTitle == null ? "" : rawTitle.trim();
        if (title.isEmpty()) title = fallback;
        title = title.replaceAll("[^A-Za-z0-9._-]+", "-").replaceAll("^-+|-+$", "");
        if (title.isEmpty()) title = fallback;
        if (title.length() > 80) title = title.substring(0, 80).replaceAll("-+$", "");
        return title;
    }

    private String valueOr(String value, String fallback) {
        return value == null ? fallback : value;
    }

    private boolean isGoogleMapsUrl(Uri uri) {
        if (uri == null) return false;
        String host = uri.getHost();
        String path = uri.getPath();
        if (host == null) return false;
        host = host.toLowerCase();
        path = path == null ? "" : path.toLowerCase();
        return "maps.google.com".equals(host)
                || (("www.google.com".equals(host) || "google.com".equals(host)) && path.startsWith("/maps"));
    }

    private void openExternalUri(Uri uri) {
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, uri));
        } catch (ActivityNotFoundException ignored) {
            Toast.makeText(MainActivity.this, "No app can open this link", Toast.LENGTH_SHORT).show();
        }
    }

    private class OdysseusAndroidBridge {
        @JavascriptInterface
        public void printReport(String title) {
            runOnUiThread(() -> printCurrentWebView(title));
        }

        @JavascriptInterface
        public void saveHtml(String html, String title) {
            new Thread(() -> saveHtmlToDownloads(html, title), "OdysseusSaveHtml").start();
        }

        @JavascriptInterface
        public void notifyResearchComplete(String researchId, String query) {
            OdysseusNotifications.showResearchComplete(MainActivity.this, researchId, query);
        }

        @JavascriptInterface
        public String getCutoutSide() {
            return cutoutSide == null ? "none" : cutoutSide;
        }
    }

    private class OdysseusWebViewClient extends WebViewClient {
        @Override
        public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
            Uri uri = request.getUrl();
            String scheme = uri == null ? "" : uri.getScheme();
            if ("http".equals(scheme) || "https".equals(scheme)) {
                if (request.isForMainFrame() && isGoogleMapsUrl(uri)) {
                    openExternalUri(uri);
                    return true;
                }
                return false;
            }
            openExternalUri(uri);
            return true;
        }

        @Override
        public void onPageFinished(WebView view, String url) {
            super.onPageFinished(view, url);
            progressBar.setVisibility(View.GONE);
        }

        @Override
        public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
            super.onReceivedError(view, request, error);
            if (request.isForMainFrame()) {
                showFallback();
            }
        }
    }

    private class OdysseusChromeClient extends WebChromeClient {
        @Override
        public void onProgressChanged(WebView view, int newProgress) {
            progressBar.setProgress(newProgress);
            progressBar.setVisibility(newProgress >= 100 ? View.GONE : View.VISIBLE);
        }

        @Override
        public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> filePath,
                                         WebChromeClient.FileChooserParams fileChooserParams) {
            if (filePathCallback != null) {
                filePathCallback.onReceiveValue(null);
            }
            filePathCallback = filePath;

            Intent intent = fileChooserParams.createIntent();
            intent.addCategory(Intent.CATEGORY_OPENABLE);
            intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE,
                    fileChooserParams.getMode() == WebChromeClient.FileChooserParams.MODE_OPEN_MULTIPLE);

            try {
                startActivityForResult(intent, FILE_CHOOSER_REQUEST);
                return true;
            } catch (ActivityNotFoundException ex) {
                try {
                    Intent fallback = new Intent(Intent.ACTION_OPEN_DOCUMENT);
                    fallback.addCategory(Intent.CATEGORY_OPENABLE);
                    fallback.setType("*/*");
                    startActivityForResult(fallback, FILE_CHOOSER_REQUEST);
                    return true;
                } catch (ActivityNotFoundException ex2) {
                    filePathCallback.onReceiveValue(null);
                    filePathCallback = null;
                    return false;
                }
            }
        }
    }
}
