import 'dart:io';
import 'package:flutter/foundation.dart';
import 'package:odysseus_desktop/core/constants.dart';
import 'package:odysseus_desktop/services/storage_service.dart';

class SettingsService extends ChangeNotifier {
  final StorageService _storage;

  SettingsService(this._storage);

  String get backendUrl => _storage.getString(AppConstants.keyBackendUrl) ?? AppConstants.defaultBackendUrl;
  set backendUrl(String value) {
    _storage.setString(AppConstants.keyBackendUrl, value);
    notifyListeners();
  }

  bool get autoStartBackend => _storage.getBool(AppConstants.keyAutoStartBackend) ?? true;
  set autoStartBackend(bool value) {
    _storage.setBool(AppConstants.keyAutoStartBackend, value);
    notifyListeners();
  }

  String get launchCommand {
    String? cmd = _storage.getString(AppConstants.keyLaunchCommand);
    if (cmd != null) return cmd;

    if (Platform.isWindows) {
      return 'powershell -ExecutionPolicy Bypass -File .\\launch-windows.ps1';
    } else if (Platform.isMacOS) {
      return './start-macos.sh';
    } else {
      return 'python -m uvicorn app:app --host 127.0.0.1 --port 7000';
    }
  }
  set launchCommand(String value) {
    _storage.setString(AppConstants.keyLaunchCommand, value);
    notifyListeners();
  }

  int get startupTimeout => _storage.getInt(AppConstants.keyStartupTimeout) ?? AppConstants.defaultStartupTimeoutSeconds;
  set startupTimeout(int value) {
    _storage.setInt(AppConstants.keyStartupTimeout, value);
    notifyListeners();
  }

  bool get loggingEnabled => _storage.getBool(AppConstants.keyLoggingEnabled) ?? true;
  set loggingEnabled(bool value) {
    _storage.setBool(AppConstants.keyLoggingEnabled, value);
    notifyListeners();
  }
  
  double get windowWidth => _storage.getDouble(AppConstants.keyWindowWidth) ?? AppConstants.defaultWindowWidth;
  set windowWidth(double value) {
    _storage.setDouble(AppConstants.keyWindowWidth, value);
    notifyListeners();
  }

  double get windowHeight => _storage.getDouble(AppConstants.keyWindowHeight) ?? AppConstants.defaultWindowHeight;
  set windowHeight(double value) {
    _storage.setDouble(AppConstants.keyWindowHeight, value);
    notifyListeners();
  }

  bool get windowMaximized => _storage.getBool(AppConstants.keyWindowMaximized) ?? false;
  set windowMaximized(bool value) {
    _storage.setBool(AppConstants.keyWindowMaximized, value);
    notifyListeners();
  }

  String? get adminUser => _storage.getString(AppConstants.keyAdminUser);
  set adminUser(String? value) {
    if (value != null) {
      _storage.setString(AppConstants.keyAdminUser, value);
    } else {
      _storage.remove(AppConstants.keyAdminUser);
    }
    notifyListeners();
  }

  String? get adminPassword => _storage.getString(AppConstants.keyAdminPassword);
  set adminPassword(String? value) {
    if (value != null) {
      _storage.setString(AppConstants.keyAdminPassword, value);
    } else {
      _storage.remove(AppConstants.keyAdminPassword);
    }
    notifyListeners();
  }
}
