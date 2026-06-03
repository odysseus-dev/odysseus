# Odysseus Desktop Modifications

This document outlines the recent modifications made to the `odysseus_desktop` application.

## Summary of Changes

1.  **Authentication Workflow**:
    *   Removed credential interception logic.
    *   Users can now configure their admin username and password directly within the **Settings** screen.
    *   The application passes these as environment variables (`ODYSSEUS_ADMIN_USER`, `ODYSSEUS_ADMIN_PASSWORD`) to the backend, enabling the backend to automatically create a secure admin account via `bcrypt` hashing on first run.

2.  **UI/UX Improvements**:
    *   Removed the top `AppBar` in the `WebViewScreen` for a cleaner, immersive experience.
    *   Improved window management to better respect the maximized state on launch.

3.  **Process Management**:
    *   Updated `ProcessService` to correctly pass environment variables to the backend process.
    *   Changed process start mode to `ProcessStartMode.normal` for better lifecycle management.

## Building the Executable (.exe)

To build the executable for Windows, run the following command in the `odysseus_desktop` directory:

```bash
fvm flutter build windows
```

The resulting executable will be located in:
`odysseus_desktop\build\windows\x64\runner\Release\odysseus_desktop.exe`

## Security Note for Contributors

Ensure that you do not commit any personal credentials or data to the repository.
- **Do not commit** `auth.json` from the backend data directory.
- **Do not commit** any local `.env` files containing actual API keys or credentials.
- Ensure your `Settings` in the desktop app do not contain your personal password if you intend to push the app settings file (if any).
