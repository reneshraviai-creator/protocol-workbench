# Visual device workspace

The local development interface groups the existing engineering functions around devices. Existing configurations, protocol adapters and test snapshots are retained. This interface change does not update published installers.

## First simulator and readback

1. Overview → Create simulator. Select an appearance/area and OPC UA, Modbus TCP or RTU.
2. Enter the endpoint, point count, pattern and interval. Choose continuous operation or a duration.
3. Create device saves the device and job stopped. Open Simulation & jobs, or use the persistent Start control.
4. Live values shows generated job samples; these are distinct from protocol acquisition.
5. Connect a local scanner copies the known definitions of this workspace's simulator into a new stopped endpoint. Modbus does not generally discover external register maps.
6. Start read-only monitor on the scanner. Values, quality, age and received-update counts are acquired through the actual protocol. The overview links the scanner to its source.
7. Open trends/recording for the full inspector. Stop monitor and Stop simulation are separate actions.

RTU server and scanner need different paired serial ports/adapters. The app does not create virtual ports or install drivers. A simulator can run without clients; running is not proof of communication.

## Where features moved

- Overview: device illustrations, endpoint details, runtime state, scenario-owned devices, actual scanner/gateway relationships, presentation and motion controls.
- Devices: choose a device for connection, samples/acquired values, jobs, tags and dependencies. Tag creation/import/browsing, mixed types, groups, bridges and verification remain linked here.
- Tests & templates: all previous scenario automation and portable templates.
- Results: run comparisons, expected/actual results and reports.
- Project library: existing project saving/versioning/backups.
- Engineering tools: original tag editor, simulation jobs/routes, full live inspector, bridge mappings, communication/data verification, reports, health, performance and activity.
- Settings & security: project settings and all original authentication/certificate/write-consent tools.

Back to device workspace returns from detailed tools. The selected endpoint remains in context. Existing project save/draft recovery and role enforcement remain in place.

## Device appearance and presentation

Choose from 39 device illustrations grouped under Common, Data centre, Pharma and Manufacturing. An illustration does not add a vendor program, physical/process model or regulatory qualification. Existing tags and job settings define the values.

Presentation mode hides editing/navigation chrome but retains the isolated-test-equipment notice and endpoints. Pause animation and reduced-motion preferences disable motion without stopping communication. Paths represent configured scanner-source references or gateway jobs, not inferred physical wiring. Live state is derived from engine/monitor status; last-good/retained values are labelled.

## Removing a device

Connections & dependencies → Review removal lists the owning jobs and linked scanners, with direct navigation. Stop and remove the stopped jobs in the same device's Simulation & jobs tab. Stop associated monitors and deal with linked scanners before removing the source. Saved scenarios are independent snapshots and are never silently deleted. Saved configuration history supports recovery.

## Limits

This is a local development UI release, not a refreshed OS distribution. Generated Float points are the initial quick-create path; existing mixed-type/import editors remain available in the workspace. Local listener creation is restricted to loopback; advanced existing tools retain their previous capabilities. Physical hardware, all Windows UI workflows and production use are not qualified by local software checks.

## Focused and customer views — Preview 15 dev.1

A newly created simulator opens in its own focused view. Adding its local scanner focuses the source/scanner pair. Choose Show all devices to see other configurations.

From a device, Present this simulation preselects only that device. Add its scanner or any other relevant devices explicitly, enter a presentation title and test description, and choose whether to save the view. From Overview, Choose devices / save view gives the same controls. Saved views can be reused as focused working views or presentations. No job is stopped or started by changing views. Only links with both endpoints selected are drawn. Unrelated scenario-owned devices are excluded from presentations.

A saved view referencing removed devices shows the missing/empty selection rather than falling back to everything. The global stop action is labelled Stop entire workspace when focused; individual device controls affect their named job/monitor.
