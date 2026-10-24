# Protocol Workbench — private development archive

Current source snapshot: **2.0.0-preview.12**. Source-server-to-simulation import; superseded by Preview 13 dependency remediation. No installer archives were built for Preview 12.

This is a private, clean export of selected application source checkpoints. Original local Git history, user projects, accounts, credentials, recordings, backups, private engineering evidence and screenshots are excluded. Third-party notices are retained. This repository is not a public open-source release.

## Versions

| Tag | Description |
|---|---|
| v2.0.0-preview.11 | Legacy package baseline; unresolved dependency findings |
| v2.0.0-preview.12 | OPC UA server import and independent local simulation |
| v2.0.0-preview.13 | Security refresh, revised packages and provisional specifications |

All versions remain Engineering Preview. Zero matches in a dated PyPI advisory scan is not a whole-system security certificate. Native Windows/Linux testing, physical devices, endurance and other release gates remain outstanding. Windows ARM64/32-bit legacy packages are not patched or recommended for new installations.

## Run or test source

Use a dedicated virtual environment with a compatible Python version. From `source`, install `requirements.txt`; run `python app/launcher.py start` for the local GUI, or install pytest and run `python -m pytest -q` for isolated tests. Do not connect saved production endpoints as part of tests. Fresh source defaults create a new data directory; no original user's data is included.

Installable archives, checksums, guides and version comments are attached to the matching GitHub prereleases. Preview 12 has source only. No automatic GitHub Actions workflows are enabled by this export. Historical preview.11 dependencies retain known advisory findings.

`ProtocolWorkbench/licenses` supplies the existing shared third-party notices required by the package builders' sibling-directory layout. See the included software guides and notices for their scope. Preserve the private source and local runtime separately when testing another version.
