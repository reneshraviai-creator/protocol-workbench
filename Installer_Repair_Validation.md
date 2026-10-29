# Preview 14 — installer repair validation

Date: 30 October 2026. Engineering Preview; local simulators and isolated test equipment only.

## Confirmed defect and correction

The Preview 13 publisher-attribution refresh changed licence documents and app publisher defaults but left five internal SHA256SUMS entries stale in each installer. Outer ZIP checksums alone did not detect this packaging mistake. Preview 14 is rebuilt from final source and validates every internal manifest entry and the external ZIP checksum. The attribution defect explains a possible installation checksum error; it does not diagnose folder deletion, malware or antivirus false positives.

## Installer changes

- Windows Check Package.bat checks the full manifest using built-in PowerShell before Python starts. Missing runtime files are reported without depending on that runtime.
- Readable shared Windows action runner saves console/error logs outside the download under LocalAppData/ProtocolWorkbenchInstallerLogs. Runtime architecture/import checking remains available.
- Clear ZIP-preview, incomplete extraction, checksum mismatch and quarantine guidance. No hidden/encoded execution, execution-policy overrides, Defender exclusions or protection changes.
- Python preflight checks malformed, duplicate and escaping paths, missing files and unlisted runtime source files before installation data changes. Generated runtime bytecode is excluded from extra-file inventory.
- Local failure logs survive removal of the extracted download. Logs can contain local paths; review/redact before sharing.
- Public evaluation notice and GitHub-only publisher identity are included in rebuilt packages.

## Recorded results

| Check | Result |
|---|---|
| Baseline regression with localhost permission | 688 passed, 0 failed; 118.23 seconds |
| Full regression after repair | 697 passed, 0 failed; 361 warnings; 118.81 seconds |
| Installer/packaging targeted checks | 29 passed |
| Missing runtime / changed payload / malformed or escaping manifest | Rejected; existing project fixture preserved |
| Unlisted runtime source / generated cache distinction | PASS |
| Error log retained outside deleted extraction fixture | PASS |
| Rebuilt archive and deliberate post-build attribution edit | Clean archive accepted; altered archive rejected |
| Final Windows x64/macOS/Linux archives | All CRC, complete internal manifest, external SHA-256 and privacy static checks passed |
| Windows native binary inspection | 50 PE files checked; no foreign binaries executed |
| Native Mac disposable install | PASS; dependencies installed; all isolated OPC UA/Modbus loopback checks passed |
| Native Mac installed GUI | Preview 14 bootstrap verified; started on disposable port and stopped cleanly |
| Native Windows batch/PowerShell execution | NOT TESTED; generated command inspected only |
| Native Windows/Linux installation | NOT TESTED |
| Antivirus/EDR acceptance and signing | NOT TESTED / unsigned; no certificate supplied |
| Real PLC/RTU and endurance | NOT TESTED in this revision |

The initial restricted baseline attempt failed because the sandbox prohibited localhost sockets. No app/installer implementation change began until the rerun with network permission passed. No tests were silently waived. Existing regression warnings are recorded, not described as failures.

## Distribution and limits

Windows x64, macOS and Linux packages are refreshed; patched native Windows ARM64/32-bit remain unavailable. Windows x64-on-ARM emulation is not qualified. Native Windows testing and the exact antivirus detection remain required to confirm the reported Windows symptom is resolved. No guarantee of bug-free operation, malware absence or production readiness is made. The existing development application and its saved projects were not upgraded; the disposable installation was left stopped.

Historical Preview 13 installer/documentation ZIPs are withdrawn from GitHub downloads; source tags and original local archives remain historical. Use matching Preview 14 ZIP/checksum files without manually replacing packaged files.
