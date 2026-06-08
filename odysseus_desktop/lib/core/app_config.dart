import 'dart:io';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';

class AppConfig {
  static Future<String> get projectRoot async {
    // Assuming the desktop app is in a folder sibling to the backend or inside it.
    // In our structure: 
    // Agent/
    // ├── odysseus/
    // └── odysseus_desktop/
    
    // For development, we can try to find the backend relative to the executable
    // or use a fixed path if we know the structure.
    
    // On Windows development:
    // C:\path\to\project\odysseus_desktop
    
    // We want: C:\path\to\project\odysseus
    
    Directory current = Directory.current;
    if (current.path.endsWith('odysseus_desktop')) {
      return p.join(current.parent.path, 'odysseus');
    }
    
    // Fallback or production logic would go here
    return p.join(current.path, 'odysseus');
  }

  static Future<String> get logDir async {
    final docDir = await getApplicationDocumentsDirectory();
    final path = p.join(docDir.path, 'Odysseus', 'logs');
    final dir = Directory(path);
    if (!await dir.exists()) {
      await dir.create(recursive: true);
    }
    return path;
  }

  static Future<String> get userDataDir async {
    String? localAppData = Platform.environment['LOCALAPPDATA'];
    late String path;
    if (localAppData != null) {
      path = p.join(localAppData, 'Odysseus', 'webview_data');
    } else {
      // Fallback to support directory
      final supportDir = await getApplicationSupportDirectory();
      path = p.join(supportDir.path, 'Odysseus', 'webview_data');
    }
    
    final dir = Directory(path);
    if (!await dir.exists()) {
      await dir.create(recursive: true);
    }
    return path;
  }
}
