; Inno Setup script - wraps dist\EyeScan into a single Keeper-Setup.exe
; Build with build_installer.bat (needs Inno Setup 6: winget install JRSoftware.InnoSetup)
#define AppName "Keeper"
#define AppVersion "2.3.0"
#define AppPublisher "Devendra Kulkarni - DEeV.in"
#define AppURL "https://www.deev.in"

[Setup]
AppId={{C5A7317B-B2EB-468A-A373-87D39E4C62BA}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppCopyright=Copyright (c) 2026 Devendra Kulkarni - DEeV.in. All rights reserved.
LicenseFile=LICENSE.txt
SetupIconFile=assets\Keeper.ico
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
OutputDir=installer
OutputBaseFilename=Keeper-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
UninstallDisplayIcon={app}\Keeper.exe
DisableProgramGroupPage=yes

[Files]
Source: "dist\Keeper\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\Keeper.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\Keeper.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\Keeper.exe"; Description: "Launch Keeper"; Flags: nowait postinstall skipifsilent
