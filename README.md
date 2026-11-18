# SIL Laser Automation

Camera-based sheath measurement, template nesting, DXF export and embedded LaserCAD automation for the SIL / Trocen AWC7813 workflow.

Current release: **v2026.11.18.1**.

## Install the desktop application

Open [Releases](https://github.com/Madhushree2024/SIL_laser/releases/latest) and download **TemplateCutting-Setup-With-Python-2026.11.18.1.zip**. Download the installer asset, rather than GitHub's automatically generated Source code ZIP.

1. Wait until the machine is idle, then close Template Cutting and LaserCAD normally.
2. Extract the entire installer ZIP into a new folder.
3. Run `TemplateCutting-Setup/Install.cmd`. Install the included prerequisites if requested, then run it again.
4. Open **Template Cutting Automation** using its desktop shortcut. Check the app version and LaserCAD paths under **Installation details**.
5. Check the USB device and your saved camera/marker/machine calibration before operating.

The package contains the Python runtime, LaserCAD, required runtime installers and `camera_calibration.npz`. Existing accounts, calibration, settings and job data are kept in the installed Workspace and are not replaced by a normal update. The supplied camera profile and marker positions are seed data, not a replacement for another machine's measurements.

## Current operator workflow

In Automated mode:

1. First fresh **Space**: camera refresh, capture, lens correction, four-marker perspective, outline detection, nesting, DXF export, embedded LaserCAD, configured USB selection, red-layer parameters, Download and Download Document.
2. Second fresh **Space**: operator authorizes **Start**.
3. After cutting has physically finished and the machine is idle, third fresh **Space**: acknowledges completion, resets the app cycle and opens **Process Sheath**. It does not send Start or capture again.
4. Next fresh **Space**: refreshes the camera for the next sheath.

Manual mode stops automation after Download Document. The remaining machine actions are manual; a fresh Space after cutting acknowledges completion and resets. Admin assigns other users' workflow modes in Account. Datum is a separate manual button and is not part of this key sequence.

The app does not infer physical cutting completion from controller telemetry. Download Document submission is not a verified transfer-success message. v2026.11.18.1 passed 133 focused software tests. Actual Windows x64 API declarations, hidden test controls, and pass-through hook installation/removal were validated. Keyboard events for the workflow were simulated; connected LaserCAD/cutting validation remains necessary.

## Git pull and download behavior

This Git checkout is source code, separate from the installed desktop application. **Cloning, downloading or pulling does not install an update, launch the app, send machine commands, change shortcuts or replace the installed Workspace.** There are no auto-deployment actions or repository hooks.

For normal use, install a tested Release ZIP deliberately while the machine is idle. Do not overwrite a live installation with a source checkout. Keep your existing Workspace when updating.

The normal installed Workspace is `%LOCALAPPDATA%/SheathTemplateCuttingAutomation/Workspace`; custom installations can use another explicitly configured folder. Accounts, logs, captures, jobs and production records are excluded from this repository. Runtime files must remain outside the checkout for safe pulls.

## Run the desktop source for development

Use Windows with Python 3.12 and the normal desktop runtime prerequisites. A source checkout does not contain the supplier's LaserCAD binaries; use the Release installer or install LaserCAD separately.

```powershell
python -m venv .venv-windows
.\.venv-windows\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv-windows\Scripts\python.exe tools/run_source.py
```

The helper creates missing seed files only and uses a separate persistent workspace at `%LOCALAPPDATA%/SheathTemplateCuttingAutomation-Source/Workspace`. Later pulls and starts do not overwrite its saved files. Select the real `LaserCAD.exe` path in Settings. Its initial path is intentionally blank rather than referring to a build on the original computer.

For the production desktop workflow, do not use the older browser launchers. Historical guides are retained in `docs/legacy-*`; this README and Release notes describe the current desktop workflow.

## Checks and building

```powershell
.\.venv-windows\Scripts\python.exe -m pytest tests/test_native_completion_focus.py tests/test_space_cycle.py tests/test_account_workflows.py -q
node --test tests/test_space_cycle_ui.cjs tests/test_account_workflows_ui.cjs
.\.venv-windows\Scripts\python.exe packaging/build_desktop.py
```

The last command builds the application into `dist/releases`. To recreate the full installer, extract a trusted previous installer into ignored `packaging/base_setup`, retaining its LaserCAD, prerequisite installers and seed assets, then run `packaging/package_portable.py`. Its output is under `dist/setup`. Build tools and local verification scripts may need their environment-specific paths adjusted; they are not required to use the published installer.

## Defaults in this release

Usable travel: **915 × 630 mm**. Cutting defaults: **90 mm/s**, **10% power**, **Cut**. Shared-cut spacing: **0 mm**; edge clearance: **3 mm**; rotation step: **5°**; nesting precision: **0.1 mm**. USB selection: `MachName:_AWC_D2713611` when present on the connected machine.

The four surveyed markers define camera measurements with ID 12 centre as the working reference. The nested template bounding box defines the separate local DXF job origin. Saved marker-to-machine calibration supplies photographed machine placement. Export and LaserCAD import coordinates are separate transformations; correct controller positioning mode still matters.


## Completion reliability in v2026.11.18.1

A committed reset includes a durable receipt identifying the completed cycle and its revision. If the reset response is lost, the UI reads backend status before deciding whether another reset is needed. It never replays an already-committed reset. Process Sheath is clicked at most once for that completion; the existing navigator recovers a failed navigation without creating another handler. A pending recovery cannot Start or capture. A later fresh Space begins the normal camera-refresh workflow only after recovery completes.

Reset publishes `ready_process` in memory only after the candidate has been durably saved. Failed saves retain the previous cycle, and uncertain storage outcomes block further reset attempts until storage access and the saved record can be verified. The event journal and cycle ID/revision checks are retained. First-Space processing and second-Space Start implementations are unchanged.
