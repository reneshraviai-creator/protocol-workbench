# Preview 15 dev.3 — source release

**Version:** 2.0.0-preview.15-dev.3 · **Classification:** Engineering Preview, source-only · **Date:** 31 October 2026.

This release updates the graphical engineering workspace and developer handover. It is for local simulators and isolated non-production test equipment. It is not a production-ready or certified commissioning tool. Existing Preview 14 installers remain separately available and do not include this UI.

## Included changes

- Dark navy/teal interface with illustrated device cards, visible endpoints, running states and device-focused controls.
- Architecture presentation views select a subset of devices and their configured relationships; hidden devices/jobs are not stopped merely because a view excludes them.
- Device context carries through tags, monitoring and Back navigation. Refresh and browser Back/Forward retain page/device/tab selection without automatically starting protocol I/O.
- A dedicated running-activity view links jobs, monitors and test runs to their owners and endpoints.
- Device dependency review links to jobs and related devices before removal. Reusable templates and saved test definitions have clearer lifecycle/removal paths.
- Guided bridge creation selects source/destination devices, previews matching or individual tag pairs and keeps timing/conversion settings optional. Existing advanced mapping tools remain available. Conflicting writers remain blocked.
- Engineer role configuration controls match the existing backend permissions. Pause/Stop controls follow job state; recording/export/monitor controls require appropriate monitor/recording state.
- Named job and test-run selectors reduce the need to enter internal IDs in reports and verification.
- Simulator setup supports explicit continuous/timed operation, uses the saved interval default, suggests an unused configured TCP port and hides Modbus Unit ID for OPC UA.
- Tag deletion has named accessible labels; loading a saved test into the workspace is explicitly labelled as replacement.
- OPC UA, Modbus TCP/RTU, mixed signal generation, scenario automation, evidence/verification, watchlists, recording, recovery and project features are retained.

## Source and development

Application code is in `source/app`, tests in `source/tests`. Read [the development handover](../DEVELOPMENT_HANDOVER.md) for setup instructions, module ownership, architecture, regression commands, known constraints and the agreed UX improvement backlog.

From `source`, after creating an isolated environment and installing `requirements-dev.txt`:

```sh
python -m pytest tests -q
python tools/run_ui_regressions.py
```

Node.js is required for the UI regression runner. Tests use isolated loopback servers; allow local sockets. Use a separate data directory and alternate port for development. Do not point automated tests at operating PLCs.

## Validation scope

The local development/export snapshot passed 729 Python tests and all four Node UI regression files. Browser checks covered device switching/Back, selected-device monitor setup, empty-monitor controls, refresh and navigation history, bridge preview/conflict rejection and named report selectors.

Publication uses the existing public dependency pin (`aiohttp==3.14.5`) and retains prior public installer/security checks rather than downgrading them to the local development baseline. The final combined public-source run passed **760 tests, 379 warnings in 125.97 seconds**, using macOS ARM64 with the existing public security-test environment (`aiohttp==3.14.5`, pytest 9.1.1). All four Node UI regression files also passed. A fresh isolated public-source instance started on port 8892, returned the expected bootstrap version and was stopped cleanly. This is local execution evidence, not native Windows/Linux acceptance.

## Known limitations and next work

- The editable bulk tag table remains dense. Guided setup is not yet consistent across every feature; template/profile consolidation and explicit keep/discard draft choices need further work.
- The next UX priority is a connected create-simulator → start → connect-scanner → live-values journey with contextual next actions and one consistent review/completion pattern.
- Device illustrations and animations are not physical equipment models or proof of communication. Acknowledged writes are not proof of physical delivery.
- 20,000 tags per connection is a configuration limit, not a universal performance guarantee. Protocol addressing, serial speed, target capacity and concurrency still constrain useful rates.
- No new Windows ARM64/x86, Windows x64, Linux or macOS installer was built or qualified for this source release. No signed installer is supplied.
- Local regression/browser results do not certify field reliability, physical RS-485/PLC behavior, hardware compatibility, cross-platform execution or commercial usability.
- The GitHub publisher identity and current public permitted-use terms remain in place. No open-source licence or new redistribution grant is introduced.

Private data, live catalogues/databases, customer endpoints, personal publisher defaults, original development Git history and the private AI handover ZIP are excluded from publication.
