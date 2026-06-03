import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:odysseus_desktop/services/settings_service.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  late TextEditingController _urlController;
  late TextEditingController _cmdController;
  late TextEditingController _timeoutController;
  late TextEditingController _userController;
  late TextEditingController _passController;

  @override
  void initState() {
    super.initState();
    final settings = context.read<SettingsService>();
    _urlController = TextEditingController(text: settings.backendUrl);
    _cmdController = TextEditingController(text: settings.launchCommand);
    _timeoutController = TextEditingController(text: settings.startupTimeout.toString());
    _userController = TextEditingController(text: settings.adminUser);
    _passController = TextEditingController(text: settings.adminPassword);
  }

  @override
  void dispose() {
    _urlController.dispose();
    _cmdController.dispose();
    _timeoutController.dispose();
    _userController.dispose();
    _passController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final settings = context.watch<SettingsService>();

    return Scaffold(
      appBar: AppBar(title: const Text('Settings')),
      body: ListView(
        padding: const EdgeInsets.all(16.0),
        children: [
          TextField(
            controller: _urlController,
            decoration: const InputDecoration(labelText: 'Backend URL'),
            onChanged: (value) => settings.backendUrl = value,
          ),
          const SizedBox(height: 16),
          SwitchListTile(
            title: const Text('Auto-start Backend'),
            value: settings.autoStartBackend,
            onChanged: (value) => setState(() => settings.autoStartBackend = value),
          ),
          const SizedBox(height: 16),
          TextField(
            controller: _cmdController,
            decoration: const InputDecoration(labelText: 'Launch Command'),
            onChanged: (value) => settings.launchCommand = value,
          ),
          const SizedBox(height: 16),
          TextField(
            controller: _timeoutController,
            decoration: const InputDecoration(labelText: 'Startup Timeout (seconds)'),
            keyboardType: TextInputType.number,
            onChanged: (value) {
              final val = int.tryParse(value);
              if (val != null) settings.startupTimeout = val;
            },
          ),
          const SizedBox(height: 16),
          TextField(
            controller: _userController,
            decoration: const InputDecoration(labelText: 'Admin Username (optional)'),
            onChanged: (value) => settings.adminUser = value,
          ),
          const SizedBox(height: 16),
          TextField(
            controller: _passController,
            decoration: const InputDecoration(labelText: 'Admin Password (optional)'),
            obscureText: true,
            onChanged: (value) => settings.adminPassword = value,
          ),
          const SizedBox(height: 16),
          SwitchListTile(
            title: const Text('Enable Logging'),
            value: settings.loggingEnabled,
            onChanged: (value) => setState(() => settings.loggingEnabled = value),
          ),
        ],
      ),
    );
  }
}
