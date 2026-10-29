# Windows EXE validation — Preview 14, installer revision exe1

Date: 30 October 2026. Engineering Preview. This is an unsigned single-EXE setup wrapper around the existing patched Preview 14 offline x64 application; it is not a native ARM64 application build.

## Included setup behavior

Standard setup wizard, Windows-version/architecture checks, explicit initially-unchecked experimental x64-emulation option on Windows 11 ARM64, per-user installation through the existing validated package installer, durable local control files, Start menu/optional desktop shortcuts and a Windows Installed apps uninstall entry. Optional app launch is unchecked by default. No service, scheduled autostart, security exclusions, execution-policy changes, serial drivers, online activation or runtime download is added. The uninstall recipe delegates program removal/data preservation to the existing application control tool.

## Actual evidence

| Check | Result |
|---|---|
| Unchanged source baseline regression | 697 passed, 0 failed; 118.24 seconds |
| New EXE-builder input validation tests | 11 passed |
| Full regression with EXE-builder source | 708 passed, 0 failed; 361 warnings; 118.50 seconds |
| Official NSIS compiler acquisition | Homebrew makensis 3.13 ARM64 bottle SHA-256 matched published formula metadata |
| NSIS compilation on Mac | PASS; valid Unicode Windows PE installer generated using standard zlib compression |
| Input ZIP external SHA/CRC/internal manifest/required files/version | PASS; 1,971 manifest entries |
| Actual bundled runtime PE target | x64, verified before compilation |
| EXE extraction with independently obtained sevenzip | PASS; official Homebrew bottle digest verified |
| Extracted package comparison | All 1,972 original files byte-identical to the verified input ZIP, including its manifest |
| Embedded setup guide / NSIS notices | Included and byte-verified |
| Personal publisher name/email and local build-path scan | PASS in extracted text payload; local build path absent from EXE raw/UTF-16 strings |
| EXE external SHA-256 | Generated and verified |
| PE certificate table | Empty: UNSIGNED |
| Native Windows wizard/install/shortcuts/uninstall | NOT TESTED |
| Windows 11 ARM64 x64 emulation on Parallels | NOT TESTED; explicit experimental UI option only |
| SmartScreen/antivirus/EDR acceptance | NOT TESTED; no guarantee of acceptance |
| Physical PLC/USB serial hardware | NOT TESTED in this change |

The builder rejects changed outer/internal hashes, escaping paths, incomplete offline packages, non-x64 PE payloads and inconsistent version identity before invoking the compiler. Build/static checks cannot prove the intended Windows wizard/lifecycle behavior. No real Windows or VM installation has been claimed. No running development app or saved project was changed. Original Preview 14 ZIP assets remain unchanged.

## Reproducibility and licence scope

Source recipe and builder are under source/packaging/windows-exe and source/packaging/build_windows_exe.py on main. The Preview 14 source tag predates this EXE wrapper; main contains the new packaging source. Compiler hash, NSIS source hash, EXE hash and input ZIP hash are in EXE_Build_Info.json / EXE_Payload_Verification.json. NSIS and Modern UI notices are preserved separately from the Workbench evaluation notice.

Installer technology: NSIS 3.13, official Homebrew formula https://formulae.brew.sh/formula/makensis . Windows 11 supports x64 emulation generally: https://learn.microsoft.com/en-us/windows/arm/apps-on-arm-x86-emulation . That platform capability is not evidence that this particular application has passed on ARM.

Use only local simulators and isolated test equipment. Native Windows x64 and Windows 11 ARM64 acceptance, signing and security-product investigation remain release gates. Do not bypass operating-system security warnings or restore quarantined files without investigation.
