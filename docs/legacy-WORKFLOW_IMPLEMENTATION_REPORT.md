# Automated workflow implementation report

The existing application in `C:\Users\Admin\cutting_image_based` now has an automated Spacebar workflow and a dynamic template library. The final checks passed 151 Python tests and 16 JavaScript UI logic tests. The full physical capture-to-export workflow and visual browser interaction remain untested; USB discovery, default opening and live frame reading have now been verified on the connected camera.

## Modified files

| File | Reason |
| --- | --- |
| `app/main.py` | Background capture-to-export job, unique job identity, status endpoint, shared mutation lock, logging, failure recovery, shutdown cleanup, and deletion endpoint. Extracts reusable raw capture, corrected preview and corrected detection helpers. |
| `app/templates/template_manager.py` | Shared template validation, duplicate-name checks, atomic JSON saves and deletion by exact stable ID. |
| `app/camera/frame_capture.py` | Adds microseconds to project names so fast successive jobs cannot reuse the same second-based directory. |
| `frontend/app.js` | Spacebar safeguards and lifecycle, UI lock and status polling, template loading and confirmed deletion, stable-ID inputs, reuse of existing add/edit and nesting functions, and request identity checks against stale success. |
| `frontend/index.html` | Workflow status and export-format control, library selection/load/add/delete/refresh controls, and relocation of size editing into Library. Preferences retains Nesting Priority prominently. |
| `tests/test_automated_workflow.py` | 27 focused Python cases for workflow, data integrity, priority, dynamic templates, correction reuse and error recovery. |
| `tests/test_workflow_ui.cjs` | 13 cases executing the actual JavaScript functions in a controlled DOM, covering keys, library actions, UI locking and success/error reporting. |
| `README.md` | Usage, default-versus-loaded template behavior and test commands. |
| `WORKFLOW_IMPLEMENTATION_REPORT.md` | Final implementation and verification record. |

Evidence files are in `verification/`: `python-tests.txt`, `ui-tests.txt`, `calibration-integrity.json` and four before/after diffs. Original versions of the four principal code files are in `data/workflow_code_backup_20261014/`. No dependencies were added.

## Spacebar workflow

The USB camera opens automatically when the page loads; verify its preview first. Load a template through Library, or use the existing enabled-template set. Choose automatic DXF or SVG output, then click the workspace and press Space once.

1. Reserve the shared processing lock and validate a snapshot of templates and settings before capture.
2. Capture through the existing camera manager into a new project folder.
3. Apply the existing camera correction to the raw image once. Use that same corrected frame for the preview and material detection.
4. Detect the sheath using the existing segmentation and coordinate-conversion functions, including the saved scale or working-area mapping.
5. Run the existing nesting engine with the snapshotted template IDs, quantities, priority, spacing, clearance, rotation, strategy and precision. A loaded template is used alone; the default uses enabled library templates.
6. Export once through the existing SVG/DXF function. DXF uses the existing alignment settings and sidecar metadata. Empty nesting fails before export.
7. Report Completed only after the export function returns and the output file exists. Failures report the failing stage and diagnostic details. Release the lock in `finally`.

Actual displayed stages are Ready, Capturing image, Correcting distortion, Detecting material and converting coordinates, Processing and applying nesting priority, Exporting, Completed and Failed. This application has no separate image-template matching function: its templates are geometric nesting shapes. No artificial matching stage or new algorithm was added. No Space Number is required.

The main page stays asynchronous while the server worker processes the job. Other browser tabs can read status but conflicting mutations receive HTTP 409. The page keeps controls locked across temporary connection failures until it obtains a terminal status for the relevant job. Server shutdown waits for its worker and closes the camera.

## Template library and Preferences

The library lists all stored IDs without a fixed count or `custom-` restriction. Load Selected Template sets the active ID; selecting All enabled library templates and loading restores the default. Add and edit reuse the existing shape editor and save endpoint. Validation rejects invalid geometry/dimensions/quantity/rotations and duplicate names or IDs. Saving atomically replaces the existing JSON only after a complete write.

Delete requires a valid selected template and confirmation. Cancellation leaves data intact. The backend removes only the exact ID from the stored list; it does not interpret IDs as file paths. Successful deletion refreshes the library and clears a deleted active selection. Missing or inaccessible storage returns an error.

Preferences now contains Nesting Priority and its existing save/reorder behavior. Size editing is available in Library. Existing priority calculations and persistence remain unchanged. The original eight starter sizes remain only as initialization defaults for a new library; they impose no runtime limit.

## Verification results

Baseline: 110 pre-existing Python tests passed before the workflow edits. Final commands were:

```powershell
.\.venv-windows\Scripts\python.exe -m pytest -q
node --check frontend/app.js
node --test tests/test_workflow_ui.cjs
```

Final results: **151 Python tests passed**, **16 JavaScript tests passed**, JavaScript syntax passed, and all **110 HTML IDs were unique**. Python emitted one existing Starlette/httpx deprecation warning. No test failed or was skipped.

| Requirement or scenario | Result and evidence |
| --- | --- |
| Full workflow with selected template | Passed: actual detection, nesting, SVG and DXF export with controlled frame capture in `test_real_detection_nesting_and_export`. |
| No Space Number selection | Passed: workflow UI payload contains no Space Number; endpoint and pipeline require none. |
| Selected template used throughout | Passed: selected ID assertions in exported placement metadata and UI payload; snapshot test rejects later request changes and ignores a stale boundary. |
| Success after export completes | Passed: export held on an event remains busy with no result; completion follows export. Missing output and failed exports produce Failed. |
| Repeated Space and duplicate exports | Passed: UI double starts/key repeats are ignored; five duplicate server starts return 409 and exporter runs once. |
| Responsiveness and conflicting actions | Passed under controlled tests: GET status/library remains available during blocked export; capture, nesting, export, calibration, config, deletion and camera-close mutations are blocked. Physical browser responsiveness is not tested. |
| Keyboard safeguards | Passed in controlled DOM: focus, hidden page, native controls, editable content, modifiers, IME, calibration view and boundary editing. Native dropdown/browser behavior is not manually tested. |
| Single registration and removal | Passed: repeated registration produces one handler; unregister removes it. Pagehide/pageshow lifecycle wiring is present. |
| Add valid template and immediate refresh | Passed: existing form saves a unique UUID-derived ID and rerenders Library. |
| Invalid input and duplicate names/IDs | Passed: invalid input never saves; server validation preserves the original library. Existing duplicate-ID tests also pass. |
| Select and load any stored template | Passed: legacy IDs, 17-item library and selected-template workflow. |
| Delete after confirmation | Passed: confirmed deletion removes the exact stored ID and refreshes UI selection. |
| Cancel or delete without selection | Passed: no API deletion occurs and data stays intact. |
| Missing files, invalid IDs and storage failure | Passed: read/start/delete/save errors are handled; failed atomic replacement leaves old JSON and no temporary files. |
| Do not delete unrelated files | Passed: slash-containing ID deletion removes only its JSON entry; unrelated file survives. |
| Fewer/more than previous starter count | Passed: 0, 1 and 17 stored templates; processing and deletion with dynamic IDs. |
| Existing template metadata | Passed: old entry without priority loads and processes without an on-disk migration. |
| Correction stage and downstream image | Passed: one correction call; detector receives that exact corrected frame with no second camera profile. |
| Saved calibration and representative photo | Passed: existing `camera_calibration.npz` and saved `2026-10-14_092009/original.jpg`; automated and manual corrected frames and detected mm polygons match. |
| Calibration files preserved | Passed: SHA256 hashes of all 15 existing calibration files are unchanged. |
| Nesting Priority persistence and calculation | Passed: existing priority persistence/strategy tests plus new workflow tests. |
| Priority effect in final export | Passed: changing priority between 80 mm and 20 mm shapes changes the chosen placement and the actual DXF geometry. |
| Capture/correction/detection/nesting/export failure | Passed: controlled exceptions at each stage log diagnostics, release the lock and permit a successful retry. |
| Empty nesting or unreadable capture | Passed: Failed without successful export. |
| Worker creation failure | Passed: start fails safely and releases the lock. |
| Lost start response or earlier successful job | Passed: request IDs and UI error tests prevent a previous completed job from being reported as the new request's success. |
| Temporary resources and handles | Passed for atomic-save cleanup and exported-file reopening; worker joins are verified by test fixtures. Actual shutdown with a physical MJPEG camera was not tested. |
| Existing application regressions | Passed: all 110 original tests, covering calibration, camera support, vision, nesting, geometry, alignment and export. |
| Visual layout and real device interaction | Not tested: no live browser/physical-camera manual run was performed. |

## Preserved behavior and limitations

The camera matrix, distortion coefficients, NPZ/JSON profiles, calibration resolution, scale mapping, laser alignment and saved settings were not changed. Lens correction, segmentation, coordinate transformation, nesting algorithms and vector export formats remain the existing implementations. The raw capture helper and corrected detection helper allow the same processing logic to run in the background without correcting its input twice.

Physical capture requires an open camera; the page now opens the USB camera automatically using saved settings. The automated path reports a capture error if the camera is unavailable. No camera retraining, calibration migration or template migration is required. Current calibration compatibility behavior, including the existing aspect-ratio checks, remains unchanged; the saved-photo comparison is not a new physical accuracy certification.

The lock and workflow status are scoped to this local server process. Use the existing single-process launcher; multi-worker deployment is not supported by the application's shared in-memory state. Workflow status is not retained after restarting the server, while existing project/export files remain saved. A connection that remains unavailable leaves the page waiting for status. Failed exports can leave partial output artifacts written by the existing exporter; they are not reported as successful.

Restart this copy's server after installing the changes, then refresh the browser to load the new JavaScript. Verify the camera, template, nesting result and physical dimensions in the live setup before relying on the automated path.


## USB camera startup update

The application now exposes USB cameras only. On this Windows machine it identifies
devices by DirectShow enumeration order, excludes the built-in HP HD Camera and
virtual/integrated cameras, and opens the USB Camera automatically on page load.
Existing camera settings remain 1280 x 720 at 30 FPS. A connected USB camera is
reused on repeat startup rather than reopened. If no USB camera is available,
the app reports that fact and never falls back to the built-in camera.

Additional changes: `app/camera/windows_devices.py` reads native camera identities
without adding dependencies; `app/camera/camera_manager.py` uses those identities
and avoids probing built-in devices in USB-only mode; `app/main.py` enforces USB-only
API selection and idempotent default opening; `frontend/app.js` starts the camera;
`frontend/index.html` and `README.md` describe the new default. New
`tests/test_usb_camera.py` contributes 14 cases, and the UI suite adds 3 startup cases.

Live verification listed only USB Camera at index 0, opened it using DirectShow at
1280 x 720 and approximately 30 FPS, read a (720, 1280, 3) frame, confirmed repeated
opening reused the connection, and closed the test handle afterward. The record is
`verification/usb-camera-live-check.json`. All 15 saved calibration files still
match their original SHA256 hashes. The full suites now pass 151 Python and 16 UI
tests, with the same existing deprecation warning.


## Template tab clarity update

The Template tab now shows a prominent Used for next run summary with dimensions,
quantity and the active template name. Pending dropdown choices explicitly require
Use this template; each saved template card also has a direct Use action. Cards
show labelled quantities, enabled status, dimensions and a distinct In use badge.
Edit and Delete controls are separate, the destructive action uses a red treatment,
and Library displays the saved/enabled counts and an empty-library message.
Nesting Priority remains a separate tab. The editor identifies the template being
edited and opens visibly from Add or Edit; deletion no longer opens an Add form.
Save guidance explains that quantities/enabled changes must be saved to persist.

Changed files: frontend/index.html, frontend/app.js and frontend/styles.css, plus
updated realistic UI test fixtures and two selection-contract tests in
tests/test_workflow_ui.cjs. No backend, storage, nesting or calibration changes.
All 18 UI tests pass, JavaScript syntax passes, and isolated headless Chrome
screenshots verify the real HTML/CSS/JS with read-only saved templates and a saved
camera image. The UI snapshots are verification/template-ui-preview.png and
verification/template-ui-editor-preview.png. The 151-test Python result from the
USB update remains the most recent backend suite run; it was not rerun for these
frontend-only edits. All 15 calibration hashes are still unchanged.


## Visual cycle workflow update

After a successful export the UI now returns to Ready for the next cycle, keeping
the last result available until the next user-triggered capture. Space starts a
fresh cycle; status reads and completed exports never trigger another capture.
The server retains the terminal job record so clients can verify export success.

The workspace dashboard displays the five actual stages, their completed/running/
failed status and elapsed processing time. Stage history is retained so fast stages
are still shown even if a polling interval skips them. View buttons expose the
real captured frame, corrected preview, detected contour, segmentation mask and
nesting overlay; Live camera helps position the next piece. Output links appear
only after successful export. Failed stages stay visibly failed with actionable
messages, and unavailable image views remain disabled.

Changed files: app/main.py adds stage history, cycle numbers and artifact URLs;
frontend/app.js handles the visual states, artifact views and readiness;
frontend/index.html and frontend/styles.css add the dashboard; both workflow test
files add consecutive-cycle, artifact, history and failure checks. README.md
explains the new cycle behavior.

A real saved frame from project 2026-10-14_122520_016714 was confirmed entirely
black. Automated capture now rejects uniform blank frames before detection or
export, with an explicit error. This validation does not change lens correction,
segmentation, coordinate conversion, nesting or export algorithms. Calibration
hashes for all 15 files remain unchanged. Earlier exports are preserved.

Verification passes 156 Python tests and 21 UI tests; JavaScript syntax also
passes. New server tests verify two consecutive independent captures and exports,
complete stage history, real intermediate files, export failure, and uniform black
or white frame rejection. UI tests verify automatic readiness, no implicit repeat,
artifact availability and failed-stage display. The Chrome screenshot in
verification/cycle-visual-preview.png uses archived valid project
2026-10-14_120700_411464 and its actual existing export; it does not represent a
new live camera run. No physical multi-cycle production run was performed for
this change. Restart the server and refresh the browser to load the updated API
and UI. Cycle counters reset on server restart; saved exports remain on disk.


## Live camera refresh and dimension display update

The Ready view now resumes the MJPEG feed after export instead of keeping the
saved nesting snapshot as its default. Archived cycle views remain available via
the image buttons. Refresh camera and Camera Refresh now reopen the actual USB
device through the existing open endpoint, preserving its current resolution and
FPS. Duplicate reconnects and workflow starts during reconnect are blocked. The
live stream uses no-cache headers and stream failures display a reconnect message.

Dimensions now appear prominently above the viewer and in the Sheath panel, with
optional width/height arrows on captured-image views. Edge-aligned enclosing
rectangle dimensions are additional display measurements. Original X/Y spans,
area, contour, nesting geometry and export paths remain unchanged. Older payloads
without the new values are explicitly labelled X/Y spans. The live view labels
retained values Last captured piece; measurement updates require capture and
detection, not merely a video frame. New capture resets the old measurements.
This change does not resolve the previously identified physical calibration
error: the archived 263 x 262 mm ruler sample still estimates approximately
272.1 x 264.6 mm along its enclosing rectangle with the saved calibration.

Changed files: frontend/app.js, frontend/index.html, frontend/styles.css,
app/main.py and app/vision/measurements.py. New tests/test_measurement_dimensions.py
covers rotation, preserved legacy spans/area and arrow endpoints; UI tests cover
live resumption, reconnection, settings preservation, failure recovery and honest
legacy measurement labels. Frontend text encoding was normalized to UTF-8 after
visual verification found a malformed area unit. README.md describes the controls.

Verification: 162 Python tests and 26 UI tests pass. Chrome rendered the actual
saved sample with dimensions and arrows; the preview is
verification/camera-dimensions-preview.png. The running app's USB camera was
reconnected through its existing API at 1280 x 720 and approximately 30 FPS; a
valid live frame with pixel range 0-255 was received. No new capture-to-export
cycle was started by this check. All 15 calibration files still match the initial
hashes. Restart the existing server process and refresh the browser to load the
new Python measurement fields and cache-busted frontend assets.


## Persistent preview after nesting

Auto Nest now immediately opens the result for the current captured piece.
Automatic cycles show the layout when nesting results become available, while
a persistent Last nesting preview card remains visible after export and live
camera restoration. Open full nesting preview reopens the result without running
nesting or exporting again. If no captured image is available, the existing
placements are shown as a vector layout in millimetres. Empty nesting results do
not create a new successful preview.

Changed frontend/app.js, frontend/index.html and frontend/styles.css; the existing
polygon renderer is reused for the preview canvas. No backend, calibration,
nesting algorithm or export changes were made. Three new UI tests cover manual
preview opening, persistence after live restoration and preview without a photo.
All 29 UI tests and JavaScript syntax checks pass. Chrome visually verified the
card and full overlay using archived real placement geometry and a saved image.
The screenshot is verification/nesting-preview.png. The most recent Python suite
remains 162 passed from the camera/dimension update; it was not rerun for these
frontend-only changes. All calibration hashes remain unchanged.


## Spacebar recovery after the first cycle

The running backend reported Completed, busy=false, cycle_number=1, so its
processing lock was already released. Inspection found that the camera canvas
was not focusable and its click handler did not clear focused controls. This
could leave the global Spacebar safeguard rejecting subsequent key presses.

frontend/index.html now makes the camera canvas keyboard focusable, supplies an
accessible shortcut label, and updates the asset version. frontend/app.js focuses
the canvas when the camera is clicked and restores workspace focus in the
workflow finally block when focus is on a cycle action, the canvas or the page.
Inputs, selectors, unrelated controls, calibration/boundary editing and inactive
windows keep their focus. Keyboard safeguards and duplicate-execution guards
remain in place. tests/test_workflow_ui.cjs adds four regressions: two consecutive
Space-triggered cycles, camera click focus recovery, protected editing focus, and
retry after failure. No backend or calibration files were changed.

Verification: the 29 existing UI tests passed before editing; all 33 tests now
pass, and JavaScript syntax validation passes. An isolated headless Chrome test
using the real application DOM and JavaScript passed two sequential Spacebar
cycles with controlled server responses, request IDs, focus recovery and input
safeguards. Evidence: verification/ui-tests.txt and
verification/space-next-cycle-browser.txt. The fixture is
verification/space-next-cycle-browser.html. These checks do not run physical
camera captures or real exports; a second live hardware cycle remains not tested.
The prior Python result remains 162 passed; backend tests were not rerun for
this frontend-only change. Refresh the browser with Ctrl+F5 to load this fix.


## Last nesting preview size legend

The numbered outlines remain unchanged. A new legend maps every placement number
to its saved template name/size, e.g. 1 → 7 inch square, 2 → 3 inch square.
Placement names are retained from the nesting result, so subsequent library edits
or deletion do not change the last result's labels. Older placements without
names fall back to the library by stable template ID; missing metadata is marked
Size unavailable. Labels are inserted as text, and repeated sizes keep individual
placement numbers. The legend refreshes with each new nesting result.

Changed frontend/app.js (legend rendering), frontend/index.html (legend container
and asset version), frontend/styles.css (wrapping and scrollable legend), and
tests/test_workflow_ui.cjs (number/size mapping and metadata fallback tests).
All 35 UI tests and JavaScript syntax validation pass. Headless Chrome visually
verified archived real nesting results: verification/nesting-legend.png and the
isolated fixture verification/nesting-legend.html. No workflow, backend, nesting,
export, calibration or template storage changes were made. No physical cycle
was run for this display-only change. Refresh the browser with Ctrl+F5.


## Spacebar after manual camera and other button actions

Mouse-clicked Refresh, Open camera, Open USB camera, navigation and other action
buttons now return focus to the camera workspace when the clicked button still
owns focus. The previous fix covered cycle buttons only. A document click
handler shares the existing single-registration/pagehide cleanup mechanism.
Keyboard/programmatic activation (detail=0), text fields, dialogs, inactive
windows and calibration/boundary editing retain their existing safeguards.
Pending requests, camera-refresh and workflow locks still prevent duplicate jobs.
No camera operation, workflow sequence, calibration, nesting or export code was
changed. Changes: frontend/app.js (mouse action focus), frontend/index.html
(asset version), tests/test_workflow_ui.cjs (four focused regressions).

All 39 UI tests and JavaScript syntax validation pass. An isolated headless
Chrome fixture passed Space key dispatch after five manual-action clicks,
keyboard focus safeguards and the prior consecutive-cycle test. Evidence:
verification/manual-action-space-browser.txt and verification/ui-tests.txt.
The browser fixture uses controlled responses and no-op manual operation handlers
to test real DOM focus without hardware side effects; physical captures after
these actions were not performed. Refresh the page with Ctrl+F5 to apply the fix.


## Rectangle horizontal/vertical cutting direction

Add/edit Rectangle now offers Either direction (default), Horizontal only (0°),
and Vertical only (90°). The existing allowed_rotations field stores [0,90], [0]
or [90]; entered dimensions are preserved. Direction appears in library size
summaries. Editing older templates restores their existing restriction; custom
angle lists can be kept without conversion. Square and circle behavior remains
unchanged, and no stored templates were migrated or rewritten during testing.

A specific pre-existing defect was found in shared-side row packing: it rotates
the whole layout to the material's long side without honoring template rotation
restrictions. Shared_side with any rectangle locked to a single angle now uses
the existing precise engine, which respects allowed_rotations. Other shared-side
layouts retain their existing implementation. No new nesting algorithm was
introduced; priorities, sizes, spacing, workflow stages and exporters are reused.

Modified frontend/index.html (selector, hint, asset version), frontend/app.js
(direction persistence, editor restoration, library labels),
app/nesting/nesting_engine.py (existing precise fallback for locked rectangles),
tests/test_workflow_ui.cjs (three UI regressions), and new
tests/test_rectangle_orientation.py (20 orientation, storage and export tests).
README.md documents use; this report records implementation and verification.

Baseline Python suite: 162 passed. Final suite: 182 passed with one existing
Starlette/httpx deprecation warning. All 42 UI tests and JavaScript syntax checks
pass. Tests cover both fixed directions across all six strategies, JSON storage
round trips, rejection of wrong-orientation fits, real SVG/DXF output dimensions,
Add form submission, old template restoration and custom-angle preservation.
Evidence: verification/python-tests.txt, verification/ui-tests.txt, and the
headless Chrome editor preview verification/rectangle-orientation.png. All 15
calibration files match original hashes. Physical cutting was not performed.

The idle server was restarted on port 8001 to load the backend change. Its
previous USB camera connection was restored through the existing API at
1280 x 720. No capture-to-export cycle was started. Refresh the browser with
Ctrl+F5 to load the selector. Template migration: none required.


## Nesting speed optimization

Modified app/nesting/precision_nesting.py only in the search/candidate hot path:
fallback grid candidates now batch across rows in the same row-major order;
disjoint expanded bounding boxes bypass irrelevant collision checks; prepared
intersection predicates reject overlapping detailed outlines before expensive
distance calculations. Small simple layouts retain the cheaper original distance
path. Candidate grids, packing frames, exact-contact positions, refinement,
precision, containment, spacing tolerance, priority scoring, allowed rotations,
exporters and calibration values are unchanged. No search timeout or reduced
quality setting was introduced. The UI and workflow sequence are unchanged.

Added tests/test_nesting_candidate_optimization.py: 16 tests compare optimized
predicates with exhaustive geometry checks on seeded rectangle/circle/rotated
candidates, shared-edge and spacing thresholds, material holes and skipped
expensive distance calls. Added verification/benchmark_nesting.py and the original
module snapshot verification/nesting-speed-before/precision_nesting.py for
reproducible before/after measurements. README.md documents the optimization.
No template migration is needed.

Benchmark timings below were measured sequentially without the profiler after
other tests finished, using the same inputs, accurate strategy, 0.5 mm refinement,
2 mm spacing and 3 mm clearance. These measure nesting only; they do not include
camera capture or export. All complete output dictionaries, including every
coordinate, placement order, angle and utilization, match the baseline exactly.

- archived_camera_piece: 13.601s → 12.856s (1.06×).
- crowded_rectangles: 5.072s → 2.168s (2.34×).
- circles: 30.378s → 6.167s (4.93×).

Evidence: verification/nesting-speed-summary.json,
verification/nesting-speed-reference-unprofiled.json and
verification/nesting-speed-optimized-unprofiled.json. Profiling identified
polygon distance as the dominant cost. Individual timings vary with CPU load;
the archived irregular camera piece still takes about 13 seconds, so this is
not an instant-nesting guarantee. No physical cutting was performed. All 15
calibration hashes remain unchanged. The idle local server was restarted to
load the optimized code, and its prior USB connection restored at 1280 x 720.
No real workflow/export was started for the performance benchmark.

Final validation after the small-layout fast path: 198 Python tests passed (one existing Starlette/httpx warning); all 42 UI tests passed. Logs are verification/python-tests.txt and verification/ui-tests.txt. No test failures remain.


## Independent multiple-sheath workflow and shared-cut speed

User-confirmed behavior: requested template quantities apply to each sheath
separately. A single Space capture detects all valid separated material contours,
corrects distortion once for processing, converts each boundary with the same
existing calibration, measures each piece, nests its own quantities in existing
priority order, and produces one combined export with no bridging outline or
cross-sheath template. The lock stays held through every piece and final export.
Actual nesting progress identifies sheath 1 of N, then 2 of N, etc. The existing
Ready/next-cycle behavior, keyboard guards and template selection are preserved.

Modified files and reasons:
- app/vision/preprocessing.py: retain all valid foreground components; apply the
  existing closing/opening kernels separately when multiple components are found
  to avoid merging visible gaps. Single-component morphology is unchanged.
- app/vision/contour.py: return all valid polygons while retaining the original
  largest_contour_polygon wrapper and approximation settings.
- app/vision/sheath_detection.py: map each contour independently, retain the
  largest legacy aliases, and draw all boundaries with per-capture sheath IDs.
- app/main.py: store/reset sheath lists, report individual measurements, nest
  quantities per sheath, aggregate stats, report actual per-sheath progress and
  export all boundaries through one shared coordinate alignment. The legacy
  editable single-boundary state remains supported. Empty/invalid extra boundaries
  fail safely; no partial nesting result is saved.
- app/nesting/nesting_engine.py: shared-cut candidates use stable (y,x) order and
  batched predicates instead of checking every candidate after a valid minimum
  has been found. Existing score, candidate positions, frames and priority remain.
- app/nesting/precision_nesting.py: reusable candidate filtering optionally uses
  the original shared-cut contains predicate; default dense covers behavior stays.
- app/export/svg_export.py and app/export/dxf_export.py: optional disconnected
  sheath lists, one path/polyline per material, with unchanged export formats.
- app/laser/coordinate_alignment.py: compute bounds across every sheath and apply
  the same existing transform matrix to all materials and cuts.
- frontend/app.js, frontend/index.html and frontend/styles.css: individual
  dimension/cut-count rows, a sheath inspection selector, separate preview outlines,
  dimension arrows for each sheath and cut-to-size-to-sheath legend entries.
- tests/test_multi_sheath.py: new detection/gap, whole-workflow, independent quota,
  orientation/priority, global alignment, invalid-boundary and progress tests.
- tests/test_shared_cut_optimization.py: compare full output with exhaustive
  original candidate scoring for rectangular, rotated and concave materials,
  including shared-edge/tiny-spacing cases.
- tests/test_workspace_cutting.py: multiple inside materials with outside objects
  and calibration markers excluded.
- tests/test_workflow_ui.cjs: independent dimension selection, preview labels,
  state clearing, unchanged template choice and truthful processing progress.
- README.md and this report document behavior, measurements and limits.

Validation: baseline 198 Python tests passed. Final 222 Python tests and 46 UI
  tests pass; JavaScript syntax validation passes. One existing Starlette/httpx
  deprecation warning remains. Controlled tests cover visible gaps of 15/25/80px,
  corrected capture with two sheaths, independent measurements and quantities,
  accurate/shared-side/first-fit nesting, real SVG and DXF files, exactly one
  export per capture, no cross-gap placement, independent vertical restrictions
  and priority, marker/workspace exclusion, missing fit on one sheath, invalid
  extra boundaries and lock release. All 15 calibration hashes remain unchanged.

Shared-cut performance, measured sequentially with exact full output equality:
- Rectangular 13-piece test: 9.019s → 0.215s (41.87×).
- Archived camera geometry/current template library: 33.232s → 32.922s (1.01×).
  This complex/fallback case remains a limitation; no universal speedup is claimed.
Evidence: verification/shared-cut-speed-summary.json and the reproducible
verification/benchmark_shared_cut.py. Test logs: verification/python-tests.txt
and verification/ui-tests.txt. Headless Chrome visually checked controlled real
segmentation/nesting/export data in verification/multi-sheath-preview.png; fixture
and result data are verification/multi-sheath-preview.html and
verification/multi-sheath-controlled-result.json. A controlled SVG is saved in
verification/multi-sheath-visual/controlled-cut-layout.svg.

Limitations: physical camera capture with multiple user sheaths and physical
cutting were not performed. Touching/overlapping pieces can form one contour;
a visible gap is required. Existing min-area filtering still applies to small
objects. Component-aware closing is the only segmentation change, addressing the
specific defect where the old 71px kernel merged separate sheaths. Camera matrix,
distortion coefficients, calibration resolution, coordinate scale, priority
calculation, allowed rotations and export unit/format settings were preserved.
Template migration: none. New project JSON fields supplement legacy single-piece
fields. The idle app was restarted and its USB camera restored at 1280 x 720.
No live capture-to-export job was started during this verification. Ctrl+F5 loads
the updated frontend.


## Requested measurement/nesting precision and automatic CSV data

The user selected improvements to both measurements and nesting. Measurement
coordinate conversion now uses float64 points and homography arithmetic instead
of discarding saved calibration precision through float32. The saved matrix,
distortion coefficients, resolutions, contour/segmentation settings and all 15
calibration-file hashes are unchanged. Measurement displays use two decimals;
underlying numeric values and CSV source geometry are not rounded to UI precision.

Nesting precision_mm is now 0.1 in existing persisted configuration; all other
configuration values match the backup exactly. Accurate/fixed-direction modes
use their existing precise engine. Shared-side refinement below 0.5 delegates
positioning to the existing exact-contact/grid/local-refinement helper, using the
original shared-cut contains and overlap/spacing rules and existing frame/priority
scoring. Fallback candidate count stays bounded; impossible repeated templates
are skipped only while identical geometry/context remains unchanged. The standard
0.5+ shared-cut path remains available. Fine processing can be slower. Tests on a
slanted material improved first placement from x=2.00 to x=1.75 mm in both modes,
while retaining 3 pieces, exact 10×10 sizes and 2 mm spacing. This is numerical
refinement, not a physical camera-accuracy certification.

Each completed manual/automatic SVG or DXF now has one same-stem CSV alongside it.
Rows include separate sheath measurements and counts; individual cuts with stable
template IDs, names, dimensions, priority, quantities and permitted rotations;
unplaced-template rows including material with zero cuts; source image/project/run
identity; UTC time; refinement/spacing; calibration snapshot; full source polygon
coordinates and actual exported coordinates/centres/angles. CSV records DXF's
actual common alignment and SVG's existing 0.001 mm formatting. Template/settings
snapshots come from nesting, so later library edits cannot rewrite recorded history.
UTF-8 BOM and csv.DictWriter handle Unicode and commas. Spreadsheet formula-like
user text is escaped. Temp writes flush/fsync then atomically replace; failure
cleans temporary files and preserves older data. A CSV failure after vector save
returns an actionable error naming the saved vector and marks the workflow Failed;
no completion or new CSV link is reported, and the processing lock releases.

Modified files:
- app/calibration/scale_calibration.py: float64 coordinate conversion only.
- app/nesting/precision_nesting.py: reuse precise positioning with selectable
  contains/covers semantics; defaults preserve existing dense behavior.
- app/nesting/nesting_engine.py: fine shared-cut positioning and failed-fit cache,
  preserving allowed rotations, dimensions, frame scoring and priorities.
- app/config/config.yaml: only nesting.precision_mm changed to 0.1.
- app/export/csv_report.py: new atomic CSV writer and complete per-sheath/cut data.
- app/main.py: freeze actual template/settings snapshots and require successful
  CSV sidecar creation before reporting export success.
- frontend/app.js and frontend/index.html: two-decimal dimensions, CSV links,
  refinement description, and Spacebar focus recovery after mouse CSV downloads.
- tests/test_precision_settings.py: double arithmetic and genuine tighter placement.
- tests/test_csv_report.py: multi-sheath CSV, full-precision/Unicode round trips,
  actual SVG/DXF coordinates, frozen templates and storage-error recovery.
- tests/test_workflow_ui.cjs: display precision, CSV link visibility, failure state,
  and keyboard readiness after CSV download.
- README.md and this report: user instructions, evidence and precision limits.

Validation: measurement/export baseline 34 tests passed. Final full suite: 233
Python tests passed, with one existing Starlette/httpx deprecation warning; 49 UI
tests and JavaScript syntax validation passed. Tests exercise real SVG/DXF and CSV
writers, source-vs-export coordinates, independent sheath quotas, vertical/priority
rules, storage failures, atomic replacement, clean temp files and lock release.
Evidence: verification/python-tests.txt, verification/ui-tests.txt and
verification/precision-csv-baseline-tests.txt. Headless Chrome visually checked
verification/precision-csv-preview.png with a real controlled export and parsed
CSV (2 sheath rows, 4 cut rows). Result/paths are recorded in
verification/precision-csv-controlled-result.json. No live camera workflow or
physical cutting was performed. The idle server was restarted, the USB camera
restored at 1280×720, and the running config/HTML checked. Ctrl+F5 loads the update.

Unresolved physical accuracy: the known ruler sample is 263×262 mm, but saved
calibration and double-precision projection produce 272.1168×264.6379 mm. The
largest arithmetic change relative to the earlier float32 coordinates is only
0.000099 mm. Numerical precision cannot correct this physical discrepancy;
measurement accuracy requires a reliable camera/working-plane calibration and
ruler verification. No blind rescaling or calibration retraining was performed.
See verification/precision-reference-check.json. No template migration is needed;
older stored nesting results still export with unavailable metadata left blank.
Historical exports are not automatically rewritten. CSV is per successful export,
not a cumulative file; records remain with the existing project storage.


## UI redesign from hand-drawn reference — 14 October 2026

UI-only changes in the existing FastAPI/vanilla JavaScript application:

- `frontend/index.html`: title SHEATH TEMPLATE CUTTING; original Process control in header; eight tabs in the requested order; navigation-only Process Sheath; compact cycle/measurement controls; larger left camera workspace and persistent right nesting preview; Pieces, Utilization and Estimate panels; existing waste retained. All previous control IDs remain present exactly once.
- `frontend/styles.css`: industrial styling, responsive two-column previews, active section styling, readable metrics and compact desktop sizing. Existing configuration controls remain accessible; individual sheath details and guidance can be expanded.
- `frontend/app.js`: section visibility navigation preserves camera source, templates, calibration points, placements and unsaved inputs. Process binding and `runWorkflow` function are unchanged (normalized newline comparison). Existing busy lock includes the moved header/capture/format controls. Nesting rendering fits its panel and keeps the original numbered legend; coordinates and nesting results are unchanged. Data displays existing current measurement/export values, copies real download links, lists projects through the existing GET `/api/projects`, and reuses POST `/api/project/save`; storage errors use existing message handling.
- `tests/test_workflow_ui.cjs`: focused tests for eight-tab navigation, state preservation, original Process function/binding, original control IDs, busy controls, actual preview empty state, and Data reads/saves/errors/downloads.
- `verification/verify_ui_redesign.py`, isolated HTML fixture, screenshots, JSON and logs: reproducible browser verification; production workflow is never started by this visual fixture. Browser click dispatch uses a spy for Process; actual original workflow execution is covered by the existing Node tests and backend regression suite.

Verification results:

| Requirement | Result | Evidence / limitation |
| --- | --- | --- |
| Existing UI baseline | Passed | 49 tests before editing |
| UI behavior and workflow regression tests | Passed | 56 tests; `verification/ui-redesign-ui-tests.txt` |
| Existing processing/backend regression suite | Passed | 233 tests; `verification/ui-redesign-python-tests.txt`; existing Starlette/httpx deprecation warning |
| Eight tabs / correct section / return to processing | Passed | Isolated Chrome actual DOM clicks at 1440x900, 1366x768, 1920x1080 and 760x1000; JSON lists all eight sections |
| Process Sheath does not trigger processing or camera resets | Passed | Node counters and browser state snapshot equality; no processing call on section/return clicks |
| Process triggers original workflow | Passed in controlled tests | Original function and binding unchanged; existing workflow tests run capture-to-export with controlled API results, duplicate prevention and export-failure recovery |
| Selected template, calibration points, placements and unsaved fields survive navigation | Passed | Focused Node test and Chrome before/after equality |
| Camera initialization, zoom, orientation and settings unchanged | Passed for code/settings preservation | Existing tests; read-only status before/after showed USB Camera index 0 / DirectShow / 1280x720 / ~30 FPS unchanged |
| Real live-preview stream | Partially verified | Two JPEG frames received and stream URL present in served DOM. A received frame was black, and existing startup reported No USB camera found despite connected status. Image quality and discovery warning remain an existing camera issue; no camera logic changed |
| Nesting preview and numbering/size legend | Passed | Existing and focused Node tests; controlled Chrome screenshots render real saved fixture geometry. Actual served app renders its existing 11-piece saved result |
| Desktop layout | Passed by visual inspection | `verification/ui-redesign-1440.png`, `ui-redesign-1366.png`, `ui-redesign-1920.png`; side-by-side previews and three bottom panels. Narrow screens stack panels and scroll |
| New frontend runtime errors | None in controlled browser checks | `verification/ui-redesign-browser-results.json` contains empty error arrays and no horizontal overflow |
| Actual served app / projects endpoint | Passed | HTTP 200 for `/`, `/api/camera/status`, `/api/projects`; served title/eight tabs confirmed |
| Backend/calibration/template integrity | Passed | 55 files match pre-edit SHA-256 hashes; `verification/ui-redesign-integrity.json` and backup hash manifest |
| Physical cutting / new hardware capture-to-export run | Not tested in this UI-only change | No new processing, calibration, cutting, template save, or export was initiated on real hardware |
| Estimate | Unavailable in existing implementation | Panel explicitly says Not available; no calculation added; existing waste remains visible |

No backend, motor, serial, calibration, model, processing algorithm, export format, database schema, filename or saved-data format was changed. No migration is required. Existing calibration/measurement accuracy limitations documented earlier still apply. Production changes are limited to the three frontend files; test/evidence/documentation files are additional support artifacts.

Refresh the existing page at http://127.0.0.1:8001/ to load the updated asset version `20261014-ui-redesign`. Process starts the same existing automatic workflow; Process Sheath only returns to the processing screen.


### Process section dropdown follow-up

The eight existing section buttons now appear inside a native dropdown directly beneath the header Process button. Process remains the original workflow action; opening the dropdown never starts processing. Selecting a section closes the dropdown and updates the current-section label. Process Sheath returns to the workspace and closes the dropdown while retaining existing state.

Modified `frontend/index.html`, `frontend/styles.css`, `frontend/app.js` and the focused UI test file. Asset version: `20261014-process-dropdown`. All 57 Node UI tests passed (`verification/process-dropdown-tests.txt`); isolated Chrome opened the dropdown and selected all eight sections at four viewport sizes with unchanged state, no JavaScript errors and no horizontal overflow. Visually checked `verification/process-dropdown-preview.png`. All 55 protected backend/calibration/template hashes still match. No backend or workflow changes; no additional hardware processing was run for this navigation-only change.


### Single sequential processing status box

Replaced the five visible Capture / Lens correction / Detection / Nesting / Export cards with one status box driven by the existing worker stage. It shows the actual stage, step number, running indicator, per-sheath nesting status, export completion, ready-next-cycle message or actionable failure. The original hidden stage-history nodes retain their existing IDs for compatibility and diagnostics. Processing functions, stage sequencing, locks and backend are unchanged.

Modified the three frontend files and added a focused UI test. All 58 UI tests pass (`verification/single-stage-tests.txt`), including ordered stage display, completion, export failure and retry. Chrome verified the updated UI at four sizes with no JavaScript errors or horizontal overflow; visual evidence is `verification/single-stage-preview.png`. All 55 protected file hashes remain unchanged. No new hardware processing run was performed for this display-only change. Refresh the page to load asset version `20261014-single-stage`.


### Header controls follow-up

Process Sheath now occupies the former Process button position at the top-right, with the Process section dropdown directly beside it. The separate Process action button and its DOM bindings were removed at the user's request. Process Sheath retains its navigation-only behavior; Spacebar still invokes the unchanged `runWorkflow` function. Visible start instructions now say Press Space. Removed the old navigation row; current section is displayed under the title.

Updated the three frontend files and tests for the intentionally removed button. All 58 UI tests passed (`verification/header-controls-tests.txt`), including controlled workflow execution via Spacebar, duplicate prevention, failure recovery and navigation. Chrome exercised all eight sections at four resolutions, confirmed no workflow starts during navigation, preserved state and reported no JavaScript errors. Visually checked `verification/header-controls-preview.png`. All 55 protected backend/calibration/template hashes remain unchanged. Asset version: `20261014-header-controls`.


### Dedicated full-page dropdown sections

Selecting a dropdown section now hides the automatic workflow dashboard and unrelated result panels. Camera, Calibration and Sheath fill the page with their existing relevant preview and controls; Sheath also retains its captured dimensions. Nesting shows the saved layout with existing controls and can open the existing interactive full preview. Templates, Export, Settings and Data use the full content area. Process Sheath restores the processing dashboard. Existing DOM/state is retained; no camera reconnect, calibration reset, template reset or processing start occurs during navigation.

Modified `frontend/index.html` (move the existing Capture control into the shared relevant preview toolbar, contextual preview heading and cache version), `frontend/styles.css` (dedicated section layouts), `frontend/app.js` (contextual titles and resize the existing full nesting preview after opening), and focused UI/browser checks. No workflow functions, API endpoints or backend algorithms were changed.

All 59 Node UI tests passed (`verification/full-section-tests.txt`). Chrome checked all eight pages at four resolutions: selected sections visible, workflow status and unrelated metrics hidden, full-page content views hide the shared workspace, and preview sections expose only their relevant preview. Camera source, zoom, template edits and workflow-preview selection are preserved by focused tests. Browser snapshots show unchanged data, zero navigation-triggered workflow starts, no JavaScript errors and no horizontal overflow. Camera page screenshot: `verification/full-section-camera-preview.png`. All 55 protected file hashes remain unchanged. No additional hardware processing was run. Refresh the application to load `20261014-full-sections`.


## Live nesting overlay and stage timers — 15 October 2026

Changes:

- `frontend/app.js`: project existing placement polygons onto the already-corrected live feed using the existing inverse homography or saved mm-per-pixel scale and existing crop/zoom mapping. Render original numbered outlines for all sheaths without changing geometry, templates, nesting, camera source or export data. Suppress the overlay for mismatched known image resolution, unusable scale, calibration editing and boundary editing. Add a checkbox redraw handler and a last-captured-position notice. A new capture clears old placements through the existing workflow reset.
- `frontend/index.html`: optional Nesting overlay checkbox (enabled initially), static-layout notice and timing summary/list inside the existing single workflow box. Asset version: `20261015-live-timers`.
- `frontend/styles.css`: readable timing rows and overlay controls; all stage timers remain in the single workflow box.
- `tests/test_workflow_ui.cjs`: focused tests for homography and scale projection, crop/zoom, two sheaths/numbering, geometry preservation, invalid mappings, checkbox redraw, server timing progression, zero/unknown durations, rollover, completion, failure, new-cycle reset and rejected-start stale history.
- `verification/verify_ui_redesign.py` and controlled browser fixture: assert nontransparent overlay pixels when enabled, an empty overlay when disabled, restoration when re-enabled, and five timer rows with a known synthetic event total. Test timings in screenshots are explicit controlled fixture values, not a processing speed benchmark.

Verification: all 67 Node UI tests passed (`verification/live-timers-ui-tests.txt`); all 36 selected existing Python workflow/green-preview/calibration tests passed (`verification/live-timers-python-tests.txt`, existing Starlette/httpx deprecation warning). Chrome checked four viewport sizes, all existing section navigation/state invariants, overlay pixel toggling and timer rendering without runtime errors or horizontal overflow. Visually inspected 1440x900 and 1366x768; screenshot: `verification/live-timers-preview.png`.

Timer semantics: each row uses the worker's existing `events[].elapsed_seconds`. The current stage and total of recorded stages refresh through existing workflow polling (~300 ms). Completed/failed values stop advancing; absent timings display a dash. Rejected starts cannot reuse completed timing history. No estimated or client-generated durations are reported. No additional polling interval or worker was introduced.

Overlay semantics: the live camera continues updating while the last captured nesting remains fixed in calibrated image coordinates. This does not track moving material or re-run detection/nesting. Capture again after moving material. Live frames already use `_correct_camera_frame`, as does detection; this change does not add distortion correction or modify calibration. Known resolution mismatch disables projection rather than drawing a falsely aligned layout.

All protected backend source, configuration, calibration and template files match the before-change hashes (`verification/live-timers-integrity.json`). No processing sequence, nesting algorithm, priority logic, export format or stored-data schema changed. No new real-camera cutting/export cycle was initiated; prior hardware/physical accuracy limitations still apply. No migration required.


### Replace unsupported Estimate placeholder

The former Estimate tile was static and had no calculation behind it. Replaced it with Material waste, using the existing `waste` percentage from the nesting result. Its existing DOM binding now drives the prominent numeric value; no new estimate, pricing or cutting-time model was added. New capture/reset shows a dash until a nesting result exists instead of showing an unmeasured zero. Updated `frontend/index.html`, the existing reset display in `frontend/app.js`, and a focused UI test. Asset version: `20261015-material-waste`.

All 68 UI tests passed (`verification/material-waste-ui-tests.txt`), including actual 25% waste, valid zero waste and reset. Four browser sizes passed existing navigation/overlay/timer checks. A full-page browser render confirmed the controlled result's 85.0% waste is visible and Not available is absent (`verification/material-waste-preview.png`). All 56 protected backend/configuration/calibration/template files remain unchanged; no new physical processing run was initiated.


### Compact workflow box and nesting-preview waste

Reduced workflow-box padding, heading size, icon size and timer spacing while retaining every timer and status. Added the existing nesting result's waste percentage beside placed pieces and utilization in the nesting preview summary. Valid zero waste displays 0.0%; absent waste displays a dash rather than an invented value. No calculations or processing algorithms were changed.

Modified `frontend/styles.css`, `frontend/app.js`, `frontend/index.html` (asset version `20261015-compact-workflow-waste`) and focused UI tests. All 69 UI tests passed (`verification/compact-workflow-ui-tests.txt`); four browser sizes passed existing navigation, overlay and timer checks. Visually inspected the compact box and the actual controlled result's 85.0% waste (`verification/compact-workflow-preview.png`). All 56 protected backend/configuration/calibration/template hashes remain unchanged.


## Daily production CSV — 15 October 2026

Daily files are stored in `data/production/production_YYYY-MM-DD.csv`, using Asia/Kolkata dates. One row is saved per successfully exported automatic cycle. Today’s `production_2026-10-15.csv` was created with headers only; no production data was invented. The next successful automatic export adds a record. New daily files are created automatically. Old per-cycle CSV/DXF/SVG files and formats are retained.

Columns include export date/time/timezone, stable workflow ID, cycle number, export status, project/source/vector/per-cycle CSV paths, sheath count, exported/requested piece counts, material/used area, utilization/waste percentages, per-stage/cycle seconds, nesting strategy, template-wise counts and per-sheath measurements. Counts represent exported cut plans, not hardware-confirmed physical cutting completion. Existing manual re-exports do not increment production. Historical cycles predating this feature are not automatically imported.

Modified files:

- `app/export/daily_production.py` (new): UTF-8 BOM CSV writer, India-local export-date grouping, duplicate checks using persistent workflow IDs, thread serialization, atomic replace/fsync, strict existing-header validation and exact saved-record replay. The per-cycle CSV’s original UTC timestamp fixes each cycle’s production date even when retried later.
- `app/main.py`: record daily production after vector/per-cycle CSV verification, while retaining the existing processing lock and Exporting stage until the daily record is durable. Add daily file metadata to the existing projects-list response, plus record-only `/api/production/retry` and saved-record `/api/production/recover` endpoints, guarded by existing mutation locking.
- `frontend/index.html`, `frontend/app.js`, `frontend/styles.css`: Data page daily CSV download links, current-failure Retry daily record, and recovery from saved export records. Existing controls, previews and per-cycle CSV links remain available. Asset version: `20261015-daily-production`.
- `tests/test_daily_production.py` (new) and `tests/test_workflow_ui.cjs`: focused ledger, recovery, locking, real controlled processing integration and Data UI tests.
- `data/production/production_2026-10-15.csv`: initial header-only daily file.
- Verification artifacts and one-time deployment helper/snapshot under `verification/`: test logs, browser evidence and session-preserving reload of the idle local server. Normal application launch scripts are unchanged.

Durability and recovery:

Each completed export first saves an immutable `.production.json` record beside its existing vector and detailed CSV. These new sidecars preserve the exact row and original measured times if the daily CSV is locked, a later cycle starts, or the server restarts. Data → Retry daily record repairs the current failed logging attempt and restores completion without recapturing or re-exporting. Data → Recover daily records from saved exports replays saved production records into their original daily files without duplicating rows or changing active processing. Recorded daily rows survive restarts; recovery can also recreate a deleted daily CSV. Corrupt headers/records produce an error rather than overwriting data.

Daily-log failure preserves the already saved vector/per-cycle CSV, marks the workflow failed with an actionable storage error and releases the lock. The existing five processing stages, calibration, detection/nesting algorithms, priorities, export formats and template storage are unchanged. Per-stage CSV times are the worker snapshot taken after successful vector/per-cycle export and before the daily-log commit; the final on-screen Export timer also includes ledger-write overhead. No new physical processing cycle was initiated for deployment.

Executed validation:

- Full Python suite: **247 passed**, one existing Starlette/httpx deprecation warning (`verification/daily-production-all-python-tests.txt`). Includes 14 daily-production tests: actual counts/precision/Unicode, two cycles, persistent duplicate checks, India midnight rollover, concurrent writes, atomic failure/retry, invalid headers, missing exports, conflicting IDs, real controlled multi-sheath exports, manual re-export exclusion, lock/completion behavior, failed-export exclusion, restart recovery and path/date validation.
- Node UI suite: **72 passed** (`verification/daily-production-ui-tests.txt`), including daily download links, record-only retry and recovery error restoration.
- Chrome: four sizes passed existing navigation/state, overlay/timer and new daily-download checks. Data-page screenshot: `verification/daily-production-data-page.png`.
- Live backend: new production endpoints loaded; GET daily CSV returned HTTP 200; existing cycle ID/number, nesting result and USB Camera index 0 / DirectShow / 1280x720 / ~30 FPS matched before and after the idle reload (`verification/daily-production-active-backend.json`). No new capture/nesting/export was triggered.
- Calibration/config/template hashes all unchanged (`verification/daily-production-integrity.json`). No migration required for existing templates or per-cycle files.

Usage: refresh the application, run automatic cycles as usual, then choose Process dropdown → Data to download the date’s CSV. Close the server-side daily CSV in Excel while logging; browser downloads are separate copies. Use the Data recovery controls if a record could not be saved. The one-time server reload helper in verification was used only to activate this change while retaining the idle session; use the existing application launcher for normal starts.


## Data tab daily production visualization — 2026-10-15

- `frontend/index.html`: date selection, refresh and CSV download, six metric cards, template count bars, exported cycle table. Existing measurements, export links, recovery and project controls retained.
- `frontend/app.js`: read-only parsing of existing daily CSV URLs, area-weighted utilization/waste, measured average cycle timing, stable template-ID aggregation, empty/error states and stale-response protection. No backend, processing, camera or storage changes.
- `frontend/styles.css`: responsive cards, template bars and internally scrollable cycle table.
- `tests/test_workflow_ui.cjs`: five focused dashboard tests plus the existing 72 regression tests; all 77 passed. Includes BOM/quoted/multiline/Unicode CSV, malformed data, duplicate IDs, exact weighted totals, missing measurements, date preservation, stale responses and read errors.
- Controlled Chrome verification at 1440×900, 1366×768, 1920×1080 and 760×1000: correct totals (2 cycles, 5 pieces, 72.5% utilization, 27.5% waste), cycle table and template bar, no horizontal page overflow or browser errors. Fixture values are controlled test data only; no actual production data written.
- Live server read-only checks confirmed new frontend and existing daily CSV availability. Actual production CSV currently contains headers and zero records; dashboard will show no exported cycles until a new automatic export records one. No physical capture/export performed for this UI change.
- Calibration/config/template hashes unchanged; workflow and backend remain running without restart. No migration required.
- Evidence: `verification/production-dashboard-ui-tests.txt`, `verification/production-dashboard-browser-checks.txt`, `verification/production-dashboard-live-checks.json`. Browser fixture and verifier updated only for controlled visual checks.


## True shared cut from the supplied sketch — 15 October 2026

Added a distinct `common_line` mode labelled Shared cut — cut common edges once, now the configured default for new jobs. Legacy `shared_side` remains available as Spaced side packing and still supports positive spacing. Shared cut forces effective inter-template spacing to zero while preserving sheath-edge clearance, template priority, orientation restrictions and per-sheath quantities.

Modified files and purpose:
- `app/nesting/nesting_engine.py`: route true shared cut through existing precision nesting with contact preference and zero spacing.
- `app/nesting/precision_nesting.py`: optional contact preference chooses full or partial shared edges among exact candidate positions; other mode behavior remains unchanged. Preserve piece count and priority ahead of cut-length tie breaks.
- `app/nesting/optimization.py`: register the new strategy.
- `app/export/common_line.py` (new): node and union boundary lines, not filled polygons, so common internal dividers remain once. Keep different sheath IDs separate; snap-round at 0.000001 mm for rotated-edge numerical agreement.
- `app/export/svg_export.py`, `dxf_export.py`: optional shared-cut path export. SVG remains mm vector output and DXF remains R2010/mm; ordinary modes retain separate closed outlines. Shared mode uses connected/open cut paths without adding closure edges.
- `app/main.py`: record actual zero spacing and route both normal and aligned exports using the saved layout strategy. Existing automatic workflow, background worker, lock and daily logging unchanged.
- `frontend/index.html`, `frontend/app.js`: clearly labelled mode, zero spacing lock and explanation, restore previous spacing when returning to other modes. Original runWorkflow and keyboard behavior unchanged.
- `app/config/config.yaml`: only nesting.strategy changed from accurate to common_line; all other settings retained.
- `tests/test_common_line.py` (new), `tests/test_workflow_ui.cjs`: focused geometry/export/workflow/UI coverage.
- `README.md`: operation, compatibility and vector path import guidance.

Validation: baseline 247 Python and 77 UI tests passed. Updated full suites: 261 Python and 79 UI tests passed, one existing deprecation warning only. Fourteen focused tests cover the supplied four-piece topology, partial-edge T junctions, internal-divider preservation, rotated SVG/DXF exports without duplicate segments, orientation/clearance, external sheaths kept separate, invalid geometry and actual controlled automatic two-sheath workflows with detailed and daily CSVs. Two UI tests cover zero spacing and settings transitions. Example dimensions are controlled values, not measurements inferred from the photo: outline sum 1000 mm, unique common-cut network 810 mm, shared segments saved 190 mm. Both SVG and DXF checks verify unique length and complete boundary coverage.

Live activation retained idle cycle 18/session/layout and USB camera settings. Protected camera calibration, original detection and crop, templates and production CSV hashes verified unchanged. No real production cycle or physical laser cut was run for this change. No existing templates or exports were migrated. The existing Word working-flow report describes the preceding implementation snapshot; this section records the new shared-cut behavior.

Limitations: this is a packing heuristic, not a global optimum guarantee. Common cuts require matching coincident edges; curved shapes touching at a point do not provide a shared segment. No kerf compensation or downstream machine behavior has been validated. Use a newly nested result; previously saved layouts retain their original export behavior. Evidence is in `verification/shared-common-cut/` including test logs, controlled exports, preview, backup and activation verification.


## Shared cut speed correction — 15 October 2026

The first real common-cut cycle spent over four minutes in nesting, while capture, correction and detection completed in about one second. Cycle 19 was stopped only after confirming nesting was active and export had not started. The captured image and detected polygons were retained; the interrupted attempt remains failed and was not recorded as completed production.

Changed `app/nesting/precision_nesting.py`, added `app/nesting/shared_search.py`, and added 18 focused tests in `tests/test_shared_cut_speed.py`. The common-cut path now filters parallel rectangle edge-contact candidates, calculates unambiguous rectangle overlap areas analytically with exact GEOS fallback near the existing tolerance, and avoids constructing rejected candidate polygons. A conservative feasible-center region rejects impossible fits and filters scans. Bounded caches reuse material expansion and corner-feasibility geometry. The expanded/contracted 0.001 mm helper regions are only conservative rejection filters; they do not expand the final material boundary or change accepted geometry. Final containment, overlap constraints and requested 0.1 mm refinement are retained. Small, nonparallel or custom shapes retain the exact fallback. No rotations were removed and no early time cutoff was introduced.

Verification: 279 Python tests and 79 UI tests passed, with one existing deprecation warning. Tests cover exact candidate equivalence across rotations, overlap tolerance, shared-contact shortlist completeness for non-overlapping candidates, original contact-choice comparison, custom fallback, exact fits and impossible fits. Existing priority, direction, shared-cut SVG/DXF, separate-sheath and production tests remain passing.

Timing evidence: the controlled four-piece example originally profiled at 2.713 seconds and subsequently about 0.29 seconds, with identical serialized layout. An intermediate search took 41.77 and 18.35 seconds on the two saved actual sheaths. The final unprofiled search took 2.88 and 1.20 seconds, approximately 4.08 seconds total, with exactly the same serialized placements and utilization (six pieces and one piece). The original multi-minute calculation was interrupted, so its unknown final runtime/result is not used for a like-for-like speed ratio. Times vary with workload and host load.

The update is active. Preserved the failed cycle's capture, detected geometry, original detector/crop/calibration/templates and production CSV hashes. The USB camera was reconnected through the existing endpoint after reload; index, resolution and FPS matched, and a live 1280x720 preview frame was verified. Benchmarks did not trigger a real capture workflow, production export or machine cut. Press Space for a fresh automatic cycle, or use Auto Nest to retry the retained capture. No migration, export-format or precision-setting changes.

Evidence: `verification/shared-cut-speed/` contains the stalled-cycle snapshot, original search backup, profiles, exact comparisons, test logs, final benchmark and activation verification.
