const API = window.K2_API_BASE || "/api";
const POLL_MS = window.K2_POLL_INTERVAL_MS || 900;

let jobId = localStorage.getItem("k2_job_id");
// Which flow's own stages the sidebar is allowed to show, or null for the
// unrestricted master view (dashboard / new batch / resuming a job from the
// job history list). Set to 2 or 3 only when a job was just adopted through
// the Flow 2 or Flow 3 standalone page, so Naresh/Haider only ever see their
// own flow's step(s) -- not the other flows' stage names or a stage count
// that hints at work beyond their own page. Display-only: the full status
// JSON is still fetched underneath, this just narrows what gets rendered.
let viewScopeFlow = Number(localStorage.getItem("k2_view_scope")) || null;

function setViewScope(flowNum) {
  viewScopeFlow = flowNum || null;
  if (viewScopeFlow) localStorage.setItem("k2_view_scope", String(viewScopeFlow));
  else localStorage.removeItem("k2_view_scope");
}
let pendingEdits = {}; // row_key -> { field: value }
let savedDraftEdits = {}; // server-persisted draft values, keyed like pendingEdits
let originalManualValues = {}; // only the rows currently on screen
let editHistory = [];
let activeManualPage = 1;
let knownHistoryCount = null; // null = not yet synced with the server
let isBusy = false; // hard lock: guarantees no duplicate in-flight requests
let forceCheckNow = false; // set by the overlay's "Check now" button

const stageListEl = document.getElementById("stageList");
const stageMeterEl = document.getElementById("stageMeter");
const progressPanelEl = document.getElementById("progressPanel");
const stageListLabelEl = document.getElementById("stageListLabel");
const mainCardEl = document.getElementById("mainCard");
const toastEl = document.getElementById("toast");
const busyOverlayEl = document.getElementById("busyOverlay");
const busyTextEl = document.getElementById("busyText");
const progressRingFillEl = document.getElementById("progressRingFill");
const busyPercentEl = document.getElementById("busyPercent");
const progressLabelEl = document.getElementById("progressLabel");
const progressPctEl = document.getElementById("progressPct");
const checkNowBtnEl = document.getElementById("checkNowBtn");
const workspaceStateEl = document.getElementById("workspaceState");
const goToDashboardBtnEl = document.getElementById("goToDashboardBtn");
const rollbackJobBtnEl = document.getElementById("rollbackJobBtn");
const layoutEl = document.querySelector(".layout");
const sidebarToggleEl = document.getElementById("sidebarToggle");
const sidebarBrandToggleEl = document.getElementById("sidebarBrandToggle");

const ICON_PATHS = {
  fileUp: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M12 18v-6"/><path d="m9 15 3-3 3 3"/>',
  listChecks: '<path d="m3 17 2 2 4-4"/><path d="M3 7l2 2 4-4"/><path d="M13 6h8"/><path d="M13 12h8"/><path d="M13 18h8"/>',
  copy: '<rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
  key: '<circle cx="8" cy="15" r="4"/><path d="M10.85 12.15 19 4"/><path d="m18 5 2 2"/><path d="m15 8 2 2"/>',
  pencil: '<path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/>',
  help: '<circle cx="12" cy="12" r="10"/><path d="M9.1 9a3 3 0 1 1 5.1 2.1c-1.3 1.2-2.2 1.6-2.2 3.4"/><path d="M12 17h.01"/>',
  rotate: '<path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 3v6h6"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>',
  play: '<path d="m5 3 14 9-14 9z"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m17 8-5-5-5 5"/><path d="M12 3v12"/>',
  check: '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><path d="m9 11 3 3L22 4"/>',
  shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
  layers: '<path d="m12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z"/><path d="m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65"/><path d="m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65"/>',
  database: '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14c0 1.66 4.03 3 9 3s9-1.34 9-3V5"/><path d="M3 12c0 1.66 4.03 3 9 3s9-1.34 9-3"/>',
  filter: '<path d="M3 4h18l-7 8v6l-4 2v-8Z"/>',
  userCheck: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="m16 11 2 2 4-4"/>',
  idCard: '<rect width="18" height="14" x="3" y="5" rx="2"/><circle cx="9" cy="12" r="2"/><path d="M15 10h2"/><path d="M15 14h2"/><path d="M6 17c.8-1.2 1.8-2 3-2s2.2.8 3 2"/>',
  mapPin: '<path d="M20 10c0 5-8 12-8 12S4 15 4 10a8 8 0 1 1 16 0Z"/><circle cx="12" cy="10" r="3"/>',
  smartphone: '<rect width="14" height="20" x="5" y="2" rx="2" ry="2"/><path d="M12 18h.01"/>',
  puzzle: '<path d="M15.4 5a2.5 2.5 0 1 0-4.8 0H5v5.4a2.5 2.5 0 1 0 0 4.8V21h5.6a2.5 2.5 0 1 1 4.8 0H21v-5.6a2.5 2.5 0 1 0 0-4.8V5Z"/>',
  home: '<path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/><path d="M9 22V12h6v10"/>',
  mail: '<rect width="20" height="16" x="2" y="4" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/>',
};

const DETAIL_ICON_ASSETS = {
  upload: "assets/k2-details/upload-document.png",
  export: "assets/k2-details/export-download.png",
  source: "assets/k2-details/source-database.png",
  pipeline: "assets/k2-details/pipeline-sync.png",
  rollback: "assets/k2-details/rollback-scope.png",
  cleanup: "assets/k2-details/cleanup-filter.png",
  identity: "assets/k2-details/identity-check.png",
  customer: "assets/k2-details/customer-record.png",
};

const STAGE_SIDEBAR_ICONS = {
  clean: "filter",
  replace: "database",
  reset_cms: "rotate",
  name_validate: "userCheck",
  id_dob_validate: "idCard",
  address_fix: "mapPin",
  mobile_fill: "smartphone",
  flow1_dispatch: "mail",
  flow2_dispatch: "puzzle",
  final_id_check: "shield",
};

function iconMarkup(name, className = "ui-icon") {
  return `<svg class="${className}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICON_PATHS[name]}</svg>`;
}

function detailIconMarkup(name, className = "detail-icon") {
  return `<img class="${className}" src="${DETAIL_ICON_ASSETS[name]}" alt="" aria-hidden="true" />`;
}

goToDashboardBtnEl.innerHTML = `${iconMarkup("home")}<span>Dashboard</span>`;
rollbackJobBtnEl.innerHTML = `${detailIconMarkup("rollback", "action-detail-icon")}<span>Rollback</span>`;

checkNowBtnEl.onclick = () => { forceCheckNow = true; };

function setSidebarCollapsed(collapsed) {
  layoutEl.classList.toggle("sidebar-collapsed", collapsed);
  const label = collapsed ? "Expand pipeline sidebar" : "Collapse pipeline sidebar";
  [sidebarToggleEl, sidebarBrandToggleEl].forEach((control) => {
    control.setAttribute("aria-expanded", String(!collapsed));
    control.setAttribute("aria-label", label);
    control.setAttribute("title", label);
  });
  localStorage.setItem("k2_sidebar_collapsed", String(collapsed));
}

// Hides the pipeline progress meter + "Pipeline stages" list header while no
// job is loaded (dashboard / start-new-batch screens), instead of showing an
// empty "0 / 0 stages" placeholder.
function setSidebarJobChrome(visible) {
  progressPanelEl.hidden = !visible;
  stageListLabelEl.hidden = !visible;
  if (!visible) {
    stageListEl.innerHTML = "";
    stageMeterEl.innerHTML = "";
  }
}

function setJobContextActions(status) {
  const hasJob = Boolean(jobId && status);
  // Both buttons are meaningless without an active job -- there's nothing to
  // jump back to or roll back on the dashboard/new-batch/flow-picker screens,
  // so hide them there instead of showing a permanently-disabled pair.
  goToDashboardBtnEl.hidden = !hasJob;
  // Rollback hidden in the GUI for now -- re-enable by restoring the line
  // below (`rollbackJobBtnEl.hidden = !hasJob;`) in place of the next one.
  // rollbackJobBtnEl.hidden = !hasJob;
  rollbackJobBtnEl.hidden = true;
  goToDashboardBtnEl.disabled = false;
  rollbackJobBtnEl.disabled = !status?.rollback_available;
  rollbackJobBtnEl.title = status?.rollback_available
    ? `Restore ${status.rollback_label || "the latest checkpoint"}`
    : "No rollback checkpoint is available";
}

function collapseSidebarForEditing() {
  if (window.matchMedia("(min-width: 861px)").matches && !layoutEl.classList.contains("sidebar-collapsed")) {
    setSidebarCollapsed(true);
  }
}

const sidebarWasCollapsed = localStorage.getItem("k2_sidebar_collapsed") === "true";
if (sidebarWasCollapsed && window.matchMedia("(min-width: 861px)").matches) {
  setSidebarCollapsed(true);
}
sidebarToggleEl.onclick = () => setSidebarCollapsed(!layoutEl.classList.contains("sidebar-collapsed"));
sidebarBrandToggleEl.onclick = () => {
  if (window.matchMedia("(min-width: 861px)").matches) {
    setSidebarCollapsed(!layoutEl.classList.contains("sidebar-collapsed"));
  }
};

goToDashboardBtnEl.onclick = () => {
  if (!jobId || isBusy) return;
  setViewScope(null);
  renderDashboard();
};

rollbackJobBtnEl.onclick = () => {
  if (!jobId || isBusy || rollbackJobBtnEl.disabled) return;
  const label = rollbackJobBtnEl.title.replace(/^Restore\s+/, "");
  if (!window.confirm(`Rollback to ${label}? Changes made after that point will be removed.`)) return;
  runAction(`/jobs/${jobId}/rollback`, { method: "POST" }, "Restoring checkpoint...");
};

function rewindToStage(target) {
  if (!jobId || isBusy || !target?.id) return;
  const title = target.stage_title || target.label || "the selected stage";
  if (!window.confirm(`Return to ${title}? All work completed after this stage will be removed.`)) return;
  runAction(
    `/jobs/${jobId}/rollback/${encodeURIComponent(target.id)}`,
    { method: "POST" },
    `Returning to ${title}...`,
  );
}

// Matches r="50" on the SVG circle in index.html (2 * pi * 50).
const RING_CIRCUMFERENCE = 314.159;

// ---------- small UI helpers ----------

function toast(msg, type = "info") {
  toastEl.textContent = msg;
  toastEl.className = `toast-visible toast-${type}`;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { toastEl.className = ""; }, 3200);
}

// Swaps main card content and replays the enter animation, instead of a raw
// innerHTML swap, so every stage change reads as a deliberate transition.
function setCard(html) {
  mainCardEl.classList.remove("card-enter");
  void mainCardEl.offsetWidth; // force reflow so the animation restarts
  mainCardEl.innerHTML = html;
  mainCardEl.classList.add("card-enter");
}

function filePickerMarkup(inputId, zoneId, fileNameId, prompt, note) {
  return `
    <label class="dropzone" id="${zoneId}" for="${inputId}">
      <input class="file-input" type="file" id="${inputId}" accept=".csv,.xlsx,.xls" />
      <span class="dropzone-content">
        <span class="file-picker-icon">${iconMarkup("fileUp")}</span>
        <span class="file-picker-trigger">${prompt}</span>
        <span class="file-picker-note">${note}</span>
        <span class="file-name" id="${fileNameId}">No file selected</span>
      </span>
    </label>
  `;
}

function formatFileSize(bytes) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function wireFilePicker(inputId, zoneId, fileNameId, onFileSelected) {
  const input = document.getElementById(inputId);
  const zone = document.getElementById(zoneId);
  const fileName = document.getElementById(fileNameId);

  const reflectFile = () => {
    const file = input.files[0];
    const hideWhenEmpty = fileName.dataset.emptyHidden === "true";
    fileName.textContent = file ? `${file.name} - ${formatFileSize(file.size)}` : "No file selected";
    fileName.hidden = hideWhenEmpty && !file;
    zone.classList.toggle("file-selected", Boolean(file));
    if (file && onFileSelected) onFileSelected(file);
  };

  input.addEventListener("change", reflectFile);
  ["dragenter", "dragover"].forEach((eventName) => {
    zone.addEventListener(eventName, (event) => {
      event.preventDefault();
      zone.classList.add("drag-over");
    });
  });
  ["dragleave", "drop"].forEach((eventName) => {
    zone.addEventListener(eventName, (event) => {
      event.preventDefault();
      zone.classList.remove("drag-over");
    });
  });
  zone.addEventListener("drop", (event) => {
    if (!event.dataTransfer.files.length) return;
    input.files = event.dataTransfer.files;
    reflectFile();
  });
}

// Belt-and-suspenders alongside isBusy + the overlay: disable every control
// on screen the instant an action starts, so a click that slips through
// before the overlay paints still hits a disabled element.
function lockAllControls(lock) {
  document.querySelectorAll("button, input").forEach((el) => { el.disabled = lock; });
}

// Ring starts as an indeterminate spinning arc (no real percent yet) and
// switches to a determinate fill the moment GET /progress reports one.
function setRingIndeterminate() {
  progressRingFillEl.classList.add("indeterminate");
  busyPercentEl.textContent = "";
}

function setRingProgress(pct) {
  progressRingFillEl.classList.remove("indeterminate");
  const clamped = Math.max(0, Math.min(100, pct));
  progressRingFillEl.style.strokeDashoffset = String(RING_CIRCUMFERENCE * (1 - clamped / 100));
  busyPercentEl.textContent = `${Math.round(clamped)}%`;
}

function setBusy(text) {
  busyTextEl.textContent = text || "Processing…";
  setRingIndeterminate();
  forceCheckNow = false;
  busyOverlayEl.classList.remove("hidden");
}

function clearBusy() {
  busyOverlayEl.classList.add("hidden");
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function api(path, opts = {}) {
  let res;
  try {
    res = await fetch(API + path, opts);
  } catch (e) {
    throw new Error("Unable to connect to the backend. Check that the K2 server is running, then retry.");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    const detail = body.detail || res.statusText || "The request could not be completed.";
    if (res.status === 404 && String(detail).toLowerCase().includes("job")) {
      throw new Error("This job has already been deleted or is no longer available.");
    }
    throw new Error(detail);
  }
  return res.json();
}

// Same as api(), but retries transient failures (dropped LAN connection, a
// request that raced a background write, etc.) a few times before giving
// up, so one flaky request doesn't strand the UI on stale content.
async function apiRetrying(path, opts = {}, retries = 3, delayMs = 500) {
  let lastErr;
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      return await api(path, opts);
    } catch (e) {
      lastErr = e;
      if (attempt < retries) await sleep(delayMs);
    }
  }
  throw lastErr;
}

// ---------- real backend progress polling ----------
// Requires GET /jobs/{id}/progress -- see BACKEND_REQUIREMENTS.md.
// Polls while status === "processing" and updates the busy overlay live.
//
// This loop must NEVER be the only way out of the busy overlay: if
// /progress ever reports "processing" indefinitely (a stale/misbehaving
// backend, a job whose progress tracker didn't get updated, etc.), this
// stops trusting it after MAX_WAIT_MS and falls through to refresh(), which
// reads the job's real state directly from /jobs/{id} and /current instead
// -- that's the actual source of truth, /progress is just a status hint.
// The "Check now" button in the overlay sets forceCheckNow to bail out
// immediately without waiting for the timeout.
const MAX_POLL_WAIT_MS = 5 * 60 * 1000;

async function pollProgressUntilIdle(fallbackLabel) {
  const startedAt = Date.now();
  let consecutiveFailures = 0;
  while (true) {
    if (forceCheckNow || Date.now() - startedAt > MAX_POLL_WAIT_MS) break;

    let progress;
    try {
      progress = await api(`/jobs/${jobId}/progress`);
      consecutiveFailures = 0;
    } catch (e) {
      consecutiveFailures++;
      if (consecutiveFailures >= 5) break;
      await sleep(POLL_MS);
      continue;
    }
    if (progress && progress.status === "error") {
      throw new Error(progress.message || "The backend stopped while processing this stage.");
    }
    if (!progress || progress.status !== "processing") break;

    const label = progress.current_step_name
      ? `Step ${progress.current_step_index}/${progress.total_steps} — ${progress.current_step_name}`
      : fallbackLabel;
    busyTextEl.textContent = label;
    setRingProgress(progress.percent ?? 0);

    await sleep(POLL_MS);
  }
}

// Shared wrapper for every action once a job exists: locks the UI, fires the
// request, polls real progress, then refreshes. The isBusy check is the
// first thing that runs, synchronously, so a second click is always a no-op
// regardless of how fast the overlay paints.
async function runAction(path, opts, busyLabel) {
  if (isBusy) return;
  isBusy = true;
  lockAllControls(true);
  setBusy(busyLabel);
  try {
    await api(path, opts);
    await pollProgressUntilIdle(busyLabel);
    clearBusy();
    await refresh(true);
  } catch (e) {
    clearBusy();
    renderProcessingError(e);
  } finally {
    isBusy = false;
    lockAllControls(false);
  }
}

function renderProcessingError(e) {
  setCard(`
    <div class="stage-intro">
      <span class="stage-kicker">Stage needs attention</span>
      <h2>Processing could not continue</h2>
      <p class="muted">${escapeHtml(e.message || "The current stage did not complete.")}</p>
    </div>
    <div class="recovery-panel">
      <p class="muted">Return to the current stage to correct the file or data, then retry. Include job ${escapeHtml(jobId || "") } when contacting the K2 administrator.</p>
      <div class="row-actions">
        <button id="retryCurrentStageBtn">Retry current stage</button>
        <button class="secondary" id="returnCurrentStageBtn">Return to current stage</button>
        <button class="secondary" id="downloadErrorAuditBtn">Download error report</button>
        <button class="secondary" id="restartJobBtn">Restart job</button>
      </div>
    </div>
  `);
  document.getElementById("retryCurrentStageBtn").onclick = () => refresh(false);
  document.getElementById("returnCurrentStageBtn").onclick = () => refresh(false);
  document.getElementById("downloadErrorAuditBtn").onclick = () => {
    window.location.href = `${API}/jobs/${jobId}/audit/download`;
  };
  document.getElementById("restartJobBtn").onclick = () => {
    localStorage.removeItem("k2_job_id");
    jobId = null;
    knownHistoryCount = null;
    setViewScope(null);
    renderDashboard();
  };
}

// ---------- progress bar + stage list ----------

// Stages the sidebar is allowed to show right now: every stage when no scope
// is set (the master dashboard/wizard view), or only the stages owned by
// `viewScopeFlow` when a job was adopted through the Flow 2/3 page. Keeps
// each entry's original index into status.stages so "is this the current
// stage" and rollback-target lookups still line up.
function visibleStageEntries(status) {
  return status.stages
    .map((stage, index) => ({ stage, index }))
    .filter(({ stage }) => !viewScopeFlow || stage.flow === viewScopeFlow);
}

function renderStageMeterAndHeader(status) {
  setSidebarJobChrome(true);
  const entries = visibleStageEntries(status);
  const total = entries.length;
  const done = entries.filter(({ stage }) => stage.status === "done").length;
  const pct = total ? Math.round((done / total) * 100) : 0;
  const current = status.stages[status.stage_index];
  // If the job has moved on to a different flow than the one this screen is
  // scoped to, don't let the header show that other flow's stage title --
  // fall back to the last stage this scope owns instead.
  const headerStage = !viewScopeFlow || (current && current.flow === viewScopeFlow)
    ? current
    : entries[entries.length - 1]?.stage;
  progressLabelEl.textContent = `${done} / ${total} stages`;
  progressPctEl.textContent = `${pct}%`;
  workspaceStateEl.textContent = headerStage ? headerStage.title : "Final output";
  setJobContextActions(status);

  stageMeterEl.innerHTML = "";
  entries.forEach(({ stage, index }) => {
    const seg = document.createElement("div");
    seg.className = "meter-seg";
    if (stage.status === "done") seg.classList.add("done");
    else if (index === status.stage_index) seg.classList.add("current");
    stageMeterEl.appendChild(seg);
  });
}

function renderStageList(status) {
  renderStageMeterAndHeader(status);
  stageListEl.innerHTML = "";
  const targetsByStage = new Map((status.rollback_targets || []).map((target) => [target.stage_index, target]));
  const entries = visibleStageEntries(status);
  let lastGroup = null;
  entries.forEach(({ stage, index }) => {
    if (!viewScopeFlow && stage.flow !== lastGroup) {
      lastGroup = stage.flow;
      const header = document.createElement("li");
      header.className = "stage-group-header";
      header.textContent = `Flow ${stage.flow}`;
      stageListEl.appendChild(header);
    }

    const li = document.createElement("li");
    let cls = "pending";
    if (stage.status === "done") cls = "done";
    if (index === status.stage_index) cls = "current";
    li.className = cls;
    const stageState = index === status.stage_index ? "Current stage" : stage.status === "done" ? "Completed" : "Queued";
    li.title = `${stage.title}: ${stageState}`;
    li.setAttribute("aria-label", `${stage.title}: ${stageState}`);

    const dot = document.createElement("span");
    dot.className = "dot";
    if (index === status.stage_index && stage.status !== "done") {
      dot.innerHTML = '<span class="mini-spinner"></span>';
    }
    li.appendChild(dot);

    const stageIcon = document.createElement("span");
    stageIcon.className = "stage-icon";
    stageIcon.innerHTML = iconMarkup(STAGE_SIDEBAR_ICONS[stage.id] || "layers", "stage-sidebar-icon");
    li.appendChild(stageIcon);

    const stageText = document.createElement("span");
    stageText.className = "stage-text";
    const label = document.createElement("span");
    label.className = "stage-name";
    label.textContent = stage.title;
    const state = document.createElement("span");
    state.className = "stage-status";
    state.textContent = stageState;
    stageText.append(label, state);
    li.appendChild(stageText);

    // Rollback hidden in the GUI for now -- re-enable by uncommenting this
    // block (the sidebar's per-stage "Reopen" rewind entry point).
    // const rewindTarget = targetsByStage.get(index);
    // if (rewindTarget && index < status.stage_index) {
    //   li.classList.add("rewind-target");
    //   const rewindButton = document.createElement("button");
    //   rewindButton.className = "stage-rewind";
    //   rewindButton.type = "button";
    //   rewindButton.textContent = "Reopen";
    //   rewindButton.title = `Return to ${stage.title} and remove all later work`;
    //   rewindButton.setAttribute("aria-label", rewindButton.title);
    //   rewindButton.disabled = isBusy;
    //   rewindButton.onclick = () => rewindToStage(rewindTarget);
    //   li.appendChild(rewindButton);
    // }

    stageListEl.appendChild(li);
  });
}

// Short animated recap of stages that auto-completed server-side between
// manual gates, so the jump from one checkpoint to the next is visible.
function playTransition(newItems) {
  return new Promise((resolve) => {
    if (!newItems.length) {
      resolve();
      return;
    }
    const itemsHtml = newItems
      .map(
        (h, i) => `
        <div class="transition-item" style="animation-delay:${i * 180}ms">
          <span class="dot"></span>
          <span><strong>${escapeHtml(h.title)}</strong> <span class="muted">— ${escapeHtml(h.summary)}</span></span>
        </div>`
      )
      .join("");
    setCard(`
      <h2><span class="mini-spinner"></span>Running pipeline…</h2>
      <div class="transition-list">${itemsHtml}</div>
    `);
    setTimeout(resolve, newItems.length * 180 + 450);
  });
}

// The single place that decides what's on screen after any action settles.
// Both server reads are retried a few times (apiRetrying) since a dropped
// LAN request here previously left the page silently showing the OLD stage
// until a manual browser refresh -- now it self-heals, and if it still can't
// reach the server it shows an explicit Retry card instead of doing nothing.
async function refresh(animate) {
  if (!jobId) {
    knownHistoryCount = null;
    renderDashboard();
    return;
  }

  let status;
  try {
    status = await apiRetrying(`/jobs/${jobId}`);
  } catch (e) {
    renderJobRecovery(e);
    return;
  }

  const firstLoad = knownHistoryCount === null;
  const newItems = firstLoad ? [] : status.history.slice(knownHistoryCount);
  knownHistoryCount = status.history.length;
  renderStageList(status);

  if (!firstLoad && animate && newItems.length) {
    await playTransition(newItems);
    renderStageList(status);
  }

  try {
    const current = await apiRetrying(`/jobs/${jobId}/current`);
    renderStage(status, current);
  } catch (e) {
    renderSettleError(e);
  }
}

function renderJobRecovery(e) {
  stageListEl.innerHTML = "";
  stageMeterEl.innerHTML = "";
  progressLabelEl.textContent = "Saved job";
  progressPctEl.textContent = "";
  workspaceStateEl.textContent = "Connection needed";
  setJobContextActions(null);
  setCard(`
    <div class="stage-intro">
      <span class="stage-kicker">Saved job unavailable</span>
      <h2>Unable to connect to the backend</h2>
      <p class="muted">${escapeHtml(e.message || "The saved job could not be loaded.")}</p>
    </div>
    <div class="recovery-panel">
      <p class="muted">Your saved job reference has been kept in this browser.</p>
      <div class="row-actions">
        <button id="retryJobBtn">Retry</button>
        <button class="secondary" id="forgetJobBtn">Forget this job</button>
      </div>
    </div>
  `);
  document.getElementById("retryJobBtn").onclick = () => refresh(false);
  document.getElementById("forgetJobBtn").onclick = () => {
    localStorage.removeItem("k2_job_id");
    jobId = null;
    knownHistoryCount = null;
    setViewScope(null);
    renderDashboard();
  };
}

function renderSettleError(e) {
  setCard(`
    <h2>Couldn't load the next step</h2>
    <p class="muted">${escapeHtml(e.message || "The server didn't respond.")}</p>
    <button id="retryRefreshBtn">Retry</button>
  `);
  document.getElementById("retryRefreshBtn").onclick = () => refresh(false);
}

// ---------- stage screens ----------

// Dashboard: pure job history, no upload/data-init controls -- starting or
// importing a job lives on its own page (renderNewBatch), one click away.
function renderDashboard() {
  setSidebarJobChrome(false);
  workspaceStateEl.textContent = "Ready";
  setJobContextActions(null);
  setCard(`
    <div class="stage-intro upload-stage-intro">
      <div class="stage-title-row">
        <h2>Dashboard</h2>
      </div>
      <p class="muted">Resume a job in progress, or start a new batch.</p>
    </div>
    <div class="row-actions">
      <button id="newBatchBtn">${iconMarkup("play")}<span>Start new batch</span></button>
      <button class="secondary" id="flow2PageBtn" type="button">${iconMarkup("mail")}<span>Flow 2 (Naresh)</span></button>
      <button class="secondary" id="flow3PageBtn" type="button">${iconMarkup("puzzle")}<span>Flow 3 (Haider)</span></button>
    </div>
    <div class="job-history">
      <div class="job-history-filters">
        <input type="search" id="jobSearchInput" placeholder="Search job ID or filename…" aria-label="Search jobs" />
        <select id="jobStatusFilter" aria-label="Filter by status">
          <option value="">All statuses</option>
          <option value="processing">Processing</option>
          <option value="in_progress">In progress</option>
          <option value="done">Done</option>
        </select>
        <select id="jobFlowFilter" aria-label="Filter by flow">
          <option value="">All flows</option>
          <option value="1">Flow 1</option>
          <option value="2">Flow 2</option>
          <option value="3">Flow 3</option>
        </select>
      </div>
      <div class="table-wrap job-history-wrap" id="jobHistoryWrap">
        <p class="muted"><span class="mini-spinner"></span>Loading job history…</p>
      </div>
    </div>
  `);

  document.getElementById("newBatchBtn").onclick = () => renderNewBatch();
  document.getElementById("flow2PageBtn").onclick = () => renderFlow2Page();
  document.getElementById("flow3PageBtn").onclick = () => renderFlow3Page();
  document.getElementById("jobSearchInput").oninput = () => renderJobHistoryTable();
  document.getElementById("jobStatusFilter").onchange = () => renderJobHistoryTable();
  document.getElementById("jobFlowFilter").onchange = () => renderJobHistoryTable();

  loadJobHistory();
}

// The data-init / "start a new batch" flow -- its own page, reached from the
// dashboard's "Start new batch" button rather than shown inline there.
function renderNewBatch() {
  setSidebarJobChrome(false);
  workspaceStateEl.textContent = "Ready";
  setJobContextActions(null);
  setCard(`
    <div class="stage-intro upload-stage-intro">
      <button class="secondary quiet-action back-to-dashboard" id="backToDashboardBtn" type="button">&larr; Back to dashboard</button>
      <h2>Start data preparation</h2>
    </div>
    <div class="upload-simple-layout">
      <p class="muted">Upload the raw K2 customer export (CSV, XLSX, or XLS) to start the pipeline. It's checked for the required columns as soon as you pick a file.</p>
      ${filePickerMarkup("fileInput", "rawFileZone", "rawFileName", "Select raw file", "CSV, XLSX, or XLS")}
    </div>
    <div class="upload-preview" id="rawUploadPreview" aria-live="polite"></div>
    <div class="row-actions">
      <button id="startBtn">${iconMarkup("play")}<span>Start batch</span></button>
      <button class="secondary" id="backToDashboardBtn2" type="button">Cancel</button>
    </div>
  `);

  document.getElementById("backToDashboardBtn").onclick = () => renderDashboard();
  document.getElementById("backToDashboardBtn2").onclick = () => renderDashboard();
  wireFilePicker("fileInput", "rawFileZone", "rawFileName", previewRawUpload);

  document.getElementById("startBtn").onclick = async () => {
    if (isBusy) return;
    const file = document.getElementById("fileInput").files[0];
    if (!file) { toast("Choose a file first", "error"); return; }

    const fd = new FormData();
    fd.append("file", file);

    isBusy = true;
    lockAllControls(true);
    setBusy("Uploading file…");
    try {
      const status = await api("/jobs", { method: "POST", body: fd });
      jobId = status.job_id;
      knownHistoryCount = 0;
      localStorage.setItem("k2_job_id", jobId);
      setViewScope(null);
      await pollProgressUntilIdle("Running automated steps…");
      clearBusy();
      toast("Job created");
      await refresh(true);
    } catch (e) {
      clearBusy();
      toast(e.message, "error");
    } finally {
      isBusy = false;
      lockAllControls(false);
    }
  };
}

// Polls a specific job's progress without touching the global `jobId` --
// used by the Flow 2/3 intake pages, which act on a picked job that may be
// completely different from (or absent from) the currently loaded job, so
// they must not disturb it or its localStorage entry while the upload is
// still processing.
async function pollJobProgress(targetJobId) {
  const startedAt = Date.now();
  while (Date.now() - startedAt <= MAX_POLL_WAIT_MS) {
    let progress;
    try {
      progress = await api(`/jobs/${targetJobId}/progress`);
    } catch (e) {
      return;
    }
    if (progress && progress.status === "error") {
      throw new Error(progress.message || "The backend stopped while applying this response.");
    }
    if (!progress || progress.status !== "processing") return;
    await sleep(POLL_MS);
  }
}

async function fetchFlowJobs(stageId) {
  const data = await apiRetrying(`/jobs?stage_id=${encodeURIComponent(stageId)}`);
  return data.jobs || [];
}

// Shared shell for the Flow 2 ("Naresh intake") and Flow 3 ("Haider intake")
// pages: standalone pages (not part of the per-job wizard) with a job picker
// filtered to whichever checkpoint that flow reads from, since the person
// uploading a response may not be the one who created the job. Once the
// upload is applied, the job is adopted as the active job (same as clicking
// it from the dashboard) and handed to the normal wizard via refresh() --
// Flow 2's dispatch screen (renderFlowWaitStage) and Flow 3's confirm/done
// screens (renderConfirmStage/renderDone) are the exact same shared code
// every job uses, not a separate copy living on this page.
function renderFlowIntakePage(config) {
  setSidebarJobChrome(false);
  workspaceStateEl.textContent = "Ready";
  setJobContextActions(null);
  renderFlowUploadStep(config);
}

async function renderFlowUploadStep(config) {
  setCard(`
    <div class="stage-intro upload-stage-intro">
      <button class="secondary quiet-action back-to-dashboard" id="backToDashboardBtn" type="button">&larr; Back to dashboard</button>
      <h2>${escapeHtml(config.title)}</h2>
      <p class="muted">${escapeHtml(config.description)}</p>
    </div>
    <div id="flowIntakeBody"><p class="muted"><span class="mini-spinner"></span>Loading eligible jobs…</p></div>
  `);
  document.getElementById("backToDashboardBtn").onclick = () => renderDashboard();

  const body = document.getElementById("flowIntakeBody");
  let jobs;
  try {
    jobs = await fetchFlowJobs(config.pickerStageId);
  } catch (e) {
    body.innerHTML = `<p class="muted">Could not load jobs: ${escapeHtml(e.message)}</p>`;
    return;
  }
  if (!document.getElementById("flowIntakeBody")) return;
  if (!jobs.length) {
    body.innerHTML = `<p class="muted">No jobs are currently waiting at this checkpoint.</p>`;
    return;
  }

  const optionsHtml = jobs.map((j) => `<option value="${escapeHtml(j.job_id)}">${escapeHtml(j.job_id)} — ${escapeHtml(j.filename || "")} (${j.row_count != null ? Number(j.row_count).toLocaleString() : "?"} rows)</option>`).join("");
  body.innerHTML = `
    <label class="flow-job-picker">
      <span>Job</span>
      <select id="flowJobSelect">${optionsHtml}</select>
    </label>
    ${config.filePickersHtml}
    <div class="row-actions">
      <button id="flowSubmitBtn" type="button">${iconMarkup("upload")}<span>${escapeHtml(config.submitLabel)}</span></button>
    </div>
  `;
  config.wireFilePickers();

  document.getElementById("flowSubmitBtn").onclick = async () => {
    if (isBusy) return;
    const selectedJobId = document.getElementById("flowJobSelect").value;
    let fd;
    try {
      fd = config.buildFormData();
    } catch (e) {
      toast(e.message, "error");
      return;
    }
    isBusy = true;
    lockAllControls(true);
    renderFlowProcessingStep(config);
    try {
      await api(`/jobs/${selectedJobId}${config.path}`, { method: "POST", body: fd });
      await pollJobProgress(selectedJobId);
      // Adopt this job as the active one (same as clicking it on the
      // dashboard) and let the normal wizard render whatever comes next --
      // no custom completion screen to maintain here.
      jobId = selectedJobId;
      localStorage.setItem("k2_job_id", jobId);
      knownHistoryCount = null;
      setViewScope(config.scopeFlow);
      isBusy = false;
      lockAllControls(false);
      await refresh(true);
    } catch (e) {
      isBusy = false;
      lockAllControls(false);
      toast(e.message, "error");
      renderFlowUploadStep(config);
    }
  };
}

function renderFlowProcessingStep(config) {
  setCard(`
    <div class="stage-intro">
      <span class="stage-kicker">Processing</span>
      <h2>${escapeHtml(config.title)}</h2>
      <p class="muted"><span class="mini-spinner"></span> ${escapeHtml(config.processingLabel)}</p>
    </div>
  `);
}

function renderFlow2Page() {
  renderFlowIntakePage({
    title: "Flow 2 — Naresh's response",
    description: "Upload Naresh's completed IDs + DOB workbook (2 sheets). Only jobs currently parked at Flow 1 Dispatch are listed.",
    pickerStageId: "flow1_dispatch",
    scopeFlow: 2,
    path: "/flow2/naresh-response",
    submitLabel: "Upload and apply",
    processingLabel: "Applying Naresh's response — merging IDs and DOBs, rechecking…",
    filePickersHtml: filePickerMarkup("flow2FileInput", "flow2FileZone", "flow2FileName", "Select Naresh's response file", "ID Corrections + DOB Mistakes sheets"),
    wireFilePickers: () => wireFilePicker("flow2FileInput", "flow2FileZone", "flow2FileName"),
    buildFormData: () => {
      const file = document.getElementById("flow2FileInput").files[0];
      if (!file) throw new Error("Choose Naresh's response file first");
      const fd = new FormData();
      fd.append("file", file);
      return fd;
    },
  });
}

function renderFlow3Page() {
  renderFlowIntakePage({
    title: "Flow 3 — Haider's response",
    description: "Upload both of Haider's completed files together. Only jobs currently parked at Flow 2 Dispatch are listed.",
    pickerStageId: "flow2_dispatch",
    scopeFlow: 3,
    path: "/flow3/haider-response",
    submitLabel: "Upload and apply",
    processingLabel: "Applying Haider's response — merging corrections…",
    filePickersHtml: `
      ${filePickerMarkup("flow3CorrectionsFileInput", "flow3CorrectionsFileZone", "flow3CorrectionsFileName", "Select Haider's corrections file", "Name/Mobile/CMS workbook")}
      ${filePickerMarkup("flow3IdsFileInput", "flow3IdsFileZone", "flow3IdsFileName", "Select Haider's IDs/DOB response file", "Optional — only if IDs or DOBs are still invalid")}
    `,
    wireFilePickers: () => {
      wireFilePicker("flow3CorrectionsFileInput", "flow3CorrectionsFileZone", "flow3CorrectionsFileName");
      wireFilePicker("flow3IdsFileInput", "flow3IdsFileZone", "flow3IdsFileName");
    },
    // ids file is optional: if Naresh already resolved every invalid ID and
    // DOB, there's nothing left for Haider's second-pass file to fix, so it
    // can be left unselected -- the backend enforces whether that's actually true.
    buildFormData: () => {
      const correctionsFile = document.getElementById("flow3CorrectionsFileInput").files[0];
      const idsFile = document.getElementById("flow3IdsFileInput").files[0];
      if (!correctionsFile) throw new Error("Choose Haider's corrections file first");
      const fd = new FormData();
      fd.append("corrections_file", correctionsFile);
      if (idsFile) fd.append("ids_file", idsFile);
      return fd;
    },
  });
}

function resumeJob(jobIdToResume) {
  if (isBusy || !jobIdToResume) return;
  jobId = jobIdToResume;
  localStorage.setItem("k2_job_id", jobId);
  knownHistoryCount = null;
  setViewScope(null);
  refresh(true);
}

function formatJobDateKey(dateKey) {
  const year = dateKey.slice(0, 4), month = dateKey.slice(4, 6), day = dateKey.slice(6, 8);
  const parsed = new Date(`${year}-${month}-${day}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return dateKey;
  return parsed.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function jobStatusPillMarkup(job) {
  if (job.is_processing) return `<span class="data-label data-label-operator-corrected">Processing</span>`;
  if (job.is_done) return `<span class="data-label data-label-source-file-updated">Done</span>`;
  return `<span class="data-label data-label-unverified">${escapeHtml(job.stage_title || "In progress")}</span>`;
}

// A job's own "flow" tag tells you which of the 3 self-contained flows it's
// currently stuck in without having to read/parse the specific stage title --
// e.g. "Flow 2" at a glance, instead of having to know that "Flow 2
// Dispatch" belongs to Flow 2. Blank once a job is fully done -- by then it
// isn't stuck anywhere.
function jobFlowCellMarkup(job) {
  if (job.is_done || !job.flow) return "—";
  return `Flow ${job.flow}`;
}

function jobHistoryRowMarkup(job) {
  const rowCount = job.row_count != null ? Number(job.row_count).toLocaleString() : "—";
  const downloadCell = job.is_done
    ? `<a href="${API}/jobs/${job.job_id}/download" class="job-history-download">Download</a>`
    : "";
  return `<tr class="job-history-row" data-resume-job="${escapeHtml(job.job_id)}" tabindex="0">
    <td class="mono">${escapeHtml(job.job_id)}</td>
    <td title="${escapeHtml(job.filename || "")}">${escapeHtml(job.filename || "")}</td>
    <td>${rowCount}</td>
    <td>${jobFlowCellMarkup(job)}</td>
    <td>${jobStatusPillMarkup(job)}</td>
    <td>${downloadCell}</td>
  </tr>`;
}

let allJobsHistory = []; // full unfiltered list from the server, re-filtered client-side on every keystroke/change

function jobStatusBucket(job) {
  if (job.is_processing) return "processing";
  if (job.is_done) return "done";
  return "in_progress";
}

function filteredJobHistory() {
  const query = (document.getElementById("jobSearchInput")?.value || "").trim().toLowerCase();
  const statusFilter = document.getElementById("jobStatusFilter")?.value || "";
  const flowFilter = document.getElementById("jobFlowFilter")?.value || "";
  return allJobsHistory.filter((job) => {
    if (statusFilter && jobStatusBucket(job) !== statusFilter) return false;
    if (flowFilter && String(job.flow ?? "") !== flowFilter) return false;
    if (query && !`${job.job_id} ${job.filename || ""}`.toLowerCase().includes(query)) return false;
    return true;
  });
}

function renderJobHistoryTable() {
  const wrap = document.getElementById("jobHistoryWrap");
  if (!wrap) return;
  if (!allJobsHistory.length) {
    wrap.innerHTML = `<p class="muted">No previous jobs yet.</p>`;
    return;
  }
  const jobs = filteredJobHistory();
  if (!jobs.length) {
    wrap.innerHTML = `<p class="muted">No jobs match the current search/filters.</p>`;
    return;
  }
  const groups = new Map();
  jobs.forEach((job) => {
    const dateKey = /^\d{8}_/.test(job.job_id) ? job.job_id.slice(0, 8) : "Earlier";
    if (!groups.has(dateKey)) groups.set(dateKey, []);
    groups.get(dateKey).push(job);
  });
  const bodyHtml = [...groups.entries()].map(([dateKey, jobsInGroup]) => {
    const heading = dateKey === "Earlier" ? "Earlier" : formatJobDateKey(dateKey);
    return `<tr class="job-history-group"><td colspan="6">${escapeHtml(heading)}</td></tr>${jobsInGroup.map(jobHistoryRowMarkup).join("")}`;
  }).join("");
  wrap.innerHTML = `
    <table class="job-history-table">
      <thead><tr><th>Job</th><th>File</th><th>Rows</th><th>Flow</th><th>Status</th><th></th></tr></thead>
      <tbody>${bodyHtml}</tbody>
    </table>
  `;
  wrap.querySelectorAll(".job-history-download").forEach((link) => {
    link.addEventListener("click", (event) => event.stopPropagation());
  });
  wrap.querySelectorAll("[data-resume-job]").forEach((row) => {
    row.addEventListener("click", () => resumeJob(row.dataset.resumeJob));
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); resumeJob(row.dataset.resumeJob); }
    });
  });
}

async function loadJobHistory() {
  const wrap = document.getElementById("jobHistoryWrap");
  if (!wrap) return;
  try {
    const data = await apiRetrying("/jobs");
    allJobsHistory = data.jobs || [];
  } catch (e) {
    if (document.getElementById("jobHistoryWrap")) {
      wrap.innerHTML = `<p class="muted">Could not load job history: ${escapeHtml(e.message)}</p>`;
    }
    return;
  }
  if (!document.getElementById("jobHistoryWrap")) return;
  renderJobHistoryTable();
}

async function previewRawUpload(file) {
  const previewEl = document.getElementById("rawUploadPreview");
  if (!previewEl) return;
  const selectedName = file.name;
  previewEl.innerHTML = `<span class="mini-spinner"></span><span>Inspecting file structure...</span>`;
  const form = new FormData();
  form.append("file", file);
  try {
    const preview = await api("/uploads/raw-preview", { method: "POST", body: form });
    if (!document.getElementById("rawUploadPreview") || selectedName !== file.name) return;
    const missing = preview.missing_required_columns || [];
    previewEl.className = `upload-preview ${missing.length ? "preview-invalid" : "preview-valid"}`;
    previewEl.innerHTML = missing.length
      ? `Missing required columns: ${escapeHtml(missing.join(", "))}`
      : `${preview.row_count.toLocaleString()} rows detected — all required columns found.`;
  } catch (e) {
    if (!document.getElementById("rawUploadPreview")) return;
    previewEl.className = "upload-preview preview-invalid";
    previewEl.textContent = e.message;
  }
}

function renderStage(status, current) {
  if (current.type === "upload") return renderUploadStage(current);
  if (current.type === "flow1" || current.type === "flow2") return renderFlowWaitStage(current);
  if (current.type === "confirm") return renderConfirmStage(current);
  if (current.type === "manual_edit") return renderManualEditStage(current);
  pendingEdits = {};
  savedDraftEdits = {};
  if (current.type === "done") return renderDone(current);
  setCard(`<p class="muted"><span class="mini-spinner"></span>Processing…</p>`);
}

function renderUploadStage(current) {
  const guidance = current.guidance || {};
  const overwriteCount = (guidance.overwrite_fields || []).length;
  // "expected_file" already names exactly what this stage wants (e.g.
  // "Historical replacement export") -- reuse it in the picker prompt and
  // processing message too, instead of a generic hardcoded "reference file"
  // that wouldn't describe a future upload stage expecting something else.
  const expectedFile = guidance.expected_file || "file";
  // current.label (the short subtitle rendered above) already names the
  // expected file and its matching key -- this second line only adds
  // whatever that one didn't cover, instead of restating it.
  const description = [
    overwriteCount ? `Updates ${overwriteCount} field(s)` : null,
    guidance.duplicate_handling,
  ].filter(Boolean).join(" — ");
  setCard(`
    <div class="stage-intro">
      <span class="stage-kicker">Data source required</span>
      <h2>${escapeHtml(current.title)}</h2>
      <p class="muted">${escapeHtml(current.label)}</p>
    </div>
    <div class="upload-simple-layout">
      <p class="muted">${escapeHtml(description)}</p>
      ${filePickerMarkup("refFileInput", "referenceFileZone", "referenceFileName", `Select ${expectedFile.toLowerCase()}`, "CSV, XLSX, or XLS")}
    </div>
    <div class="row-actions">
      <button id="uploadBtn">${iconMarkup("upload")}<span>Upload and continue</span></button>
      ${current.skippable ? `<button class="secondary" id="skipUploadBtn" type="button">Skip this step</button>` : ""}
      ${current.api_invoke_planned ? `<button class="secondary" type="button" disabled title="Coming soon: pull this file automatically via API">Invoke via API (coming soon)</button>` : ""}
    </div>
  `);
  wireFilePicker("refFileInput", "referenceFileZone", "referenceFileName");
  document.getElementById("uploadBtn").onclick = () => {
    const file = document.getElementById("refFileInput").files[0];
    if (!file) { toast("Choose a file first", "error"); return; }
    const fd = new FormData();
    fd.append("file", file);
    runAction(`/jobs/${jobId}/upload`, { method: "POST", body: fd }, `Merging ${expectedFile.toLowerCase()}…`);
  };
  if (current.skippable) {
    document.getElementById("skipUploadBtn").onclick = () => {
      if (!window.confirm(`Skip "${current.title}" without uploading a file? Accounts will keep their current values.`)) return;
      runAction(`/jobs/${jobId}/skip`, { method: "POST" }, "Skipping stage…");
    };
  }
}

function renderConfirmStage(current) {
  setCard(`
    <div class="stage-intro">
      <span class="stage-kicker">Confirmation required</span>
      <h2>${escapeHtml(current.title)}</h2>
    </div>
    <div class="decision-panel">
      <div class="decision-info">
        <div class="decision-info-row">
          <span class="guidance-icon guidance-icon-art">${detailIconMarkup("identity", "guidance-detail-icon")}</span>
          <p class="decision-copy">${escapeHtml(current.summary)}</p>
        </div>
      </div>
      <div class="row-actions">
        <button id="confirmBtn">${iconMarkup("check")}<span>Apply and continue</span></button>
      </div>
    </div>
  `);
  document.getElementById("confirmBtn").onclick = () => {
    runAction(`/jobs/${jobId}/confirm`, { method: "POST" }, "Applying and running automated steps…");
  };
}

// Flow 1 and Flow 2 dispatch are both "download and wait" screens inside the
// normal job wizard -- advancing only happens externally, from the separate
// Flow 2 / Flow 3 intake pages (renderFlow2Page / renderFlow3Page above).
// The "Next" button is just a navigation shortcut to that page -- it doesn't
// advance the job itself, only uploading a response there does.
function renderFlowWaitStage(current) {
  const isFlow1 = current.type === "flow1";
  const downloads = isFlow1
    ? [
        { label: "Download Haider's Flow 1 file", href: `${API}/jobs/${jobId}/flow1/haider.xlsx` },
        { label: "Download Naresh's Flow 1 file", href: `${API}/jobs/${jobId}/flow1/naresh.xlsx` },
      ]
    : [{ label: "Download Flow 2 file (for Haider)", href: `${API}/jobs/${jobId}/flow2/haider.xlsx` }];
  const waitingOn = isFlow1
    ? "Naresh's response is uploaded on the Flow 2 page"
    : "Haider's response is uploaded on the Flow 3 page";
  const stillInvalid = (current.invalid_id_count || 0) + (current.invalid_dob_count || 0);
  const invalidNote = isFlow1
    ? ""
    : stillInvalid > 0
      ? `<p class="muted">${current.invalid_id_count} account(s) still have an invalid ID and ${current.invalid_dob_count} an invalid DOB — that's what's in the Flow 2 file above.</p>`
      : `<p class="muted">No IDs or DOBs are currently invalid — Haider doesn't need a second-pass file for this job on Flow 3, just his corrections file.</p>`;
  const nextLabel = isFlow1 ? "Next: Flow 2" : "Next: Flow 3";
  setCard(`
    <div class="stage-intro upload-stage-intro">
      <button class="secondary quiet-action back-to-dashboard" id="backToDashboardBtn" type="button">&larr; Back to dashboard</button>
      <span class="stage-kicker">Share checkpoint</span>
      <h2>${escapeHtml(current.title)}</h2>
      <p class="muted">Download the file(s) below (${current.row_count} rows) and share them manually. This job stays parked here until ${waitingOn}.</p>
      ${invalidNote}
    </div>
    <div class="row-actions">
      ${downloads.map((d, i) => `<button class="secondary" id="flowDownloadBtn${i}" type="button">${iconMarkup("download")}<span>${escapeHtml(d.label)}</span></button>`).join("")}
    </div>
    <div class="row-actions">
      <button id="flowNextBtn" type="button">${iconMarkup("play")}<span>${nextLabel}</span></button>
    </div>
  `);
  document.getElementById("backToDashboardBtn").onclick = () => renderDashboard();
  downloads.forEach((d, i) => {
    document.getElementById(`flowDownloadBtn${i}`).onclick = () => { window.location.href = d.href; };
  });
  document.getElementById("flowNextBtn").onclick = () => (isFlow1 ? renderFlow2Page() : renderFlow3Page());
}

function renderDone(current) {
  const quality = current.quality_summary || {};
  const statusClass = quality.status === "PASSED" ? "quality-passed" : "quality-exceptions";
  const auditRows = (current.audit_preview || []).slice().reverse().map((event) => `
    <tr>
      <td>${escapeHtml(event.account_number || "")}</td>
      <td>${escapeHtml(event.field || "")}</td>
      <td><span class="data-label data-label-${String(event.label || "source").toLowerCase().replace(/[^a-z]+/g, "-")}">${escapeHtml(event.label || "Source file")}</span></td>
      <td>${escapeHtml(event.reason || "")}</td>
      <td>${escapeHtml(event.operator || "")}</td>
    </tr>`).join("");
  setCard(`
    <div class="completion-header">
      <div class="completion-mark" aria-hidden="true"></div>
      <div>
        <span class="stage-kicker">Final output</span>
        <h2>Pipeline complete</h2>
      </div>
    </div>
    <div class="result-summary">${current.row_count} rows are ready in the final dataset.</div>
    <div class="quality-summary">
      <div class="quality-status ${statusClass}">${escapeHtml(quality.status || "COMPLETED")}</div>
      <div class="quality-grid">
        <span><b>Total rows</b>${(quality.total_rows || current.row_count).toLocaleString()}</span>
        <span><b>Names corrected</b>${(quality.names_corrected || 0).toLocaleString()}</span>
        <span><b>Operator corrections</b>${(quality.operator_corrections || 0).toLocaleString()}</span>
        <span><b>Invalid DOBs remaining</b>${(quality.invalid_dobs_remaining || 0).toLocaleString()}</span>
        <span><b>Invalid addresses remaining</b>${(quality.invalid_addresses_remaining || 0).toLocaleString()}</span>
        <span><b>Missing phones remaining</b>${(quality.missing_phones_remaining || 0).toLocaleString()}</span>
        <span><b>Generated IDs assigned</b>${(quality.generated_ids_assigned || 0).toLocaleString()}</span>
        <span><b>CMS matches</b>${(quality.cms_matches || 0).toLocaleString()}</span>
        <span><b>CMS unmatched</b>${(quality.cms_unmatched || 0).toLocaleString()}</span>
      </div>
    </div>
    ${auditRows ? `
      <details class="audit-preview">
        <summary>Recent audit history</summary>
        <div class="table-wrap audit-table-wrap">
          <table><thead><tr><th>Account number</th><th>Field</th><th>Label</th><th>Reason</th><th>Operator</th></tr></thead><tbody>${auditRows}</tbody></table>
        </div>
      </details>` : ""}
    <div class="row-actions">
      <button id="downloadBtn">Download final file</button>
      <button class="secondary" id="downloadAuditBtn">Download audit report (${(current.audit_event_count || 0).toLocaleString()})</button>
      <button class="secondary quiet-action" id="newJobBtn">Start a new batch</button>
    </div>
  `);
  document.getElementById("downloadBtn").onclick = () => {
    window.location.href = `${API}/jobs/${jobId}/download`;
  };
  document.getElementById("downloadAuditBtn").onclick = () => {
    window.location.href = `${API}/jobs/${jobId}/audit/download`;
  };
  document.getElementById("newJobBtn").onclick = () => {
    localStorage.removeItem("k2_job_id");
    jobId = null;
    knownHistoryCount = null;
    setViewScope(null);
    refresh();
  };
}

function editMapFromItems(items) {
  const map = {};
  (items || []).forEach((edit) => {
    const rowKey = String(edit.row_key);
    map[rowKey] = map[rowKey] || {};
    map[rowKey][edit.field] = String(edit.value ?? "");
  });
  return map;
}

function cloneEditMap(map) {
  return Object.fromEntries(Object.entries(map).map(([rowKey, fields]) => [rowKey, { ...fields }]));
}

function hasOwn(object, property) {
  return Object.prototype.hasOwnProperty.call(object || {}, property);
}

function currentManualValue(rowKey, field, fallback) {
  if (hasOwn(pendingEdits[rowKey], field)) return pendingEdits[rowKey][field];
  if (hasOwn(savedDraftEdits[rowKey], field)) return savedDraftEdits[rowKey][field];
  return String(fallback ?? "");
}

function effectiveEditMap() {
  const result = cloneEditMap(savedDraftEdits);
  Object.entries(pendingEdits).forEach(([rowKey, fields]) => {
    Object.entries(fields).forEach(([field, value]) => {
      const original = originalManualValues[rowKey]?.[field];
      result[rowKey] = result[rowKey] || {};
      if (original !== undefined && value === original) delete result[rowKey][field];
      else result[rowKey][field] = value;
      if (!Object.keys(result[rowKey]).length) delete result[rowKey];
    });
  });
  return result;
}

function flattenEditMap(map) {
  const edits = [];
  Object.entries(map).forEach(([rowKey, fields]) => {
    Object.entries(fields).forEach(([field, value]) => {
      edits.push({ row_key: parseInt(rowKey, 10), field, value });
    });
  });
  return edits;
}

function hasUnsavedChanges() {
  return Object.values(pendingEdits).some((fields) => Object.keys(fields).length > 0);
}

function updateReviewCounts() {
  const effective = effectiveEditMap();
  const count = Object.keys(effective).length;
  const countEl = document.getElementById("editedRowCount");
  if (countEl) countEl.textContent = `${count} row${count === 1 ? "" : "s"}`;
  const undoBtn = document.getElementById("undoEditBtn");
  if (undoBtn) undoBtn.disabled = !editHistory.length;
}

function fieldClassName(field) {
  return `field-${String(field).toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
}

function labelMarkup(labels) {
  if (!labels || !labels.length) return '<span class="data-label data-label-source">Source file</span>';
  return labels.map((label) => {
    const kind = label.toLowerCase().replace(/[^a-z]+/g, "-");
    return `<span class="data-label data-label-${kind}">${escapeHtml(label)}</span>`;
  }).join("");
}

function issueReasonsByField(reasons) {
  const issues = {};
  const add = (field, reason) => {
    issues[field] = issues[field] || [];
    issues[field].push(reason);
  };
  (reasons || []).forEach((reason) => {
    if (reason.startsWith("First name")) add("ACCOUNT_FIRST_NAME", reason);
    else if (reason.startsWith("Middle name")) add("ACCOUNT_MIDDLE_NAME", reason);
    else if (reason.startsWith("Last name")) add("ACCOUNT_LAST_NAME", reason);
    else if (reason.startsWith("DOB")) add("ACCOUNT_HOLDER_DOB", reason);
    else if (reason.startsWith("Phone number")) add("PHONE_NUMBER", reason);
    else if (reason.startsWith("ID type")) add("ID_TYPE", reason);
    else if (reason.startsWith("Passport number") || reason.startsWith("National ID") ||
             reason.startsWith("Civil ID") || reason.startsWith("ID number")) add("ID_NUMBER", reason);
  });
  return issues;
}

function issueInputMarkup(row, column, value, original, issueReasons, editable) {
  const rowKey = String(row.row_key);
  const edited = value !== original ? " edited-cell" : "";
  const mode = column === "PHONE_NUMBER" ? "tel" : "text";
  const issueId = `issue-${rowKey}-${column}`;
  const input = `<input class="manual-input${edited}${issueReasons.length ? " has-issue" : ""}" data-row="${row.row_key}" data-field="${column}" data-original="${escapeHtml(original)}" inputmode="${mode}" value="${escapeHtml(value)}"${editable ? "" : " readonly"}${issueReasons.length ? ` aria-describedby="${issueId}"` : ""} />`;
  if (!issueReasons.length) return input;
  const tooltip = issueReasons.map((reason) => `<span>${escapeHtml(reason)}</span>`).join("");
  return `<div class="issue-field">${input}<span class="issue-flag" aria-hidden="true">!</span><span class="issue-tooltip" id="${issueId}" role="tooltip">${tooltip}</span></div>`;
}

function renderManualEditStage(current) {
  activeManualPage = current.page || 1;
  savedDraftEdits = editMapFromItems(current.draft_edits);
  pendingEdits = {};
  originalManualValues = {};
  editHistory = [];

  const cols = [...current.context_cols, ...current.editable_cols];
  const editableSet = new Set(current.editable_cols);
  const accountColumn = cols[0];
  const restColumns = cols.slice(1);

  current.rows.forEach((row) => {
    const rowKey = String(row.row_key);
    originalManualValues[rowKey] = {};
    current.editable_cols.forEach((field) => { originalManualValues[rowKey][field] = String(row[field] ?? ""); });
  });

  const headerHtml = [`<th>${accountColumn}</th>`, "<th>Data status</th>"]
    .concat(restColumns.map((column) => `<th>${column}</th>`))
    .join("");
  const rowsHtml = current.rows.map((row) => {
    const accountCell = `<td class="account-cell">${escapeHtml(row[accountColumn] ?? "")}</td>`;
    const supportingCells = `<td class="label-cell">${labelMarkup(row.data_labels)}</td>`;
    const issues = issueReasonsByField(row.validation_reasons);
    const dataCells = restColumns.map((column) => {
      if (!editableSet.has(column)) return `<td class="${fieldClassName(column)}">${escapeHtml(row[column] ?? "")}</td>`;
      const rowKey = String(row.row_key);
      const original = String(row[column] ?? "");
      const value = currentManualValue(rowKey, column, original);
      return `<td class="${fieldClassName(column)}">${issueInputMarkup(row, column, value, original, issues[column] || [], current.manual_edit_enabled)}</td>`;
    }).join("");
    return `<tr>${accountCell}${supportingCells}${dataCells}</tr>`;
  }).join("");

  setCard(`
    <div class="stage-intro">
      <span class="stage-kicker">Review checkpoint</span>
      <div class="stage-title-row">
        <h2>${escapeHtml(current.title)}</h2>
        <span class="badge">${current.total_flagged} flagged</span>
      </div>
      <p class="muted">${escapeHtml(current.instructions)}</p>
    </div>
    <div class="review-toolbar">
      <div class="review-stat"><span class="review-stat-label">Page</span><span class="review-stat-value">${current.page} of ${current.page_count}</span></div>
      <div class="review-stat"><span class="review-stat-label">Visible now</span><span class="review-stat-value">${current.showing} records</span></div>
      <div class="review-stat"><span class="review-stat-label">Rows remaining</span><span class="review-stat-value">${current.rows_remaining} records</span></div>
      <div class="review-stat"><span class="review-stat-label">Edited</span><span class="review-stat-value" id="editedRowCount">0 rows</span></div>
    </div>
    <div class="table-wrap">
      <table class="manual-table"><thead><tr>${headerHtml}</tr></thead><tbody>${rowsHtml}</tbody></table>
    </div>
    <div class="review-controls">
      <div class="row-actions review-actions">
        <button class="secondary quiet-action" id="previousPageBtn" ${current.page <= 1 ? "disabled" : ""}>Previous page</button>
        <button class="secondary quiet-action" id="nextPageBtn" ${current.page >= current.page_count ? "disabled" : ""}>Next page</button>
        ${current.manual_edit_enabled ? `
        <button class="secondary quiet-action" id="undoEditBtn" disabled>Undo last change</button>
        <button class="secondary quiet-action" id="resetChangesBtn">Reset changes</button>` : ""}
      </div>
      <div class="row-actions review-actions">
        ${current.manual_edit_enabled ? `
        <button class="secondary draft-action" id="saveDraftBtn">Save draft</button>
        <button id="submitBtn">Save and validate</button>` : ""}
        <button class="warning-action" id="skipBtn">Skip unresolved rows</button>
      </div>
      ${current.manual_edit_enabled ? "" : `<p class="muted readonly-notice">Inline editing is off for now — this table is view-only. Fix these rows in the Excel review workbook below and upload it back here.</p>`}
    </div>
    <div class="workbook-panel">
      <div class="guidance-card">
        <span class="guidance-icon guidance-icon-art">${detailIconMarkup("export", "guidance-detail-icon")}</span>
        <div><dt>Prefer Excel?</dt><dd>Download the review workbook (one sheet per manual stage reached so far), fill it in, and upload it back here instead of editing inline.</dd></div>
      </div>
      <div class="workbook-panel-actions">
        <button class="secondary" id="downloadWorkbookBtn" type="button">${iconMarkup("download")}<span>Download review workbook</span></button>
        <label class="workbook-file-input" for="workbookFileInput" title="Select the completed review workbook">
          <input class="file-input" type="file" id="workbookFileInput" accept=".xlsx,.xls" />
          <span class="file-picker-trigger">Choose completed workbook</span>
          <span class="file-name" id="workbookFileName">No file selected</span>
        </label>
        <label class="workbook-force-advance"><input type="checkbox" id="workbookForceAdvance" /> Skip rows still flagged after import</label>
        <button id="uploadWorkbookBtn" type="button">${iconMarkup("upload")}<span>Import workbook</span></button>
      </div>
    </div>
  `);

  mainCardEl.querySelectorAll("input[data-row]").forEach((input) => {
    input.addEventListener("focus", () => {
      collapseSidebarForEditing();
      input.dataset.previousValue = input.value;
    });
    input.addEventListener("input", () => recordManualInput(input));
    input.addEventListener("change", () => {
      if (input.dataset.previousValue !== input.value) {
        editHistory.push({ rowKey: input.dataset.row, field: input.dataset.field, value: input.dataset.previousValue });
      }
      input.dataset.previousValue = input.value;
      updateReviewCounts();
    });
    input.addEventListener("keydown", (event) => handleManualKeyboard(event, input, current));
  });

  document.getElementById("previousPageBtn").onclick = () => loadManualPage(current.page - 1);
  document.getElementById("nextPageBtn").onclick = () => loadManualPage(current.page + 1);
  document.getElementById("skipBtn").onclick = () => submitEdits(true);
  if (current.manual_edit_enabled) {
    document.getElementById("undoEditBtn").onclick = undoLastChange;
    document.getElementById("resetChangesBtn").onclick = resetChanges;
    document.getElementById("saveDraftBtn").onclick = saveDraft;
    document.getElementById("submitBtn").onclick = () => submitEdits(false);
  }

  document.getElementById("downloadWorkbookBtn").onclick = () => {
    window.location.href = `${API}/jobs/${jobId}/workbook`;
  };
  const workbookInput = document.getElementById("workbookFileInput");
  const workbookFileName = document.getElementById("workbookFileName");
  workbookInput.addEventListener("change", () => {
    const file = workbookInput.files[0];
    workbookFileName.textContent = file ? file.name : "No file selected";
  });
  document.getElementById("uploadWorkbookBtn").onclick = () => {
    const file = workbookInput.files[0];
    if (!file) { toast("Choose a completed workbook first", "error"); return; }
    const forceAdvance = document.getElementById("workbookForceAdvance").checked;
    const fd = new FormData();
    fd.append("file", file);
    fd.append("force_advance", forceAdvance ? "true" : "false");
    runAction(`/jobs/${jobId}/submit-workbook`, { method: "POST", body: fd }, "Importing workbook…");
  };

  updateReviewCounts();
  mainCardEl.querySelectorAll("input[data-field='ID_NUMBER']").forEach(validateManualInput);
}

function recordManualInput(input) {
  const rowKey = input.dataset.row;
  const field = input.dataset.field;
  const original = String(input.dataset.original ?? "");
  const saved = savedDraftEdits[rowKey]?.[field];
  const baseline = saved === undefined ? original : saved;
  pendingEdits[rowKey] = pendingEdits[rowKey] || {};
  if (input.value === baseline) delete pendingEdits[rowKey][field];
  else pendingEdits[rowKey][field] = input.value;
  if (!Object.keys(pendingEdits[rowKey]).length) delete pendingEdits[rowKey];
  input.classList.toggle("edited-cell", input.value !== original);
  validateManualInput(input);
  if (field === "ID_TYPE") {
    const idInput = input.closest("tr").querySelector("input[data-field='ID_NUMBER']");
    if (idInput) validateManualInput(idInput);
  }
  updateReviewCounts();
}

function validateManualInput(input) {
  if (input.dataset.field !== "ID_NUMBER") return;
  const typeInput = input.closest("tr").querySelector("input[data-field='ID_TYPE']");
  const idType = typeInput ? typeInput.value.trim().toLowerCase() : "";
  const invalidNationalId = ["national id", "nid", "nationalid"].includes(idType) && !/^\d*$/.test(input.value);
  input.setCustomValidity(invalidNationalId ? "National ID must contain digits only." : "");
  input.classList.toggle("field-invalid", invalidNationalId);
}

function handleManualKeyboard(event, input, current) {
  if (event.altKey && event.key === "ArrowRight" && current.page < current.page_count) {
    event.preventDefault();
    loadManualPage(current.page + 1);
    return;
  }
  if (event.altKey && event.key === "ArrowLeft" && current.page > 1) {
    event.preventDefault();
    loadManualPage(current.page - 1);
    return;
  }
  if (event.key !== "Enter") return;
  event.preventDefault();
  const inputs = [...mainCardEl.querySelectorAll("input[data-row]")];
  const next = inputs[inputs.indexOf(input) + 1];
  if (next) next.focus();
}

async function loadManualPage(page) {
  if (hasUnsavedChanges()) {
    toast("Save the draft or reset local changes before changing pages.", "error");
    return;
  }
  try {
    const current = await apiRetrying(`/jobs/${jobId}/current?page=${page}`);
    if (current.type === "manual_edit") renderManualEditStage(current);
  } catch (e) {
    renderSettleError(e);
  }
}

async function saveDraft() {
  if (isBusy) return;
  const editMap = effectiveEditMap();
  isBusy = true;
  lockAllControls(true);
  try {
    await api(`/jobs/${jobId}/draft`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ edits: flattenEditMap(editMap) }),
    });
    savedDraftEdits = editMap;
    pendingEdits = {};
    editHistory = [];
    updateReviewCounts();
    toast("Draft saved");
  } catch (e) {
    toast(e.message, "error");
  } finally {
    isBusy = false;
    lockAllControls(false);
  }
}

async function resetChanges() {
  if (isBusy) return;
  isBusy = true;
  lockAllControls(true);
  try {
    await api(`/jobs/${jobId}/draft`, { method: "DELETE" });
    savedDraftEdits = {};
    pendingEdits = {};
    editHistory = [];
    mainCardEl.querySelectorAll("input[data-row]").forEach((input) => {
      input.value = input.dataset.original || "";
      input.classList.remove("edited-cell", "field-invalid");
      input.setCustomValidity("");
    });
    updateReviewCounts();
    toast("Draft and local changes reset");
  } catch (e) {
    toast(e.message, "error");
  } finally {
    isBusy = false;
    lockAllControls(false);
  }
}

function undoLastChange() {
  const change = editHistory.pop();
  if (!change) return;
  const input = mainCardEl.querySelector(`input[data-row="${change.rowKey}"][data-field="${change.field}"]`);
  if (!input) return;
  input.value = change.value;
  recordManualInput(input);
  input.focus();
  updateReviewCounts();
}

function submitEdits(forceAdvance) {
  const invalidInput = mainCardEl.querySelector("input.field-invalid");
  if (invalidInput && !forceAdvance) {
    invalidInput.reportValidity();
    invalidInput.focus();
    return;
  }
  const edits = flattenEditMap(effectiveEditMap());
  runAction(
    `/jobs/${jobId}/submit`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ edits, force_advance: forceAdvance }),
    },
    forceAdvance ? "Skipping remaining rows…" : "Submitting edits and re-validating…"
  );
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

window.addEventListener("beforeunload", (event) => {
  if (!hasUnsavedChanges()) return;
  event.preventDefault();
  event.returnValue = "";
});

refresh();
