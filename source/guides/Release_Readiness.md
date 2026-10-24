# Community preview release gates

This build remains a local engineering preview. A passed loopback test does not qualify field devices, every security policy, or target-platform installers.

| Gate | Evidence / remaining work |
|---|---|
| Regression | See Test_Results.txt; rerun after each functional change. |
| 6,500-point OPC UA | Local explicit Float NodeIds, None security, server + client on one Mac. Reported timing includes writer and subscription delivery. See qualification JSON files. |
| Connection recovery | Qualification stops/restarts its own server, checks retained disconnected values and all fresh restart values. |
| App crash/restart | Isolated subprocess test kills only the test app; project survives and unfinished run becomes ERROR with no automatic replay. New local authentication token is issued. |
| Extended soak | Short sustained qualification provided; multi-hour/overnight runs, memory-growth profiling and repeated network fault injection remain. |
| Hardware | Siemens PLC tag limits, server revisions, security/certificates, real LAN failures and Modbus gateways require hardware acceptance. |
| UI | Core workflows exercised. Large-monitor browser responsiveness, accessibility and final visual consistency need release acceptance. |
| Windows | Build and install/uninstall on a clean Windows machine; check background lifecycle, firewall prompts and paths with spaces. No Next native installer qualified yet. |
| Linux | Build and install/uninstall on the selected distributions; qualify startup and desktop integration. No Next native installer qualified yet. |
| macOS | Build signed/notarized distribution and test install/uninstall on clean supported systems. Current development launch is not a signed installer. |
| Licensing | Review dependency notices and choose the project's community licence before publishing. |
| Distribution | Version metadata, checksums, installation/user/troubleshooting guides and release notes must accompany qualified packages. |

## Repeat the OPC UA check

Use the project's Python environment with requirements installed:

```text
python tools/qualify_opcua.py --points 6500 --cycles 60 --output qualification.json
```

This binds one automatically selected loopback port. It never reads the user's workspace data directory or connects to a PLC. It starts/stops its own server, writes deterministic Float values, verifies every subscribed point, checks reconnection and writes a JSON report. The default maximum wait per stage is 120 seconds. Per-point history is bounded; this is not a process-memory leak assessment. Increase cycles for a longer run. Stop with Ctrl+C; a partial result is written when normal cancellation cleanup runs. A forced process termination may prevent that report.
