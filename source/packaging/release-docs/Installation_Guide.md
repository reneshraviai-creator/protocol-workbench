# Installation — Protocol Workbench 2.0.0-preview.13

Engineering Preview; use local simulators and isolated non-production test equipment. These are unsigned archive installers, not MSI/DMG/DEB images. Windows/Linux native acceptance remains pending. Do not bypass operating-system security warnings.

## Security-refresh availability

Preview 13 provides Windows x64, macOS and Linux archives only. Official cryptography 50.0.2 native Windows ARM64/win32 wheels are unavailable; those targets are blocked. Do not substitute an old affected runtime or claim x64 emulation is qualified. Native Windows/Linux execution remains NOT TESTED.

Read [recommended system specifications](System_Requirements.html) before installation; these are provisional sizing guidelines.

## Choose the right package
| Computer | Package |
|---|---|
| Windows ARM64, including an ARM Windows VM on Apple Silicon | BLOCKED in this security refresh; no patched native installer supplied |
| Windows x64 | Windows 64 bit |
| Windows x86 (32-bit OS) | BLOCKED in this security refresh; no patched native installer supplied |
| macOS with Python 3.11–3.14 | macOS |
| Linux with Python 3.11–3.14 and venv support | Linux |

The supplied Windows x64 package includes an architecture-specific Python runtime and dependencies and install offline. Linux/macOS packages create a virtual environment and download pinned dependencies from PyPI; internet is needed during installation, not for licence information/acknowledgement or normal offline simulator use. Dependencies differ across native Windows builds; inspect BUILD_INFO.json and payload/requirements.txt. Cross-architecture static checks do not establish native execution.

## Windows, step by step
1. Download the correct ZIP and its .sha256 checksum file. Verify with PowerShell Get-FileHash if required by your organisation.
2. In File Explorer, right-click the ZIP → Extract All. Choose a short folder such as C:\PW. Finish extraction. Do not run files while browsing the ZIP or from its temporary preview folder.
3. Open the extracted PW-Win-x64 folder. Run Check Runtime.bat. It must report imports OK and the expected architecture/bitness.
4. Run Install.bat. It installs for your Windows user under LocalAppData\ProtocolWorkbenchCommunity; it does not need administrator access or a separate Python installation. Keep the console output if it fails.
5. Run Open App.bat. Open http://127.0.0.1:8878/ if the browser does not open. The development app on port 8877 is separate.
6. Run Validate Installation.bat for isolated loopback protocol checks. It does not contact saved devices.
7. Use Stop App.bat to stop the background application. Closing the browser alone does not stop it.
If Windows asks which app opens a command file, run the .bat alternative from Command Prompt in the extracted folder; do not rename the extension to .command.

## macOS, step by step
1. Confirm python3 --version reports 3.11–3.14; obtain a suitable Python installation through your approved software source if necessary.
2. Extract the macOS ZIP. Open Terminal and change to its ProtocolWorkbench-Community-macOS folder.
3. Run sh Install.command. Wait for environment creation, dependency installation and loopback validation to finish.
4. Run sh "Open App.command". Dashboard: http://127.0.0.1:8878/.
5. Run sh "Stop App.command" to stop. Run sh "Validate Installation.command" for checks and sh "Create Diagnostics.command" for a minimal support ZIP.
Default installation: ~/Library/Application Support/ProtocolWorkbenchCommunity. Scripts use the selected python3 executable; an unsupported interpreter produces an actionable error. No signing/notarisation is claimed.

## Linux, step by step
1. Confirm python3 --version is 3.11–3.14 and python3 -m venv is available. Install the distribution's Python/venv packages through your approved administrator if needed.
2. Extract the ZIP and open a terminal in ProtocolWorkbench-Community-Linux.
3. Run sh Install.sh. Installation requires network access to PyPI.
4. Run sh "Open App.sh"; browse to http://127.0.0.1:8878/.
5. Run sh "Stop App.sh" to stop. Validation and diagnostic scripts are supplied.
Default installation: $XDG_DATA_HOME/ProtocolWorkbenchCommunity or ~/.local/share/ProtocolWorkbenchCommunity. Serial devices may need OS group/driver permissions; these are not changed automatically.

## Custom installation / upgrades
From the extracted package folder: python3 setup.py install --prefix "YOUR SEPARATE INSTALL FOLDER" --port 8878 (Windows: use runtime\python.exe and add --offline).
Run the new package's Install script to upgrade the same default installation. New dependencies and fixed-loopback checks are staged first. After validation, the old application is stopped, its data copied to backups and the new version selected. Installed versions remain under versions/. The saved port is retained. Start explicitly after reviewing the upgrade. Recordings running before Stop can be interrupted; finish testing before upgrading.

## Rollback
Rollback restores BOTH the previous installed release and its matching pre-upgrade data snapshot. Later work is preserved under a before-rollback backup directory but is not merged. Stop engineering work, export needed configurations and run Rollback.bat / sh Rollback.command / sh Rollback.sh. Type RESTORE when prompted. The CLI equivalent is setup.py rollback --confirm-data-restore. Review the result before starting. No previous release/backup means rollback is refused. This is local rollback preparation, not qualified arbitrary cross-version database migration.

Uninstall removes installed program versions after stopping; user data/backups remain. Keep the extracted installer/control.py to reinstall or administer retained data. Read Administrator and Troubleshooting guides before distributing or upgrading.
