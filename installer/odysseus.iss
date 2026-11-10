; Odysseus Windows installer (Inno Setup 6).
; Built by build-windows-installer.ps1, which passes /DAppVersion=<version>.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "Odysseus"
#define AppExeName "Odysseus.exe"

[Setup]
AppId={{9563D4C3-66CA-48F5-AFD0-5DE4F006C2A2}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppName}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
LicenseFile=..\LICENSE
SetupIconFile=..\static\icon.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
OutputDir=..\dist\installer
OutputBaseFilename={#AppName}-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; force: the app has no main window (tray icon only), so a polite close request cannot reach it.
CloseApplications=force
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: checkedonce

[Files]
Source: "..\dist\Odysseus\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\static\icon.ico"; DestDir: "{app}"; DestName: "Odysseus.ico"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\Odysseus.ico"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\Odysseus.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Start Odysseus"; Flags: postinstall nowait skipifsilent

[UninstallRun]
; Stop this copy of the app and its helper processes so their files can be removed.
; Only processes started from the install folder (the working directory here) are stopped.
; The uninstaller itself also runs from that folder and is left alone.
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -NonInteractive -Command ""$d = (Get-Location).ProviderPath + '\'; Get-Process | Where-Object {{ $_.Path -and $_.Path.StartsWith($d, [StringComparison]::OrdinalIgnoreCase) -and $_.Name -notlike 'unins*' } | Stop-Process -Force -PassThru | Wait-Process -Timeout 15"""; WorkingDir: "{app}"; Flags: runhidden skipifdoesntexist; RunOnceId: "StopOdysseus"

[UninstallDelete]
; User data lives in %USERPROFILE%\.odysseus\data, outside the install folder, and is never removed.
; These entries only remove the empty folders the app creates next to itself
; (user-added fonts, browser helper cache). A folder that holds files stays.
Type: dirifempty; Name: "{app}\static\fonts\custom"
Type: dirifempty; Name: "{app}\static\fonts"
Type: dirifempty; Name: "{app}\static"
Type: dirifempty; Name: "{app}\_internal\data\local\playwright-mcp-cache"
Type: dirifempty; Name: "{app}\_internal\data\local"
Type: dirifempty; Name: "{app}\_internal\data"
; The install folder itself is only recorded for removal when Setup created it.
; These two cover a folder that was already there, for example left by an earlier uninstall.
Type: dirifempty; Name: "{app}\_internal"
Type: dirifempty; Name: "{app}"
