; ==============================================================================
; Archivio — Programma di Installazione Ufficiale per Windows (Inno Setup 6)
; Copyright (C) 2026 Antonio Cafaro — Licenza GNU GPLv3
; ==============================================================================

#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif

[Setup]
AppId={{8F4C9D2A-51B7-4E83-9A14-7C6B3D2E1F09}
AppName=Archivio
AppVersion={#MyAppVersion}
AppVerName=Archivio {#MyAppVersion}
AppPublisher=Antonio Cafaro
AppPublisherURL=https://github.com/a-bit-are/archivio
AppSupportURL=https://github.com/a-bit-are/archivio
AppUpdatesURL=https://github.com/a-bit-are/archivio/releases
DefaultDirName={autopf}\Archivio
DefaultGroupName=Archivio
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
OutputDir=..\dist
OutputBaseFilename=Archivio-Windows-Setup
SetupIconFile=..\assets\archivio.ico
UninstallDisplayIcon={app}\Archivio.exe
UninstallDisplayName=Archivio
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Languages]
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\Archivio\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Archivio"; Filename: "{app}\Archivio.exe"
Name: "{autodesktop}\Archivio"; Filename: "{app}\Archivio.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Archivio.exe"; Description: "{cm:LaunchProgram,Archivio}"; Flags: nowait postinstall skipifsilent
