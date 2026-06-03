import 'package:http/http.dart' as http;
import 'package:odysseus_desktop/core/logger.dart';

class HealthCheckService {
  Future<bool> check(String url) async {
    try {
      final response = await http.get(Uri.parse(url)).timeout(const Duration(seconds: 2));
      if (response.statusCode == 200) {
        return true;
      } else {
        AppLogger.d('Health check failed for $url: Status Code ${response.statusCode}');
        return false;
      }
    } catch (e) {
      AppLogger.d('Health check failed for $url: Connection error: $e');
      return false;
    }
  }

  Future<bool> waitForBackend(String url, int timeoutSeconds) async {
    final startTime = DateTime.now();
    while (DateTime.now().difference(startTime).inSeconds < timeoutSeconds) {
      if (await check(url)) {
        return true;
      }
      await Future.delayed(const Duration(seconds: 1));
    }
    return false;
  }
}
