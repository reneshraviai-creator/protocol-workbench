# Recommended system specifications — provisional

These are engineering sizing recommendations for approximately 5,400–6,500 scalar tags at two-second updates. They are not measured minimums, certified platform support, a real-time guarantee or a promise that all configured limits can run simultaneously. Local 6,500-tag tests passed; sustained 20,000-tag, Windows/Linux native and physical-device qualification remain pending.

| Component | Recommended specification |
|---|---|
| CPU | Modern four-core processor or Apple Silicon |
| RAM | 16 GB recommended; 8 GB for lighter testing |
| Storage | SSD with 10 GB free; allow more space for recordings, reports, backups and upgrades |
| Architecture | 64-bit x64 or ARM64; package availability below is a separate gate |
| Network | Reliable wired Gigabit Ethernet recommended for external test devices |
| Browser | Current Edge, Chrome, Firefox or Safari |
| Graphics | Integrated graphics; no dedicated GPU required |
| Modbus RTU | Compatible USB-to-RS-485 adapter, operating-system driver and correct wiring/serial settings |

## Operating systems and runtime

- Windows x64: bundled CPython 3.13.16 and pinned runtime libraries; offline installation. Windows native acceptance is NOT TESTED on this build machine.
- macOS: existing CPython 3.11–3.14; installer creates a private virtual environment and needs internet to download pinned dependencies. ARM64 native qualification is recorded separately; Intel macOS remains untested.
- Linux 64-bit: existing CPython 3.11–3.14 with venv support and internet for installation. Linux x64/ARM64 wheel resolution checks are not native execution or distribution compatibility qualification.
- Native Windows ARM64 and 32-bit: this security refresh is BLOCKED because an official cryptography 50.0.2 wheel is unavailable for those architectures. A specification recommendation does not make the old affected packages safe. No rebuilt ARM64/32-bit installer is supplied in this refresh.

For a Windows VM, a starting allocation is four virtual CPUs and 8 GB RAM, with sufficient resources remaining for the host. Match binaries to the guest OS architecture. An ARM64 Windows guest on Apple Silicon does not qualify as tested by running the native macOS application. A patched native ARM64 package needs a compatible dependency build and separate Windows qualification; x64 emulation is not qualified here.

## Capacity and connectivity

The software configuration limit is 20,000 tags per connection. The tested end-to-end import/readback workload is 6,500 tags. Update rate, security, String size, recording, number of monitors and concurrent servers change resource use. Do not multiply the 100-connection configuration limit by 20,000 and treat the result as validated capacity. Authenticated write-operation limits apply separately.

The packaged dashboard normally uses http://127.0.0.1:8878/. A local imported OPC UA simulator can use opc.tcp://127.0.0.1:15440, with another free port for each concurrent listener. External targets require a working route and permitted protocol ports. Source-server and PLC capacity limits may be lower than Workbench's configuration limit. Read only or simulate against local/isolated test equipment.

No internet is required for ordinary local operation after installation. Optional installation downloads and explicit dependency advisory checks require internet. Reports, recordings and backups need additional disk space; retention must match the test duration.
