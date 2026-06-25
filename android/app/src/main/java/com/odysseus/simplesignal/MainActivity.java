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
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.print.PrintAttributes;
import android.print.PrintDocumentAdapter;
import android.print.PrintManager;
import android.provider.MediaStore;
import android.provider.Settings;
import android.util.Base64;
import android.view.DisplayCutout;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.view.WindowManager;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.InputMethodManager;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.MimeTypeMap;
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
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class MainActivity extends Activity {
    private static final String PREFS_NAME = "odysseus_android";
    private static final String PREF_URL = "server_url";
    private static final String PREF_MODE = "app_mode";
    private static final String PREF_STORAGE_ACCESS_PROMPTED = "storage_access_prompted";
    private static final String MODE_REMOTE = "remote";
    private static final String MODE_STANDALONE = "standalone";
    private static final String PC_TOOLS_LABEL = "ADB PC Tools";
    private static final int FILE_CHOOSER_REQUEST = 1001;
    private static final int NOTIFICATION_PERMISSION_REQUEST = 1002;
    private static final int STORAGE_PERMISSION_REQUEST = 1003;
    private static final int COLOR_BG = Color.rgb(13, 15, 14);
    private static final int COLOR_PANEL = Color.rgb(21, 25, 23);
    private static final int COLOR_PANEL_SOFT = Color.rgb(26, 31, 29);
    private static final int COLOR_INPUT = Color.rgb(12, 14, 13);
    private static final int COLOR_LINE = Color.rgb(54, 65, 59);
    private static final int COLOR_LINE_STRONG = Color.rgb(122, 143, 129);
    private static final int COLOR_TEXT = Color.rgb(236, 241, 235);
    private static final int COLOR_MUTED = Color.rgb(163, 174, 166);
    private static final int COLOR_SUBTLE = Color.rgb(119, 130, 122);
    private static final int COLOR_ACCENT = Color.rgb(188, 210, 178);
    private static final int COLOR_ACCENT_DARK = Color.rgb(52, 68, 56);

    private WebView webView;
    private LinearLayout fallbackView;
    private EditText urlInput;
    private ProgressBar progressBar;
    private ValueCallback<Uri[]> filePathCallback;
    private volatile String cutoutSide = "none";
    private boolean modeChooserVisible = false;
    private boolean modeChooserCanCancel = false;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        configureSystemBars();

        // Accept mode config via intent extras — works on release builds (no run-as needed)
        Bundle extras = getIntent().getExtras();
        if (extras != null) {
            if (extras.containsKey(PREF_MODE)) {
                saveMode(extras.getString(PREF_MODE));
            }
            if (extras.containsKey(PREF_URL)) {
                saveConfiguredUrl(extras.getString(PREF_URL));
            }
        }

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

    private void startRemoteModeAt(String url) {
        saveMode(MODE_REMOTE);
        saveConfiguredUrl(url);
        startRemoteMode();
    }

    private void configureSystemBars() {
        getWindow().setStatusBarColor(Color.TRANSPARENT);
        getWindow().setNavigationBarColor(Color.TRANSPARENT);
        getWindow().setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_NOTHING);
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
        modeChooserVisible = false;
        modeChooserCanCancel = false;

        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(COLOR_BG);
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
        fallbackView.setGravity(Gravity.CENTER);
        fallbackView.setPadding(dp(22), dp(28), dp(22), dp(24));
        fallbackView.setBackgroundColor(COLOR_BG);
        fallbackView.setVisibility(View.GONE);

        LinearLayout fallbackPanel = new LinearLayout(this);
        fallbackPanel.setOrientation(LinearLayout.VERTICAL);
        fallbackPanel.setPadding(dp(22), dp(22), dp(22), dp(22));
        fallbackPanel.setBackground(rounded(COLOR_PANEL, COLOR_LINE, 1, 18));
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) fallbackPanel.setElevation(dp(3));

        TextView title = new TextView(this);
        title.setText("Connect to Odysseus");
        title.setTextColor(COLOR_TEXT);
        title.setTextSize(24);
        title.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        title.setGravity(Gravity.CENTER);
        fallbackPanel.addView(title, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        ));

        TextView help = new TextView(this);
        help.setText("Choose the backend this phone should open. Wireless debugging uses the full PC tool stack through ADB reverse.");
        help.setTextColor(COLOR_MUTED);
        help.setTextSize(14);
        help.setGravity(Gravity.CENTER);
        help.setLineSpacing(dp(2), 1.0f);
        LinearLayout.LayoutParams helpParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        );
        helpParams.setMargins(0, dp(10), 0, dp(18));
        fallbackPanel.addView(help, helpParams);

        urlInput = new EditText(this);
        urlInput.setSingleLine(true);
        urlInput.setTextColor(COLOR_TEXT);
        urlInput.setHintTextColor(COLOR_SUBTLE);
        urlInput.setText(getConfiguredUrl());
        urlInput.setHint("http://127.0.0.1:7000");
        urlInput.setInputType(android.text.InputType.TYPE_CLASS_TEXT | android.text.InputType.TYPE_TEXT_VARIATION_URI);
        urlInput.setImeOptions(EditorInfo.IME_ACTION_GO);
        urlInput.setMinHeight(dp(50));
        urlInput.setPadding(dp(14), 0, dp(14), 0);
        urlInput.setTextSize(14);
        urlInput.setBackground(rounded(COLOR_INPUT, COLOR_LINE, 1, 12));
        fallbackPanel.addView(urlInput, new LinearLayout.LayoutParams(
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
        fallbackPanel.addView(actions, actionsParams);

        TextView retry = createActionButton("Open", true);
        retry.setText("Open");
        actions.addView(retry, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));

        TextView reset = createActionButton(BuildConfig.ODYSSEUS_DEFAULT_URL.contains("10.0.2.2") ? "Emulator" : "Default", false);
        reset.setText(BuildConfig.ODYSSEUS_DEFAULT_URL.contains("10.0.2.2") ? "Emulator" : "Default");
        LinearLayout.LayoutParams resetParams = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1);
        resetParams.setMargins(dp(8), 0, 0, 0);
        actions.addView(reset, resetParams);

        TextView pcTools = createActionButton("ADB PC", false);
        pcTools.setText("ADB PC");
        LinearLayout.LayoutParams pcToolsParams = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1);
        pcToolsParams.setMargins(dp(8), 0, 0, 0);
        actions.addView(pcTools, pcToolsParams);

        TextView standalone = createActionButton("Standalone", false);
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
        pcTools.setOnClickListener(v -> {
            urlInput.setText(BuildConfig.ODYSSEUS_ADB_REVERSE_URL);
            startRemoteModeAt(BuildConfig.ODYSSEUS_ADB_REVERSE_URL);
        });
        standalone.setOnClickListener(v -> startStandaloneMode());
        urlInput.setOnEditorActionListener((v, actionId, event) -> {
            if (actionId == EditorInfo.IME_ACTION_GO) {
                retry.performClick();
                return true;
            }
            return false;
        });

        TextView helper = new TextView(this);
        helper.setText("ADB PC uses http://127.0.0.1:7000. URL mode also supports LAN, Tailscale, and emulator addresses.");
        helper.setTextColor(COLOR_SUBTLE);
        helper.setTextSize(12);
        helper.setGravity(Gravity.CENTER);
        helper.setLineSpacing(dp(2), 1.0f);
        LinearLayout.LayoutParams helperParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        );
        helperParams.setMargins(0, dp(14), 0, 0);
        fallbackPanel.addView(helper, helperParams);

        fallbackView.addView(fallbackPanel, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        ));

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
        showModeChooser(false);
    }

    private void showModeChooser(boolean allowCancel) {
        modeChooserVisible = true;
        modeChooserCanCancel = allowCancel;

        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(COLOR_BG);
        applySystemBarPadding(root);

        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);

        LinearLayout content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setGravity(Gravity.CENTER_HORIZONTAL);
        content.setPadding(dp(22), dp(30), dp(22), dp(24));

        LinearLayout panel = new LinearLayout(this);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setPadding(dp(22), dp(22), dp(22), dp(20));
        panel.setBackground(rounded(COLOR_PANEL, COLOR_LINE, 1, 20));
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) panel.setElevation(dp(3));

        TextView title = new TextView(this);
        title.setText("Odysseus");
        title.setTextColor(COLOR_TEXT);
        title.setTextSize(30);
        title.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        title.setGravity(Gravity.CENTER);
        panel.addView(title, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        ));

        TextView subtitle = new TextView(this);
        subtitle.setText("Choose how this phone connects.");
        subtitle.setTextColor(COLOR_MUTED);
        subtitle.setTextSize(15);
        subtitle.setGravity(Gravity.CENTER);
        subtitle.setLineSpacing(dp(2), 1.0f);
        LinearLayout.LayoutParams subtitleParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        );
        subtitleParams.setMargins(0, dp(8), 0, dp(20));
        panel.addView(subtitle, subtitleParams);

        panel.addView(createModeCard(
                PC_TOOLS_LABEL,
                "Recommended",
                "Use wireless debugging and ADB reverse to open the full PC backend with the same Agent tools, shell, files, MCP, Cookbook, and image tools.",
                true,
                v -> startRemoteModeAt(BuildConfig.ODYSSEUS_ADB_REVERSE_URL)
        ), cardParams(0));

        panel.addView(createModeCard(
                "Standalone Mobile",
                "Phone only",
                "Run directly on this device with configured API endpoints. Best when you are away from your computer.",
                false,
                v -> startStandaloneMode()
        ), cardParams(dp(10)));

        panel.addView(createModeCard(
                "Connect by URL",
                "Manual",
                "Use a LAN, Tailscale, or emulator backend address when ADB reverse is not the right connection.",
                false,
                v -> {
            saveMode(MODE_REMOTE);
            startRemoteMode();
                }
        ), cardParams(dp(10)));

        if (allowCancel) {
            panel.addView(createModeCard(
                    "Cancel",
                    "Back",
                    "Return to the current connection without changing modes.",
                    false,
                    v -> resumeSavedMode()
            ), cardParams(dp(10)));
        }

        TextView footer = new TextView(this);
        footer.setText(allowCancel
                ? "Cancel returns to the current connection."
                : "Open Connect from the sidebar menu when you want to switch modes later.");
        footer.setTextColor(COLOR_SUBTLE);
        footer.setTextSize(12);
        footer.setGravity(Gravity.CENTER);
        footer.setLineSpacing(dp(2), 1.0f);
        LinearLayout.LayoutParams footerParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        );
        footerParams.setMargins(0, dp(16), 0, 0);
        panel.addView(footer, footerParams);

        content.addView(panel, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        ));
        scroll.addView(content, new ScrollView.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        ));
        root.addView(scroll, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
        ));
        setContentView(root);
    }

    private void resumeSavedMode() {
        String mode = getSavedMode();
        if (MODE_STANDALONE.equals(mode)) {
            startStandaloneMode();
            return;
        }
        if (MODE_REMOTE.equals(mode)) {
            startRemoteMode();
            return;
        }
        showModeChooser();
    }

    private GradientDrawable rounded(int fill, int stroke, int strokeDp, int radiusDp) {
        GradientDrawable drawable = new GradientDrawable();
        drawable.setColor(fill);
        drawable.setCornerRadius(dp(radiusDp));
        if (strokeDp > 0) drawable.setStroke(dp(strokeDp), stroke);
        return drawable;
    }

    private TextView createActionButton(String label, boolean primary) {
        TextView button = new TextView(this);
        button.setText(label);
        button.setTextSize(13);
        button.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        button.setGravity(Gravity.CENTER);
        button.setMinHeight(dp(46));
        button.setPadding(dp(10), 0, dp(10), 0);
        button.setClickable(true);
        button.setFocusable(true);
        button.setTextColor(primary ? Color.rgb(20, 26, 21) : COLOR_TEXT);
        button.setBackground(rounded(
                primary ? COLOR_ACCENT : COLOR_PANEL_SOFT,
                primary ? COLOR_ACCENT : COLOR_LINE,
                1,
                12
        ));
        return button;
    }

    private LinearLayout.LayoutParams cardParams(int topMargin) {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        );
        params.setMargins(0, topMargin, 0, 0);
        return params;
    }

    private LinearLayout createModeCard(String title, String badge, String body,
                                        boolean primary, View.OnClickListener listener) {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(16), dp(15), dp(16), dp(15));
        card.setMinimumHeight(dp(100));
        card.setClickable(true);
        card.setFocusable(true);
        card.setOnClickListener(listener);
        card.setBackground(rounded(
                primary ? Color.rgb(31, 40, 33) : COLOR_PANEL_SOFT,
                primary ? COLOR_LINE_STRONG : COLOR_LINE,
                1,
                16
        ));

        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.HORIZONTAL);
        header.setGravity(Gravity.CENTER_VERTICAL);

        TextView titleView = new TextView(this);
        titleView.setText(title);
        titleView.setTextColor(COLOR_TEXT);
        titleView.setTextSize(17);
        titleView.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        header.addView(titleView, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));

        TextView badgeView = new TextView(this);
        badgeView.setText(badge);
        badgeView.setTextSize(11);
        badgeView.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        badgeView.setGravity(Gravity.CENTER);
        badgeView.setPadding(dp(10), dp(4), dp(10), dp(4));
        badgeView.setTextColor(primary ? Color.rgb(24, 30, 24) : COLOR_MUTED);
        badgeView.setBackground(rounded(
                primary ? COLOR_ACCENT : COLOR_INPUT,
                primary ? COLOR_ACCENT : COLOR_LINE,
                1,
                999
        ));
        header.addView(badgeView, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        ));

        card.addView(header, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        ));

        TextView bodyView = new TextView(this);
        bodyView.setText(body);
        bodyView.setTextColor(primary ? Color.rgb(201, 215, 202) : COLOR_MUTED);
        bodyView.setTextSize(13);
        bodyView.setLineSpacing(dp(2), 1.0f);
        LinearLayout.LayoutParams bodyParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        );
        bodyParams.setMargins(0, dp(8), 0, 0);
        card.addView(bodyView, bodyParams);
        return card;
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
        ensureStandaloneStorageAccess();
        try {
            String baseUrl = MobileBackendServer.getInstance().start(this);
            loadUrl(baseUrl + "/static/index.html?mobile=standalone&v=" + System.currentTimeMillis());
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

    private void ensureStandaloneStorageAccess() {
        ensureLegacyStoragePermission();
        maybePromptAllFilesAccess();
    }

    private void ensureLegacyStoragePermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) return;
        List<String> permissions = new ArrayList<>();
        if (checkSelfPermission(android.Manifest.permission.READ_EXTERNAL_STORAGE) != PackageManager.PERMISSION_GRANTED) {
            permissions.add(android.Manifest.permission.READ_EXTERNAL_STORAGE);
        }
        if (Build.VERSION.SDK_INT <= Build.VERSION_CODES.P
                && checkSelfPermission(android.Manifest.permission.WRITE_EXTERNAL_STORAGE) != PackageManager.PERMISSION_GRANTED) {
            permissions.add(android.Manifest.permission.WRITE_EXTERNAL_STORAGE);
        }
        if (!permissions.isEmpty()) {
            requestPermissions(permissions.toArray(new String[0]), STORAGE_PERMISSION_REQUEST);
        }
    }

    private void maybePromptAllFilesAccess() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) return;
        try {
            if (Environment.isExternalStorageManager()) return;
        } catch (Exception ignored) {
            return;
        }

        SharedPreferences prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        if (prefs.getBoolean(PREF_STORAGE_ACCESS_PROMPTED, false)) return;
        prefs.edit().putBoolean(PREF_STORAGE_ACCESS_PROMPTED, true).apply();
        Toast.makeText(this, "Allow all files access so Odysseus can edit project files.", Toast.LENGTH_LONG).show();

        Intent intent = new Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION);
        intent.setData(Uri.parse("package:" + getPackageName()));
        try {
            startActivity(intent);
        } catch (ActivityNotFoundException ex) {
            try {
                startActivity(new Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION));
            } catch (ActivityNotFoundException ignored) {
                Toast.makeText(this, "Open Android settings and allow Odysseus all files access.", Toast.LENGTH_LONG).show();
            }
        }
    }

    @Override
    public void onBackPressed() {
        if (modeChooserVisible) {
            if (modeChooserCanCancel) {
                resumeSavedMode();
            } else {
                super.onBackPressed();
            }
            return;
        }
        if (fallbackView != null && fallbackView.getVisibility() == View.VISIBLE) {
            super.onBackPressed();
            return;
        }
        if (webView != null && webView.canGoBack()) {
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
        saveBytesToDownloads(bytes, filename, "text/html", "HTML");
    }

    private void saveDownloadToDownloads(String dataUrl, String rawFilename, String rawMimeType) {
        try {
            String mimeType = normalizeDownloadMimeType(rawMimeType, dataUrl);
            String filename = safeDownloadFileName(rawFilename, "odysseus-download", mimeType);
            byte[] bytes = decodeDownloadDataUrl(dataUrl);
            saveBytesToDownloads(bytes, filename, mimeType, "Download");
        } catch (Exception ex) {
            String message = "Download failed: " + ex.getMessage();
            runOnUiThread(() -> Toast.makeText(MainActivity.this, message, Toast.LENGTH_LONG).show());
        }
    }

    private void saveBytesToDownloads(byte[] bytes, String filename, String mimeType, String label) {
        try {
            String location;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                ContentResolver resolver = getContentResolver();
                ContentValues values = new ContentValues();
                values.put(MediaStore.Downloads.DISPLAY_NAME, filename);
                values.put(MediaStore.Downloads.MIME_TYPE, mimeType);
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
                File root = getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS);
                if (root == null) root = getFilesDir();
                File dir = new File(root, "Odysseus");
                if (!dir.exists() && !dir.mkdirs()) throw new IllegalStateException("Could not create app downloads folder");
                File file = new File(dir, filename);
                try (FileOutputStream output = new FileOutputStream(file)) {
                    output.write(bytes);
                }
                location = file.getAbsolutePath();
            }
            String message = "Saved " + label + " to " + location;
            runOnUiThread(() -> Toast.makeText(MainActivity.this, message, Toast.LENGTH_LONG).show());
        } catch (Exception ex) {
            String message = "Save " + label + " failed: " + ex.getMessage();
            runOnUiThread(() -> Toast.makeText(MainActivity.this, message, Toast.LENGTH_LONG).show());
        }
    }

    private byte[] decodeDownloadDataUrl(String dataUrl) {
        String payload = valueOr(dataUrl, "").trim();
        int comma = payload.indexOf(',');
        if (payload.toLowerCase(Locale.US).startsWith("data:") && comma >= 0) {
            String header = payload.substring(0, comma).toLowerCase(Locale.US);
            if (!header.contains(";base64")) {
                throw new IllegalArgumentException("Only base64 downloads are supported");
            }
            payload = payload.substring(comma + 1);
        }
        if (payload.isEmpty()) throw new IllegalArgumentException("Empty download");
        return Base64.decode(payload, Base64.DEFAULT);
    }

    private String normalizeDownloadMimeType(String rawMimeType, String dataUrl) {
        String mimeType = valueOr(rawMimeType, "").trim().toLowerCase(Locale.US);
        String payload = valueOr(dataUrl, "").trim();
        if ((mimeType.isEmpty() || !mimeType.contains("/")) && payload.toLowerCase(Locale.US).startsWith("data:")) {
            int semi = payload.indexOf(';');
            int comma = payload.indexOf(',');
            int end = semi >= 0 ? semi : comma;
            if (end > 5) mimeType = payload.substring(5, end).trim().toLowerCase(Locale.US);
        }
        if (mimeType.isEmpty() || !mimeType.contains("/") || mimeType.contains("\n") || mimeType.contains("\r")) {
            return "application/octet-stream";
        }
        return mimeType;
    }

    private String safeDownloadFileName(String rawFilename, String fallback, String mimeType) {
        String filename = valueOr(rawFilename, "").trim();
        int slash = Math.max(filename.lastIndexOf('/'), filename.lastIndexOf('\\'));
        if (slash >= 0) filename = filename.substring(slash + 1);
        filename = filename.replaceAll("[^A-Za-z0-9._-]+", "-").replaceAll("^-+|-+$", "");
        if (filename.isEmpty()) filename = fallback;
        String ext = extensionForMimeType(mimeType);
        if (!hasFileExtension(filename) && !ext.isEmpty()) filename = filename + "." + ext;
        if (filename.length() > 100) {
            filename = filename.substring(0, 100).replaceAll("-+$", "");
        }
        return filename.isEmpty() ? fallback : filename;
    }

    private boolean hasFileExtension(String filename) {
        int dot = filename.lastIndexOf('.');
        return dot > 0 && dot < filename.length() - 1;
    }

    private String extensionForMimeType(String mimeType) {
        String normalized = valueOr(mimeType, "").toLowerCase(Locale.US);
        String ext = MimeTypeMap.getSingleton().getExtensionFromMimeType(normalized);
        if (ext != null && !ext.trim().isEmpty()) return ext.trim();
        if ("image/jpeg".equals(normalized)) return "jpg";
        if ("image/png".equals(normalized)) return "png";
        if ("image/webp".equals(normalized)) return "webp";
        if ("image/gif".equals(normalized)) return "gif";
        if ("application/zip".equals(normalized)) return "zip";
        return "";
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

    private boolean isExternalAuthUrl(Uri uri) {
        String host = uri == null ? "" : String.valueOf(uri.getHost()).toLowerCase(Locale.US);
        String path = uri == null ? "" : String.valueOf(uri.getPath()).toLowerCase(Locale.US);
        return "auth.openai.com".equals(host)
                || ("github.com".equals(host) && path.startsWith("/login/device"));
    }

    private void hideSoftKeyboard() {
        InputMethodManager imm = (InputMethodManager) getSystemService(Context.INPUT_METHOD_SERVICE);
        View target = getCurrentFocus();
        if (target == null) target = webView;
        if (imm != null && target != null) {
            imm.hideSoftInputFromWindow(target.getWindowToken(), 0);
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
        public void saveDownload(String dataUrl, String filename, String mimeType) {
            new Thread(
                    () -> saveDownloadToDownloads(dataUrl, filename, mimeType),
                    "OdysseusSaveDownload"
            ).start();
        }

        @JavascriptInterface
        public void notifyResearchComplete(String researchId, String query) {
            OdysseusNotifications.showResearchComplete(MainActivity.this, researchId, query);
        }

        @JavascriptInterface
        public String getCutoutSide() {
            return cutoutSide == null ? "none" : cutoutSide;
        }

        @JavascriptInterface
        public void hideKeyboard() {
            runOnUiThread(MainActivity.this::hideSoftKeyboard);
        }

        @JavascriptInterface
        public void showConnectionMode() {
            runOnUiThread(() -> {
                hideSoftKeyboard();
                showModeChooser(true);
            });
        }

        @JavascriptInterface
        public void switchToStandalone() {
            runOnUiThread(() -> {
                hideSoftKeyboard();
                startStandaloneMode();
            });
        }

        @JavascriptInterface
        public void switchToEmulator() {
            runOnUiThread(() -> {
                hideSoftKeyboard();
                startRemoteModeAt("http://10.0.2.2:7000");
            });
        }

        @JavascriptInterface
        public void switchToUrl(String url) {
            final String resolvedUrl = (url != null && !url.trim().isEmpty()) ? url : getConfiguredUrl();
            runOnUiThread(() -> {
                hideSoftKeyboard();
                startRemoteModeAt(resolvedUrl);
            });
        }
    }

    private class OdysseusWebViewClient extends WebViewClient {
        @Override
        public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
            Uri uri = request.getUrl();
            String scheme = uri == null ? "" : uri.getScheme();
            if ("http".equals(scheme) || "https".equals(scheme)) {
                if (request.isForMainFrame() && isExternalAuthUrl(uri)) {
                    openExternalUri(uri);
                    return true;
                }
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
