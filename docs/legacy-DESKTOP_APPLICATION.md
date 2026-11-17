# Windows desktop application

The desktop window displays the existing application and calls its existing local backend. Spacebar, capture, correction, detection, nesting, priority, CSV/Excel logging and export use the same handlers and processing functions. The normal start.bat browser launcher remains available.

## Install and open

Run dist/SheathTemplateCutting-Setup.exe and open the Sheath Template Cutting desktop shortcut. Installation is for the current Windows user. Python is included in the application folder. The window uses Microsoft Edge WebView2, which is already installed on this machine. Another PC needs WebView2 Runtime installed.

The folder version is dist/SheathTemplateCutting/SheathTemplateCutting.exe. Keep the entire folder together; the EXE depends on its bundled _internal directory.

## Your data

On this PC the desktop application detects and reuses C:/Users/Admin/cutting_image_based. It does not move or reset calibration, templates, captures, exports or production records. If the existing browser backend is already running with the same data folder, the desktop window uses it. Closing that window leaves the pre-existing backend running.

On a new computer without that project folder, data is stored in %LOCALAPPDATA%/SheathTemplateCutting/Workspace. Missing initial calibration, template and configuration files are copied from the packaged defaults. Existing files are never overwritten by initialization or updates. Production history is not shipped in the installer. Calibration must match that computer's actual physical camera setup.

An explicit existing folder can be selected with SheathTemplateCutting.exe --workspace "C:/path/to/project". The application refuses to attach to a backend using a different data folder.

The program is installed in %LOCALAPPDATA%/Programs/Sheath Template Cutting. Uninstall removes program files and shortcuts, retaining the separate data folder. Logs are in the data folder's logs/desktop.log.

## Closing

A second desktop launch does not create a second window or backend. Closing is blocked while an automatic workflow or an owned manual operation is active. Wait for processing and export to finish. When it owns the backend, the desktop application requests normal shutdown so the existing camera cleanup runs. It does not stop a backend that was already running before the desktop window opened.

## Rebuilding

Packaging files are desktop.py, desktop_runtime.py, requirements-desktop.txt and packaging/. The desktop dependencies are optional and separate from requirements.txt.

The build environment .venv-desktop uses the existing .venv-windows application packages without upgrading them. Its existing_application.pth points to that package directory. Install requirements-desktop.txt in the build environment, then run:

    .venv-desktop/Scripts/python.exe packaging/build_desktop.py
    .venv-desktop/Scripts/python.exe packaging/build_installer.py

The second step uses the Windows IExpress installer builder. Calibration, templates and saved configuration are bundled only as initial defaults. No production CSV, Excel report, capture or existing export is included.

## Verified scope

Verification records are under verification/desktop/. Tests cover the current workflow, UI, persistent paths, initialization that preserves existing files, matching-backend reuse, close guards and graceful shutdown. The compiled EXE has also been tested with no Python directory on PATH and outside the source working directory. A hidden native WebView2 window loaded the eight-section interface, live USB preview and Excel download control without UI errors. A fresh test workspace loaded the packaged calibration and all stored templates.

These checks do not certify physical cutting, dimensions on a different camera setup, or deployment on another Windows computer.


## Accounts in version 1.1

The updated desktop window opens the sign-in screen before loading protected camera, template or production data. On a new installation, create the first Admin account in that window; there is no shared default password. Use Account to add Editor/Manager or Read-only users. User accounts are kept in the persistent workspace security/accounts.sqlite3, which is preserved across updates and is not included in the installer. An existing running backend must be updated too; closing and reopening only a desktop window attached to an older backend does not update that backend.

The current account-enabled backend and installer have been updated together on this machine. The capture, correction, detection, nesting and export function bodies remain unchanged. Real hardware checks require the external USB camera to be connected.

Version 1.1.1 adds Delete beside each user in Account. Only Admin can delete other accounts, after confirmation. The signed-in account and last active Admin are protected. Deletion immediately revokes sessions and preserves production records and activity history.


## Automatic LaserCAD DXF handoff (1.4.6)

Successful manual DXF/CSV exports and completed automatic production records request standalone LaserCAD to open the exact saved DXF. CorelDRAW is no longer invoked. Polling never relaunches LaserCAD, and SVG exports do not launch it. The file is not resaved, rescaled or mirrored by this handoff, and no cutting command is issued.

Discovery checks registered LaserCAD.exe paths, the system-drive LaserCAD/AWCPrograms folder, Program Files/LaserCAD and PATH. The installed application here is C:/LaserCAD/AWCPrograms/LaserCAD.exe. An Admin can set lasercad.executable in Settings to an absolute LaserCAD.exe path, or set lasercad.auto_open_dxf to false. Missing software or launch failure leaves the exported DXF intact and shows a separate warning. A launch acknowledgement is not proof of successful import. Confirm the drawing is present at 1:1 in millimetres; use File > Import if required by that LaserCAD version.

LaserCAD's supplier manual documents DXF through File > Import: https://tagless.inkcups.com/wp-content/uploads/2017/08/LaserCAD-Software-Manual.pdf .

## Detection rollback (1.3.1, 16 October 2026)

At the user's request, sheath segmentation and detection were restored byte-for-byte from the pre-16-October-refinement backups, matching the previous afternoon's behavior. The 1.3.0 edge-refinement behavior and associated accuracy results described above are historical and no longer apply. Calibration, measurement calculations, workflow, accounts, nesting, exports, the then-current DXF handoff and UI were preserved. The clipping-warning display remains compatible with supplied metadata; the restored detector does not generate the new clipping flags. Physical measurement accuracy has not been certified.

## Release 1.4.0 (17 October 2026)

Adds Process > Cutting Time with per-sheath template outline estimates using an editable 100 x 100 mm square benchmark (5 seconds by default). Complete template outlines are counted; shared edges remain a conservative estimate. Travel and pauses are excluded.

Opening the shortcut again restores the existing desktop window without an already-open error dialog. Owned backend shutdown allows five seconds for lingering stream requests before cancelling them, so camera preview connections cannot indefinitely retain the desktop instance lock. Closing during processing is still guarded.

Setup now requests a normal close of this installation and waits up to 30 seconds. If an operation prevents closing, setup stops before replacing files. Existing calibration, templates, accounts and production data are preserved.
## Branding update 1.4.1

The supplied laser-cutting photo is retained in frontend/assets/brand-photo.jpg. Square PNG and multi-resolution ICO assets preserve the full photo with dark side padding. The header, sign-in view, browser icon, native title bar, executable, shortcuts, Installed Apps entry and release installer use these assets. Shortcuts reference a versioned ICO path so Windows can load the new artwork without deleting the user's icon cache. The application name and processing behavior are unchanged.

## Circular logo update 1.4.2

The supplied photo now uses a circular presentation with transparent corners in app-icon-v142.png and app-icon-v142.ico. The application header, sign-in screen, browser, native window, executable, shortcuts, Installed Apps entry and installer share that artwork. The original photo remains retained in brand-photo.jpg.

## Black title bar update 1.4.3

The native Windows title bar and border are explicitly black with white caption text. This application-level styling is applied when the window is shown and when its content loads, without changing the Windows theme. Standard window controls and the round logo remain available.

## Green-board preview and exposure update 1.4.4

The full-frame preview masks the background outside the convex green board and retains nearby ArUco markers with their white margins. Live masks refresh every 15 frames instead of freezing the first frame. A bounded gamma correction based on the green surface brightens the display without changing raw captures, calibration, sheath measurement, nesting or DXF geometry. Camera controls report driver rejection, use the appropriate auto-exposure flags for the capture backend and refresh the preview after accepted changes. Camera > Enable Auto Exposure restores automatic exposure on supported drivers. Exposure is not forced automatically; resolution, focus and saved calibration are preserved.

## Full-length sheath detection update 1.4.5

Surveyed marker centres define the measurement homography rather than a clipping rectangle. Detection now uses the full visible green-board surface and excludes actual marker margins, so long sheaths extending past the centre rectangle remain complete. The original detector and pixel-to-mm calibration are retained. Results touching the image or board/marker boundary are rejected before nesting. Display-only brightness correction is not used for measurements.

## Automatic LaserCAD preview (1.4.7)

After passing the saved DXF to LaserCAD, the handoff waits up to 30 seconds for a visible non-dialog window whose executable path and document filename match. It restores/activates that window, requests the verified Zoom to all objects menu command when available, and queues painting for the main window and children. No mouse clicks, placement changes, geometry edits or machine commands are used. A blocked foreground request, ambiguous window or refresh failure is reported separately while preserving the exported DXF. A refresh acknowledgement is not a visual inspection of the drawing.

## Repeated LaserCAD exports (1.4.8)

Handoffs are serialized through preview setup. Matching document titles are revalidated before four delayed readiness, activation, zoom and repaint attempts. Responsiveness and viewport commands use bounded native messages. Custom command bars without a Win32 menu use the installed English language resource to validate Zoom to all objects (32796); no unverified machine command is sent. A target document change aborts refresh instead of activating another file. Preview attempts and failures are logged; DXF, calibration and machine settings are preserved.

When Windows denies foreground focus, the preview window is shown at the top without activation or a permanent topmost setting. The read-only zoom command still updates its viewport. Resource validation handles both UTF-8 and UTF-16 LaserCAD language files.


## Matching LaserCAD layout and exact shared edges (1.4.9)

The installed ID 12 export profile now translates X by -1250 mm and reverses Y for the CAD upward-Y display: DXF X = design X - 1250, DXF Y = -design Y. ID 12 remains (0,0); dimensions and relative positions are unchanged, and LaserCAD displays the same left/right and top/bottom layout as the app. Negative coordinates are intentional for this top-right CAD reference. The previous positive-left/positive-down profile is backed up under verification/lasercad-layout-shared/before. Camera calibration is unchanged. Machine home configuration and import docking are separate from drawing orientation; the software check does not verify a physical cut.

Shared-cut search now keeps exact neighbour coordinates instead of rounding candidate centres to seven decimals. Previously a sub-micron rounding error could produce a thin apparent overlap and reject an otherwise valid common edge, leaving a piece separated. Shared-cut mode retains zero spacing, material clearance, quantities, and locked template directions. DXF/SVG keep internal dividers and emit each coincident segment once per sheath. Separate material islands or incompatible shapes cannot always share an edge.


## Shared cuts across angled packing frames (1.4.10)

The common-line search now adds exact contacts in the rectangles' own edge directions. A slightly angled sheath packing frame previously aligned rotated bounding boxes instead of actual edges, leaving small gaps even with zero spacing. Original candidate positions remain available; new contacts pass the same material-containment and overlap checks. Non-rectangular and nonparallel geometry retains its existing fallback. Camera calibration, ID 12 alignment, dimensions, template rotations, and export handoff are unchanged.


Complete layouts from both the original and edge-aligned contact search compete under the existing template-priority/material-usage score. After placing each complete layout, isolated pieces are revisited with all neighbours present. Only pieces with no shared edge are translated, so existing common cuts stay connected and dimensions, allowed directions, and selected quantities stay intact. Regression coverage includes the actual photographed sheath and small non-right-angle packing frames.
