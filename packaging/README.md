# Odysseus Installer Packaging

This directory contains unsigned development packaging for Odysseus.

## Outputs

- Windows MSI: `dist/windows/Odysseus.msi`
- Windows bootstrapper EXE: `dist/windows/OdysseusSetup.exe`
- macOS DMG: `dist/macos/Odysseus.dmg`

## Windows

Windows packaging uses the Docker-backed runtime for feature parity with the
normal Compose setup. The MSI installs the app payload under Program Files and
shortcuts under the Start Menu. Runtime data is created under:

```text
%LOCALAPPDATA%\Odysseus
```

Build prerequisites:

- Windows
- WiX Toolset CLI v4 or newer on `PATH`
- .NET SDK if installing WiX via `dotnet tool`

Suggested WiX install for development:

```powershell
dotnet tool install --global wix
wix extension add WixToolset.BootstrapperApplications.wixext
```

Build commands:

```powershell
powershell -ExecutionPolicy Bypass -File .\packaging\windows\build-msi.ps1
powershell -ExecutionPolicy Bypass -File .\packaging\windows\build-bundle.ps1
```

WiX v7 requires explicit EULA acceptance. If you are eligible to accept it, pass:

```powershell
powershell -ExecutionPolicy Bypass -File .\packaging\windows\build-msi.ps1 -AcceptWixEula
powershell -ExecutionPolicy Bypass -File .\packaging\windows\build-bundle.ps1 -AcceptWixEula
```

The bootstrapper attempts to install Docker Desktop through `winget` when Docker
is missing. If Docker Desktop requires a reboot or manual setup, the bootstrapper
stops and the Start Menu shortcut will show a clear Docker prerequisite message.

## macOS

macOS packaging builds a native launcher DMG. On first launch, the app copies the
bundled payload into:

```text
~/Library/Application Support/Odysseus
```

Then it creates a venv, installs `requirements.txt`, runs `setup.py`, starts
uvicorn, and opens the UI. This follows the existing native macOS approach so
local Mac GPU workflows are not hidden behind a Docker VM.

Build command on macOS:

```bash
./packaging/macos/build-dmg.sh
```

## Signing

These artifacts are unsigned development builds. Windows SmartScreen and macOS
Gatekeeper warnings are expected. Production releases should add Authenticode
signing for MSI/EXE and Apple Developer ID signing plus notarization for DMG.
