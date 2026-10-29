; Per-user EXE wrapper around the verified Preview 14 offline x64 package.
Unicode true
RequestExecutionLevel user
ManifestDPIAware true
ManifestLongPathAware true
SetCompressor zlib
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "x64.nsh"
!include "WinVer.nsh"
!include "nsDialogs.nsh"

!ifndef PACKAGE_ROOT
 !error "PACKAGE_ROOT must identify the verified package"
!endif
!ifndef OUTPUT_FILE
 !error "OUTPUT_FILE required"
!endif
!ifndef APP_VERSION
 !error "APP_VERSION must match the verified payload"
!endif
!define SETUP_REVISION "exe1"
!define HELPER_DIR "ProtocolWorkbenchInstallers\${APP_VERSION}-${SETUP_REVISION}"
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\ProtocolWorkbenchCommunity"
VIProductVersion "2.0.0.14"
VIAddVersionKey /LANG=1033 "ProductName" "Protocol Workbench"
VIAddVersionKey /LANG=1033 "CompanyName" "reneshraviai-creator"
VIAddVersionKey /LANG=1033 "FileDescription" "Engineering Preview setup; x64 payload, experimental ARM64 emulation"
VIAddVersionKey /LANG=1033 "FileVersion" "2.0.0.14"
VIAddVersionKey /LANG=1033 "ProductVersion" "${APP_VERSION} (${SETUP_REVISION})"
VIAddVersionKey /LANG=1033 "LegalCopyright" "Publisher: GitHub reneshraviai-creator; third-party rights retained"
Name "Protocol Workbench — Engineering Preview"
OutFile "${OUTPUT_FILE}"
InstallDir "$LOCALAPPDATA\${HELPER_DIR}"
BrandingText "Protocol Workbench | Engineering Preview | isolated testing only"
ShowInstDetails show
ShowUninstDetails show
Var ArmConsent
Var ArmCheckbox
Var Dialog

!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "Install Protocol Workbench"
!define MUI_WELCOMEPAGE_TEXT "A graphical OPC UA and Modbus test workbench.$\r$\n$\r$\nInstalls for your Windows user. Python and dependencies are included; administrator access and manual ZIP extraction are not required.$\r$\n$\r$\nEngineering Preview: local simulators and isolated test equipment only. Native Windows execution is not qualified. No production or safety-system use.$\r$\n$\r$\nThis installer is unsigned. It does not disable security protections or install serial drivers."
!insertmacro MUI_PAGE_WELCOME
Page custom ArchitecturePage ArchitectureLeave
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "Open Protocol Workbench"
!define MUI_FINISHPAGE_RUN_FUNCTION OpenWorkbench
!define MUI_FINISHPAGE_RUN_NOTCHECKED
!define MUI_FINISHPAGE_TEXT "Setup completed. Use the desktop or Start menu shortcut to open the application.$\r$\n$\r$\nClosing the browser does not stop the background app. Use Stop Protocol Workbench.$\r$\n$\r$\nSaved projects and backups are retained when uninstalling. Installer logs are under LocalAppData\ProtocolWorkbenchInstallerLogs."
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Function .onInit
 SetShellVarContext current
 SetRegView 64
 StrCpy $ArmConsent "0"
 ${IfNot} ${AtLeastWin10}
  MessageBox MB_ICONSTOP "This package requires Windows 10/11 x64, or experimental x64 emulation on Windows 11 ARM64."
  Abort
 ${EndIf}
 ${IfNot} ${RunningX64}
  MessageBox MB_ICONSTOP "A 64-bit Windows operating system is required. No 32-bit application package is included."
  Abort
 ${EndIf}
 ${If} ${IsNativeARM64}
  ${IfNot} ${AtLeastWin11}
   MessageBox MB_ICONSTOP "ARM64 requires Windows 11 x64 emulation. No native ARM64 runtime is included."
   Abort
  ${EndIf}
  IfSilent 0 +3
   MessageBox MB_ICONSTOP "ARM64 requires reviewing the experimental-emulation page. Run the interactive installer."
   Abort
 ${EndIf}
FunctionEnd

Function ArchitecturePage
 !insertmacro MUI_HEADER_TEXT "Check your Windows system" "The setup detects architecture; no separate runtime download is needed."
 nsDialogs::Create 1018
 Pop $Dialog
 ${If} $Dialog == error
  Abort
 ${EndIf}
 ${If} ${IsNativeARM64}
  ${NSD_CreateLabel} 0 0 100% 75u "Detected: Windows 11 ARM64, including Parallels on Apple Silicon.$\r$\n$\r$\nThis package contains the patched x64 runtime, not a native ARM64 runtime. Windows can emulate x64 applications, but Protocol Workbench has not been validated under that emulation. Installation may fail its runtime or local protocol checks."
  Pop $0
  ${NSD_CreateCheckbox} 0 90u 100% 35u "Try the experimental x64-emulation installation on this ARM64 computer."
  Pop $ArmCheckbox
  ${If} $ArmConsent == "1"
   ${NSD_Check} $ArmCheckbox
  ${EndIf}
 ${Else}
  ${NSD_CreateLabel} 0 0 100% 70u "Detected: Windows x64.$\r$\n$\r$\nSetup includes the x64 Python runtime and required libraries. It will verify the package, install the application for your Windows user and create shortcuts. Native Windows testing remains pending; this is an Engineering Preview."
  Pop $0
 ${EndIf}
 ${NSD_CreateLabel} 0 140u 100% 40u "No application jobs start automatically. Existing installations are upgraded using the application's backup procedure. On failure, keep the installer log; do not disable antivirus."
 Pop $0
 nsDialogs::Show
FunctionEnd

Function ArchitectureLeave
 ${If} ${IsNativeARM64}
  ${NSD_GetState} $ArmCheckbox $0
  ${If} $0 != ${BST_CHECKED}
   MessageBox MB_ICONINFORMATION "Select the experimental option to try x64 emulation, or cancel. This is not a native ARM64 build."
   Abort
  ${EndIf}
  StrCpy $ArmConsent "1"
 ${EndIf}
FunctionEnd

Section "Protocol Workbench and Start menu shortcuts (required)" SecApp
 SectionIn RO
 SetShellVarContext current
 SetOutPath "$INSTDIR"
 DetailPrint "Extracting verified offline package..."
 File /r "${PACKAGE_ROOT}/*"
 File "Installation.html"
 SetOutPath "$INSTDIR\installer-notices"
 File "notices/*"
 SetOutPath "$INSTDIR"
 DetailPrint "Checking runtime and installing application. This may take several minutes..."
 nsExec::ExecToLog '"$SYSDIR\cmd.exe" /D /S /C ""$INSTDIR\Run Workbench.bat" install"'
 Pop $0
 ${If} $0 != "0"
  MessageBox MB_ICONSTOP "Installation failed. No shortcuts or uninstall registration were created.$\r$\n$\r$\nKeep the log under LocalAppData\ProtocolWorkbenchInstallerLogs and inspect Windows Security Protection history if files disappeared.$\r$\n$\r$\nThe setup files remain available for diagnosis. Existing project data is not removed."
  SetErrorLevel 1
  Abort
 ${EndIf}
 WriteUninstaller "$INSTDIR\Uninstall.exe"
 CreateDirectory "$SMPROGRAMS\Protocol Workbench"
 CreateShortcut "$SMPROGRAMS\Protocol Workbench\Open Protocol Workbench.lnk" "$SYSDIR\cmd.exe" '/D /S /C ""$INSTDIR\Open App.bat""'
 CreateShortcut "$SMPROGRAMS\Protocol Workbench\Stop Protocol Workbench.lnk" "$SYSDIR\cmd.exe" '/D /S /C ""$INSTDIR\Stop App.bat""'
 CreateShortcut "$SMPROGRAMS\Protocol Workbench\Create Diagnostics.lnk" "$SYSDIR\cmd.exe" '/D /S /C ""$INSTDIR\Create Diagnostics.bat""'
 CreateShortcut "$SMPROGRAMS\Protocol Workbench\Installation Guide.lnk" "$INSTDIR\Installation.html"
 CreateShortcut "$SMPROGRAMS\Protocol Workbench\Uninstall Protocol Workbench.lnk" "$INSTDIR\Uninstall.exe"
 WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "Protocol Workbench (Engineering Preview)"
 WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${APP_VERSION} (${SETUP_REVISION})"
 WriteRegStr HKCU "${UNINSTALL_KEY}" "Publisher" "reneshraviai-creator"
 WriteRegStr HKCU "${UNINSTALL_KEY}" "URLInfoAbout" "https://github.com/reneshraviai-creator/protocol-workbench"
 WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$LOCALAPPDATA\ProtocolWorkbenchCommunity"
 WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
 WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
 WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1
SectionEnd

Section /o "Desktop shortcut" SecDesktop
 CreateShortcut "$DESKTOP\Protocol Workbench.lnk" "$SYSDIR\cmd.exe" '/D /S /C ""$INSTDIR\Open App.bat""'
SectionEnd

Function OpenWorkbench
 Exec '"$SYSDIR\cmd.exe" /D /S /C ""$INSTDIR\Open App.bat""'
FunctionEnd

Function un.onInit
 SetShellVarContext current
 SetRegView 64
 ; Restrict removal to the fixed, version-specific setup helper directory.
 StrCpy $INSTDIR "$LOCALAPPDATA\${HELPER_DIR}"
FunctionEnd

Section "Uninstall"
 SetShellVarContext current
 IfFileExists "$LOCALAPPDATA\ProtocolWorkbenchCommunity\install.json" 0 remove_shortcuts
 nsExec::ExecToLog '"$SYSDIR\cmd.exe" /D /S /C ""$INSTDIR\Run Workbench.bat" uninstall"'
 Pop $0
 ${If} $0 != "0"
  MessageBox MB_ICONSTOP "The application could not be stopped or uninstalled. Review the installer log. Project data, shortcuts and registration have been retained."
  SetErrorLevel 1
  Abort
 ${EndIf}
 remove_shortcuts:
 Delete "$DESKTOP\Protocol Workbench.lnk"
 Delete "$SMPROGRAMS\Protocol Workbench\Open Protocol Workbench.lnk"
 Delete "$SMPROGRAMS\Protocol Workbench\Stop Protocol Workbench.lnk"
 Delete "$SMPROGRAMS\Protocol Workbench\Create Diagnostics.lnk"
 Delete "$SMPROGRAMS\Protocol Workbench\Installation Guide.lnk"
 Delete "$SMPROGRAMS\Protocol Workbench\Uninstall Protocol Workbench.lnk"
 RMDir "$SMPROGRAMS\Protocol Workbench"
 DeleteRegKey HKCU "${UNINSTALL_KEY}"
 ; Application's own uninstaller retains data/backups; never recursively remove its prefix.
 RMDir /r "$INSTDIR"
SectionEnd
