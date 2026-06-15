package com.odysseus.simplesignal;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.inputmethod.EditorInfo;
import android.webkit.CookieManager;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

public class MainActivity extends Activity {
    private static final String PREFS_NAME = "odysseus_android";
    private static final String PREF_URL = "server_url";
    private static final int FILE_CHOOSER_REQUEST = 1001;

    private WebView webView;
    private LinearLayout fallbackView;
    private EditText urlInput;
    private ProgressBar progressBar;
    private ValueCallback<Uri[]> filePathCallback;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        configureSystemBars();
        buildLayout();
        configureWebView();
        loadConfiguredUrl();
    }

    private void configureSystemBars() {
        getWindow().setStatusBarColor(Color.rgb(5, 8, 5));
        getWindow().setNavigationBarColor(Color.rgb(5, 8, 5));
    }

    private void buildLayout() {
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.rgb(5, 8, 5));

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

    @SuppressLint("SetJavaScriptEnabled")
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

    private class OdysseusWebViewClient extends WebViewClient {
        @Override
        public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
            Uri uri = request.getUrl();
            String scheme = uri == null ? "" : uri.getScheme();
            if ("http".equals(scheme) || "https".equals(scheme)) {
                return false;
            }
            try {
                startActivity(new Intent(Intent.ACTION_VIEW, uri));
            } catch (ActivityNotFoundException ignored) {
                Toast.makeText(MainActivity.this, "No app can open this link", Toast.LENGTH_SHORT).show();
            }
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
