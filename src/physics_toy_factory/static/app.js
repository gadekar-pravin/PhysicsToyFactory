"use strict";

const MAX_DETAIL_CHARS = 240;
const GRAPH_DEBOUNCE_MS = 60;

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
  viewCode: document.querySelector("#view-code"),
  viewRun: document.querySelector("#view-run"),
  reset: document.querySelector("#reset-session"),
  codeDialog: document.querySelector("#code-dialog"),
  codeMeta: document.querySelector("#code-meta"),
  codeContent: document.querySelector("#code-content"),
  runDialog: document.querySelector("#run-dialog"),
  runMeta: document.querySelector("#run-meta"),
  runContent: document.querySelector("#run-content"),
};

const state = {
  session: null,
  healthReady: false,
  runId: null,
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
  updateControls();
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
  clearActivity();
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
  elements.runContent.textContent = "Loading raw graph…";
  elements.runDialog.showModal();
  if (!state.runId) {
    return;
  }
  try {
    const graph = await forceGraphRefresh(state.runId);
    elements.runContent.textContent = JSON.stringify(graph, null, 2);
  } catch (error) {
    elements.runContent.textContent = error.message || "The raw graph could not be read.";
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
    state.runKind = null;
    state.followUpSubmitting = false;
    state.graph = null;
    state.nodeCache = new Map();
    state.seen = new Set();
    clearActivity();
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
elements.viewCode.addEventListener("click", openCodeDialog);
elements.viewRun.addEventListener("click", openRunDialog);
elements.reset.addEventListener("click", resetSession);
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
window.addEventListener("pagehide", () => {
  state.eventSource?.close();
  destroyPreview();
});

updatePromptCount();
updateFollowUpCount();
boot();
