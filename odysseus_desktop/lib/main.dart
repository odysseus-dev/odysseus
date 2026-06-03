import 'package:flutter/material.dart';
import 'package:odysseus_desktop/core/constants.dart';
import 'package:odysseus_desktop/core/logger.dart';
import 'package:odysseus_desktop/screens/splash_screen.dart';
import 'package:odysseus_desktop/services/backend_service.dart';
import 'package:odysseus_desktop/services/health_check_service.dart';
import 'package:odysseus_desktop/services/process_service.dart';
import 'package:odysseus_desktop/services/reset_service.dart';
import 'package:odysseus_desktop/services/settings_service.dart';
import 'package:odysseus_desktop/services/storage_service.dart';
import 'package:provider/provider.dart';
import 'package:window_manager/window_manager.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  
  // Initialize Logger
  await AppLogger.init();
  AppLogger.i('Starting Odysseus Desktop Launcher...');

  // Initialize Window Manager
  await windowManager.ensureInitialized();

  // Initialize Storage and Settings
  final storage = StorageService();
  await storage.init();
  final settings = SettingsService(storage);

  // Initialize Services
  final processService = ProcessService();
  final healthCheckService = HealthCheckService();
  final resetService = ResetService();
  final backendService = BackendService(settings, processService, healthCheckService, resetService);

  // Configure Window
  WindowOptions windowOptions = WindowOptions(
    size: Size(settings.windowWidth, settings.windowHeight),
    minimumSize: const Size(AppConstants.minWindowWidth, AppConstants.minWindowHeight),
    center: true,
    backgroundColor: Colors.transparent,
    skipTaskbar: false,
    titleBarStyle: TitleBarStyle.normal,
    title: AppConstants.appTitle,
  );

  await windowManager.waitUntilReadyToShow(windowOptions, () async {
    await windowManager.show();
    await windowManager.focus();
    if (settings.windowMaximized) {
      await windowManager.maximize();
    } else {
      await windowManager.setFullScreen(false); // Ensure it's not stuck in full screen
    }
  });

  runApp(
    MultiProvider(
      providers: [
        ChangeNotifierProvider.value(value: settings),
        ChangeNotifierProvider.value(value: backendService),
        Provider.value(value: processService),
      ],
      child: const OdysseusApp(),
    ),
  );
}

class OdysseusApp extends StatefulWidget {
  const OdysseusApp({super.key});

  @override
  State<OdysseusApp> createState() => _OdysseusAppState();
}

class _OdysseusAppState extends State<OdysseusApp> with WindowListener {
  @override
  void initState() {
    super.initState();
    windowManager.addListener(this);
  }

  @override
  void dispose() {
    windowManager.removeListener(this);
    super.dispose();
  }

  @override
  void onWindowResize() async {
    final settings = context.read<SettingsService>();
    final size = await windowManager.getSize();
    settings.windowWidth = size.width;
    settings.windowHeight = size.height;
  }

  @override
  void onWindowMaximize() {
    context.read<SettingsService>().windowMaximized = true;
  }

  @override
  void onWindowRestore() {
    context.read<SettingsService>().windowMaximized = false;
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: AppConstants.appTitle,
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: Colors.blue),
        useMaterial3: true,
      ),
      home: const SplashScreen(),
    );
  }
}
