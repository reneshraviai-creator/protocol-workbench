# Usability fixes — Preview 15 dev.3

Local build, 31 October 2026. Git checkpoint: `checkpoint/ux-fixes-20261031` (`f98efc2`). No GitHub upload or distribution package update is part of this change.

## Implemented

| User problem | Change |
|---|---|
| Back opens a different device after changing the tag dropdown | Changing devices updates workspace context; Back opens the current selected device. |
| Open monitor chooses the first device | Monitor setup preselects the selected device, falling back only if it has no available tags. |
| Engineer cannot configure existing objects | Corrected the frontend role guard. Backend authorization remains unchanged. |
| Pause/Stop appear usable on stopped jobs | Controls reflect job lifecycle; Start remains explicit. |
| Empty inspector offers recording/export/stop actions | Controls require a selected monitor; record/stop-recording follow actual recording state. Empty-state guidance explains the next action. |
| Refresh returns to Overview | Page, device, device tab and monitor selection are encoded in the URL. Browser Back/Forward follow navigation. No acquisition is started by navigation. Invalid device identifiers are discarded. |
| Creating a bridge requires JSON | Uses guided source/destination selection, tag-pair preview and optional scale/offset. Existing advanced editors remain available. Conflicting writers remain blocked. |
| Reports/verification require internal job IDs | Named job selectors and named test-run selection; exact evidence IDs remain available under Advanced. |
| Job kind names are implementation terms | Job cards display Simulated server, Value writer, Scanner or Protocol bridge. |
| Loading a test can replace the workspace unexpectedly | Button explicitly says “Replace workspace with this test”; existing confirmation remains. |
| Evidence pages imply everything belongs to the selected device | Context distinguishes workspace evidence from a device shortcut. |
| Quick simulator suggests an already configured TCP port | Suggests the next unused configured Modbus TCP port. OS-level port availability is still checked when starting. |
| OPC UA setup shows an irrelevant Unit ID | Hidden for OPC UA. Quick simulator uses the saved simulation interval default. |
| Tag delete controls are ambiguous to assistive tools | Accessible delete labels include the tag name. |

## Validation

- Before changes: 729 Python regression tests passed, 378 warnings.
- Focused Node checks: Engineer/Operator/Read-only controls, stopped and active job controls, validated route restoration, empty-monitor and recording action states.
- Existing Node checks retained: 25 scanner reads preserve page/device/tab/table nodes; presentation isolation; active ownership links and stable template polling.
- Isolated browser workspace on port 8881: switch tag device then Back; selected scanner in monitor setup; disabled empty-inspector actions; refresh Performance with the same selected device; browser Back; guided bridge preview and conflicting-writer rejection; named report selectors.
- Browser setup checks do not establish physical-device compatibility or certify usability for every engineer.

## Remaining broader design work

The dense editable tag table, consolidation of the different template/profile libraries, and clearer wizard recovery-draft choices need a separate design pass. These are not claimed resolved by this patch. Independent engineer usability sessions are still needed before describing the interface as mature.

Protocol adapters, project schema, acquisition scheduling and write-response semantics are unchanged.

## Activation result

After changes: **729 Python tests passed**, 378 warnings, 124.48 seconds. All four Node test files passed. Local app restarted as `2.0.0-preview.15-dev.3`; bootstrap and rendered version verified. Project JSON SHA-256 matched before and after activation; five saved connections and the four pre-existing recovery drafts remain. No jobs or monitors were active at restart. Screenshot: `UX_Fixes_Activated.png`.
