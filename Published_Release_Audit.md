# Published release audit — Preview 14

Audited 30 October 2026. This remains an **Engineering Preview for local simulators and isolated test equipment**, subject to [permitted use](PERMITTED_USE.md). Passing these checks does not qualify production use or guarantee absence of defects.

## Results

| Target | Actual environment | Result |
|---|---|---|
| macOS | Apple Silicon, fresh isolated installation | PASS: 12 lifecycle/protocol checks |
| Windows x64 | GitHub Windows Server 2022 x64 runner | PASS: 14 checks, including published batch/PowerShell preflight |
| Windows on ARM | GitHub Windows 11 ARM64 runner, **x64 package through emulation** | FAIL: preflight/imports and local protocol self-tests passed, but final installation-folder rename returned WinError 5 (Access denied); unsupported |
| Linux x64 | GitHub Ubuntu 24.04 x64 runner | PASS: 12 checks |
| Linux ARM64 | GitHub Ubuntu 24.04 ARM64 runner | PASS: 12 checks |
| Linux ARM64 container | Official Python 3.13 slim image, normal container filesystem, capabilities dropped | PASS: 12 checks |

[Hosted run and logs](https://github.com/reneshraviai-creator/protocol-workbench/actions/runs/40916336854). [Machine-readable evidence](Published_Release_Audit.json) includes the package digests and each check's exit status. The manually triggered workflow has read-only repository access; it uses disposable test installations and localhost protocols.

The checks cover published archive download/hash/CRC, safe extraction and complete internal manifests, fresh installation, installed runtime validation with local OPC UA and Modbus, GUI bootstrap, background start/stop, upgrade with retained port/data and backup, rollback with matching data and preservation of later data, restart after rollback, and uninstall retaining data. The Windows checks additionally execute the published package preflight and bundled-runtime imports. These are automated lifecycle checks, not manual acceptance of every GUI action.

All **16 previously published assets** across Preview 11–14 were downloaded and checked against GitHub digests and sizes. Current installer archives also passed internal-file integrity checks. External ZIP checksum files match the archives. Original installer ZIP bytes were not changed by this audit.

Regression gate: **712 passed, 0 failed, 361 warnings** in the isolated Mac environment. The final test-environment key-case correction additionally passed the four focused audit tests. Application, protocol adapter and GUI code were unchanged.

## Failed attempts and correction

The first two hosted attempts failed both Windows targets at `Get-FileHash`, before installation. The audit runner inherited PowerShell 7's module path when launching Windows PowerShell. The first attempted cleanup missed Windows' uppercase environment keys. Removing that inherited variable case-insensitively fixed the test environment; the same published ZIP then passed on Windows x64. Windows ARM emulation passed preflight/imports and local protocol self-tests but failed installation at the final directory rename with `WinError 5: Access is denied`. Its cause has not been established; do not infer antivirus involvement or disable protection. Windows ARM emulation is unsupported in this release. No installer checks were bypassed and no package was modified. Previous runs: [first](https://github.com/reneshraviai-creator/protocol-workbench/actions/runs/40915265659), [second](https://github.com/reneshraviai-creator/protocol-workbench/actions/runs/40916127822).

The first Linux container attempt used a no-execute temporary mount and failed to map a cryptography library. Repeating on the normal container filesystem passed with the same archive and security restrictions. This fixture failure is not evidence of universal Linux support.

## Historical releases and limits

Preview 14 is the current installer release. Preview 11 and 13 installer downloads were withdrawn for previously recorded dependency/integrity issues; historical documentation and source tags are retained. Preview 12 has documentation/source only, with no packaged installer. GitHub's automatically generated historical source archives are not current installation recommendations.

The unsigned EXE remains withdrawn, with **no signed EXE replacement**. Existing historical EXE reports do not constitute an available signed installer. Remaining ZIP installers are unsigned. Native Windows ARM64/32-bit packages remain unavailable; x64 emulation also failed installation on the tested ARM runner and is unsupported.

Not established by this audit: Intel Mac compatibility, all Linux distributions/Windows editions, physical PLC or USB/RS-485 interoperability, long-duration endurance, every GUI workflow, security-product acceptance or independent security review. Use current versioned instructions and checksums. An acknowledgement of a protocol write does not prove physical-process delivery.
