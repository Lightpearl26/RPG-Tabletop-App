; Inno Setup 6 installer of RPG Tabletop (player application, and GM application on demand).
;
; Built by packaging\build.ps1, which prepares build\installer\:
;   python\        embedded Python with every dependency of requirements.txt
;   app\           the code (rpg_tabletop\, requirements.txt, launcher.py)
;   launcher.pyw   the launcher, which keeps app\ up to date with the main branch
;   app.ico        icon of the shortcuts (drawn in packaging\app_icon.svg)
;
; The shortcuts start "python\pythonw.exe launcher.pyw player" (or gm). Nothing has to
; be installed beforehand, and the installation is per user (%LOCALAPPDATA%\Programs),
; without administrator rights.
;
; Updates of the code do not need a new installer: the launcher downloads them from
; GitHub. A new installer is only needed for new users, or when the embedded Python
; itself must change.
;
; User data (%LOCALAPPDATA%\RPGTabletop: campaigns, certificate) is never touched,
; not even by the uninstaller.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef StageDir
  #define StageDir AddBackslash(SourcePath) + "..\build\installer"
#endif

#define AppName "RPG Tabletop"
#define RepoUrl "https://github.com/Lightpearl26/RPG-Tabletop-App"
#define Pythonw "{app}\python\pythonw.exe"
#define Icon "{app}\app.ico"

#if !FileExists(StageDir + "\launcher.pyw")
  #error build\installer is missing: run packaging\build.ps1
#endif

[Setup]
; Never change the AppId: it is how a new installer finds the existing installation
AppId={{EFC65291-F4D1-4FA1-BB5D-32B23A4D68D4}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName}
AppPublisher=Lightpearl26
AppPublisherURL={#RepoUrl}
AppSupportURL={#RepoUrl}/issues
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#AddBackslash(SourcePath)}..\dist\installers
OutputBaseFilename=RPGTabletop-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={#Icon}
SetupIconFile={#AddBackslash(SourcePath)}..\rpg_tabletop\ui\theme\app_icon.ico
UninstallDisplayName={#AppName}
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Types]
Name: "player"; Description: "Joueur"
Name: "full"; Description: "Maître du jeu et joueur"

[Components]
Name: "player"; Description: "Application joueur"; Types: player full; Flags: fixed
Name: "gm"; Description: "Application MJ (gestionnaire de campagne et serveur de partie)"; Types: full

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; Start from a clean copy: a previous version may have more files
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\app.new"
Type: filesandordirs; Name: "{app}\app.old"
Type: filesandordirs; Name: "{app}\python"

[Files]
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; AppUserModelID: same IDs as rpg_tabletop\ui\theme\app_icon.py, so that the windows
; are grouped under this icon in the taskbar, and not under Python's
Name: "{autoprograms}\RPG Tabletop Player"; Filename: "{#Pythonw}"; Parameters: """{app}\launcher.pyw"" player"; WorkingDir: "{app}"; IconFilename: "{#Icon}"; AppUserModelID: "RPGTabletop.Player"; Components: player
Name: "{autoprograms}\RPG Tabletop"; Filename: "{#Pythonw}"; Parameters: """{app}\launcher.pyw"" gm"; WorkingDir: "{app}"; IconFilename: "{#Icon}"; AppUserModelID: "RPGTabletop.GM"; Components: gm
Name: "{autodesktop}\RPG Tabletop Player"; Filename: "{#Pythonw}"; Parameters: """{app}\launcher.pyw"" player"; WorkingDir: "{app}"; IconFilename: "{#Icon}"; AppUserModelID: "RPGTabletop.Player"; Components: player; Tasks: desktopicon
Name: "{autodesktop}\RPG Tabletop"; Filename: "{#Pythonw}"; Parameters: """{app}\launcher.pyw"" gm"; WorkingDir: "{app}"; IconFilename: "{#Icon}"; AppUserModelID: "RPGTabletop.GM"; Components: gm; Tasks: desktopicon

[Run]
Filename: "{#Pythonw}"; Parameters: """{app}\launcher.pyw"" gm"; WorkingDir: "{app}"; Description: "{cm:LaunchProgram,RPG Tabletop}"; Components: gm; Flags: nowait postinstall skipifsilent
Filename: "{#Pythonw}"; Parameters: """{app}\launcher.pyw"" player"; WorkingDir: "{app}"; Description: "{cm:LaunchProgram,RPG Tabletop Player}"; Components: player; Check: not WizardIsComponentSelected('gm'); Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Files written after the installation (updates, pip, Python caches)
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\app.new"
Type: filesandordirs; Name: "{app}\app.old"
Type: filesandordirs; Name: "{app}\python"
Type: files; Name: "{app}\launcher.pyw"
Type: files; Name: "{app}\app.ico"
