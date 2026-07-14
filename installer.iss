; Inno Setup script for HyperFetch.
; Build with: iscc installer.iss   (after build.ps1 has produced dist\HyperFetch)

#define AppName "HyperFetch"
; Overridable from the command line: iscc /DAppVersion=2.0.0 installer.iss
#ifndef AppVersion
  #define AppVersion "2.1.0"
#endif
#define AppPublisher "HyperFetch"
#define AppExe "HyperFetch.exe"

[Setup]
AppId={{8F3C1A92-5D44-4E27-9C61-2B7A0E5F1D33}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}
OutputDir=dist\installer
OutputBaseFilename=HyperFetch-{#AppVersion}-setup
SetupIconFile=assets\icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
ChangesAssociations=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"
Name: "startup"; Description: "Start {#AppName} when Windows starts"; GroupDescription: "Startup:"; Flags: unchecked
Name: "assoc"; Description: "Open &.torrent files and magnet: links with {#AppName}"; GroupDescription: "File associations:"

[Registry]
; .torrent file type (shown with the app icon, opens in HyperFetch)
Root: HKA; Subkey: "Software\Classes\.torrent\OpenWithProgids"; ValueType: string; ValueName: "HyperFetch.torrent"; ValueData: ""; Flags: uninsdeletevalue; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\HyperFetch.torrent"; ValueType: string; ValueName: ""; ValueData: "BitTorrent File"; Flags: uninsdeletekey; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\HyperFetch.torrent\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExe},0"; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\HyperFetch.torrent\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: assoc
; magnet: protocol
Root: HKA; Subkey: "Software\Classes\magnet"; ValueType: string; ValueName: ""; ValueData: "URL:magnet"; Flags: uninsdeletekey; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\magnet"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\magnet\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExe},0"; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\magnet\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: assoc

[Files]
; the whole PyInstaller onedir tree
Source: "dist\HyperFetch\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: startup

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
