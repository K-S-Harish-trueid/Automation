const API = window.K2_API_BASE || "/api";
const POLL_MS = window.K2_POLL_INTERVAL_MS || 900;

// Flip to true to show the specific, backend-driven detail lines on stage
// screens again (row/field counts, per-column instructions, invalid-count
// notes). Off by default -- these screens show short generic copy instead.
const STAGE_DETAILS_VERBOSE = false;

// Master switch for the short subtitle paragraph under a page's <h2> (New
// Batch / Stage 2 / Stage 3 intake pages -- see showPageDescription below
// for exactly which key belongs to which screen). Off hides all of them at
// once; set one key in PAGE_DESCRIPTION_OVERRIDES to make a single page
// differ from this master switch without touching the others, e.g.
// `{ stage3: false }` keeps every subtitle except Stage 3's, regardless of
// what SHOW_PAGE_DESCRIPTIONS is set to.
const SHOW_PAGE_DESCRIPTIONS = false;
const PAGE_DESCRIPTION_OVERRIDES = {
  // newBatch: false,
  // stage2: false,
  // stage3: false,
};
function showPageDescription(key) {
  return PAGE_DESCRIPTION_OVERRIDES[key] ?? SHOW_PAGE_DESCRIPTIONS;
}

let jobId = localStorage.getItem("k2_job_id");
// Which stage's own steps the sidebar is allowed to show, or null for the
// unrestricted master view (dashboard / new batch / resuming a job from the
// job history list). Set to 2 or 3 only when a job was just adopted through
// the Stage 2 or Stage 3 standalone page, so Naresh/Haider only ever see
// their own stage's step(s) -- not the other stages' names or a step count
// that hints at work beyond their own page. Display-only: the full status
// JSON is still fetched underneath, this just narrows what gets rendered.
let viewScopeStage = Number(localStorage.getItem("k2_view_scope")) || null;

function setViewScope(stageNum) {
  viewScopeStage = stageNum || null;
  if (viewScopeStage) localStorage.setItem("k2_view_scope", String(viewScopeStage));
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
const goToDashboardBtnEl = document.getElementById("goToDashboardBtn");
const rollbackJobBtnEl = document.getElementById("rollbackJobBtn");
const layoutEl = document.querySelector(".layout");
const sidebarToggleEl = document.getElementById("sidebarToggle");
const sidebarBrandToggleEl = document.getElementById("sidebarBrandToggle");
const themeToggleEl = document.getElementById("themeToggle");

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
  stage1_dispatch: "mail",
  stage2_dispatch: "puzzle",
  final_id_check: "shield",
};

// Icons for Stage 2's merge/name/ID/DOB/dispatch sub-steps, in the fixed
// order handoff.py's apply_naresh_response always appends them.
const STAGE2_SUBSTEP_ICONS = ["layers", "userCheck", "idCard", "listChecks", "mail"];

// Toggle the Stage 2 Dispatch sub-step breakdown (Merge/Name/ID/DOB/Dispatch
// rows) in the sidebar. Off for now -- flip to true to show them again.
const SHOW_STAGE2_SUBSTEPS = false;

function iconMarkup(name, className = "ui-icon") {
  return `<svg class="${className}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICON_PATHS[name]}</svg>`;
}

function detailIconMarkup(name, className = "detail-icon") {
  return `<img class="${className}" src="${DETAIL_ICON_ASSETS[name]}" alt="" aria-hidden="true" />`;
}

goToDashboardBtnEl.innerHTML = `${iconMarkup("home")}<span>Dashboard</span>`;
rollbackJobBtnEl.innerHTML = `${detailIconMarkup("rollback", "action-detail-icon")}<span>Rollback</span>`;

if (checkNowBtnEl) checkNowBtnEl.onclick = () => { forceCheckNow = true; };

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

// Drops the whole pipeline sidebar while no job is loaded (dashboard /
// start-new-batch / Stage 2-3 intake screens), instead of showing an empty
// rail with a "0 / 0 stages" placeholder and nothing under it.
function setSidebarJobChrome(visible) {
  layoutEl.classList.toggle("no-sidebar", !visible);
  progressPanelEl.hidden = !visible;
  stageListLabelEl.hidden = !visible;
  if (!visible) {
    stageListEl.innerHTML = "";
    stageMeterEl.innerHTML = "";
  }
}

function setJobContextActions(status) {
  // Dashboard is the one consistent way back everywhere -- always visible,
  // instead of screens (new batch, Stage 2/3 intake) each needing their own
  // in-card "Back to dashboard" button when this one was hidden.
  goToDashboardBtnEl.hidden = false;
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
  if (isBusy) return;
  localStorage.removeItem("k2_job_id");
  jobId = null;
  knownHistoryCount = null;
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

function filePickerMarkup(inputId, zoneId, fileNameId, prompt, note, icon = "fileUp") {
  return `
    <label class="dropzone" id="${zoneId}" for="${inputId}">
      <input class="file-input" type="file" id="${inputId}" accept=".csv,.xlsx,.xls" />
      <span class="file-picker-icon">${iconMarkup(icon)}</span>
      <span class="dropzone-content">
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

// Sheet-name/row-count preview shown under every download button, so seeing
// what's in a handoff file doesn't require downloading and opening it first
// (backed by GET .../summary siblings of each download route -- see
// _workbook_summary in helpers.py). Placeholder markup goes in immediately;
// loadWorkbookSummary fills it in once the (async) fetch resolves.
function workbookSummaryMarkup(containerId) {
  return `<div class="workbook-summary" id="${containerId}"><span class="muted">Reading file contents…</span></div>`;
}

async function loadWorkbookSummary(containerId, summaryPath) {
  const el = document.getElementById(containerId);
  if (!el) return; // screen moved on before the fetch resolved
  try {
    const data = await api(summaryPath);
    const sheets = data.sheets || [];
    if (!sheets.length) { el.innerHTML = `<span class="muted">Empty.</span>`; return; }
    const rows = sheets.map((s) => `<tr><td>${escapeHtml(s.name)}</td><td>${s.rows.toLocaleString()}</td></tr>`).join("");
    el.innerHTML = `
      <table class="workbook-summary-table">
        <thead><tr><th>Sheet</th><th>Rows</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    `;
  } catch (e) {
    el.innerHTML = `<span class="muted">Couldn't read file contents.</span>`;
  }
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
  // A stage that reports its percent once and then does real work with no
  // incremental updates (e.g. matching a large reference file) would
  // otherwise freeze the ring on a static number for the whole time, which
  // reads as "stuck," not "still going." Only switch to the determinate
  // fill when the percent actually moves between polls; a repeated value
  // keeps the ring spinning instead of settling on one that isn't changing.
  //
  // Backend percent only advances at stage boundaries (idx/total per stage,
  // not within one), so at a 900ms poll interval a fresh percent is almost
  // always immediately followed by an "unchanged" poll -- flipping straight
  // to indeterminate on that first unchanged poll made the ring visibly pop
  // between filled and spinning every stage instead of holding the fill.
  // Require a few consecutive unchanged polls (a real stall, not just the
  // gap until the next stage reports in) before giving up on determinate.
  const STALL_POLLS_BEFORE_INDETERMINATE = 3;
  let lastReportedPercent = null;
  let unchangedPolls = 0;
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
    const pct = progress.percent ?? 0;
    if (pct !== lastReportedPercent) {
      setRingProgress(pct);
      lastReportedPercent = pct;
      unchangedPolls = 0;
    } else if (++unchangedPolls >= STALL_POLLS_BEFORE_INDETERMINATE) {
      setRingIndeterminate();
    }

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
// `viewScopeStage` when a job was adopted through the Stage 2/3 page --
// plus never any stage the backend flagged "hidden" (registry.py's
// HIDDEN_STAGE_IDS; the stage still runs normally, it's just not shown).
// Keeps each entry's original index into status.stages so "is this the
// current stage" and rollback-target lookups still line up.
function visibleStageEntries(status) {
  return status.stages
    .map((stage, index) => ({ stage, index }))
    .filter(({ stage }) => !stage.hidden && (!viewScopeStage || stage.stage === viewScopeStage));
}

function renderStageMeterAndHeader(status) {
  setSidebarJobChrome(true);
  const entries = visibleStageEntries(status);
  const total = entries.length;
  const done = entries.filter(({ stage }) => stage.status === "done").length;
  const pct = total ? Math.round((done / total) * 100) : 0;
  progressLabelEl.textContent = `${done} / ${total} stages`;
  progressPctEl.textContent = `${pct}%`;
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
    if (!viewScopeStage && stage.stage !== lastGroup) {
      lastGroup = stage.stage;
      const header = document.createElement("li");
      header.className = "stage-group-header";
      header.textContent = `Stage ${stage.stage}`;
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

    // Naresh's response resolves merge -> name/ID/DOB recheck -> dispatch in
    // one atomic call, but the history entry it appends lands under stage_id
    // "stage1_dispatch" (that's the gate this action actually resolves --
    // see handoff.py's file-level comment: routes are grouped by which
    // stage's action they serve, not which stage_id they resolve). It shows
    // up here, under stage2_dispatch, since that's the stage this breakdown
    // is actually explaining -- stage2_dispatch's OWN status is still
    // "current"/pending its own resolution at this point, so the gate is
    // "does this history entry with sub_steps exist", not stage2_dispatch's
    // own status.
    if (SHOW_STAGE2_SUBSTEPS && stage.id === "stage2_dispatch") {
      const historyEntry = [...(status.history || [])].reverse()
        .find((h) => h.stage_id === "stage1_dispatch" && h.sub_steps);
      (historyEntry?.sub_steps || []).forEach((sub, i) => {
        const subLi = document.createElement("li");
        subLi.className = "stage-substep done";
        subLi.title = `${sub.label}: ${sub.detail}`;
        subLi.setAttribute("aria-label", `${sub.label}: ${sub.detail}`);
        // Same 3-column dot/icon/text structure real stage rows use (not a
        // scaled-down variant) so these read as the same design language --
        // only the left indent marks them as nested under Stage 2 Dispatch.
        subLi.innerHTML = `
          <span class="dot"></span>
          <span class="stage-icon">${iconMarkup(STAGE2_SUBSTEP_ICONS[i] || "layers", "stage-sidebar-icon")}</span>
          <span class="stage-text">
            <span class="stage-name">${escapeHtml(sub.label)}</span>
            <span class="stage-status">${escapeHtml(sub.detail)}</span>
          </span>
        `;
        stageListEl.appendChild(subLi);
      });
    }
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

// The sidebar's own "which job is this" context (progress bar, stage list)
// collapses or hides outright on narrow/mobile widths (the stage list itself
// shrinks to icon-only dots below 860px) -- .main-context stays visible at
// every width, just wrapping at 560px, so that's where job identity lives
// instead of the sidebar. No job loaded means there's nothing to say here,
// so both pills stay hidden rather than showing a placeholder.
function setJobContextLabel() {
  const idEl = document.getElementById("jobContextId");
  if (!idEl) return;
  if (jobId) {
    idEl.hidden = false;
    idEl.textContent = `Job ${jobId}`;
  } else {
    idEl.hidden = true;
  }
}

// The single place that decides what's on screen after any action settles.
// Both server reads are retried a few times (apiRetrying) since a dropped
// LAN request here previously left the page silently showing the OLD stage
// until a manual browser refresh -- now it self-heals, and if it still can't
// reach the server it shows an explicit Retry card instead of doing nothing.
async function refresh(animate) {
  if (!jobId) {
    knownHistoryCount = null;
    setJobContextLabel();
    renderDashboard();
    return;
  }

  let status;
  try {
    status = await apiRetrying(`/jobs/${jobId}`);
  } catch (e) {
    setJobContextLabel();
    renderJobRecovery(e);
    return;
  }
  setJobContextLabel();

  const firstLoad = knownHistoryCount === null;
  // Same hidden-stage filter the sidebar uses (registry.py's
  // HIDDEN_STAGE_IDS, surfaced per-stage as status.stages[].hidden) --
  // otherwise a hidden stage's history entry still flashes through this
  // recap even though it never appears in the sidebar list it's meant to
  // summarize.
  const hiddenStageIds = new Set(status.stages.filter((s) => s.hidden).map((s) => s.id));
  const newItems = firstLoad ? [] : status.history.slice(knownHistoryCount).filter((h) => !hiddenStageIds.has(h.stage_id));
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
  setJobContextActions(null);
  setCard(`
    <div class="stage-intro">
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
  setJobContextActions(null);
  setJobContextLabel();
  setCard(`
    <div class="stage-intro upload-stage-intro">
      <div class="stage-title-row">
        <h2>Dashboard</h2>
      </div>
    </div>
    <div class="row-actions">
      <button id="newBatchBtn">${iconMarkup("play")}<span>Start new batch</span></button>
      <button class="secondary" id="stage2PageBtn" type="button">${iconMarkup("mail")}<span>Stage 2 (Naresh)</span></button>
      <button class="secondary" id="stage3PageBtn" type="button">${iconMarkup("puzzle")}<span>Stage 3 (Haider)</span></button>
    </div>
    <div class="job-history">
      <div class="job-history-filters">
        <input type="search" id="jobSearchInput" placeholder="Search job ID or filename…" aria-label="Search jobs" />
        <select id="jobStageFilter" aria-label="Filter by stage">
          <option value="">All stages</option>
          <option value="1">Stage 1</option>
          <option value="2">Stage 2</option>
          <option value="3">Stage 3</option>
          <option value="done">Done</option>
        </select>
      </div>
      <div class="table-wrap job-history-wrap" id="jobHistoryWrap">
        <p class="muted"><span class="mini-spinner"></span>Loading job history…</p>
      </div>
    </div>
    <details class="audit-preview" id="historicalPanel">
      <summary>${iconMarkup("database")}<span>Historical reference data</span></summary>
      <div class="historical-panel-body">
        <p class="muted" id="historicalStatusLine"><span class="mini-spinner"></span>Checking historical.db…</p>
        ${historicalSeedPanelMarkup("historical")}
      </div>
    </details>
  `);

  document.getElementById("newBatchBtn").onclick = () => renderNewBatch();
  document.getElementById("stage2PageBtn").onclick = () => renderStage2Page();
  document.getElementById("stage3PageBtn").onclick = () => renderStage3Page();
  document.getElementById("jobSearchInput").oninput = () => renderJobHistoryTable();
  document.getElementById("jobStageFilter").onchange = () => renderJobHistoryTable();
  wireHistoricalSeedPanel("historical", (status) => {
    const line = document.getElementById("historicalStatusLine");
    if (line) line.textContent = formatHistoricalStatus(status);
  });

  loadJobHistory();
  loadHistoricalStatus();
}

// Shared "seed historical data" block (file picker + Seed button) -- used by
// both the Dashboard's "Historical reference data" panel and the
// historical-override warning gate (renderHistoricalWarningStage), so the
// two entry points to the same action can't drift out of sync with each
// other. idPrefix keeps each call site's DOM ids distinct on the page.
function historicalSeedPanelMarkup(idPrefix) {
  return `
    <div class="upload-simple-layout">
      ${filePickerMarkup(`${idPrefix}FileInput`, `${idPrefix}FileZone`, `${idPrefix}FileName`, "Select xlsx/csv", "Replaces the entire historical store -- same as seed_historical.py", "database")}
    </div>
    <div class="row-actions">
      <button class="secondary quiet-action" id="${idPrefix}SeedBtn" type="button">${iconMarkup("database")}<span>Seed historical data</span></button>
    </div>
  `;
}

function wireHistoricalSeedPanel(idPrefix, onSeeded) {
  wireFilePicker(`${idPrefix}FileInput`, `${idPrefix}FileZone`, `${idPrefix}FileName`);
  document.getElementById(`${idPrefix}SeedBtn`).onclick = async () => {
    const file = document.getElementById(`${idPrefix}FileInput`).files[0];
    const status = await seedHistoricalStore(file);
    if (status) onSeeded(status);
  };
}

function formatHistoricalStatus(status) {
  if (!status || !status.seeded) {
    return "Not seeded -- the historical override stage will run with a warning and no accounts updated until this is loaded.";
  }
  const rows = (status.row_count || 0).toLocaleString();
  const updated = status.seeded_at ? new Date(status.seeded_at).toLocaleString() : "unknown time";
  return `Seeded: ${rows} row(s), last updated ${updated}.`;
}

async function loadHistoricalStatus() {
  const line = document.getElementById("historicalStatusLine");
  if (!line) return;
  try {
    const status = await api("/historical/status");
    if (document.getElementById("historicalStatusLine")) line.textContent = formatHistoricalStatus(status);
  } catch (e) {
    if (document.getElementById("historicalStatusLine")) line.textContent = "Status unavailable -- could not reach the backend.";
  }
}

// Replaces the entire historical SQL store from a picked xlsx/csv -- the web
// equivalent of running `python backend/data/seed_historical.py --source
// <file>` (same code path, see routes/historical.py), so an operator never
// has to touch the server's filesystem to (re)load reference data. Shared by
// the Dashboard's "Historical reference data" panel and the historical
// override warning gate (renderHistoricalWarningStage) -- same action, two
// places to reach it from. Returns the new status on success, or null if the
// operator cancelled or it failed (already toasted either way).
async function seedHistoricalStore(file) {
  if (isBusy) return null;
  if (!file) { toast("Choose a file first", "error"); return null; }
  if (!window.confirm(
    `Replace the entire historical SQL store with ${file.name}? This overwrites all existing historical data, not a merge.`
  )) return null;

  const fd = new FormData();
  fd.append("file", file);

  isBusy = true;
  lockAllControls(true);
  setBusy("Seeding historical data…");
  try {
    const status = await api("/historical/seed", { method: "POST", body: fd });
    clearBusy();
    toast(`Historical store seeded (${(status.row_count || 0).toLocaleString()} row(s))`);
    return status;
  } catch (e) {
    clearBusy();
    toast(e.message, "error");
    return null;
  } finally {
    isBusy = false;
    lockAllControls(false);
  }
}

// The data-init / "start a new batch" flow -- its own page, reached from the
// dashboard's "Start new batch" button rather than shown inline there.
function renderNewBatch() {
  setSidebarJobChrome(false);
  setJobContextActions(null);
  setJobContextLabel();
  setCard(`
    <div class="stage-intro upload-stage-intro">
      <h2>Start data preparation</h2>
    </div>
    <div class="upload-simple-layout">
      ${showPageDescription("newBatch") ? `<p class="muted">Upload the raw K2 export (CSV, XLSX, or XLS) to start the pipeline.</p>` : ""}
      ${filePickerMarkup("fileInput", "rawFileZone", "rawFileName", "Select raw file", "CSV, XLSX, or XLS")}
    </div>
    <div class="upload-preview" id="rawUploadPreview" aria-live="polite"></div>
    <div class="row-actions">
      <button id="startBtn">${iconMarkup("play")}<span>Start batch</span></button>
      <button class="secondary" id="backToDashboardBtn2" type="button">Cancel</button>
    </div>
  `);

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
      setViewScope(1); // a brand-new job always starts in Stage 1 -- no reason to show all 12 stages upfront
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
// used by the Stage 2/3 intake pages, which act on a picked job that may be
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

async function fetchStageJobs(stageId) {
  const data = await apiRetrying(`/jobs?stage_id=${encodeURIComponent(stageId)}`);
  return data.jobs || [];
}

// Shared shell for the Stage 2 ("Naresh intake") and Stage 3 ("Haider
// intake") pages: standalone pages (not part of the per-job wizard) with a
// job picker filtered to whichever checkpoint that stage reads from, since
// the person uploading a response may not be the one who created the job.
// Once the upload is applied, the job is adopted as the active job (same as
// clicking it from the dashboard) and handed to the normal wizard via
// refresh() -- Stage 2's dispatch screen (renderStageWaitScreen) and Stage
// 3's confirm/done screens (renderConfirmStage/renderDone) are the exact
// same shared code every job uses, not a separate copy living on this page.
function renderStageIntakePage(config) {
  setSidebarJobChrome(false);
  setJobContextActions(null);
  setJobContextLabel();
  renderStageUploadStep(config);
}

async function renderStageUploadStep(config) {
  setCard(`
    <div class="stage-intro upload-stage-intro">
      <h2>${escapeHtml(config.title)}</h2>
      ${showPageDescription(config.descriptionKey) ? `<p class="muted">${escapeHtml(config.description)}</p>` : ""}
    </div>
    <div id="stageIntakeBody"><p class="muted"><span class="mini-spinner"></span>Loading eligible jobs…</p></div>
  `);

  const body = document.getElementById("stageIntakeBody");
  let jobs;
  try {
    jobs = await fetchStageJobs(config.pickerStageId);
  } catch (e) {
    body.innerHTML = `<p class="muted">Could not load jobs: ${escapeHtml(e.message)}</p>`;
    return;
  }
  if (!document.getElementById("stageIntakeBody")) return;
  if (!jobs.length) {
    body.innerHTML = `<p class="muted">No jobs are currently waiting at this checkpoint.</p>`;
    return;
  }

  const optionsHtml = jobs.map((j) => `<option value="${escapeHtml(j.job_id)}">${escapeHtml(j.job_id)} — ${escapeHtml(j.filename || "")} (${j.row_count != null ? Number(j.row_count).toLocaleString() : "?"} rows)</option>`).join("");
  body.innerHTML = `
    <label class="stage-job-picker">
      <span>Job</span>
      <select id="stageJobSelect">${optionsHtml}</select>
    </label>
    ${config.filePickersHtml}
    <div class="row-actions">
      <button id="stageSubmitBtn" type="button">${iconMarkup("upload")}<span>${escapeHtml(config.submitLabel)}</span></button>
    </div>
  `;
  config.wireFilePickers();

  document.getElementById("stageSubmitBtn").onclick = async () => {
    if (isBusy) return;
    const selectedJobId = document.getElementById("stageJobSelect").value;
    let fd;
    try {
      fd = config.buildFormData();
    } catch (e) {
      toast(e.message, "error");
      return;
    }
    isBusy = true;
    lockAllControls(true);
    renderStageProcessingStep(config);
    try {
      await api(`/jobs/${selectedJobId}${config.path}`, { method: "POST", body: fd });
      await pollJobProgress(selectedJobId);
      // Adopt this job as the active one (same as clicking it on the
      // dashboard) and let the normal wizard render whatever comes next --
      // no custom completion screen to maintain here.
      jobId = selectedJobId;
      localStorage.setItem("k2_job_id", jobId);
      knownHistoryCount = null;
      setViewScope(config.scopeStage);
      isBusy = false;
      lockAllControls(false);
      await refresh(true);
    } catch (e) {
      isBusy = false;
      lockAllControls(false);
      toast(e.message, "error");
      renderStageUploadStep(config);
    }
  };
}

function renderStageProcessingStep(config) {
  setCard(`
    <div class="stage-intro">
      <h2>${escapeHtml(config.title)}</h2>
      <p class="muted"><span class="mini-spinner"></span> ${escapeHtml(config.processingLabel)}</p>
    </div>
  `);
}

function renderStage2Page() {
  renderStageIntakePage({
    title: "Stage 2 — Naresh's response",
    description: "Upload Naresh's IDs + DOB workbook. Only Stage 1 Dispatch jobs are listed below.",
    descriptionKey: "stage2",
    pickerStageId: "stage1_dispatch",
    scopeStage: 2,
    path: "/stage2/naresh-response",
    submitLabel: "Upload and apply",
    processingLabel: "Merging IDs, DOBs responses and rechecking…",
    filePickersHtml: filePickerMarkup("stage2FileInput", "stage2FileZone", "stage2FileName", "Select Naresh's response file", "ID Corrections + DOB Corrections sheets"),
    wireFilePickers: () => wireFilePicker("stage2FileInput", "stage2FileZone", "stage2FileName"),
    buildFormData: () => {
      const file = document.getElementById("stage2FileInput").files[0];
      if (!file) throw new Error("Choose Naresh's response file first");
      const fd = new FormData();
      fd.append("file", file);
      return fd;
    },
  });
}

function renderStage3Page() {
  renderStageIntakePage({
    title: "Stage 3 — Haider's response",
    description: "Upload all 3 files together. Only Stage 2 Dispatch jobs are listed below.",
    descriptionKey: "stage3",
    pickerStageId: "stage2_dispatch",
    scopeStage: 3,
    path: "/stage3/haider-response",
    submitLabel: "Upload and apply",
    processingLabel: "Merging CMS data and stage 2 corrections…",
    // Grouped and numbered (not 3 identical stacked boxes) so it reads as
    // one 3-part handoff instead of three unrelated uploads -- each row also
    // gets its own icon (phone / card / person) instead of a repeated
    // generic file icon, to make the 3 files tell apart at a glance.
    filePickersHtml: `
      <div class="upload-file-group">
        <div class="upload-file-row">
          <span class="upload-file-step">1</span>
          ${filePickerMarkup("stage3MobileFileInput", "stage3MobileFileZone", "stage3MobileFileName", "CMS Mobile Numbers export", "CSV, XLSX, or XLS", "smartphone")}
        </div>
        <div class="upload-file-row">
          <span class="upload-file-step">2</span>
          ${filePickerMarkup("stage3CardFileInput", "stage3CardFileZone", "stage3CardFileName", "CMS Card Details export", "CSV, XLSX, or XLS", "idCard")}
        </div>
        <div class="upload-file-row">
          <span class="upload-file-step">3</span>
          ${filePickerMarkup("stage3HaiderFileInput", "stage3HaiderFileZone", "stage3HaiderFileName", "Haider's corrected file", "Name/ID/DOB workbook", "userCheck")}
        </div>
      </div>
    `,
    wireFilePickers: () => {
      wireFilePicker("stage3MobileFileInput", "stage3MobileFileZone", "stage3MobileFileName");
      wireFilePicker("stage3CardFileInput", "stage3CardFileZone", "stage3CardFileName");
      wireFilePicker("stage3HaiderFileInput", "stage3HaiderFileZone", "stage3HaiderFileName");
    },
    buildFormData: () => {
      const mobileFile = document.getElementById("stage3MobileFileInput").files[0];
      const cardFile = document.getElementById("stage3CardFileInput").files[0];
      const haiderFile = document.getElementById("stage3HaiderFileInput").files[0];
      if (!mobileFile) throw new Error("Choose the CMS Mobile Numbers file first");
      if (!cardFile) throw new Error("Choose the CMS Card Details file first");
      if (!haiderFile) throw new Error("Choose Haider's corrected file first");
      const fd = new FormData();
      fd.append("cms_mobile_file", mobileFile);
      fd.append("cms_card_file", cardFile);
      fd.append("haider_corrections_file", haiderFile);
      return fd;
    },
  });
}

// Reopening any job from the dashboard's job list scopes the sidebar to
// whichever stage it's currently sitting in (done jobs land on Stage 3,
// since that's where "done" itself lives) -- stage is meant to be a hard
// boundary everywhere, not just on the Naresh/Haider intake pages.
function resumeJob(jobIdToResume, resumeStage) {
  if (isBusy || !jobIdToResume) return;
  jobId = jobIdToResume;
  localStorage.setItem("k2_job_id", jobId);
  knownHistoryCount = null;
  setViewScope(resumeStage || null);
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

// A job's own "stage" tag tells you which of the 3 self-contained stages
// it's currently stuck in without having to read/parse the specific stage
// title -- e.g. "Stage 2" at a glance, instead of having to know that
// "Stage 2 Dispatch" belongs to Stage 2. Blank once a job is fully done --
// by then it isn't stuck anywhere.
function jobStageCellMarkup(job) {
  if (job.is_done || !job.stage) return "—";
  return `Stage ${job.stage}`;
}

// What "download" means for a job depends on how far it's gotten: the most
// recently frozen artifact, one step behind wherever it currently sits (the
// in-progress screens for stage1/stage2_dispatch already offer their own
// direct download buttons for the CURRENT stage's files -- this is for
// getting back whatever came before that, which otherwise has no other way
// to be recovered once the job has moved on. See handoff.py's
// _download_frozen_dispatch_file and store.py's raw_upload persistence).
function jobDownloadTargets(job) {
  const base = `${API}/jobs/${job.job_id}`;
  if (job.is_done) return { label: "Final output", urls: [`${base}/download`] };
  if (job.stage === 3) return { label: "Stage 2 files", urls: [`${base}/stage2/haider.xlsx`] };
  if (job.stage === 2) return { label: "Stage 1 files", urls: [`${base}/stage1/haider.xlsx`, `${base}/stage1/naresh.xlsx`] };
  return { label: "Raw upload", urls: [`${base}/raw-upload`] };
}

// Fires one browser download per URL from a single click. Not zipped -- a
// job history row can point at Stage 1's two separate reviewer-addressed
// files (Haider's, Naresh's), and collapsing those into one archive would
// hide a distinction the operator actually needs to see. Staggered slightly
// since firing multiple downloads in the same tick makes some browsers
// treat it like a popup flood and silently block everything after the first.
function triggerStaggeredDownloads(urls) {
  urls.forEach((url, i) => {
    setTimeout(() => {
      const a = document.createElement("a");
      a.href = url;
      a.rel = "noopener";
      document.body.appendChild(a);
      a.click();
      a.remove();
    }, i * 400);
  });
}

function jobHistoryRowMarkup(job) {
  const rowCount = job.row_count != null ? Number(job.row_count).toLocaleString() : "—";
  const downloadTargets = jobDownloadTargets(job);
  const downloadCell = `<button type="button" class="job-history-download" data-download-urls="${escapeHtml(JSON.stringify(downloadTargets.urls))}">${escapeHtml(downloadTargets.label)}</button>`;
  return `<tr class="job-history-row" data-resume-job="${escapeHtml(job.job_id)}" data-resume-stage="${job.stage ?? ""}" tabindex="0">
    <td class="mono">${escapeHtml(job.job_id)}</td>
    <td title="${escapeHtml(job.filename || "")}">${escapeHtml(job.filename || "")}</td>
    <td>${rowCount}</td>
    <td>${jobStageCellMarkup(job)}</td>
    <td>${jobStatusPillMarkup(job)}</td>
    <td>${downloadCell}</td>
  </tr>`;
}

let allJobsHistory = []; // full unfiltered list from the server, re-filtered client-side on every keystroke/change

// Single coarse bucket per job: which of Stage 1/2/3 it's sitting in, or
// "done" once it's finished -- replaces the old separate status/stage
// filters (processing vs in-progress was too fine-grained to be useful
// alongside a stage picker that already narrows things down).
function jobFilterBucket(job) {
  if (job.is_done) return "done";
  return String(job.stage ?? "");
}

function filteredJobHistory() {
  const query = (document.getElementById("jobSearchInput")?.value || "").trim().toLowerCase();
  const stageFilter = document.getElementById("jobStageFilter")?.value || "";
  return allJobsHistory.filter((job) => {
    if (stageFilter && jobFilterBucket(job) !== stageFilter) return false;
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
      <thead><tr><th>Job</th><th>File</th><th>Rows</th><th>Stage</th><th>Status</th><th>Downloads</th></tr></thead>
      <tbody>${bodyHtml}</tbody>
    </table>
  `;
  wrap.querySelectorAll(".job-history-download").forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.stopPropagation();
      triggerStaggeredDownloads(JSON.parse(btn.dataset.downloadUrls || "[]"));
    });
  });
  wrap.querySelectorAll("[data-resume-job]").forEach((row) => {
    const resumeStage = row.dataset.resumeStage ? Number(row.dataset.resumeStage) : null;
    row.addEventListener("click", () => resumeJob(row.dataset.resumeJob, resumeStage));
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); resumeJob(row.dataset.resumeJob, resumeStage); }
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
  if (current.type === "stage1" || current.type === "stage2") return renderStageWaitScreen(current);
  if (current.type === "historical_warning") return renderHistoricalWarningStage(current);
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
  const description = STAGE_DETAILS_VERBOSE
    ? [
        overwriteCount ? `Updates ${overwriteCount} field(s)` : null,
        guidance.duplicate_handling,
      ].filter(Boolean).join(" — ")
    : "";
  setCard(`
    <div class="stage-intro">
      <h2>${escapeHtml(current.title)}</h2>
      <p class="muted">${escapeHtml(current.label)}</p>
    </div>
    <div class="upload-simple-layout">
      ${description ? `<p class="muted">${escapeHtml(description)}</p>` : ""}
      ${filePickerMarkup("refFileInput", "referenceFileZone", "referenceFileName", `Select ${expectedFile.toLowerCase()}`, "CSV, XLSX, or XLS")}
    </div>
    <div class="row-actions">
      <button id="uploadBtn">${iconMarkup("upload")}<span>Upload and continue</span></button>
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
}

function renderHistoricalWarningStage(current) {
  // Seeding here re-runs the SAME /continue-historical resume path as
  // continuing without data -- background.py re-executes the "replace"
  // stage's handler on resume regardless, so if historical.db now has data
  // by the time Continue is clicked, the real override applies instead of
  // being skipped. No separate "seed and retry" endpoint needed.
  const goOn = () => runAction(`/jobs/${jobId}/continue-historical`, { method: "POST" }, "Continuing…");

  const paint = (message, continueLabel) => {
    setCard(`
      <div class="stage-intro">
        <h2>${escapeHtml(current.title)}</h2>
      </div>
      <div class="decision-panel">
        <div class="decision-info">
          <div class="decision-info-row">
            <span class="guidance-icon guidance-icon-art">${detailIconMarkup("identity", "guidance-detail-icon")}</span>
            <p class="decision-copy">${escapeHtml(message)}</p>
          </div>
        </div>
        <details class="audit-preview" id="historicalWarnSeedPanel">
          <summary>${iconMarkup("database")}<span>Seed historical data now instead</span></summary>
          <div class="historical-panel-body">
            ${historicalSeedPanelMarkup("historicalWarn")}
          </div>
        </details>
        <div class="row-actions">
          <button id="continueHistoricalBtn">${iconMarkup("check")}<span>${continueLabel}</span></button>
        </div>
      </div>
    `);
    document.getElementById("continueHistoricalBtn").onclick = goOn;
    wireHistoricalSeedPanel("historicalWarn", (status) => {
      if (status.seeded) {
        paint(
          `Seeded ${(status.row_count || 0).toLocaleString()} row(s). Click Continue to run the historical override using this data.`,
          "Continue (will use the data just seeded)",
        );
      }
    });
  };

  paint(current.message, "Continue without historical data");
}

function renderConfirmStage(current) {
  setCard(`
    <div class="stage-intro">
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

// Stage 1 and Stage 2 dispatch are both "download and wait" screens inside
// the normal job wizard -- advancing only happens externally, from the
// separate Stage 2 / Stage 3 intake pages (renderStage2Page / renderStage3Page
// above). The "Next" button is just a navigation shortcut to that page -- it
// doesn't advance the job itself, only uploading a response there does.
function renderStageWaitScreen(current) {
  const isStage1 = current.type === "stage1";
  const downloads = isStage1
    ? [
        { label: "Download Haider's Stage 1 file", href: `${API}/jobs/${jobId}/stage1/haider.xlsx`, summaryPath: `/jobs/${jobId}/stage1/haider.xlsx/summary` },
        { label: "Download Naresh's Stage 1 file", href: `${API}/jobs/${jobId}/stage1/naresh.xlsx`, summaryPath: `/jobs/${jobId}/stage1/naresh.xlsx/summary` },
      ]
    : [{ label: "Download Stage 2 file (for Haider)", href: `${API}/jobs/${jobId}/stage2/haider.xlsx`, summaryPath: `/jobs/${jobId}/stage2/haider.xlsx/summary` }];
  const stillInvalid = (current.invalid_id_count || 0) + (current.invalid_dob_count || 0) + (current.invalid_name_count || 0);
  const invalidNote = !STAGE_DETAILS_VERBOSE || isStage1
    ? ""
    : stillInvalid > 0
      ? `<p class="muted">${current.invalid_name_count} account(s) still have an invalid name, ${current.invalid_id_count} an invalid ID, and ${current.invalid_dob_count} an invalid DOB — that's what's in the Stage 2 file above.</p>`
      : `<p class="muted">No names, IDs, or DOBs are currently invalid — Haider doesn't need a corrections file for this job on Stage 3, just the two CMS exports.</p>`;
  const nextLabel = isStage1 ? "Next: Stage 2" : "Next: Stage 3";
  setCard(`
    <div class="stage-intro upload-stage-intro">
      <h2>${escapeHtml(current.title)}</h2>
      ${invalidNote}
    </div>
    ${downloads.map((d, i) => `
      <div class="row-actions">
        <button class="secondary" id="stageDownloadBtn${i}" type="button">${iconMarkup("download")}<span>${escapeHtml(d.label)}</span></button>
      </div>
      ${workbookSummaryMarkup(`stageDownloadSummary${i}`)}
    `).join("")}
    <div class="row-actions">
      <button id="stageNextBtn" type="button">${iconMarkup("play")}<span>${nextLabel}</span></button>
    </div>
  `);
  downloads.forEach((d, i) => {
    document.getElementById(`stageDownloadBtn${i}`).onclick = () => { window.location.href = d.href; };
    loadWorkbookSummary(`stageDownloadSummary${i}`, d.summaryPath);
  });
  document.getElementById("stageNextBtn").onclick = () => (isStage1 ? renderStage2Page() : renderStage3Page());
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
      <button id="updateHistoricalBtn" type="button">Update historical data</button>
    </div>
    ${workbookSummaryMarkup("finalDownloadSummary")}
    <div class="row-actions">
      <button class="secondary" id="downloadAuditBtn">Download audit report (${(current.audit_event_count || 0).toLocaleString()})</button>
    </div>
    ${workbookSummaryMarkup("auditDownloadSummary")}
  `);
  document.getElementById("downloadBtn").onclick = () => {
    window.location.href = `${API}/jobs/${jobId}/download`;
  };
  document.getElementById("downloadAuditBtn").onclick = () => {
    window.location.href = `${API}/jobs/${jobId}/audit/download`;
  };
  loadWorkbookSummary("finalDownloadSummary", `/jobs/${jobId}/download/summary`);
  loadWorkbookSummary("auditDownloadSummary", `/jobs/${jobId}/audit/download/summary`);
  document.getElementById("updateHistoricalBtn").onclick = async () => {
    if (isBusy) return;
    if (!window.confirm("Add this job's accounts into the historical SQL store? Existing entries for matching accounts will be refreshed, not duplicated.")) return;
    isBusy = true;
    lockAllControls(true);
    try {
      const result = await api(`/jobs/${jobId}/update-historical`, { method: "POST" });
      toast(`Historical store updated (${result.updated_rows.toLocaleString()} account(s))`);
    } catch (e) {
      toast(e.message, "error");
    } finally {
      isBusy = false;
      lockAllControls(false);
    }
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
      <div class="stage-title-row">
        <h2>${escapeHtml(current.title)}</h2>
        <span class="badge">${current.total_flagged} flagged</span>
      </div>
      <p class="muted">${escapeHtml(STAGE_DETAILS_VERBOSE ? current.instructions : "These accounts are flagged for review. Download the workbook below, fix them, and upload it back here.")}</p>
    </div>
    <div class="review-toolbar">
      <div class="review-stat"><span class="review-stat-label">Page</span><span class="review-stat-value">${current.page} of ${current.page_count}</span></div>
      <div class="review-stat"><span class="review-stat-label">Visible now</span><span class="review-stat-value">${current.showing} records</span></div>
      <div class="review-stat"><span class="review-stat-label">Rows remaining</span><span class="review-stat-value">${current.rows_remaining} records</span></div>
      ${current.manual_edit_enabled ? `<div class="review-stat"><span class="review-stat-label">Edited</span><span class="review-stat-value" id="editedRowCount">0 rows</span></div>` : ""}
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
      ${current.manual_edit_enabled ? `
      <div class="guidance-card">
        <span class="guidance-icon guidance-icon-art">${detailIconMarkup("export", "guidance-detail-icon")}</span>
        <div><dt>Prefer Excel?</dt><dd>Download the review workbook (one sheet per manual stage reached so far), fill it in, and upload it back here instead of editing inline.</dd></div>
      </div>` : ""}
      <div class="workbook-panel-actions">
        <button class="secondary" id="downloadWorkbookBtn" type="button">${iconMarkup("download")}<span>Download review workbook</span></button>
        ${workbookSummaryMarkup("workbookDownloadSummary")}
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
  loadWorkbookSummary("workbookDownloadSummary", `/jobs/${jobId}/workbook/summary`);
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

// ── Theme Toggle (light / dark) ──
// Lives in the main header bar (main-context-actions) rather than the
// sidebar, since the sidebar itself is hidden on the Dashboard / New Batch /
// Stage 2-3 intake screens (see setSidebarJobChrome) -- the header bar is
// the one chrome element rendered on every screen, so that's where the
// toggle needs to be to stay reachable everywhere.
function setTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  localStorage.setItem("k2_theme", theme);
  if (themeToggleEl) {
    themeToggleEl.setAttribute("aria-label", theme === "light" ? "Switch to dark mode" : "Switch to light mode");
    themeToggleEl.setAttribute("title", theme === "light" ? "Switch to dark mode" : "Switch to light mode");
    themeToggleEl.innerHTML = theme === "light"
      ? `<svg class="theme-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>`
      : `<svg class="theme-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>`;
  }
}

// Dark stays the default -- matches old's only look today, so anyone who
// hasn't touched the toggle yet sees exactly what they saw before this change.
const savedTheme = localStorage.getItem("k2_theme") || "dark";
setTheme(savedTheme);

if (themeToggleEl) {
  themeToggleEl.onclick = () => {
    const current = document.documentElement.getAttribute("data-theme") || "dark";
    setTheme(current === "dark" ? "light" : "dark");
  };
}

refresh();
