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
  document.querySelector("#run-state").textContent = !online ? "Disconnected" : failed ? "Stopped · error" : running ? "Running" : "Paused";
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
    }
  } finally {
    commandPending = false;
    updateControls();
  }
}

document.querySelector("#play").addEventListener("click", () => command("play"));
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
