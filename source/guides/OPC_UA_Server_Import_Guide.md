# Create a simulation from an OPC UA server

Available in Preview 12. This is a read-only import of supported application tag definitions and an optional starting-value snapshot. It does not copy PLC programs, methods, events, alarms, security configuration or a complete namespace/address space.

## Create the simulation

1. Open **Dashboard → Quick paths → Create simulation from OPC UA server**, or use the same button in **Scenarios**. **Tags & registers** also offers **Create simulation from this server** for the selected OPC UA connection.
2. Choose an existing OPC UA connection, or enter a source URL. Expand **Security and authentication** for credentials and certificate paths. These paths refer to files on the machine running Workbench. Authenticated Workbench mode requires SignAndEncrypt for username/password access. Source reading uses existing certificate/vault controls.
3. Click **Browse server**. Progress shows discovered variables and supported tags. Closing the window cancels browsing and closes the source session. Browsing is bounded to five minutes.
4. Choose a folder (including descendants), search by name/path/datatype/NodeId and check individual tags. Selection survives filters and 100-row pages. **Select filtered tags** adds every matching tag, not just the visible page. **Select all discovered tags** adds the complete supported inventory. **Clear selection** clears every selection.
5. Expand browse issues to review skipped arrays, structures, inaccessible metadata or limits. Supported scalar children below structured variables are still discovered. Duplicate names are disambiguated with their source NodeId. Counts cannot include nodes that the source account is not allowed to discover.
6. Click **Configure simulator**. Choose a name, a free loopback URL with port 1024–65535, interval 0.1–60 seconds and run duration 1–3600 seconds. Example: `opc.tcp://127.0.0.1:15440`. The source's local port cannot be reused. Endpoint availability is checked when the scenario runs; save does not reserve a port.
7. Optional: enable **Read current values as a starting snapshot**. Values are read in batches, so this is not an atomic process snapshot. Non-good quality, nonfinite/out-of-range values and values outside supported limits use datatype defaults, with a count and issue list on Review. A connection/service failure aborts review rather than silently fabricating a successful snapshot.
8. Optional: enable **Generate changing values**. Numeric points use sine 0–100, Booleans toggle, DateTime points follow current time and strings remain constant. Otherwise values remain frozen. Without a snapshot defaults are zero/false/empty text/2000-01-01 UTC. These are synthetic defaults, not an approximation of the PLC program. For other per-tag patterns, use **Open in workspace** on a saved scenario (review workspace replacement first), then **Tags & registers → Edit table & simulation**. Create a new scenario from that edited workspace configuration; existing saved scenarios do not automatically follow workspace edits.
9. If browsing was incomplete or skipped items, explicitly acknowledge those issues. Click **Build review**, inspect copied/default counts and local NodeIds, and optionally download the full source-to-local mapping JSON. No source endpoint, password, account or certificate is retained in the new scenario. The mapping contains source tag paths and NodeIds; treat it as engineering configuration information when sharing.
10. Check the review confirmation and click **Save stopped scenario**. The scenario appears in Scenarios. Nothing is started and the current workspace is not replaced.
11. When ready, click **Run test** on the saved scenario. It starts the local server, waits the configured duration, reads all selected points and stops its own server. In authenticated mode normal write/start reviews still apply. A separate application can connect to the local endpoint while the scenario runs. The listener is loopback-only; it is not exposed to another computer by this workflow.

## What is preserved and what changes

Supported scalar datatypes: Boolean, SByte, Byte, Int16, UInt16, Int32, UInt32, Int64, UInt64, Float, Double, String and DateTime; resolvable custom aliases to supported scalar base types are accepted. Large 64-bit integers are represented as strings in JSON to preserve precision. Arrays and structure-valued variables are not cloned as values. Descriptions, engineering units, original quality/timestamps and source access flags are not copied; the new simulator exposes locally writable supported variables.

Source identities are remapped to `ns=2;s=<unique tag name>` in Workbench's simulator namespace. Variables are served under the existing **Simulation** folder; the original folder hierarchy is preserved in the downloaded mapping, not recreated in the simulator. Do not reuse the source's namespace index as though it identified the local server's namespace URI.

The workflow traverses hierarchical Object/Variable children beneath Objects, excludes the standard Server subtree and namespace-zero variables, deduplicates repeated/cyclic references, consumes BrowseNext continuation points, and inspects metadata in bounded batches. Maximums: 20,000 supported tags, 25,000 browsed/pending nodes, depth 32, 100,000 references. Hitting a limit produces an explicit partial-inventory warning. Up to 100 issue details are shown, with a full observed issue count. No claim of discovering every inaccessible server tag is made.

Imports are session-owned, memory-only and expire after 20 minutes (cleanup runs every 30 seconds). Up to two inventories browse concurrently, with four retained sessions. Closing the wizard releases its inventory. Restarting Workbench discards unfinished imports; credentials and draft snapshots are not written to recovery files. In authenticated mode only Administrators/Engineers can create imports and simulations; another login session cannot retrieve an inventory.

## Troubleshooting

- **Browse failed:** verify the endpoint and permissions through Connections → Test; review required policy/certificates. The importer never writes source values to test access.
- **Missing tags:** review account access, arrays/structures, namespace-zero exclusions and depth/count/time limits. Import a smaller application model or use explicit CSV definitions where needed.
- **Defaults instead of current values:** select the snapshot option before Build review; inspect bad-quality/default warnings. Read permissions and value validity are distinct from readable metadata.
- **Import expired:** return to Source and browse again. No existing scenario is altered.
- **Port already used / scenario fails to start:** change the local endpoint in the saved scenario or create it again with an unused port. Stop other listeners through their normal controls.

This feature remains Engineering Preview. Phase 12 security/native-platform/endurance release blockers are unchanged. Existing Preview 11 distribution archives do not include this feature and have not been rebuilt.
