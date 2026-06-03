import 'dart:async';
import 'dart:io';
import 'package:flutter/foundation.dart';
import 'package:odysseus_desktop/core/app_config.dart';
import 'package:odysseus_desktop/core/logger.dart';
import 'package:odysseus_desktop/services/health_check_service.dart';
import 'package:odysseus_desktop/services/process_service.dart';
import 'package:odysseus_desktop/services/settings_service.dart';
import 'package:odysseus_desktop/services/reset_service.dart';

enum BackendStatus {
  idle,
  checking,
  starting,
  ready,
  error,
  resetting,
}

class BackendService extends ChangeNotifier {
  final SettingsService _settings;
  final ProcessService _processService;
  final HealthCheckService _healthCheckService;
  final ResetService _resetService;

  BackendStatus _status = BackendStatus.idle;
  String? _lastError;
  bool _spawnedBackend = false;
  String? _extractedPassword;
  StreamSubscription<String>? _stdoutSubscription;

  BackendStatus get status => _status;
  String? get lastError => _lastError;
  String? get extractedPassword => _extractedPassword;

  BackendService(this._settings, this._processService, this._healthCheckService, this._resetService);

  void _updateStatus(BackendStatus newStatus) {
    _status = newStatus;
    notifyListeners();
  }

  void _setError(String error) {
    _lastError = error;
    _status = BackendStatus.error;
    notifyListeners();
  }

  Future<void> init() async {
    _updateStatus(BackendStatus.checking);
    AppLogger.i('Checking if backend is already running at ${_settings.backendUrl}');
    
    if (await _healthCheckService.check(_settings.backendUrl)) {
      AppLogger.i('Backend already running.');
      _updateStatus(BackendStatus.ready);
      return;
    }

    if (!_settings.autoStartBackend) {
      AppLogger.w('Backend not running and auto-start is disabled.');
      _setError('Backend not running and auto-start is disabled.');
      return;
    }

    await startBackend();
  }

  Future<void> resetAndRestart() async {
    _updateStatus(BackendStatus.resetting);
    await _processService.stop();
    await _resetService.resetAllRuntimeData();
    await startBackend();
  }

  Future<void> startBackend({bool isRetry = false}) async {
    _updateStatus(BackendStatus.starting);
    try {
      final workingDir = await AppConfig.projectRoot;
      Map<String, String> env = Map.from(Platform.environment);
      if (_settings.adminUser != null && _settings.adminUser!.isNotEmpty) {
        env['ODYSSEUS_ADMIN_USER'] = _settings.adminUser!;
      }
      if (_settings.adminPassword != null && _settings.adminPassword!.isNotEmpty) {
        env['ODYSSEUS_ADMIN_PASSWORD'] = _settings.adminPassword!;
      }
      
      _stdoutSubscription = _processService.stdout.listen((outputLine) {
        if (outputLine.toLowerCase().contains("temporary password:")) {
          final RegExp regExp = RegExp(r"Temporary password:\s*(\S+)", caseSensitive: false);
          final match = regExp.firstMatch(outputLine);
          if (match != null) {
            _extractedPassword = match.group(1);
            AppLogger.i('Securely intercepted temporary setup password: $_extractedPassword');
            notifyListeners();
          }
        }
      });
      await _processService.start(
        _settings.launchCommand, 
        workingDir, 
        environment: env,
        onExit: (int code) {
          _stdoutSubscription?.cancel();
          AppLogger.e('Backend process exited unexpectedly (code: $code)');
          if (!isRetry) {
            AppLogger.i('Attempting automatic restart...');
            startBackend(isRetry: true);
          } else {
            _setError('Backend crashed repeatedly.');
          }
        }
      );
      _spawnedBackend = true;

      AppLogger.i('Waiting for backend to become healthy...');
      bool ready = await _healthCheckService.waitForBackend(_settings.backendUrl, _settings.startupTimeout);
      
      if (ready) {
        _stdoutSubscription?.cancel();
        AppLogger.i('Backend is ready.');
        _updateStatus(BackendStatus.ready);
      } else {
        _stdoutSubscription?.cancel();
        AppLogger.e('Backend failed to start within timeout.');
        _setError('Backend failed to start within ${_settings.startupTimeout} seconds.');
        await _processService.stop();
      }
    } catch (e) {
      _stdoutSubscription?.cancel();
      AppLogger.e('Error starting backend', e);
      _setError('Error starting backend: $e');
    }
  }

  Future<void> disposeBackend() async {
    if (_spawnedBackend) {
      await _processService.stop();
    }
  }
}
