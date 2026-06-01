; Inno Setup Script for Odysseus AI Workspace
; This script packages a portable Python environment, the Odysseus daemon, and the codebase.

[Setup]
AppName=Odysseus AI Workspace
AppVersion=1.0
DefaultDirName={commonpf}\Odysseus
DefaultGroupName=Odysseus AI Workspace
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\python\python.exe
Compression=lzma2
SolidCompression=yes
OutputDir=Output
OutputBaseFilename=OdysseusSetup
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=admin
SetupLogging=yes

[Files]
; Portable Python environment. Assumes portable Python is in the 'python' directory at the root of the source directory.
; Sourced from two directories up (project root) relative to the location of this .iss script.
Source: "..\..\python\*"; DestDir: "{app}\python"; Flags: recursesubdirs createallsubdirs ignoreversion; Check: DirExists(ExpandConstant('{src}\python')) or DirExists(ExpandConstant('{src}\..\..\python'))

; Odysseus repository/codebase files, excluding developer/junk directories.
Source: "..\..\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion; Excludes: ".git\*,.pytest_cache\*,venv\*,node_modules\*,__pycache__\*,*.pid,*.db,*.log,local_config.json,scripts\windows\Output\*"

[INI]
; Create standard Windows Web URL shortcut to open the Odysseus console
Filename: "{app}\odysseus.url"; Section: "InternetShortcut"; Key: "URL"; String: "http://localhost:8000"

[Icons]
; Startup and control shortcuts
Name: "{group}\Start Odysseus"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\scripts\windows\odysseus_daemon.py"" start"; WorkingDir: "{app}"
Name: "{group}\Stop Odysseus"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\scripts\windows\odysseus_daemon.py"" stop"; WorkingDir: "{app}"
Name: "{group}\Odysseus Web Console"; Filename: "{app}\odysseus.url"
Name: "{group}\Uninstall Odysseus"; Filename: "{uninstallexe}"
Name: "{commondesktop}\Odysseus Web Console"; Filename: "{app}\odysseus.url"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Run]
; Install Python packages into the portable environment.
Filename: "{app}\python\python.exe"; Parameters: "-m pip install --upgrade pip"; StatusMsg: "Upgrading pip..."; Flags: runhidden; Check: FileExists(ExpandConstant('{app}\python\python.exe'))
Filename: "{app}\python\python.exe"; Parameters: "-m pip install -r requirements.txt"; StatusMsg: "Installing dependencies (this may take a few minutes)..."; Flags: runhidden; Check: FileExists(ExpandConstant('{app}\python\python.exe'))

; Automatically start the background daemon at the end of installation
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\scripts\windows\odysseus_daemon.py"" start"; StatusMsg: "Starting Odysseus background service..."; Flags: runhidden; Check: FileExists(ExpandConstant('{app}\python\pythonw.exe'))

[UninstallRun]
; Stop the background daemon when uninstalling
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\scripts\windows\odysseus_daemon.py"" stop"; Flags: runhidden; Check: FileExists(ExpandConstant('{app}\python\pythonw.exe'))
