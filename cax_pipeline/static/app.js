const state = {
  run: null,
  records: [],
  kind: "",
  layer: "",
  category: "",
  timer: null,
  apiKeyMemory: localStorage.getItem("cax.apiKey") || "",
  currentDetail: null,
};

const CATEGORY_OPTIONS = [
  ["geometry", "几何度量", "data"],
  ["topology", "拓扑结构", "data"],
  ["feature", "设计特征", "data"],
  ["assembly", "装配关系", "data"],
  ["simulation_method", "仿真方法", "data"],
  ["material", "材料与介质", "data"],
  ["boundary_condition", "边界与工况", "data"],
  ["mesh", "网格与离散", "data"],
  ["result", "仿真结果", "data"],
  ["derived_metric", "派生工程指标", "data"],
  ["visual_semantic", "视觉语义", "data"],
  ["asset", "派生资源", "data"],
  ["identity", "资源身份", "metadata"],
  ["source_file", "来源文件", "metadata"],
  ["technical", "技术信息", "metadata"],
  ["lineage", "处理链路", "metadata"],
  ["quality", "质量信息", "metadata"],
  ["capability", "能力声明", "metadata"],
];
const CATEGORY_LABELS = Object.fromEntries(CATEGORY_OPTIONS.map(([key, label]) => [key, label]));
const CATEGORY_LAYERS = Object.fromEntries(CATEGORY_OPTIONS.map(([key, , layer]) => [key, layer]));
const ORIGIN_LABELS = {
  declared: "源文件声明",
  extracted: "解析提取",
  computed: "计算",
  derived: "派生",
  inferred: "推断",
  observed: "系统观测",
};
const FACE_TYPE_LABELS = {
  PLANE: "平面",
  CYLINDER: "圆柱面",
  CONE: "圆锥面",
  SPHERE: "球面",
  TORUS: "圆环面",
  BSPLINE: "B 样条面",
  REVOLUTION: "旋转面",
};
const FEATURE_TYPE_LABELS = {
  cylindrical: "圆柱特征",
  pattern: "阵列",
  round: "圆角",
  chamfer: "倒角",
};
const TOPOLOGY_LABELS = {
  solids: "实体",
  shells: "壳体",
  faces: "面",
  edges: "边",
  vertices: "顶点",
};
const CAPABILITY_LABELS = {
  geometry: "几何",
  mesh: "网格",
  setup: "设置",
  results: "结果",
  render: "渲染",
  vision: "视觉分析",
};

const $ = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");

function toast(message, bad = false) {
  const el = $("toast");
  el.textContent = message;
  el.className = `toast${bad ? " bad" : ""}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.add("hidden"), 4200);
}

function formatCount(value) {
  const number = Number(value || 0);
  if (!Number.isFinite(number)) return "0";
  if (number >= 1_000_000) return `${(number / 1_000_000).toFixed(2)}M`;
  if (number >= 10_000) return `${(number / 1_000).toFixed(1)}K`;
  return number.toLocaleString("zh-CN");
}

function formatDuration(milliseconds) {
  const value = Number(milliseconds || 0);
  if (!Number.isFinite(value) || value <= 0) return "0 ms";
  if (value < 1000) return `${value.toFixed(value < 10 ? 2 : 0)} ms`;
  if (value < 60_000) return `${(value / 1000).toFixed(2)} s`;
  const minutes = Math.floor(value / 60_000);
  const seconds = (value % 60_000) / 1000;
  return `${minutes} min ${seconds.toFixed(0)} s`;
}

function recordQuery() {
  const params = new URLSearchParams();
  if (state.kind) params.set("kind", state.kind);
  if (state.layer) params.set("layer", state.layer);
  if (state.category) params.set("category", state.category);
  return params.toString() ? `?${params}` : "";
}

function populateCategoryFilter() {
  const select = $("categoryFilter");
  const groups = [
    ["数据", "data"],
    ["元数据", "metadata"],
  ];
  for (const [label, layer] of groups) {
    const group = document.createElement("optgroup");
    group.label = label;
    for (const [key, name, optionLayer] of CATEGORY_OPTIONS) {
      if (optionLayer !== layer) continue;
      const option = document.createElement("option");
      option.value = key;
      option.textContent = name;
      group.appendChild(option);
    }
    select.appendChild(group);
  }
  select.addEventListener("change", async () => {
    state.category = select.value;
    if (state.category) {
      $("layerFilter").value = CATEGORY_LAYERS[state.category] || "";
      state.layer = $("layerFilter").value;
    }
    await applyRecordFilters();
  });
}

async function applyRecordFilters() {
  if (!state.run) return;
  if ($("query").value.trim()) await search();
  else await refreshRun();
}

function credentials() {
  const headers = {};
  const key = $("apiKey").value.trim();
  if (key) headers["X-API-Key"] = key;
  return headers;
}

function configBody() {
  return {
    base_url: $("baseUrl").value.trim(),
    model: $("model").value.trim(),
    timeout: Number($("timeout").value || 120),
  };
}

async function request(url, options = {}) {
  const response = await fetch(url, { cache: "no-store", ...options });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `请求失败 (${response.status})`);
  }
  return data;
}

async function loadConfig() {
  try {
    const data = await request("/api/config");
    $("baseUrl").value = data.base_url || "https://api.deepseek.com";
    $("model").value = data.model || "deepseek-flash";
    $("timeout").value = data.timeout || 120;
    $("configState").textContent = "接口配置已加载；留空 API Key 时使用本地配置。";
    $("apiKey").value = state.apiKeyMemory;
    $("rememberKey").checked = Boolean(state.apiKeyMemory);
  } catch (error) {
    $("configState").textContent = error.message;
  }
}

async function checkHealth(showError = true) {
  try {
    await request("/api/health");
    $("health").textContent = "服务正常";
    $("health").className = "status-pill ok";
    const active = state.run && ["queued", "running"].includes(state.run.status);
    $("scanBtn").disabled = Boolean(active);
  } catch {
    $("health").textContent = "服务未启动";
    $("health").className = "status-pill bad";
    $("scanBtn").disabled = true;
    if (showError) {
      toast("服务未启动，请双击“启动前端.cmd”或运行 python -m cax_pipeline.cli --serve", true);
    }
  }
}

function saveBrowserKey() {
  const key = $("apiKey").value.trim();
  if ($("rememberKey").checked && key) {
    localStorage.setItem("cax.apiKey", key);
    state.apiKeyMemory = key;
  } else {
    localStorage.removeItem("cax.apiKey");
    state.apiKeyMemory = "";
  }
}

async function saveConfig() {
  try {
    const data = await request("/api/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json", ...credentials() },
      body: JSON.stringify(configBody()),
    });
    $("configState").textContent = "接口配置已保存。";
    toast("API 配置已保存到 config.local.json");
  } catch (error) {
    toast(error.message, true);
  }
}

async function testConfig() {
  try {
    await request("/api/config/test", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...credentials() },
      body: JSON.stringify(configBody()),
    });
    toast("API 连接成功");
  } catch (error) {
    toast(error.message, true);
  }
}

async function startScan() {
  const workspace = $("workspace").value.trim();
  if (!workspace) {
    toast("请输入工作区目录", true);
    return;
  }
  saveBrowserKey();
  $("scanBtn").disabled = true;
  try {
    const run = await request("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...credentials() },
      body: JSON.stringify({
        workspace,
        with_vision: $("withVision").checked,
        ...configBody(),
      }),
    });
    state.run = run;
    state.records = [];
    state.layer = "";
    state.category = "";
    $("layerFilter").value = "";
    $("categoryFilter").value = "";
    $("cancelBtn").disabled = false;
    $("exportBtn").disabled = true;
    $("runStatus").classList.remove("hidden");
    renderRecords([]);
    await refreshRun();
    state.timer = setInterval(refreshRun, 1200);
  } catch (error) {
    $("scanBtn").disabled = false;
    toast(error.message, true);
  }
}

async function pickWorkspace() {
  const button = $("pickWorkspaceBtn");
  button.disabled = true;
  button.textContent = "选择中…";
  try {
    const data = await request("/api/workspace/pick", { method: "POST" });
    if (!data.cancelled && data.path) {
      $("workspace").value = data.path;
    }
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "选择目录";
  }
}

async function cancelScan() {
  if (!state.run) return;
  try {
    await request(`/api/runs/${state.run.id}/cancel`, { method: "POST" });
    toast("已请求取消");
  } catch (error) {
    toast(error.message, true);
  }
}

async function refreshRun() {
  if (!state.run) return;
  try {
    const run = await request(`/api/runs/${state.run.id}`);
    state.run = run;
    renderRunStatus(run);
    const records = await request(`/api/runs/${run.id}/records${recordQuery()}`);
    state.records = records.items || [];
    renderRecords(state.records);
    await refreshPerformanceSummary();
    if (["complete", "failed", "cancelled"].includes(run.status)) {
      clearInterval(state.timer);
      state.timer = null;
      $("scanBtn").disabled = false;
      $("cancelBtn").disabled = true;
    }
  } catch (error) {
    toast(error.message, true);
    clearInterval(state.timer);
    state.timer = null;
    $("scanBtn").disabled = false;
  }
}

async function refreshPerformanceSummary() {
  if (!state.run) return;
  try {
    const summary = await request(`/api/runs/${state.run.id}/performance/summary`);
    renderPerformanceSummary(summary);
  } catch {
    $("performanceCards").innerHTML = '<div class="performance-card"><span>性能数据</span><b>尚不可用</b></div>';
  }
}

function performanceCardsMarkup(summary) {
  const success = Number(summary.success_rate || 0) * 100;
  const cards = [
    ["总耗时", formatDuration(summary.total_elapsed_ms), `${summary.done || 0}/${summary.total || 0} 已处理`],
    ["成功率", `${success.toFixed(1)}%`, `失败 ${summary.failed || 0}`],
    ["LLM token", formatCount(summary.total_tokens), `${summary.llm_calls || 0} 次调用`],
    ["有效识别速率", `${Number(summary.effective_records_per_minute || 0).toFixed(1)} 文件/min`, "按已成功记录计算"],
    ["缓存命中", formatCount(summary.cache_hits), "未重复消耗模型调用"],
  ];
  return cards.map(([label, value, note]) => `
    <div class="performance-card">
      <span>${escapeHtml(label)}</span>
      <b>${escapeHtml(value)}</b>
      <small>${escapeHtml(note)}</small>
    </div>
  `).join("");
}

function renderPerformanceSummary(summary) {
  $("performanceCards").innerHTML = performanceCardsMarkup(summary);
}

function renderRunStatus(run) {
  const processed = (run.done || 0) + (run.failed || 0);
  const percent = run.total ? Math.min(100, processed / run.total * 100) : 0;
  $("progressBar").style.width = `${percent}%`;
  const labels = {
    queued: "等待处理",
    running: "正在解析和生成视觉描述",
    complete: "扫描完成",
    failed: "扫描失败",
    cancelled: "已取消",
  };
  $("runText").textContent = `${labels[run.status] || run.status} · ${run.workspace}`;
  $("runCounts").textContent = `成功 ${run.done || 0} / 失败 ${run.failed || 0} / 共 ${run.total || 0}`;
}

function renderRecords(items) {
  const grid = $("recordGrid");
  if (!items.length) {
    grid.innerHTML = `<div class="empty">${state.run ? "正在扫描，结果会逐步出现。" : "选择工作区并开始扫描。"}</div>`;
    $("resultMeta").textContent = state.run ? "暂无记录" : "尚未开始扫描";
    return;
  }
  const filterLabel = state.category
    ? ` · 分类：${CATEGORY_LABELS[state.category] || state.category}`
    : state.layer
      ? ` · 层级：${state.layer === "data" ? "数据" : "元数据"}`
      : "";
  $("resultMeta").textContent = `显示 ${items.length} 条记录${filterLabel}`;
  $("exportBtn").disabled = !state.run || items.length === 0;
  grid.innerHTML = items.map((item) => {
    const profile = item.visual_profile || {};
    const card = item.card || {};
    const title = item.title || profile.display_name_zh || item.relative_path;
    const summary = item.summary || "等待视觉摘要";
    const statusClass = item.status === "ok" ? "ok" : item.status === "failed" ? "failed" : "processing";
    const statusLabel = item.status === "ok" ? "完成" : item.status === "failed" ? "失败" : "处理中";
    const sourceLabel = item.from_cache
      ? (item.stale ? "已有报告 · 需更新" : "已有报告")
      : "本次生成";
    const sourceClass = item.stale ? "stale-tag" : "cache-tag";
    const sourceText = item.score
      ? `${sourceLabel} · 相关度 ${item.score}`
      : sourceLabel;
    const confidence = item.confidence == null ? "" : ` · 置信度 ${(item.confidence * 100).toFixed(0)}%`;
    const tags = (card.tags || []).slice(0, 3)
      .map((tag) => `<span class="chip">${escapeHtml(tag)}</span>`).join("");
    const classification = item.classification || {};
    const classTags = (classification.summary || []).slice(0, 4)
      .map((entry) => `<span class="chip ${entry.layer === "metadata" ? "metadata-chip" : "classification-chip"}">${escapeHtml(entry.label_zh || entry.category)} ${Number(entry.field_count || 0)}</span>`)
      .join("");
    const warnings = Number(card.warning_count || 0);
    return `
      <article class="record" data-record-id="${escapeHtml(item.id)}">
        <div class="thumb">${item.thumbnail_url
          ? `<img src="${escapeHtml(item.thumbnail_url)}" alt="${escapeHtml(title)} 预览">`
          : "无预览"}</div>
        <div class="record-body">
          <div class="record-top">
            <div class="record-heading">
              <div class="record-kicker">
                <span>${escapeHtml(item.kind.toUpperCase())} · ${escapeHtml(item.format)}</span>
                <span class="${statusClass}">${escapeHtml(statusLabel)}</span>
                ${warnings ? `<span class="failed">${warnings} 条警告</span>` : ""}
              </div>
              <h3 title="${escapeHtml(title)}">${escapeHtml(title)}</h3>
            </div>
            <div class="record-actions">
              <button type="button" class="mini-btn ghost regenerate-btn" data-record-id="${escapeHtml(item.id)}">重新生成</button>
              <button type="button" class="mini-btn ghost export-record-btn" data-export-url="${escapeHtml(item.export_url || "")}">导出</button>
            </div>
          </div>
          <div class="record-path" title="${escapeHtml(item.relative_path)}">${escapeHtml(item.relative_path)}</div>
          <div class="record-summary">${escapeHtml(summary)}</div>
          <div class="record-bottom">
            <div class="card-tags">${classTags}${tags}</div>
            <div class="record-foot">
              <span class="${sourceClass}">${escapeHtml(sourceText)}</span>
              <span>${escapeHtml(confidence)}</span>
            </div>
          </div>
        </div>
      </article>`;
  }).join("");
  grid.querySelectorAll(".record").forEach((card) => {
    card.addEventListener("click", () => openDetail(card.dataset.recordId));
  });
  grid.querySelectorAll(".regenerate-btn").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      regenerateRecord(button.dataset.recordId);
    });
  });
  grid.querySelectorAll(".export-record-btn").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      downloadUrl(button.dataset.exportUrl);
    });
  });
}

async function search() {
  if (!state.run) {
    toast("请先扫描一个工作区", true);
    return;
  }
  const query = $("query").value.trim();
  if (!query) {
    await refreshRun();
    return;
  }
  try {
    const params = new URLSearchParams({ q: query, limit: "100" });
    if (state.kind) params.set("kind", state.kind);
    if (state.layer) params.set("layer", state.layer);
    if (state.category) params.set("category", state.category);
    const data = await request(`/api/runs/${state.run.id}/search?${params}`);
    state.records = data.items || [];
    renderRecords(state.records);
    $("resultMeta").textContent = `“${query}”命中 ${data.total} 条（分类 + 结构化标签 + 文本匹配）`;
  } catch (error) {
    toast(error.message, true);
  }
}

function resolveJsonPointer(documentValue, pointer) {
  if (!pointer || !pointer.startsWith("/")) return undefined;
  let current = documentValue;
  for (const raw of pointer.slice(1).split("/")) {
    const token = raw.replaceAll("~1", "/").replaceAll("~0", "~");
    if (Array.isArray(current)) current = current[Number(token)];
    else if (current && typeof current === "object") current = current[token];
    else return undefined;
  }
  return current;
}

function formatDisplayNumber(value, digits = 2, fixed = false) {
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value ?? "暂无");
  if (fixed) return number.toFixed(digits);
  return number.toLocaleString("zh-CN", {
    maximumFractionDigits: Number.isInteger(number) ? 0 : digits,
  });
}

function formatBytes(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes)) return "暂无";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let amount = bytes;
  let index = 0;
  while (amount >= 1024 && index < units.length - 1) {
    amount /= 1024;
    index += 1;
  }
  return `${formatDisplayNumber(amount, index === 0 ? 0 : 2)} ${units[index]}`;
}

function formatDateValue(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value ?? "暂无");
  return date.toLocaleString("zh-CN", { hour12: false });
}

function summarizeCountMap(value, labels = {}) {
  return Object.entries(value || {})
    .filter(([, count]) => Number(count) > 0)
    .slice(0, 6)
    .map(([key, count]) => `${labels[key] || key} ${formatDisplayNumber(count, 0)}`)
    .join(" · ");
}

function summarizePlainObject(value) {
  const entries = Object.entries(value || {}).filter(([, item]) => item != null);
  if (!entries.length) return "无";
  const text = entries.slice(0, 4).map(([key, item]) => {
    let display;
    if (Array.isArray(item)) display = `${item.length} 项`;
    else if (item && typeof item === "object") display = `${Object.keys(item).length} 项`;
    else if (typeof item === "boolean") display = item ? "是" : "否";
    else if (typeof item === "number") display = formatDisplayNumber(item, 2);
    else display = String(item);
    return `${key.replaceAll("_", " ")}：${display}`;
  }).join("；");
  return entries.length > 4 ? `${text}；另有 ${entries.length - 4} 项` : text;
}

function formatObjectValue(value, facet) {
  const pointer = facet.json_pointer || "";
  if (pointer.endsWith("/bbox")) {
    const dimensions = [
      Number(value.xmax) - Number(value.xmin),
      Number(value.ymax) - Number(value.ymin),
      Number(value.zmax) - Number(value.zmin),
    ];
    const unit = facet.unit ? ` ${facet.unit}` : "";
    return `${dimensions.map((item) => formatDisplayNumber(item, 2, true)).join(" × ")}${unit}`;
  }
  if (pointer.endsWith("/topology")) {
    return summarizeCountMap(value, TOPOLOGY_LABELS) || "无拓扑统计";
  }
  if (pointer.endsWith("/validation")) {
    const warnings = Number((value.warnings || []).length);
    const errors = Number((value.errors || []).length);
    return `${value.is_valid ? "校验通过" : "校验失败"} · ${warnings} 条警告 · ${errors} 条错误`;
  }
  if (pointer.endsWith("/capabilities")) {
    const enabled = Object.entries(value)
      .filter(([, item]) => Boolean(item))
      .map(([key]) => CAPABILITY_LABELS[key] || key);
    return enabled.length ? enabled.join(" · ") : "未声明能力";
  }
  if (pointer.endsWith("/provenance")) {
    const steps = value.steps || [];
    const sources = value.source_files || [];
    const preview = steps.slice(0, 4).join(" → ");
    return `${steps.length} 个处理步骤 · ${sources.length} 个来源文件${preview ? ` · ${preview}` : ""}`;
  }
  if (pointer.endsWith("/profile")) {
    const name = value.display_name_zh || value.primary_class || "未知类别";
    const traits = (value.shape_traits || []).slice(0, 4);
    const aliases = [...(value.aliases_zh || []), ...(value.aliases_en || [])].slice(0, 4);
    return [
      name,
      traits.length ? `特征：${traits.join("、")}` : "",
      aliases.length ? `别名：${aliases.join("、")}` : "",
    ].filter(Boolean).join("；");
  }
  if (pointer.endsWith("/models")) {
    return summarizePlainObject(value);
  }
  if (pointer.endsWith("/materials") || facet.category === "material") {
    const names = Object.keys(value || {});
    return names.length
      ? `${names.length} 种材料：${names.slice(0, 5).join("、")}${names.length > 5 ? " 等" : ""}`
      : "未声明材料";
  }
  if (pointer.endsWith("/time")) {
    if (value.transient === true) return "瞬态分析";
    if (value.transient === false) return "稳态分析";
    return summarizePlainObject(value);
  }
  if (pointer.endsWith("/mesh")) {
    return [
      value.cells != null ? `单元 ${formatDisplayNumber(value.cells, 0)}` : "",
      value.nodes != null ? `节点 ${formatDisplayNumber(value.nodes, 0)}` : "",
      value.faces != null ? `面 ${formatDisplayNumber(value.faces, 0)}` : "",
      Object.keys(value.cell_types || {}).length
        ? `单元类型 ${Object.keys(value.cell_types).slice(0, 3).join("、")}`
        : "",
    ].filter(Boolean).join(" · ");
  }
  return summarizePlainObject(value);
}

function formatArrayValue(value, facet) {
  const pointer = facet.json_pointer || "";
  if (!value.length) return "无";
  if (pointer.endsWith("/features")) {
    const counts = {};
    for (const item of value) {
      const type = FEATURE_TYPE_LABELS[item.type] || item.type || "其他";
      counts[type] = (counts[type] || 0) + 1;
    }
    return `${value.length} 个：${Object.entries(counts).slice(0, 5).map(([type, count]) => `${type} ${count}`).join(" · ")}`;
  }
  if (pointer.endsWith("/face_types")) {
    return value.slice(0, 6).map((item) => `${FACE_TYPE_LABELS[item.type] || item.type} ${item.count}`).join(" · ");
  }
  if (pointer.endsWith("/rendered_views")) {
    return `${value.length} 个视图：${value.slice(0, 6).map((item) => item.view || "view").join("、")}`;
  }
  if (pointer.endsWith("/results")) return `${value.length} 个结果场`;
  if (pointer.endsWith("/zone_results")) return `${value.length} 条分区结果`;
  if (pointer.endsWith("/phases")) {
    const materials = [...new Set(value.map((item) => item.material).filter(Boolean))];
    return materials.length ? `${value.length} 相：${materials.join("、")}` : `${value.length} 相`;
  }
  const preview = value.slice(0, 3).map((item) => {
    if (item && typeof item === "object") return item.name || item.type || item.view || "对象";
    return String(item);
  });
  return `${value.length} 项${preview.length ? `：${preview.join("、")}` : ""}`;
}

function formatFacetValue(value, facet) {
  if (value == null) return "暂无";
  if (Array.isArray(value)) return formatArrayValue(value, facet);
  if (value && typeof value === "object") return formatObjectValue(value, facet);
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "number") {
    const key = `${facet.key || ""} ${facet.json_pointer || ""}`;
    if (key.endsWith("bytes")) return formatBytes(value);
    if (/volume|surface_area|dimensions|mass_properties/.test(key)) {
      return `${formatDisplayNumber(value, 2, true)}${facet.unit ? ` ${facet.unit}` : ""}`;
    }
    return `${formatDisplayNumber(value, facet.category === "geometry" ? 2 : 2)}${facet.unit ? ` ${facet.unit}` : ""}`;
  }
  if ((facet.json_pointer || "").endsWith("/sha256")) {
    return `${String(value).slice(0, 12)}…`;
  }
  if ((facet.json_pointer || "").endsWith("_at")) return formatDateValue(value);
  return String(value);
}

function renderClassificationCategory(category, facets, doc) {
  const limit = 16;
  const shown = facets.slice(0, limit);
  const rows = shown.map((item) => {
    const value = formatFacetValue(resolveJsonPointer(doc, item.json_pointer), item);
    const origin = ORIGIN_LABELS[item.origin] || item.origin;
    const confidence = item.confidence == null ? "" : ` · ${(item.confidence * 100).toFixed(0)}%`;
    return `
      <tr>
        <td>${escapeHtml(item.label_zh || item.key)}</td>
        <td>${escapeHtml(value)}</td>
        <td><span class="chip ${item.origin === "inferred" ? "origin-inferred" : ""}">${escapeHtml(origin)}${escapeHtml(confidence)}</span></td>
      </tr>`;
  }).join("");
  const remainder = facets.length > limit
    ? `<div class="classification-note">另有 ${facets.length - limit} 项完整数据保存在后台和导出文件中。</div>`
    : "";
  return `
    <section class="classification-category">
      <div class="classification-category-head">
        <h4>${escapeHtml(CATEGORY_LABELS[category] || category)}</h4>
        <span>${facets.length} 项</span>
      </div>
      <div class="classification-table-wrap">
        <table class="classification-table">
          <thead><tr><th>字段</th><th>值</th><th>来源</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      ${remainder}
    </section>`;
}

function renderClassification(classification, doc) {
  const facets = classification?.facets || [];
  if (!facets.length) return '<span class="chip">暂无分类</span>';
  const order = new Map(CATEGORY_OPTIONS.map(([key], index) => [key, index]));
  const grouped = new Map();
  for (const facet of facets) {
    const key = `${facet.layer}:${facet.category}`;
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key).push(facet);
  }
  const entries = [...grouped.entries()].sort(
    (left, right) => (order.get(left[0].split(":")[1]) ?? 999) - (order.get(right[0].split(":")[1]) ?? 999),
  );
  const renderLayer = (layer) => entries
    .filter(([key]) => key.startsWith(`${layer}:`))
    .map(([key, items]) => renderClassificationCategory(key.split(":")[1], items, doc))
    .join("");
  const dataTables = renderLayer("data");
  const metadataTables = renderLayer("metadata");
  return `
    ${dataTables ? `<div class="classification-category-stack">${dataTables}</div>` : ""}
    ${metadataTables ? `
      <details class="classification-metadata">
        <summary>元数据分类 <span>${(classification.summary || []).filter((item) => item.layer === "metadata").length} 类</span></summary>
        <div class="classification-category-stack">${metadataTables}</div>
      </details>` : ""}`;
}

function renderRecordPerformance(performance) {
  if (!performance || !performance.span_count) {
    return '<span class="chip">暂无性能数据</span>';
  }
  const stageEntries = Object.entries(performance.stage_ms || {})
    .sort((a, b) => b[1] - a[1])
    .slice(0, 3)
    .map(([, value]) => formatDuration(value))
    .join(" · ");
  return `
    <div class="performance-strip">
      <div class="modal-metric"><span>记录总耗时</span><b>${escapeHtml(formatDuration(performance.total_ms))}</b></div>
      <div class="modal-metric"><span>LLM 调用 / token</span><b>${Number(performance.llm_calls || 0)} / ${escapeHtml(formatCount(performance.tokens))}</b></div>
      <div class="modal-metric"><span>主要阶段</span><b>${escapeHtml(stageEntries || "无子阶段")}</b></div>
    </div>
    <button id="recordPerformanceBtn" class="mini-btn ghost" type="button">查看运行性能详情</button>`;
}

async function openDetail(recordId) {
  try {
    const detail = await request(`/api/runs/${state.run.id}/records/${recordId}`);
    state.currentDetail = detail;
    const doc = detail.json || {};
    const content = doc.content || {};
    const views = content.rendered_views || [];
    const card = detail.card || {};
    const visual = content.visual || {};
    const images = views
      .filter((view) => view.path && String(view.mime_type || "").startsWith("image/"))
      .slice(0, 6)
      .map((view) => `<img src="/api/runs/${encodeURIComponent(state.run.id)}/asset?record_id=${encodeURIComponent(recordId)}&path=${encodeURIComponent(view.path)}" alt="${escapeHtml(view.view)}">`)
      .join("");
    const cardMetrics = (card.metrics || [])
      .map((metric) => `<div class="modal-metric"><span>${escapeHtml(metric.label)}</span><b>${escapeHtml(metric.value)}</b></div>`)
      .join("");
    const classification = detail.classification || doc.meta?.classification || {};
    const classificationPanel = renderClassification(classification, doc);
    const recordPerformance = renderRecordPerformance(detail.performance || {});
    $("detailContent").innerHTML = `
      <div class="detail-grid">
        <div>
          <div class="eyebrow">${escapeHtml(detail.kind.toUpperCase())} · ${escapeHtml(detail.format)}</div>
          <h2>${escapeHtml(detail.title || detail.relative_path)}</h2>
          <p class="record-path">${escapeHtml(detail.relative_path)}</p>
          <div class="detail-images">${images || '<div class="empty">没有可显示的渲染图</div>'}</div>
        </div>
        <div>
          <h3>视觉摘要</h3>
          <p>${escapeHtml(visual.summary || detail.summary || "暂无")}</p>
          <div class="detail-block"><h3>关键指标</h3><div class="modal-metrics">${cardMetrics || '<span class="chip">暂无</span>'}</div></div>
          <div class="detail-block"><h3>性能</h3>${recordPerformance}</div>
          ${visual.error ? `<div class="detail-block"><h3>视觉 API 错误</h3><p class="failed">${escapeHtml(visual.error)}</p></div>` : ""}
        </div>
      </div>
      <div class="detail-block classification-detail-block">
        <div class="classification-heading">
          <div>
            <h3>分类数据</h3>
            <p>按分类表格展示，数值已按阅读习惯格式化；完整精度保存在后台和导出文件中。</p>
          </div>
        </div>
        <div class="classification-summary">${classificationPanel}</div>
      </div>`;
    const actions = document.createElement("div");
    actions.className = "modal-actions";
    const regenerate = document.createElement("button");
    regenerate.type = "button";
    regenerate.className = "secondary";
    regenerate.textContent = "重新生成此文件";
    regenerate.addEventListener("click", () => regenerateRecord(recordId));
    const exportButton = document.createElement("button");
    exportButton.type = "button";
    exportButton.className = "ghost";
    exportButton.textContent = "导出此文件报告";
    exportButton.addEventListener("click", () => downloadUrl(detail.export_url));
    actions.append(regenerate, exportButton);
    $("detailContent").prepend(actions);
    $("recordPerformanceBtn")?.addEventListener("click", openPerformanceDetails);
    $("detailModal").classList.remove("hidden");
  } catch (error) {
    toast(error.message, true);
  }
}

function renderRateText(rates) {
  return Object.entries(rates || {})
    .map(([key, value]) => `${key}: ${Number(value).toFixed(2)}`)
    .join(" · ");
}

function renderPerformanceDetails(data) {
  const summary = data.summary || {};
  const operations = data.operations || [];
  const slowest = data.slowest || [];
  const errors = data.errors || [];
  const operationRows = operations.map((item) => `
    <tr>
      <td>${escapeHtml(item.operation)}</td>
      <td>${escapeHtml(item.kind || "")}</td>
      <td>${Number(item.count || 0)}</td>
      <td>${escapeHtml(formatDuration(item.avg_ms))}</td>
      <td>${escapeHtml(formatDuration(item.p95_ms))}</td>
      <td>${escapeHtml(formatDuration(item.total_ms))}</td>
      <td>${escapeHtml(formatCount(item.tokens))}</td>
      <td>${escapeHtml(renderRateText(item.rates))}</td>
      <td class="${Number(item.errors || 0) ? "performance-error" : ""}">${Number(item.errors || 0)}</td>
    </tr>
  `).join("");
  const slowRows = slowest.slice(0, 12).map((item) => `
    <tr>
      <td>${escapeHtml(item.operation)}</td>
      <td>${escapeHtml(item.record_id || "-")}</td>
      <td>${escapeHtml(item.status || "")}</td>
      <td>${escapeHtml(formatDuration(item.wall_ms))}</td>
      <td>${escapeHtml(formatCount((item.token_usage || {}).total_tokens))}</td>
    </tr>
  `).join("");
  const errorRows = errors.length
    ? errors.slice(-30).map((item) => `
      <tr>
        <td>${escapeHtml(item.operation)}</td>
        <td>${escapeHtml(item.record_id || "-")}</td>
        <td class="performance-error">${escapeHtml(item.status)}</td>
        <td>${escapeHtml(item.error || "")}</td>
      </tr>
    `).join("")
    : '<tr><td colspan="4">没有错误、降级或警告</td></tr>';
  $("performanceContent").innerHTML = `
    <div class="eyebrow">PERFORMANCE DETAILS</div>
    <h2>运行性能详情</h2>
    <p class="record-path">trace ${escapeHtml(summary.trace_id || "尚未完成")}</p>
    <div class="performance-section">
      <div class="performance-cards">${performanceCardsMarkup(summary)}</div>
      <div class="performance-note">
        Prompt ${escapeHtml(formatCount(summary.prompt_tokens))} ·
        Completion ${escapeHtml(formatCount(summary.completion_tokens))} ·
        缓存 token ${escapeHtml(formatCount(summary.cached_prompt_tokens))} ·
        usage 缺失 ${Number(summary.usage_missing_calls || 0)} 次
      </div>
    </div>
    <div class="performance-section">
      <h3>操作耗时</h3>
      <div class="performance-table-wrap">
        <table class="performance-table">
          <thead><tr><th>操作</th><th>类型</th><th>次数</th><th>平均</th><th>P95</th><th>总计</th><th>Token</th><th>速率</th><th>错误</th></tr></thead>
          <tbody>${operationRows || '<tr><td colspan="9">暂无性能 span</td></tr>'}</tbody>
        </table>
      </div>
    </div>
    <div class="performance-section">
      <h3>最慢操作</h3>
      <div class="performance-table-wrap">
        <table class="performance-table">
          <thead><tr><th>操作</th><th>记录</th><th>状态</th><th>耗时</th><th>Token</th></tr></thead>
          <tbody>${slowRows || '<tr><td colspan="5">暂无数据</td></tr>'}</tbody>
        </table>
      </div>
    </div>
    <div class="performance-section">
      <h3>错误与降级</h3>
      <div class="performance-table-wrap">
        <table class="performance-table">
          <thead><tr><th>操作</th><th>记录</th><th>状态</th><th>原因</th></tr></thead>
          <tbody>${errorRows}</tbody>
        </table>
      </div>
    </div>
    <div class="modal-actions">
      <button id="downloadPerformanceBtn" class="ghost" type="button">导出性能 JSON</button>
    </div>`;
  $("downloadPerformanceBtn").addEventListener("click", () => {
    downloadUrl(`/api/runs/${encodeURIComponent(state.run.id)}/performance/export?format=json`);
  });
}

async function openPerformanceDetails() {
  if (!state.run) {
    toast("请先扫描或读取报告", true);
    return;
  }
  try {
    const data = await request(`/api/runs/${encodeURIComponent(state.run.id)}/performance/details`);
    renderPerformanceDetails(data);
    $("performanceModal").classList.remove("hidden");
  } catch (error) {
    toast(error.message, true);
  }
}

function downloadUrl(url) {
  if (!url) {
    toast("没有可导出的报告", true);
    return;
  }
  const link = document.createElement("a");
  link.href = url;
  link.download = "";
  document.body.appendChild(link);
  link.click();
  link.remove();
}

async function exportRunReport() {
  if (!state.run) {
    toast("请先扫描或读取报告", true);
    return;
  }
  downloadUrl(`/api/runs/${encodeURIComponent(state.run.id)}/export`);
}

async function regenerateRecord(recordId) {
  if (!state.run) return;
  try {
    await request(`/api/runs/${encodeURIComponent(state.run.id)}/records/${encodeURIComponent(recordId)}/regenerate`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...credentials() },
      body: JSON.stringify({
        with_vision: $("withVision").checked,
        ...configBody(),
      }),
    });
    toast("已开始重新生成该文件");
    await refreshRun();
    if (!state.timer) state.timer = setInterval(refreshRun, 1200);
  } catch (error) {
    toast(error.message, true);
  }
}

function closeDetail() {
  $("detailModal").classList.add("hidden");
}

function closePerformanceModal() {
  $("performanceModal").classList.add("hidden");
}

$("scanBtn").addEventListener("click", startScan);
$("pickWorkspaceBtn").addEventListener("click", pickWorkspace);
$("cancelBtn").addEventListener("click", cancelScan);
$("saveConfigBtn").addEventListener("click", saveConfig);
$("testConfigBtn").addEventListener("click", testConfig);
$("searchBtn").addEventListener("click", search);
$("exportBtn").addEventListener("click", exportRunReport);
$("query").addEventListener("keydown", (event) => {
  if (event.key === "Enter") search();
});
$("clearBtn").addEventListener("click", async () => {
  $("query").value = "";
  await refreshRun();
});
$("kindFilter").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-kind]");
  if (!button) return;
  state.kind = button.dataset.kind;
  document.querySelectorAll("#kindFilter button").forEach((item) => item.classList.toggle("active", item === button));
  if ($("query").value.trim()) await search();
  else if (state.run) await refreshRun();
});
$("rememberKey").addEventListener("change", saveBrowserKey);
$("apiKey").addEventListener("change", saveBrowserKey);
$("layerFilter").addEventListener("change", async () => {
  state.layer = $("layerFilter").value;
  if (state.category && state.layer && CATEGORY_LAYERS[state.category] !== state.layer) {
    state.category = "";
    $("categoryFilter").value = "";
  }
  await applyRecordFilters();
});
$("performanceDetailBtn").addEventListener("click", openPerformanceDetails);
$("closeModal").addEventListener("click", closeDetail);
$("closePerformanceModal").addEventListener("click", closePerformanceModal);
$("detailModal").addEventListener("click", (event) => {
  if (event.target.id === "detailModal") closeDetail();
});
$("performanceModal").addEventListener("click", (event) => {
  if (event.target.id === "performanceModal") closePerformanceModal();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeDetail();
    closePerformanceModal();
  }
});

populateCategoryFilter();
state.apiKeyMemory = localStorage.getItem("cax.apiKey") || "";
checkHealth(true);
setInterval(() => checkHealth(false), 3000);
window.addEventListener("focus", () => checkHealth(false));
loadConfig();
