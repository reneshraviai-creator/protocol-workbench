# Protocol Workbench

**An integrated graphical workbench for OPC UA and Modbus simulation, inspection and automated engineering tests.**

Protocol Workbench brings a local browser interface and Python protocol services together in one application. Engineers can configure connections, create tags and registers, simulate test devices, monitor values, move data between endpoints and save repeatable test scenarios. It is intended for local simulators and isolated test equipment, not production or live operating environments.

**Current packaged version: 2.0.0-preview.13 — Engineering Preview.** This private repository contains clean source checkpoints, downloadable archive installers, guides and recorded validation summaries. It is not a certified commissioning product or a public open-source release.

## What an engineer can do

| Area | Included workflows |
|---|---|
| Graphical workspace | Dashboard task paths, connection configuration, tags and registers, simulation/routes, projects, settings and engineering guides. The GUI opens in a local web browser. |
| OPC UA client | Connect to configured servers, browse their address space, inspect/read values, select nodes, use subscriptions/watchlists and write supported values to writable nodes. Server permissions and endpoint security still apply. |
| OPC UA simulation | Create a local server with engineer-defined tags and configurable dynamic values. Choose supported data types and simulation patterns rather than relying on a fixed PLC-specific sample. |
| Import an OPC UA server | Inventory a source server; filter by folder/name/type/NodeId; choose individual, filtered or all discovered tags; optionally snapshot initial values; create a separate, initially stopped local simulation with remapped NodeIds. |
| Modbus scanning | Configure Modbus TCP or serial RTU connections, inspect coils/registers and perform supported reads/writes with the appropriate address and data interpretation. |
| Modbus simulation | Configure simulated Modbus devices/registers for isolated client testing. RTU operation requires a compatible serial adapter, driver and matching serial settings. |
| Tag engineering | Create individual or bulk tags, configure mixed supported scalar types and value patterns, and manage the tags used by jobs. Creating a Workbench tag does not create a variable in a PLC program. |
| Protocol routes | Configure source/destination mappings for supported OPC UA and Modbus workflows, with conversion/settings described in the bridge guide. |
| Repeatable testing | Save scenarios, configure automation and verification, observe outcomes and inspect reports. Results distinguish PASS, FAIL, INCONCLUSIVE and NOT TESTED. |
| Communication diagnostics | Connection health, event logs, recovery/reconnect behavior and performance views help identify interruptions and unsuccessful operations. |
| Projects and reuse | Capture named project copies, retain revisions and use portable backup/export workflows. Review restore restrictions before replacing a workspace. |
| Recording and reporting | Record test activity and generate engineering reports and diagnostics for review. Storage requirements grow with duration and activity. |
| Access controls | Optional local authentication, roles, credential-vault facilities and authenticated writer controls. Authentication is not enabled until an administrator is created. |
| Installation administration | User-local installation, start/stop/status, isolated installation validation, diagnostics, upgrade preparation and rollback to a previous release with its matching data snapshot. |

This is a unified engineering workspace with Python services behind its GUI. It is not a copy of UAExpert, ModScan or ModSim, and this description does not claim complete feature parity or their certification/qualification.

## Example workflows

1. **Test without a PLC:** create a local OPC UA or Modbus simulator, add tags/registers, select dynamic patterns, start it and monitor the changing values.
2. **Reproduce an OPC UA address space:** open Scenarios → Create simulation from OPC UA server, browse/select nodes, optionally capture starting values and create a separate local simulation. Review it before starting.
3. **Check an isolated device:** create the correct connection, browse/read the intended addresses, confirm data types and access rights, then perform an explicitly configured write or automated test.
4. **Test a protocol mapping:** configure the source, destination and mappings, run the route against isolated endpoints, and inspect destination readback and reports.
5. **Repeat a test:** save the project/scenario, run verification and retain the report so another engineer can inspect the setup and outcome.

An imported simulation reproduces supported tags and optional starting values. It does not clone PLC logic, methods, every server behavior or the original server's identity/security configuration. Source credentials are not copied into the generated simulator.

## Versions and downloads

Open the matching release and expand **Assets** to download its packages, checksum files and documentation. GitHub also supplies source ZIP/tar archives for each tag.

| Version | Contents | Availability |
|---|---|---|
| [Preview 13](https://github.com/reneshraviai-creator/protocol-workbench/releases/tag/v2.0.0-preview.13) | Security refresh, Preview 12 OPC UA import, revised packages and provisional hardware/VM specifications | Windows x64, macOS and Linux packages; preferred available preview |
| [Preview 12](https://github.com/reneshraviai-creator/protocol-workbench/releases/tag/v2.0.0-preview.12) | Source-server browsing, tag selection, optional snapshots and independent local simulation | Source checkpoint and import guide only; no installer archives built |
| [Preview 11](https://github.com/reneshraviai-creator/protocol-workbench/releases/tag/v2.0.0-preview.11) | Historical package baseline, including native Windows ARM64 and 32-bit archives | Legacy archive with unresolved dependency findings; not recommended for new installations |

### Platform status for Preview 13

| Target | Package/runtime | Recorded qualification |
|---|---|---|
| Windows x64 | Offline ZIP with bundled architecture-specific Python and dependencies | Static package checks passed; native Windows execution not tested |
| macOS | ZIP installer; existing Python 3.11–3.14; internet for pinned dependency installation | Native Apple Silicon install/start/stop, upgrade and rollback checked; Intel Mac untested |
| Linux | ZIP installer; existing Python 3.11–3.14 and venv; internet for installation | Static checks and x64/ARM64 dependency-wheel resolution passed; native execution not tested |
| Windows ARM64 | No refreshed Preview 13 package | Blocked by unavailable official cryptography 50.0.2 native wheel |
| Windows 32-bit | No refreshed Preview 13 package | Blocked by unavailable official cryptography 50.0.2 win32 wheel |

Windows x64 emulation on ARM is not qualified. The Preview 11 ARM64/32-bit archives remain historical and must not be mistaken for patched Preview 13 builds. All packages are unsigned archive installers, not MSI, DMG or DEB installers. Folder names containing “production release” are distribution labels, not production approval.

## Installation overview

Always download the package for the actual operating-system architecture, extract the entire ZIP and read the included installation guide. Do not run scripts from inside a compressed ZIP or its temporary preview directory.

- **Windows x64:** extract to a short path such as `C:\PW`; run `Check Runtime.bat`, then `Install.bat`, then `Open App.bat`. No separate Python installation is required by this package.
- **macOS:** install/select Python 3.11–3.14, open Terminal in the extracted package, run `sh Install.command`, then `sh "Open App.command"`.
- **Linux:** provide Python 3.11–3.14 with venv, open a terminal in the extracted package, run `sh Install.sh`, then `sh "Open App.sh"`.

The packaged dashboard normally opens at **http://127.0.0.1:8878/**. A separately running development instance may use a different port. Closing the browser does not stop the service: use the supplied Stop App script. Installation validation uses isolated loopback checks and does not test your real PLC or serial wiring.

Default installations are user-local: Windows LocalAppData/ProtocolWorkbenchCommunity, macOS ~/Library/Application Support/ProtocolWorkbenchCommunity, and Linux $XDG_DATA_HOME/ProtocolWorkbenchCommunity or ~/.local/share/ProtocolWorkbenchCommunity. Review upgrade/rollback instructions before modifying an existing installation; rollback restores a release together with its matching saved data, not an arbitrary merge of later changes.

## Capacity and recommended resources

For approximately 5,400–6,500 scalar tags at two-second updates, the provisional recommendation is a modern four-core CPU/Apple Silicon, 16 GB RAM, an SSD with at least 10 GB free and wired Gigabit Ethernet for external test devices. Lighter work may use 8 GB RAM. A starting VM allocation is four vCPUs and 8 GB RAM while leaving resources for the host. A current browser and integrated graphics are sufficient; RTU requires a suitable USB-to-RS-485 adapter/driver and correct wiring.

The configured limit is **20,000 tags per connection**; the recorded end-to-end local workload is **6,500 tags**. These are different measures. Sustained 20,000-tag operation and the product of connection/tag limits are not validated capacity. Update rate, security, string sizes, concurrent jobs and recording affect actual load; PLC/server limits may be lower.

## Validation and limitations

Preview 13 recorded **688 automated tests passed, 0 failed** in the patched Mac environment. A synthetic local 6,500 Float-tag browse → snapshot → independent simulator → full readback test passed. Native Mac installation, Preview 11-to-13 project/scenario preservation and matching-data rollback were checked. These results are scoped evidence, not a guarantee of bug-free operation or real-time delivery.

Preview 13 pins aiohttp 3.14.5 and cryptography 50.0.2. Dated OSV checks returned zero PyPI advisory matches for the assessed dependency targets. This does not cover every OS/CPython/system OpenSSL vulnerability or certify security. Preview 11 retains known dependency findings.

Outstanding gates include native Windows/Linux execution, Intel Mac, physical PLC/serial interoperability, long-duration endurance and independent acceptance/security review. A successful protocol write acknowledgement alone does not prove physical-process delivery. Stop cancels further application work but cannot undo a write already sent.

## Documentation

| Guide | Purpose |
|---|---|
| [Installation](source/packaging/release-docs/Installation_Guide.md) | Platform selection, extraction, install, validation, upgrades and rollback |
| [User guide](source/packaging/release-docs/User_Guide.md) | Main workflows and application navigation |
| [Troubleshooting](source/packaging/release-docs/Troubleshooting_Guide.md) | Installation/runtime problems and diagnostics |
| [System requirements](source/packaging/release-docs/System_Requirements.md) | Provisional sizing, VM guidance and capacity caveats |
| [OPC UA import](source/guides/OPC_UA_Server_Import_Guide.md) | Browsing, selecting and importing source-server tags |
| [Administrator guide](source/packaging/release-docs/Administrator_Guide.md) | Local administration, access and maintenance |
| [Release history](source/packaging/release-docs/Version_History.md) | Changes between previews |

Additional HTML guides under `source/guides/` cover bridges, mixed tags, scenario automation, projects, communication/data verification, security, performance, recording and reports. Download/open HTML locally; GitHub's source viewer does not render it as an application page. Documentation ZIPs are attached to the packaged releases. Some inherited documents retain a historical heading; check the versioned release notes and qualification report for current status.

## Source layout and development

| Path | Role |
|---|---|
| `source/app/` | Application services, GUI assets, protocol drivers, simulation, automation and reporting |
| `source/tests/` | Regression and isolated test coverage |
| `source/packaging/` | Package preparation, runtime checks and installer/control tooling |
| `source/guides/` | Detailed HTML/Markdown engineering guides |
| `source/examples/` | Supplied examples |
| `ProtocolWorkbench/licenses/` | Shared third-party notices used by package builders |

To run from source, use a compatible Python and an isolated environment. From the repository root:

```sh
python3 -m venv .venv
# macOS/Linux:
. .venv/bin/activate
# Windows Command Prompt instead: .venv\Scripts\activate.bat
cd source
python -m pip install -r requirements.txt
python app/launcher.py start
```

Read the launcher output for its actual local URL. For regression tests in a disposable environment, install pytest and run `python -m pytest -q` from `source`. Follow individual fixture requirements; never substitute a real production endpoint for an isolated test fixture. No automatic GitHub Actions workflows are enabled in this export.

## Privacy, ownership and distribution

This is a **private clean export**. Original local Git history, personal runtime projects, accounts, credentials, recordings, backups, private engineering evidence and screenshots are excluded. The version tags preserve selected clean checkpoints; they do not expose the original development repository history.

Private GitHub access is not code encryption: anyone granted repository access can read/download the included source. No new public/open-source licence is granted by this repository. Preserve included third-party notices; repository privacy does not override dependency licence obligations. Review the existing licensing materials before external redistribution.
