; Per-user installer for Harness Discussion. Built by packaging\build.ps1 (Inno Setup 6).
#define AppName "Harness Discussion"
#define AppExe "HarnessDiscussion.exe"
#define AppId "F1F1865D-A77B-4C8E-A0C4-7157560F4610"
#define RunKey "Software\Microsoft\Windows\CurrentVersion\Run"
; Older builds started at sign-in through this Run value
#define RunValue "select-to-tts"
; The app was called Select to TTS before 0.5.0, then Codex Speak. Both used this AppId. Setup removes
; that install by hand after installing, and never runs its uninstaller.
#define OldAppId "FED44346-501C-414C-A557-8F7BDA1AC94A"
#define OldExe "SelectToTTS.exe"
#define OldExe2 "CodexSpeak.exe"
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{{#AppId}}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=tzachbon
AppPublisherURL=https://github.com/tzachbon/harness-discussion
DefaultDirName={code:DefaultDir}
UsePreviousAppDir=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=HarnessDiscussion-Setup
SetupIconFile=..\build\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
CloseApplications=no
InfoBeforeFile=privacy.txt

[Tasks]
Name: startup; Description: "Start {#AppName} when I sign in to Windows"; Check: IsFreshInstall
Name: desktopicon; Description: "Create a desktop shortcut"; Flags: unchecked
Name: localonly; Description: "Use only voices that run on this PC (turns off Cursor, Codex, Edge and the speech-to-text server)"; Flags: unchecked

[Files]
Source: "..\build\dist\HarnessDiscussion\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; Drop files from an older version that this version no longer ships
Type: filesandordirs; Name: "{app}\_internal"
; Leftovers from Select to TTS. The app moves its settings and sign-in task on first start.
Type: files; Name: "{autoprograms}\Select to TTS.lnk"
Type: files; Name: "{autodesktop}\Select to TTS.lnk"
Type: filesandordirs; Name: "{%TEMP}\select-to-tts"
Type: filesandordirs; Name: "{%TEMP}\comtypes_cache\SelectToTTS-311"
; Leftovers from Codex Speak, which its uninstaller would have removed
Type: files; Name: "{autoprograms}\Codex Speak.lnk"
Type: files; Name: "{autodesktop}\Codex Speak.lnk"
Type: filesandordirs; Name: "{%TEMP}\codex-speak"
Type: filesandordirs; Name: "{%TEMP}\comtypes_cache\CodexSpeak-311"

[UninstallDelete]
; App-owned caches outside {app}. Settings and the log in %APPDATA%\harness-discussion are kept.
Type: filesandordirs; Name: "{%TEMP}\comtypes_cache\HarnessDiscussion-311"
Type: filesandordirs; Name: "{%TEMP}\harness-discussion"
Type: filesandordirs; Name: "{localappdata}\harness-discussion\updates"
Type: filesandordirs; Name: "{localappdata}\harness-discussion\kokoro"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
; Start-at-sign-in is a Task Scheduler logon task, so it starts at once instead of queueing
; behind every Run-key app. The app creates and removes it.
Filename: "{app}\{#AppExe}"; Parameters: "--startup on"; Tasks: startup; Flags: runhidden waituntilterminated
; An existing sign-in task is re-pointed here, even if the app isn't launched after setup
Filename: "{app}\{#AppExe}"; Parameters: "--startup refresh"; Flags: runhidden waituntilterminated
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall; Check: CanLaunch

[UninstallRun]
Filename: "{app}\{#AppExe}"; Parameters: "--startup off"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveStartupTask"

[Code]
function DefaultDir(Param: String): String;
var
  Dir: String;
begin
  // Harness Discussion upgrades keep a custom folder. Select to TTS and Codex Speak move to the new default.
  if RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{{#AppId}}_is1',
                         'InstallLocation', Dir) and FileExists(AddBackslash(Dir) + '{#AppExe}') then
    Result := RemoveBackslash(Dir)
  else
    Result := ExpandConstant('{autopf}\{#AppName}');
end;

// Upgrades keep the user's sign-in choice from the Settings page instead of re-asking.
function IsFreshInstall: Boolean;
begin
  Result := not RegKeyExists(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{{#AppId}}_is1')
            and not RegKeyExists(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{{#OldAppId}}_is1');
end;

procedure StopApp;
var
  Code: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM {#AppExe} /IM {#OldExe} /IM {#OldExe2}', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Sleep(500);
end;

var
  OldDir, OldUninstaller: String;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Key: String;
begin
  StopApp;
  // Where Select to TTS or Codex Speak was installed, possibly not the default folder
  Key := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{{#OldAppId}}_is1';
  if not RegQueryStringValue(HKCU, Key, 'InstallLocation', OldDir) then
  begin
    Key := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{{#AppId}}_is1';
    if not RegQueryStringValue(HKCU, Key, 'InstallLocation', OldDir) then
      OldDir := '';
  end;
  RegQueryStringValue(HKCU, Key, 'UninstallString', OldUninstaller);
  OldUninstaller := RemoveQuotes(OldUninstaller);
  Result := '';
end;

// The privacy choice must be saved before the app starts. A failure shows an error, skips the launch,
// and makes setup exit with code 1. Raising an exception here would not stop the launch.
var LocalOnlyFailed: Boolean;

function CanLaunch: Boolean;
begin
  Result := not LocalOnlyFailed;
end;

function GetCustomSetupExitCode: Integer;
begin
  Result := Ord(LocalOnlyFailed);
end;

function ApplyLocalOnly(const Exe: String): Boolean;
var
  Code: Integer;
begin
  Result := Exec(Exe, '--local-only', '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0);
end;
// end ApplyLocalOnly

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep <> ssPostInstall then
    Exit;
  if WizardIsTaskSelected('localonly') and not ApplyLocalOnly(ExpandConstant('{app}\{#AppExe}')) then
  begin
    LocalOnlyFailed := True;
    SuppressibleMsgBox('Could not turn off the online voices, so {#AppName} was not started. Check that '
      + ExpandConstant('{userappdata}') + '\harness-discussion\settings.json can be written, then run setup again.',
      mbError, MB_OK, IDOK);
  end;
  // Keep shared folders and any folder containing the new install.
  if (OldDir <> '') and (FileExists(AddBackslash(OldDir) + '{#OldExe}') or FileExists(AddBackslash(OldDir) + '{#OldExe2}'))
     and (CompareText(RemoveBackslash(OldDir), RemoveBackslash(ExpandConstant('{app}'))) <> 0)
     and (CompareText(Copy(AddBackslash(ExpandConstant('{app}')), 1, Length(AddBackslash(OldDir))),
                      AddBackslash(OldDir)) <> 0) then
  begin
    DeleteFile(AddBackslash(OldDir) + '{#OldExe}');
    DeleteFile(AddBackslash(OldDir) + '{#OldExe2}');
    DelTree(AddBackslash(OldDir) + '_internal', True, True, True);
    // Shared folders may have another app's unins000. Only remove our registered uninstaller.
    if CompareText(ExtractFileDir(OldUninstaller), RemoveBackslash(OldDir)) = 0 then
    begin
      DeleteFile(OldUninstaller);
      DeleteFile(ChangeFileExt(OldUninstaller, '.dat'));
    end;
    RemoveDir(RemoveBackslash(OldDir));  // succeeds only when empty
  end;
  // Only one Apps & features entry. Running the old uninstaller instead would delete the sign-in
  // task and the update folder this setup runs from.
  RegDeleteKeyIncludingSubkeys(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{{#OldAppId}}_is1');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    StopApp;
    RegDeleteValue(HKCU, '{#RunKey}', '{#RunValue}');  // left by builds before the logon task
  end;
end;
