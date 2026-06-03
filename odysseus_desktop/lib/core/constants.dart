class AppConstants {
  static const String appTitle = 'Odysseus';
  static const String defaultBackendUrl = 'http://localhost:7000';
  static const int defaultStartupTimeoutSeconds = 60;
  
  static const double minWindowWidth = 1000;
  static const double minWindowHeight = 700;
  static const double defaultWindowWidth = 1280;
  static const double defaultWindowHeight = 800;

  // Settings Keys
  static const String keyBackendUrl = 'backend_url';
  static const String keyAutoStartBackend = 'auto_start_backend';
  static const String keyLaunchCommand = 'launch_command';
  static const String keyStartupTimeout = 'startup_timeout';
  static const String keyLoggingEnabled = 'logging_enabled';
  static const String keyWindowWidth = 'window_width';
  static const String keyWindowHeight = 'window_height';
  static const String keyWindowMaximized = 'window_maximized';
  static const String keyAdminUser = 'admin_user';
  static const String keyAdminPassword = 'admin_password';
}
