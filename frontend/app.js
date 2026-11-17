const state = {
  config: {},
  savedCalibration: null,
  workspaceLayoutDirty: false,
  workspaceDimensionsDirty: false,
  workspaceEditVersion: 0,
  workspaceSaving: false,
  calibrationReadVersion: 0,
  workspaceGeometryVersion: 0,
  savedAlignment: null,
  user: null,
  csrfToken: null,
  selectedTemplateId: null,
  workflowBusy: false,
  pendingMutations: 0,
  pendingDisplayUpdates: 0,
  cameraRefreshing: false,
  measurements: null,
  workflowPreview: null,
  workflowPreviewManual: false,
  workflowArtifacts: {},
  visualWorkflowId: null,
  templates: [],
  sheathMm: null,
  sheaths: [],
  selectedSheathId: null,
  sheathPixel: null,
  scale: null,
  placements: [],
  selectedPlacement: -1,
  editBoundary: false,
  scalePoints: [],
  imageNatural: { width: 1280, height: 720 },
  previewCrop: null,
  activeView: "camera",
  viewZoom: 1,
  checkerboardPaths: [],
};

const $ = (id) => document.getElementById(id);
const cameraImage = $("cameraStream");
const overlay = $("overlay");
const ctx = overlay.getContext("2d");

function displayError(error, path='') {
  if(error?.userMessage)return error.userMessage;
  return window.RolePresentation?.error(error,{role:state.user?.role,path}) ?? (error?.message || String(error));
}
function roleCopy(full, compact) {
  return ['editor','viewer'].includes(state.user?.role) ? compact : full;
}

async function api(path, options = {}) {
  const mutation = ["POST", "DELETE", "PUT", "PATCH"].includes(options.method);
  if (mutation && !path.startsWith('/api/auth/') && !hasPermission(actionPermission(path))) {
    throw new Error(displayError('Your account does not have permission for this action.',path));
  }
  // The embedded pane renews its display lease every 900 ms. This is not a
  // configuration/job mutation and must not silently swallow an operator's Space.
  // Hide/detach and every other mutation remain blocking; permissions/CSRF apply.
  let displayOnly = false;
  if (mutation && path === '/api/laser-control/lasercad-host') {
    try { displayOnly = JSON.parse(options.body).action === 'show'; } catch (_) {}
  }
  const counter = displayOnly ? 'pendingDisplayUpdates' : 'pendingMutations';
  if (mutation) state[counter] = (state[counter] || 0) + 1;
  try {
    let response;
    try {
      response = await fetch(path, {
        ...options,
        headers: { ...(options.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
          ...(options.headers || {}), ...(mutation && state.csrfToken ? { "X-CSRF-Token": state.csrfToken } : {}) },
      });
    } catch (error) {
      $("statusPill").textContent = "SERVER OFFLINE";
      $("statusPill").classList.add("warn");
      throw new Error(displayError("Cannot reach Areca Cutter. Run start.bat, keep its terminal open, then refresh this page.",path));
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = Array.isArray(data.detail) ? data.detail.map((item) => item.msg).join("; ") : data.detail;
      const error = new Error(detail || `Request failed: ${path}`);
      error.status = response.status;
      error.userMessage = displayError(error,path);
      if (response.status === 401 && !path.endsWith('/login')) lockAccess(false, 'Your session ended. Sign in again.');
      throw error;
    }
    return data;
  } finally { if (mutation) state[counter]--; }
}

function message(text, error = false) {
  if(error)text=displayError(text);
  if (error && document.body?.dataset?.auth === 'locked') $('authError').textContent = text;
  const node = document.createElement("div");
  node.className = `message${error ? " error" : ""}`;
  node.textContent = text;
  $("messages").prepend(node);
}

function fitCanvas() {
  const rect = overlay.getBoundingClientRect();
  overlay.width = Math.max(1, Math.round(rect.width * devicePixelRatio));
  overlay.height = Math.max(1, Math.round(rect.height * devicePixelRatio));
  positionCameraImage();
  draw();
  const preview = $("nestingPreviewCanvas");
  if (preview.previewResult) renderNestingPreview(preview.previewResult);
}

function isLayoutView() {
  if (state.workflowPreview) return state.workflowPreview === "layout";
  return ["nesting", "export"].includes(state.activeView);
}

function canProjectLayoutOnPhoto() {
  if (state.workflowPreview === "layout" && !cyclePreviewSource("layout")) return false;
  return (state.workflowPreview ? state.workflowPreview === "layout" : state.activeView === "nesting") && state.scale?.homography && state.sheathPixel;
}

function updateViewerMode() {
  const viewer = overlay.closest(".viewer");
  viewer?.classList.toggle("layout-mode", isLayoutView() && !canProjectLayoutOnPhoto());
  viewer?.classList.toggle("photo-layout", canProjectLayoutOnPhoto());
}

function worldBounds() {
  const points = [...(state.sheaths.length ? state.sheaths.flatMap((piece) => piece.mm_polygon || []) : state.sheathMm || []), ...state.placements.flatMap((p) => p.polygon)];
  if (!points.length) {
    const width=Number(state.config.workspace?.width_mm) || 915;
    const height=Number(state.config.workspace?.height_mm) || 630;
    return worldYUp() ? {minX:-width,minY:-height,maxX:0,maxY:0} : {minX:0,minY:0,maxX:width,maxY:height};
  }
  return {
    minX: Math.min(...points.map((p) => p[0])),
    minY: Math.min(...points.map((p) => p[1])),
    maxX: Math.max(...points.map((p) => p[0])),
    maxY: Math.max(...points.map((p) => p[1])),
  };
}

function toCanvas(point) {
  const b = worldBounds();
  const w = Math.max(1, b.maxX - b.minX);
  const h = Math.max(1, b.maxY - b.minY);
  const scale = Math.min((overlay.width - 40) / w, (overlay.height - 40) / h);
  return [20 + (point[0] - b.minX) * scale, 20 + (worldYUp() ? b.maxY-point[1] : point[1]-b.minY) * scale];
}

function fromCanvas(x, y) {
  const b = worldBounds();
  const w = Math.max(1, b.maxX - b.minX);
  const h = Math.max(1, b.maxY - b.minY);
  const scale = Math.min((overlay.width - 40) / w, (overlay.height - 40) / h);
  return [b.minX + (x - 20) / scale, worldYUp() ? b.maxY-(y-20)/scale : b.minY+(y-20)/scale];
}

function imageRect() {
  const naturalWidth = cameraImage.naturalWidth || state.imageNatural.width || 1;
  const naturalHeight = cameraImage.naturalHeight || state.imageNatural.height || 1;
  state.imageNatural = { width: naturalWidth, height: naturalHeight };
  const crop = state.previewCrop;
  const cropX = crop?.green_found ? crop.x : 0;
  const cropY = crop?.green_found ? crop.y : 0;
  const cropWidth = crop?.green_found ? crop.width : naturalWidth;
  const cropHeight = crop?.green_found ? crop.height : naturalHeight;
  const imageAspect = cropWidth / cropHeight;
  const canvasAspect = overlay.width / overlay.height;
  const fittedWidth = canvasAspect > imageAspect ? overlay.height * imageAspect : overlay.width;
  const fittedHeight = canvasAspect > imageAspect ? overlay.height : overlay.width / imageAspect;
  const width = fittedWidth * state.viewZoom;
  const height = fittedHeight * state.viewZoom;
  return { x: (overlay.width - width) / 2, y: (overlay.height - height) / 2, width, height, naturalWidth, naturalHeight, cropX, cropY, cropWidth, cropHeight };
}

function toImageCanvas(point) {
  const rect = imageRect();
  return [rect.x + ((point[0] - rect.cropX) / rect.cropWidth) * rect.width, rect.y + ((point[1] - rect.cropY) / rect.cropHeight) * rect.height];
}

function fromImageCanvas(x, y) {
  const rect = imageRect();
  if (x < rect.x || y < rect.y || x > rect.x + rect.width || y > rect.y + rect.height) return null;
  return [rect.cropX + ((x - rect.x) / rect.width) * rect.cropWidth, rect.cropY + ((y - rect.y) / rect.height) * rect.cropHeight];
}

function setPreviewCrop(crop) {
  state.previewCrop = crop || null;
  const status = $("greenCropStatus");
  if (status) status.textContent = crop?.green_found ? "Green working area cropped; measurement coordinates preserved." : "Waiting for a visible green working area.";
  positionCameraImage();
  draw();
}

function positionCameraImage() {
  const rect = imageRect();
  const ratio = window.devicePixelRatio || 1;
  cameraImage.style.inset = "auto";
  cameraImage.style.objectFit = "fill";
  cameraImage.style.width = `${rect.width * rect.naturalWidth / rect.cropWidth / ratio}px`;
  cameraImage.style.height = `${rect.height * rect.naturalHeight / rect.cropHeight / ratio}px`;
  cameraImage.style.left = `${(rect.x - rect.cropX * rect.width / rect.cropWidth) / ratio}px`;
  cameraImage.style.top = `${(rect.y - rect.cropY * rect.height / rect.cropHeight) / ratio}px`;
  cameraImage.style.transform = "none";
  // Clip the masked image to its crop box, including letterboxed views.
  cameraImage.style.clipPath = `inset(${rect.cropY / rect.naturalHeight * 100}% ${(rect.naturalWidth - rect.cropX - rect.cropWidth) / rect.naturalWidth * 100}% ${(rect.naturalHeight - rect.cropY - rect.cropHeight) / rect.naturalHeight * 100}% ${rect.cropX / rect.naturalWidth * 100}%)`;
}

function startLivePreview() {
  state.workflowPreview = "live";
  state.previewCrop = null;
  cameraImage.src = `/api/camera/stream?t=${Date.now()}`;
  positionCameraImage();
  updateDimensionSummary();
}

let cropPollRunning = false;
setInterval(async () => {
  if (cropPollRunning || !cameraImage.src.includes("/api/camera/stream")) return;
  cropPollRunning = true;
  try {
    const data = await api("/api/camera/preview-crop");
    if (cameraImage.src.includes("/api/camera/stream") && JSON.stringify(data.preview_crop) !== JSON.stringify(state.previewCrop)) setPreviewCrop(data.preview_crop);
  } catch (_) { /* Camera readiness and errors are handled by the camera controls. */ }
  finally { cropPollRunning = false; }
}, 500);

function invert3x3(m) {
  if (!Array.isArray(m) || m.length !== 3 || m.some((row) => !Array.isArray(row) || row.length !== 3)) return null;
  const [a, b, c] = m[0];
  const [d, e, f] = m[1];
  const [g, h, i] = m[2];
  const A = e * i - f * h;
  const B = c * h - b * i;
  const C = b * f - c * e;
  const D = f * g - d * i;
  const E = a * i - c * g;
  const F = c * d - a * f;
  const G = d * h - e * g;
  const H = b * g - a * h;
  const I = a * e - b * d;
  const det = a * A + b * D + c * G;
  if (Math.abs(det) < 1e-9) return null;
  return [
    [A / det, B / det, C / det],
    [D / det, E / det, F / det],
    [G / det, H / det, I / det],
  ];
}

function transformHomography(point, matrix) {
  const [x, y] = point;
  const w = matrix[2][0] * x + matrix[2][1] * y + matrix[2][2];
  return [(matrix[0][0] * x + matrix[0][1] * y + matrix[0][2]) / w, (matrix[1][0] * x + matrix[1][1] * y + matrix[1][2]) / w];
}

function mmToPixel(point) {
  const inverse = invert3x3(state.scale?.homography || []);
  return inverse ? transformHomography(point, inverse) : point;
}

function toProjectedImageCanvas(point) {
  return toImageCanvas(mmToPixel(point));
}

function drawPolygon(points, stroke, fill = "transparent", width = 2, mapper = toCanvas, context = ctx) {
  if (!points || points.length < 2) return;
  context.beginPath();
  points.forEach((point, index) => {
    const [x, y] = mapper(point);
    if (index === 0) context.moveTo(x, y);
    else context.lineTo(x, y);
  });
  context.closePath();
  context.fillStyle = fill;
  context.strokeStyle = stroke;
  context.lineWidth = width * devicePixelRatio;
  context.fill();
  context.stroke();
}

function drawPlacementLabel(text, x, y) {
  ctx.font = `${12 * devicePixelRatio}px system-ui`;
  const paddingX = 5 * devicePixelRatio;
  const paddingY = 3 * devicePixelRatio;
  const metrics = ctx.measureText(text);
  const boxWidth = metrics.width + paddingX * 2;
  const boxHeight = 18 * devicePixelRatio;
  ctx.fillStyle = "rgba(8, 9, 9, 0.68)";
  ctx.fillRect(x - boxWidth / 2, y - boxHeight / 2, boxWidth, boxHeight);
  ctx.strokeStyle = "rgba(93, 184, 224, 0.65)";
  ctx.lineWidth = devicePixelRatio;
  ctx.strokeRect(x - boxWidth / 2, y - boxHeight / 2, boxWidth, boxHeight);
  ctx.fillStyle = "#ffffff";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(text, x, y + paddingY * 0.25);
  ctx.textAlign = "start";
  ctx.textBaseline = "alphabetic";
}

function drawGrid() {
  if (!$("showGrid")?.checked) return;
  const b = worldBounds();
  const step = 25;
  ctx.strokeStyle = "rgba(255,255,255,0.10)";
  ctx.lineWidth = devicePixelRatio;
  for (let x = Math.ceil(b.minX / step) * step; x <= b.maxX; x += step) {
    const a = toCanvas([x, b.minY]);
    const c = toCanvas([x, b.maxY]);
    ctx.beginPath();
    ctx.moveTo(a[0], a[1]);
    ctx.lineTo(c[0], c[1]);
    ctx.stroke();
  }
  for (let y = Math.ceil(b.minY / step) * step; y <= b.maxY; y += step) {
    const a = toCanvas([b.minX, y]);
    const c = toCanvas([b.maxX, y]);
    ctx.beginPath();
    ctx.moveTo(a[0], a[1]);
    ctx.lineTo(c[0], c[1]);
    ctx.stroke();
  }
}

// The stream and detection both use _correct_camera_frame; only project geometry here.
function liveNestingMapper() {
  if (state.workflowPreview !== "live" || !$("showLiveNesting").checked || state.activeView === "calibration" || state.editBoundary) return null;
  const scale = state.scale;
  if (!scale) return null;
  const width = cameraImage.naturalWidth || state.imageNatural.width;
  const height = cameraImage.naturalHeight || state.imageNatural.height;
  if ((scale.image_width && scale.image_width !== width) || (scale.image_height && scale.image_height !== height)) return null;
  if (scale.homography) {
    const inverse = invert3x3(scale.homography);
    if (!inverse || inverse.flat().some((value) => !Number.isFinite(value))) return null;
    return (point) => toImageCanvas(transformHomography(point, inverse));
  }
  if (Number.isFinite(scale.mm_per_pixel) && scale.mm_per_pixel > 0) {
    return ([x, y]) => toImageCanvas([x / scale.mm_per_pixel, y / scale.mm_per_pixel]);
  }
  return null;
}

function updateLiveNestingNotice() {
  const note = $("liveNestingInfo");
  note.hidden = state.workflowPreview !== "live" || !$("showLiveNesting").checked || !state.placements.length || state.activeView === "calibration" || state.editBoundary;
  note.textContent = "";
  if (!note.hidden) {
    note.hidden = !!liveNestingMapper();
    if (!note.hidden) note.textContent = "Live nesting overlay unavailable: check the saved scale and restore the capture resolution, or capture again.";
  }
}

function drawLiveNestingOverlay() {
  const mapper = liveNestingMapper();
  if (!mapper) return;
  state.placements.forEach((placement, index) => {
    if (!placement.polygon?.length) return;
    const projected = placement.polygon.map(mapper);
    if (projected.some((point) => point.some((value) => !Number.isFinite(value)))) return;
    drawPolygon(projected, placement.valid === false ? "#e05d5d" : "#5db8e0", "rgba(93,184,224,0.12)", 1.8, (point) => point);
    const center = Number.isFinite(placement.x_mm) && Number.isFinite(placement.y_mm)
      ? mapper([placement.x_mm, placement.y_mm])
      : projected.reduce((sum, point) => [sum[0] + point[0] / projected.length, sum[1] + point[1] / projected.length], [0, 0]);
    if (center.every(Number.isFinite)) drawPlacementLabel(String(index + 1), ...center);
  });
}

function draw() {
  updateDimensionEstimatedTime();
  if (state.activeView === "cutting-time") renderCuttingTime();
  ctx.clearRect(0, 0, overlay.width, overlay.height);
  const worldMode = isLayoutView();
  const photoLayout = canProjectLayoutOnPhoto();
  updateViewerMode();
  updateLiveNestingNotice();
  if (state.workflowPreview && state.workflowPreview !== "layout") {
    if (state.workflowPreview === "live") drawLiveNestingOverlay();
    if (["boundary", "corrected"].includes(state.workflowPreview)) drawAllMaterialDimensions(false);
    return;
  }
  if (photoLayout) {
    if ($("showBoundary")?.checked && state.sheathPixel) {
      drawSheathBoundaries(toImageCanvas, true);
    }
    state.placements.forEach((placement, index) => {
      drawPolygon(
        placement.polygon,
        index === state.selectedPlacement ? "#f4d35e" : placement.valid ? "#5db8e0" : "#e05d5d",
        "rgba(93,184,224,0.12)",
        1.8,
        toProjectedImageCanvas,
      );
      const [x, y] = toProjectedImageCanvas([placement.x_mm, placement.y_mm]);
      drawPlacementLabel(placement.template_name, x, y);
    });
  } else if (worldMode) {
    drawGrid();
    if ($("showBoundary")?.checked && state.sheathMm) {
      drawSheathBoundaries(toCanvas);
    }
  } else if ($("showBoundary")?.checked && state.sheathPixel) {
    drawSheathBoundaries(toImageCanvas, true);
  }
  if (worldMode && !photoLayout) {
    state.placements.forEach((placement, index) => {
      drawPolygon(placement.polygon, index === state.selectedPlacement ? "#f4d35e" : placement.valid ? "#5db8e0" : "#e05d5d", "rgba(93,184,224,0.08)", 1.5);
      const [x, y] = toCanvas([placement.x_mm, placement.y_mm]);
      drawPlacementLabel(placement.template_name, x, y);
    });
  }
  if (!worldMode && $("showPoints")?.checked && state.sheathPixel) {
    ctx.fillStyle = "#49c37b";
    state.sheathPixel.forEach((point) => {
      const [x, y] = toImageCanvas(point);
      ctx.beginPath();
      ctx.arc(x, y, 4 * devicePixelRatio, 0, Math.PI * 2);
      ctx.fill();
    });
  }
  ctx.fillStyle = "#d6a84d";
  state.scalePoints.forEach((point, i) => {
    const [x, y] = toImageCanvas(point);
    ctx.beginPath();
    ctx.arc(x, y, 5 * devicePixelRatio, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillText(String(i + 1), x + 8, y);
  });
  if (worldMode || photoLayout) drawTemplateBounds(state.placements, photoLayout ? toProjectedImageCanvas : toCanvas);
  drawAllMaterialDimensions(worldMode);
}

function drawMaterialDimensions(worldMode, m = state.measurements, prefix = "") {
  if (!$("showDimensions")?.checked || !m?.width_edge_mm || !m?.height_edge_mm || state.workflowPreview === "live") return;
  const mapper = worldMode && !canProjectLayoutOnPhoto() ? toCanvas : (point) => {
    if (state.scale?.homography) return toImageCanvas(mmToPixel(point));
    const factor = state.scale?.mm_per_pixel;
    return factor > 0 ? toImageCanvas([point[0] / factor, point[1] / factor]) : null;
  };
  for (const [edge, label] of [[m.width_edge_mm, `${prefix}W ${m.aligned_width_mm.toFixed(2)} mm`], [m.height_edge_mm, `${prefix}H ${m.aligned_height_mm.toFixed(2)} mm`]]) {
    const a = mapper(edge[0]), b = mapper(edge[1]);
    if (!a || !b) continue;
    const length = Math.hypot(b[0] - a[0], b[1] - a[1]);
    if (!length) continue;
    ctx.save();
    ctx.strokeStyle = "#f2d28b";
    ctx.lineWidth = 2 * devicePixelRatio;
    ctx.beginPath(); ctx.moveTo(...a); ctx.lineTo(...b); ctx.stroke();
    const ux = (b[0] - a[0]) / length, uy = (b[1] - a[1]) / length;
    const arrow = 7 * devicePixelRatio;
    for (const [point, sign] of [[a, 1], [b, -1]]) {
      ctx.beginPath(); ctx.moveTo(point[0] + sign * ux * arrow - uy * arrow / 2, point[1] + sign * uy * arrow + ux * arrow / 2);
      ctx.lineTo(...point); ctx.lineTo(point[0] + sign * ux * arrow + uy * arrow / 2, point[1] + sign * uy * arrow - ux * arrow / 2); ctx.stroke();
    }
    ctx.font = `${13 * devicePixelRatio}px system-ui`;
    const padding = 6 * devicePixelRatio, width = ctx.measureText(label).width + padding * 2, height = 24 * devicePixelRatio;
    const x = Math.max(width / 2, Math.min(overlay.width - width / 2, (a[0] + b[0]) / 2));
    const y = Math.max(height / 2, Math.min(overlay.height - height / 2, (a[1] + b[1]) / 2 - height / 2));
    ctx.fillStyle = "#292418"; ctx.fillRect(x - width / 2, y - height / 2, width, height);
    ctx.fillStyle = "#f2d28b"; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText(label, x, y);
    ctx.restore();
  }
}

async function loadCameras() {
  const data = await api("/api/cameras");
  const cameras = data.cameras.filter((cam) => !/iphone|continuity/i.test(cam.name));
  $("cameraSelect").innerHTML = cameras
    .map((cam) => {
      const blocked = /iphone|macbook|facetime|continuity/i.test(cam.name);
      const label = `${cam.name} - ${cam.width}x${cam.height}${blocked ? " (blocked)" : ""}`;
      return `<option value="${cam.index}" ${blocked ? "disabled" : ""}>${label}</option>`;
    })
    .join("");
  const preferred = cameras.find((cam) => /imx|waveshare|usb/i.test(cam.name) && !/iphone|macbook|facetime|continuity/i.test(cam.name));
  if (preferred) $("cameraSelect").value = String(preferred.index);
  if (!data.cameras.length) message("No USB camera found. Connect the USB camera and enable camera access in Windows Settings, then click Refresh.", true);
}

async function syncCameraStatus() {
  const status = await api("/api/camera/status");
  if (!status.connected) return false;
  $("cameraSelect").value = String(status.index);
  $("widthInput").value = String(status.width || $("widthInput").value);
  $("heightInput").value = String(status.height || $("heightInput").value);
  $("fpsInput").value = String(Math.round(status.fps || Number($("fpsInput").value)));
  startLivePreview();
  $("emptyView").style.display = "none";
  $("statusPill").textContent = /usb|imx|uvc|waveshare/i.test(status.name || "") ? "USB / IMX READY" : "READY";
  $("statusPill").classList.remove("warn");
  renderCameraControls(status.controls || []);
  return true;
}

async function initializeDefaultCamera() {
  await loadCameras();
  if (!await syncCameraStatus()) {
    if (hasPermission('operate')) await openMachineCamera();
    else $('emptyView').textContent = 'Camera is closed. Ask an Editor/Manager or Admin to open it.';
  }
}

async function openCamera() {
  if (!$("cameraSelect").options.length) throw new Error("Connect a camera and click Refresh, or upload an image.");
  const payload = {
    index: Number($("cameraSelect").value),
    width: Number($("widthInput").value),
    height: Number($("heightInput").value),
    fps: Number($("fpsInput").value),
  };
  const status = await api("/api/camera/open", { method: "POST", body: JSON.stringify(payload) });
  $("widthInput").value = String(status.width || payload.width);
  $("heightInput").value = String(status.height || payload.height);
  $("fpsInput").value = String(Math.round(status.fps || payload.fps));
  startLivePreview();
  $("emptyView").style.display = "none";
  $("statusPill").textContent = /usb|imx|uvc|waveshare/i.test(status.name || "") ? "USB / IMX READY" : "READY";
  $("statusPill").classList.remove("warn");
  renderCameraControls(status.controls || []);
}

async function openMachineCamera() {
  const status = await api("/api/camera/open-machine", { method: "POST", body: JSON.stringify({}) });
  $("cameraSelect").value = String(status.index);
  $("widthInput").value = String(status.width || 1920);
  $("heightInput").value = String(status.height || 1080);
  $("fpsInput").value = String(Math.round(status.fps || 30));
  startLivePreview();
  $("emptyView").style.display = "none";
  $("statusPill").textContent = "USB / IMX READY";
  $("statusPill").classList.remove("warn");
  renderCameraControls(status.controls || []);
  message(`Opened ${status.name || "USB / IMX camera"} at index ${status.index}`);
}

function renderCameraControls(controls) {
  $("cameraControls").innerHTML = controls
    .filter((control) => control.supported)
    .map((control) => `<label>${control.name}<input data-control="${control.name}" type="number" step="0.1" value="${control.value}"></label>`)
    .join("");
}

async function captureImage() {
  const data = await api("/api/capture", { method: "POST" });
  resetAnalysisState();
  setPreviewCrop(data.preview_crop);
  cameraImage.src = data.image;
  $("emptyView").style.display = "none";
  message("Image captured");
}

async function uploadImage(event) {
  const file = event.target.files[0];
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  const data = await api("/api/upload", { method: "POST", body: form });
  resetAnalysisState();
  setPreviewCrop(data.preview_crop);
  cameraImage.src = data.image;
  $("emptyView").style.display = "none";
  message("Image uploaded");
}

function resetAnalysisState() {
  state.workflowPreview = null;
  state.measurements = null;
  updateDimensionSummary();
  clearAlignmentPreview();
  state.sheaths = [];
  state.selectedSheathId = null;
  $("sheathMeasurements").hidden = true;
  state.sheathMm = null;
  state.sheathPixel = null;
  state.scale = null;
  state.placements = [];
  state.selectedPlacement = -1;
  $("pieces").textContent = "0";
  $("utilization").textContent = "0%";
  $("waste").textContent = "--";
  $("measurements").innerHTML = "";
  draw();
}

async function detectSheath() {
  const data = await api("/api/detect", { method: "POST" });
  state.sheathMm = data.mm_polygon;
  state.sheathPixel = data.pixel_polygon;
  state.scale = data.scale;
  updateViewerMode();
  renderMeasurements(data.measurements);
  if (data.sheaths) renderSheaths(data.sheaths);
  draw();
  message(data.scale_source === "aruco_workspace"
    ? `Sheath measured in the calibrated ${data.scale.working_width_mm.toFixed(0)} x ${data.scale.working_height_mm.toFixed(0)} mm working area. Check the displayed boundary before nesting.`
    : data.scale_source === "aruco"
    ? `Sheath detected and scaled using ArUco ID ${data.scale.marker_id}, ${data.scale.marker_size_mm} mm.`
    : data.scale_source === "saved_aruco"
      ? `Sheath measured using saved ${data.scale.marker_size_mm} mm ArUco scale. Keep the camera and working plane fixed.`
      : `Sheath boundary detected (${data.scale_source || "pixels"}). ArUco marker was not used; check the configured ID, size, and visibility.`);
}


function sheathLabel(id) {
  const index = state.sheaths.findIndex((piece) => piece.id === id);
  return index >= 0 ? `Sheath ${index + 1}` : id || "Sheath";
}

function renderSheaths(pieces) {
  state.sheaths = pieces || [];
  $("sheathMeasurements").hidden = state.sheaths.length < 2;
  const selector = $("sheathSelection"), list = $("sheathMeasurementList");
  selector.replaceChildren(); list.replaceChildren();
  state.sheaths.forEach((piece, index) => {
    const name = `Sheath ${index + 1}`, m = piece.measurements;
    selector.append(new Option(name, piece.id));
    const row = document.createElement("div"); row.className = "sheath-measurement-row";
    row.textContent = m ? `${name}: ${(m.aligned_width_mm ?? m.width_mm).toFixed(2)} x ${(m.aligned_height_mm ?? m.height_mm).toFixed(2)} mm / ${m.area_m2.toFixed(4)} m2${piece.placements ? ` / ${piece.placements.length} cuts` : ""}` : `${name}: dimensions unavailable`;
    if (piece.clipped_to_workspace) row.textContent += " / usable portion only";
    list.append(row);
  });
  if (!state.sheaths.some((piece) => piece.id === state.selectedSheathId)) state.selectedSheathId = state.sheaths[0]?.id || null;
  selector.value = state.selectedSheathId || "";
  selectSheath(state.selectedSheathId);
}

function selectSheath(id) {
  const piece = state.sheaths.find((item) => item.id === id);
  if (!piece) return;
  state.selectedSheathId = piece.id;
  state.sheathMm = piece.mm_polygon;
  if (piece.pixel_polygon) state.sheathPixel = piece.pixel_polygon;
  if (piece.measurements) renderMeasurements(piece.measurements);
  draw();
}

function drawSheathBoundaries(mapper, pixel = false) {
  const polygons = state.sheaths.length ? state.sheaths.map((piece) => pixel ? piece.pixel_polygon : piece.mm_polygon)
    : [pixel ? state.sheathPixel : state.sheathMm];
  polygons.forEach((polygon) => { if (polygon?.length) drawPolygon(polygon, "#49c37b", "rgba(73,195,123,0.08)", 2, mapper); });
}

function drawAllMaterialDimensions(worldMode) {
  if (state.sheaths.length > 1) state.sheaths.forEach((piece, index) => drawMaterialDimensions(worldMode, piece.measurements, `S${index + 1} `));
  else drawMaterialDimensions(worldMode);
}

function renderMeasurements(m) {
  state.measurements = m;
  const aligned = Number.isFinite(m.aligned_width_mm) && Number.isFinite(m.aligned_height_mm);
  $("measurements").innerHTML = `
    <div>${aligned ? "Edge-aligned width" : "Width (X span)"}: ${(aligned ? m.aligned_width_mm : m.width_mm).toFixed(2)} mm</div>
    <div>${aligned ? "Edge-aligned height" : "Height (Y span)"}: ${(aligned ? m.aligned_height_mm : m.height_mm).toFixed(2)} mm</div>
    <div>Area: ${m.area_m2.toFixed(4)} m\u00b2</div>`;
  updateDimensionSummary();
  if (state.activeView === "data") renderDataSummary();
}

function updateDimensionSummary() {
  const m = state.measurements;
  const aligned = m && Number.isFinite(m.aligned_width_mm) && Number.isFinite(m.aligned_height_mm);
  $("dimensionContext").textContent = !m ? "No piece measured yet" : state.workflowPreview === "live" ? "Last captured piece" : "Captured piece dimensions";
  if (m && state.sheaths.length > 1) $("dimensionContext").textContent = `${sheathLabel(state.selectedSheathId)} - ${state.workflowPreview === "live" ? "last captured dimensions" : "captured dimensions"}`;
  const clipped = state.sheaths.map((piece,index) => piece.clipped_to_workspace ? index + 1 : null).filter(Boolean);
  ["materialScopeWarning", "measurementScopeWarning"].forEach(id => {
    const warning = $(id);
    warning.hidden = !clipped.length;
    warning.textContent = clipped.length ? `${clipped.length > 1 ? "Sheaths" : "Sheath"} ${clipped.join(", ")} ${clipped.length > 1 ? "extend" : "extends"} outside the calibrated cutting area. ${clipped.length > 1 ? "Their" : "Its"} dimensions and area cover the usable portion only. Nesting stays inside the calibrated area.` : "";
  });
  if (m && state.sheaths.find(piece => piece.id === state.selectedSheathId)?.clipped_to_workspace) {
    $("dimensionContext").textContent += " - usable portion only";
  }
  $("dimensionWidthLabel").textContent = !m || aligned ? "Edge-aligned width" : "Width (X span)";
  $("dimensionHeightLabel").textContent = !m || aligned ? "Edge-aligned height" : "Height (Y span)";
  $("dimensionWidth").textContent = m ? `${(aligned ? m.aligned_width_mm : m.width_mm).toFixed(2)} mm` : "--";
  $("dimensionHeight").textContent = m ? `${(aligned ? m.aligned_height_mm : m.height_mm).toFixed(2)} mm` : "--";
  $("dimensionArea").textContent = m ? `${m.area_m2.toFixed(4)} m\u00b2` : "--";
  updateDimensionEstimatedTime();
  const detailText = !m ? "Capture and detect a piece to measure it. Live video alone does not update dimensions."
    : aligned ? `Dimensions follow the fitted enclosing rectangle. X/Y spans: ${m.width_mm.toFixed(2)} / ${m.height_mm.toFixed(2)} mm. Camera estimates use the existing calibration. Extra decimal places do not establish physical accuracy.`
    : "Horizontal and vertical spans from the last detection. Camera estimates use the existing calibration.";
  $("dimensionDetail").textContent=roleCopy(detailText,m ? `X span: ${m.width_mm.toFixed(2)} mm; Y span: ${m.height_mm.toFixed(2)} mm.` : "");
}

async function refreshCamera({required = false} = {}) {
  if (state.workflowBusy || state.pendingMutations || state.cameraRefreshing) {
    if (required) throw new Error('The camera or display is still busy. Wait, then press Space again.');
    return false;
  }
  state.cameraRefreshing = true;
  $("cycleRefreshCamera").disabled = true;
  $("cyclePreviewLabel").textContent = "Reconnecting USB camera...";
  try {
    cameraImage.src = ""; // Release the old MJPEG request before reopening its device.
    await loadCameras();
    const current = await api("/api/camera/status");
    if (current.connected) {
      if (Array.from($("cameraSelect").options || []).some((option) => option.value === String(current.index))) $("cameraSelect").value = String(current.index);
      $("widthInput").value = String(current.width);
      $("heightInput").value = String(current.height);
      $("fpsInput").value = String(Math.round(current.fps));
    }
    await openCamera(); // Force a real reconnect; the default-opening endpoint intentionally reuses a handle.
    showWorkflowPreview("live", false);
    message("USB camera refreshed. Live preview is running.");
    return true;
  } catch (error) {
    $("cyclePreviewLabel").textContent = `Camera refresh failed: ${displayError(error)}`;
    throw error;
  } finally {
    state.cameraRefreshing = false;
    $("cycleRefreshCamera").disabled = state.workflowBusy;
  }
}

async function loadTemplates() {
  const data = await api("/api/templates");
  state.templates = data.templates;
  renderTemplates();
}

function preferenceTemplate(values, previous = {}) {
  const factor = values.unit === "in" ? 25.4 : 1;
  const width = Number(values.width) * factor;
  const height = values.shape === "rectangle" ? Number(values.height) * factor : width;
  const quantity = Number(values.quantity);
  if (!["rectangle", "square", "circle"].includes(values.shape) || !Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
    throw new Error("Enter a positive width and height for your preferred size.");
  }
  if (!Number.isInteger(quantity) || quantity < 1) throw new Error("Quantity must be a whole number of at least 1.");
  let rotations = values.rotate && values.shape !== "circle" ? [0, 90] : [0];
  if (values.shape === "rectangle" && values.orientation) {
    const choices = { both: [0, 90], horizontal: [0], vertical: [90], custom: previous.allowed_rotations };
    rotations = choices[values.orientation];
    if (!rotations?.length || !rotations.every(Number.isFinite)) throw new Error("Select a valid rectangle cutting direction.");
  }
  return {
    ...previous,
    id: values.id,
    name: values.name.trim() || `${Number(values.width)}${values.shape === "rectangle" ? ` x ${Number(values.height)}` : ""} ${values.unit} ${values.shape}`,
    width_mm: width, height_mm: height, shape: values.shape, quantity,
    min_spacing_mm: previous.min_spacing_mm ?? 2,
    allowed_rotations: [...rotations],
    enabled: previous.enabled ?? true, polygon: null,
  };
}

function showTemplateTab(tab) {
  const preferences = tab === "preferences";
  $("templateLibrary").hidden = preferences;
  $("templatePreferences").hidden = !preferences;
  document.querySelectorAll("[data-template-tab]").forEach((button) => {
    const active = button.dataset.templateTab === tab;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
}

function updatePreferenceFields() {
  const shape = $("preferredShape").value;
  const units = $("preferredUnits").value === "in" ? "inches" : "mm";
  $("preferredWidthLabel").textContent = `${shape === "circle" ? "Diameter" : shape === "square" ? "Side length" : "Width"} (${units})`;
  $("preferredHeightLabel").textContent = `Height (${units})`;
  $("preferredHeight").disabled = shape !== "rectangle";
  $("preferredHeightField").hidden = shape !== "rectangle";
  if (shape !== "rectangle") $("preferredHeight").value = $("preferredWidth").value;
  $("preferredOrientationField").hidden = shape !== "rectangle";
  $("preferredOrientation").disabled = shape !== "rectangle";
  $("preferredRotateField").hidden = shape === "rectangle";
  $("preferredRotate").disabled = shape === "circle" || shape === "rectangle";
}

function editPreferredTemplate(id, reveal = true) {
  const template = state.templates.find((item) => item.id === id);
  $("preferredTemplate").value = template ? id : "";
  $("preferredUnits").value = "mm";
  $("preferredUnits").dataset.previousUnit = "mm";
  $("preferredName").value = template?.name || "";
  $("preferredShape").value = template?.shape || "rectangle";
  $("preferredWidth").value = template ? Number(template.width_mm.toFixed(6)) : "";
  $("preferredHeight").value = template ? Number(template.height_mm.toFixed(6)) : "";
  $("preferredQuantity").value = Math.max(1, template?.quantity ?? 1);
  const rotations = template?.allowed_rotations || [0, 90];
  $("preferredRotate").checked = rotations.includes(90);
  const orientation = rotations.length === 1 && rotations[0] === 0 ? "horizontal"
    : rotations.length === 1 && rotations[0] === 90 ? "vertical"
    : rotations.length === 2 && rotations.includes(0) && rotations.includes(90) ? "both" : "custom";
  $("preferredOrientation").value = orientation;
  $("preferredCustomOrientation").hidden = orientation !== "custom";
  $("savePreference").textContent = template ? "Save template changes" : "Save new template";
  $("templateEditorTitle").textContent = template ? `Editing: ${template.name}` : "New template";
  $("templateEditorHeading").textContent = template ? "Edit template details" : "Add a new template";
  updatePreferenceFields();
  if (reveal) {
    showTemplateTab("library");
    $("templateEditor").open = true;
    $("templateEditor").scrollIntoView?.({ behavior: "smooth", block: "nearest" });
  }
}

function moveTemplatePriority(templates, index, direction) {
  const reordered = [...templates];
  const target = index + direction;
  if (target < 0 || target >= reordered.length) return templates;
  [reordered[index], reordered[target]] = [reordered[target], reordered[index]];
  return reordered.map((template, i) => ({ ...template, priority: i + 1 }));
}

function renderTemplatePriority() {
  $("templatePriorityList").replaceChildren();
  state.templates.forEach((template, index) => {
    const row = document.createElement("div");
    row.className = "priority-row";
    const name = document.createElement("span");
    name.textContent = `${index + 1}. ${template.name}${template.enabled ? "" : " (disabled)"}`;
    row.append(name);
    for (const direction of [-1, 1]) {
      const button = document.createElement("button");
      button.textContent = direction === -1 ? "Up" : "Down";
      button.setAttribute("aria-label", `Move ${template.name} ${direction === -1 ? "up" : "down"}`);
      button.disabled = index + direction < 0 || index + direction >= state.templates.length;
      button.onclick = () => {
        state.templates = moveTemplatePriority(state.templates, index, direction);
        renderTemplates();
      };
      row.append(button);
    }
    $("templatePriorityList").append(row);
  });
}

function renderTemplates() {
  state.templates.sort((a, b) => (a.priority ?? Number.MAX_SAFE_INTEGER) - (b.priority ?? Number.MAX_SAFE_INTEGER));
  renderTemplatePriority();
  $("templates").replaceChildren();
  const selected = $("preferredTemplate").value;
  $("preferredTemplate").replaceChildren(new Option("Add new size", ""));
  state.templates.forEach((template, i) => {
    const row = document.createElement("div");
    row.className = "template template-card";
    row.dataset.templateId = template.id;
    const heading = document.createElement("div");
    heading.className = "template-card-heading";
    const description = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = template.name;
    const size = document.createElement("span");
    size.className = "template-dimensions";
    size.textContent = templateDescription(template);
    description.append(name, size);
    const badge = document.createElement("span");
    badge.className = "template-badge";
    badge.textContent = state.selectedTemplateId === template.id ? "In use" : template.enabled ? "Enabled" : "Excluded";
    heading.append(description, badge);
    const settings = document.createElement("div");
    settings.className = "template-card-settings";
    const enabledLabel = document.createElement("label");
    enabledLabel.className = "checkbox-label";
    const enabled = document.createElement("input");
    enabled.type = "checkbox";
    enabled.checked = template.enabled;
    enabled.dataset.templateId = template.id;
    enabled.dataset.field = "enabled";
    enabled.setAttribute("aria-label", `Enable ${template.name}`);
    const enabledText = document.createElement("span");
    enabledText.textContent = "Enabled for nesting";
    enabledLabel.append(enabled, enabledText);
    const quantityLabel = document.createElement("label");
    quantityLabel.textContent = "Quantity";
    const quantity = document.createElement("input");
    quantity.type = "number";
    quantity.min = "0";
    quantity.step = "1";
    quantity.value = template.quantity;
    quantity.dataset.templateId = template.id;
    quantity.dataset.field = "quantity";
    quantity.setAttribute("aria-label", `Quantity for ${template.name}`);
    quantityLabel.append(quantity);
    settings.append(enabledLabel, quantityLabel);
    const actions = document.createElement("div");
    actions.className = "template-card-actions";
    const use = document.createElement("button");
    use.textContent = state.selectedTemplateId === template.id ? "In use" : "Use this template";
    use.disabled = state.workflowBusy || state.selectedTemplateId === template.id;
    use.onclick = () => {
      if (state.workflowBusy) return;
      $("librarySelection").value = template.id;
      loadSelectedTemplate();
    };
    const edit = document.createElement("button");
    edit.textContent = "Edit";
    edit.setAttribute("aria-label", `Edit ${template.name}`);
    edit.onclick = () => { if (!state.workflowBusy) editPreferredTemplate(template.id); };
    actions.append(use, edit);
    row.classList.toggle("is-active", state.selectedTemplateId === template.id);
    row.append(heading, settings, actions);
    $("preferredTemplate").append(new Option(template.name, template.id));
    $("templates").append(row);
  });
  if (state.templates.some((template) => template.id === selected)) $("preferredTemplate").value = selected;
  const librarySelection = $("librarySelection").value;
  $("librarySelection").replaceChildren(new Option("All enabled templates", ""));
  state.templates.forEach((template) => $("librarySelection").append(new Option(`${template.name} - ${templateDescription(template)}`, template.id)));
  if (state.templates.some((item) => item.id === librarySelection)) $("librarySelection").value = librarySelection;
  if (state.selectedTemplateId && !state.templates.some((item) => item.id === state.selectedTemplateId)) state.selectedTemplateId = null;
  $("templateCount").textContent = `${state.templates.length} saved / ${state.templates.filter((item) => item.enabled).length} enabled`;
  $("templateEmpty").hidden = state.templates.length > 0;
  updateActiveTemplate();
}

function templateDescription(template) {
  const size = template.shape === "circle" ? `Diameter ${template.width_mm.toFixed(1)} mm`
    : `${template.width_mm.toFixed(1)} x ${template.height_mm.toFixed(1)} mm`;
  const direction = template.shape === "rectangle" && template.allowed_rotations?.length === 1
    ? template.allowed_rotations[0] === 90 ? " / Vertical only" : template.allowed_rotations[0] === 0 ? " / Horizontal only" : "" : "";
  return `${template.shape.charAt(0).toUpperCase() + template.shape.slice(1)} / ${size}${direction}`;
}

function workflowTemplates() {
  return state.selectedTemplateId ? state.templates.filter((item) => item.id === state.selectedTemplateId) : state.templates;
}

function nestingPayload() {
  return {
    templates: workflowTemplates(),
    spacing_mm: $("strategy").value === "common_line" ? 0 : Number($("spacing").value),
    edge_clearance_mm: Number($("edgeClearance").value),
    rotation_step_deg: Number($("rotationStep").value),
    strategy: $("strategy").value,
    precision_mm: Number($("nestingPrecision").value),
  };
}

async function autoNest() {
  clearAlignmentPreview();
  const payload = nestingPayload();
  message("Nesting...");
  const result = await api("/api/nest", { method: "POST", body: JSON.stringify(payload) });
  if (result.sheath_polygon) state.sheathMm = result.sheath_polygon;
  if (result.scale) state.scale = result.scale;
  state.placements = result.placements;
  if (result.placements.length) {
    const snapshot = await api("/api/state").catch((error) => {
      message(`Nesting finished; photo preview unavailable: ${displayError(error)}`, true);
      return {};
    });
    state.workflowArtifacts = { nesting: result, corrected_image: snapshot.image, preview_crop: snapshot.preview_crop };
    if (snapshot.pixel_polygon) state.sheathPixel = snapshot.pixel_polygon;
    if (snapshot.measurements) renderMeasurements(snapshot.measurements);
    if (result.sheaths) renderSheaths(result.sheaths);
    else if (snapshot.sheaths) renderSheaths(snapshot.sheaths);
    renderNestingPreview(result);
    showWorkflowPreview("layout");
  }
  updateViewerMode();
  $("pieces").textContent = String(result.placements.length);
  $("utilization").textContent = `${(result.utilization * 100).toFixed(1)}%`;
  $("waste").textContent = `${(result.waste * 100).toFixed(1)}%`;
  draw();
  message(result.placements.length ? `Nesting complete: ${result.placements.length} of ${result.requested_pieces ?? result.placements.length} requested pieces placed.` : "No valid template placement found", !result.placements.length);
  if (result.placements.length && result.requested_pieces === result.placements.length) {
    message("All requested quantities are placed. To fill remaining space, increase quantities or enable smaller template sizes.");
  }
}


function renderExportLinks(output) {
  $("exportResult").replaceChildren();
  const vector = document.createElement("a");
  vector.href = output.url; vector.target = "_blank"; vector.textContent = output.path;
  $("exportResult").append(vector);
  if (output.csv) {
    const separator = document.createElement("span"); separator.textContent = " / ";
    const csv = document.createElement("a");
    csv.href = output.csv.url; csv.setAttribute("download", ""); csv.textContent = "Download CSV data";
    $("exportResult").append(separator, csv);
  }
  renderLaserCadHandoff(output);
  if (state.activeView === "data") {
    renderDataSummary();
    if (output.daily_production) void refreshDataProjects();
  }
}

function renderLaserCadHandoff(output) {
  const handoff = output?.lasercad;
  ["laserCadHandoffStatus", "laserCadExportStatus"].forEach(id => {
    const node = $(id);
    node.hidden = !handoff;
    const frame = handoff?.boundary_frame;
    const preparation = handoff?.download_automation;
    const handoffText=[handoff?.message, frame?.message, preparation?.message].filter(Boolean).join(" ");
    node.textContent=state.user?.role==='viewer' && (handoff?.status==='warning' || ['blocked','transfer_failed','transfer_unconfirmed'].includes(preparation?.status)) ? displayError(handoffText,'lasercad') : handoffText;
    node.dataset.state = ["blocked","transfer_failed","transfer_unconfirmed"].includes(preparation?.status) || (frame && frame.status !== "requested") ? "warning" : preparation?.status === "transfer_completed" ? "completed" : (handoff?.status || "");
  });
}

const laserControlOpenedExports = new Set();
function openLaserControlForExport(output) {
  if (!state.user) return;
  const jobId = output?.laser_control?.job_id;
  if (!jobId || !/\.dxf$/i.test(output.path || '')) return;
  const key = output.path + ':' + jobId;
  if (laserControlOpenedExports.has(key)) return;
  laserControlOpenedExports.add(key);
  if (laserControlOpenedExports.size > 128) laserControlOpenedExports.delete(laserControlOpenedExports.values().next().value);
  window.LaserControl?.receiveExport?.();
  navigateSection('lasercad');
}

function watchLaserControlExport(requestId) {
  let stopped = false, timer;
  const poll = async () => {
    if (stopped) return;
    try {
      const data = await api('/api/laser-control/status', {cache:'no-store'});
      if (!stopped && data.job?.ui_request_id === requestId) {
        openLaserControlForExport({path:data.job.source_path,laser_control:{job_id:data.job.id}});
        stopped = true;
      }
    } catch (error) { if (error.status === 401) stopped = true; }
    if (!stopped) timer = setTimeout(poll,250);
  };
  timer = setTimeout(poll,250);
  return () => { stopped = true; if (typeof clearTimeout === 'function') clearTimeout(timer); };
}

async function exportLayout(format) {
  const requestId = format === 'dxf' ? crypto.randomUUID() : null;
  const stopWatching = requestId ? watchLaserControlExport(requestId) : () => {};
  try {
    const data = await api("/api/export", { method: "POST", body: JSON.stringify({ format, include_sheath: false, ...(format === "dxf" ? { alignment: readAlignment(), ui_request_id: requestId } : {}) }) });
    renderExportLinks(data);
    openLaserControlForExport(data);
    message(`${format.toUpperCase()} exported${data.csv ? " and CSV data saved" : ""}`);
  } finally { stopWatching(); }
}

const alignmentFields = {
  enabled: "alignmentEnabled", reference: "alignmentReference",
  design_x_mm: "alignmentDesignX", design_y_mm: "alignmentDesignY",
  laser_x_mm: "alignmentLaserX", laser_y_mm: "alignmentLaserY",
  rotation_deg: "alignmentRotation", flip_x: "alignmentFlipX", flip_y: "alignmentFlipY",
};

function usesId12Frame() { return state.config.workspace?.coordinate_frame === 'id12_negative_xy'; }
function worldYUp() { return state.scale ? state.scale.coordinate_frame === 'id12_negative_xy' : usesId12Frame(); }
function sharedAlignment() { return {enabled:true,reference:'templates_top_right',design_x_mm:0,design_y_mm:0,laser_x_mm:0,laser_y_mm:0,rotation_deg:0,flip_x:false,flip_y:false}; }
function applySharedOriginUI() {
  $('nestingOrigin').hidden = !usesId12Frame();
  $('nestingExportOrigin').hidden = !usesId12Frame();
  $('laserCadOriginIndicator').hidden = !usesId12Frame();
  if (document.body) document.body.dataset.coordinateFrame = usesId12Frame() ? 'id12_negative_xy' : 'legacy';
  if (!usesId12Frame()) return;
  $('sharedOriginExplanation').hidden=false;
  $('alignmentTransformFields').disabled=true;
  $('saveAlignment').disabled=true;
  for (const [key,id] of Object.entries(alignmentFields)) {
    const input=$(id);input.disabled=true;
    if (input.type === 'checkbox') input.checked=sharedAlignment()[key]; else input.value=String(sharedAlignment()[key]);
  }
  const select=$('alignmentReference');
  let option=select.querySelector?.('option[value="templates_top_right"]');
  if (!option) { option=document.createElement('option');option.value='templates_top_right';option.textContent='Nested templates: bounding-box top-right corner (automatic)';select.append(option); }
  select.value='templates_top_right';
}

function readAlignment() {
  if (usesId12Frame()) return sharedAlignment();
  return Object.fromEntries(Object.entries(alignmentFields).map(([key, id]) => {
    const input = $(id);
    if (input.type === "checkbox") return [key, input.checked];
    if (input.type === "number") {
      if (!input.value.trim() || !Number.isFinite(Number(input.value))) throw new Error("Enter finite coordinates and rotation for alignment.");
      return [key, Number(input.value)];
    }
    return [key, input.value];
  }));
}

function clearAlignmentPreview() {
  $("alignmentCanvas").hidden = true;
  $("alignmentSummary").textContent = "Preview again to see the current DXF coordinates.";
  $("alignmentCustom").hidden = $("alignmentReference").value !== "custom";
}

async function loadAlignment() {
  const alignment = await api("/api/laser/alignment", { cache: "no-store" });
  state.savedAlignment = alignment;
  for (const [key, id] of Object.entries(alignmentFields)) {
    if ($(id).type === "checkbox") $(id).checked = alignment[key];
    else $(id).value = alignment[key];
  }
  clearAlignmentPreview();
  renderSavedWorkspace();
}

async function previewAlignment() {
  const data = await api("/api/laser/alignment/preview", { method: "POST", body: JSON.stringify(readAlignment()) });
  const canvas = $("alignmentCanvas"), context = canvas.getContext("2d");
  const b = data.bounds_mm, target = data.laser_reference_mm;
  const minX = Math.min(0, b.min_x, target[0]), maxX = Math.max(0, b.max_x, target[0]);
  const minY = Math.min(0, b.min_y, target[1]), maxY = Math.max(0, b.max_y, target[1]);
  const scale = Math.min((canvas.width - 60) / Math.max(1, maxX - minX), (canvas.height - 60) / Math.max(1, maxY - minY));
  const map = ([x, y]) => [30 + (x - minX) * scale, canvas.height - 30 - (y - minY) * scale];
  context.clearRect(0, 0, canvas.width, canvas.height);
  function line(points, color, closed = false) {
    context.beginPath();
    points.map(map).forEach(([x, y], i) => i ? context.lineTo(x, y) : context.moveTo(x, y));
    if (closed) context.closePath();
    context.strokeStyle = color;
    context.stroke();
  }
  line([[minX, 0], [maxX, 0]], "#68777b");
  line([[0, minY], [0, maxY]], "#68777b");
  (data.sheaths || [data.sheath]).forEach((polygon) => line(polygon, "#49c37b", true));
  data.placements.forEach((p) => line(p.polygon, "#5db8e0", true));
  drawTemplateBounds(data.placements,map,context,true,false);
  context.font = "11px system-ui";
  for (const [point, label, color] of [[[0, 0], "Laser (0,0)", "#fff"], [target, "Reference", "#f4d35e"]]) {
    const [x, y] = map(point);
    context.fillStyle = color;
    context.fillRect(x - 3, y - 3, 6, 6);
    context.fillText(label, Math.min(x + 6, canvas.width - 85), Math.max(12, y - 8));
  }
  context.fillStyle = "#fff";
  context.fillText("+X ->   +Y Up", 8, canvas.height - 8);
  canvas.hidden = false;
  const fmt = (n) => n.toFixed(2);
  $("alignmentSummary").textContent = `${data.alignment.enabled ? "Aligned DXF" : "Original DXF"}: design (${data.design_reference_mm.map(fmt).join(", ")}) -> laser (${target.map(fmt).join(", ")}) mm. Bounds X ${fmt(b.min_x)}...${fmt(b.max_x)}, Y ${fmt(b.min_y)}...${fmt(b.max_y)} mm.`;
}

let ordinarySpacing = null;
function updateSharedCutControls() {
  const shared = $("strategy").value === "common_line";
  if (shared) {
    if (ordinarySpacing === null) ordinarySpacing = $("spacing").value;
    $("spacing").value = "0";
  } else if (ordinarySpacing !== null) {
    $("spacing").value = ordinarySpacing; ordinarySpacing = null;
  }
  $("spacing").disabled = shared || state.workflowBusy;
  $("sharedCutHint").hidden = !shared;
}


// Settings use a draft so unknown configuration keys survive ordinary edits.
const settingsGroups = [
  { title: "Camera", help: "Capture size and frame rate. Reconnect the camera after changing these settings.", fields: [
    ["camera.device", "Camera device", "text", "auto", "Use auto for automatic selection, or a camera index such as 0."],
    ["camera.resolution.width", "Image width (px)", "number", 1280, "", 1, null, 1],
    ["camera.resolution.height", "Image height (px)", "number", 720, "", 1, null, 1],
    ["camera.fps", "Frame rate (fps)", "number", 30, "", 1, null, 1]
  ]},
  { title: "Laser bed and capture automation", help: "Machine travel limits are independent of marker spacing. Measure marker positions in Calibration and set the laser origin under Export → DXF → laser coordinates.", fields: [
    ["workspace.width_mm", "Usable machine travel X (mm)", "number", 915, "Reachable travel, independent of physical bed size and marker span.", 0.1, null, "any"],
    ["workspace.height_mm", "Usable machine travel Y (mm)", "number", 630, "", 0.1, null, "any"],
    ["automation.refresh_workspace_calibration", "Read all four markers on each capture", "checkbox", true, "Uses the surveyed marker positions. A missing marker stops this cycle before DXF export."]
  ]},
  { title: "Marker detection", help: "Single-marker scale detection. This marker ID does not select the DXF origin. Four-marker calibration takes priority when saved.", fields: [
    ["calibration.aruco.enabled", "Enable marker scale detection", "checkbox", true],
    ["calibration.aruco.dictionary", "Marker dictionary", "text", "DICT_4X4_50", "Must match the printed markers."],
    ["calibration.aruco.marker_id", "Detection marker ID", "number", 10, "", 0, null, 1],
    ["calibration.aruco.marker_size_mm", "Black square size (mm)", "number", 95, "Measure the black square, excluding the white margin.", 0.1, null, "any"]
  ]},
  { title: "Nesting defaults", help: "Defaults for arranging templates inside the sheath. Shared cut uses zero spacing between templates.", fields: [
    ["nesting.spacing_mm", "Gap between templates (mm)", "number", 2, "", 0, null, "any"],
    ["nesting.edge_clearance_mm", "Distance from sheath edge (mm)", "number", 3, "", 0, null, "any"],
    ["nesting.rotation_step_deg", "Rotation step (°)", "number", 5, "Smaller steps explore more angles and can take longer.", 1, 90, "any"],
    ["nesting.precision_mm", "Placement refinement (mm)", "number", 0.1, "Smaller values search more precisely and can take longer.", 0.1, 5, "any"],
    ["nesting.strategy", "Layout method", "select", "accurate", "", [["accurate", "Dense layout"], ["common_line", "Shared cut"], ["shared_side", "Spaced side packing"], ["maximum_pieces", "Maximum pieces"], ["best_fit", "Best fit"], ["first_fit", "First fit"], ["maximum_utilization", "Maximum utilization"]]]
  ]},
  { title: "Verified machine cycle", help: "Requires a commissioned AWC7813 status/command adapter. The supplied operating manual does not provide that interface; automatic movement and Start are blocked until it is available. Existing workflow stays available while this is disabled.", fields: [
    ["machine_workflow.enabled", "Datum, park, job Origin and automatic Start", "checkbox", false, "Blocks capture/download when verified controller feedback is missing. Does not enable blind UI clicks."],
    ["machine_workflow.park_x_mm", "Capture park machine X (mm)", "number", 0, "Verify this position clears the complete camera view.", 0, 915, "any"],
    ["machine_workflow.park_y_mm", "Capture park machine Y (mm)", "number", 0, "Machine coordinates: X left, Y down from top-right.", 0, 630, "any"]
  ]},
  { title: "Export & LaserCAD", help: "DXF (0,0) is local job origin. LaserCAD preserves photographed placement after the measured marker-to-machine survey is saved in Calibration.", fields: [
    ["export.output_folder", "Export folder", "text", "data/exports"],
    ["lasercad.auto_open_dxf", "Open DXF in LaserCAD after export", "checkbox", true],
    ["lasercad.prepare_and_download.enabled", "Set cutting parameters and download after DXF export", "checkbox", false, "Selects the configured USB port, checks mm/s without opening System settings, opens the red layer to apply your saved Cut speed and maximum power, then triggers Download and Download Document once. The sequence ends at submission and reports Download requested. Transfer completion is not checked. Run Box and Start remain manual."],
    ["lasercad.prepare_and_download.speed_mm_s", "Cutting speed (mm/s)", "number", 90, "Saved value is applied to the red LaserCAD layer on the next new DXF export.", 0.01, null, "0.01"],
    ["lasercad.prepare_and_download.power_percent", "Maximum power (%)", "number", 10, "Primary laser channel. An existing minimum above this maximum is reduced to match; lower minimum power is preserved.", 0, 100, "0.01"],
    ["lasercad.prepare_and_download.usb_port", "LaserCAD USB communication entry", "text", "MachName:_AWC_D2713611", "Selects and verifies this exact named entry before Download. Missing or duplicate entries stop automation; no fallback to COM4. Explicit COM ports remain supported when configured."],
    ["lasercad.auto_frame_after_dxf", "Run Box after DXF opens (without download automation)", "checkbox", false, "Deployment option: this moves the laser head using LaserCAD's Run Box settings. Enable on the machine computer after setting its origin. Cutting still requires a separate Start action."],
    ["lasercad.executable", "LaserCAD application path (optional)", "text", "", "Leave blank to use automatic detection."]
  ]}
];
let settingsDraft = null;
let settingsBusy = false;
let settingsJsonDirty = false;

function settingsValue(config, path) {
  return path.split(".").reduce((value, key) => value?.[key], config);
}

function setSettingsValue(config, path, value) {
  const keys = path.split(".");
  let parent = config;
  for (const key of keys.slice(0, -1)) {
    if (!parent[key] || typeof parent[key] !== "object" || Array.isArray(parent[key])) parent[key] = {};
    parent = parent[key];
  }
  parent[keys.at(-1)] = value;
}

function settingsFeedback(text, error = false) {
  if(error)text=displayError(text);
  $("settingsStatus").textContent = text;
  $("settingsStatus").dataset.error = String(error);
}

function renderSettings(config) {
  settingsDraft = JSON.parse(JSON.stringify(config));
  const container = $("settingsSections");
  container.replaceChildren();
  for (const group of settingsGroups) {
    const card = document.createElement("section");
    card.className = "settings-card";
    if (group.title === "Workspace defaults") card.dataset.workspaceDefaults = "true";
    const heading = document.createElement("h3"); heading.textContent = group.title;
    const help = document.createElement("p"); help.className = "hint role-help"; help.textContent = group.help;
    card.append(heading, help);
    const fields = document.createElement("div"); fields.className = "settings-fields";
    for (const [path, title, type, fallback, hint, min, max, step] of group.fields) {
      const label = document.createElement("label");
      label.className = type === "checkbox" ? "settings-toggle" : "settings-field";
      const caption = document.createElement("span"); caption.textContent = title;
      const input = document.createElement(type === "select" ? "select" : "input");
      input.id = "setting-" + path.replaceAll(".", "-");
      input.dataset.configPath = path;
      label.htmlFor = input.id;
      if (type !== "select") input.type = type;
      const value = settingsValue(config, path) ?? fallback;
      if (type === "select") {
        for (const [key, name] of min) input.append(new Option(name, key));
        if (!min.some(([key]) => key === value)) input.append(new Option(String(value), String(value)));
      }
      if (type === "checkbox") input.checked = Boolean(value);
      else input.value = String(value);
      if (type === "number") {
        if (min != null) input.min = min;
        if (max != null) input.max = max;
        input.step = step || "any";
      }
      if (type !== "checkbox" && path !== "lasercad.executable") input.required = true;
      input.disabled = !hasPermission("configure") || settingsBusy;
      if (type === "checkbox") label.append(input, caption);
      else label.append(caption, input);
      if (hint) {
        const detail = document.createElement("small"); detail.className="role-help"; detail.textContent = hint;
        detail.id = input.id + "-hint"; input.setAttribute("aria-describedby", detail.id);
        label.append(detail);
      }
      fields.append(label);
    }
    card.append(fields); container.append(card);
  }
  $("configEditor").value = JSON.stringify(config, null, 2);
  settingsJsonDirty = false;
  $("saveConfig").disabled = !hasPermission("configure") || settingsBusy;
  renderSavedWorkspace();
}

function readSettingsForm() {
  if (!settingsDraft) throw new Error("Settings have not loaded. Reopen the app and try again.");
  const config = JSON.parse(JSON.stringify(settingsDraft));
  for (const input of document.querySelectorAll("#settingsSections [data-config-path]")) {
    if (!input.checkValidity()) { input.reportValidity(); throw new Error("Check the highlighted setting."); }
    let value = input.type === "checkbox" ? input.checked : input.type === "number" ? Number(input.value) : input.value.trim();
    if (input.dataset.configPath === "camera.device" && /^\d+$/.test(value)) value = Number(value);
    setSettingsValue(config, input.dataset.configPath, value);
  }
  return config;
}

async function saveSettings(event) {
  event.preventDefault();
  if (settingsBusy || !hasPermission("configure")) return;
  try {
    if (settingsJsonDirty) throw new Error("Apply your JSON changes to the form before saving.");
    const config = readSettingsForm();
    settingsBusy = true;
    $("settingsForm").querySelectorAll("input, select, textarea, button").forEach(input => { input.disabled = true; });
    $("saveConfig").textContent = "Saving…";
    settingsFeedback("Saving settings…");
    await api("/api/config", { method: "POST", body: JSON.stringify(config) });
    state.config = config;
    renderSavedWorkspace();
    applyNestingConfig();
    applySharedOriginUI();
    settingsDraft = JSON.parse(JSON.stringify(config));
    $("configEditor").value = JSON.stringify(config, null, 2);
    settingsFeedback("Settings saved. Camera changes apply when you reconnect the camera." + (state.workspaceLayoutDirty ? " Marker edits are still unsaved; use Save marker positions in Calibration." : ""));
    message("Settings saved");
  } catch (error) {
    settingsFeedback(displayError(error), true);
  } finally {
    settingsBusy = false;
    $("saveConfig").textContent = "Save settings";
    $("settingsForm").querySelectorAll("input, select, textarea, button").forEach(input => { input.disabled = !hasPermission("configure"); });
  }
}

function setupSettings() {
  $("settingsForm").onsubmit = saveSettings;
  $("settingsSections").addEventListener("input", () => {
    settingsFeedback("Unsaved changes");
    if (!settingsJsonDirty) {
      try { $("configEditor").value = JSON.stringify(readSettingsForm(), null, 2); } catch (_) { /* Allow partially typed numbers. */ }
    }
  });
  $("configEditor").addEventListener("input", () => {
    settingsJsonDirty = true;
    settingsFeedback("JSON changed. Apply JSON to form before saving.");
  });
  $("applyConfigJson").onclick = () => {
    try {
      const config = JSON.parse($("configEditor").value);
      if (!config || typeof config !== "object" || Array.isArray(config)) throw new Error("Configuration must be a JSON object.");
      if (Object.keys(config).some(key => ["__proto__", "constructor", "prototype"].includes(key))) throw new Error("Invalid configuration key.");
      for (const key of ["camera", "workspace", "automation", "calibration", "nesting", "export", "lasercad"]) {
        if (config[key] != null && (typeof config[key] !== "object" || Array.isArray(config[key]))) throw new Error(key + " must be an object.");
      }
      renderSettings(config);
      settingsFeedback("JSON applied to form. Review your changes, then save settings.");
    } catch (error) { settingsFeedback("Could not apply JSON: " + displayError(error), true); }
  };
}

async function loadConfig() {
  try {
    state.config = await api("/api/config");
    applySharedOriginUI();
    renderSettings(state.config);
    applyNestingConfig();
    settingsFeedback("Your saved settings are ready.");
  } catch (error) {
    settingsFeedback("Could not load settings. " + displayError(error), true);
    throw error;
  }
}

function applyNestingConfig() {
  const n = state.config.nesting || {};
  $("spacing").value = n.spacing_mm ?? 2;
  $("edgeClearance").value = n.edge_clearance_mm ?? 3;
  $("rotationStep").value = n.rotation_step_deg ?? 5;
  $("nestingPrecision").value = n.precision_mm ?? 0.5;
  $("strategy").value = n.strategy || "accurate";
  ordinarySpacing = null;
  updateSharedCutControls();
}

function markWorkspaceEdits(dimensions=false) {
  state.workspaceLayoutDirty=true;
  state.workspaceEditVersion++;
  if (dimensions) state.workspaceDimensionsDirty=true;
  $('workspaceEditStatus').textContent='Unsaved marker positions. Save marker positions to make them active.';
}

function isCurrentMarkerGeometry(scale) {
  const layout=state.savedCalibration?.workspace_layout;
  if (!layout?.markers?.length) return true;
  if (!scale?.bed_markers || scale.dictionary!==layout.dictionary || scale.bed_markers.length!==4) return false;
  return layout.markers.every(marker=>{
    const found=scale.bed_markers.find(m=>m.marker_id===marker.marker_id);
    return found && Math.abs(found.x_mm-marker.x_mm)<1e-7 && Math.abs(found.y_mm-marker.y_mm)<1e-7;
  });
}

function clearWorkspaceGeometry() {
  state.workspaceGeometryVersion++;
  resetAnalysisState();state.workflowArtifacts={};
  $('nestingPreviewCanvas').previewResult=null;
  $('nestingPreviewCanvas').hidden=true;
  $('nestingPreviewEmpty').hidden=false;
  $('nestingPreviewSummary').textContent='Marker positions changed. Detect and nest again.';
  $('nestingPreviewLegend').replaceChildren();
  $('openNestingPreview').disabled=true;
}

async function saveWorkspacePositions() {
  if (state.workspaceSaving || !hasPermission('configure')) return;
  state.workspaceSaving=true;state.calibrationReadVersion++;$('saveWorkspacePositions').disabled=true;
  $('workspaceEditStatus').textContent='Saving marker positions…';
  try {
    const layout=readWorkspaceMarkers(), version=state.workspaceEditVersion;
    const saved=await api('/api/calibration/workspace-layout',{method:'POST',body:JSON.stringify(layout)});
    if (state.workspaceEditVersion===version) {state.workspaceLayoutDirty=false;state.workspaceDimensionsDirty=false;}
    if (saved.changed) clearWorkspaceGeometry();
    state.calibrationReadVersion++;
    renderCalibrationStatus(saved.calibration);
    $('workspaceEditStatus').textContent=state.workspaceLayoutDirty ? 'The submitted positions were saved. Newer edits are still unsaved.' : 'Marker positions saved to this application workspace.';
    message(saved.calibration.workspace_calibration_pending ? 'Marker positions saved. Capture or upload all four markers, then detect again or calibrate from the photo. Old measurements and layouts cannot be reused.' : 'Marker positions saved.');
  } catch(error) {$('workspaceEditStatus').textContent='Save did not finish: '+displayError(error);message(displayError(error),true);}
  finally {state.workspaceSaving=false;$('saveWorkspacePositions').disabled=!hasPermission('configure');}
}

function workspaceMarkerRows() {
  return [...document.querySelectorAll('#workspaceMarkers .bed-marker')];
}

function readWorkspaceNumber(input, label) {
  if (!input.value.trim() || !Number.isFinite(Number(input.value))) throw new Error(`Enter a valid ${label}.`);
  return Number(input.value);
}

function fillWorkspaceRectangle() {
  const width = readWorkspaceNumber($("workspaceWidth"), "marker-centre width");
  const height = readWorkspaceNumber($("workspaceHeight"), "marker-centre height");
  if (width <= 0 || height <= 0) throw new Error("Marker-centre width and height must be greater than zero.");
  if (usesId12Frame()) {
    const rows=workspaceMarkerRows();
    const ids=rows.map(row=>Number(row.querySelector('[data-workspace="id"]').value));
    if (![11,12,13].every(id=>ids.includes(id)) || new Set(ids).size!==4) throw new Error('Keep ID 12 as origin, ID 11 along -X and ID 13 along -Y; use one different fourth marker.');
    const byId={11:[-width,0],12:[0,0],13:[0,-height]};
    rows.forEach(row=>{
      const id=Number(row.querySelector('[data-workspace="id"]').value), point=byId[id] || [-width,-height];
      row.querySelector('[data-workspace="x"]').value=String(point[0]);
      row.querySelector('[data-workspace="y"]').value=String(point[1]);
    });
    state.workspaceDimensionsDirty=false;markWorkspaceEdits();
    return;
  }
  const positions = [[0, 0], [width, 0], [width, height], [0, height]];
  workspaceMarkerRows().forEach((row, i) => {
    row.querySelector('[data-workspace="x"]').value = String(positions[i][0]);
    row.querySelector('[data-workspace="y"]').value = String(positions[i][1]);
  });
  state.workspaceDimensionsDirty=false;markWorkspaceEdits();
}

function readWorkspaceMarkers() {
  if (state.workspaceDimensionsDirty) fillWorkspaceRectangle();
  const markers = workspaceMarkerRows().map(row => {
    const read = key => readWorkspaceNumber(row.querySelector(`[data-workspace="${key}"]`), `marker ${key}`);
    return { marker_id: read("id"), x_mm: read("x"), y_mm: read("y") };
  });
  if (markers.length !== 4 || markers.some(m => !Number.isInteger(m.marker_id) || m.marker_id < 0) || new Set(markers.map(m => m.marker_id)).size !== 4) {
    throw new Error("Enter four different non-negative integer marker IDs.");
  }
  return { dictionary: $("workspaceDictionary").value, markers };
}

function applyWorkspaceLayout(layout) {
  if (!layout?.markers?.length || state.workspaceLayoutDirty) return;
  $("workspaceDictionary").value = layout.dictionary;
  workspaceMarkerRows().forEach((row, i) => {
    const marker = layout.markers[i];
    if (!marker) return;
    for (const [key, value] of [["id", marker.marker_id], ["x", marker.x_mm], ["y", marker.y_mm]]) {
      row.querySelector('[data-workspace="' + key + '"]').value = String(value);
    }
  });
  $("workspaceWidth").value = String(Math.max(...layout.markers.map(m => m.x_mm)) - Math.min(...layout.markers.map(m => m.x_mm)));
  $("workspaceHeight").value = String(Math.max(...layout.markers.map(m => m.y_mm)) - Math.min(...layout.markers.map(m => m.y_mm)));
}

async function loadWorkspaceMarkerLayout(version=state.calibrationReadVersion) {
  try {
    const { layout } = await api("/api/calibration/workspace-layout", { cache: "no-store" });
    if (version===state.calibrationReadVersion) applyWorkspaceLayout(layout);
  } catch (error) {
    $("workspaceStatus").textContent += " Enter the four measured marker positions before saving.";
  }
}

// Keep a marker-based DXF reference attached to the same ID when working coordinates change.
// Arbitrary measured points are not inferred from rectangle dimensions.
function alignmentForWorkspaceSave(previousScale, nextScale, alignment) {
  if (usesId12Frame()) return null;
  if (!alignment?.enabled || alignment.reference !== "custom" || !previousScale?.bed_markers?.length) return null;
  if (previousScale.dictionary !== nextScale?.dictionary) return null;
  const previous = previousScale.bed_markers.filter(m => Math.abs(m.x_mm - alignment.design_x_mm) < 0.000001 && Math.abs(m.y_mm - alignment.design_y_mm) < 0.000001);
  if (previous.length !== 1) return null;
  const next = (nextScale.bed_markers || []).filter(m => m.marker_id === previous[0].marker_id);
  if (next.length !== 1) return null;
  return { ...alignment, design_x_mm: next[0].x_mm, design_y_mm: next[0].y_mm };
}

function savedAlignmentText() {
  if (usesId12Frame()) return 'Measurement origin: ID 12 centre (0, 0) mm. DXF origin: top-right corner of the bounding box around all nested templates, calculated for each export. Coordinates are translated only; size, orientation and spacing stay unchanged. Preview box and origin marker are not cutting paths.';
  const a = state.savedAlignment;
  if (!a) return "Saved DXF alignment is loading.";
  if (!a.enabled) return "Saved DXF alignment: disabled; export uses the design coordinates.";
  const point = a.reference === "custom" ? [a.design_x_mm, a.design_y_mm]
    : a.reference === "design_origin" ? [0, 0] : null;
  const marker = point && state.savedCalibration?.scale?.bed_markers?.find(m =>
    Math.abs(m.x_mm - point[0]) < 0.000001 && Math.abs(m.y_mm - point[1]) < 0.000001);
  const label = point ? (marker ? "ID " + marker.marker_id + " centre " : "Design point ") + "(" + point.join(", ") + ") mm"
    : ({rightmost_template_top_right:"Rightmost template top-right corner (automatic for each DXF)",min_x_min_y:"Layout minimum X / minimum Y",min_x_max_y:"Layout minimum X / maximum Y",max_x_min_y:"Layout maximum X / minimum Y",max_x_max_y:"Layout maximum X / maximum Y",center:"Layout centre"})[a.reference] || a.reference;
  return "Saved DXF reference: " + label + " → laser (" + a.laser_x_mm + ", " + a.laser_y_mm + ") mm. Rotation " + a.rotation_deg + "°; reverse X: " + (a.flip_x ? "yes" : "no") + "; reverse Y: " + (a.flip_y ? "yes" : "no") + ".";
}

function renderSavedWorkspace() {
  const profile = state.savedCalibration?.scale;
  const loaded = state.savedCalibration !== null;
  const layout=state.savedCalibration?.workspace_layout;
  const markers = layout?.markers || profile?.bed_markers || [];
  const origin = markers.find(m => Math.abs(m.x_mm) < 0.000001 && Math.abs(m.y_mm) < 0.000001);
  for (const id of ["settingsActiveCalibration", "calibrationSavedSummary"]) {
    const target = $(id);
    if (!target) continue;
    target.replaceChildren();
    const add = (tag, text) => { const node = document.createElement(tag); node.textContent = text; target.append(node); return node; };
    add("h3", "Machine bed and marker calibration");
    add("p", roleCopy(`Usable machine travel: ${state.config.workspace?.width_mm ?? 915} × ${state.config.workspace?.height_mm ?? 630} mm. Physical bed: ${state.config.workspace?.physical_width_mm ?? 960} × ${state.config.workspace?.physical_height_mm ?? 650} mm. Marker measurements remain separate; machine placement must fit the usable travel area.`, `Usable travel: ${state.config.workspace?.width_mm ?? 915} × ${state.config.workspace?.height_mm ?? 630} mm. Bed: ${state.config.workspace?.physical_width_mm ?? 960} × ${state.config.workspace?.physical_height_mm ?? 650} mm.`));
    add("p", state.config.automation?.refresh_workspace_calibration
      ? "Each capture recalculates perspective from the four surveyed markers. Keep all four visible."
      : "Using saved calibration. Keep the camera fixed.").className="role-help";
    if (usesId12Frame()) add("p", "Shared axes: -Y points toward ID 13; perpendicular -X points toward the ID 11 side. The markers need not form a rectangle. ID 12 centre is (0, 0) for measurement and nesting. DXF uses the nested-template bounding-box top-right corner.").className="role-help";
    if (!loaded) { add("p", "Loading saved calibration…"); continue; }
    if (!profile && !markers.length) { add("p", "No working-area calibration is saved. Save it in Calibration to display your measured dimensions and origin."); }
    else {
      const width=markers.length ? Math.max(...markers.map(m=>m.x_mm))-Math.min(...markers.map(m=>m.x_mm)) : profile.working_width_mm;
      const height=markers.length ? Math.max(...markers.map(m=>m.y_mm))-Math.min(...markers.map(m=>m.y_mm)) : profile.working_height_mm;
      add("p", (state.savedCalibration?.workspace_calibration_pending ? 'Saved marker span: ' : 'Calibrated marker span: ') + width + " × " + height + " mm").className = "saved-workspace-size";
      if (markers.length===4 && usesId12Frame()) {
        const byId=new Map(markers.map(m=>[m.marker_id,m]));
        const rectangular=byId.get(11)?.y_mm===0 && byId.get(13)?.x_mm===0 && byId.get(10)?.x_mm===byId.get(11)?.x_mm && byId.get(10)?.y_mm===byId.get(13)?.y_mm;
        if (!rectangular) add('p','Surveyed four-corner layout: the span above is its bounding extent, not an assumed rectangle. Use the individual marker coordinates below.');
      }
      if (state.savedCalibration?.workspace_calibration_pending) add('p','New marker positions are saved. A four-marker photo is required before using this measurement calibration.');
      add("p", "Working origin: " + (origin ? "ID " + origin.marker_id + " centre (0, 0) mm." : markers.length ? "(0, 0) mm in the saved coordinate frame; no marker is at that point." : "Not specified by this scale calibration."));
      if (layout?.dictionary || profile?.dictionary) add("p", "Marker dictionary: " + (layout?.dictionary || profile.dictionary));
      if (markers.length) {
        const table = document.createElement("table"); table.className = "saved-marker-table";
        const head = document.createElement("thead"), row = document.createElement("tr");
        for (const text of ["Marker ID", "X (mm)", "Y (mm)"]) { const cell = document.createElement("th"); cell.textContent = text; cell.setAttribute("scope", "col"); row.append(cell); }
        head.append(row); table.append(head);
        const body = document.createElement("tbody");
        for (const m of markers) {
          const row = document.createElement("tr");
          for (const value of [m.marker_id, m.x_mm, m.y_mm]) { const cell = document.createElement("td"); cell.textContent = String(value); row.append(cell); }
          body.append(row);
        }
        table.append(body); target.append(table);
      }
    }
    add("p", savedAlignmentText()).className = "hint";
    add("p", state.savedCalibration?.workspace_calibration_pending ? 'These are the latest saved marker positions. Photo calibration is pending; the previous scale will not be used.' : "These are the latest saved values. Unsaved edits below do not change the active calibration.").className = "hint role-help";
  }
  const alignmentSummary = $("savedAlignmentStatus");
  if (alignmentSummary) alignmentSummary.textContent = savedAlignmentText();
  document.querySelectorAll('[data-workspace-defaults="true"]').forEach(card => { card.hidden = !loaded || Boolean(profile); });
}

async function loadCalibrationStatus() {
  const version=++state.calibrationReadVersion;
  const data = await api("/api/calibration/status", { cache: "no-store" });
  if (version!==state.calibrationReadVersion) return;
  renderCalibrationStatus(data);
  // Surveyed positions are authoritative even while the photo calibration is pending.
  if (!data.workspace_layout && !data.scale?.bed_markers?.length) await loadWorkspaceMarkerLayout(version);
}

function renderCalibrationStatus(data) {
  state.savedCalibration = data;
  if ($("calibrationMethodStatus")) $("calibrationMethodStatus").textContent = data.scale?.source === "aruco_workspace"
    ? "Active method: measured working area. No additional calibration method is required."
    : data.scale?.source === "aruco_bed"
      ? "Active method: machine-coordinate calibration, available under Advanced. Saving a measured working area replaces it."
      : "Use this method to save your working-area dimensions and marker positions.";
  if (data.scale?.source === "aruco_bed") {
    $("bedDictionary").value = data.scale.dictionary;
    document.querySelectorAll("#bedMarkers .bed-marker").forEach((row, i) => {
      const marker = data.scale.bed_markers[i];
      row.querySelector('[data-bed="id"]').value = marker.marker_id;
      row.querySelector('[data-bed="x"]').value = marker.x_mm;
      row.querySelector('[data-bed="y"]').value = marker.y_mm;
    });
    $("bedStatus").textContent = "Four-marker laser bed coordinates are active.";
  } else $("bedStatus").textContent = data.scale?.source === "aruco_workspace"
    ? "Optional alternative: not active. Your measured working area is already saved."
    : "Optional alternative: not currently in use.";
  $("workspaceStatus").textContent = data.workspace_calibration_pending
    ? 'Latest marker positions are saved. Capture or upload all four markers and calibrate/detect before nesting. Older calibration will not be used.'
    : data.scale?.source === "aruco_workspace"
    ? `Working area active: ${data.scale.working_width_mm.toFixed(1)} x ${data.scale.working_height_mm.toFixed(1)} mm. Uses your saved marker-centre X/Y coordinates.`
    : "Working-area calibration has not been saved. Capture the four markers, then save it here.";
  if (data.workspace_layout?.markers?.length===4) applyWorkspaceLayout(data.workspace_layout);
  else if (data.scale?.bed_markers?.length === 4) applyWorkspaceLayout({ dictionary: data.scale.dictionary, markers: data.scale.bed_markers });
  renderSavedWorkspace();
  state.arucoConfig = data.aruco || {};
  const aruco = state.arucoConfig;
  if ($("arucoScaleHelp")) $("arucoScaleHelp").textContent = `Capture the ${aruco.marker_size_mm || 100} mm marker and save its scale here. Keep the camera position, capture mode and working surface fixed.`;
  $("downloadAruco").textContent = `Download ${aruco.marker_size_mm || 100} mm ArUco Marker`;
  $("calibrationStatus").innerHTML = `
    <div>Camera calibrated: ${data.camera_calibrated ? "YES" : "NO"}</div>
    <div>Lens profile: ${data.lens_profile_source === "checkerboard" ? "Checkerboard calibration" : data.lens_profile_source === "imported" ? "Imported profile - recalibrate for this camera" : "None"}</div>
    <div>Scale calibrated: ${data.scale_calibrated ? "YES" : "NO"}${data.scale?.source === "saved_aruco" ? ` - saved ${data.scale.marker_size_mm} mm ArUco photo` : ""}</div>
    <div>Per-photo ArUco: ${aruco.enabled === false ? "DISABLED" : "ENABLED"}, ${aruco.marker_size_mm || 100} mm marker, ID ${aruco.marker_id ?? "any"}</div>
    <div>Working area: ${data.scale ? `${data.scale.working_width_mm.toFixed(1)} x ${data.scale.working_height_mm.toFixed(1)} mm` : data.workspace_calibration_pending ? "Latest marker positions saved; photo calibration pending" : "Unknown"}</div>`;
}

async function downloadArucoMarker() {
  const data = await api("/api/calibration/aruco-marker", {
    method: "POST",
    body: JSON.stringify({ marker_id: state.arucoConfig?.marker_id ?? 10, side_pixels: 1000, dictionary: state.arucoConfig?.dictionary || "DICT_4X4_50" }),
  });
  $("arucoResult").innerHTML = `<a href="${data.url}" target="_blank">Open printable marker</a><div>${data.note}</div>`;
  message("ArUco marker ready");
}

async function loadCurrentState() {
  const version=state.workspaceGeometryVersion;
  const data = await api("/api/state");
  if (version!==state.workspaceGeometryVersion) return;
  if (data.image) {
    setPreviewCrop(data.preview_crop);
    cameraImage.src = `${data.image}?t=${Date.now()}`;
    $("emptyView").style.display = "none";
  }
  if (data.scale && !isCurrentMarkerGeometry(data.scale)) {clearWorkspaceGeometry();return;}
  if (data.mm_polygon) state.sheathMm = data.mm_polygon;
  if (data.pixel_polygon) state.sheathPixel = data.pixel_polygon;
  if (data.scale) state.scale = data.scale;
  if (data.measurements) renderMeasurements(data.measurements);
  if (data.nesting_result?.sheaths || data.sheaths) renderSheaths(data.nesting_result?.sheaths || data.sheaths);
  if (data.nesting_result) {
    state.placements = data.nesting_result.placements || [];
    $("pieces").textContent = String(state.placements.length);
    $("utilization").textContent = `${((data.nesting_result.utilization || 0) * 100).toFixed(1)}%`;
    $("waste").textContent = `${((data.nesting_result.waste || 0) * 100).toFixed(1)}%`;
  }
  updateViewerMode();
  draw();
}

async function saveScale() {
  if (state.scalePoints.length !== 4) throw new Error("Click four reference square corners in order");
  const payload = {
    image_points: state.scalePoints,
    square_size_mm: Number($("squareSize").value),
    image_width: state.imageNatural.width,
    image_height: state.imageNatural.height,
  };
  await api("/api/calibration/scale", { method: "POST", body: JSON.stringify(payload) });
  state.scalePoints = [];
  await loadCalibrationStatus();
  draw();
  message("Scale calibration saved");
}

// Navigation changes visibility only: no capture, camera reconnect or state reset.
function navigateSection(view) {
  if (view === 'laser-control' && document.body?.dataset.laserControl === 'disabled') return;
  if (!["process", "camera", "calibration", "sheath", "templates", "nesting", "cutting-time", "export", "lasercad", "laser-control", "settings", "data"].includes(view)) return;
  state.activeView = view;
  if (view !== "lasercad") void window.LaserCADTab?.hide();
  $("workspacePreviewTitle").textContent = ({ process: "Live Preview", camera: "Camera Preview", calibration: "Calibration Image", sheath: "Sheath Preview", nesting: "Nesting Preview" })[view] || "Preview";
  $("processSections").open = false;
  $("currentSectionLabel").textContent = view === "process" ? "Processing workspace" : (view === "cutting-time" ? "Cutting Time" : view.charAt(0).toUpperCase() + view.slice(1));
  if (document.body) document.body.dataset.screen = view;
  document.querySelectorAll("nav button").forEach((button) => {
    const active = button.dataset.view === view;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  document.querySelectorAll(".panel-view").forEach((panel) => panel.classList.toggle("active", panel.dataset.panel === view));
  $("processSheath").classList.toggle("active", view === "process");
  $("processSheath").setAttribute("aria-pressed", String(view === "process"));
  if (view === "export") void refreshLaserControls();
  if (view === "lasercad") { $("currentSectionLabel").textContent = "LaserCAD"; window.LaserCADTab?.activate(); }
  if (view === "laser-control") { $("currentSectionLabel").textContent = "Laser Control"; window.LaserControl?.refresh(); }
  if (view === "cutting-time") renderCuttingTime();
  if (view === "data") { renderDataSummary(); refreshDataProjects(); }
  updateViewerMode();
  if (window.requestAnimationFrame) window.requestAnimationFrame(fitCanvas);
  else draw();
}

// Each placed polygon is a closed template outline in millimetres.
// Count complete outlines, including dividers between adjacent templates.
function estimateCuttingTime(placements, sheaths, benchmarkSeconds) {
  if (!Number.isFinite(benchmarkSeconds) || benchmarkSeconds <= 0) throw new Error("Enter a benchmark time greater than zero.");
  const speed = 400 / benchmarkSeconds;
  const groups = new Map((sheaths || []).map((piece, index) => [piece.id, { id: piece.id, label: `Sheath ${index + 1}`, count: 0, length: 0 }]));
  for (const placement of placements) {
    const points = placement.polygon;
    if (!Array.isArray(points) || points.length < 3 || points.some(point => !Array.isArray(point) || point.length < 2 || !Number.isFinite(point[0]) || !Number.isFinite(point[1]))) {
      throw new Error("Cut length unavailable: a placed template has invalid geometry. Run nesting again.");
    }
    let length = 0;
    for (let i = 0; i < points.length; i++) {
      const next = points[(i + 1) % points.length];
      length += Math.hypot(next[0] - points[i][0], next[1] - points[i][1]);
    }
    if (!Number.isFinite(length) || length <= 0) throw new Error("Cut length unavailable: a placed template has an empty outline.");
    const id = placement.sheath_id ?? (sheaths?.length === 1 ? sheaths[0].id : "unassigned");
    if (!groups.has(id)) groups.set(id, { id, label: id === "unassigned" ? (sheaths?.length ? "Unassigned sheath" : "Sheath 1") : String(id), count: 0, length: 0 });
    const group = groups.get(id);
    group.count++;
    group.length += length;
  }
  const rows = [...groups.values()].map(group => ({ ...group, seconds: group.length / speed }));
  const length = rows.reduce((sum, row) => sum + row.length, 0);
  return { rows, speed, length, seconds: length / speed, count: placements.length };
}

function formatCuttingDuration(seconds) {
  if (seconds < 60) return `${seconds.toFixed(1)} sec`;
  const tenths = Math.round(seconds * 10);
  return `${Math.floor(tenths / 600)} min ${((tenths % 600) / 10).toFixed(1)} sec`;
}

function updateDimensionEstimatedTime() {
  const target = $("dimensionEstimatedTime");
  target.textContent = "--";
  if (!state.placements.length) return;
  try {
    const estimate = estimateCuttingTime(state.placements, state.sheaths, Number($("cuttingBenchmarkSeconds").value));
    target.textContent = formatCuttingDuration(estimate.seconds);
  } catch (error) {
    // An invalid benchmark or layout has no usable time estimate.
  }
}

function renderCuttingTime() {
  updateDimensionEstimatedTime();
  const body = $("cuttingTimeRows"), total = $("cuttingTimeTotal");
  body.replaceChildren(); total.replaceChildren();
  const status = $("cuttingTimeStatus");
  try {
    const estimate = estimateCuttingTime(state.placements, state.sheaths, Number($("cuttingBenchmarkSeconds").value));
    $("cuttingSpeed").textContent = `400 mm / ${$("cuttingBenchmarkSeconds").value} seconds = ${estimate.speed.toFixed(2)} mm/second.`;
    status.textContent = estimate.count ? "Current layout estimate. Adjust the benchmark above to match your machine." : "Process a sheath or run nesting to estimate cutting time.";
    if (!estimate.count) return;
    function addRow(target, label, count, length, seconds) {
      const row = document.createElement("tr");
      for (const value of [label, String(count), `${length.toFixed(1)} mm`, formatCuttingDuration(seconds)]) {
        const cell = document.createElement("td"); cell.textContent = value; row.append(cell);
      }
      target.append(row);
    }
    estimate.rows.forEach(row => addRow(body, row.label, row.count, row.length, row.seconds));
    addRow(total, "Total", estimate.count, estimate.length, estimate.seconds);
  } catch (error) {
    $("cuttingSpeed").textContent = "Estimate unavailable.";
    status.textContent = displayError(error);
  }
}
function renderDataSummary() {
  $("dataMeasurements").textContent = state.measurements
    ? `${$("dimensionContext").textContent}: ${$("dimensionWidth").textContent} x ${$("dimensionHeight").textContent}; area ${$("dimensionArea").textContent}`
    : "No piece measured yet.";
  const target = $("dataExport"); target.replaceChildren();
  // Reuse the existing export links, including their actual download attributes.
  if ($("exportResult").children.length) {
    Array.from($("exportResult").children).forEach((node) => target.append(node.cloneNode(true)));
  } else target.textContent = "No exported file available.";
}

// Read existing production CSVs only; these views never mutate workflow state.
let productionReports = [], productionLoadId = 0;
function parseProductionCsv(text) {
  const rows = []; let row = [], cell = "", quoted = false, closed = false;
  text = text.replace(/^\uFEFF/, "");
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') { cell += '"'; i++; }
      else if (ch === '"') { quoted = false; closed = true; }
      else cell += ch;
    } else if (ch === '"' && !cell && !closed) quoted = true;
    else if (ch === ',' || ch === '\n' || ch === '\r') {
      row.push(cell); cell = ""; closed = false;
      if (ch !== ',') { if (row.some(value => value !== "")) rows.push(row); row = []; if (ch === '\r' && text[i + 1] === '\n') i++; }
    } else { if (closed || ch === '"') throw new Error("Invalid CSV quoting."); cell += ch; }
  }
  if (quoted) throw new Error("Incomplete CSV quoted field.");
  if (cell || row.length || closed) { row.push(cell); rows.push(row); }
  const header = rows.shift() || [];
  const required = ['workflow_id', 'status', 'exported_at_local', 'exported_piece_count', 'sheath_count', 'material_area_mm2', 'used_area_mm2', 'template_counts_json'];
  if (new Set(header).size !== header.length || required.some(key => !header.includes(key))) throw new Error("Unrecognized production CSV columns.");
  return rows.map(values => {
    if (values.length !== header.length) throw new Error("A production row has missing or extra fields.");
    return Object.fromEntries(header.map((key, index) => [key, values[index]]));
  });
}
function productionNumber(value) {
  if (value === undefined || String(value).trim() === '') return null;
  const number = Number(value); return Number.isFinite(number) && number >= 0 ? number : null;
}
function summarizeProduction(records) {
  const rows = records.filter(row => row.status === 'exported'), ids = new Set(), templates = new Map();
  let pieces = 0, sheaths = 0, area = 0, used = 0, seconds = 0, timingKnown = rows.length > 0, areaKnown = rows.length > 0;
  for (const row of rows) {
    if (!row.workflow_id || ids.has(row.workflow_id)) throw new Error("Missing or duplicate cycle ID in production CSV.");
    ids.add(row.workflow_id);
    const count = productionNumber(row.exported_piece_count), sheets = productionNumber(row.sheath_count);
    if (!Number.isInteger(count) || !Number.isInteger(sheets)) throw new Error("Invalid production counts.");
    pieces += count; sheaths += sheets;
    const material = productionNumber(row.material_area_mm2), consumed = productionNumber(row.used_area_mm2);
    if (material === null || material === 0 || consumed === null || consumed > material) areaKnown = false;
    else { area += material; used += consumed; }
    const duration = productionNumber(row.cycle_seconds);
    if (duration === null) timingKnown = false; else seconds += duration;
    const counts = JSON.parse(row.template_counts_json);
    if (!Array.isArray(counts)) throw new Error("Invalid template counts.");
    let templateTotal = 0;
    for (const item of counts) {
      if (!item.template_id || !Number.isInteger(item.count) || item.count < 0) throw new Error("Invalid template count.");
      const entry = templates.get(item.template_id) || { name: item.name || item.template_id, count: 0 };
      entry.count += item.count; templates.set(item.template_id, entry); templateTotal += item.count;
    }
    if (templateTotal !== count) throw new Error("Template counts do not match the exported piece total.");
  }
  return { rows, pieces, sheaths, utilization: areaKnown ? used / area * 100 : null,
    seconds: timingKnown ? seconds / rows.length : null, templates: [...templates.values()].sort((a, b) => b.count - a.count) };
}
function renderProduction(records) {
  const summary = summarizeProduction(records), percent = value => value === null ? '--' : `${value.toFixed(1)}%`;
  const metrics = [['Exported cycles', summary.rows.length], ['Pieces', summary.pieces], ['Sheaths', summary.sheaths],
    ['Utilization', percent(summary.utilization)], ['Material waste', percent(summary.utilization === null ? null : 100 - summary.utilization)],
    ['Average cycle', summary.seconds === null ? '--' : formatWorkflowDuration(summary.seconds)]];
  $('productionMetrics').replaceChildren();
  metrics.forEach(([label, value]) => {
    const card = document.createElement('div'); card.className = 'production-metric';
    const title = document.createElement('span'), number = document.createElement('strong'); title.textContent = label; number.textContent = value;
    card.append(title, number); $('productionMetrics').append(card);
  });
  const templates = $('productionTemplates'); templates.replaceChildren();
  if (!summary.templates.length) templates.textContent = 'No exported pieces for this date.';
  const maximum = Math.max(1, ...summary.templates.map(item => item.count));
  summary.templates.forEach(item => {
    const row = document.createElement('div'); row.className = 'production-template-row';
    const label = document.createElement('span'), track = document.createElement('div'), bar = document.createElement('div'), count = document.createElement('strong');
    label.textContent = item.name; count.textContent = item.count; track.className = 'production-bar-track'; bar.className = 'production-bar'; bar.style.width = `${item.count / maximum * 100}%`; track.setAttribute('aria-hidden', 'true'); track.append(bar); row.append(label, track, count); templates.append(row);
  });
  const table = $('productionCycles'); table.replaceChildren();
  summary.rows.slice().sort((a, b) => b.exported_at_local.localeCompare(a.exported_at_local)).forEach(record => {
    const row = document.createElement('tr'); row.title = `Cycle ID: ${record.workflow_id}`;
    const duration = productionNumber(record.cycle_seconds);
    [record.exported_at_local.slice(11, 19), record.cycle_number || '--', record.sheath_count, record.exported_piece_count,
      percent(productionNumber(record.utilization_percent)), percent(productionNumber(record.waste_percent)),
      duration === null ? '--' : formatWorkflowDuration(duration), (record.export_format || '--').toUpperCase()].forEach(value => {
      const cell = document.createElement('td'); cell.textContent = value; row.append(cell);
    }); table.append(row);
  });
  if (!summary.rows.length) { const row = document.createElement('tr'), cell = document.createElement('td'); cell.colSpan = 8; cell.textContent = 'No successfully exported automatic cycles recorded for this date.'; row.append(cell); table.append(row); }
}
function unavailableProduction() {
  renderProduction([]);
  Array.from($('productionMetrics').children).forEach(card => { card.children[1].textContent = '--'; });
  $('productionTemplates').textContent = 'Production data unavailable.';
  $('productionCycles').children[0].children[0].textContent = 'Production data unavailable. Refresh or inspect the CSV.';
}
async function loadProductionReport() {
  const loadId = ++productionLoadId, report = productionReports.find(item => item.name === $('productionDate').value);
  $('productionDownload').hidden = !report;
  $('productionExcelDownload').hidden = !report?.excel_url;
  if (report?.excel_url) $('productionExcelDownload').href = report.excel_url;
  if (!report) { renderProduction([]); $('productionStatus').textContent = 'No daily production records yet.'; return; }
  $('productionDownload').href = report.url;
  $('productionStatus').textContent = 'Loading daily production...';
  $('productionMetrics').replaceChildren(); $('productionTemplates').replaceChildren(); $('productionCycles').replaceChildren();
  try {
    const response = await fetch(report.url, { cache: 'no-store' });
    if (!response.ok) throw new Error(`CSV request failed (${response.status}).`);
    const records = parseProductionCsv(await response.text());
    if (loadId !== productionLoadId) return;
    renderProduction(records);
    $('productionStatus').textContent = `${report.name} | Asia/Kolkata | Exported plans; utilization and waste weighted by material area. Physical cutting completion is not monitored.`;
  } catch (error) {
    if (loadId !== productionLoadId) return;
    unavailableProduction(); $('productionStatus').textContent = `Cannot visualize this CSV: ${displayError(error)} Refresh production or download the file to inspect it.`;
  }
}
function setProductionReports(reports) {
  const selected = $('productionDate').value;
  productionReports = reports.slice().sort((a, b) => b.name.localeCompare(a.name));
  $('productionDate').replaceChildren(...productionReports.map(report => new Option(report.name.replace(/^production_|\.csv$/g, ''), report.name)));
  $('productionDate').disabled = !productionReports.length;
  $('productionDate').value = productionReports.some(report => report.name === selected) ? selected : productionReports[0]?.name || '';
  return loadProductionReport();
}

async function refreshDataProjects() {
  const target = $("dataProjects"), reports = $("dailyProductionReports"); target.textContent = "Loading saved projects...";
  reports.textContent = "Loading daily production files...";
  try {
    const data = await api("/api/projects");
    target.replaceChildren();
    reports.replaceChildren();
    if (!data.production_reports?.length) reports.textContent = "No daily production CSV yet. It is created after the first successfully exported automatic cycle.";
    else data.production_reports.forEach((report) => {
      const row = document.createElement("p"); row.className = "daily-production-report";
      const link = document.createElement("a"); link.href = report.url; link.textContent = report.name; link.setAttribute("download", "");
      row.append(link);
      if (report.excel_url) {
        const excel = document.createElement("a"); excel.href = report.excel_url;
        excel.textContent = "Download Excel report"; excel.setAttribute("download", "");
        row.append(" | ", excel);
      }
      reports.append(row);
    });
    await setProductionReports(data.production_reports || []);
    if (!data.projects?.length) target.textContent = "No saved projects yet.";
    else data.projects.forEach((project) => { const row = document.createElement("p"); row.textContent = project; target.append(row); });
  } catch (error) {
    ++productionLoadId; unavailableProduction(); $("productionDownload").hidden = true; $("productionExcelDownload").hidden = true; $("productionDate").disabled = true; $("productionStatus").textContent = `Cannot load production: ${displayError(error)}`;
    target.textContent = `Cannot load saved projects: ${displayError(error)}`;
    reports.textContent = `Cannot load daily production files: ${displayError(error)}`;
    message(displayError(error), true);
  }
}

async function retryDailyProduction() {
  const button = $("retryDailyProduction");
  if (button.disabled || state.workflowBusy || state.pendingMutations) return;
  button.disabled = true;
  try {
    const data = await api("/api/production/retry", { method: "POST", body: JSON.stringify({}) });
    displayWorkflow(data.workflow);
    renderExportLinks(data.workflow.result.export);
    openLaserControlForExport(data.workflow.result.export);
    await refreshDataProjects();
    message("Daily production record saved.");
  } catch (error) { message(displayError(error), true); }
  finally { button.disabled = false; }
}

async function recoverDailyProduction() {
  const button = $("recoverDailyProduction");
  if (button.disabled || state.workflowBusy || state.pendingMutations) return;
  button.disabled = true;
  try {
    const result = await api("/api/production/recover", { method: "POST", body: JSON.stringify({}) });
    await refreshDataProjects();
    message(`Daily production files updated from ${result.checked} saved export records.`);
  } catch (error) { message(displayError(error), true); }
  finally { button.disabled = false; }
}


let laserControlSnapshot = null;
let laserControlPending = false;
let laserControlRefreshPending = false;
let laserControlGeneration = 0;

function updateLaserControlButtons() {
  document.querySelectorAll("[data-laser-action]").forEach(button => {
    const action = button.dataset.laserAction;
    button.disabled = !hasPermission("operate") || !laserControlSnapshot?.actions?.[action]
      || (action !== "stop" && (laserControlPending || state.workflowBusy));
  });
  $("openLaserCadControl").disabled = !hasPermission("operate") || laserControlPending || state.workflowBusy;
}

async function refreshLaserControls() {
  if (laserControlRefreshPending || !state.user) return;
  laserControlRefreshPending = true;
  const generation = ++laserControlGeneration;
  try {
    const data = await api("/api/laser/control/status");
    if (generation !== laserControlGeneration) return;
    laserControlSnapshot = data;
    $("laserControlStatus").textContent = data.available
      ? data.window_title + " · " + (data.communication_selection?.join(" / ") || "Communication selection unavailable") + " · machine connection unverified"
      : data.message;
    $("laserControlJob").textContent = data.job
      ? (data.job_matches ? "Current exported DXF: " : "Exported DXF is not the active LaserCAD drawing: ") + data.job
      : "Export a DXF from this app before Download, Run Box or Start.";
    const p = data.displayed_position || {};
    $("laserDisplayedPosition").textContent = p.x != null && p.y != null
      ? "LaserCAD displayed values: X = " + p.x + ", Y = " + p.y + ". Use Read Current Pos to update."
      : "Position fields could not be identified. Read Current Pos requests an update in LaserCAD.";
  } catch (error) {
    laserControlSnapshot = null;
    $("laserControlStatus").textContent = "LaserCAD controls unavailable: " + displayError(error);
  } finally {
    laserControlRefreshPending = false;
    updateLaserControlButtons();
  }
}

async function sendLaserControl(action) {
  if (!hasPermission("operate") || !laserControlSnapshot?.actions?.[action]) return;
  if (action !== "stop" && (laserControlPending || state.workflowBusy)) return;
  const payload = {action, request_id: crypto.randomUUID()};
  if (action === "move_to") {
    for (const [axis, id] of [["x", "laserTargetX"], ["y", "laserTargetY"]]) {
      const input = $(id);
      if (!input.value.trim() || !input.checkValidity() || !Number.isFinite(Number(input.value))) {
        $("laserControlResult").textContent = "Enter valid target X and Y coordinates in millimetres."; return;
      }
      payload[axis] = Number(input.value);
    }
  }
  if (["start", "datum", "origin", "move_to"].includes(action)) {
    const prompt = action === "start" ? "Start cutting the active exported DXF in LaserCAD? " + (laserControlSnapshot.job || "")
      : action === "datum" ? "Invoke LaserCAD Datum? This may home the machine."
      : action === "origin" ? "Invoke LaserCAD Origin for the connected machine?"
      : "Move using LaserCAD to X=" + payload.x + ", Y=" + payload.y + " mm?";
    if (!window.confirm(prompt)) return;
    payload.confirm_start = action === "start";
  }
  const isStop = action === "stop";
  if (!isStop) laserControlPending = true;
  updateLaserControlButtons();
  $("laserControlResult").textContent = "Sending " + action + " request to LaserCAD…";
  try {
    const result = await api("/api/laser/control/" + (isStop ? "stop" : "command"), {method:"POST", body:JSON.stringify(payload)});
    $("laserControlResult").textContent = result.message;
  } catch (error) {
    $("laserControlResult").textContent = displayError(error) + " No automatic retry was made.";
  } finally {
    if (!isStop) laserControlPending = false;
    await refreshLaserControls();
    updateLaserControlButtons();
  }
}

function setupLaserControls() {
  $("refreshLaserControl").onclick = refreshLaserControls;
  $("openLaserCadControl").onclick = async () => {
    if (!hasPermission("operate") || laserControlPending || state.workflowBusy) return;
    laserControlPending = true; updateLaserControlButtons();
    try {
      const result = await api("/api/laser/control/open", {method:"POST", body:"{}"});
      $("laserControlResult").textContent = result.message;
    } catch (error) { $("laserControlResult").textContent = displayError(error); }
    finally { laserControlPending = false; await refreshLaserControls(); }
  };
  document.querySelectorAll("[data-laser-action]").forEach(button => {
    button.onclick = () => sendLaserControl(button.dataset.laserAction);
  });
  // Read-only polling; no machine action is ever scheduled.
  setInterval(() => { if (state.activeView === "export" && state.user && !document.hidden) void refreshLaserControls(); }, 2000);
}


function bindEvents() {
  setupLaserControls();
  $("cuttingBenchmarkSeconds").oninput = renderCuttingTime;
  $("strategy").onchange = updateSharedCutControls;
  $("productionDate").onchange = loadProductionReport;
  $("refreshProduction").onclick = refreshDataProjects;
  $("recoverDailyProduction").onclick = recoverDailyProduction;
  $("retryDailyProduction").onclick = retryDailyProduction;
  $("showLiveNesting").onchange = draw;
  $("sheathSelection").onchange = () => selectSheath($("sheathSelection").value);
  $("saveTemplateOrder").onclick = async () => {
    $("saveTemplateOrder").disabled = true;
    try {
      const templates = state.templates.map((template, index) => ({ ...template, priority: index + 1 }));
      await api("/api/templates", { method: "POST", body: JSON.stringify(templates) });
      state.templates = templates;
      renderTemplates();
      message("Nesting order saved. Preferred sizes will be tried first in every nesting strategy.");
    } catch (error) { message(displayError(error), true); }
    finally { $("saveTemplateOrder").disabled = false; }
  };

  document.querySelectorAll("[data-template-tab]").forEach((button) => {
    button.onclick = () => showTemplateTab(button.dataset.templateTab);
  });
  $("preferredTemplate").onchange = () => editPreferredTemplate($("preferredTemplate").value);
  $("preferredShape").onchange = updatePreferenceFields;
  $("preferredWidth").oninput = updatePreferenceFields;
  $("preferredUnits").onchange = () => {
    const previous = $("preferredUnits").dataset.previousUnit || "mm";
    const current = $("preferredUnits").value;
    const factor = (previous === "in" ? 25.4 : 1) / (current === "in" ? 25.4 : 1);
    ["preferredWidth", "preferredHeight"].forEach((id) => {
      if ($(id).value !== "") $(id).value = String(Number((Number($(id).value) * factor).toFixed(6)));
    });
    $("preferredUnits").dataset.previousUnit = current;
    updatePreferenceFields();
  };
  $("templatePreferenceForm").onsubmit = async (event) => {
    event.preventDefault();
    $("savePreference").disabled = true;
    try {
      const id = $("preferredTemplate").value || `custom-${crypto.randomUUID()}`;
      const previous = state.templates.find((template) => template.id === id);
      const template = preferenceTemplate({
        id, name: $("preferredName").value, shape: $("preferredShape").value,
        unit: $("preferredUnits").value, width: $("preferredWidth").value,
        height: $("preferredHeight").value, quantity: $("preferredQuantity").value,
        rotate: $("preferredRotate").checked, orientation: $("preferredOrientation").value,
      }, previous);
      const templates = previous ? state.templates.map((item) => item.id === id ? template : item) : [...state.templates, template];
      await api("/api/templates", { method: "POST", body: JSON.stringify(templates) });
      state.templates = templates;
      renderTemplates();
      $("librarySelection").value = id;
      updateActiveTemplate();
      editPreferredTemplate(id);
      message(`Saved ${template.name}. It is available in Templates and Auto Nest.`);
    } catch (error) { message(displayError(error), true); }
    finally { $("savePreference").disabled = false; }
  };

  $("saveArucoScale").onclick = async () => {
    $("saveArucoScale").disabled = true;
    try {
      const data = await api("/api/calibration/aruco-scale", { method: "POST" });
      resetAnalysisState();
      state.scalePoints = [];
      $("scalePoints").innerHTML = "";
      await loadCalibrationStatus();
      message(`Saved ${data.scale.marker_size_mm} mm ArUco scale. Remove the marker and capture the sheath without moving the camera.`);
    } catch (error) { message(displayError(error), true); }
    finally { $("saveArucoScale").disabled = false; }
  };
  $("viewZoom").oninput = () => {
    state.viewZoom = Number($("viewZoom").value);
    $("zoomValue").textContent = `${state.viewZoom.toFixed(1)}x`;
    positionCameraImage();
    draw();
  };
  $("resetZoom").onclick = () => {
    $("viewZoom").value = "1";
    $("viewZoom").oninput();
  };
  const updateBoardCount = () => {
    $("checkerboardCount").textContent = `${state.checkerboardPaths.length} checkerboard photos`;
  };
  $("captureCheckerboard").onclick = async () => {
    try {
      const data = await api("/api/calibration/camera/capture", { method: "POST" });
      state.checkerboardPaths.push(data.image_path);
      updateBoardCount();
      message("Raw checkerboard photo saved. Move or tilt the board for the next photo.");
    } catch (error) { message(displayError(error), true); }
  };
  $("uploadCheckerboards").onchange = async (event) => {
    try {
      const form = new FormData();
      for (const file of event.target.files) form.append("files", file);
      if (!event.target.files.length) return;
      const data = await api("/api/calibration/camera/upload", { method: "POST", body: form });
      state.checkerboardPaths.push(...data.image_paths);
      updateBoardCount();
    } catch (error) { message(displayError(error), true); }
    event.target.value = "";
  };
  $("clearCheckerboards").onclick = () => { state.checkerboardPaths = []; updateBoardCount(); };
  $("calibrateLens").onclick = async () => {
    if (state.checkerboardPaths.length < 3) { message("Capture or upload at least three checkerboard photos; 10-15 is recommended.", true); return; }
    $("calibrateLens").disabled = true;
    try {
      const profile = await api("/api/calibration/camera", { method: "POST", body: JSON.stringify({
        image_paths: state.checkerboardPaths,
        checkerboard: [Number($("boardColumns").value), Number($("boardRows").value)],
        square_size_mm: Number($("boardSquare").value),
      }) });
      resetAnalysisState();
      state.scalePoints = [];
      $("scalePoints").innerHTML = "";
      await loadCalibrationStatus();
      await loadCurrentState();
      message(`Lens calibration saved (RMS ${profile.rms_error.toFixed(3)} pixels). Capture again and save four-marker working-area calibration.`);
    } catch (error) { message(displayError(error), true); }
    finally { $("calibrateLens").disabled = false; }
  };

  document.querySelectorAll("nav button").forEach((button) => {
    button.addEventListener("click", () => navigateSection(button.dataset.view));
  });
  $("processSheath").onclick = () => navigateSection("process");
  $("refreshData").onclick = () => refreshDataProjects();
  $("refreshCameras").onclick = () => refreshCamera().catch((e) => message(e.message, true));
  $("openCamera").onclick = () => openCamera().catch((e) => message(e.message, true));
  $("openMachineCamera").onclick = () => openMachineCamera().catch((e) => message(e.message, true));
  $("captureImage").onclick = () => captureImage().catch((e) => message(e.message, true));
  $("uploadImage").onchange = (event) => uploadImage(event).catch((e) => message(e.message, true));
  $("detectSheath").onclick = () => detectSheath().catch((e) => message(e.message, true));
  $("editBoundary").onclick = () => {
    state.editBoundary = !state.editBoundary;
    message(state.editBoundary ? "Boundary editing enabled" : "Boundary editing disabled");
  };
  $("acceptBoundary").onclick = () => message("Boundary accepted");
  $("autoNest").onclick = async () => {
    $("autoNest").disabled = true;
    try { await autoNest(); }
    catch (error) { message(displayError(error), true); }
    finally { $("autoNest").disabled = false; }
  };
  $("exportSvg").onclick = () => exportLayout("svg").catch((e) => message(e.message, true));
  $("exportDxf").onclick = () => exportLayout("dxf").catch((e) => message(e.message, true));
  Object.values(alignmentFields).forEach((id) => $(id).addEventListener("input", clearAlignmentPreview));
  $("previewAlignment").onclick = () => previewAlignment().catch((e) => message(e.message, true));
  $("saveAlignment").onclick = async () => {
    try {
      const alignment = readAlignment();
      await api("/api/laser/alignment", { method: "POST", body: JSON.stringify(alignment) });
      state.savedAlignment = alignment;
      renderSavedWorkspace();
      message("Laser alignment defaults saved.");
    } catch (error) { message(displayError(error), true); }
  };
  $("saveScale").onclick = () => saveScale().catch((e) => message(e.message, true));
  $("downloadAruco").onclick = () => downloadArucoMarker().catch((e) => message(e.message, true));
  $("downloadBedMarkers").onclick = async () => {
    try {
      const ids = usesId12Frame() ? readWorkspaceMarkers().markers.map(marker=>marker.marker_id) : [...document.querySelectorAll('[data-bed="id"]')].map((input) => Number(input.value));
      const dictionary = usesId12Frame() ? $("workspaceDictionary").value : $("bedDictionary").value;
      const limit = dictionary === "DICT_4X4_100" ? 100 : 50;
      if (new Set(ids).size !== 4 || ids.some((id) => !Number.isInteger(id) || id < 0 || id >= limit)) throw new Error("Enter four different IDs within the selected dictionary.");
      $("bedMarkerDownloads").replaceChildren();
      for (const marker_id of ids) {
        const data = await api("/api/calibration/aruco-marker", { method: "POST", body: JSON.stringify({ marker_id, dictionary }) });
        const link = document.createElement("a");
        link.href = data.url; link.target = "_blank";
        link.textContent = `ID ${marker_id} (${data.print_size_mm} mm)`;
        $("bedMarkerDownloads").append(link, document.createElement("br"));
      }
      message("Print all four black marker squares at the same indicated size with white margins.");
    } catch (error) { message(displayError(error), true); }
  };
  $('workspaceWidth').addEventListener('input',()=>markWorkspaceEdits(true));
  $('workspaceHeight').addEventListener('input',()=>markWorkspaceEdits(true));
  $('workspaceMarkers').addEventListener('input',()=>{state.workspaceDimensionsDirty=false;markWorkspaceEdits();});
  $('workspaceDictionary').addEventListener('change',()=>markWorkspaceEdits());
  $('saveWorkspacePositions').onclick=saveWorkspacePositions;
  $("applyWorkspaceRectangle").onclick = () => {
    try { fillWorkspaceRectangle(); message("Rectangular positions filled. Click Save marker positions, or calibrate from a four-marker photo."); }
    catch (error) { message(displayError(error), true); }
  };
  $("calibrateWorkspace").onclick = async () => {
    if (state.workspaceSaving) return;
    state.workspaceSaving=true;state.calibrationReadVersion++;
    $("calibrateWorkspace").disabled = true;
    try {
      const layout = readWorkspaceMarkers(), version=state.workspaceEditVersion;
      const [previousCalibration, alignment] = await Promise.all([
        api("/api/calibration/status", { cache: "no-store" }),
        api("/api/laser/alignment", { cache: "no-store" }),
      ]);
      const saved = await api("/api/calibration/workspace", { method: "POST", body: JSON.stringify(layout) });
      if (state.workspaceEditVersion===version) {state.workspaceLayoutDirty=false;state.workspaceDimensionsDirty=false;}
      state.calibrationReadVersion++;
      renderCalibrationStatus({ ...state.savedCalibration, workspace_layout:saved.layout || layout, workspace_calibration_pending:false, scale: saved.scale, scale_calibrated: true });
      clearWorkspaceGeometry();
      $('workspaceEditStatus').textContent=state.workspaceLayoutDirty ? 'Newer marker edits remain unsaved.' : 'Marker positions and photo calibration saved.';
      const updatedAlignment = alignmentForWorkspaceSave(previousCalibration.scale, saved.scale, alignment);
      if (updatedAlignment) {
        try {
          await api("/api/laser/alignment", { method: "POST", body: JSON.stringify(updatedAlignment) });
        } catch (error) {
          throw new Error("Working area was saved, but its DXF reference could not be updated: " + displayError(error) + ". Review and save alignment in Export before exporting another DXF.");
        }
      }
      await loadAlignment();
      await loadCalibrationStatus();
      const note = updatedAlignment ? " Its marker-based DXF reference is up to date."
        : alignment.enabled && alignment.reference === "custom"
          ? " The custom DXF reference was retained because its marker could not be identified; review Export alignment." : "";
      message("Working-area coordinates saved." + note + " Capture and detect the material again before nesting.");
    } catch (error) { message(displayError(error), true); }
    finally {state.workspaceSaving=false;$("calibrateWorkspace").disabled=!hasPermission('configure');}
  };
  $("calibrateBed").onclick = async () => {
    try {
      const markers = [...document.querySelectorAll("#bedMarkers .bed-marker")].map((row) => {
        const read = (key) => {
          const value = row.querySelector(`[data-bed="${key}"]`).value;
          if (!value.trim() || !Number.isFinite(Number(value))) throw new Error("Enter all four IDs and measured laser coordinates.");
          return Number(value);
        };
        return { marker_id: read("id"), x_mm: read("x"), y_mm: read("y") };
      });
      await api("/api/calibration/aruco-bed", { method: "POST", body: JSON.stringify({ markers, dictionary: $("bedDictionary").value }) });
      resetAnalysisState();
      await loadCalibrationStatus();
      await loadAlignment();
      message("Laser bed alignment saved. Capture the sheath, detect it, then nest again; DXF will use laser coordinates.");
    } catch (error) { message(displayError(error), true); }
  };
  $("clearScalePoints").onclick = () => {
    state.scalePoints = [];
    $("scalePoints").innerHTML = "";
    draw();
  };
  $("saveTemplates").onclick = async () => {
    try {
      await api("/api/templates", { method: "POST", body: JSON.stringify(state.templates) });
      message("Templates saved");
    } catch (error) { message(displayError(error), true); }
  };
  setupSettings();
  $("saveProject").onclick = async () => {
    try {
      const data = await api("/api/project/save", { method: "POST", body: JSON.stringify({}) });
      message(`Project saved: ${data.project_dir}`);
      if (state.activeView === "data") await refreshDataProjects();
    } catch (error) { message(displayError(error), true); }
  };
  $("deletePlacement").onclick = () => {
    if (state.selectedPlacement >= 0) state.placements.splice(state.selectedPlacement, 1);
    state.selectedPlacement = -1;
    draw();
  };
  $("templates").addEventListener("input", (event) => {
    const target = event.target;
    if (state.workflowBusy) return;
    const template = state.templates.find((item) => item.id === target.dataset.templateId);
    const field = target.dataset.field;
    if (template && field) {
      template[field] = field === "enabled" ? target.checked : Number(target.value);
      updateActiveTemplate();
    }
  });
  $("restoreCameraExposure").onclick = async () => {
    try {
      const result = await api("/api/camera/control", { method: "POST", body: JSON.stringify({ name: "auto_exposure", value: 1 }) });
      if (!result.ok) throw new Error("This camera driver does not accept automatic exposure. Adjust exposure manually or improve lighting on the board.");
      $("cameraExposureStatus").textContent = "Camera accepted auto exposure. Allow a few seconds for brightness to settle; preview correction also remains active.";
      await syncCameraStatus();
      applyRolePermissions();
    } catch (error) { message(displayError(error), true); }
  };
  $("cameraControls").addEventListener("change", async (event) => {
    const target = event.target;
    if (target.dataset.control) {
      try {
        const result = await api("/api/camera/control", { method: "POST", body: JSON.stringify({ name: target.dataset.control, value: Number(target.value) }) });
        if (!result.ok) throw new Error("The camera driver rejected this setting. Its previous value is still in use.");
        message("Camera setting applied.");
      } catch (error) { message(displayError(error), true); }
      try { await syncCameraStatus(); applyRolePermissions(); } catch (error) { message(displayError(error), true); }
    }
  });
  ["showBoundary", "showPoints", "showGrid"].forEach((id) => $(id).addEventListener("change", draw));
  overlay.addEventListener("pointerdown", () => overlay.focus({ preventScroll: true }));
  overlay.addEventListener("click", (event) => {
    if (state.workflowBusy) return;
    const rect = overlay.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const canvasX = x * devicePixelRatio;
    const canvasY = y * devicePixelRatio;
    if (state.editBoundary && state.sheathPixel) {
      const imagePoint = fromImageCanvas(canvasX, canvasY);
      if (!imagePoint) return;
      state.sheathPixel.push(imagePoint);
      draw();
      return;
    }
    if (state.scalePoints.length < 4 && document.querySelector('[data-panel="calibration"]').classList.contains("active")) {
      const imagePoint = fromImageCanvas(canvasX, canvasY);
      if (!imagePoint) return;
      state.scalePoints.push(imagePoint);
      $("scalePoints").innerHTML = state.scalePoints.map((p) => `<li>${p[0].toFixed(1)}, ${p[1].toFixed(1)}</li>`).join("");
      draw();
      return;
    }
    state.selectedPlacement = state.placements.findIndex((placement) => pointInPolygon(fromCanvas(canvasX, canvasY), placement.polygon));
    draw();
  });
  cameraImage.addEventListener("error", () => {
    if (cameraImage.src.includes("/api/camera/stream")) $("cyclePreviewLabel").textContent = "Live camera unavailable. Use Refresh camera to reconnect.";
  });
  cameraImage.addEventListener("load", () => {
    state.imageNatural = {
      width: cameraImage.naturalWidth || state.imageNatural.width,
      height: cameraImage.naturalHeight || state.imageNatural.height,
    };
    fitCanvas();
  });
  window.addEventListener("resize", fitCanvas);
}


function updateActiveTemplate() {
  const template = state.templates.find((item) => item.id === state.selectedTemplateId);
  const selected = state.templates.find((item) => item.id === $("librarySelection").value);
  const pending = ($("librarySelection").value || null) !== state.selectedTemplateId;
  $("activeTemplate").textContent = template ? template.name : "All enabled templates";
  const count = state.templates.filter((item) => item.enabled && item.quantity > 0).length;
  $("activeTemplateDetail").textContent = template
    ? `${templateDescription(template)} / Quantity ${template.quantity}${template.enabled ? "" : " / Excluded: enable this template before running"}`
    : `${count} enabled ${count === 1 ? "template" : "templates"} with quantities / Tried in nesting priority order`;
  $("templateSelectionHint").textContent = pending
    ? selected ? `${selected.name} is selected. Click Use this template to apply it.` : "Click Use enabled templates to restore the full enabled library."
    : "This is the selection Spacebar will use.";
  $("templateCount").textContent = `${state.templates.length} saved / ${state.templates.filter((item) => item.enabled).length} enabled`;
  document.querySelectorAll(".template-card").forEach((row) => {
    const item = state.templates.find((candidate) => candidate.id === row.dataset.templateId);
    if (!item) return;
    const active = state.selectedTemplateId === item.id;
    row.classList.toggle("is-active", active);
    const badge = row.querySelector(".template-badge");
    if (badge) badge.textContent = active ? "In use" : item.enabled ? "Enabled" : "Excluded";
  });
  $("loadTemplate").textContent = pending ? (selected ? "Use this template" : "Use enabled templates") : (selected ? "Template is in use" : "Using enabled templates");
  $("loadTemplate").disabled = state.workflowBusy || !pending;
  $("editSelectedTemplate").disabled = state.workflowBusy || !selected;
  $("deleteTemplate").disabled = state.workflowBusy || !selected;
}

function loadSelectedTemplate() {
  if (state.workflowBusy) return;
  const id = $("librarySelection").value;
  if (id && !state.templates.some((item) => item.id === id)) throw new Error("Template missing. Refresh the library.");
  state.selectedTemplateId = id || null;
  renderTemplates();
  message($("activeTemplate").textContent);
}

async function deleteSelectedTemplate() {
  if (state.workflowBusy || state.pendingMutations) return;
  const template = state.templates.find((item) => item.id === $("librarySelection").value);
  if (!template) { message("Select a valid template to delete.", true); return; }
  if (!window.confirm(`Delete template "${template.name}"? This cannot be undone.`)) return;
  try {
    const data = await api(`/api/templates/${encodeURIComponent(template.id)}`, { method: "DELETE" });
    state.templates = data.templates;
    if (state.selectedTemplateId === template.id) state.selectedTemplateId = null;
    renderTemplates();
    if ($("preferredTemplate").value === "") { editPreferredTemplate("", false); $("templateEditor").open = false; }
    message(`Deleted ${template.name}`);
  } catch (error) { message(displayError(error), true); }
}

const workflowDisabled = new Map();
function setWorkflowBusy(busy) {
  state.workflowBusy = busy;
  if (busy) {
    document.querySelectorAll(".panel button, .panel input, .panel select, .panel textarea, .panel summary, #cycleRefreshCamera, #captureImage, #workflowFormat").forEach((node) => {
      if (!workflowDisabled.has(node)) workflowDisabled.set(node, node.disabled);
      node.disabled = true;
    });
  } else {
    workflowDisabled.forEach((disabled, node) => { node.disabled = disabled; });
    workflowDisabled.clear();
    updateActiveTemplate();
  }
  applyRolePermissions();
}

const cycleStages = [
  { stage: "Capturing image", id: "cycleCapture" },
  { stage: "Correcting distortion", id: "cycleCorrection" },
  { stage: "Detecting material and converting coordinates", id: "cycleDetection" },
  { stage: "Processing and applying nesting priority", id: "cycleNesting" },
  { stage: "Exporting", id: "cycleExport" },
];
const cyclePreviews = {
  live: "cyclePreviewLive", capture: "cyclePreviewCapture", corrected: "cyclePreviewCorrected",
  boundary: "cyclePreviewBoundary", mask: "cyclePreviewMask", layout: "cyclePreviewLayout",
};

function cyclePreviewSource(view) {
  const artifacts = state.workflowArtifacts;
  return { capture: artifacts.captured_image, corrected: artifacts.corrected_image,
    boundary: artifacts.detection?.overlay, mask: artifacts.detection?.mask,
    layout: artifacts.nesting?.placements?.length ? artifacts.corrected_image : null }[view];
}

function canShowNestingPreview() {
  return !!state.workflowArtifacts.nesting?.placements?.length;
}

function renderNestingPreview(result) {
  if (!result?.placements?.length) return;
  $("nestingPreviewCard").hidden = false;
  $("nestingPreviewEmpty").hidden = true;
  $("nestingPreviewCanvas").hidden = false;
  $("openNestingPreview").disabled = false;
  const waste = Number.isFinite(result.waste) && result.waste >= 0 && result.waste <= 1 ? `${(100 * result.waste).toFixed(1)}%` : "-";
  $("nestingPreviewSummary").textContent = `${result.placements.length} placed pieces / ${(100 * (result.utilization || 0)).toFixed(1)}% utilization / ${waste} waste`;
  const legend = $("nestingPreviewLegend");
  legend.replaceChildren();
  result.placements.forEach((item, index) => {
    // The saved placement name keeps the last result independent of library edits.
    const name = item.template_name || state.templates.find((template) => template.id === item.template_id)?.name;
    const size = name ? name.replace(/(\d+(?:\.\d+)?)\s*["\u2033]/g, "$1 inch") : "Size unavailable";
    const entry = document.createElement("span");
    entry.textContent = `${index + 1} -> ${size}${result.sheaths?.length > 1 ? ` / ${sheathLabel(item.sheath_id)}` : ""}`;
    legend.append(entry);
  });
  const canvas = $("nestingPreviewCanvas");
  canvas.previewResult = result;
  if (canvas.clientWidth && canvas.clientHeight) {
    canvas.width = Math.round(canvas.clientWidth * devicePixelRatio);
    canvas.height = Math.round(canvas.clientHeight * devicePixelRatio);
  }
  const context = canvas.getContext("2d");
  if (!context || typeof context.beginPath !== "function") return;
  const boundaries = result.sheath_polygons || [result.sheath_polygon || state.sheathMm || []];
  const points = [...boundaries.flat(), ...result.placements.flatMap((item) => item.polygon)];
  if (!points.length) return;
  const xs = points.map((p) => p[0]), ys = points.map((p) => p[1]);
  const minX = Math.min(...xs), minY = Math.min(...ys), width = Math.max(1, Math.max(...xs) - minX), height = Math.max(1, Math.max(...ys) - minY);
  const factor = Math.min((canvas.width - 32) / width, (canvas.height - 32) / height);
  const offsetX = (canvas.width - width * factor) / 2, offsetY = (canvas.height - height * factor) / 2;
  const yUp = result.scale ? result.scale.coordinate_frame === 'id12_negative_xy' : usesId12Frame();
  const mapper = ([x, y]) => [offsetX + (x - minX) * factor, offsetY + (yUp ? Math.max(...ys)-y : y-minY) * factor];
  context.clearRect(0, 0, canvas.width, canvas.height);
  boundaries.forEach((polygon) => drawPolygon(polygon, "#49c37b", "rgba(73,195,123,0.08)", 1, mapper, context));
  result.placements.forEach((item, index) => {
    drawPolygon(item.polygon, item.valid === false ? "#e05d5d" : "#5db8e0", "rgba(93,184,224,0.18)", 1, mapper, context);
    const center = item.polygon.reduce((a, p) => [a[0] + p[0] / item.polygon.length, a[1] + p[1] / item.polygon.length], [0, 0]);
    const [x, y] = mapper(center);
    context.font = `${12 * devicePixelRatio}px system-ui`; context.fillStyle = "#edf1f2"; context.textAlign = "center"; context.textBaseline = "middle";
    context.fillText(String(index + 1), x, y);
  });
  drawTemplateBounds(result.placements,mapper,context,yUp);
}

function templateBounds(placements, yUp = worldYUp()) {
  const points=(placements || []).flatMap(item=>item.polygon || []);
  if (!points.length) return null;
  const xs=points.map(p=>p[0]), ys=points.map(p=>p[1]);
  const minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys);
  return {minX,maxX,minY,maxY,origin:[maxX,yUp?maxY:minY],polygon:[[minX,minY],[maxX,minY],[maxX,maxY],[minX,maxY]]};
}

function drawTemplateBounds(placements, mapper=toCanvas, context=ctx, yUp=worldYUp(), updateStatus=true) {
  if (!usesId12Frame()) return;
  const bounds=templateBounds(placements,yUp);
  const status=updateStatus ? $('nestingExportOrigin') : null;
  if (!bounds) {
    if (status) status.textContent='DXF origin: top-right corner of the box around all nested templates, calculated after nesting. Preview guide only.';
    return;
  }
  if (status) status.textContent=`DXF origin: template-box top-right (${bounds.origin.map(n=>n.toFixed(2)).join(', ')}) mm in ID 12 coordinates → DXF (0, 0). Box ${(bounds.maxX-bounds.minX).toFixed(2)} × ${(bounds.maxY-bounds.minY).toFixed(2)} mm. Preview guide only; not a cutting path.`;
  context.save();context.setLineDash([7*devicePixelRatio,5*devicePixelRatio]);
  drawPolygon(bounds.polygon,'#f4d35e','transparent',1.6,mapper,context);
  context.setLineDash([]);
  const [x,y]=mapper(bounds.origin);
  context.strokeStyle='#f4d35e';context.lineWidth=2*devicePixelRatio;
  context.beginPath();context.arc(x,y,5*devicePixelRatio,0,Math.PI*2);context.stroke();
  context.font=`${12*devicePixelRatio}px system-ui`;context.textAlign='right';context.textBaseline='top';
  const label='DXF origin (0, 0)',pad=5*devicePixelRatio;
  const labelWidth=context.measureText(label).width;
  const tx=Math.max(labelWidth+pad,Math.min(context.canvas.width-pad,x-pad));
  const ty=Math.max(pad,Math.min(context.canvas.height-22*devicePixelRatio,y+pad));
  context.fillStyle='#172225';context.fillRect(tx-labelWidth-pad,ty-pad,labelWidth+2*pad,22*devicePixelRatio);
  context.fillStyle='#f4d35e';context.fillText(label,tx,ty);
  context.restore();
}

function showWorkflowPreview(view, manual = true) {
  if (view !== "live" && !(view === "layout" ? canShowNestingPreview() : cyclePreviewSource(view))) return;
  state.workflowPreview = view;
  state.workflowPreviewManual = manual;
  if (view === "live") startLivePreview();
  else {
    setPreviewCrop(view === "capture" || !cyclePreviewSource(view) ? null : state.workflowArtifacts.preview_crop);
    cameraImage.src = cyclePreviewSource(view) || "";
    $("emptyView").style.display = "none";
  }
  const labels = { live: "", capture: "Captured image before lens correction",
    corrected: "Corrected camera image", boundary: "Detected material boundary", mask: "Material segmentation mask",
    layout: canProjectLayoutOnPhoto() ? "Nesting layout on the corrected image" : "Nesting layout in millimetres" };
  $("cyclePreviewLabel").textContent = labels[view];
  updateDimensionSummary();
  Object.entries(cyclePreviews).forEach(([key, id]) => $(id).classList.toggle("active", key === view));
  updateViewerMode();
  draw();
}

function updateCycleVisuals(status) {
  if (status.id && status.id !== state.visualWorkflowId) {
    state.visualWorkflowId = status.id;
    state.workflowArtifacts = {};
    state.workflowPreviewManual = false;
    resetAnalysisState();
  }
  if (status.artifacts) state.workflowArtifacts = status.artifacts;
  else if (status.result) state.workflowArtifacts = {
    detection: status.result.detection, nesting: status.result.nesting, export: status.result.export,
  };
  if ([state.workflowArtifacts.detection,state.workflowArtifacts.nesting].some(item=>item && !isCurrentMarkerGeometry(item.scale))) {
    clearWorkspaceGeometry();
    $('cyclePreviewLabel').textContent='Previous layout used older marker positions. Detect and nest again.';
    return;
  }
  const detection = state.workflowArtifacts.detection;
  if (detection) {
    state.sheathMm = detection.mm_polygon;
    state.sheathPixel = detection.pixel_polygon;
    state.scale = detection.scale;
    if (detection.measurements) renderMeasurements(detection.measurements);
    if (detection.sheaths) renderSheaths(detection.sheaths);
  }
  const nesting = state.workflowArtifacts.nesting;
  if (nesting) {
    state.placements = nesting.placements;
    renderNestingPreview(nesting);
    if (nesting.sheath_polygon) state.sheathMm = nesting.sheath_polygon;
    if (nesting.scale) state.scale = nesting.scale;
    if (nesting.sheaths) renderSheaths(nesting.sheaths);
    $("pieces").textContent = String(nesting.placements.length);
    $("utilization").textContent = `${((nesting.utilization || 0) * 100).toFixed(1)}%`;
    $("waste").textContent = `${((nesting.waste || 0) * 100).toFixed(1)}%`;
  }
  Object.entries(cyclePreviews).forEach(([view, id]) => { $(id).disabled = view !== "live" && !(view === "layout" ? canShowNestingPreview() : cyclePreviewSource(view)); });
  const desired = state.workflowPreviewManual ? state.workflowPreview
    : status.stage === "Completed" && !status.busy ? "live"
    : canShowNestingPreview() ? "layout" : cyclePreviewSource("boundary") ? "boundary"
    : cyclePreviewSource("corrected") ? "corrected" : cyclePreviewSource("capture") ? "capture" : null;
  if (desired && (state.workflowPreview !== desired || (desired === "live" && !cameraImage.src.includes("/api/camera/stream")) || (desired !== "live" && cyclePreviewSource(desired) && cameraImage.src !== new URL(cyclePreviewSource(desired), document.baseURI).href))) {
    showWorkflowPreview(desired, state.workflowPreviewManual);
  }
}

function formatWorkflowDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return "-";
  const ticks = Math.round(seconds * 10), hours = Math.floor(ticks / 36000);
  const minutes = Math.floor(ticks / 600) % 60, wholeSeconds = Math.floor(ticks / 10) % 60;
  return `${hours ? hours + ":" : ""}${String(minutes).padStart(2, "0")}:${String(wholeSeconds).padStart(2, "0")}.${ticks % 10}`;
}

function renderWorkflowTimings(status) {
  // A rejected start may retain a previous job's completed history on the server.
  const events = Array.isArray(status.events) && !(status.stage === "Failed" && !status.events.some((event) => event.status === "failed")) ? status.events : [];
  const names = ["Capture", "Lens correction", "Detection", "Nesting", "Export"];
  const recorded = events.filter((event) => cycleStages.some((item) => item.stage === event.stage) && Number.isFinite(event.elapsed_seconds) && event.elapsed_seconds >= 0);
  const current = recorded.find((event) => event.stage === status.stage) || recorded.at(-1);
  $("workflowCurrentTimer").textContent = `${status.busy ? "Stage" : "Last stage"}: ${formatWorkflowDuration(current?.elapsed_seconds)}`;
  $("workflowTotalTimer").textContent = `Cycle: ${recorded.length ? formatWorkflowDuration(recorded.reduce((sum, event) => sum + event.elapsed_seconds, 0)) : "-"}`;
  const rows = cycleStages.map((item, index) => {
    const event = recorded.find((entry) => entry.stage === item.stage);
    const row = document.createElement("div"); row.className = "workflow-timer-row"; row.dataset.state = event?.status || "pending";
    const label = document.createElement("dt"); label.textContent = names[index];
    const time = document.createElement("dd"); time.textContent = formatWorkflowDuration(event?.elapsed_seconds) + (event?.status === "running" ? " | running" : event?.status === "failed" ? " | failed" : "");
    row.append(label, time); return row;
  });
  $("workflowStageTimers").replaceChildren(...rows);
}

function displayWorkflow(status) {
  if(state.user?.role==='viewer' && status.error)status={...status,error:displayError(status.error)};
  renderWorkflowTimings(status);
  const retryProduction = status.stage === "Failed" && !status.busy && status.artifacts?.export?.csv && !status.artifacts.export.daily_production;
  $("retryDailyProduction").hidden = !retryProduction;
  if (retryProduction) renderExportLinks(status.artifacts.export);
  const complete = status.stage === "Completed" && !status.busy;
  $("workflowStatus").textContent = status.error ? `Failed - ${status.error}` : complete ? "Ready for next cycle - press Space" : status.stage;
  $("cycleTitle").textContent = `Cycle ${status.cycle_number || 1}${complete ? " completed" : ""}`;
  $("cycleState").textContent = status.error ? "Failed" : complete ? `Ready for cycle ${(status.cycle_number || 1) + 1}` : status.stage;
  $("cycleState").dataset.state = status.error ? "failed" : complete ? "ready" : status.busy ? "running" : "ready";
  let index = cycleStages.findIndex((item) => item.stage === status.stage);
  if (status.stage === "Failed") index = cycleStages.findIndex((item) => status.error?.startsWith(item.stage + ":"));
  cycleStages.forEach((item, i) => {
    const event = status.events?.find((entry) => entry.stage === item.stage);
    const kind = event?.status || (status.stage === "Completed" ? "completed" : i < index ? "completed"
      : i === index ? status.stage === "Failed" ? "failed" : "running" : "pending");
    $(item.id).dataset.state = kind;
    const text = { pending: "Waiting", running: "Working", completed: "Done", failed: "Failed" }[kind];
    $(item.id + "Status").textContent = text + (event?.elapsed_seconds ? ` / ${event.elapsed_seconds.toFixed(1)}s` : "");
  });
  // Display the actual worker stage in a single box without changing its workflow.
  $("processStageBox").dataset.state = status.error ? "failed" : complete ? "completed" : status.busy ? "running" : "ready";
  $("processStageTitle").textContent = status.error ? "Processing failed" : complete ? "Export completed" : status.stage || "Ready";
  $("processStageStep").textContent = status.busy && index >= 0 ? `Step ${index + 1} of ${cycleStages.length}` : "Automatic workflow";
  $("processStageDetail").textContent = status.error ? status.error : complete
    ? "Ready for the next piece. Press Space to start a new cycle."
    : status.busy ? "Processing this piece. Please wait." : "Press Space to start.";
  if (status.busy && status.stage === "Processing and applying nesting priority" && status.sheath_count > 1) {
    $("processStageDetail").textContent = `Processing sheath ${status.sheath_index} of ${status.sheath_count}.`;
  }
  $("cycleNextHelp").textContent = status.error ? status.error : complete
    ? "Export finished. Replace the piece, then press Space for a fresh capture. The last result stays available below."
    : status.busy ? "Processing this piece. Spacebar cannot start a duplicate cycle."
    : "Place the piece, click the workspace and press Space to start.";
  if (status.busy && status.stage === "Processing and applying nesting priority" && status.sheath_count > 1) {
    $("cycleNextHelp").textContent = `Processing sheath ${status.sheath_index} of ${status.sheath_count}. Each sheath receives its own quantities and priority.`;
  }
  $("cycleResult").hidden = !complete;
  $("cycleCsvLink").hidden = !complete || !status.result?.export?.csv;
  if (complete && status.result?.export?.csv) $("cycleCsvLink").href = status.result.export.csv.url;
  if (complete && status.result?.export) {
    $("cycleResultText").textContent = `Cycle ${status.cycle_number || 1}: ${status.result.nesting.placements.length} pieces exported successfully`;
    $("cycleExportLink").href = status.result.export.url;
  }
  if (complete && status.supervised) {
    $('workflowStatus').textContent = 'DXF prepared — continue in Laser Control';
    $('cycleState').textContent = 'Awaiting cutting approval';
    $('cycleTitle').textContent = `Cycle ${status.cycle_number || 1}: preparation completed`;
    $('processStageTitle').textContent = 'DXF prepared — cutting approval required';
    $('processStageDetail').textContent = 'Continue in Laser Control. Review LaserCAD, confirm Start, then confirm cutting completion before loading another sheath.';
    $('cycleNextHelp').textContent = 'Preparation completed. The supervised cycle remains active until cutting completion or an explicit abort is confirmed.';
  }
  if (window.SpaceCycle) window.SpaceCycle.decorate();
  renderLaserCadHandoff(complete ? status.result.export : null);
  updateCycleVisuals(status);
}

async function followWorkflow(status, expectedId = null) {
  // A lost HTTP response does not cancel a server worker. Keep the UI locked until status is known.
  while (!status || status.busy) {
    if (status && expectedId && status.id !== expectedId) throw new Error("The requested workflow was not started. Check server status and try again.");
    if (status) { displayWorkflow(status); openLaserControlForExport(status.artifacts?.export); }
    await new Promise((resolve) => setTimeout(resolve, 300));
    try { status = await api("/api/workflow"); }
    catch (error) { if (error.status === 401) throw error; $("workflowStatus").textContent = "Connection lost - waiting for workflow status. " + displayError(error); }
  }
  if (expectedId && status.id !== expectedId) throw new Error("The requested workflow was not started. Check server status and try again.");
  displayWorkflow(status);
  if (status.stage === "Completed") {
    const output = status.result.export;
    renderExportLinks(output);
    openLaserControlForExport(output);
    message(`Completed: ${status.result.nesting.placements.length} pieces exported to ${output.path}`);
    await loadCurrentState().catch((error) => message(`Export completed; preview refresh failed: ${displayError(error)}`, true));
    updateCycleVisuals(status);
    showWorkflowPreview("live", false);
  } else if (status.error) message(status.error, true);
}

async function runWorkflow(space = null) {
  if (!space && window.SpaceCycle) return window.SpaceCycle.advance();
  if (!hasPermission('operate')) return;
  if (state.workflowBusy || state.pendingMutations || state.cameraRefreshing) return;
  const supervised = document.body?.dataset.laserControl !== 'disabled' && !!$('supervisedCutting')?.checked;
  if (supervised && window.LaserControl?.hasActiveCycle()) { navigateSection('laser-control'); message('Finish or abort the active supervised cycle before loading the next sheath.',true); return; }
  if (supervised && $('workflowFormat').value !== 'dxf') { message('Choose DXF for supervised cutting, or turn off supervised cutting for an export-only workflow.', true); return; }
  setWorkflowBusy(true);
  let status;
  let expectedId = crypto.randomUUID();
  try {
    const payload = { ...nestingPayload(), request_id: expectedId, selected_template_id: state.selectedTemplateId,
      supervised, operator_loaded:supervised,
      ...(space ? {space_cycle_id:space.cycle_id,space_revision:space.revision} : {}),
      export: { format: $("workflowFormat").value, include_sheath: false,
        ...($("workflowFormat").value === "dxf" ? { alignment: readAlignment() } : {}) } };
    try { status = await api("/api/workflow", { method: "POST", body: JSON.stringify(payload) }); }
    catch (error) {
      if (error.status && error.status !== 409) throw error;
      message(displayError(error), true);
      // Includes rejected duplicate starts and responses lost after the worker was accepted.
      try { status = await api("/api/workflow"); } catch (_) { status = null; }
      if (error.status === 409) {
        if (!status?.busy) throw error;
        expectedId = status.id;
      }
    }
    await followWorkflow(status, expectedId);
  } catch (error) {
    displayWorkflow({ stage: "Failed", error: displayError(error) });
    message(displayError(error), true);
  } finally {
    setWorkflowBusy(false);
    restoreWorkflowFocus();
  }
}

window.beginSupervisedCycle = () => {
  if (document.body?.dataset.laserControl === 'disabled' || !$('supervisedCutting')) return;
  $('supervisedCutting').checked = true; $('workflowFormat').value = 'dxf';
  navigateSection('process'); return runWorkflow();
};

function restoreWorkflowFocus(clickedAction = null) {
  if (document.hidden || !document.hasFocus() || state.editBoundary || ["calibration", "lasercad", "laser-control"].includes(state.activeView)) return;
  const focused = document.activeElement;
  // Return cycle actions to the camera workspace; never steal focus from an editor.
  if (!focused || focused === document.body || focused === overlay || focused === $("processSheath") || focused === clickedAction ||
      focused.closest?.("[data-cycle-preview], #openNestingPreview")) {
    overlay.focus({ preventScroll: true });
  }
}


function workflowActionClick(event) {
  // Mouse actions return to the capture workspace. Keyboard-activated buttons
  // keep their native focus and Space behavior, as do text fields and dialogs.
  if (!(event.detail >= 1) || event.defaultPrevented) return;
  const action = event.target?.closest?.('button, #cycleCsvLink, #exportResult a[download]');
  if (!action || action.disabled || action.closest('dialog, [role="dialog"]')) return;
  restoreWorkflowFocus(action);
}

function workflowKeydown(event) {
  if (window.SpaceCycle) { window.SpaceCycle.keydown(event); return; }
  if (!hasPermission('operate')) return;
  if (event.code !== "Space" || event.repeat || event.defaultPrevented || event.isComposing ||
      event.ctrlKey || event.altKey || event.metaKey || event.shiftKey || document.hidden ||
      !document.hasFocus() || state.editBoundary || ["calibration", "lasercad", "laser-control"].includes(state.activeView)) return;
  const ownsSpace = (node) => node?.closest?.('input, textarea, select, button, a, summary, [contenteditable]:not([contenteditable="false"]), [role="textbox"], [role="combobox"], [role="button"], [role="slider"], [role="checkbox"], [role="listbox"], [role="menuitem"], [role="radio"], [role="switch"], [role="tab"], [role="option"], [role="treeitem"], dialog');
  if (ownsSpace(event.target) || ownsSpace(document.activeElement)) return;
  event.preventDefault();
  if (!state.workflowBusy && !state.pendingMutations && !state.cameraRefreshing) void runWorkflow();
}

let workflowKeysRegistered = false;
function registerWorkflowKeys() {
  if (workflowKeysRegistered) return;
  document.addEventListener("keydown", workflowKeydown);
  document.addEventListener("click", workflowActionClick);
  workflowKeysRegistered = true;
}
function unregisterWorkflowKeys() {
  document.removeEventListener("keydown", workflowKeydown);
  document.removeEventListener("click", workflowActionClick);
  workflowKeysRegistered = false;
}
window.addEventListener("pagehide", unregisterWorkflowKeys);
window.addEventListener("pageshow", registerWorkflowKeys);

function pointInPolygon(point, polygon) {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const xi = polygon[i][0], yi = polygon[i][1];
    const xj = polygon[j][0], yj = polygon[j][1];
    const intersect = yi > point[1] !== yj > point[1] && point[0] < ((xj - xi) * (point[1] - yi)) / (yj - yi) + xi;
    if (intersect) inside = !inside;
  }
  return inside;
}

async function init() {
  bindEvents();
  registerWorkflowKeys();
  $("openNestingPreview").onclick = () => {
    showWorkflowPreview("layout");
    if (window.requestAnimationFrame) window.requestAnimationFrame(fitCanvas);
  };
  $("cycleRefreshCamera").onclick = () => refreshCamera().catch((error) => message(displayError(error), true));
  $("showDimensions").onchange = draw;
  Object.entries(cyclePreviews).forEach(([view, id]) => { $(id).onclick = () => showWorkflowPreview(view); });
  $("loadTemplate").onclick = () => { try { loadSelectedTemplate(); } catch (error) { message(displayError(error), true); } };
  $("librarySelection").onchange = updateActiveTemplate;
  $("addTemplate").onclick = () => editPreferredTemplate("");
  $("deleteTemplate").onclick = deleteSelectedTemplate;
  $("editSelectedTemplate").onclick = () => { if (!state.workflowBusy && $("librarySelection").value) editPreferredTemplate($("librarySelection").value); };
  $("refreshTemplates").onclick = () => loadTemplates().catch((error) => message(displayError(error), true));
  updateViewerMode();
  fitCanvas();
  bindAccountUI();
  const account = await api('/api/auth/me');
  if (!account.user) { lockAccess(account.setup_required); return; }
  acceptAccount(account);
  await initializeWorkspace();
}

async function initializeWorkspace() {
  const results = await Promise.allSettled([loadConfig(), loadTemplates(), loadCalibrationStatus(), loadAlignment()]);
  results.filter((result) => result.status === "rejected").forEach((result) => message(result.reason.message, true));
  await loadCurrentState().catch(() => {});
  await initializeDefaultCamera().catch((error) => message(displayError(error), true));
  const workflow = await api("/api/workflow");
  if (workflow.busy) {
    setWorkflowBusy(true);
    try { await followWorkflow(workflow); } finally { setWorkflowBusy(false); }
  } else displayWorkflow(workflow);
  applyRolePermissions();
}

function hasPermission(permission) {
  return !!state.user?.permissions?.includes(permission);
}
function actionPermission(path) {
  if (path === '/api/laser-control/profiles') return 'configure';
  if (path === '/api/config' || path.startsWith('/api/calibration/') || path.startsWith('/api/laser/alignment') || path === '/api/camera/control') return 'configure';
  if (path === '/api/templates' || path.startsWith('/api/templates/')) return 'templates';
  return 'operate';
}
const roleLabels = { admin: 'Admin', editor: 'Editor / Manager', viewer: 'Read-only' };
const roleDisabled = new Map();
function applyRolePermissions() {
  window.RolePresentation?.apply(state.user?.role);
  // Restore the prior control state before applying permissions for the current account.
  roleDisabled.forEach((disabled,node) => { node.disabled = disabled; });
  roleDisabled.clear();
  const deny = (selector, permission) => {
    if (hasPermission(permission)) return;
    document.querySelectorAll(selector).forEach(node => {
      if (!roleDisabled.has(node)) roleDisabled.set(node,node.disabled);
      node.disabled = true;
      node.title = `Requires ${permission === 'configure' ? 'Admin' : permission === 'operate' ? 'assigned workflow or operator' : 'Editor/Manager or Admin'} access`;
    });
  };
  deny('[data-panel="calibration"] button, [data-panel="calibration"] input, [data-panel="calibration"] select, [data-panel="settings"] input, [data-panel="settings"] select, [data-panel="settings"] textarea, [data-panel="settings"] button, #cameraControls input, #cameraControls button, #restoreCameraExposure, #widthInput, #heightInput, #fpsInput, #saveAlignment, #previewAlignment, #alignmentReference, #alignmentDesignX, #alignmentDesignY, #alignmentLaserX, #alignmentLaserY, #alignmentRotation, #alignmentFlipX, #alignmentFlipY', 'configure');
  deny('#laserCadTabOpen, #laserCadTabRetry, #laserCadTabDetach, #manualDatum', 'operate');
  deny('[data-panel="templates"] button:not([data-template-tab]):not(#refreshTemplates), [data-panel="templates"] input, [data-panel="templates"] select', 'templates');
  deny('[data-panel="nesting"] button, [data-panel="nesting"] input, [data-panel="nesting"] select, [data-panel="sheath"] button, [data-panel="sheath"] input, #captureImage, #cycleRefreshCamera, #openCamera, #openMachineCamera, #uploadImage, #cameraSelect, #workflowFormat, #exportSvg, #exportDxf, #saveProject, #retryDailyProduction, #recoverDailyProduction', 'operate');
  overlay.style.pointerEvents = hasPermission('operate') ? '' : 'none';
  if (!hasPermission('operate')) state.editBoundary = false;
  updateLaserControlButtons();
  $('signOut').disabled = state.workflowBusy;
  $('openAccount').disabled = state.workflowBusy;
  applySharedOriginUI();
}
function lockAccess(setupRequired = false, detail = '') {
  void window.LaserCADTab?.hide();
  window.LaserControl?.reset();
  state.user = null; state.csrfToken = null;
  document.querySelectorAll('input[type="password"]').forEach(input => { input.value = ''; });
  document.body.dataset.auth = 'locked';
  $('authGate').hidden = false;
  $('authGate').dataset.setup = String(setupRequired);
  $('authTitle').textContent = setupRequired ? 'Create your administrator account' : 'Sign in';
  $('authIntro').textContent = setupRequired ? 'Set up the first Admin account on this computer. There is no default password.' : 'Use your application account to continue.';
  $('authConfirmLabel').hidden = !setupRequired;
  $('authConfirm').required = setupRequired;
  $('authPasswordHelp').hidden = !setupRequired;
  $('authPassword').autocomplete = setupRequired ? 'new-password' : 'current-password';
  $('authPassword').minLength = setupRequired ? 12 : 1;
  $('authPassword').value = ''; $('authConfirm').value = '';
  $('signInSubmit').textContent = setupRequired ? 'Create Admin account' : 'Sign in';
  $('authError').textContent = detail;
  $('statusPill').textContent = setupRequired ? 'SETUP REQUIRED' : 'SIGN IN';
  $('statusPill').classList.add('warn');
  if ($('accountDialog').open) $('accountDialog').close();
  cameraImage.removeAttribute?.('src');
  applyRolePermissions();
}
function renderApplicationVersion(info) {
  if (typeof info?.version !== 'string' || !info.version.trim()) throw new Error('Application version is unavailable.');
  document.querySelectorAll('[data-app-version]').forEach(node => { node.textContent = 'v' + info.version; });
  if ($('applicationBuild')) $('applicationBuild').textContent = 'Version ' + info.version + (info.update ? ' · ' + info.update : '');
}
async function refreshApplicationVersion() {
  if (!state.user) return;
  try { renderApplicationVersion(await api('/api/application-info', {cache:'no-store'})); }
  catch (_) { document.querySelectorAll('[data-app-version]').forEach(node => { node.textContent = 'Version unavailable'; }); }
}

function acceptAccount(account) {
  document.querySelectorAll('input[type="password"]').forEach(input => { input.value = ''; });
  state.user = account.user; state.csrfToken = account.csrf_token;
  void refreshApplicationVersion();
  document.body.dataset.auth = 'signed-in';
  $('authGate').hidden = true;
  $('authPassword').value = ''; $('authConfirm').value = '';
  $('signedInUser').textContent = `${account.user.username} | ${roleLabels[account.user.role]}`;
  applyRolePermissions();
  fitCanvas();
}
function accountResult(text, error = false) {
  if(error)text=displayError(text);
  $('accountStatus').textContent = text;
  $('accountStatus').style.color = error ? 'var(--danger)' : 'var(--accent)';
}
async function refreshAccountPanel() {
  const admin = hasPermission('users');
  $('manageAccounts').hidden = !admin;
  if (!admin) return;
  const [accounts, audit] = await Promise.all([api('/api/auth/users'), api('/api/auth/audit')]);
  $('accountList').replaceChildren();
  accounts.users.forEach(user => {
    const row = document.createElement('div'); row.className = 'account-user-row';
    const name = document.createElement('strong'); name.textContent = user.username;
    const role = document.createElement('select'); role.setAttribute('aria-label', `Role for ${user.username}`);
    Object.entries(roleLabels).forEach(([value,label]) => role.append(new Option(label,value))); role.value = user.role;
    const workflow = document.createElement('select');workflow.setAttribute('aria-label',`Workflow for ${user.username}`);
    const unassigned=new Option('Not assigned','');
    workflow.append(unassigned,new Option('Automated','automated'),new Option('Manual','manual'));
    workflow.value=user.workflow_mode || '';
    const syncWorkflowRole=()=>{workflow.disabled=role.value==='admin';unassigned.hidden=unassigned.disabled=role.value!=='viewer';if(role.value==='editor' && !workflow.value)workflow.value='automated';};
    role.onchange=syncWorkflowRole;syncWorkflowRole();
    const enabled = document.createElement('input'); enabled.type = 'checkbox'; enabled.checked = user.enabled;
    const enabledLabel = document.createElement('label'); enabledLabel.append(enabled,'Enabled');
    const password = document.createElement('input'); password.type = 'password'; password.autocomplete = 'new-password';
    password.placeholder = user.id === state.user.id ? 'Use Change your password above' : 'New password (optional)';
    password.disabled = user.id === state.user.id; password.minLength = 12; password.maxLength = 256;
    password.setAttribute('aria-label', `Reset password for ${user.username}`);
    const save = document.createElement('button'); save.textContent = 'Save'; save.type = 'button';
    save.onclick = async () => {
      try {
        if (password.value && password.value.length < 12) throw new Error('Use at least 12 characters for the new password.');
        save.disabled = true;
        await api(`/api/auth/users/${user.id}`, {method:'PATCH',body:JSON.stringify({role:role.value,enabled:enabled.checked,workflow_mode:role.value==='admin'?null:workflow.value||null})});
        if (password.value) await api(`/api/auth/users/${user.id}/password`, {method:'POST',body:JSON.stringify({password:password.value})});
        password.value = '';
        accountResult(`Saved ${user.username}. Role/workflow changes and password resets require a new sign-in.`);
        const current = await api('/api/auth/me');
        if (!current.user) { lockAccess(false,'Your account changed. Sign in again.'); return; }
        acceptAccount(current); await refreshAccountPanel();
      } catch(error) { accountResult(displayError(error),true); } finally { save.disabled = false; }
    };
    const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = 'Delete';
    remove.className = 'delete-account'; remove.setAttribute('aria-label', `Delete user ${user.username}`);
    remove.disabled = user.id === state.user.id;
    if (remove.disabled) remove.title = 'You cannot delete your signed-in account.';
    remove.onclick = async () => {
      if (remove.disabled) return;
      if (!window.confirm(`Delete user ${user.username}? This permanently removes their account and signs them out. Production records and activity history will be kept.`)) return;
      try {
        remove.disabled = true; save.disabled = true;
        await api(`/api/auth/users/${user.id}`, {method:'DELETE'});
        accountResult(`Deleted ${user.username}. Production records and activity history were kept.`);
        await refreshAccountPanel();
      } catch(error) { accountResult(displayError(error),true); }
      finally { remove.disabled = user.id === state.user?.id; save.disabled = false; }
    };
    row.append(name,role,workflow,enabledLabel,password,save,remove); $('accountList').append(row);
  });
  $('accountAudit').replaceChildren();
  audit.events.forEach(event => {
    const row = document.createElement('tr');
    const time = new Date(event.at).toLocaleString('en-IN',{timeZone:'Asia/Kolkata'});
    const labels = {'/api/workflow':'Start processing','/api/capture':'Capture image','/api/upload':'Load image','/api/detect':'Detect sheath','/api/nest':'Run nesting','/api/export':'Export cutting plan','/api/templates':'Save template library','/api/config':'Change settings','/api/project/save':'Save project','/api/camera/open':'Open camera','/api/camera/open-machine':'Open USB camera','/api/camera/close':'Close camera','/api/camera/control':'Adjust camera','/api/production/retry':'Retry production record','/api/production/recover':'Recover production records'};
    let action = ['POST','PATCH','PUT','DELETE'].includes(event.action) ? labels[event.resource] || (event.resource.startsWith('/api/calibration/') ? 'Change calibration' : event.resource.startsWith('/api/templates/') ? 'Delete template' : 'Update application data') : event.action;
    if (!['POST','PATCH','PUT','DELETE','access denied'].includes(event.action) && event.resource) action += ' | '+event.resource.replace(' / editor /',' / Editor / Manager /').replace(' / viewer /',' / Read-only /').replace(' / admin /',' / Admin /');
    const outcome = ({success:'Done',denied:'Not allowed','200':'Done','202':'Job accepted','403':'Not allowed','409':'Busy or conflict'})[event.outcome] || (Number(event.outcome)>=400 ? 'Failed' : event.outcome);
    [time,event.username,action,outcome].forEach(text => {
      const cell = document.createElement('td'); cell.textContent = text; row.append(cell);
    });
    if (event.workflow_id) row.title = 'Workflow reference: '+event.workflow_id;
    $('accountAudit').append(row);
  });
}
function desktopProtectedDownload(event) {
  if (!window.pywebview || event.defaultPrevented) return;
  const link = event.target?.closest?.('a[href]');
  if (!link) return;
  const url = new URL(link.href, document.baseURI), local = new URL(document.baseURI);
  if (url.origin !== local.origin || !(url.pathname.startsWith('/data/') || /^\/api\/production\/[^/]+\/excel$/.test(url.pathname))) return;
  if (!state.user) { event.preventDefault(); lockAccess(false,'Sign in to download this file.'); return; }
  // Use the renderer's existing authenticated download and Save As mechanism.
  // An external browser would not have this desktop session's cookie.
  link.target = '_self'; link.setAttribute('download','');
  if (event.detail > 0) link.blur?.();
}

function bindAccountUI() {
  document.addEventListener('click', desktopProtectedDownload, true);
  $('signInForm').onsubmit = async event => {
    event.preventDefault(); $('authError').textContent = '';
    const setup = $('authGate').dataset.setup === 'true';
    try {
      if (setup && $('authPassword').value !== $('authConfirm').value) throw new Error('The passwords do not match.');
      $('signInSubmit').disabled = true;
      const account = await api(setup ? '/api/auth/setup' : '/api/auth/login', {method:'POST',body:JSON.stringify({username:$('authUsername').value.trim(),password:$('authPassword').value})});
      acceptAccount(account); await initializeWorkspace();
    } catch(error) { $('authError').textContent = displayError(error); }
    finally { $('signInSubmit').disabled = false; }
  };
  $('signOut').onclick = async () => {
    if (state.workflowBusy || state.pendingMutations) return;
    try { await api('/api/auth/logout',{method:'POST'}); lockAccess(false); }
    catch(error) { message(displayError(error),true); }
  };
  $('openAccount').onclick = async () => {
    if (state.workflowBusy) return;
    $('accountDialog').showModal(); accountResult('');
    try { await refreshAccountPanel(); } catch(error) { accountResult(displayError(error),true); }
  };
  $('closeAccount').onclick = () => $('accountDialog').close();
  $('refreshAccounts').onclick = () => refreshAccountPanel().catch(error => accountResult(displayError(error),true));
  $('newUserRole').addEventListener('change',()=>{
    const role=$('newUserRole').value;const mode=$('newUserWorkflow');mode.disabled=role==='admin';mode.options[0].hidden=mode.options[0].disabled=role!=='viewer';mode.value=role==='viewer'?'':'automated';
  });
  $('addAccountForm').onsubmit = async event => {
    event.preventDefault();
    const button = event.submitter;
    try {
      if (button) button.disabled = true;
      await api('/api/auth/users',{method:'POST',body:JSON.stringify({username:$('newUsername').value.trim(),password:$('initialPassword').value,role:$('newUserRole').value,workflow_mode:$('newUserRole').value==='admin'?null:$('newUserWorkflow').value||null})});
      $('initialPassword').value = ''; $('newUsername').value = '';
      accountResult('User added.'); await refreshAccountPanel();
    } catch(error) { accountResult(displayError(error),true); }
    finally { if (button) button.disabled = false; }
  };
  $('changePasswordForm').onsubmit = async event => {
    event.preventDefault();
    try {
      if ($('newPassword').value !== $('confirmNewPassword').value) throw new Error('The new passwords do not match.');
      await api('/api/auth/password',{method:'POST',body:JSON.stringify({current_password:$('currentPassword').value,password:$('newPassword').value})});
      ['currentPassword','newPassword','confirmNewPassword'].forEach(id => { $(id).value = ''; });
      lockAccess(false,'Password changed. Sign in with your new password.');
    } catch(error) { accountResult(displayError(error),true); }
  };
  if (typeof MutationObserver !== 'undefined') {
    new MutationObserver(applyRolePermissions).observe(document.querySelector('main'),{childList:true,subtree:true});
  }
  setInterval(async () => {
    if (!state.user) return;
    const checkedSession = state.csrfToken;
    try {
      const account = await api('/api/auth/me');
      if (!state.user || state.csrfToken !== checkedSession) return;
      if (!account.user) lockAccess(false,'Your session ended. Sign in again.');
      else if (account.user.id !== state.user?.id || account.user.role !== state.user?.role || account.user.workflow_mode !== state.user?.workflow_mode || account.csrf_token !== state.csrfToken) {
        acceptAccount(account);
        if ($('accountDialog').open) await refreshAccountPanel();
      }
    } catch(error) { if (error.status === 401) lockAccess(false); }
  },60000);
}

init().catch((error) => message(error.message, true));

if ($('supervisedCutting') && document.body?.dataset.laserControl === 'disabled') { $('supervisedCutting').checked=false; $('supervisedCutting').disabled=true; }
