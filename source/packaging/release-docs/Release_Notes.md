# Release notes — 2.0.0-preview.13

Engineering Preview: dependency remediation and refreshed package preparation, not a commercial readiness certificate.

Updates aiohttp from 3.13.3 to 3.14.5; consistently requires cryptography 50.0.2. Exact runtime dependency locks and package/runtime metadata checks prevent reusing older affected payloads. Advisory results and native acceptance are recorded in the accompanying qualification report, with explicit scan scope and date.

Includes Preview 12's OPC UA server-to-simulation import, all existing protocol/GUI adapters, licence information and project management. Recommended hardware and VM sizing are now included in HTML and editable guides as provisional recommendations.

Prepared package targets: Windows x64 offline runtime; macOS and Linux online virtual-environment installers. Native Windows ARM64 and win32 are BLOCKED: official cryptography 50.0.2 wheels are unavailable. No old affected package is relabelled as patched. No emulation qualification is claimed. Default packaged dashboard port 8878 stays separate from development 8877.

Archives are unsigned, not MSI/DMG/DEB images. Native Windows/Linux, physical PLC/RTU, long-duration endurance and independent security acceptance remain outstanding. A zero-match PyPI advisory scan is not a full-system vulnerability certificate. Current running development app and old distribution archives are unchanged. No publishing or main application upgrade was performed.
