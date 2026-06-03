# Odysseus Desktop

A Flutter-based desktop wrapper for the [Odysseus](https://github.com/pewdiepie-archdaemon/odysseus) AI workspace.

## Features

- **Automated Startup:** Automatically launches the Odysseus backend if not already running.
- **Embedded Web UI:** Displays the full Odysseus web interface in a native desktop window.
- **Window Management:** Remembers your preferred window size, position, and maximized state.
- **Native Experience:** Better integration with your desktop environment.
- **Configurable:** Easily change the backend URL, launch commands, and startup timeouts.

## Repository Structure

For development, ensure `odysseus` (backend) and `odysseus_desktop` (frontend) are placed as parallel sibling directories within your master workspace directory:

```text
/my-workspace/
├── odysseus/         (Backend & Core)
└── odysseus_desktop/ (Flutter Frontend)
```

## Prerequisites

- [Flutter SDK](https://docs.flutter.dev/get-started/install)
- [FVM (Flutter Version Management)](https://fvm.app/)
- [Python 3.11+](https://www.python.org/downloads/) (for the backend)

## Getting Started

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/pewdiepie-archdaemon/odysseus.git
    cd odysseus/odysseus_desktop
    ```

2.  **Install dependencies:**
    ```bash
    fvm flutter pub get
    ```

3.  **Run the application:**
    ```bash
    fvm flutter run -d windows
    ```

## Configuration

Settings can be managed directly within the application's Settings page:

- **Backend URL:** The local address where Odysseus is running (default: `http://localhost:7000`).
- **Auto-start Backend:** Toggle whether the launcher should attempt to start the backend.
- **Launch Command:** The command used to start the backend, platform-dependent.
- **Startup Timeout:** How long to wait for the backend to become healthy.

## Logging

Logs are stored in your application documents directory:
- `Odysseus/logs/launcher.log`: Logs related to the desktop wrapper.
- `Odysseus/logs/backend.log`: Captured stdout/stderr from the backend process.

## Building for Production

### Windows
```bash
fvm flutter build windows
```

### Linux
```bash
fvm flutter build linux
```

### macOS
```bash
fvm flutter build macos
```
