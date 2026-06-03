import 'dart:io';
import 'package:path/path.dart' as p;
import 'package:odysseus_desktop/core/app_config.dart';
import 'package:odysseus_desktop/core/logger.dart';

class ResetService {
  Future<void> resetAllRuntimeData() async {
    AppLogger.w('Initiating system reset of all runtime data...');

    try {
      final backendRoot = await AppConfig.projectRoot;
      final dataDir = p.join(backendRoot, 'data');
      
      // List of folders/files to safely remove
      final targets = [
        dataDir,
        // Add vector store paths if they are separate
        p.join(backendRoot, 'chroma'),
      ];

      for (var target in targets) {
        final dir = Directory(target);
        if (await dir.exists()) {
          AppLogger.i('Removing runtime data: $target');
          await dir.delete(recursive: true);
        }
      }

      // Also clear WebView data
      final webViewData = await AppConfig.userDataDir;
      final webViewDir = Directory(webViewData);
      if (await webViewDir.exists()) {
        AppLogger.i('Removing WebView data: $webViewData');
        await webViewDir.delete(recursive: true);
      }

      AppLogger.i('System reset complete.');
    } catch (e, stack) {
      AppLogger.e('Error during system reset', e, stack);
      rethrow;
    }
  }
}
