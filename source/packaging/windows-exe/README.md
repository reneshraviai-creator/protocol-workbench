# Build the Windows EXE

This NSIS recipe wraps a verified offline Preview 14 x64 archive; it does not convert the runtime to ARM64. Build with NSIS 3.13 (native compiler on macOS, Linux or Windows). NSIS includes the Windows stubs/plugins required to cross-compile an EXE. Keep upstream NSIS notices with your compiler distribution; third-party payload notices remain inside the archive.

From the repository root, using your own paths:

```sh
python source/packaging/build_windows_exe.py \
  --archive /path/to/ProtocolWorkbench-2.0.0-preview.14-Windows-x64.zip \
  --compiler /path/to/makensis \
  --nsisdir /path/to/share/nsis \
  --output /path/to/output
```

The matching `.zip.sha256` must be next to the input archive. The build checks its digest, CRC, path safety, complete internal manifest, required files, actual x64 runtime PE header and version identity before invoking the compiler. Outputs: Setup.exe, its SHA-256 file and EXE_Build_Info.json. No signing certificate is supplied; no signing or native Windows acceptance is claimed. The compiler uses a temporary staging directory only; it does not access the existing application/data.

Installer behavior: current-user install, 64-bit Windows checks, explicit experimental opt-in for Windows 11 ARM64 x64 emulation, durable control files, Start menu/optional desktop shortcuts and a matching uninstall entry. The application's existing installer performs package/runtime validation, upgrade staging, backup and local protocol diagnostics. App launch is optional; no service/autostart is registered. ARM silent install is refused.

Before public promotion, test real Windows x64 and Windows 11 ARM64 installation, cancellation, shortcut launch, port preservation, upgrade, runtime-check failure, uninstall/data retention and antivirus handling. Extraction/static checks cannot establish those behaviors.
