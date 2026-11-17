# Separate image-based cutting copy

Project folder: `C:\Users\Admin\cutting_image_based`. Run `start.bat` here to open this copy at http://127.0.0.1:8001/. The original app remains at port 8000. This copy has its own Python environment, settings, templates, calibration and project data.

# Areca Cutter

Local Windows, macOS, and Linux web application for camera-assisted areca sheath detection, template nesting, and vector export for a CorelDRAW/CO2 laser workflow.

Everything runs on your computer. The app does not call cloud APIs, upload images, or use telemetry.

## Current MVP

- Camera discovery through OpenCV with DirectShow/Media Foundation on Windows, AVFoundation on macOS, and V4L2 on Linux instead of assuming camera index `0`
- Live MJPEG preview, capture, upload fallback, and camera controls where the UVC driver exposes them
- Camera calibration data model and checkerboard calibration API
- Scale calibration from a 100 mm reference square with homography storage
- Classical OpenCV sheath segmentation with saved mask and contour overlay
- Real-world millimetre geometry path from calibration into nesting/export
- Dynamic template library with starter square sizes and custom rectangle, square, circle or stored polygon templates
- Shapely-based polygon containment, spacing, collision checks, and heuristic nesting
- Browser workspace with overlays, grid, basic boundary editing, template selection, metrics, and export controls
- SVG and DXF vector export in millimetres with `SHEATH` and `CUT` layers
- Project save folders under `data/projects`
- Dummy `LaserController` abstraction for later machine/software integration

## Install on Windows

Install Python 3.11 or newer, then double-click `start.bat` in this folder.
The launcher installs missing dependencies, starts the server, and opens the application in your browser when ready. It reopens an existing Areca Cutter server, or chooses another local port if port 8000 is occupied by another application.
You can also run `setup.bat` separately to install dependencies.
Keep the server window open; press Ctrl+C to stop it.

Setup creates `.venv-windows` because the included `.venv` was created on macOS and cannot run on Windows. Do not activate the copied Mac environment.
To use PowerShell instead:

```powershell
python -m venv .venv-windows
.\.venv-windows\Scripts\python.exe -m pip install -r requirements.txt
.\.venv-windows\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Windows camera access uses DirectShow with a Media Foundation fallback. Enable camera access for desktop apps in Windows Settings, and close other applications using the camera before opening it here. Devices are listed by OpenCV index on Windows; select and preview the correct USB camera manually if multiple cameras are connected. Windows uses DirectShow device names to show only USB cameras and excludes built-in/virtual cameras. The USB camera opens automatically when the page loads, using the saved resolution and frame rate; verify its preview before capture.

The root `app` and `frontend` folders are used by these launchers. The nested `Nesting + Camera + LAser` folder is a separate copied snapshot.

## Install on macOS / Linux

Use Python 3.11 or newer. On macOS, give Terminal or the Codex app camera permission when prompted.

```bash
cd "/path/to/Nesting + Camera + LAser"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
chmod +x start.sh
./start.sh
```

Open [http://localhost:8000](http://localhost:8000).

## Camera Setup

### Calibrate the camera, then visually measure ArUco markers

The viewer now defaults to the surveyed four-marker layout in `data/calibration/aruco_viewer_bed_layout.json`: ID 11 centre = (0, 0), ID 12 = (1250, 0), ID 10 = (0, 560), ID 13 = (1250, 560), all in mm. These coordinates use the supplied **centre-to-centre** ruler measurements and assume a rectangular, flat marker layout. X goes from ID 11 toward ID 12; Y goes from ID 11 toward ID 10. All four markers must be visible. If one is missing, mm readings are disabled rather than reverting to a single-marker estimate. The 95 mm marker edges are independent checks; the 1250/560 centre distances are supplied calibration constraints, not independent validation. A warning appears if a marker edge differs by over 10%. Lens distortion and corner detection still affect interior measurements. This is a working-area coordinate frame, not verified laser-machine alignment.

Use `--single-marker` to explicitly return to the earlier reference-marker mapping, or `--bed-layout path.json` for a different surveyed layout. Restart a running viewer to load updated code and coordinates.

If the existing `camera_calibration.npz` belongs to your current camera and optical settings, use it directly:

```powershell
.\.venv-windows\Scripts\python.exe aruco_visual_check.py --camera 1 --lens-profile camera_calibration.npz --marker-size-mm 95
```

The preview automatically crops to the largest green working surface and masks the surrounding room. White markers and objects inside the surface remain visible. Measurement coordinates stay in the full corrected frame, so cropping does not change distances. Use `--no-crop-green` to show the whole frame.

The viewer reads its saved matrices and distortion coefficients and requests the saved calibration resolution (1920 x 1080 for the supplied file). It checks the distortion mapping and rejects mismatched frame dimensions. The supplied profile has a saved RMS of 1.52 pixels; verify physical distances with a ruler before relying on mm estimates. The reference side must match your measured outer black square size. No new checkerboard capture is needed to reuse this profile. Without an explicit path, the viewer uses its per-camera JSON if present, otherwise the root NPZ. `--recalibrate` explicitly creates a new JSON profile and preserves the existing NPZ.

To create a new calibration instead:

Print `data/calibration/checkerboard_9x6_30mm.svg` on A3 landscape at **actual size / 100%**. Disable fit-to-page and measure a square with a ruler: it must be 30 mm. The board has 10 x 7 squares, giving **9 x 6 inner corners**. The black-and-white pattern is 300 x 210 mm, so use A3 paper to retain white margins. Mount it flat on a rigid backing.

Close other camera streams, then run:

```powershell
.\.venv-windows\Scripts\python.exe aruco_visual_check.py --camera 1 --recalibrate --checkerboard 9 6 --square-size-mm 30 --reference-id 10 --marker-size-mm 95
```

First the checkerboard preview opens. Keep the camera focus, zoom and resolution fixed. Show the entire board; move it around the centre and image edges, vary distance, and tilt it in both directions. Press **Space** to save each view. At least **12 varied views** are required; near-duplicate views are rejected. **U** removes the last saved view. Press **C** to calculate calibration, save it and start measurement. **Q** cancels without starting measurement. Calibration with a fit RMS above 1 pixel is rejected; the RMS is a fit diagnostic, not a guarantee of measurement accuracy.

The profile is saved separately under `data/aruco_visual_check/camera_1_profile.json`. Subsequent live runs reuse this profile if `--recalibrate` is omitted. Recalibrate after changing camera, focus or zoom. Resolution must match the calibration exactly. Camera index 0 uses its own profile; use the correct index for your USB camera.

After calibration, put different ArUco IDs (for example 10 and 0 from `DICT_4X4_50`) flat on the same surface. Replace `95` with the actual measured side of ID 10's **outer black square**, excluding its white paper margin. The viewer applies lens correction before detecting corners and mapping dimensions. Camera calibration corrects the lens; the known reference marker size establishes the millimetre scale.

The overlay shows coloured outer black edges, corners C0-C3, pixel and mm edge lengths, centre crosses, centre distances (cyan), and shortest border gaps (orange). Edge names follow decoded marker orientation. The reference marker dimensions are supplied values; other marker sizes are estimated. Distances assume a shared flat surface and do not measure camera-to-marker depth. Missing reference IDs disable mm measurements.

**S** saves the annotated image and JSON under `data/aruco_visual_check`; **R** toggles rejected candidate outlines; **Q** exits. Real saved photos also require a lens profile:

```powershell
.\.venv-windows\Scripts\python.exe aruco_visual_check.py --image photo.jpg --lens-profile data/aruco_visual_check/camera_1_profile.json --marker-size-mm 95 --headless
```

A synthetic demo remains available without physical calibration:

```powershell
.\.venv-windows\Scripts\python.exe aruco_visual_check.py --demo --marker-size-mm 100
```

The Waveshare IMX335 5MP USB Camera (B) should appear as a UVC camera on macOS. Use the Camera page to refresh devices, select the correct camera, choose resolution/FPS, and open the stream.

macOS may expose only some controls, depending on the driver. Unsupported exposure, gain, brightness, or white-balance controls are hidden rather than treated as failures.

## Calibration Procedure

Camera calibration:

1. Print or display a checkerboard pattern.
2. Capture at least three images at different positions and angles.
3. Call `POST /api/calibration/camera` with `image_paths`, `checkerboard`, and `square_size_mm`.
4. The profile is stored in `data/calibration/camera_profile.json`.

Scale calibration:

1. Place a known square, for example 100 mm x 100 mm, in the work area.
2. Open the Calibration page.
3. Click the four square corners in order.
4. Save the scale profile.

The saved profile includes mm/pixel, working area dimensions, calibration resolution, and homography.

## Lighting And Background

Use a matte background that contrasts with the sheath. Avoid glare, strong shadows, and patterned table surfaces. A fixed camera mount and uniform side lighting will improve repeatability.

## Templates

Initial templates are square 3-10 inch sizes. Edit quantities and enabled states in the Templates panel. The architecture also supports rectangle, circle, and polygon template geometry; SVG/DXF import can be added without changing the nesting/export boundary.

## Nesting

The nesting engine works in millimetres. It checks that each template polygon remains inside the sheath polygon, respects edge clearance, and avoids overlap with spacing. The initial heuristic sorts templates, scans candidate positions/rotations, and keeps valid placements.

## Export

SVG and DXF exports are written under the active project folder. Files use millimetres, vector paths, clean closed geometry, and separate layers for reference sheath outline and cutting paths.

## DXF and laser origin alignment

In **Export > DXF → laser coordinates**, enable alignment and select a design reference: the original (0, 0), a full-layout bounding-box corner, its center, or a custom measured point in calibrated millimetres. Enter the laser X/Y coordinate at that same physical reference. For a manually positioned head with a job origin, enter (0, 0); for absolute bed coordinates, enter the measured machine position.

The mapping is `laser_point = laser_reference + rotation × axis_reversal × (design_point − design_reference)`. Reverse Y when the calibrated design and laser use opposite Y directions. Positive rotation goes from +X toward +Y. There is no scaling, and rotation is around the selected reference. Bounding references include the sheath even if its reference layer is omitted by an API export.

Choose **Preview Alignment** to inspect laser (0, 0), the mapped reference, and coordinate bounds with +Y upward. **Save Alignment Defaults** persists settings in `data/calibration/laser_alignment.json`; DXF export uses current form values, and API exports without explicit settings use the saved defaults. Each DXF has a matching `.alignment.json` with the settings, transform matrix, reference coordinates, and bounds. Saved projects include the saved alignment defaults. SVG keeps the original design coordinates.

Import the DXF in millimetres at 1:1 and preserve coordinates in the laser software. Automatic centering or a different origin mode can add another offset. Verify the reference and job frame in your laser workflow before cutting. Alignment exports coordinates; the app's laser controller remains a dummy integration. Recheck a custom physical reference after changing camera calibration or moving the camera.

For the four-marker setup, open **Calibration > Four-marker laser bed alignment**. Place equal-size markers with four unique IDs around the work area on the measurement plane. Generate their images in the app, print the indicated black-square size with white margins, and measure each center's laser X/Y coordinates. IDs 10–13 are editable defaults, not fixed corner assignments. Enter actual coordinates; equal marker size alone cannot establish where laser (0, 0) lies.

Capture or upload a photo containing all four markers and choose **Save Laser Bed From Four-Marker Photo**. The app matches centers by ID and saves a perspective mapping directly into laser millimetres as the active scale profile (`source: aruco_bed`). This replaces the prior scale and resets secondary DXF alignment offsets. Capture the sheath, detect it, and nest again. Single-marker scale detection will not override this bed origin. The saved mapping can be used with markers removed, provided the camera, resolution, lens calibration, and measurement plane remain fixed. Recalibrate after changing the setup. Four fitted points establish the mapping; verify a separate known point or distance to assess physical accuracy.

## Projects

Captured images, masks, contours, nesting results, exports, and `project.json` are saved under timestamped folders:

```text
data/projects/YYYY-MM-DD_HHMMSS/
```

## Troubleshooting

- `No cameras reported`: check USB connection and camera permissions for your operating system.
- `Camera is not open`: select a camera and click Open Camera before Capture.
- `Unable to detect sheath`: improve contrast/lighting or upload a clearer test image.
- `No valid template placement found`: reduce template quantity/size, spacing, or edge clearance.
- Export fails: run detection and nesting before exporting.

## Standalone ArUco camera coverage diagnostic

Run `camera_aruco_coverage.py` separately to estimate the camera's visible physical area from one known-size marker. It does not change application calibration. Close the application camera stream before opening the same camera in this script.

```powershell
cd "C:\Users\Admin\Nesting + Camera + LAser"
.\.venv-windows\Scripts\python.exe camera_aruco_coverage.py --camera 1 --marker-id 10 --marker-size-mm 95
```

Show a full `DICT_4X4_50` marker ID 10, with its outer black square printed at 95 mm per side, flat on the measurement plane. Adjust the ID, dictionary, and size arguments to match your marker. The live window shows actual frame resolution, estimated marker-plane bounds, four frame-edge lengths, and local mm/pixel. Press **S** to save the current annotated PNG and JSON under `data/camera_coverage`; press **Q** or **Esc** to quit. Measurements are unavailable whenever the marker is not detected.

For a saved photo, use `--image "C:\path\marker_photo.jpg"` instead of `--camera 1`. For one camera capture without a window, add `--snapshot`. Request another camera mode with `--width 1920 --height 1080`; the report shows the resolution actually returned by the camera.

Raw-image estimates include lens distortion. To apply your saved checkerboard lens calibration, add `--lens-profile "data\calibration\camera_profile.json"`. Omit that option for already-corrected photos. A tilted view covers a quadrilateral, so its enclosing width/height differs from its individual edge lengths. The origin is the selected marker's oriented top-left black-square corner; this diagnostic does not locate the laser origin.

## Tests

```bash
python -m pytest
```

To run tests on Windows:

```powershell
.\.venv-windows\Scripts\python.exe -m pytest
```

## Wide-angle view and lens correction

Use the Camera panel's View zoom slider (1x–3x) to narrow the displayed view. Zoom preserves the full captured image and keeps boundary overlays and reference-square clicks aligned.

For curved edges, open Calibration and capture or upload 10–15 checkerboard photos from the current camera, with the same resolution, at different angles and positions. Set the board's inner-corner counts and physical square size, then choose Calibrate Lens. The saved checkerboard profile takes precedence over the imported NPZ profile and corrects the live stream, captured-image preview, and detection. Original images and calibration photos remain raw to avoid repeated correction. An aspect-ratio mismatch skips correction; recalibrate if the camera mode changes.

After lens calibration, capture again and redo scale calibration. The previous scale profile is retained as a backup under data/calibration. The imported Mac-era profile has not been verified for your current camera.

## Separate marker and sheath photos

Capture or upload the full 95 mm ArUco marker, then choose Calibration > Save Scale From ArUco Photo. Its scale and plane homography are saved in data/calibration/scale_profile.json and survive restarts. Remove the marker, capture the sheath, then choose Sheath > Auto Detect to measure width, height, and area using the saved scale. A marker in the sheath photo takes precedence over the saved profile. Resolution mismatches are rejected.

Keep the camera, lens settings, camera mode, and measurement plane fixed between photos. Keep the sheath flat on the same surface where the marker was placed. Recalibrate after moving the camera or changing lens calibration. View zoom only changes the display and does not alter the saved image coordinates.

## Preferred template sizes

Open Templates > Preferences to add custom rectangle, square, or circle sizes. Choose millimetres or inches, enter dimensions and quantity, and choose whether to allow 90-degree rotation. Add & Save Size persists the template in the library for future sessions and Auto Nest. Use the preferred-size selector or Edit size in the Library tab to change saved custom sizes. Remove deletes a custom size from the library. Existing standard templates remain available; disable sizes you do not want included in a nesting run.

## Template nesting priority

In Templates > Preferences, use the up/down arrows to arrange sizes in the order you want, then click Save Nesting Order. All nesting strategies honor that priority: attempt each size up to its quantity, then try the next size in the remaining space. If a preferred size cannot fit, nesting continues with later sizes. Disabled templates are skipped. Priority and quantities remain saved in the template library.

## Dense nesting

Dense layout is the default nesting strategy. It searches exact boundary and neighbouring-piece contacts, compares several packing directions, and refines positions to the selected tolerance (0.5 mm by default). It keeps each template's actual geometry, allowed rotations, requested quantities, and saved priority. Placements are checked for containment, clearance, spacing, and overlap before being returned. Shared-cut mode also respects the entered spacing; circles and polygon templates use the shape-aware search instead of becoming rectangles.

This is a bounded heuristic search, not a guarantee of globally optimal packing. Refinement controls placement search precision, not camera measurement accuracy. Calibrate the camera and scale, check the detected boundary, and keep the measurement surface fixed. If all requested pieces are placed and space remains, increase quantities or enable smaller templates rather than changing their physical dimensions.

## Image-based cutting with the measured working area

This reuses the existing material-photo, preferred-template, nesting and vector-export workflow. The measured marker centres are ID 11 (0,0), ID 12 (1250,0), ID 10 (0,560), and ID 13 (1250,560), in mm. These are working-area coordinates. Laser alignment remains a separate export setting.

1. Close the standalone ArUco camera viewer so the web app can access the USB camera. Start `start.bat`, open the camera and capture a photo containing all four markers.
2. In Calibration, choose **Save Working Area From Four-Marker Photo**. This backs up the old scale and saves the surveyed mapping. Camera pose, lens settings and capture resolution must remain fixed afterward.
3. Place the material flat between the marker centres and capture it. Remove the checkerboard and other unrelated objects from this area. Choose **Sheath > Auto Detect**. Detection excludes the surrounding area and a 140 x 140 mm reference zone around each marker centre.
4. Inspect the detected dimensions and boundary. Use Edit Boundary if needed. The supplied centre spacings define the coordinate system; residual lens distortion and corner detection errors still affect interior measurements. Check dimensions against a ruler before cutting.
5. In Templates, choose saved sizes or add preferred rectangle, square or circle sizes with quantities, priority and permitted rotations.
6. Choose spacing and edge clearance, then **Auto Nest**. Inspect the placements on the captured image.
7. Export SVG or DXF. Preview and set DXF laser alignment separately; no physical cutting command is sent by this app.

The saved working-area mapping takes precedence over the old single-marker scale. A capture at another resolution is rejected until calibration is saved again. Existing templates, priorities and laser alignment settings are retained.

Exports from the separate app default to template cut paths only for both SVG and DXF. The sheath remains available for boundary detection, nesting containment and preview. DXF still uses the saved ID 12 origin alignment.


## Automatic capture-to-export workflow

Open this copy at port 8001; the USB camera opens automatically. In Templates > Library, select a stored
size and click **Load Selected Template**, or leave the enabled-library default.
Set quantities and Nesting Priority as usual. Choose DXF or SVG in Automatic export,
then click the workspace and press **Space** once (or use Capture to Export).
The app captures, corrects the lens, detects material and converts coordinates,
runs the existing priority-based nesting, and exports. Status stays busy until
export finishes; errors release the lock and leave diagnostics in the server log.
There is no Space Number selection. This app uses geometric nesting templates,
not a separate image-template matching algorithm.

Space is ignored in editable fields and native controls, during boundary editing
and calibration, on key repeat, and when the page lacks focus. The server rejects
conflicting mutations from any browser tab during a job. If the connection drops,
the page waits for a confirmed server status before unlocking.

Library supports adding/editing all IDs, explicit loading, refresh, and confirmed
deletion. Preferences contains Nesting Priority; the size editor is in Library.
An explicitly loaded size is used alone. The default processes all enabled sizes,
in priority order. Unload by selecting All enabled library templates and Load.
Priority changes retain the existing Save Nesting Order behavior. Existing JSON
IDs, metadata, calibration and export formats need no migration.

Verification: run `.\.venv-windows\Scripts\python.exe -m pytest -q` and
`node --test tests/test_workflow_ui.cjs`. See `WORKFLOW_IMPLEMENTATION_REPORT.md`
for evidence and live-hardware limitations.


## Visual cycles

The workspace shows Capture, Lens correction, Detection, Nesting and Export with
Waiting, Working, Done or Failed states and measured stage durations. Buttons let
you inspect the live camera, captured frame, corrected image, detected boundary,
segmentation mask and nesting layout. Visual outputs become available after their
real processing stage finishes. A completed export keeps its file link and result
visible and returns the UI to Ready for the next cycle. Replace the piece, click
the workspace, and press Space to capture the next piece. There is no automatic
repeat loop. A failed export stays Failed and can be retried. Uniform blank camera
frames are rejected before detection or export; inspect lighting, lens cover and
the USB connection, or use Open USB Camera to reconnect.

Cycle numbering and status are retained for the current server process. The
server preserves the last completed job while the UI presents readiness for the
next one. Existing project folders and exports remain saved after restart.


## Live refresh and dimensions

Ready automatically returns to live USB video after export. Use Refresh camera
above the viewer, or Refresh in Camera, to reconnect the USB device. Reconnection
preserves current resolution/FPS and blocks new workflow starts until it ends.
Saved captured/corrected/boundary/mask/layout views remain available for review.

Width, height and area appear above the viewer after detection. New results show
edge-aligned enclosing dimensions; legacy data is labelled X/Y spans. Show on
image adds measurement arrows to saved image views. In live mode these values are
labelled Last captured piece and update after the next capture/detection. Existing
calibration accuracy limitations still apply. Close the old server terminal and
run start.bat again before refreshing the page to load backend changes.


After nesting, use Full nesting preview to inspect the placed templates on the
captured image. Auto Nest opens this view immediately. The Last nesting preview
card remains visible when export finishes and the camera returns to live video;
Open full nesting preview reopens that saved result without recalculation/export.


### Rectangle cutting direction

In Templates → Add template (or Edit selected), choose Rectangle and set
Rectangle cutting direction to Horizontal only, Vertical only, or Either
direction. Horizontal places the entered width along X and height along Y;
Vertical places the entered width along Y and height along X. Dimensions are
not changed. Save the template before running. Fixed-direction rectangles stay
locked during nesting and export; when Shared cut side is selected the existing
precise nesting path handles these rectangles. Existing custom rotation angles
are retained unless you explicitly choose a different direction. No template
migration is needed.


### Faster dense nesting

Dense nesting now batches fallback positions across rows and avoids expensive
polygon-distance work for distant pieces and overlapping detailed outlines.
Search order, refinement, packing directions, priorities and rotation settings
remain unchanged. Speed depends on boundary complexity and template geometry.
The saved camera boundary remains slower than simple test layouts; current
benchmarks are recorded in verification/nesting-speed-summary.json.


### Multiple sheaths and shared-cut nesting

Place separate sheaths in the calibrated working area with a visible green gap
between them. Detection now retains every valid material contour above the
existing minimum-area threshold, rather than only the largest. Each detected
sheath has its own width, height, area and cut count. Use Show dimensions for to
inspect one sheath; dimensions on the image and the number-to-size legend identify
the individual sheaths. Template quantities and nesting priority apply separately
to every sheath. Space still captures once and exports once for the complete
capture. Cuts and optional sheath outlines remain separate in one SVG/DXF file,
using their actual working-area coordinates and one common laser alignment.

Shared cut side now evaluates its existing candidate positions in batches and
stops at the first valid position in the same scoring order. A controlled
13-piece rectangular benchmark improved from 9.02s to 0.22s with identical output.
Complex camera contours and templates that require the precise fallback can
remain slow; the archived-camera benchmark was essentially unchanged at 33s.
Touching/overlapping sheaths cannot reliably be identified as separate pieces
by the existing classical segmentation. Keep a visible gap. No calibration or
template migration is required.


### Finer refinement and CSV data

The default Placement refinement is now 0.1 mm, saved through the existing
nesting settings. Dense and fixed-angle processing reuse the precise engine;
shared-cut rectangles/squares also reuse its exact contacts, bounded fallback
search and local refinement below 0.5 mm. Choose 0.5 mm or greater to restore
the faster shared-cut search. Finer refinement can take longer.

Pixel-to-mm conversion uses double precision with the unchanged saved calibration.
Dimensions display two decimal places; CSV data preserves their underlying
precision. Decimal places and a 0.1 mm nesting step do not certify camera accuracy.
The previously supplied 263 × 262 mm sample still measures about 272.12 × 264.64 mm
with the existing calibration; that physical discrepancy remains unresolved.

Every successful manual or automatic DXF/SVG export saves a matching .csv beside
it in the project's exports folder. Download CSV data appears in the completed
cycle and export result. One CSV covers all sheaths, with record_type rows for
sheath summaries, individual cuts and unplaced templates. It includes dimensions,
quantities, priorities, template sizes, source image, calibration snapshot,
refinement, spacing, original coordinates and actual exported coordinates.
SVG export coordinates reflect the existing three-decimal file formatting;
source geometry retains full precision. Times use the explicitly marked UTC
column. UTF-8 BOM supports spreadsheet import. CSV writes are atomic; a CSV
failure marks the export stage failed even if the vector file has already saved.


## Daily production records

Each successfully exported automatic cycle adds one row to `data/production/production_YYYY-MM-DD.csv` (Asia/Kolkata date). Download these files from the Data section. Records contain exported piece/sheath counts, utilization, waste, measured stage times, template counts and links to the original exports. Manual re-exports do not increase daily totals; these are exported-plan counts rather than machine-confirmed physical production.

If a daily CSV cannot be written, the vector and per-cycle CSV remain saved. Close the daily CSV in Excel, check storage permissions/space, and choose **Retry daily record** in Data. **Recover daily records from saved exports** replays the exact saved production records after a restart or later cycles, without duplicating rows, capturing images or exporting again. Existing per-cycle files remain unchanged. New `.production.json` sidecars preserve recovery data; do not remove them if recovery is needed. Cycles predating this feature are not imported automatically.


## True shared cut

The default nesting strategy is now **Shared cut — cut common edges once** (`common_line`). It uses zero spacing between templates, prefers full or partial edge contact, keeps the selected sheath-edge clearance, template priorities and allowed cutting directions, and processes quantities on each sheath independently. SVG and DXF contain a unique network of cutting lines: shared dividers remain in the file but each coincident segment is exported once. CSV records remain per sheath and per piece for measurements and production counts.

Refresh the application and run a new Spacebar cycle or Auto Nest before exporting. Older layouts retain the strategy that created them. The previous `shared_side` strategy remains available as **Spaced side packing**; other strategies retain their configured spacing. Switching away from Shared cut restores the previous spacing field value. Existing templates require no migration.

Common cuts are open or connected vector paths rather than a separate closed outline for every piece. Preserve those paths when importing into the laser software; automatically closing open paths would add unintended cuts. No new kerf compensation or physical laser control has been added. The controlled reference example and export checks are under `verification/shared-common-cut/`.


Shared-cut performance uses conservative rectangle contact and feasible-position filters followed by exact material and overlap validation. Requested refinement, priorities, cutting directions and unique cut paths are retained. The checked saved two-sheath job completed nesting in about four seconds with the same seven reference placements. Timing depends on geometry and template quantities. Retry the retained image with Auto Nest or press Space for a fresh automatic cycle after refreshing the page.


### Readable daily Excel reports

Data now offers **Download Excel report** for the selected production date. A formatted `data/production/production_YYYY-MM-DD.xlsx` is also refreshed automatically after a daily CSV record is saved. Existing daily CSVs and per-cycle exports retain their original formats.

The workbook contains Daily summary, Production log, Sheath measurements, and Piece totals. It uses readable template names, India-local export dates, typed numbers, percentages, widths and heights in millimetres, and areas in square centimetres. Summary utilization and waste are weighted by material area. Blank measurements and times remain unknown. Counts describe exported plans, not confirmed physical cuts.

Excel is a generated snapshot: manual workbook edits are replaced when it refreshes. Close the workbook before processing if you want its saved copy to update automatically. If Excel locks the file, the original CSV continues recording production; close Excel and use Data > Download Excel report to refresh it. Downloading does not recapture, nest, export a cutting plan, or add production rows. The application fills a bundled formatted template using the Python standard library, without new runtime dependencies.


### Local accounts and access roles

The application now requires sign-in. On first launch, create the first Admin account with a unique username and a password of at least 12 characters. No default account or password is shipped. Use **Account** in the header to change your password. Admins can add users, change roles, enable or disable accounts, reset another user's password, and review recent activity. Sign out before handing the computer to someone else.

| Role | Access |
| --- | --- |
| Admin | All existing sections and operations, calibration, camera controls, settings, users and activity log. |
| Editor / Manager | View all sections, operate or reconnect the camera, manage templates and priorities, run jobs, export and recover production reports. Calibration, camera-control adjustments, global settings and user management remain protected. |
| Read-only | View previews, dimensions, template and priority information, nesting results and production reports; download existing CSV and Excel reports. Cannot process, edit templates, export a new cutting plan, change settings or recover/write production records. |

Permissions are enforced on the backend as well as the UI. First-run setup is available once; the last enabled Admin cannot be disabled, demoted or deleted. Admins can delete other users after confirmation; their sessions are revoked and production records and activity history are retained. Your signed-in account cannot be deleted. Roles, disabled status and password resets are checked for every API request; role changes and password resets revoke that user's current sessions. Camera streams also recheck the session. Sessions expire after 12 hours. Login throttling persists across restarts. Password changes require the current password and sign the account out.

Accounts, hashed sessions and the activity log are stored in `security/accounts.sqlite3` inside the persistent workspace, outside the downloadable `data/` directory. Passwords use salted PBKDF2-HMAC-SHA256 with 600,000 iterations. Session cookies are HttpOnly and SameSite Strict; protected writes also require a session CSRF token and same-origin checks. The application remains bound to localhost. Guidance: [OWASP password storage](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html) and [OWASP session management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html).

The activity log identifies accepted workflow requests by the exact workflow ID returned to the caller, and records account changes and API writes without logging passwords or request bodies. An already accepted workflow continues to finish normally if the user session expires or is revoked; future requests require valid authorization. Account actions do not acquire the processing lock, so a user can sign in while a job is running.

These are application roles, not Windows accounts or filesystem permissions. People who can directly change the Windows application/data files can also change the local installation. Updates and uninstall preserve the separate accounts database. Installer defaults never include accounts, sessions, activity history or production history from this machine.


## Automatic CorelDRAW DXF handoff (1.2.0)

After a manual DXF and its CSV save successfully, the app requests CorelDRAW to open that exact saved DXF. Automatic cycles wait until the daily production record also saves; a failed record defers the handoff until a successful Retry daily record. Workflow polling and previews do not launch CorelDRAW. SVG exports are unaffected. The DXF is never resaved or rescaled by this integration, and it does not trigger cutting.

CorelDRAW must be installed separately on the Windows PC running the application. Discovery checks Windows App Paths, standard Program Files/Corel locations and PATH. For a custom location, an Admin can add a top-level section in the existing Settings JSON editor (preserving all other settings):

    "coreldraw": {
      "auto_open_dxf": true,
      "executable": "C:/Program Files/Corel/CorelDRAW Graphics Suite 2026/Programs64/CorelDRW.exe"
    }

The executable value is optional; omit it to use discovery. Set auto_open_dxf to false to disable automatic opening. Use the installed version's actual path. Missing CorelDRAW, invalid configuration and launch errors show a separate warning; they do not turn an otherwise successful export into a failed export. A process-start acknowledgement is reported as Sent to CorelDRAW, not proof that a drawing imported successfully. CorelDRAW may require an import dialog; use millimetres and 1:1, and verify dimensions before cutting.

Corel documents opening files on application launch: https://learn.corel.com/how-to/questions/coreldraw/coreldraw-questions-session-5/ . Its DXF format guidance is https://help.coreldraw.com/CorelDRAW/540111192/Documentation-Windows/CorelDRAW-en/CorelDRAW-AutoCAD-Drawing-Database-DWG-and-AutoCAD-Drawing-Interchange-Format-DXF.html . Real CorelDRAW import could not be verified at implementation because CorelDRAW was not installed.


## Detection edge refinement (1.3.0)

Green-board detection uses the raw colour boundary instead of an expanded green mask, with 3px elliptical material cleanup instead of 71px closing. Genuine notches and small visible gaps remain unavailable for cutting. Dark reddish wood grain is included without filling green cutouts. A detected green board with no valid sheath no longer falls back to a board-sized material outline. The existing non-green-background fallback is unchanged.

Material is segmented before applying the existing calibrated working-area mask. A per-sheath clipped_to_workspace flag drives visible usable-portion-only warnings. Nesting and measurements continue to use the in-area polygon; the calibration, marker exclusion zones, measurement calculations, workflow, exports and CorelDRAW handoff are unchanged. No calibrated limits are expanded.

Validation records are in verification/detection-accuracy-current. Ten saved real captures were replayed: nine retained three separate sheaths, while one underexposed false-board detection was rejected. Controlled notched-mask IoU improved from 94.31-95.73% to 99.988%. This is pixel-mask accuracy on a synthetic fixture, not physical millimetre certification. For the user-reported 265 x 275 mm small sheath in the latest saved capture, the refined outline with the unchanged saved mapping measured about 283.10 x 277.62 mm. A read-only four-marker mapping recalculation measured about 280.13 x 274.15 mm. The width discrepancy remains unresolved; no scale factor was fitted to force the stated dimensions and no calibration files were overwritten. Marker distances were confirmed by the user as 1250 x 560 mm centre-to-centre. Physical verification with a flat reference at the working plane is still required.


## Detection rollback (1.3.1, 16 October 2026)

At the user's request, sheath segmentation and detection were restored byte-for-byte from the pre-16-October-refinement backups, matching the previous afternoon's behavior. The 1.3.0 edge-refinement behavior and associated accuracy results described above are historical and no longer apply. Calibration, measurement calculations, workflow, accounts, nesting, exports, CorelDRAW handoff and UI were preserved. The clipping-warning display remains compatible with supplied metadata; the restored detector does not generate the new clipping flags. Physical measurement accuracy has not been certified.
