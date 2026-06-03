import 'dart:io';
import 'package:logger/logger.dart';
import 'package:odysseus_desktop/core/app_config.dart';
import 'package:path/path.dart' as p;

class AppLogger {
  static late Logger _logger;
  static File? _logFile;

  static Future<void> init() async {
    final logDirPath = await AppConfig.logDir;
    _logFile = File(p.join(logDirPath, 'launcher.log'));

    _logger = Logger(
      printer: PrettyPrinter(
        methodCount: 0,
        errorMethodCount: 5,
        lineLength: 80,
        colors: true,
        printEmojis: true,
        dateTimeFormat: DateTimeFormat.onlyTimeAndSinceStart,
      ),
      output: MultiOutput([
        ConsoleOutput(),
        if (_logFile != null) FileOutput(file: _logFile!),
      ]),
    );
  }

  static void i(String message) => _logger.i(message);
  static void e(String message, [dynamic error, StackTrace? stackTrace]) => _logger.e(message, error: error, stackTrace: stackTrace);
  static void w(String message) => _logger.w(message);
  static void d(String message) => _logger.d(message);
}

class FileOutput extends LogOutput {
  final File file;
  FileOutput({required this.file});

  @override
  void output(OutputEvent event) {
    for (var line in event.lines) {
      file.writeAsStringSync('$line\n', mode: FileMode.append);
    }
  }
}
