# User guide — Engineering Preview 11

Open the application with Open App; it runs in the background on loopback port 8878. Use Stop App to stop the service. Never expose its HTTP port through a reverse proxy or use it with an operating process.

Choose a Dashboard task: Monitor a device, Simulate a device, Connect two protocols or Run a test. Existing detailed guides cover OPC UA browsing/subscriptions, Modbus TCP/RTU scanning/simulation, mixed tag generation, signal-path verification, bridges, recovery, performance and reports. Next_Guide.html links the engineering workflows.

Configure endpoints in Connections, tags in Tags & registers and jobs in Simulation & routes. Review destinations before running. Generated tags do not create variables in a PLC program. A successful write acknowledgement does not prove physical process delivery. Stop all cancels app operations but cannot undo writes already transmitted.

Projects holds explicit named copies. Capture a saved workspace before replacing it; Open requires idle resources and disables autostart. Update from workspace appends a library revision. Portable backups omit passwords and certificate/key paths. Refer to Project_Management_Guide.html for restore/migration limits.

Authentication is optional and OFF until the owner creates an Administrator in Security & access. Once enabled, each sign-in displays licence information and requires ACKNOWLEDGE & CONTINUE. This records receipt only; it is not contract acceptance or activation. The separately issued paper agreement governs licensing. A missing/expired licence-information field does not disable features after acknowledgement. The notice returns at the next login, not repeatedly during the same session.

Project & settings → About → Licence Information shows the installed version, organisation/reference/type/dates, publisher and contact. Administrators can edit metadata. Runtime software name/version are not editable. Expiry produces an informational banner; normal operation remains available. No licence keys, payment connection or online validation exist.

Authentication-enabled writer leases expire after five minutes and limits apply; inspect Security_Guide.html. Read-only roles can observe but cannot configure/run writers. Reports distinguish PASS, FAIL, INCONCLUSIVE and NOT TESTED. The application remains Engineering Preview; validate actual device/OS behavior in an isolated environment.
