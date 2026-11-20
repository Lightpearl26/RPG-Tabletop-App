; Inno Setup 6 installer of the GM application and of the player application.
;
; One script, two installers:
;   iscc /DApp=gm     /DAppVersion=0.2.0 packaging\installer.iss   -> dist\installers\RPGTabletop-Setup.exe
;   iscc /DApp=player /DAppVersion=0.2.0 packaging\installer.iss   -> dist\installers\RPGTabletopPlayer-Setup.exe
; It packs the one-folder PyInstaller build dist\<exe>\ (see gm.spec / player.spec).
; packaging\build.ps1 runs the whole chain.
;
; The installation is per user (%LOCALAPPDATA%\Programs\...), so neither the
; installation nor the automatic updates need administrator rights.
;
; Automatic updates (rpg_tabletop/net/update.py) start the installer with
;   /SILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS /RELAUNCH=1
; /RELAUNCH=1 starts the application again once the files are replaced.
;
; User data (%LOCALAPPDATA%\RPGTabletop: campaigns, certificate) is never touched,
; not even by the uninstaller.

#ifndef App
  #define App "gm"
#endif
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#if App == "gm"
  #define AppName "RPG Tabletop"
  #define AppExe "RPGTabletop"
  ; Never change an AppId: it is how upgrades find the existing installation
  #define AppGuid "{{EFC65291-F4D1-4FA1-BB5D-32B23A4D68D4}"
#elif App == "player"
  #define AppName "RPG Tabletop Player"
  #define AppExe "RPGTabletopPlayer"
  #define AppGuid "{{E7CB1E70-2850-48CC-B979-AC3AC75E705C}"
#else
  #error App must be "gm" or "player"
#endif

#define RepoUrl "https://github.com/Lightpearl26/RPG-Tabletop-App"
#define BuildDir AddBackslash(SourcePath) + "..\dist\" + AppExe

#if !DirExists(BuildDir)
  #error The PyInstaller build is missing: run "pyinstaller packaging\<app>.spec" first
#endif

[Setup]
AppId={#AppGuid}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Lightpearl26
AppPublisherURL={#RepoUrl}
AppSupportURL={#RepoUrl}/issues
AppUpdatesURL={#RepoUrl}/releases
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#AddBackslash(SourcePath)}..\dist\installers
OutputBaseFilename={#AppExe}-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#AppExe}.exe
UninstallDisplayName={#AppName}
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; Libraries of the previous version: removed so that no stale file survives an upgrade
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#BuildDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}.exe"; Tasks: desktopicon

[Run]
; Interactive installation: "Launch" check box on the last page
Filename: "{app}\{#AppExe}.exe"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
; Automatic update: start the application again
Filename: "{app}\{#AppExe}.exe"; Flags: nowait; Check: ShouldRelaunch

[Code]
function ShouldRelaunch: Boolean;
begin
  Result := WizardSilent and (ExpandConstant('{param:RELAUNCH|0}') = '1');
end;
