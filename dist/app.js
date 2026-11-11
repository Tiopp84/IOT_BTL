const byId = (id) => document.getElementById(id);
const setText = (id, text) => {
  if (byId(id).textContent !== text) byId(id).textContent = text;
};
const number = (value, digits = 1) => Number.isFinite(value) ? value.toFixed(digits) : "—";
const clock = new Intl.DateTimeFormat("vi-VN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const formatTime = (stamp) => clock.format(new Date(stamp * 1000));

const riskLabels = {
  SAFE: "An toàn",
  ATTENTION: "Cần chú ý",
  FIRE_ALERT: "Cảnh báo cháy",
};
const healthLabels = {
  OK: "Đang nhận dữ liệu",
  WARMUP: "Khởi tạo",
  SENSOR_ERROR: "Lỗi cảm biến",
  OFFLINE: "Mất dữ liệu",
};
const tones = { SAFE: "green", ATTENTION: "amber", FIRE_ALERT: "red" };
const climate = [["temperature_c", "#ffbf69"], ["humidity_pct", "#5dd8ef"]];
const dust = [["pm1_ug_m3", "#8aa1ff"], ["pm25_ug_m3", "#bf9cff"]];
let snapshot = null;
let streamOnline = false;
let journal = null;
const selector = byId("device");
const source = byId("source");
let stream;

function render() {
  const simulated = source.value !== "mqtt";
  const connected = streamOnline && snapshot?.mqtt_connected;
  byId("connection").dataset.tone = simulated ? "amber" : connected ? "green" : "muted";
  const connectionText = simulated
    ? (streamOnline ? "Đang giả lập · 1 Hz" : "Giả lập · đang kết nối")
    : (streamOnline ? (connected ? "MQTT trực tuyến" : "MQTT gián đoạn") : "Đang kết nối web");
  setText("connection-text", connectionText);
  byId("restart-simulation").hidden = !simulated;
  byId("simulation-phase").hidden = !simulated;
  setText("simulation-phase", streamOnline ? snapshot?.simulation_phase ?? "" : "Đang khởi tạo kịch bản giả lập…");

  const devices = snapshot?.devices ?? {};
  const keys = Object.keys(devices).sort();
  const optionKeys = Array.from(selector.options, (option) => option.value).filter(Boolean);
  if (keys.join("|") !== optionKeys.join("|")) {
    const selected = selector.value;
    const options = keys.map((id) => new Option(id, id));
    selector.replaceChildren(...(keys.length ? options : [new Option("Chờ thiết bị", "")]));
    selector.disabled = !keys.length;
    if (keys.includes(selected)) selector.value = selected;
  }

  const device = devices[selector.value];
  const current = device?.current;
  const health = current && (streamOnline ? current.health : "OFFLINE");
  const latched = current?.latched;
  const live = health && health !== "OFFLINE" && health !== "SENSOR_ERROR";
  const risk = live ? current.measured_risk_level : null;
  byId("alarm").dataset.tone = tones[risk] ?? "muted";
  setText("risk", riskLabels[risk] ?? (health === "OK" ? "Đang đánh giá" : healthLabels[health] ?? "Chờ dữ liệu"));
  const reason = !streamOnline ? "Đang kết nối lại với backend." : current?.measured_reason ?? "Đang chờ bản tin MQTT từ thiết bị.";
  setText("reason", reason);
  setText("health", healthLabels[health] ?? "Chưa có dữ liệu");
  setText("updated", current ? `Mẫu cuối · ${formatTime(current.timestamp)}` : "Mẫu cuối · —");
  setText("score", number(live ? current.p_fire : null, 3));
  byId("meter-fill").style.width = live && Number.isFinite(current.p_fire) ? `${current.p_fire * 100}%` : "0%";
  setText("votes", live ? `${current.votes}/${snapshot.window} phiếu · ${current.vote_samples}/${snapshot.window} mẫu` : "— phiếu");
  setText("threshold", snapshot ? `Ngưỡng cao · ${number(snapshot.threshold, 2)}` : "Ngưỡng cao · —");
  setText("low-threshold", snapshot
    ? `Chú ý · ≥ ${number(snapshot.low_threshold, 2)} × ${snapshot.attention_samples} mẫu liên tiếp`
    : "Mức chú ý · —");
  byId("high-marker").hidden = !snapshot;
  if (snapshot) byId("high-marker").style.left = `${snapshot.threshold * 100}%`;
  setText("latch", `Chốt báo cháy · ${current ? (latched ? "Đang giữ" : "Tắt") : "—"}`);
  byId("latch").dataset.tone = latched ? "red" : "muted";
  for (const [key] of [...climate, ...dust]) setText(key, number(live ? current[key] : null));
  const irRaw = live && Number.isFinite(current.ir_raw) ? String(current.ir_raw) : null;
  const irState = current?.ir_confirmed ? "Đã xác nhận" : current?.ir_active ? "Đang kích hoạt" : "Không kích hoạt";
  setText("ir", live ? irRaw ?? irState : "—");
  setText("ir-duration", live ? `${irState} · ${current.ir_active_duration_ms} ms` : "Thời gian kích hoạt · —");
  const features = live ? current.features : null;
  setText("d-temp", `Δ 5 mẫu · ${number(features?.d_temp_5)}`);
  setText("d-hum", `Δ 5 mẫu · ${number(features?.d_hum_5)}`);
  setText("std-pm1", `Độ lệch chuẩn (5 mẫu) · ${number(features?.pm1_roll_5_std, 2)}`);
  setText("std-pm25", `Độ lệch chuẩn (5 mẫu) · ${number(features?.pm25_roll_5_std, 2)}`);
  renderFeatures(features);
  renderVotes(live ? current.score_window : []);
  drawChart("climate", device?.history ?? [], climate, true);
  drawChart("dust", device?.history ?? [], dust, false);
  renderJournal(device?.events ?? []);
}

function renderFeatures(values) {
  const keys = snapshot?.feature_names ?? [];
  setText("feature-count", keys.length ? `${keys.length} ĐẶC TRƯNG` : "LIGHTGBM");
  const grid = byId("features");
  if (!keys.length) {
    grid.replaceChildren();
    return;
  }
  if (!grid.children.length) {
    grid.replaceChildren(...keys.map((key) => {
      const cell = document.createElement("div");
      const name = document.createElement("dt");
      name.textContent = key;
      cell.append(name, document.createElement("dd"));
      return cell;
    }));
  }
  keys.forEach((key, index) => {
    grid.children[index].lastElementChild.textContent = number(values?.[key], 3);
  });
}

function renderVotes(scores) {
  const windowSize = snapshot?.window ?? 0;
  if (snapshot) {
    setText("vote-rule", `Cần ít nhất ${snapshot.required_votes} điểm ≥ ${number(snapshot.threshold, 2)} trong đủ ${windowSize} mẫu.`);
  } else {
    setText("vote-rule", "Đang chờ cấu hình từ backend.");
  }
  byId("vote-window").replaceChildren(...Array.from({ length: windowSize }, (_, index) => {
    const score = scores[index - (windowSize - scores.length)];
    const slot = document.createElement("li");
    const label = document.createElement("small");
    label.textContent = index === windowSize - 1 ? "Mới nhất" : `Ô ${index + 1}`;
    slot.dataset.high = Number.isFinite(score) && score >= snapshot.threshold;
    slot.append(label, document.createTextNode(number(score, 3)));
    return slot;
  }));
}

function renderJournal(events) {
  const serialized = JSON.stringify(events);
  if (serialized === journal) return;
  journal = serialized;
  const list = byId("activity");
  if (!events.length) {
    const item = document.createElement("li");
    item.className = "empty";
    item.textContent = "Chưa có sự kiện.";
    list.replaceChildren(item);
    return;
  }
  list.replaceChildren(...events.map((event) => {
    const item = document.createElement("li");
    const dot = document.createElement("i");
    const label = document.createElement("strong");
    const time = document.createElement("time");
    const reason = document.createElement("p");
    dot.className = "event-dot";
    item.dataset.tone = tones[event.risk_level] ?? "muted";
    label.textContent = riskLabels[event.risk_level] ?? healthLabels[event.health];
    if (event.risk_level && event.health !== "OK") label.textContent += ` · ${healthLabels[event.health]}`;
    time.textContent = formatTime(event.timestamp);
    reason.textContent = event.reason;
    item.append(dot, label, time, reason);
    return item;
  }));
}

function svgNode(tag, attributes, text) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
  if (text !== undefined) element.textContent = text;
  return element;
}

function drawChart(id, history, series, dualAxis) {
  const chart = byId(`${id}-chart`);
  chart.replaceChildren();
  const latest = history.at(-1);
  const samples = latest ? history.filter((sample) => sample.boot_id === latest.boot_id
    && Number.isFinite(sample.uptime_ms) && latest.uptime_ms - sample.uptime_ms <= 30000) : [];
  const values = series.map(([key]) => samples.map((sample) => sample[key]).filter(Number.isFinite));
  const hasData = values.some((points) => points.length);
  byId(`${id}-empty`).hidden = hasData;
  if (!hasData) return;

  const left = 50, right = 510, top = 14, bottom = 130;
  const end = latest.timestamp;
  const start = end - 30;
  const x = (sample) => left + (30000 - (latest.uptime_ms - sample.uptime_ms)) / 30000 * (right - left);
  const range = (points) => {
    if (!points.length) return [0, 1];
    const low = Math.floor(Math.min(...points));
    return [low, Math.max(low + 1, Math.ceil(Math.max(...points)))];
  };
  const ranges = dualAxis ? values.map(range) : [range(values.flat())];
  const y = (value, [low, high]) => bottom - (value - low) / (high - low) * (bottom - top);
  for (let tick = 0; tick <= 4; tick++) {
    const position = bottom - (bottom - top) * tick / 4;
    chart.append(svgNode("line", { x1: left, x2: right, y1: position, y2: position, stroke: "#28354d" }));
    ranges.forEach(([low, high], index) => {
      const label = svgNode("text", { x: index ? right + 6 : left - 6, y: position + 4, "text-anchor": index ? "start" : "end" }, number(low + (high - low) * tick / 4));
      if (dualAxis) label.style.fill = series[index][1];
      chart.append(label);
    });
  }
  for (const stamp of [start, start + 15, end]) {
    chart.append(svgNode("text", { x: left + (stamp - start) / 30 * (right - left), y: 150, "text-anchor": "middle" }, formatTime(stamp)));
  }
  series.forEach(([key, color], index) => {
    let path = "", previous = null;
    const markers = [];
    for (const sample of samples) {
      if (!Number.isFinite(sample[key])) {
        previous = null;
        continue;
      }
      const continuous = previous && sample.boot_id === previous.boot_id
        && sample.seq === previous.seq + 1 && sample.uptime_ms - previous.uptime_ms <= 2500;
      const cx = x(sample), cy = y(sample[key], ranges[dualAxis ? index : 0]);
      path += `${continuous ? "L" : "M"} ${cx} ${cy} `;
      const point = svgNode("circle", { cx, cy, r: 2.3, fill: color });
      point.append(svgNode("title", {}, `${formatTime(sample.timestamp)} · ${number(sample[key], 2)}`));
      markers.push(point);
      previous = sample;
    }
    chart.append(svgNode("path", { d: path, stroke: color, "stroke-width": 2, fill: "none" }), ...markers);
  });
}

selector.addEventListener("change", render);

function connectSource() {
  stream?.close();
  snapshot = null;
  streamOnline = false;
  render();
  const endpoint = source.value === "mqtt"
    ? "/api/events"
    : `/api/simulation/events?scenario=${encodeURIComponent(source.value)}`;
  const connection = new EventSource(endpoint);
  stream = connection;
  connection.onmessage = (event) => {
    if (stream !== connection) return;
    snapshot = JSON.parse(event.data);
    streamOnline = true;
    render();
  };
  connection.onerror = () => {
    if (stream !== connection) return;
    streamOnline = false;
    render();
  };
}

source.addEventListener("change", connectSource);
byId("restart-simulation").addEventListener("click", connectSource);
connectSource();
