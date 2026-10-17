const canvas = document.querySelector("#world");
const context = canvas.getContext("2d");
const offscreen = document.createElement("canvas");
const pixels = offscreen.getContext("2d");
const terrainColors = ["#b7ad70", "#456b48", "#276f8d", "#777d78"];
const terrainNames = ["Plain", "Forest", "Water", "Mountain"];
const barColors = { plain: "#b7ad70", forest: "#456b48", water: "#3d95b4", mountain: "#858b85" };
let frame = null;
let socket = null;
let reconnectTimer = null;
let mapBounds = null;
let control = null;
let online = false;
let commandPending = false;
let connectionEpoch = 0;
let lastRevision = -1;
let runId = null;
let selectedAgentId = null;
let lastSelectedAgent = null;
let lastSeenTick = null;
let activeConfiguration = null;
let coreCatalog = [];
let resultsSignature = null;
let historyRequest = 0;
let summaryRequest = 0;
let seenCompletions = new Set();
try {
  seenCompletions = new Set(JSON.parse(sessionStorage.getItem("alife:seen-completions") || "[]"));
} catch { /* Results remain available when browser storage is disabled. */ }

async function refreshResults() {
  const request = ++historyRequest;
  try {
    const response = await fetch("/api/results", { signal: AbortSignal.timeout(15000) });
    if (!response.ok) throw new Error("Could not load saved results.");
    const data = await response.json();
    if (request !== historyRequest) return;
    const select = document.querySelector("#saved-result");
    const previous = select.value;
    select.replaceChildren(...data.results.map(result => {
      const option = document.createElement("option");
      option.value = result.result_id;
      const state = result.result_id === control?.active_result_id ? "running" : result.status;
      const coreLabel = coreCatalog.find(core => core.id === result.core)?.label || result.core;
      option.textContent = `${state} · ${coreLabel} · seed ${result.seed} · ${result.started_at.slice(0, 19).replace("T", " ")} UTC`;
      return option;
    }));
    if ([...select.options].some(option => option.value === previous)) select.value = previous;
    document.querySelector("#open-result").disabled = !select.value;
    document.querySelector("#results-status").textContent = data.results.length
      ? `${data.results.length} saved executions. Earlier summaries remain available after replacing the world.`
      : "No executions saved yet. Start an experiment or use Run & pause.";
  } catch (error) {
    if (request === historyRequest) document.querySelector("#results-status").textContent = error.message;
  }
}

function resultChart(metrics) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 720 270");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Living population by simulation tick");
  if (!metrics.length) return svg;
  const first = metrics[0].tick;
  const last = metrics.at(-1).tick;
  const maximum = metrics.reduce((max, row) => Math.max(max, row.population), 1);
  function mark(tag, attributes, text) {
    const node = document.createElementNS(ns, tag);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
    if (text !== undefined) node.textContent = text;
    svg.append(node);
  }
  mark("title", {}, "Sampled population trajectory");
  mark("path", { d: "M50 30V220H680", fill: "none", stroke: "currentColor" });
  const points = metrics.map(row => `${50 + 630 * (row.tick - first) / Math.max(1, last - first)},${220 - 190 * row.population / maximum}`).join(" ");
  mark("polyline", { points, fill: "none", stroke: "#5bb9d4", "stroke-width": 3 });
  mark("circle", { cx: 50, cy: 220 - 190 * metrics[0].population / maximum, r: 3, fill: "#5bb9d4" });
  for (const [x, y, text, anchor] of [[8, 35, maximum, "start"], [25, 225, 0, "start"], [50, 244, first, "start"], [680, 244, last, "end"], [360, 265, "Tick", "middle"]]) {
    mark("text", { x, y, fill: "currentColor", "text-anchor": anchor }, text);
  }
  return svg;
}

async function showResult(resultId) {
  const request = ++summaryRequest;
  const dialog = document.querySelector("#result-dialog");
  document.querySelector("#result-heading").textContent = "Experiment summary";
  document.querySelector("#result-description").textContent = "Loading saved execution…";
  document.querySelector("#result-metrics").replaceChildren();
  document.querySelector("#result-chart").replaceChildren();
  document.querySelector("#result-configuration").textContent = "";
  document.querySelector("#result-links").classList.add("hidden");
  if (!dialog.open) dialog.showModal();
  try {
    const response = await fetch(`/api/results/${encodeURIComponent(resultId)}`, { signal: AbortSignal.timeout(15000) });
    if (!response.ok) throw new Error("Could not load this result. Check the server and retry from execution history.");
    const result = await response.json();
    if (request !== summaryRequest) return;
    const s = result.summary;
    document.querySelector("#result-heading").textContent = `Experiment ${s.status}`;
    document.querySelector("#result-description").textContent = `${s.reason} Execution ${s.result_id}. Ticks ${s.start_tick}–${s.end_tick}; seed ${s.seed}.`;
    const metrics = {
      "Decision core": coreCatalog.find(core => core.id === s.core)?.label || s.core,
      "Elapsed ticks": s.elapsed_ticks,
      "Elapsed seconds": s.elapsed_seconds,
      "Initial population": s.initial_population,
      "Final population": s.final_population,
      "Population change": s.population_change,
      "Births": s.births,
      "Deaths": s.deaths,
      "Extinction": s.extinct === null ? "Unknown" : s.extinct ? `Yes (tick ${s.extinction_tick ?? "unknown"})` : "No",
      "Peak population": s.peak_population,
      "Final average energy": s.average_energy,
    };
    for (const [label, value] of Object.entries(metrics)) {
      const item = document.createElement("div");
      const term = document.createElement("dt");
      const detail = document.createElement("dd");
      term.textContent = label;
      detail.textContent = value ?? "Unavailable";
      item.append(term, detail);
      document.querySelector("#result-metrics").append(item);
    }
    document.querySelector("#result-chart").replaceChildren(resultChart(result.metrics));
    document.querySelector("#result-configuration").textContent = JSON.stringify(result.manifest.configuration, null, 2);
    document.querySelector("#result-report").href = `/api/results/${encodeURIComponent(resultId)}/report`;
    document.querySelector("#result-download").href = `/api/results/${encodeURIComponent(resultId)}/download`;
    document.querySelector("#result-links").classList.remove("hidden");
  } catch (error) {
    if (request === summaryRequest) document.querySelector("#result-description").textContent = error.message;
  }
}

function updateResults() {
  const notice = document.querySelector("#results-error");
  notice.textContent = control.results_error || "";
  notice.classList.toggle("hidden", !control.results_error);
  const signature = JSON.stringify([control.active_result_id, control.latest_result?.result_id]);
  if (signature !== resultsSignature) {
    resultsSignature = signature;
    refreshResults();
  }
  const completed = control.completed_result_id;
  if (completed && !seenCompletions.has(completed)) {
    seenCompletions.add(completed);
    try { sessionStorage.setItem("alife:seen-completions", JSON.stringify([...seenCompletions].slice(-200))); } catch { /* Optional persistence. */ }
    showResult(completed);
  }
}
const setupFields = [
  ["world.seed", "Seed", 0, 9007199254740991, 1],
  ["world.width", "World width", 4, 4096, 1],
  ["world.height", "World height", 4, 4096, 1],
  ["agents.initial_population", "Founder population", 0, 1000000, 1],
  ["agents.initial_energy", "Founder energy", 0.001, null, "any"],
  ["agents.vision_radius", "Base vision radius", 1, 50, "any"],
  ["agents.idle_probability", "Random core: idle probability", 0, 1, "any"],
  ["agents.move_energy_cost", "Movement energy cost", 0, null, "any"],
  ["world.resources.food.initial_nodes", "Food nodes", 0, null, 1],
  ["world.resources.food.capacity", "Food capacity per node", 0.001, null, "any"],
  ["world.resources.food.regeneration_rate", "Food regeneration / tick", 0, null, "any"],
  ["world.resources.water.initial_nodes", "Water nodes (no thirst yet)", 0, null, 1],
  ["world.generation.water_level", "Terrain water level", 0, 1, "any"],
  ["world.generation.mountain_level", "Terrain mountain level", 0, 1, "any"],
  ["life.max_population", "Population cap", 1, null, 1],
  ["life.metabolism_cost", "Base metabolism / tick", 0, null, "any"],
  ["life.reproduction_min_age", "Minimum reproduction age", 1, null, 1],
  ["life.reproduction_cooldown", "Reproduction cooldown", 1, null, 1],
  ["life.mutation_probability", "Mutation probability", 0, 1, "any"],
  ["life.mutation_sigma", "Mutation strength", 0, 1, "any"],
];

function pathValue(config, path) {
  return path.split(".").reduce((value, key) => value[key], config);
}

function resetSetup() {
  if (!activeConfiguration) return;
  const select = document.querySelector("#setup-core");
  select.replaceChildren(...coreCatalog.map(core => {
    const option = document.createElement("option");
    option.value = core.id;
    option.textContent = core.label;
    return option;
  }));
  select.value = activeConfiguration.agents.brain;
  document.querySelector("#setup-inputs").replaceChildren(...setupFields.map(([path, title, min, max, step]) => {
    const label = document.createElement("label");
    label.textContent = title;
    const input = document.createElement("input");
    input.type = "number";
    input.dataset.path = path;
    input.min = min;
    if (max !== null) input.max = max;
    input.step = step;
    input.required = true;
    input.value = pathValue(activeConfiguration, path);
    label.append(input);
    return label;
  }));
  document.querySelector("#setup-ticks").value = control.horizon;
  updateCoreDescription();
}

function updateCoreDescription() {
  const core = document.querySelector("#setup-core").value;
  document.querySelector("#core-description").textContent = coreCatalog.find(item => item.id === core)?.description || "";
  const idle = document.querySelector('[data-path="agents.idle_probability"]');
  if (idle) idle.disabled = core !== "random";
}

function colorForScalar(name, value) {
  let t = value;
  if (name === "temperature") t = Math.max(0, Math.min(1, (value + 10) / 45));
  if (name === "elevation") return interpolate([31, 62, 70], [223, 211, 172], t);
  if (name === "temperature") {
    if (t < .5) return interpolate([43, 106, 158], [224, 222, 158], t * 2);
    return interpolate([224, 222, 158], [176, 54, 44], (t - .5) * 2);
  }
  if (name === "moisture") return interpolate([78, 63, 43], [46, 154, 186], t);
  return interpolate([72, 54, 35], [151, 194, 88], t);
}

function interpolate(start, end, amount) {
  const value = Math.max(0, Math.min(1, amount));
  return start.map((channel, index) => Math.round(channel + (end[index] - channel) * value));
}

function draw() {
  if (!frame) return;
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.round(rect.width * dpr);
  canvas.height = Math.round(rect.height * dpr);
  context.setTransform(dpr, 0, 0, dpr, 0, 0);
  context.fillStyle = "#080c0b";
  context.fillRect(0, 0, rect.width, rect.height);

  offscreen.width = frame.width;
  offscreen.height = frame.height;
  const image = pixels.createImageData(frame.width, frame.height);
  const layerName = document.querySelector("#layer").value;
  const values = frame.layers[layerName];
  for (let index = 0; index < values.length; index += 1) {
    const color = layerName === "terrain"
      ? hexToRgb(terrainColors[values[index]])
      : colorForScalar(layerName, values[index]);
    const offset = index * 4;
    image.data[offset] = color[0];
    image.data[offset + 1] = color[1];
    image.data[offset + 2] = color[2];
    image.data[offset + 3] = 255;
  }
  pixels.putImageData(image, 0, 0);

  const scale = Math.min(rect.width / frame.width, rect.height / frame.height);
  const width = frame.width * scale;
  const height = frame.height * scale;
  const x = (rect.width - width) / 2;
  const y = (rect.height - height) / 2;
  mapBounds = { x, y, width, height, scale };
  context.imageSmoothingEnabled = false;
  context.drawImage(offscreen, x, y, width, height);

  if (document.querySelector("#resources").checked) {
    for (const resource of frame.resources) {
      const fraction = Math.max(.12, resource.amount / resource.capacity);
      context.fillStyle = resource.type === "food" ? `rgba(210, 235, 94, ${.45 + fraction * .5})` : `rgba(92, 211, 244, ${.45 + fraction * .5})`;
      context.beginPath();
      context.arc(x + (resource.x + .5) * scale, y + (resource.y + .5) * scale, Math.max(1.5, scale * .22), 0, Math.PI * 2);
      context.fill();
    }
  }
  for (const agent of frame.agents || []) {
    context.fillStyle = agent.generation > 0 ? "#f9dc73" : "#ff855c";
    context.strokeStyle = "rgba(38, 20, 15, .8)";
    context.lineWidth = Math.max(.6, scale * .08);
    context.beginPath();
    context.arc(x + (agent.x + .5) * scale, y + (agent.y + .5) * scale, Math.max(2.8, scale * .36), 0, Math.PI * 2);
    context.fill();
    context.stroke();
  }
  const selected = (frame.agents || []).find(agent => agent.id === selectedAgentId);
  if (selected) {
    context.strokeStyle = "#ffffff";
    context.lineWidth = 2;
    context.beginPath();
    context.arc(x + (selected.x + .5) * scale, y + (selected.y + .5) * scale, Math.max(6, scale * .65), 0, Math.PI * 2);
    context.stroke();
  }
  updateLegend(layerName);
}

function hexToRgb(hex) {
  return [parseInt(hex.slice(1, 3), 16), parseInt(hex.slice(3, 5), 16), parseInt(hex.slice(5, 7), 16)];
}

function updateLegend(layerName) {
  const legend = document.querySelector("#legend");
  if (layerName === "terrain") {
    legend.innerHTML = terrainNames.map((name, index) => `<span><i class="swatch" style="background:${terrainColors[index]}"></i>${name}</span>`).join("");
  } else {
    const labels = layerName === "temperature" ? ["Cold", "Temperate", "Hot"] : ["Low", "Medium", "High"];
    const values = layerName === "temperature" ? [-10, 12.5, 35] : [0, .5, 1];
    legend.innerHTML = labels.map((name, index) => `<span><i class="swatch" style="background:rgb(${colorForScalar(layerName, values[index]).join(",")})"></i>${name}</span>`).join("");
  }
  legend.innerHTML += '<span><i class="swatch" style="background:#d2eb5e"></i>Food</span><span><i class="swatch" style="background:#5cd3f4"></i>Water source</span><span><i class="swatch" style="background:#f47e54;border-radius:50%"></i>Agent</span>';
}

function updateDashboard() {
  const stats = frame.statistics;
  document.querySelector("#tick").textContent = frame.tick.toLocaleString();
  document.querySelector("#dimensions").textContent = `${frame.width} × ${frame.height}`;
  document.querySelector("#temperature-range").textContent = `${stats.temperature_min}–${stats.temperature_max}°`;
  document.querySelector("#food-total").textContent = Math.round(stats.resource_totals.food).toLocaleString();
  document.querySelector("#water-total").textContent = Math.round(stats.resource_totals.water).toLocaleString();
  document.querySelector("#resource-nodes").textContent = stats.resource_nodes;
  const agentStats = frame.agent_statistics;
  document.querySelector("#population").textContent = agentStats.population.toLocaleString();
  document.querySelector("#average-energy").textContent = agentStats.average_energy.toFixed(1);
  document.querySelector("#average-age").textContent = agentStats.average_age.toFixed(1);
  document.querySelector("#brain-types").textContent = Object.entries(agentStats.brain_types).map(([name, count]) => `${count} ${name}`).join(" · ") || "No active brains";
  document.querySelector("#births").textContent = agentStats.births ?? 0;
  document.querySelector("#deaths").textContent = agentStats.deaths ?? 0;
  document.querySelector("#generation").textContent = agentStats.max_generation ?? 0;
  document.querySelector("#evolution-details").textContent = `Gene diversity: ${(agentStats.genetic_diversity ?? 0).toFixed(3)} · Orange: founders · Gold: offspring`;
  document.querySelector("#population-status").textContent = agentStats.population === 0 ? "Population extinct. Regenerate to start a new experiment." : "";
  const total = frame.width * frame.height;
  document.querySelector("#terrain-bars").innerHTML = Object.entries(stats.terrain_cells).map(([name, count]) => {
    const percentage = count / total * 100;
    return `<div class="terrain-row"><span>${name}</span><div class="bar"><i style="width:${percentage}%;background:${barColors[name]}"></i></div><span>${percentage.toFixed(0)}%</span></div>`;
  }).join("");
}

function acceptMessage(message) {
  if (message.kind === "heartbeat") return;
  if (!["world", "update", "status"].includes(message.kind)) return;
  if (!frame && message.kind !== "world") return;
  if (message.revision < lastRevision) return;
  const runChanged = message.run_id !== runId;
  if (message.run_id !== runId) {
    if (message.kind !== "world") return;
    clearSelection();
    runId = message.run_id;
    document.querySelector("#seed").value = message.control.seed;
    document.querySelector("#cell-details").textContent = "Move over the map to inspect a cell.";
  }
  lastRevision = message.revision;
  const previousError = control?.error;
  control = message.control;
  if (message.kind === "world") {
    activeConfiguration = message.run_configuration;
    coreCatalog = message.core_catalog;
    document.querySelector("#active-configuration").textContent = JSON.stringify(activeConfiguration, null, 2);
    const core = coreCatalog.find(item => item.id === activeConfiguration.agents.brain);
    const horizonLabel = control.checkpoint_origin
      ? `restored at tick ${control.checkpoint_origin.tick}` : `${control.horizon} ticks`;
    document.querySelector("#active-setup").textContent = `${core.label} · seed ${control.seed} · ${activeConfiguration.agents.initial_population} founders · ${horizonLabel}. Settings are fixed for this run.`;
    if (runChanged) {
      document.querySelector("#checkpoint-status").textContent = control.checkpoint_origin
        ? `Restored at tick ${control.checkpoint_origin.tick}. Use Run & pause to continue for additional ticks.` : "";
    }
    if (runChanged || !document.querySelector("#setup-inputs input")) resetSetup();
  }
  if (previousError && !control.error) showError("");
  setConnection(true);
  if (message.kind === "world") {
    frame = message;
  } else if (message.kind === "update") {
    const temperatureDelta = message.temperature_offset - frame.temperature_offset;
    frame.temperature_offset = message.temperature_offset;
    frame.tick = message.tick;
    frame.statistics = { ...frame.statistics, ...message.statistics };
    frame.agents = message.agents;
    frame.agent_statistics = message.agent_statistics;
    for (let index = 0; index < frame.layers.temperature.length; index += 1) {
      frame.layers.temperature[index] += temperatureDelta;
    }
    const amounts = new Map(message.resource_amounts);
    for (const resource of frame.resources) {
      resource.amount = amounts.get(resource.id) ?? resource.amount;
    }
  }
  updateControls();
  updateDashboard();
  updateAgentInspector();
  updateResults();
  draw();
}

function connect() {
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${protocol}://${location.host}/ws/world`);
  socket.addEventListener("open", () => {
    // The first full frame establishes a new revision sequence after reconnect.
    connectionEpoch += 1;
    frame = null;
    lastRevision = -1;
  });
  socket.addEventListener("message", event => {
    try {
      acceptMessage(JSON.parse(event.data));
    } catch (error) {
      showError("Could not display a world update: " + error.message);
    }
  });
  socket.addEventListener("error", () => setConnection(false));
  socket.addEventListener("close", () => {
    connectionEpoch += 1;
    setConnection(false);
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(connect, 1200);
  });
}

function setConnection(connected) {
  online = connected;
  const element = document.querySelector(".connection");
  element.classList.toggle("online", online);
  document.querySelector("#connection").textContent = online ? "Live connection" : "Reconnecting";
  updateControls();
}

function showError(message) {
  const element = document.querySelector("#control-error");
  element.textContent = message || "";
  element.classList.toggle("hidden", !message);
}

function updateControls() {
  const ready = online && frame && control && !commandPending;
  const running = control?.running;
  const failed = Boolean(control?.error);
  document.querySelector("#play").disabled = !ready || running || failed;
  document.querySelector("#pause").disabled = !ready || !running;
  document.querySelector("#step").disabled = !ready || running || failed;
  document.querySelector("#run").disabled = !ready || running || failed;
  document.querySelector("#set-speed").disabled = !ready || failed;
  document.querySelector("#regenerate").disabled = !ready;
  document.querySelector("#setup-fields").disabled = !ready || running;
  document.querySelector("#start-experiment").disabled = !ready || running || !activeConfiguration;
  document.querySelector("#save-checkpoint").disabled = !ready || running || failed;
  document.querySelector("#checkpoint-file").disabled = !ready || running;
  document.querySelector("#restore-checkpoint").disabled = !ready || running;
  document.querySelector("#run-state").textContent = !online ? "Disconnected" : failed ? "Stopped · error" : control?.results_error ? "Stopped · results error" : running ? "Running" : "Paused";
  const details = document.querySelector("#run-details");
  if (!online) {
    details.textContent = "Reconnecting… Keep the serve command running in your terminal.";
  } else if (control) {
    const target = control.target_tick;
    const goal = target === null ? (running ? "Continuous run" : "Click Play, Step, or Run & pause")
      : control.remaining_ticks === 0 ? "Reached tick " + target
      : control.remaining_ticks + " ticks remaining · stopping at " + target;
    const cost = control.last_tick_ms;
    const slow = running && cost > 1000 / control.ticks_per_second ? " · CPU limited" : "";
    details.textContent = goal + " · target " + control.ticks_per_second + " ticks/s · last tick " + cost.toFixed(1) + " ms" + slow;
    if (document.activeElement !== document.querySelector("#speed") && !commandPending) {
      document.querySelector("#speed").value = control.ticks_per_second;
    }
  }
  if (control?.error) showError(control.error);
}

async function command(name, body) {
  if (commandPending || !online) return;
  const epoch = connectionEpoch;
  commandPending = true;
  showError("");
  document.querySelector("#setup-error").classList.add("hidden");
  updateControls();
  try {
    const response = await fetch(`/api/control/${name}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(15000),
    });
    const data = await response.json();
    if (!response.ok) {
      const detail = Array.isArray(data.detail) ? data.detail.map(item => item.msg).join("; ") : data.detail;
      throw new Error(detail || response.statusText);
    }
    if (epoch === connectionEpoch) acceptMessage(data);
  } catch (error) {
    if (epoch === connectionEpoch) {
      showError(error.message || "The command failed. Check that the server is running.");
      if (name === "new-experiment") {
        const notice = document.querySelector("#setup-error");
        notice.textContent = error.message;
        notice.classList.remove("hidden");
      }
    }
  } finally {
    commandPending = false;
    updateControls();
  }
}

document.querySelector("#play").addEventListener("click", () => command("play"));
async function checkpointCommand(restore) {
  if (commandPending || !online || control.running) return;
  const file = document.querySelector("#checkpoint-file").files[0];
  if (restore && !file) return;
  const notice = document.querySelector("#checkpoint-status");
  if (restore && file.size > 64 * 1024 * 1024) {
    notice.textContent = "Checkpoint exceeds the 64 MiB limit.";
    return;
  }
  const epoch = connectionEpoch;
  const expectedRun = runId;
  commandPending = true;
  notice.textContent = restore ? "Validating checkpoint…" : "Preparing checkpoint…";
  updateControls();
  try {
    const response = await fetch(restore
      ? `/api/control/restore-checkpoint?expected_run_id=${encodeURIComponent(expectedRun)}`
      : "/api/checkpoint", {
      method: restore ? "POST" : "GET",
      body: restore ? file : undefined,
      headers: restore ? { "Content-Type": "application/octet-stream" } : {},
      signal: AbortSignal.timeout(60000),
    });
    if (!response.ok) {
      const data = await response.json();
      throw new Error(typeof data.detail === "string" ? data.detail : "Invalid checkpoint.");
    }
    if (restore) {
      const data = await response.json();
      if (epoch === connectionEpoch) {
        acceptMessage(data);
        notice.textContent = `Restored at tick ${data.tick}, paused. Use Run & pause to continue for additional ticks.`;
        document.querySelector("#checkpoint-file").value = "";
      }
    } else {
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = response.headers.get("Content-Disposition")?.match(/filename="([^"]+)"/)?.[1] || "simulation.alife";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      notice.textContent = "Checkpoint download prepared. Keep the .alife file to restore this state later.";
    }
  } catch (error) {
    if (epoch === connectionEpoch) notice.textContent = error.message;
  } finally {
    commandPending = false;
    updateControls();
  }
}
document.querySelector("#save-checkpoint").addEventListener("click", () => checkpointCommand(false));
document.querySelector("#restore-checkpoint-form").addEventListener("submit", event => {
  event.preventDefault();
  checkpointCommand(true);
});
document.querySelector("#close-result").addEventListener("click", () => document.querySelector("#result-dialog").close());
document.querySelector("#refresh-results").addEventListener("click", refreshResults);
document.querySelector("#open-result").addEventListener("click", () => {
  const id = document.querySelector("#saved-result").value;
  if (id) showResult(id);
});
document.querySelector("#setup-core").addEventListener("change", updateCoreDescription);
document.querySelector("#reset-setup").addEventListener("click", resetSetup);
document.querySelector("#setup-form").addEventListener("submit", event => {
  event.preventDefault();
  if (!activeConfiguration) return;
  const config = structuredClone(activeConfiguration);
  for (const input of document.querySelectorAll("#setup-inputs input")) {
    if (input.disabled) continue;
    const keys = input.dataset.path.split(".");
    const key = keys.pop();
    const owner = keys.reduce((value, part) => value[part], config);
    owner[key] = Number(input.value);
  }
  config.agents.brain = document.querySelector("#setup-core").value;
  command("new-experiment", {
    expected_run_id: runId, world: config.world, agents: config.agents,
    life: config.life, ticks: Number(document.querySelector("#setup-ticks").value),
  });
});
document.querySelector("#pause").addEventListener("click", () => command("pause"));
document.querySelector("#step").addEventListener("click", () => command("step"));
document.querySelector("#regenerate-form").addEventListener("submit", event => {
  event.preventDefault();
  command("regenerate", { seed: Number(document.querySelector("#seed").value) });
});
document.querySelector("#run-form").addEventListener("submit", event => {
  event.preventDefault();
  command("run", { ticks: Number(document.querySelector("#run-ticks").value) });
});
document.querySelector("#speed-form").addEventListener("submit", event => {
  event.preventDefault();
  command("speed", { ticks_per_second: Number(document.querySelector("#speed").value) });
});
document.querySelector("#layer").addEventListener("change", draw);
document.querySelector("#resources").addEventListener("change", draw);
window.addEventListener("resize", draw);

canvas.addEventListener("mousemove", event => {
  if (!frame || !mapBounds) return;
  const x = Math.floor((event.offsetX - mapBounds.x) / mapBounds.scale);
  const y = Math.floor((event.offsetY - mapBounds.y) / mapBounds.scale);
  const hover = document.querySelector("#hover");
  if (x < 0 || y < 0 || x >= frame.width || y >= frame.height) {
    hover.classList.add("hidden");
    return;
  }
  const index = y * frame.width + x;
  const localResources = frame.resources.filter(item => item.x === x && item.y === y);
  const localAgents = (frame.agents || []).filter(item => item.x === x && item.y === y);
  const values = {
    Position: `${x}, ${y}`,
    Terrain: terrainNames[frame.layers.terrain[index]],
    Elevation: frame.layers.elevation[index].toFixed(3),
    Temperature: `${frame.layers.temperature[index].toFixed(1)} °C`,
    Moisture: frame.layers.moisture[index].toFixed(3),
    Fertility: frame.layers.fertility[index].toFixed(3),
    Resources: localResources.length ? localResources.map(item => `${item.type} ${item.amount.toFixed(1)}`).join(", ") : "none",
    Agents: localAgents.length ? localAgents.map(item => `#${item.id} · E ${item.energy.toFixed(1)} · age ${item.age} · gen ${item.generation ?? 0} · parent ${item.parent_id ?? "founder"}`).join(", ") : "none",
    Genes: localAgents.length && localAgents[0].genome ? Object.entries(localAgents[0].genome).map(([name, value]) => `${name}: ${value.toFixed(2)}`).join("; ") : "—",
  };
  document.querySelector("#cell-details").innerHTML = `<dl>${Object.entries(values).map(([key, value]) => `<dt>${key}</dt><dd>${value}</dd>`).join("")}</dl>`;
  hover.textContent = `${x}, ${y} · ${values.Terrain}`;
  hover.style.left = `${Math.min(event.offsetX + 20, canvas.clientWidth - 125)}px`;
  hover.style.top = `${Math.max(8, event.offsetY - 14)}px`;
  hover.classList.remove("hidden");
});
canvas.addEventListener("mouseleave", () => document.querySelector("#hover").classList.add("hidden"));

function clearSelection() {
  selectedAgentId = null;
  lastSelectedAgent = null;
  lastSeenTick = null;
  document.querySelector("#selected-agent-status").textContent = "No agent selected.";
  document.querySelector("#agent-details").replaceChildren();
  document.querySelector("#clear-agent").classList.add("hidden");
}

function updateAgentInspector() {
  if (selectedAgentId === null) return;
  const current = frame.agents.find(agent => agent.id === selectedAgentId);
  if (current) {
    lastSelectedAgent = current;
    lastSeenTick = frame.tick;
  }
  const agent = lastSelectedAgent;
  if (!agent) return;
  document.querySelector("#selected-agent-status").textContent = current
    ? "Following #" + agent.id + " · live at tick " + frame.tick
    : "Agent #" + agent.id + " has died. Last observed at tick " + lastSeenTick + ".";
  const values = {
    Position: agent.x + ", " + agent.y,
    Energy: agent.energy.toFixed(2),
    Health: agent.health.toFixed(2),
    Age: agent.age + " ticks",
    Brain: agent.brain,
    Generation: agent.generation,
    Parent: agent.parent_id ?? "Founder",
    ...Object.fromEntries(Object.entries(agent.genome).map(([name, value]) => [
      name.replaceAll("_", " "), value.toFixed(3),
    ])),
  };
  const list = document.createElement("dl");
  for (const [label, value] of Object.entries(values)) {
    const term = document.createElement("dt");
    const description = document.createElement("dd");
    term.textContent = label;
    description.textContent = value;
    list.append(term, description);
  }
  document.querySelector("#agent-details").replaceChildren(list);
  document.querySelector("#clear-agent").classList.remove("hidden");
}

canvas.addEventListener("click", event => {
  if (!frame || !mapBounds || !online) return;
  const x = Math.floor((event.offsetX - mapBounds.x) / mapBounds.scale);
  const y = Math.floor((event.offsetY - mapBounds.y) / mapBounds.scale);
  const agents = frame.agents.filter(agent => agent.x === x && agent.y === y);
  if (!agents.length) return;
  const index = agents.findIndex(agent => agent.id === selectedAgentId);
  selectedAgentId = agents[(index + 1) % agents.length].id;
  updateAgentInspector();
  draw();
});
document.querySelector("#clear-agent").addEventListener("click", () => {
  clearSelection();
  draw();
});

connect();
