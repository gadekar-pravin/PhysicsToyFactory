"use strict";

const MAX_DETAIL_CHARS = 240;
const GRAPH_DEBOUNCE_MS = 60;

const elements = {
  app: document.querySelector("#app"),
  stateLabel: document.querySelector("#state-label"),
  stageOverline: document.querySelector("#stage-overline"),
  stageTitle: document.querySelector("#stage-title"),
  stageDescription: document.querySelector("#stage-description"),
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
};

const MODE_COPY = {
  landing: {
    label: "Ready",
    overline: "Factory standing by",
    title: "Your physics toy will take shape here.",
    description: "Start with a prompt below. Preview execution stays locked until the verified-preview phase.",
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
  ready: {
    label: "Verified",
    overline: "Checker passed",
    title: "The sketch is verified and ready.",
    description: "Source and run evidence are available above. Browser preview remains locked until Phase 4.",
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
  const mutating = ["connecting", "running", "reconnecting"].includes(mode) || activeRun();
  const canCreate = mode === "landing" && state.healthReady && state.session?.state === "empty";
  elements.createButton.disabled = !canCreate;
  elements.prompt.disabled = !canCreate;
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

function showFormError(message) {
  elements.formError.textContent = message;
  elements.formError.hidden = false;
}

function clearFormError() {
  elements.formError.textContent = "";
  elements.formError.hidden = true;
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

function resetRunState(runId) {
  state.runId = runId;
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

function connectRun(runId) {
  if (state.eventSource) {
    state.eventSource.close();
  }
  if (state.runId !== runId) {
    resetRunState(runId);
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
    setMode("running");
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
    resetRunState(payload.run_id);
    connectRun(payload.run_id);
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
    state.runId = null;
    state.graph = null;
    state.nodeCache = new Map();
    state.seen = new Set();
    clearActivity();
    elements.prompt.value = "";
    updatePromptCount();
    hideBanner();
    setMode(state.healthReady ? "landing" : "degraded");
  } catch (error) {
    showBanner(error.message || "The factory could not reset the session.");
    updateControls();
  }
}

function latestRunId(session) {
  const runs = Array.isArray(session?.runs) ? session.runs : [];
  return session?.active_run_id || runs.at(-1)?.run_id || null;
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

    const runId = latestRunId(state.session);
    if (runId) {
      resetRunState(runId);
      connectRun(runId);
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
window.addEventListener("pagehide", () => state.eventSource?.close());

updatePromptCount();
boot();
