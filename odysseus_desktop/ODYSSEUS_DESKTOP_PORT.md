# Odysseus Desktop (Flutter + FVM) Engineering Specification

## Overview

Create a Flutter Desktop application that wraps the existing Odysseus project in a native desktop application.

This project is NOT a rewrite of Odysseus.

This project is NOT a replacement frontend.

The goal is to provide a desktop launcher and native desktop experience while preserving 100% of Odysseus functionality and maintaining compatibility with future upstream updates.

The desktop application should launch the existing Odysseus backend, wait for it to become available, and display the existing web interface inside a native Flutter Desktop window.

The implementation should be suitable for submitting as an upstream contribution or maintaining as a clean fork.

---

# Existing Environment

Development machine:

* Windows 11
* Flutter managed through FVM
* Git
* VS Code

Flutter requirements:

* Must support Windows Desktop
* Must support Linux Desktop
* Must compile for macOS

Use FVM for all Flutter commands.

Examples:

```bash
fvm flutter pub get
fvm flutter run -d windows
fvm flutter build windows
```

Do not assume global Flutter installation.

---

# Primary Objectives

Implement a desktop wrapper that:

1. Launches Odysseus backend if not already running.
2. Detects existing backend instances.
3. Displays Odysseus in a native desktop window.
4. Gracefully handles startup and shutdown.
5. Stores user settings.
6. Maintains compatibility with upstream Odysseus updates.
7. Requires minimal modifications to Odysseus source code.

---

# Non-Goals

Do NOT:

* Rewrite frontend
* Convert frontend to Flutter
* Modify AI functionality
* Change backend architecture
* Replace FastAPI
* Bundle models
* Bundle LLM runtimes
* Add new Odysseus features

The desktop wrapper exists solely to improve usability.

---

# Architecture

```text
┌───────────────────────────┐
│ Flutter Desktop App       │
├───────────────────────────┤
│ Splash Screen             │
│ Startup Logic             │
│ Settings                  │
│ Logging                   │
│ Native Window             │
│ Embedded WebView          │
└─────────────┬─────────────┘
              │
              ▼
┌───────────────────────────┐
│ Odysseus Backend          │
├───────────────────────────┤
│ FastAPI                   │
│ Existing Frontend         │
│ Existing APIs             │
└───────────────────────────┘
```

---

# Repository Structure

Create a dedicated Flutter desktop wrapper project.

```text
odysseus_desktop/
│
├── pubspec.yaml
├── README.md
├── analysis_options.yaml
│
├── assets/
│
├── lib/
│   ├── main.dart
│   │
│   ├── core/
│   │   ├── constants.dart
│   │   ├── app_config.dart
│   │   └── logger.dart
│   │
│   ├── services/
│   │   ├── backend_service.dart
│   │   ├── process_service.dart
│   │   ├── settings_service.dart
│   │   ├── health_check_service.dart
│   │   └── storage_service.dart
│   │
│   ├── screens/
│   │   ├── splash_screen.dart
│   │   ├── webview_screen.dart
│   │   ├── settings_screen.dart
│   │   └── error_screen.dart
│   │
│   └── widgets/
│
├── windows/
├── linux/
└── macos/
```

---

# Required Packages

Use stable maintained packages only.

Recommended:

```yaml
dependencies:
  flutter:
    sdk: flutter

  webview_flutter:
  webview_flutter_windows:
  window_manager:
  process_run:
  path_provider:
  shared_preferences:
  http:
  logger:
```

Avoid abandoned packages.

---

# Backend Detection

Default backend URL:

```text
http://localhost:7000
```

At startup:

1. Attempt connection.
2. If healthy:

   * Do not launch another instance.
3. If unavailable:

   * Launch backend process.

Health endpoint may be:

```text
/
```

or

```text
/api/health
```

depending on Odysseus implementation.

Detect automatically.

---

# Backend Startup

Backend startup logic must be configurable.

Store launch commands in settings.

Default examples:

Windows:

```powershell
powershell -ExecutionPolicy Bypass -File .\launch-windows.ps1
```

Linux:

```bash
python -m uvicorn app:app --host 0.0.0.0 --port 7000
```

macOS:

```bash
python -m uvicorn app:app --host 0.0.0.0 --port 7000
```

Implementation requirements:

* Capture stdout
* Capture stderr
* Write logs
* Detect startup failures
* Detect missing Python
* Detect invalid commands

---

# Startup Flow

Application launch:

```text
Show Splash Screen
        ↓
Load Settings
        ↓
Check Backend
        ↓
Backend Running?
        ↓
   YES ─────► Open WebView
        │
        NO
        ↓
Launch Backend
        ↓
Wait For Health Check
        ↓
Success?
        ↓
YES ─────► Open WebView
        │
        NO
        ↓
Show Error Screen
```

---

# Health Checking

Requirements:

* Poll every 1 second.
* Maximum wait:
  60 seconds.

Success criteria:

* HTTP 200
* Web interface reachable

Failure criteria:

* Timeout
* Process crash
* Connection refused

---

# WebView Requirements

Use native platform WebView.

Load:

```text
http://localhost:7000
```

Must support:

* File upload
* Clipboard
* Keyboard shortcuts
* Drag and drop when supported
* External links

Do not rewrite frontend into Flutter widgets.

---

# Window Management

Default size:

```text
1280x800
```

Minimum size:

```text
1000x700
```

Remember:

* Last size
* Last position
* Maximized state

Use window_manager package.

---

# Settings Page

Implement settings UI.

Configurable:

## Backend URL

Default:

```text
http://localhost:7000
```

## Auto Start Backend

```text
true
```

## Launch Command

Editable per platform.

## Startup Timeout

Default:

```text
60
```

seconds.

## Logging Enabled

Default:

```text
true
```

---

# Logging

Create:

```text
logs/
```

Store:

```text
backend.log
launcher.log
```

Requirements:

* Timestamp entries
* Rotate if excessively large
* Display latest logs in Settings page

---

# Error Handling

Create dedicated error screens.

Scenarios:

## Backend Startup Failed

Show:

* command used
* error message
* retry button

## Python Not Found

Show:

* installation instructions

## Port Conflict

Show:

* detected port
* process information if available

## Timeout

Show:

* startup duration
* logs

---

# Shutdown Behavior

If desktop wrapper started backend:

* terminate backend gracefully on exit.

If backend already existed before launch:

* leave backend running.

Track ownership of spawned process.

---

# FVM Requirements

Project must contain:

```text
.fvm/
.fvmrc
```

Document FVM usage.

All documentation must use:

```bash
fvm flutter ...
```

Never use:

```bash
flutter ...
```

---

# Code Quality Requirements

Must:

* use null safety
* pass flutter analyze
* follow Flutter lints
* avoid deprecated APIs
* use dependency injection where appropriate
* separate UI and business logic
* include comments only where useful

Avoid:

* giant files
* god classes
* hardcoded paths

---

# Future Extension Points

Create architecture that allows future additions.

Do not implement them.

Potential future features:

* System Tray
* Auto Update
* Portable Mode
* Bundled Python Runtime
* Bundled Backend
* Multiple Profiles
* Deep Linking
* Notifications

Only leave extension points.

---

# Documentation

Create:

README.md

Include:

## Installation

```bash
git clone <repo>
cd odysseus_desktop
fvm flutter pub get
```

## Run

```bash
fvm flutter run -d windows
```

## Build

Windows:

```bash
fvm flutter build windows
```

Linux:

```bash
fvm flutter build linux
```

macOS:

```bash
fvm flutter build macos
```

## Configuration

Explain:

* backend URL
* launch command
* logs
* startup behavior

---

# Deliverables

Generate all required files.

The resulting project should:

* compile successfully
* run successfully
* launch Odysseus automatically
* display Odysseus inside a desktop window
* require minimal manual modification

Provide complete source code, not pseudocode.

Generate every required file and implementation needed to build the project immediately.
