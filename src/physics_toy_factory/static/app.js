"use strict";

const MAX_DETAIL_CHARS = 240;
const GRAPH_DEBOUNCE_MS = 60;
const RUN_SKILL_LABELS = {
  answer_with_evidence: "Final answer",
  create_file: "Write file",
  edit_code: "Edit code",
  glob_files: "Find files",
  read_code: "Read file",
  run_command: "Run command",
};
const RUN_STATUS_ORDER = ["running", "pending", "succeeded", "failed", "cancelled", "blocked", "unknown"];

const elements = {
  app: document.querySelector("#app"),
  stateLabel: document.querySelector("#state-label"),
  stageOverline: document.querySelector("#stage-overline"),
  stageTitle: document.querySelector("#stage-title"),
  stageDescription: document.querySelector("#stage-description"),
  simulationStage: document.querySelector("#simulation-stage"),
  previewHost: document.querySelector("#preview-host"),
  previewStatus: document.querySelector("#preview-status"),
  previewStatusText: document.querySelector("#preview-status-text"),
  stageLockText: document.querySelector("#stage-lock-text"),
  headerRunStatus: document.querySelector("#header-run-status"),
  telemetryCage: document.querySelector("#telemetry-cage"),
  telemetryRevision: document.querySelector("#telemetry-revision"),
  telemetryRun: document.querySelector("#telemetry-run"),
  telemetrySequence: document.querySelector("#telemetry-sequence"),
  telemetryWatchdog: document.querySelector("#telemetry-watchdog"),
  systemBanner: document.querySelector("#system-banner"),
  systemBannerText: document.querySelector("#system-banner-text"),
  transportState: document.querySelector("#transport-state"),
  activityList: document.querySelector("#activity-list"),
  activityEmpty: document.querySelector("#activity-empty"),
  evidenceCount: document.querySelector("#evidence-count"),
  form: document.querySelector("#create-form"),
  prompt: document.querySelector("#prompt"),
  promptCount: document.querySelector("#prompt-count"),
  createButton: document.querySelector("#create-button"),
  suggestionList: document.querySelector("#suggestion-list"),
  formError: document.querySelector("#form-error"),
  followUpPanel: document.querySelector("#follow-up-panel"),
  followUpForm: document.querySelector("#follow-up-form"),
  followUpPrompt: document.querySelector("#follow-up-prompt"),
  followUpCount: document.querySelector("#follow-up-count"),
  followUpButton: document.querySelector("#follow-up-button"),
  followUpError: document.querySelector("#follow-up-error"),
  savedRuns: document.querySelector("#saved-runs"),
  savedCount: document.querySelector("#saved-count"),
  viewCode: document.querySelector("#view-code"),
  viewRun: document.querySelector("#view-run"),
  reset: document.querySelector("#reset-session"),
  codeDialog: document.querySelector("#code-dialog"),
  codeMeta: document.querySelector("#code-meta"),
  codeContent: document.querySelector("#code-content"),
  runDialog: document.querySelector("#run-dialog"),
  runMeta: document.querySelector("#run-meta"),
  runTabs: Array.from(document.querySelectorAll("[data-run-tab]")),
  runPanels: {
    overview: document.querySelector("#run-panel-overview"),
    graph: document.querySelector("#run-panel-graph"),
    raw: document.querySelector("#run-panel-raw"),
  },
  runOverview: document.querySelector("#run-overview"),
  runGraphNote: document.querySelector("#run-graph-note"),
  runOrderToggle: document.querySelector("#run-order-toggle"),
  runOrderLegend: document.querySelector("#run-order-legend"),
  runGraphScroll: document.querySelector("#run-graph-scroll"),
  runGraphCanvas: document.querySelector("#run-graph-canvas"),
  runGraphEdges: document.querySelector("#run-graph-edges"),
  runGraphNodes: document.querySelector("#run-graph-nodes"),
  runInspector: document.querySelector("#run-inspector"),
  runContent: document.querySelector("#run-content"),
  historyDialog: document.querySelector("#history-dialog"),
  historySearch: document.querySelector("#history-search"),
  historyQuery: document.querySelector("#history-query"),
  historyStatus: document.querySelector("#history-status"),
  historyList: document.querySelector("#history-list"),
  historyMore: document.querySelector("#history-more"),
  historyDetail: document.querySelector("#history-detail"),
};

const state = {
  session: null,
  healthReady: false,
  runId: null,
  latestSequence: null,
  eventSource: null,
  eventQueue: Promise.resolve(),
  seen: new Set(),
  graph: null,
  nodeCache: new Map(),
  graphRefresh: null,
  graphTimer: null,
  checkerFailed: false,
  terminal: false,
  reconnecting: false,
  evidenceRows: 0,
  previewFrame: null,
  previewId: null,
  previewRunId: null,
  previewWatchdog: null,
  previewTerminal: false,
  runKind: null,
  followUpSubmitting: false,
  runActiveTab: "overview",
  runGraphModel: null,
  runSelectedNodeId: null,
  runGraphFrame: null,
  runOrderVisible: false,
  historyItems: new Map(),
  historyCursor: null,
  historyTotal: 0,
  historySelectedId: null,
  historyPreviewFrame: null,
  historyPreviewId: null,
  historyPreviewWatchdog: null,
  historyPreviewTerminal: false,
};

const MODE_COPY = {
  landing: {
    label: "Ready",
    overline: "Factory standing by",
    title: "Your physics toy will take shape here.",
    description: "Start with a prompt below. Only a checker-verified sketch can enter the preview cage.",
  },
  connecting: {
    label: "Connecting",
    overline: "Opening the journal",
    title: "Connecting to the factory floor.",
    description: "The product is locating the current session and its real run evidence.",
  },
  running: {
    label: "Building",
    overline: "Agent run in progress",
    title: "The workshop is in motion.",
    description: "Follow the journal-backed activity at right while the sketch is written and checked.",
  },
  modifying: {
    label: "Modifying",
    overline: "Linked run in progress",
    title: "The verified toy is being refined.",
    description: "The old preview is closed while the agent reads, anchor-edits, and checks the sketch again.",
  },
  ready: {
    label: "Verified",
    overline: "Checker passed",
    title: "Opening the verified preview cage.",
    description: "The exact passing sketch is loading under the local sandbox and nonce policy.",
  },
  failed: {
    label: "Stopped",
    overline: "Run needs attention",
    title: "This build did not become ready.",
    description: "Review the real activity and raw run evidence, then reset the factory before trying again.",
  },
  reconnecting: {
    label: "Reconnecting",
    overline: "Journal link interrupted",
    title: "Keeping the evidence we already received.",
    description: "The browser is reconnecting from its last real event sequence; no progress is being invented.",
  },
  degraded: {
    label: "Degraded",
    overline: "Factory unavailable",
    title: "The workshop is not ready to build.",
    description: "Check the product health details. Creation remains disabled until the trusted workspace and S17 are ready.",
  },
};

function setMode(mode) {
  const copy = MODE_COPY[mode] || MODE_COPY.failed;
  elements.app.dataset.state = mode;
  elements.stateLabel.textContent = copy.label;
  elements.stageOverline.textContent = copy.overline;
  elements.stageTitle.textContent = copy.title;
  elements.stageDescription.textContent = copy.description;
  updateTelemetry();
  updateControls();
}

function compactIdentifier(value, visible = 12) {
  if (typeof value !== "string" || !value) {
    return "—";
  }
  return value.length > visible ? `${value.slice(0, visible)}…` : value;
}

function updateTelemetry() {
  const previewState = elements.simulationStage.dataset.previewState || "locked";
  const cageLabels = {
    locked: "Locked",
    loading: "Opening",
    ready: "Active",
    error: "Stopped",
    timeout: "Stopped",
  };
  const watchdogLabels = {
    locked: "Idle",
    loading: "Armed",
    ready: "Passed",
    error: "Stopped",
    timeout: "Stopped",
  };
  const run = compactIdentifier(state.runId);
  const sequence = Number.isInteger(state.latestSequence) ? String(state.latestSequence) : "—";
  elements.telemetryCage.textContent = cageLabels[previewState] || "—";
  elements.telemetryRevision.textContent = compactIdentifier(state.session?.current_sketch_sha256, 10);
  elements.telemetryRun.textContent = run;
  elements.telemetrySequence.textContent = sequence;
  elements.telemetryWatchdog.textContent = watchdogLabels[previewState] || "—";
  elements.headerRunStatus.hidden = !state.runId;
  elements.headerRunStatus.textContent = state.runId ? `RUN ${run} · SEQ ${sequence}` : "";
}

function activeRun() {
  return Boolean(state.session && state.session.active_run_id);
}

function updateControls() {
  const mode = elements.app.dataset.state;
  const mutating = ["connecting", "running", "modifying", "reconnecting"].includes(mode) || activeRun();
  const canCreate = mode === "landing" && state.healthReady && state.session?.state === "empty";
  const canFollowUp = mode === "ready"
    && state.session?.state === "ready"
    && state.session?.follow_up_used === false
    && !state.followUpSubmitting
    && !activeRun();
  elements.createButton.disabled = !canCreate;
  elements.prompt.disabled = !canCreate;
  elements.followUpPanel.hidden = !canFollowUp;
  elements.followUpPrompt.disabled = !canFollowUp;
  elements.followUpButton.disabled = !canFollowUp;
  elements.reset.disabled = mutating || !state.session || state.session.state === "empty";
  elements.viewRun.disabled = !state.runId;
  elements.viewCode.disabled = !state.runId;
  for (const button of elements.suggestionList.querySelectorAll("button")) {
    button.disabled = !canCreate;
  }
}

function showBanner(message) {
  elements.systemBannerText.textContent = message;
  elements.systemBanner.hidden = false;
}

function hideBanner() {
  elements.systemBanner.hidden = true;
  elements.systemBannerText.textContent = "";
}

function clearPreviewWatchdog() {
  if (state.previewWatchdog !== null) {
    window.clearTimeout(state.previewWatchdog);
    state.previewWatchdog = null;
  }
}

function destroyPreview() {
  clearPreviewWatchdog();
  state.previewFrame?.remove();
  state.previewFrame = null;
  state.previewId = null;
  state.previewRunId = null;
  state.previewTerminal = false;
  elements.previewHost.replaceChildren();
  elements.previewHost.hidden = true;
}

function setPreviewState(previewState, message = "") {
  elements.simulationStage.dataset.previewState = previewState;
  elements.previewStatusText.textContent = message;
  elements.previewStatus.hidden = !message;
  if (previewState === "ready") {
    elements.stageLockText.textContent = "Verified cage active";
  } else if (["error", "timeout"].includes(previewState)) {
    elements.stageLockText.textContent = "Preview cage stopped";
  } else if (previewState === "loading") {
    elements.stageLockText.textContent = "Opening verified cage";
  } else {
    elements.stageLockText.textContent = "Preview cage locked";
  }
  updateTelemetry();
}

function exactKeys(value, expected) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const actual = Object.keys(value).sort();
  const wanted = [...expected].sort();
  return actual.length === wanted.length && actual.every((key, index) => key === wanted[index]);
}

function validPreviewMessage(value) {
  if (exactKeys(value, ["type", "preview_id"])) {
    return value.type === "preview_ready"
      && typeof value.preview_id === "string"
      && value.preview_id === state.previewId;
  }
  if (!exactKeys(value, ["type", "preview_id", "name", "message", "line", "column"])) {
    return false;
  }
  return value.type === "preview_error"
    && typeof value.preview_id === "string"
    && value.preview_id === state.previewId
    && typeof value.name === "string"
    && value.name.length >= 1
    && value.name.length <= 100
    && typeof value.message === "string"
    && value.message.length >= 1
    && value.message.length <= 500
    && Number.isInteger(value.line)
    && value.line >= 0
    && value.line <= 1000000
    && Number.isInteger(value.column)
    && value.column >= 0
    && value.column <= 1000000;
}

async function reportPreviewFailure(error) {
  if (!state.previewRunId || !state.previewId) {
    return;
  }
  const runId = state.previewRunId;
  const payload = {
    preview_id: state.previewId,
    name: boundedDetail(error.name || "Error").slice(0, 100) || "Error",
    message: boundedDetail(error.message || "Unknown preview error").slice(0, 500),
    line: Number.isInteger(error.line) ? error.line : 0,
    column: Number.isInteger(error.column) ? error.column : 0,
  };
  const response = await requestJson(`/api/runs/${encodeURIComponent(runId)}/browser-error`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  state.session = response.session;
}

function showPreviewFailure(message, previewState = "error") {
  setMode("failed");
  elements.stageOverline.textContent = "Browser cage stopped";
  elements.stageTitle.textContent = "The verified sketch failed in the browser.";
  elements.stageDescription.textContent = message;
  setPreviewState(previewState, previewState === "timeout" ? "Preview timeout" : "Runtime error");
  showBanner(message);
}

async function failPreview(error, previewState = "error") {
  if (state.previewTerminal) {
    return;
  }
  state.previewTerminal = true;
  clearPreviewWatchdog();
  const message = previewState === "timeout"
    ? "Preview did not become responsive."
    : boundedDetail(`${error.name || "Error"}: ${error.message || "Unknown preview error"}`);
  try {
    await reportPreviewFailure(error);
  } catch (_reportError) {
    showBanner("The preview stopped, but its failure could not be recorded.");
  }
  state.previewFrame?.remove();
  state.previewFrame = null;
  elements.previewHost.replaceChildren();
  elements.previewHost.hidden = true;
  showPreviewFailure(message, previewState);
  updateControls();
}

function handlePreviewMessage(event) {
  if (handleHistoryPreviewMessage(event)) {
    return;
  }
  if (!state.previewFrame || event.source !== state.previewFrame.contentWindow) {
    return;
  }
  if (!validPreviewMessage(event.data) || state.previewTerminal) {
    return;
  }
  if (event.data.type === "preview_ready") {
    clearPreviewWatchdog();
    setPreviewState("ready", "Verified preview live");
    elements.stageOverline.textContent = "Sandbox responsive";
    elements.stageTitle.textContent = "Your verified physics toy is live.";
    elements.stageDescription.textContent = "The sketch is running inside the constrained preview cage.";
    return;
  }
  void failPreview(event.data);
}

async function mountPreview() {
  destroyPreview();
  setPreviewState("loading", "Checking verified revision");
  const revision = state.session?.current_sketch_sha256;
  if (typeof revision !== "string") {
    showPreviewFailure("No verified sketch revision is available.");
    return;
  }
  try {
    const lease = await requestJson("/api/preview", {
      method: "POST",
      body: JSON.stringify({revision}),
    });
    if (
      !lease
      || typeof lease.preview_id !== "string"
      || typeof lease.run_id !== "string"
      || lease.revision !== revision
      || typeof lease.url !== "string"
      || !Number.isInteger(lease.ready_timeout_ms)
      || lease.ready_timeout_ms < 1
    ) {
      throw new Error("The preview lease response was invalid.");
    }
    state.previewId = lease.preview_id;
    state.previewRunId = lease.run_id;
    state.previewTerminal = false;
    const frame = document.createElement("iframe");
    frame.title = "Verified physics toy preview";
    frame.setAttribute("sandbox", "allow-scripts");
    frame.referrerPolicy = "no-referrer";
    state.previewFrame = frame;
    elements.previewHost.hidden = false;
    elements.previewHost.replaceChildren(frame);
    setPreviewState("loading", "Starting verified cage");
    state.previewWatchdog = window.setTimeout(() => {
      void failPreview(
        {name: "PreviewTimeout", message: "Preview did not become responsive.", line: 0, column: 0},
        "timeout",
      );
    }, lease.ready_timeout_ms);
    frame.src = lease.url;
  } catch (error) {
    destroyPreview();
    showPreviewFailure(error.message || "The verified preview could not be opened.");
  }
}

function showFormError(message) {
  elements.formError.textContent = message;
  elements.formError.hidden = false;
}

function clearFormError() {
  elements.formError.textContent = "";
  elements.formError.hidden = true;
}

function showFollowUpError(message) {
  elements.followUpError.textContent = message;
  elements.followUpError.hidden = false;
}

function clearFollowUpError() {
  elements.followUpError.textContent = "";
  elements.followUpError.hidden = true;
}

function boundedDetail(value) {
  if (typeof value !== "string") {
    return "";
  }
  const normalized = value.replace(/\s+/g, " ").trim();
  return normalized.length > MAX_DETAIL_CHARS
    ? `${normalized.slice(0, MAX_DETAIL_CHARS - 1)}…`
    : normalized;
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {"Content-Type": "application/json", ...(options.headers || {})},
  });
  let payload = null;
  try {
    payload = await response.json();
  } catch (_error) {
    // The stable generic error below intentionally hides unrecognized bodies.
  }
  if (!response.ok) {
    const error = new Error(payload?.error?.message || "The factory returned an unrecognized error.");
    error.code = payload?.error?.code || "request_failed";
    throw error;
  }
  return payload;
}

function updateSavedCount(total) {
  const safeTotal = Number.isInteger(total) && total >= 0 ? total : 0;
  state.historyTotal = safeTotal;
  elements.savedCount.textContent = safeTotal > 999 ? "999+" : String(safeTotal);
  elements.savedCount.setAttribute(
    "aria-label",
    `${safeTotal} saved ${safeTotal === 1 ? "run" : "runs"}`,
  );
}

async function refreshSavedCount() {
  try {
    const payload = await requestJson("/api/history?limit=1");
    updateSavedCount(payload?.total);
  } catch (_error) {
    elements.savedCount.textContent = "—";
    elements.savedCount.setAttribute("aria-label", "Saved run count unavailable");
  }
}

function historyOutcomeLabel(outcome) {
  const labels = {running: "In progress", ready: "Verified", failed: "Stopped"};
  return labels[outcome] || "Unavailable";
}

function historyKindLabel(kind) {
  return kind === "follow_up" ? "Follow-up" : kind === "create" ? "Creation" : "Run";
}

function historyDate(value) {
  if (typeof value !== "string") {
    return "—";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "—";
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(parsed);
}

function currentSessionOwnsRun(runId) {
  return Array.isArray(state.session?.runs)
    && state.session.runs.some((run) => run?.run_id === runId);
}

function makeHistoryItem(item) {
  const row = document.createElement("li");
  const button = document.createElement("button");
  button.type = "button";
  button.className = "history-item";
  button.dataset.historyId = item.history_id;
  button.setAttribute("aria-current", item.history_id === state.historySelectedId ? "true" : "false");

  const head = document.createElement("span");
  head.className = "history-item-head";
  head.append(textElement("span", "history-kind", historyKindLabel(item.kind)));
  const outcome = textElement("span", "history-outcome", historyOutcomeLabel(item.outcome));
  outcome.dataset.outcome = item.outcome || "unknown";
  head.append(outcome);
  button.append(head);
  button.append(textElement("span", "history-prompt", item.user_prompt || "Prompt unavailable"));
  const foot = document.createElement("span");
  foot.className = "history-item-foot";
  foot.append(textElement("span", "", historyDate(item.started_at)));
  foot.append(textElement("span", "", compactIdentifier(item.run_id, 14)));
  button.append(foot);
  button.addEventListener("click", () => selectHistoryRun(item.history_id));
  row.append(button);
  return row;
}

function renderHistoryList() {
  clearElement(elements.historyList);
  const items = Array.from(state.historyItems.values());
  if (!items.length) {
    const empty = document.createElement("li");
    empty.className = "history-empty";
    empty.append(textElement("p", "panel-kicker", "No saved runs"));
    empty.append(textElement("p", "", "Future factory runs will be saved here automatically."));
    elements.historyList.append(empty);
    return;
  }
  for (const item of items) {
    elements.historyList.append(makeHistoryItem(item));
  }
}

function resetHistoryDetail(message = "Select a saved run") {
  destroyHistoryPreview();
  clearElement(elements.historyDetail);
  elements.historyDetail.scrollTop = 0;
  const empty = document.createElement("div");
  empty.className = "history-empty";
  empty.append(textElement("p", "panel-kicker", "Read-only archive"));
  empty.append(textElement("h3", "", message));
  empty.append(textElement(
    "p",
    "",
    "Review its prompt, evidence, verified code, and simulation without changing the active workspace.",
  ));
  elements.historyDetail.append(empty);
}

async function loadHistory(reset = false) {
  if (reset) {
    state.historyCursor = null;
    state.historyItems = new Map();
    state.historySelectedId = null;
    resetHistoryDetail();
  }
  elements.historyStatus.textContent = "Loading saved runs…";
  elements.historyMore.disabled = true;
  const params = new URLSearchParams({limit: "20"});
  const query = elements.historyQuery.value.trim();
  if (query) {
    params.set("q", query);
  }
  if (state.historyCursor) {
    params.set("cursor", state.historyCursor);
  }
  try {
    const payload = await requestJson(`/api/history?${params}`);
    for (const item of Array.isArray(payload?.items) ? payload.items : []) {
      if (item && typeof item.history_id === "string") {
        state.historyItems.set(item.history_id, item);
      }
    }
    state.historyCursor = typeof payload?.next_cursor === "string" ? payload.next_cursor : null;
    elements.historyMore.hidden = !state.historyCursor;
    elements.historyMore.disabled = false;
    renderHistoryList();
    const shown = state.historyItems.size;
    const total = Number.isInteger(payload?.total) ? payload.total : shown;
    elements.historyStatus.textContent = query
      ? `${total} matching saved ${total === 1 ? "run" : "runs"}`
      : `${total} saved ${total === 1 ? "run" : "runs"} · local archive`;
    if (!query) {
      updateSavedCount(total);
    }
    if (reset && shown > 0) {
      await selectHistoryRun(state.historyItems.keys().next().value);
    }
  } catch (error) {
    elements.historyStatus.textContent = error.message || "Saved runs could not be loaded.";
    elements.historyMore.hidden = true;
    renderHistoryList();
  }
}

function appendHistoryFact(parent, label, value) {
  const fact = document.createElement("div");
  fact.className = "history-fact";
  fact.append(textElement("dt", "", label));
  fact.append(textElement("dd", "", value ?? "—"));
  parent.append(fact);
}

function openHistoricalEvidence(detail) {
  const graph = detail?.graph;
  if (!graph || typeof graph !== "object") {
    return;
  }
  elements.historyDialog.close();
  state.runSelectedNodeId = null;
  setRunOrderVisible(false);
  setRunTab("overview");
  renderRunEvidence(graph);
  elements.runDialog.showModal();
}

async function openHistoricalCode(historyId) {
  elements.historyStatus.textContent = "Loading archived sketch.js…";
  try {
    const code = await requestJson(`/api/history/${encodeURIComponent(historyId)}/code`);
    elements.historyDialog.close();
    elements.codeMeta.textContent = `${code.bytes} bytes · sha256 ${code.sha256} · saved verified revision`;
    elements.codeContent.textContent = code.content;
    elements.codeDialog.showModal();
  } catch (error) {
    elements.historyStatus.textContent = error.message || "Archived source could not be loaded.";
  }
}

function destroyHistoryPreview() {
  if (state.historyPreviewWatchdog !== null) {
    window.clearTimeout(state.historyPreviewWatchdog);
  }
  state.historyPreviewWatchdog = null;
  state.historyPreviewFrame?.remove();
  state.historyPreviewFrame = null;
  state.historyPreviewId = null;
  state.historyPreviewTerminal = false;
}

function setHistoryPreviewMessage(message) {
  const element = elements.historyDetail.querySelector(".history-preview-message");
  if (element) {
    element.textContent = message;
    element.hidden = !message;
  }
}

function stopHistoryPreview(message) {
  if (state.historyPreviewTerminal) {
    return;
  }
  state.historyPreviewTerminal = true;
  if (state.historyPreviewWatchdog !== null) {
    window.clearTimeout(state.historyPreviewWatchdog);
    state.historyPreviewWatchdog = null;
  }
  state.historyPreviewFrame?.remove();
  state.historyPreviewFrame = null;
  setHistoryPreviewMessage(message);
}

async function mountHistoryPreview(historyId) {
  destroyHistoryPreview();
  const host = elements.historyDetail.querySelector(".history-preview");
  if (!host) {
    return;
  }
  setHistoryPreviewMessage("Opening saved verified preview…");
  try {
    const lease = await requestJson(`/api/history/${encodeURIComponent(historyId)}/preview`, {
      method: "POST",
      body: "{}",
    });
    if (
      !lease
      || lease.history_id !== historyId
      || typeof lease.preview_id !== "string"
      || typeof lease.revision !== "string"
      || typeof lease.url !== "string"
      || !Number.isInteger(lease.ready_timeout_ms)
      || lease.ready_timeout_ms < 1
    ) {
      throw new Error("The saved preview lease response was invalid.");
    }
    state.historyPreviewId = lease.preview_id;
    state.historyPreviewTerminal = false;
    const frame = document.createElement("iframe");
    frame.title = "Saved verified physics toy preview";
    frame.setAttribute("sandbox", "allow-scripts");
    frame.referrerPolicy = "no-referrer";
    state.historyPreviewFrame = frame;
    host.prepend(frame);
    state.historyPreviewWatchdog = window.setTimeout(() => {
      stopHistoryPreview("Saved preview did not become responsive.");
    }, lease.ready_timeout_ms);
    frame.src = lease.url;
  } catch (error) {
    stopHistoryPreview(error.message || "The saved preview could not be opened.");
  }
}

function handleHistoryPreviewMessage(event) {
  if (!state.historyPreviewFrame || event.source !== state.historyPreviewFrame.contentWindow) {
    return false;
  }
  const value = event.data;
  const ready = exactKeys(value, ["type", "preview_id"])
    && value.type === "preview_ready"
    && value.preview_id === state.historyPreviewId;
  const failed = exactKeys(value, ["type", "preview_id", "name", "message", "line", "column"])
    && value.type === "preview_error"
    && value.preview_id === state.historyPreviewId
    && typeof value.name === "string"
    && typeof value.message === "string";
  if ((!ready && !failed) || state.historyPreviewTerminal) {
    return true;
  }
  if (ready) {
    if (state.historyPreviewWatchdog !== null) {
      window.clearTimeout(state.historyPreviewWatchdog);
      state.historyPreviewWatchdog = null;
    }
    setHistoryPreviewMessage("");
  } else {
    stopHistoryPreview(`Saved preview stopped: ${boundedDetail(`${value.name}: ${value.message}`)}`);
  }
  return true;
}

async function deleteHistoryRun(item) {
  const confirmed = window.confirm(
    "Delete this saved run and its local archived sketch? The upstream S17 journal will be retained.",
  );
  if (!confirmed) {
    return;
  }
  elements.historyStatus.textContent = "Deleting saved run…";
  try {
    await requestJson(`/api/history/${encodeURIComponent(item.history_id)}`, {method: "DELETE"});
    state.historyItems.delete(item.history_id);
    state.historySelectedId = null;
    resetHistoryDetail();
    await loadHistory(true);
    await refreshSavedCount();
  } catch (error) {
    elements.historyStatus.textContent = error.message || "The saved run could not be deleted.";
  }
}

function renderHistoryDetail(detail) {
  destroyHistoryPreview();
  const item = detail.history;
  clearElement(elements.historyDetail);
  elements.historyDetail.scrollTop = 0;
  const content = document.createElement("div");
  content.className = "history-detail-content";
  const head = document.createElement("div");
  head.className = "history-detail-head";
  const title = document.createElement("div");
  title.append(textElement("p", "panel-kicker", `${historyKindLabel(item.kind)} · read only`));
  title.append(textElement("h3", "", historyOutcomeLabel(item.outcome)));
  head.append(title);
  const outcome = textElement("span", "history-outcome", historyOutcomeLabel(item.outcome));
  outcome.dataset.outcome = item.outcome || "unknown";
  head.append(outcome);
  content.append(head);
  content.append(textElement("p", "history-detail-run", item.run_id || "Run ID unavailable"));
  content.append(textElement("p", "history-detail-prompt", item.user_prompt || "Prompt unavailable"));

  const facts = document.createElement("dl");
  facts.className = "history-facts";
  appendHistoryFact(facts, "Started", historyDate(item.started_at));
  appendHistoryFact(facts, "Finished", historyDate(item.finished_at));
  appendHistoryFact(facts, "Revision", compactIdentifier(item.verified_sketch_sha256, 16));
  appendHistoryFact(facts, "Parent run", compactIdentifier(item.parent_run_id, 16));
  content.append(facts);

  const actions = document.createElement("div");
  actions.className = "history-actions";
  const preview = textElement("button", "button button-quiet", "Preview");
  preview.type = "button";
  preview.disabled = item.preview_available !== true;
  preview.addEventListener("click", () => mountHistoryPreview(item.history_id));
  const evidence = textElement("button", "button button-quiet", "View evidence");
  evidence.type = "button";
  evidence.disabled = !detail.graph;
  evidence.addEventListener("click", () => openHistoricalEvidence(detail));
  const code = textElement("button", "button button-quiet", "View code");
  code.type = "button";
  code.disabled = item.preview_available !== true;
  code.addEventListener("click", () => openHistoricalCode(item.history_id));
  const remove = textElement("button", "button button-quiet history-delete", "Delete");
  remove.type = "button";
  const current = currentSessionOwnsRun(item.run_id);
  remove.disabled = current;
  remove.title = current ? "Reset the current factory session before deleting this run." : "";
  remove.addEventListener("click", () => deleteHistoryRun(item));
  actions.append(preview, evidence, code, remove);
  content.append(actions);

  if (item.preview_available) {
    const previewHost = document.createElement("div");
    previewHost.className = "history-preview";
    previewHost.append(textElement("p", "history-preview-message", "Select Preview to open the saved verified simulation."));
    content.append(previewHost);
  }
  if (detail.degraded) {
    content.append(textElement(
      "p",
      "history-warning",
      `${detail.degraded.message || "Live run data is unavailable."} Showing the last saved evidence.`,
    ));
  }
  if (item.browser_error) {
    content.append(textElement(
      "p",
      "history-warning",
      "A browser runtime error was recorded for this verified revision.",
    ));
  }
  elements.historyDetail.append(content);
}

async function selectHistoryRun(historyId) {
  if (typeof historyId !== "string") {
    return;
  }
  state.historySelectedId = historyId;
  renderHistoryList();
  resetHistoryDetail("Loading saved run…");
  try {
    const detail = await requestJson(`/api/history/${encodeURIComponent(historyId)}`);
    if (state.historySelectedId !== historyId) {
      return;
    }
    if (detail?.history && typeof detail.history === "object") {
      state.historyItems.set(historyId, detail.history);
    }
    renderHistoryList();
    renderHistoryDetail(detail);
  } catch (error) {
    resetHistoryDetail(error.message || "The saved run could not be loaded.");
  }
}

async function openHistoryDialog() {
  elements.historyQuery.value = "";
  elements.historyDialog.showModal();
  await loadHistory(true);
}

function renderSuggestions(suggestions) {
  elements.suggestionList.replaceChildren();
  for (const suggestion of suggestions) {
    if (typeof suggestion !== "string") {
      continue;
    }
    const button = document.createElement("button");
    button.type = "button";
    button.className = "suggestion";
    button.textContent = suggestion;
    button.addEventListener("click", () => {
      elements.prompt.value = suggestion;
      updatePromptCount();
      elements.prompt.focus();
    });
    elements.suggestionList.append(button);
  }
}

function updatePromptCount() {
  elements.promptCount.textContent = String(elements.prompt.value.length);
}

function updateFollowUpCount() {
  elements.followUpCount.textContent = String(elements.followUpPrompt.value.length);
}

function clearActivity() {
  for (const row of elements.activityList.querySelectorAll(".activity-item")) {
    row.remove();
  }
  elements.activityEmpty.hidden = false;
  state.evidenceRows = 0;
  elements.evidenceCount.textContent = "0 evidence rows";
}

function appendActivity(evidence, presentation) {
  if (!presentation || !Number.isInteger(evidence.sequence) || !evidence.sourceKind) {
    return;
  }
  elements.activityEmpty.hidden = true;
  const row = document.createElement("li");
  row.className = "activity-item";
  row.dataset.runId = evidence.runId;
  row.dataset.sequence = String(evidence.sequence);
  row.dataset.sourceKind = evidence.sourceKind;
  row.dataset.nodeId = evidence.nodeId || "";
  row.dataset.tone = presentation.tone || "neutral";

  const message = document.createElement("span");
  message.className = "activity-message";
  message.textContent = presentation.message;
  row.append(message);

  const detailText = boundedDetail(presentation.detail);
  if (detailText) {
    const detail = document.createElement("span");
    detail.className = "activity-detail";
    detail.textContent = detailText;
    row.append(detail);
  }

  const provenance = document.createElement("span");
  provenance.className = "activity-provenance";
  provenance.textContent = [
    evidence.runId,
    `seq ${evidence.sequence}`,
    evidence.sourceKind,
    evidence.nodeId || null,
  ].filter(Boolean).join(" · ");
  row.append(provenance);

  elements.activityList.append(row);
  state.evidenceRows += 1;
  elements.evidenceCount.textContent = `${state.evidenceRows} evidence ${state.evidenceRows === 1 ? "row" : "rows"}`;
  elements.activityList.scrollTop = elements.activityList.scrollHeight;
}

function resetRunState(runId, runKind = "create") {
  state.runId = runId;
  state.latestSequence = null;
  state.runKind = runKind;
  state.seen = new Set();
  state.graph = null;
  state.nodeCache = new Map();
  state.checkerFailed = false;
  state.terminal = false;
  state.reconnecting = false;
  state.eventQueue = Promise.resolve();
  if (state.graphTimer !== null) {
    window.clearTimeout(state.graphTimer);
  }
  state.graphTimer = null;
  state.graphRefresh = null;
  state.runGraphModel = null;
  state.runSelectedNodeId = null;
  state.runOrderVisible = false;
  clearActivity();
  updateTelemetry();
  updateControls();
}

function cacheGraph(graph) {
  if (!graph || typeof graph !== "object" || !graph.nodes || typeof graph.nodes !== "object") {
    throw new Error("The raw run graph has an invalid shape.");
  }
  state.graph = graph;
  state.nodeCache = new Map(Object.entries(graph.nodes));
  return graph;
}

function fetchGraphNow(runId) {
  return requestJson(`/api/runs/${encodeURIComponent(runId)}`).then(cacheGraph);
}

function refreshGraphSoon(runId) {
  if (state.graphRefresh) {
    return state.graphRefresh;
  }
  state.graphRefresh = new Promise((resolve, reject) => {
    state.graphTimer = window.setTimeout(() => {
      state.graphTimer = null;
      fetchGraphNow(runId).then(resolve, reject).finally(() => {
        state.graphRefresh = null;
      });
    }, GRAPH_DEBOUNCE_MS);
  });
  return state.graphRefresh;
}

async function forceGraphRefresh(runId) {
  if (state.graphRefresh) {
    try {
      await state.graphRefresh;
    } catch (_error) {
      // A forced terminal refresh below gets one clean retry.
    }
  }
  return fetchGraphNow(runId);
}

async function nodeFor(nodeId) {
  if (!nodeId) {
    return null;
  }
  if (!state.nodeCache.has(nodeId)) {
    try {
      await refreshGraphSoon(state.runId);
    } catch (_error) {
      return null;
    }
  }
  return state.nodeCache.get(nodeId) || null;
}

function nodeInput(node) {
  return node && typeof node.input === "object" && node.input ? node.input : {};
}

function nodeResult(node) {
  return node && typeof node.result === "object" && node.result ? node.result : {};
}

function isCheckerNode(node) {
  if (!node || node.skill !== "run_command") {
    return false;
  }
  const command = nodeInput(node).command;
  return typeof command === "string"
    && /^node\s+(?:\.\/)?p5check\.js\s+(?:\.\/)?sketch\.js$/.test(command.trim());
}

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function clearElement(element) {
  while (element.firstChild) {
    element.firstChild.remove();
  }
}

function textElement(tagName, className, value) {
  const element = document.createElement(tagName);
  if (className) {
    element.className = className;
  }
  element.textContent = value;
  return element;
}

function humanizeIdentifier(value) {
  if (typeof value !== "string" || !value.trim()) {
    return "Unknown step";
  }
  const normalized = value.trim().replace(/[_-]+/g, " ").replace(/\s+/g, " ");
  return normalized.charAt(0).toUpperCase() + normalized.slice(1);
}

function runStateValue(node) {
  return typeof node?.state === "string" && node.state.trim()
    ? node.state.trim().toLowerCase()
    : "unknown";
}

function runStateTone(value) {
  return RUN_STATUS_ORDER.includes(value) ? value : "unknown";
}

function executionStateLabel(state) {
  if (state === "succeeded") {
    return "Execution completed";
  }
  return `Execution ${humanizeIdentifier(state).toLowerCase()}`;
}

function nodePresentation(node, state) {
  if (!isCheckerNode(node)) {
    return {label: executionStateLabel(state), tone: runStateTone(state), checker: false};
  }
  const result = nodeResult(node);
  if (result.timed_out === true) {
    return {label: "Validation timed out", tone: "failed", checker: true};
  }
  if (Number.isInteger(result.exit_code)) {
    return result.exit_code === 0
      ? {label: "Validation passed", tone: "succeeded", checker: true}
      : {label: "Validation failed", tone: "failed", checker: true};
  }
  return {label: "Validation unavailable", tone: "unknown", checker: true};
}

function runStepLabel(node, nodeId) {
  if (isCheckerNode(node)) {
    return "Verify sketch";
  }
  if (typeof node?.skill === "string" && RUN_SKILL_LABELS[node.skill]) {
    return RUN_SKILL_LABELS[node.skill];
  }
  return humanizeIdentifier(node?.skill || nodeId);
}

function graphSequence(value) {
  return Number.isInteger(value) ? value : null;
}

function buildRunGraphModel(graph) {
  const events = Array.isArray(graph.events) ? graph.events.filter(isRecord) : [];
  const nodeEntries = Object.entries(isRecord(graph.nodes) ? graph.nodes : {});
  const sequenceByNode = new Map();
  const startedSequenceByNode = new Map();
  let latestSequence = null;

  for (const event of events) {
    const sequence = graphSequence(event.sequence);
    if (sequence !== null) {
      latestSequence = latestSequence === null ? sequence : Math.max(latestSequence, sequence);
    }
    if (sequence === null || typeof event.node_id !== "string") {
      continue;
    }
    const observed = sequenceByNode.get(event.node_id) || {first: sequence, latest: sequence};
    observed.first = Math.min(observed.first, sequence);
    observed.latest = Math.max(observed.latest, sequence);
    sequenceByNode.set(event.node_id, observed);
    const kind = typeof event.kind === "string" ? event.kind.toLowerCase() : "";
    if (kind === "task_started" && !startedSequenceByNode.has(event.node_id)) {
      startedSequenceByNode.set(event.node_id, sequence);
    }
  }

  const nodes = nodeEntries.map(([id, rawNode], insertion) => {
    const data = isRecord(rawNode) ? rawNode : {value: rawNode};
    const observed = sequenceByNode.get(id);
    const nodeState = runStateValue(data);
    return {
      id,
      data,
      insertion,
      firstSequence: observed?.first ?? null,
      latestSequence: observed?.latest ?? null,
      startedSequence: startedSequenceByNode.get(id) ?? null,
      state: nodeState,
      executionLabel: executionStateLabel(nodeState),
      presentation: nodePresentation(data, nodeState),
      reportedIncoming: 0,
      reportedOutgoing: 0,
      isolated: false,
    };
  });
  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const reportedEdges = Array.isArray(graph.edges) ? graph.edges : null;
  const validEdges = [];
  let malformedEdges = 0;
  for (const edge of reportedEdges || []) {
    if (
      Array.isArray(edge)
      && edge.length === 2
      && typeof edge[0] === "string"
      && typeof edge[1] === "string"
      && nodeById.has(edge[0])
      && nodeById.has(edge[1])
    ) {
      validEdges.push({source: edge[0], target: edge[1]});
    } else {
      malformedEdges += 1;
    }
  }

  const orderNodes = (left, right) => {
    const leftSequence = left.firstSequence ?? Number.POSITIVE_INFINITY;
    const rightSequence = right.firstSequence ?? Number.POSITIVE_INFINITY;
    return leftSequence - rightSequence || left.insertion - right.insertion;
  };
  const orderedNodes = [...nodes].sort(orderNodes);
  const rankById = new Map(orderedNodes.map((node, index) => [node.id, index]));
  const incoming = new Map(nodes.map((node) => [node.id, 0]));
  const outgoing = new Map(nodes.map((node) => [node.id, []]));
  const depth = new Map(nodes.map((node) => [node.id, 0]));
  for (const edge of validEdges) {
    incoming.set(edge.target, incoming.get(edge.target) + 1);
    outgoing.get(edge.source).push(edge.target);
  }
  for (const node of nodes) {
    node.reportedIncoming = incoming.get(node.id);
    node.reportedOutgoing = outgoing.get(node.id).length;
    node.isolated = node.reportedIncoming === 0 && node.reportedOutgoing === 0;
  }
  const remainingIncoming = new Map(incoming);
  const queue = nodes
    .filter((node) => remainingIncoming.get(node.id) === 0)
    .sort(orderNodes)
    .map((node) => node.id);
  const processed = new Set();
  while (queue.length) {
    queue.sort((left, right) => rankById.get(left) - rankById.get(right));
    const nodeId = queue.shift();
    processed.add(nodeId);
    for (const target of outgoing.get(nodeId)) {
      depth.set(target, Math.max(depth.get(target), depth.get(nodeId) + 1));
      remainingIncoming.set(target, remainingIncoming.get(target) - 1);
      if (remainingIncoming.get(target) === 0) {
        queue.push(target);
      }
    }
  }
  const cycleNodes = nodes.filter((node) => !processed.has(node.id));
  if (cycleNodes.length) {
    const fallbackDepth = Math.max(0, ...depth.values()) + 1;
    for (const node of cycleNodes) {
      depth.set(node.id, fallbackDepth);
    }
  }
  const maxDepth = nodes.length ? Math.max(0, ...nodes.map((node) => depth.get(node.id))) : 0;
  const statusCounts = new Map();
  for (const node of nodes) {
    statusCounts.set(node.state, (statusCounts.get(node.state) || 0) + 1);
  }

  const reportedPairs = new Set(validEdges.map((edge) => `${edge.source}\u0000${edge.target}`));
  const observedPairs = new Set();
  const observedNextEdges = [];
  const observedAdjacentById = new Map(nodes.map((node) => [node.id, new Set()]));
  let lastTerminalNodeId = null;
  const orderedEvents = events
    .map((event, insertion) => ({event, insertion, sequence: graphSequence(event.sequence)}))
    .filter((item) => item.sequence !== null)
    .sort((left, right) => left.sequence - right.sequence || left.insertion - right.insertion);
  for (const {event, sequence} of orderedEvents) {
    const kind = typeof event.kind === "string" ? event.kind.toLowerCase() : "";
    const nodeId = typeof event.node_id === "string" && nodeById.has(event.node_id)
      ? event.node_id
      : null;
    if ((kind === "task_succeeded" || kind === "task_failed") && nodeId) {
      lastTerminalNodeId = nodeId;
      continue;
    }
    if (kind !== "task_started" || !nodeId) {
      continue;
    }
    if (lastTerminalNodeId && lastTerminalNodeId !== nodeId) {
      const pair = `${lastTerminalNodeId}\u0000${nodeId}`;
      if (!reportedPairs.has(pair) && !observedPairs.has(pair)) {
        observedPairs.add(pair);
        observedNextEdges.push({source: lastTerminalNodeId, target: nodeId, sequence});
        observedAdjacentById.get(lastTerminalNodeId).add(nodeId);
        observedAdjacentById.get(nodeId).add(lastTerminalNodeId);
      }
    }
    lastTerminalNodeId = null;
  }

  return {
    graph,
    nodes,
    nodeById,
    orderedNodes,
    validEdges,
    observedNextEdges,
    observedAdjacentById,
    reportedEdgeCount: reportedEdges ? reportedEdges.length : null,
    malformedEdges,
    cycleNodeCount: cycleNodes.length,
    events,
    eventsAvailable: Array.isArray(graph.events),
    latestSequence,
    depth,
    columnCount: maxDepth + 1,
    statusCounts,
  };
}

function structuredScalar(value) {
  if (value === undefined) {
    return {text: "—", type: "unknown"};
  }
  if (value === null) {
    return {text: "null", type: "null"};
  }
  if (typeof value === "boolean") {
    return {text: value ? "true" : "false", type: "boolean"};
  }
  if (typeof value === "number") {
    return {text: Number.isFinite(value) ? String(value) : "—", type: "number"};
  }
  return {text: String(value), type: typeof value};
}

function appendStructuredValue(parent, value, options = {}) {
  const depth = options.depth || 0;
  const expanded = options.expanded === true;
  if (typeof value === "string" && (value.length > MAX_DETAIL_CHARS || value.includes("\n"))) {
    const details = document.createElement("details");
    details.className = "structured-long-text";
    details.open = expanded;
    details.append(textElement("summary", "", `Text · ${value.length} characters`));
    details.append(textElement("pre", "structured-text", value));
    parent.append(details);
    return;
  }
  if (!Array.isArray(value) && !isRecord(value)) {
    const scalar = structuredScalar(value);
    const element = textElement("span", "structured-scalar", scalar.text);
    element.dataset.valueType = scalar.type;
    parent.append(element);
    return;
  }

  const entries = Array.isArray(value) ? value.map((item, index) => [`[${index}]`, item]) : Object.entries(value);
  const details = document.createElement("details");
  details.className = "structured-group";
  details.open = expanded;
  const kind = Array.isArray(value) ? "List" : "Object";
  details.append(textElement("summary", "", `${kind} · ${entries.length}`));
  const fields = document.createElement("dl");
  fields.className = "structured-fields";
  if (!entries.length) {
    fields.append(textElement("div", "structured-empty", "Empty"));
  } else if (depth >= 10) {
    fields.append(textElement("div", "structured-empty", "Nested value available in Raw JSON"));
  } else {
    for (const [key, item] of entries) {
      const row = document.createElement("div");
      row.className = "structured-row";
      row.append(textElement("dt", "", key));
      const valueCell = document.createElement("dd");
      appendStructuredValue(valueCell, item, {depth: depth + 1});
      row.append(valueCell);
      fields.append(row);
    }
  }
  details.append(fields);
  parent.append(details);
}

function appendStructuredSection(parent, title, value, expanded = false) {
  const section = document.createElement("section");
  section.className = "node-evidence-section";
  section.append(textElement("h4", "", title));
  appendStructuredValue(section, value, {expanded});
  parent.append(section);
}

function appendRunMetric(parent, label, value) {
  const metric = document.createElement("div");
  metric.className = "run-summary-metric";
  metric.append(textElement("dt", "", label));
  metric.append(textElement("dd", "", value === null || value === undefined ? "—" : String(value)));
  parent.append(metric);
}

function appendNodeFacts(parent, node) {
  const facts = document.createElement("dl");
  facts.className = "node-facts";
  appendRunMetric(facts, "Node ID", node.id);
  appendRunMetric(facts, "Skill", typeof node.data.skill === "string" ? node.data.skill : null);
  appendRunMetric(
    facts,
    "Execution state",
    node.state === "unknown" ? null : humanizeIdentifier(node.state)
  );
  if (node.presentation.checker) {
    appendRunMetric(
      facts,
      "Validation result",
      humanizeIdentifier(node.presentation.label.replace("Validation ", ""))
    );
  }
  appendRunMetric(
    facts,
    "Reported dependency",
    node.isolated
      ? "None"
      : `${node.reportedIncoming} incoming · ${node.reportedOutgoing} outgoing`
  );
  appendRunMetric(facts, "Started at event", node.startedSequence);
  appendRunMetric(facts, "First sequence", node.firstSequence);
  appendRunMetric(facts, "Latest sequence", node.latestSequence);
  parent.append(facts);
}

function eventSequenceBadge(node) {
  return node.startedSequence === null
    ? null
    : textElement("span", "run-event-badge", `Event ${node.startedSequence}`);
}

function appendStepPresentation(parent, node) {
  const group = document.createElement("span");
  group.className = "run-step-presentation";
  group.append(textElement("span", "run-step-status", node.presentation.label));
  if (node.presentation.checker) {
    group.append(textElement("span", "run-execution-state", node.executionLabel));
  }
  parent.append(group);
}

function renderStepEvidence(container, node) {
  clearElement(container);
  appendNodeFacts(container, node);
  appendStructuredSection(container, "Input", node.data.input, true);
  appendStructuredSection(container, "Result", node.data.result, false);
  appendStructuredSection(container, "Metadata", node.data.metadata, false);
}

function statusCountEntries(model) {
  return [...model.statusCounts.entries()].sort(([left], [right]) => {
    const leftIndex = RUN_STATUS_ORDER.indexOf(left);
    const rightIndex = RUN_STATUS_ORDER.indexOf(right);
    const normalizedLeft = leftIndex === -1 ? RUN_STATUS_ORDER.length : leftIndex;
    const normalizedRight = rightIndex === -1 ? RUN_STATUS_ORDER.length : rightIndex;
    return normalizedLeft - normalizedRight || left.localeCompare(right);
  });
}

function renderRunOverview(model) {
  clearElement(elements.runOverview);
  const summary = document.createElement("dl");
  summary.className = "run-summary-grid";
  const finished = model.graph.finished === true
    ? "Completed"
    : model.graph.finished === false ? "In progress" : null;
  appendRunMetric(summary, "Run ID", typeof model.graph.run_id === "string" ? model.graph.run_id : null);
  appendRunMetric(summary, "Run state", finished);
  appendRunMetric(summary, "Steps", model.nodes.length);
  appendRunMetric(summary, "Reported edges", model.reportedEdgeCount);
  appendRunMetric(summary, "Events", model.eventsAvailable ? model.events.length : null);
  appendRunMetric(summary, "Latest sequence", model.latestSequence);
  elements.runOverview.append(summary);

  const counts = document.createElement("div");
  counts.className = "run-status-counts";
  counts.append(textElement("span", "run-status-counts-label", "Runtime node states"));
  if (!model.nodes.length) {
    counts.append(textElement("span", "run-status-pill", "No nodes reported"));
  } else {
    for (const [status, count] of statusCountEntries(model)) {
      const pill = textElement("span", "run-status-pill", `${humanizeIdentifier(status)} ${count}`);
      pill.dataset.state = runStateTone(status);
      counts.append(pill);
    }
  }
  elements.runOverview.append(counts);

  const heading = document.createElement("div");
  heading.className = "run-section-heading";
  heading.append(textElement("p", "panel-kicker", "Observed execution"));
  heading.append(textElement("h3", "", "Execution steps"));
  elements.runOverview.append(heading);

  if (!model.nodes.length) {
    elements.runOverview.append(textElement("p", "empty-evidence", "No run nodes were reported."));
    return;
  }
  const steps = document.createElement("div");
  steps.className = "run-steps";
  model.orderedNodes.forEach((node, index) => {
    const details = document.createElement("details");
    details.className = "run-step";
    details.dataset.state = node.presentation.tone;
    const summaryRow = document.createElement("summary");
    summaryRow.className = "run-step-summary";
    summaryRow.append(textElement("span", "run-step-number", String(index + 1).padStart(2, "0")));
    const identity = document.createElement("span");
    identity.className = "run-step-identity";
    identity.append(textElement("strong", "", runStepLabel(node.data, node.id)));
    identity.append(textElement("code", "", node.id));
    if (node.isolated) {
      identity.append(textElement("span", "run-node-isolated", "No reported dependency"));
    }
    const sequenceBadge = eventSequenceBadge(node);
    if (sequenceBadge) {
      identity.append(sequenceBadge);
    }
    summaryRow.append(identity);
    appendStepPresentation(summaryRow, node);
    details.append(summaryRow);
    const body = document.createElement("div");
    body.className = "run-step-body";
    details.append(body);
    details.addEventListener("toggle", () => {
      if (details.open && body.dataset.rendered !== "true") {
        renderStepEvidence(body, node);
        body.dataset.rendered = "true";
      }
    });
    steps.append(details);
  });
  elements.runOverview.append(steps);
}

function renderRunInspector(node) {
  clearElement(elements.runInspector);
  if (!node) {
    elements.runInspector.append(textElement("p", "empty-evidence", "Select a graph node to inspect its evidence."));
    return;
  }
  const heading = document.createElement("div");
  heading.className = "run-inspector-heading";
  heading.append(textElement("p", "panel-kicker", "Selected step"));
  heading.append(textElement("h3", "", runStepLabel(node.data, node.id)));
  heading.append(textElement("code", "", node.id));
  elements.runInspector.append(heading);
  appendNodeFacts(elements.runInspector, node);
  appendStructuredSection(elements.runInspector, "Complete node record", node.data, true);
}

function selectRunGraphNode(nodeId, focus = false) {
  const model = state.runGraphModel;
  if (!model || !model.nodeById.has(nodeId)) {
    return;
  }
  state.runSelectedNodeId = nodeId;
  for (const button of elements.runGraphNodes.querySelectorAll(".run-graph-node")) {
    const selected = button.dataset.nodeId === nodeId;
    button.setAttribute("aria-pressed", selected ? "true" : "false");
    if (selected && focus) {
      button.focus();
    }
  }
  renderRunInspector(model.nodeById.get(nodeId));
  if (state.runOrderVisible) {
    scheduleRunGraphEdges();
  }
}

function svgElement(name) {
  return document.createElementNS("http://www.w3.org/2000/svg", name);
}

function graphRect(element, canvasRect) {
  const rect = element.getBoundingClientRect();
  return {
    left: rect.left - canvasRect.left,
    top: rect.top - canvasRect.top,
    right: rect.right - canvasRect.left,
    bottom: rect.bottom - canvasRect.top,
    width: rect.width,
    height: rect.height,
  };
}

function expandGraphRect(rect, amount) {
  return {
    left: rect.left - amount,
    top: rect.top - amount,
    right: rect.right + amount,
    bottom: rect.bottom + amount,
  };
}

function pointInsideGraphRect(point, rect) {
  return point.x > rect.left && point.x < rect.right
    && point.y > rect.top && point.y < rect.bottom;
}

function segmentCrossesGraphRect(start, end, rect) {
  if (start.x === end.x) {
    const low = Math.min(start.y, end.y);
    const high = Math.max(start.y, end.y);
    return start.x > rect.left && start.x < rect.right
      && Math.max(low, rect.top) < Math.min(high, rect.bottom);
  }
  const low = Math.min(start.x, end.x);
  const high = Math.max(start.x, end.x);
  return start.y > rect.top && start.y < rect.bottom
    && Math.max(low, rect.left) < Math.min(high, rect.right);
}

function graphSegmentIsClear(start, end, obstacles) {
  return obstacles.every((rect) => !segmentCrossesGraphRect(start, end, rect));
}

function simplifyGraphRoute(points) {
  const simplified = [];
  for (const point of points) {
    const last = simplified.at(-1);
    if (last && last.x === point.x && last.y === point.y) {
      continue;
    }
    const previous = simplified.at(-2);
    if (
      previous
      && ((previous.x === last.x && last.x === point.x)
        || (previous.y === last.y && last.y === point.y))
    ) {
      simplified[simplified.length - 1] = point;
    } else {
      simplified.push(point);
    }
  }
  return simplified;
}

function routeObservedEdge(sourceRect, targetRect, obstacles, width, height) {
  const clearance = 8;
  const travelsDown = targetRect.top + targetRect.height / 2
    >= sourceRect.top + sourceRect.height / 2;
  const sourceAnchor = {
    x: sourceRect.left + sourceRect.width / 2,
    y: travelsDown ? sourceRect.bottom : sourceRect.top,
  };
  const targetAnchor = {
    x: targetRect.left + targetRect.width / 2,
    y: travelsDown ? targetRect.top : targetRect.bottom,
  };
  const start = {
    x: sourceAnchor.x,
    y: sourceAnchor.y + (travelsDown ? clearance : -clearance),
  };
  const goal = {
    x: targetAnchor.x,
    y: targetAnchor.y + (travelsDown ? -clearance : clearance),
  };
  const xValues = new Set([6, width - 6, start.x, goal.x]);
  const yValues = new Set([6, height - 6, start.y, goal.y]);
  for (const rect of obstacles) {
    xValues.add(Math.max(6, rect.left));
    xValues.add(Math.min(width - 6, rect.right));
    yValues.add(Math.max(6, rect.top));
    yValues.add(Math.min(height - 6, rect.bottom));
  }
  const xs = [...xValues].sort((left, right) => left - right);
  const ys = [...yValues].sort((left, right) => left - right);
  const points = [];
  const pointByCoordinate = new Map();
  const coordinateKey = (x, y) => `${x}\u0000${y}`;
  for (const y of ys) {
    for (const x of xs) {
      const point = {x, y};
      if (obstacles.some((rect) => pointInsideGraphRect(point, rect))) {
        continue;
      }
      const index = points.length;
      points.push(point);
      pointByCoordinate.set(coordinateKey(x, y), index);
    }
  }
  const startIndex = pointByCoordinate.get(coordinateKey(start.x, start.y));
  const goalIndex = pointByCoordinate.get(coordinateKey(goal.x, goal.y));
  if (startIndex === undefined || goalIndex === undefined) {
    return null;
  }
  const adjacency = new Map(points.map((_point, index) => [index, []]));
  const connectVisibleNeighbors = (indexes) => {
    indexes.sort((left, right) => {
      const a = points[left];
      const b = points[right];
      return a.x - b.x || a.y - b.y;
    });
    for (let index = 1; index < indexes.length; index += 1) {
      const previousIndex = indexes[index - 1];
      const currentIndex = indexes[index];
      if (!graphSegmentIsClear(points[previousIndex], points[currentIndex], obstacles)) {
        continue;
      }
      adjacency.get(previousIndex).push(currentIndex);
      adjacency.get(currentIndex).push(previousIndex);
    }
  };
  for (const y of ys) {
    connectVisibleNeighbors(
      points.map((_point, index) => index).filter((index) => points[index].y === y)
    );
  }
  for (const x of xs) {
    const indexes = points.map((_point, index) => index).filter((index) => points[index].x === x);
    indexes.sort((left, right) => points[left].y - points[right].y);
    for (let index = 1; index < indexes.length; index += 1) {
      const previousIndex = indexes[index - 1];
      const currentIndex = indexes[index];
      if (!graphSegmentIsClear(points[previousIndex], points[currentIndex], obstacles)) {
        continue;
      }
      adjacency.get(previousIndex).push(currentIndex);
      adjacency.get(currentIndex).push(previousIndex);
    }
  }

  const startKey = `${startIndex}:none`;
  const distances = new Map([[startKey, 0]]);
  const previousByKey = new Map();
  const queue = [{key: startKey, index: startIndex, direction: "none", cost: 0}];
  let goalKey = null;
  while (queue.length) {
    queue.sort((left, right) => right.cost - left.cost);
    const current = queue.pop();
    if (current.cost !== distances.get(current.key)) {
      continue;
    }
    if (current.index === goalIndex) {
      goalKey = current.key;
      break;
    }
    for (const neighborIndex of adjacency.get(current.index)) {
      const currentPoint = points[current.index];
      const neighbor = points[neighborIndex];
      const direction = currentPoint.x === neighbor.x ? "vertical" : "horizontal";
      const distance = Math.abs(currentPoint.x - neighbor.x) + Math.abs(currentPoint.y - neighbor.y);
      const turnPenalty = current.direction !== "none" && current.direction !== direction ? 18 : 0;
      const cost = current.cost + distance + turnPenalty;
      const key = `${neighborIndex}:${direction}`;
      if (cost >= (distances.get(key) ?? Number.POSITIVE_INFINITY)) {
        continue;
      }
      distances.set(key, cost);
      previousByKey.set(key, current.key);
      queue.push({key, index: neighborIndex, direction, cost});
    }
  }
  if (!goalKey) {
    return null;
  }
  const route = [];
  for (let key = goalKey; key; key = previousByKey.get(key)) {
    route.push(points[Number.parseInt(key.split(":", 1)[0], 10)]);
  }
  route.reverse();
  return simplifyGraphRoute([sourceAnchor, ...route, targetAnchor]);
}

function graphRoutePath(points) {
  const coordinate = (value) => Math.round(value * 10) / 10;
  return points.map((point, index) => (
    `${index === 0 ? "M" : "L"} ${coordinate(point.x)} ${coordinate(point.y)}`
  )).join(" ");
}

function drawRunGraphEdges() {
  state.runGraphFrame = null;
  const model = state.runGraphModel;
  if (!model || state.runActiveTab !== "graph" || elements.runPanels.graph.hidden) {
    return;
  }
  clearElement(elements.runGraphEdges);
  const canvasRect = elements.runGraphCanvas.getBoundingClientRect();
  const width = Math.max(elements.runGraphCanvas.scrollWidth, elements.runGraphCanvas.clientWidth);
  const height = Math.max(elements.runGraphCanvas.scrollHeight, elements.runGraphCanvas.clientHeight);
  elements.runGraphEdges.setAttribute("viewBox", `0 0 ${width} ${height}`);
  elements.runGraphEdges.setAttribute("width", String(width));
  elements.runGraphEdges.setAttribute("height", String(height));

  const definitions = svgElement("defs");
  for (const kind of ["reported", "observed"]) {
    const marker = svgElement("marker");
    marker.setAttribute("id", `run-graph-arrow-${kind}`);
    marker.setAttribute("markerWidth", "8");
    marker.setAttribute("markerHeight", "8");
    marker.setAttribute("refX", "7");
    marker.setAttribute("refY", "4");
    marker.setAttribute("orient", "auto");
    marker.dataset.edgeKind = kind;
    const arrow = svgElement("path");
    arrow.setAttribute("d", "M0,0 L8,4 L0,8 Z");
    marker.append(arrow);
    definitions.append(marker);
  }
  elements.runGraphEdges.append(definitions);

  const buttonById = new Map(
    [...elements.runGraphNodes.querySelectorAll(".run-graph-node")]
      .map((button) => [button.dataset.nodeId, button])
  );
  const rectById = new Map(
    [...buttonById].map(([nodeId, button]) => [nodeId, graphRect(button, canvasRect)])
  );
  const observedObstacles = [...rectById.values()].map((rect) => expandGraphRect(rect, 6));
  const drawEdge = (edge, kind) => {
    const source = buttonById.get(edge.source);
    const target = buttonById.get(edge.target);
    if (!source || !target) {
      return;
    }
    const sourceRect = source.getBoundingClientRect();
    const targetRect = target.getBoundingClientRect();
    const path = svgElement("path");
    if (kind === "observed") {
      const route = routeObservedEdge(
        rectById.get(edge.source),
        rectById.get(edge.target),
        observedObstacles,
        width,
        height
      );
      if (!route) {
        return;
      }
      path.setAttribute("d", graphRoutePath(route));
      path.dataset.routeClear = "true";
    } else {
      const sourceX = sourceRect.right - canvasRect.left;
      const sourceY = sourceRect.top - canvasRect.top + sourceRect.height / 2;
      const targetX = targetRect.left - canvasRect.left;
      const targetY = targetRect.top - canvasRect.top + targetRect.height / 2;
      const span = Math.max(44, Math.abs(targetX - sourceX) / 2);
      path.setAttribute(
        "d",
        `M ${sourceX} ${sourceY} C ${sourceX + span} ${sourceY}, ${targetX - span} ${targetY}, ${targetX} ${targetY}`
      );
    }
    path.classList.add("run-graph-edge", `run-graph-edge-${kind}`);
    path.setAttribute("marker-end", `url(#run-graph-arrow-${kind})`);
    path.dataset.source = edge.source;
    path.dataset.target = edge.target;
    path.dataset.edgeKind = kind;
    if (kind === "observed") {
      const selected = state.runSelectedNodeId;
      path.dataset.emphasis = edge.source === selected || edge.target === selected
        ? "active"
        : "muted";
    }
    elements.runGraphEdges.append(path);
  };
  for (const edge of model.validEdges) {
    drawEdge(edge, "reported");
  }
  if (state.runOrderVisible) {
    for (const edge of model.observedNextEdges) {
      drawEdge(edge, "observed");
    }
  }
}

function scheduleRunGraphEdges() {
  if (state.runGraphFrame !== null) {
    window.cancelAnimationFrame(state.runGraphFrame);
  }
  state.runGraphFrame = window.requestAnimationFrame(() => {
    state.runGraphFrame = window.requestAnimationFrame(drawRunGraphEdges);
  });
}

function renderRunGraphNote(model) {
  const notes = [state.runOrderVisible
    ? "Run order shows which task started next; it is not a dependency. Node position is arranged only for readability."
    : "Lines show reported dependencies. Node position is arranged only for readability."];
  if (model?.malformedEdges) {
    notes.push(`${model.malformedEdges} malformed ${model.malformedEdges === 1 ? "edge was" : "edges were"} not drawn.`);
  }
  if (model?.cycleNodeCount) {
    notes.push(`${model.cycleNodeCount} ${model.cycleNodeCount === 1 ? "node is" : "nodes are"} shown in an unresolved dependency group.`);
  }
  elements.runGraphNote.textContent = notes.join(" ");
}

function setRunOrderVisible(visible) {
  state.runOrderVisible = visible === true;
  elements.runOrderToggle.checked = state.runOrderVisible;
  elements.runOrderLegend.hidden = !state.runOrderVisible;
  elements.runGraphCanvas.dataset.runOrderVisible = state.runOrderVisible ? "true" : "false";
  renderRunGraphNote(state.runGraphModel);
  scheduleRunGraphEdges();
}

function renderRunGraph(model) {
  clearElement(elements.runGraphNodes);
  clearElement(elements.runGraphEdges);
  state.runGraphModel = model;
  renderRunGraphNote(model);
  if (!model.nodes.length) {
    elements.runGraphNodes.append(textElement("p", "empty-evidence", "No run nodes were reported."));
    state.runSelectedNodeId = null;
    renderRunInspector(null);
    return;
  }

  elements.runGraphNodes.style.setProperty("--graph-columns", String(model.columnCount));
  const nodesByDepth = new Map();
  for (const node of model.orderedNodes) {
    const nodeDepth = model.depth.get(node.id);
    if (!nodesByDepth.has(nodeDepth)) {
      nodesByDepth.set(nodeDepth, []);
    }
    nodesByDepth.get(nodeDepth).push(node);
  }
  for (let depth = 0; depth < model.columnCount; depth += 1) {
    const column = document.createElement("div");
    column.className = "run-graph-column";
    column.dataset.depth = String(depth);
    for (const node of nodesByDepth.get(depth) || []) {
      const button = document.createElement("button");
      button.className = "run-graph-node";
      button.type = "button";
      button.dataset.nodeId = node.id;
      button.dataset.state = node.presentation.tone;
      button.setAttribute("aria-pressed", "false");
      const isolationLabel = node.isolated ? ", no reported dependency" : "";
      const executionLabel = node.presentation.checker ? `, ${node.executionLabel}` : "";
      const eventLabel = node.startedSequence === null ? "" : `, event ${node.startedSequence}`;
      button.setAttribute(
        "aria-label",
        `${runStepLabel(node.data, node.id)}, ${node.presentation.label}${executionLabel}${eventLabel}${isolationLabel}, ${node.id}`
      );
      const badges = document.createElement("span");
      badges.className = "run-graph-node-badges";
      badges.append(textElement("span", "run-graph-node-state", node.presentation.label));
      if (node.presentation.checker) {
        badges.append(textElement("span", "run-execution-state", node.executionLabel));
      }
      const sequenceBadge = eventSequenceBadge(node);
      if (sequenceBadge) {
        badges.append(sequenceBadge);
      }
      if (node.isolated) {
        badges.append(textElement("span", "run-node-isolated", "No reported dependency"));
      }
      button.append(badges);
      button.append(textElement("strong", "", runStepLabel(node.data, node.id)));
      button.append(textElement("code", "", node.id));
      button.addEventListener("click", () => selectRunGraphNode(node.id));
      column.append(button);
    }
    elements.runGraphNodes.append(column);
  }
  const selected = model.nodeById.has(state.runSelectedNodeId)
    ? state.runSelectedNodeId
    : model.orderedNodes[0].id;
  selectRunGraphNode(selected);
  scheduleRunGraphEdges();
}

function setRunTab(tabName, focus = false) {
  const selectedName = Object.prototype.hasOwnProperty.call(elements.runPanels, tabName)
    ? tabName
    : "overview";
  state.runActiveTab = selectedName;
  for (const tab of elements.runTabs) {
    const selected = tab.dataset.runTab === selectedName;
    tab.setAttribute("aria-selected", selected ? "true" : "false");
    tab.tabIndex = selected ? 0 : -1;
    if (selected && focus) {
      tab.focus();
    }
  }
  for (const [name, panel] of Object.entries(elements.runPanels)) {
    panel.hidden = name !== selectedName;
  }
  if (selectedName === "graph") {
    scheduleRunGraphEdges();
  }
}

function renderRunEvidence(graph) {
  const model = buildRunGraphModel(graph);
  const stateLabel = graph.finished === true
    ? "Completed"
    : graph.finished === false ? "In progress" : "State unavailable";
  const runId = typeof graph.run_id === "string" ? graph.run_id : "Run ID unavailable";
  elements.runMeta.textContent = `${runId} · ${stateLabel}`;
  elements.runContent.textContent = JSON.stringify(graph, null, 2);
  renderRunOverview(model);
  renderRunGraph(model);
}

function renderRunEvidenceMessage(message) {
  clearElement(elements.runOverview);
  elements.runOverview.append(textElement("p", "empty-evidence", message));
  clearElement(elements.runGraphNodes);
  elements.runGraphNodes.append(textElement("p", "empty-evidence", message));
  clearElement(elements.runGraphEdges);
  state.runGraphModel = null;
  state.runSelectedNodeId = null;
  renderRunInspector(null);
  elements.runContent.textContent = message;
}

function isRefusal(event, node) {
  const result = nodeResult(node);
  const text = [event.error, result.error, result.stderr, result.reason]
    .filter((item) => typeof item === "string")
    .join(" ");
  return /\b(refus|protected|denied|not allowed|forbidden)\w*/i.test(text);
}

function refusalDetail(event, node) {
  const result = nodeResult(node);
  return event.error || result.reason || result.error || result.stderr || "The requested action was refused.";
}

async function presentationFor(event, nodeId) {
  if (event.type === "RUN_STARTED") {
    return {message: "Starting the factory"};
  }
  if (event.type === "STATE_DELTA" && event.delta?.op === "graph_patched") {
    try {
      await refreshGraphSoon(state.runId);
    } catch (_error) {
      // The planning evidence remains valid even if the auxiliary graph read is degraded.
    }
    return {message: "Planning the next step"};
  }

  const node = nodeId ? await nodeFor(nodeId) : null;
  if (event.type === "STEP_STARTED") {
    if (node?.skill === "read_code") {
      return {message: "Reading the current sketch"};
    }
    if (node?.skill === "create_file") {
      return {message: "Writing sketch.js"};
    }
    if (node?.skill === "edit_code") {
      return {message: state.checkerFailed ? "Repairing sketch.js" : "Updating sketch.js"};
    }
    if (isCheckerNode(node)) {
      return {message: "Judging the simulation"};
    }
    return null;
  }

  if (event.type === "STEP_FINISHED") {
    if (isRefusal(event, node)) {
      return {message: "Refused an unsafe action", detail: refusalDetail(event, node), tone: "warning"};
    }
    if (isCheckerNode(node)) {
      const result = nodeResult(node);
      if (result.exit_code === 0) {
        state.checkerFailed = false;
        return {message: "Check passed", tone: "success"};
      }
      if (Number.isInteger(result.exit_code) && result.exit_code !== 0) {
        state.checkerFailed = true;
        return {
          message: `Found a problem (checker exit ${result.exit_code})`,
          detail: result.stderr || result.stdout || "The checker rejected the sketch.",
          tone: "warning",
        };
      }
    }
    return null;
  }

  if (event.type === "RUN_ERROR") {
    return {message: "Run failed", detail: event.error || "The agent run failed.", tone: "error"};
  }
  return null;
}

async function refreshSession() {
  const payload = await requestJson("/api/session");
  state.session = payload.session;
  if (payload.degraded) {
    showBanner(payload.degraded.message || "Run information is temporarily degraded.");
  }
  return payload;
}

async function finishRun(event, evidence) {
  state.terminal = true;
  state.followUpSubmitting = false;
  if (state.eventSource) {
    state.eventSource.close();
    state.eventSource = null;
  }
  elements.transportState.textContent = "";
  try {
    await forceGraphRefresh(state.runId);
    await refreshSession();
  } catch (error) {
    showBanner(error.message || "Could not classify the completed run.");
  }
  const ready = state.session?.state === "ready";
  appendActivity(evidence, {
    message: ready ? "Simulation ready" : "Run incomplete",
    tone: ready ? "success" : "error",
  });
  setMode(ready ? "ready" : "failed");
  updateControls();
  if (ready) {
    await mountPreview();
  }
}

async function processEvent(event) {
  if (!event || typeof event !== "object") {
    return;
  }
  if (event.type === "STATE_SNAPSHOT") {
    if (Number.isInteger(event.seq) && event.seq >= 0) {
      state.latestSequence = Math.max(state.latestSequence ?? -1, event.seq);
      updateTelemetry();
    }
    if (event.state && typeof event.state === "object") {
      // A reconnect snapshot replaces folded AG-UI state. It is transport state, not a journal row.
      state.foldedState = event.state;
    }
    return;
  }
  const sequence = event.seq;
  const sourceKind = event.source_kind;
  if (!Number.isInteger(sequence) || sequence < 0 || typeof sourceKind !== "string") {
    return;
  }
  const key = `${state.runId}:${sequence}`;
  if (state.seen.has(key)) {
    return;
  }
  state.seen.add(key);
  state.latestSequence = Math.max(state.latestSequence ?? -1, sequence);
  updateTelemetry();
  const nodeId = typeof event.stepName === "string" ? event.stepName : null;
  const evidence = {runId: state.runId, sequence, sourceKind, nodeId};

  if (event.type === "RUN_FINISHED") {
    await finishRun(event, evidence);
    return;
  }
  const presentation = await presentationFor(event, nodeId);
  appendActivity(evidence, presentation);
  if (event.type === "RUN_ERROR") {
    setMode("failed");
  }
}

function queueEvent(raw) {
  state.eventQueue = state.eventQueue
    .then(() => processEvent(raw))
    .catch(() => {
      showBanner("An event could not be rendered. Raw run evidence remains available.");
    });
}

function parseEventData(message) {
  try {
    const payload = JSON.parse(message.data);
    queueEvent(payload);
  } catch (_error) {
    showBanner("The journal emitted an event the browser could not read.");
  }
}

function connectRun(runId, runKind = state.runKind || "create") {
  if (state.eventSource) {
    state.eventSource.close();
  }
  if (state.runId !== runId) {
    resetRunState(runId, runKind);
  } else {
    state.runKind = runKind;
  }
  state.terminal = false;
  setMode("connecting");
  elements.transportState.textContent = "Connecting to the real run journal…";
  const source = new EventSource(`/api/runs/${encodeURIComponent(runId)}/events`);
  state.eventSource = source;

  source.onopen = () => {
    if (state.terminal) {
      return;
    }
    elements.transportState.textContent = "";
    state.reconnecting = false;
    setMode(state.runKind === "follow_up" ? "modifying" : "running");
  };
  source.onmessage = parseEventData;
  source.addEventListener("transport_error", (message) => {
    let detail = "The journal connection closed; reconnecting from the last sequence.";
    try {
      const payload = JSON.parse(message.data);
      detail = boundedDetail(payload.message) || detail;
    } catch (_error) {
      // Use the honest local transport fallback above.
    }
    elements.transportState.textContent = detail;
    state.reconnecting = true;
    setMode("reconnecting");
  });
  source.onerror = () => {
    if (state.terminal) {
      source.close();
      return;
    }
    state.reconnecting = true;
    elements.transportState.textContent = "Journal connection interrupted. Reconnecting without adding an activity row…";
    setMode("reconnecting");
  };
  updateControls();
}

async function startCreate(event) {
  event.preventDefault();
  clearFormError();
  const prompt = elements.prompt.value.trim();
  if (!prompt) {
    showFormError("Describe the physics toy you want to build.");
    elements.prompt.focus();
    return;
  }
  setMode("connecting");
  destroyPreview();
  setPreviewState("locked");
  elements.createButton.disabled = true;
  try {
    const payload = await requestJson("/api/runs", {
      method: "POST",
      body: JSON.stringify({prompt}),
    });
    state.session = {
      ...state.session,
      state: "running",
      active_run_id: payload.run_id,
    };
    void refreshSavedCount();
    resetRunState(payload.run_id, "create");
    connectRun(payload.run_id, "create");
  } catch (error) {
    showFormError(error.message || "The factory could not start this run.");
    try {
      await refreshSession();
    } catch (_refreshError) {
      // The original bounded error remains the actionable message.
    }
    const resetRequired = state.session?.state === "reset_required";
    setMode(resetRequired ? "failed" : (state.healthReady ? "landing" : "degraded"));
  }
}

async function startFollowUp(event) {
  event.preventDefault();
  clearFollowUpError();
  const prompt = elements.followUpPrompt.value.trim();
  if (!prompt) {
    showFollowUpError("Describe the one change you want to make.");
    elements.followUpPrompt.focus();
    return;
  }

  state.followUpSubmitting = true;
  updateControls();
  destroyPreview();
  setPreviewState("locked");
  setMode("modifying");
  try {
    const payload = await requestJson("/api/runs/follow-up", {
      method: "POST",
      body: JSON.stringify({prompt}),
    });
    state.followUpSubmitting = false;
    state.session = {
      ...state.session,
      state: "modifying",
      active_run_id: payload.run_id,
      current_sketch_sha256: null,
      follow_up_used: true,
    };
    void refreshSavedCount();
    resetRunState(payload.run_id, "follow_up");
    connectRun(payload.run_id, "follow_up");
  } catch (error) {
    state.followUpSubmitting = false;
    let refreshed = false;
    try {
      await refreshSession();
      refreshed = true;
    } catch (_refreshError) {
      // The bounded start error remains the actionable message.
    }
    const activeId = refreshed ? state.session?.active_run_id : null;
    if (typeof activeId === "string") {
      const linked = state.session.runs?.find((run) => run.run_id === activeId);
      resetRunState(activeId, linked?.kind || "follow_up");
      connectRun(activeId, linked?.kind || "follow_up");
      showBanner("The linked run was already accepted; reconnecting to its journal.");
      return;
    }
    if (
      refreshed
      && state.session?.state === "ready"
      && typeof state.session.current_sketch_sha256 === "string"
    ) {
      setMode("ready");
      showFollowUpError(error.message || "The factory could not start the modification.");
      await mountPreview();
      return;
    }
    showBanner(error.message || "The factory could not start the modification.");
    setMode("failed");
  }
}

async function openCodeDialog() {
  elements.codeMeta.textContent = "Loading the fixed sketch.js path…";
  elements.codeContent.textContent = "";
  elements.codeDialog.showModal();
  try {
    const code = await requestJson("/api/code");
    elements.codeMeta.textContent = `${code.bytes} bytes · sha256 ${code.sha256} · ${code.verified ? "verified" : "not verified"}`;
    elements.codeContent.textContent = code.content;
  } catch (error) {
    elements.codeMeta.textContent = "Source unavailable";
    elements.codeContent.textContent = error.message || "The source could not be read.";
  }
}

async function openRunDialog() {
  elements.runMeta.textContent = state.runId ? `Run ${state.runId}` : "No run selected";
  state.runSelectedNodeId = null;
  setRunOrderVisible(false);
  setRunTab("overview");
  renderRunEvidenceMessage(state.runId ? "Loading run evidence…" : "No run is selected.");
  elements.runDialog.showModal();
  if (!state.runId) {
    return;
  }
  try {
    const graph = await forceGraphRefresh(state.runId);
    renderRunEvidence(graph);
  } catch (error) {
    elements.runMeta.textContent = "Run evidence unavailable";
    renderRunEvidenceMessage(error.message || "The run graph could not be read.");
  }
}

async function resetSession() {
  if (!window.confirm("Reset the generated sketch and begin a new factory session? S17 journals will be retained.")) {
    return;
  }
  elements.reset.disabled = true;
  clearFormError();
  try {
    const payload = await requestJson("/api/session/reset", {method: "POST", body: "{}"});
    if (state.eventSource) {
      state.eventSource.close();
      state.eventSource = null;
    }
    state.session = payload.session;
    destroyPreview();
    setPreviewState("locked");
    state.runId = null;
    state.latestSequence = null;
    state.runKind = null;
    state.followUpSubmitting = false;
    state.graph = null;
    state.nodeCache = new Map();
    state.runGraphModel = null;
    state.runSelectedNodeId = null;
    state.seen = new Set();
    clearActivity();
    updateTelemetry();
    elements.prompt.value = "";
    elements.followUpPrompt.value = "";
    updatePromptCount();
    updateFollowUpCount();
    clearFollowUpError();
    hideBanner();
    setMode(state.healthReady ? "landing" : "degraded");
  } catch (error) {
    showBanner(error.message || "The factory could not reset the session.");
    updateControls();
  }
}

function latestRun(session) {
  const runs = Array.isArray(session?.runs) ? session.runs : [];
  if (session?.active_run_id) {
    return runs.find((run) => run.run_id === session.active_run_id) || null;
  }
  return runs.at(-1) || null;
}

async function boot() {
  setMode("connecting");
  void refreshSavedCount();
  try {
    const [sessionPayload, health] = await Promise.all([
      requestJson("/api/session"),
      requestJson("/api/health"),
    ]);
    state.session = sessionPayload.session;
    state.healthReady = health.ready === true;
    renderSuggestions(sessionPayload.suggested_prompts || []);
    if (sessionPayload.degraded) {
      showBanner(sessionPayload.degraded.message || "Current run information is degraded.");
    } else if (!state.healthReady) {
      showBanner("S17 or the trusted workspace is not ready. Creation is disabled.");
    }

    const run = latestRun(state.session);
    if (run) {
      resetRunState(run.run_id, run.kind);
      connectRun(run.run_id, run.kind);
      return;
    }
    if (!state.healthReady) {
      setMode("degraded");
    } else if (state.session.state === "empty") {
      setMode("landing");
    } else {
      setMode("failed");
    }
  } catch (error) {
    state.healthReady = false;
    showBanner(error.message || "The factory could not load its session.");
    setMode("degraded");
  }
}

elements.form.addEventListener("submit", startCreate);
elements.prompt.addEventListener("input", updatePromptCount);
elements.followUpForm.addEventListener("submit", startFollowUp);
elements.followUpPrompt.addEventListener("input", updateFollowUpCount);
elements.savedRuns.addEventListener("click", openHistoryDialog);
elements.viewCode.addEventListener("click", openCodeDialog);
elements.viewRun.addEventListener("click", openRunDialog);
elements.reset.addEventListener("click", resetSession);
elements.historySearch.addEventListener("submit", (event) => {
  event.preventDefault();
  void loadHistory(true);
});
elements.historyMore.addEventListener("click", () => {
  void loadHistory(false);
});
elements.historyDialog.addEventListener("close", destroyHistoryPreview);
elements.runOrderToggle.addEventListener("change", () => {
  setRunOrderVisible(elements.runOrderToggle.checked);
});
for (const tab of elements.runTabs) {
  tab.addEventListener("click", () => setRunTab(tab.dataset.runTab));
  tab.addEventListener("keydown", (event) => {
    const currentIndex = elements.runTabs.indexOf(tab);
    let nextIndex = null;
    if (event.key === "ArrowRight") {
      nextIndex = (currentIndex + 1) % elements.runTabs.length;
    } else if (event.key === "ArrowLeft") {
      nextIndex = (currentIndex - 1 + elements.runTabs.length) % elements.runTabs.length;
    } else if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = elements.runTabs.length - 1;
    }
    if (nextIndex !== null) {
      event.preventDefault();
      setRunTab(elements.runTabs[nextIndex].dataset.runTab, true);
    }
  });
}
for (const button of document.querySelectorAll("[data-close-dialog]")) {
  button.addEventListener("click", () => button.closest("dialog").close());
}
for (const dialog of document.querySelectorAll("dialog")) {
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) {
      dialog.close();
    }
  });
}
window.addEventListener("message", handlePreviewMessage);
window.addEventListener("resize", scheduleRunGraphEdges);
window.addEventListener("pagehide", () => {
  state.eventSource?.close();
  destroyPreview();
  destroyHistoryPreview();
});

updatePromptCount();
updateFollowUpCount();
boot();
