import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:odysseus_desktop/core/logger.dart';
import 'package:path/path.dart' as p;
import 'package:odysseus_desktop/core/app_config.dart';

class ProcessService {
  Process? _process;
  final _stdoutController = StreamController<String>.broadcast();
  final _stderrController = StreamController<String>.broadcast();
  File? _backendLogFile;

  Stream<String> get stdout => _stdoutController.stream;
  Stream<String> get stderr => _stderrController.stream;

  Future<void> start(String command, String workingDir, {required Function onExit, Map<String, String>? environment}) async {
    final logDirPath = await AppConfig.logDir;
    _backendLogFile = File(p.join(logDirPath, 'backend.log'));
    
    // Clear log on start
    try {
      if (await _backendLogFile!.exists()) {
        await _backendLogFile!.writeAsString('');
      } else {
        await _backendLogFile!.create(recursive: true);
      }
    } catch (e) {
      AppLogger.e('Failed to initialize backend log file', e);
    }

    AppLogger.i('Starting backend detached with command: $command in $workingDir');

    // Split command properly
    List<String> parts = _splitCommand(command);
    if (parts.isEmpty) {
      throw Exception('Empty launch command');
    }
    
    String executable = parts[0];
    if (Platform.isWindows && executable.toLowerCase() == 'powershell') {
      executable = 'powershell.exe';
    }
    List<String> arguments = parts.sublist(1);

    try {
      AppLogger.i('Executing: $executable ${arguments.join(' ')}');
      
      // Use detached mode for backend
      _process = await Process.start(
        executable,
        arguments,
        workingDirectory: workingDir,
        runInShell: true,
        mode: ProcessStartMode.normal,
        environment: environment,
      );

      _process!.stdout.transform(utf8.decoder).listen((data) {
        _stdoutController.add(data);
        _logToFile(data);
      });

      _process!.stderr.transform(utf8.decoder).listen((data) {
        _stderrController.add(data);
        _logToFile(data, isError: true);
      });

      _process!.exitCode.then((code) {
        AppLogger.i('Backend process exited with code: $code');
        _process = null;
        onExit(code);
      });
    } catch (e) {
      AppLogger.e('Failed to start backend process', e);
      rethrow;
    }
  }

  List<String> _splitCommand(String command) {
    var parts = <String>[];
    var currentPart = StringBuffer();
    var inQuotes = false;
    for (var i = 0; i < command.length; i++) {
      var char = command[i];
      if (char == '"' || char == "'") {
        inQuotes = !inQuotes;
      } else if (char == ' ' && !inQuotes) {
        if (currentPart.isNotEmpty) {
          parts.add(currentPart.toString());
          currentPart.clear();
        }
      } else {
        currentPart.write(char);
      }
    }
    if (currentPart.isNotEmpty) {
      parts.add(currentPart.toString());
    }
    return parts;
  }

  void _logToFile(String data, {bool isError = false}) {
    if (_backendLogFile != null) {
      try {
        final timestamp = DateTime.now().toIso8601String();
        final prefix = isError ? '[ERROR]' : '[INFO]';
        _backendLogFile!.writeAsStringSync('[$timestamp] $prefix $data', mode: FileMode.append);
      } catch (e) {
        // Ignore logging errors
      }
    }
  }

  Future<void> stop() async {
    if (_process != null) {
      AppLogger.i('Stopping backend process...');
      _process!.kill(ProcessSignal.sigterm);
      // Wait a bit for graceful shutdown, then kill if still alive
      await Future.delayed(const Duration(seconds: 2));
      if (_process != null) {
        _process!.kill(ProcessSignal.sigkill);
      }
      _process = null;
    }
  }

  bool get isRunning => _process != null;
}
