; ==============================================================================
; installer.iss - Industrial Inno Setup Script
; Application: Insitumicron Creep GUI
; Target: Windows 10/11 64-bit (x64compatible)
; Hardware: STM32 USB-Serial (Modbus RTU, LVDT, I2C Load Cell), FLIR Camera
; ==============================================================================

#define MyAppName "Insitumicron Creep GUI"
#define MyAppVersion "1.1.0"
#define MyAppPublisher "Insitu Instruments"
#define MyAppURL "https://www.insituinstruments.com"
#define MyAppExeName "InsituMicronGUI.exe"
#define MyAppId "{{D1B4F4A5-E1A7-44E2-81D6-8F8D0B7A5C22}}"

[Setup]
; --- Application Metadata ---
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} v{#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}

; --- Installation Directory & Architecture ---
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
UsePreviousAppDir=yes

; Strict 64-bit industrial requirements
MinVersion=10.0
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=

; --- Process Lifecycle Safeguards ---
CloseApplications=yes
CloseApplicationsFilter={#MyAppExeName}
RestartApplications=no

; --- Output Configuration ---
OutputDir=Output
OutputBaseFilename=Insitumicron_Creep_GUI_v{#MyAppVersion}_Setup
SetupIconFile=app_icon.ico
WizardImageFile=wizard_image.bmp
WizardSmallImageFile=wizard_small.bmp
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern

; --- Uninstallation Display ---
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} v{#MyAppVersion}

; --- Windows Version Information Block ---
VersionInfoVersion={#MyAppVersion}.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} Production Setup
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Dirs]
; Grant write permissions to local Users so standard operators can write logs and settings in Program Files
Name: "{app}"; Permissions: users-modify
Name: "{app}\logs"; Permissions: users-modify
Name: "{app}\methods"; Permissions: users-modify

[Files]
; 1. Main PyInstaller Application Bundle
Source: "dist\InsituMicronGUI\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; 2. Compliance & Licensing Notices
Source: "LICENSES\*"; DestDir: "{app}\LICENSES"; Flags: ignoreversion recursesubdirs createallsubdirs

; 3. ST-Link USB Driver Package (extracted to temp dir for installation)
Source: "drivers\stsw-link009\*"; DestDir: "{tmp}\stsw-link009"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Start Menu Shortcut
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; IconIndex: 0; Comment: "{#MyAppName} Electromechanical Test Control"
; Desktop Shortcut
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; IconFilename: "{app}\{#MyAppExeName}"; IconIndex: 0; Comment: "{#MyAppName} Electromechanical Test Control"

[Run]
; 1. Install ST-Link USB Driver (VCP & Debug) silently
Filename: "{tmp}\stsw-link009\dpinst_amd64.exe"; Parameters: "/q /se"; StatusMsg: "Installing ST-Link USB Drivers (VCP & Debug)..."; Flags: waituntilterminated; Check: NeedsSTM32Driver

; 2. Post-installation application launch option
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Clean up runtime logs on uninstallation while preserving user experimental data
Type: filesandordirs; Name: "{app}\logs"
Type: files; Name: "{app}\*.log"

[Messages]
FinishedHeadingLabel=Setup has finished installing [name].
FinishedLabel=Industrial Test Rig Deployment Notice:%n%n1. STM32 HARDWARE CONNECTION:%n   Ensure the STM32 controller is connected via USB. The ST-Link Virtual COM Port (VCP) driver has been registered with Windows.%n%n2. FLIR SPINNAKER CAMERA:%n   If using FLIR optical inspection, ensure the Teledyne FLIR Spinnaker SDK runtime is installed on this host.%n%n3. LGPL NOTICES:%n   This application uses PySide6 under GNU LGPL v3. Licensing details are available in {app}\LICENSES\.%n%nSAFETY NOTICE: Always verify physical limit switches and emergency stop functionality prior to operating mechanical motor axes.

[Code]
// =============================================================================
// Helper: Check if an application process is currently running via WMI
// =============================================================================
function IsAppRunning(const FileName: string): Boolean;
var
  FSWbemLocator: Variant;
  FWMIService: Variant;
  FWbemObjectSet: Variant;
begin
  Result := False;
  try
    FSWbemLocator := CreateOleObject('WbemScripting.SWbemLocator');
    FWMIService := FSWbemLocator.ConnectServer('', 'root\CIMV2');
    FWbemObjectSet := FWMIService.ExecQuery(
      Format('SELECT Name FROM Win32_Process WHERE Name = "%s"', [FileName]));
    Result := (FWbemObjectSet.Count > 0);
    FWbemObjectSet := Unassigned;
    FWMIService := Unassigned;
    FSWbemLocator := Unassigned;
  except
    Result := False;
  end;
end;

// =============================================================================
// Pre-Install Guard: Ensure running application is closed before upgrading
// =============================================================================
function InitializeSetup(): Boolean;
var
  Retries: Integer;
begin
  Result := True;
  Retries := 0;

  while IsAppRunning('{#MyAppExeName}') and (Retries < 3) do
  begin
    if MsgBox('{#MyAppName} is currently running.' + #13#10 + #13#10 +
              'Please close all open instances of the application before continuing setup.',
              mbConfirmation, MB_OKCANCEL) = IDCANCEL then
    begin
      Result := False;
      Exit;
    end;
    Inc(Retries);
    Sleep(1000);
  end;

  if IsAppRunning('{#MyAppExeName}') then
  begin
    MsgBox('{#MyAppName} is still active. Setup cannot overwrite locked files and will now exit.', mbCriticalError, MB_OK);
    Result := False;
    Exit;
  end;
end;

// =============================================================================
// Pre-Uninstall Guard: Ensure running application is closed before removing
// =============================================================================
function InitializeUninstall(): Boolean;
begin
  Result := True;
  if IsAppRunning('{#MyAppExeName}') then
  begin
    MsgBox('{#MyAppName} is currently running.' + #13#10 + #13#10 +
           'Please close the application before uninstalling.', mbCriticalError, MB_OK);
    Result := False;
  end;
end;

// =============================================================================
// Driver Check: Detect if ST-Link USB driver is already registered
// =============================================================================
function NeedsSTM32Driver(): Boolean;
var
  Installed: Boolean;
begin
  Installed := 
    RegKeyExists(HKLM, 'SYSTEM\CurrentControlSet\Control\Class\{88BAE032-5A81-49f0-BC3D-A4FF138216D6}') or
    RegKeyExists(HKLM, 'SOFTWARE\STMicroelectronics\Virtual COM Port Driver') or
    RegKeyExists(HKLM, 'SOFTWARE\WOW6432Node\STMicroelectronics\Virtual COM Port Driver');

  if Installed then
    Log('NeedsSTM32Driver: ST-Link driver is already registered — skipping.')
  else
    Log('NeedsSTM32Driver: ST-Link driver not detected — executing DPInst installer.');

  Result := not Installed;
end;
