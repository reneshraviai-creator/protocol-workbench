# Troubleshooting — Preview 11

| Symptom | Action |
|---|---|
| Errno 2 / missing asyncua file | Extract All to a short folder such as C:\PW. Never run inside the ZIP. Verify SHA256SUMS and use the exact architecture package. |
| Runtime requires 64-bit / wrong executable | Select ARM64 for ARM Windows, x64 for x64 Windows or x86 for 32-bit Windows. Run Check Runtime.bat; host CPU/VM naming alone is not proof of the runtime architecture. |
| Command file asks which app opens it | Use Install.bat from Command Prompt on Windows. .command is macOS; .sh is Linux. |
| Dependency import/install fails | Preserve console output, confirm supported Python 3.11–3.14 on macOS/Linux and approved PyPI network access. Windows uses the bundled offline runtime; do not mix external Python. |
| Port already in use | Stop the existing owner. Development port is 8877; packaged default is 8878. Custom installs can choose another port. Do not start a second writer against the same test equipment. |
| Sign-in succeeded but data/API access is blocked | Read licence information and click ACKNOWLEDGE & CONTINUE. It is required once per login session. Direct URL navigation does not bypass the gate. |
| Notice changed while reading | Reload/sign in again and acknowledge the current metadata snapshot. |
| Licence expired | Review the configured paper agreement date with the publisher. The warning does not disable features or require activation. |
| Cannot edit licence information | Enable authentication and sign in as Administrator. Other roles and unauthenticated local mode cannot edit it. |
| Forgot administrator password | Stop the app and use the installed release's app/security_recovery.py with its runtime and the data directory. See Security_Guide.html. Do not delete the security directory. |
| Write authorization expired | Inspect communication outcomes, stop/restart and review targets again. No automatic uncertain-write replay. |
| Rollback refused | Check that a previous release and matching data backup exist. Explicit data-restore confirmation is required. Do not fabricate paths/config metadata. |
| Need support evidence | Run Create Diagnostics; review the ZIP before sharing. No automatic upload occurs. Use separately reviewed report exports for device evidence. |

Stop all is the first app control for stopping test operations. Closing the browser leaves background work running; Stop App stops the service. Already transmitted writes are not undone. Keep original backups; avoid deleting user data as a troubleshooting shortcut. Windows/Linux native execution and physical serial acceptance remain pending until independently tested.
