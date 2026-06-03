import 'dart:io';
import 'package:flutter/material.dart';
import 'package:odysseus_desktop/services/backend_service.dart';
import 'package:odysseus_desktop/services/settings_service.dart';
import 'package:odysseus_desktop/core/logger.dart';
import 'package:odysseus_desktop/core/app_config.dart';
import 'package:webview_flutter/webview_flutter.dart';
import 'package:webview_win_floating/webview_win_floating.dart';
import 'package:provider/provider.dart';
import 'package:odysseus_desktop/screens/settings_screen.dart';

class WebViewScreen extends StatefulWidget {
  const WebViewScreen({super.key});

  @override
  State<WebViewScreen> createState() => _WebViewScreenState();
}

class _WebViewScreenState extends State<WebViewScreen> {
  // Using dynamic to support WinWebViewController and WebViewController
  dynamic _controller;
  bool _initialized = false;

  @override
  void initState() {
    super.initState();
    _initController();
  }

  Future<void> _initController() async {
    try {
      AppLogger.i('Initializing WebView Controller (Standalone Mode)...');
      final settings = context.read<SettingsService>();
      
      if (Platform.isWindows) {
        final userDataFolder = await AppConfig.userDataDir;
        AppLogger.i('WebView UserDataFolder: $userDataFolder');
        
        AppLogger.i('Creating WinWebViewController...');
        final params = WindowsWebViewControllerCreationParams(
          userDataFolder: userDataFolder,
        );
        final winController = WinWebViewController(
          params: params,
        );
        _controller = winController;
        
        await winController.setJavaScriptMode(JavaScriptMode.unrestricted);
        await winController.setNavigationDelegate(WinNavigationDelegate(
          onProgress: (int progress) {},
          onPageStarted: (String url) {
            AppLogger.i('WebView started loading: $url');
          },
          onPageFinished: (String url) {
            AppLogger.i('WebView finished loading: $url');
          },
          onWebResourceError: (WebResourceError error) {
            AppLogger.e('WebView Error: ${error.description} (${error.errorCode})');
          },
        ));
      } else {
        AppLogger.i('Creating standard WebViewController...');
        final controller = WebViewController();
        _controller = controller;
        
        await controller.setJavaScriptMode(JavaScriptMode.unrestricted);
        await controller.setNavigationDelegate(NavigationDelegate(
          onProgress: (int progress) {},
          onPageStarted: (String url) {
            AppLogger.i('WebView started loading: $url');
          },
          onPageFinished: (String url) {
            AppLogger.i('WebView finished loading: $url');
          },
          onWebResourceError: (WebResourceError error) {
            AppLogger.e('WebView Error: ${error.description} (${error.errorCode})');
          },
        ));
      }
      
      // CRITICAL: Clear cache and cookies to force clean login
      AppLogger.i('Clearing WebView cache and cookies...');
      await _controller.clearCache();
      await WebViewCookieManager().clearCookies();
      
      AppLogger.i('Loading URL: ${settings.backendUrl}');
      await _controller.loadRequest(Uri.parse(settings.backendUrl));
      
      if (mounted) {
        setState(() {
          _initialized = true;
        });
        AppLogger.i('WebView Controller initialized successfully.');
      }
    } catch (e, stack) {
      AppLogger.e('Error initializing WebView', e, stack);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (!_initialized || _controller == null) {
      return const Scaffold(
        body: Center(
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              CircularProgressIndicator(),
              SizedBox(height: 16),
              Text('Initializing WebView...'),
            ],
          ),
        ),
      );
    }

    return Scaffold(
      body: _buildWebView(),
    );
  }

  Widget _buildWebView() {
    final backendService = context.watch<BackendService>();
    final interceptedPassword = backendService.extractedPassword;

    Widget webView;
    if (Platform.isWindows && _controller is WinWebViewController) {
      webView = WinWebViewWidget(controller: _controller as WinWebViewController);
    } else if (_controller is WebViewController) {
      webView = WebViewWidget(controller: _controller as WebViewController);
    } else {
      webView = const Center(child: Text('Unsupported Platform'));
    }

    return Column(
      children: [
        if (interceptedPassword != null)
          Container(
            width: double.infinity,
            color: Colors.amber.shade900.withOpacity(0.9),
            padding: const EdgeInsets.all(12),
            child: Row(
              children: [
                const Icon(Icons.security, color: Colors.white, size: 20),
                const SizedBox(width: 12),
                Expanded(
                  child: SelectableText(
                    'First-time setup password generated! Use username "admin" and password: $interceptedPassword',
                    style: const TextStyle(color: Colors.white, fontWeight: FontWeight.bold),
                  ),
                ),
              ],
            ),
          ),
        Expanded(child: webView),
      ],
    );
  }
}
