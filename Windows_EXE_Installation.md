# Windows EXE setup — Preview 14, installer revision exe1

A single guided Windows installer for the existing Preview 14 offline x64 application. Engineering Preview; unsigned; native Windows and ARM emulation acceptance are not yet validated. The application's browser GUI and protocol adapters are unchanged.

## Download and install

1. Download **ProtocolWorkbench-2.0.0-preview.14-Setup.exe** and its matching `.exe.sha256` from the Preview 14 release. This is the EXE installer; the older `Windows-x64.zip` is the separate archive-install option. No ZIP extraction or separate Python installation is needed for the EXE.
2. Optionally verify the downloaded EXE with PowerShell `Get-FileHash -Algorithm SHA256 -LiteralPath "FULL PATH TO THE EXE"` and compare the result with the matching checksum file. A checksum detects a changed download; it is not a signing certificate or antivirus approval.
3. Run the EXE from your Windows VM or Windows computer. Read the welcome and detected-architecture page.
4. On **Windows x64**, the installer uses the bundled x64 runtime. On **Windows 11 ARM64**, including Parallels on Apple Silicon, the page explains that this is an **experimental x64-emulation installation**, not a native ARM64 build. The checkbox starts unchecked; choose it only if you want to try that unvalidated mode. Windows 10 ARM64 and 32-bit Windows are rejected. Silent ARM installation is rejected because the architecture page must be reviewed.
5. Choose whether to create the optional desktop shortcut, then click Install. Setup checks the package, creates/upgrades the per-user installation and runs isolated local protocol checks. It does not contact saved PLC endpoints or start application jobs during installation.
6. Finish. Use **Start → Protocol Workbench → Open Protocol Workbench**, or the optional desktop shortcut. Opening the app at the finish page is optional and unchecked by default. The packaged dashboard normally uses `http://127.0.0.1:8878/`; an upgraded installation retains its saved port.
7. Use **Start → Protocol Workbench → Stop Protocol Workbench** to stop the background application. Closing its browser tab does not stop it.

## What is installed

- Program versions and saved data: `%LOCALAPPDATA%\ProtocolWorkbenchCommunity`.
- Durable installer/control files: `%LOCALAPPDATA%\ProtocolWorkbenchInstallers\2.0.0-preview.14-exe1`. Shortcuts depend on these files; do not manually delete this folder.
- Logs: `%LOCALAPPDATA%\ProtocolWorkbenchInstallerLogs`.
- A current-user Start menu group, optional desktop shortcut and Windows Installed apps uninstall entry. Administrator access, system services, scheduled startup jobs, firewall/Defender changes and serial drivers are not installed.

After a successful installation, you may remove the downloaded EXE; the installed shortcuts use the durable local control files. Keep a verified installer copy if you need offline reinstall/repair later. The EXE does not silently auto-update or download runtime dependencies.

## Uninstall

Open Windows **Settings → Apps → Installed apps**, find **Protocol Workbench (Engineering Preview)** and choose Uninstall. Alternatively use its Start menu uninstall shortcut. The uninstaller stops/removes program versions through the existing application control procedure and removes its shortcuts/registration. It **retains saved projects, data, logs and backups**. If stopping or uninstalling fails, it reports the failure and retains shortcuts/registration rather than declaring success.

## If installation fails

Do not disable protection or add exclusions. Read the Details output and retain the local installer log. If files disappear, inspect Windows Security **Virus & threat protection → Protection history** or your security product's quarantine. Record the detection name, affected file and action. If SmartScreen or your organisation blocks unsigned software, stop and obtain approval through your normal process rather than bypassing the warning.

The EXE wrapper addresses extraction/architecture confusion and keeps control files outside a temporary ZIP preview. It cannot guarantee antivirus acceptance or resolve an unknown detection. The same patched x64 dependencies are bundled; no vulnerable ARM64 legacy runtime is substituted. RTU still needs an appropriate, separately installed driver/serial adapter; x64 emulation does not provide ARM64 device drivers.

## Build and test status

Read Windows_EXE_Validation.md for actual evidence. Compilation, extraction comparison and Mac-host regression checks are not native Windows installation/uninstall or Windows-on-ARM qualification.
