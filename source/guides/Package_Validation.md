# Community preview packaging validation

Version: 2.0.0-preview.4. Date: 19 October 2026.

| Package | Delivery | Validation |
|---|---|---|
| Windows x64 | ZIP with embedded Python 3.13 and pinned protocol libraries; offline installation | ZIP/checksum/PE architecture and dependency versions inspected. **Not run on Windows.** |
| macOS | ZIP with Python-based online installer; Python 3.11–3.14 prerequisite | Fresh install, protocol self-test, start, upgrade/backup, restart, status, stop and uninstall passed in an isolated folder with spaces. Project and port survived upgrade; project and backups survived uninstall. |
| Linux | ZIP with Python-based online installer; Python 3.11–3.14 and venv/pip prerequisites | ZIP/checksum verified. **Not run on Linux.** |

All packages exclude saved workspaces, credentials, logs and SQLite databases. Each archive has a SHA-256 sidecar and internal file manifest. Integrity is not publisher authentication: packages are unsigned. These are preview ZIP installers, not native MSI/DMG/DEB packages. No login startup/service/shortcut registration is included. Project autostart remains separate and defaults off for new data directories.

Installer preparation and local protocol self-test run before stopping an older installed version. Existing installation data is backed up after stopping. Installed versions are kept in version folders; uninstall removes those versions and preserves data/backups. Installation lock rejects simultaneous control/install mutations. Damage rejection and failed offline preparation preserve existing data/version metadata.

Default dashboard port: 8878, isolated from development 8877. macOS validation used 18879, not the user's app. No PLC endpoints were contacted. Release still requires Windows/Linux native acceptance, signing/distribution decisions, extended soak, field hardware/security acceptance and licence selection.

RTU preview update: pyserial 3.5 bundled/installed, with its licence notice. POSIX virtual-serial tests on macOS cover RTU client/server, coil and Float/CDAB values, monitoring/traffic, CRC corruption recovery, wrong-unit handling, port exclusivity/cleanup and RTU→TCP gateway scaling. Physical adapters, RS-485 timing/direction and Windows/Linux serial acceptance are pending. One active app role per port, one unit ID per serial simulator; no broadcast/ASCII/manual direction control/shared bus scheduling.

One-click RTU demo: POSIX virtual serial and Windows-style RTU-framed loopback modes tested on macOS. Start/stop, expiry, startup failure, concurrent/idempotent start and unchanged workspace are covered. Windows/Linux builds are prepared; native test execution is still pending. Native CI workflow is supplied but not executed.

Windows architecture preview.4: separate ARM64, x86 (32-bit), x64 archives use Python.org Python 3.13.16 embeddable runtimes, each SHA-256 checked against the official release page. Native wheels are resolved per architecture and PE headers checked. ARM64 uses aiohttp 3.14.5 / cryptography 46.0.3 / multidict 7.1.0 because matching wheels differ from x86/x64. That dependency set is regression-tested separately on macOS, not executed as Windows ARM64 here. Original official ARM64 runtime includes an unchanged x64 vcruntime140_1.dll companion; it is retained and explicitly recorded in BUILD_INFO rather than described as a mismatched app extension. All application extension modules must match the target. Native Windows acceptance for all three architectures remains pending.

For Windows preview.4, the older single-x64 restriction is replaced by PE-header checks for the executing/bundled runtime. ZIP roots are PW-Win-ARM64, PW-Win-x86 and PW-Win-x64 to reduce extraction path lengths. Install.bat and .cmd equivalents are included; incomplete extraction now produces guidance instead of a raw Errno 2. Check Runtime.bat captures native import results in Runtime_Check.txt. All three runtimes are newly assembled; no Windows binary was renamed to represent another architecture. x86 memory/load limits remain unqualified. App code and running Mac workspace are unchanged.
