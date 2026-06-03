import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:odysseus_desktop/core/constants.dart';
import 'package:odysseus_desktop/services/backend_service.dart';
import 'package:odysseus_desktop/screens/webview_screen.dart';
import 'package:odysseus_desktop/screens/error_screen.dart';

class SplashScreen extends StatefulWidget {
  const SplashScreen({super.key});

  @override
  State<SplashScreen> createState() => _SplashScreenState();
}

class _SplashScreenState extends State<SplashScreen> {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _initApp();
    });
  }

  Future<void> _initApp() async {
    final backendService = context.read<BackendService>();
    await backendService.init();
    _handleStatusChange(backendService);
  }

  void _handleStatusChange(BackendService backendService) {
    if (!mounted) return;

    if (backendService.status == BackendStatus.ready) {
      Navigator.of(context).pushReplacement(
        MaterialPageRoute(builder: (_) => const WebViewScreen()),
      );
    } else if (backendService.status == BackendStatus.error) {
      Navigator.of(context).pushReplacement(
        MaterialPageRoute(
          builder: (errorContext) => ErrorScreen(
            error: backendService.lastError ?? 'Unknown error',
            onRetry: (ctx) {
              Navigator.of(ctx).pushReplacement(
                MaterialPageRoute(builder: (_) => const SplashScreen()),
              );
            },
          ),
        ),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final backendService = context.watch<BackendService>();
    
    // Also handle status changes during build if needed, 
    // but usually better to do it in response to the future or a listener.
    // However, since we are using pushReplacement, we should be careful.

    return Scaffold(
      body: Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Image.asset(
              'assets/logo.png',
              width: 150,
              height: 150,
            ),
            const SizedBox(height: 20),
            const Text(
              AppConstants.appTitle,
              style: TextStyle(fontSize: 32, fontWeight: FontWeight.bold),
            ),
            const SizedBox(height: 20),
            const CircularProgressIndicator(),
            const SizedBox(height: 20),
            Text(_getStatusText(backendService.status)),
          ],
        ),
      ),
    );
  }

  String _getStatusText(BackendStatus status) {
    switch (status) {
      case BackendStatus.checking:
        return 'Checking backend...';
      case BackendStatus.starting:
        return 'Starting backend...';
      case BackendStatus.ready:
        return 'Backend ready!';
      case BackendStatus.error:
        return 'Error occurred.';
      default:
        return 'Initializing...';
    }
  }
}
