const refreshMs = 5000;

const stateNames = {
  completed: "已完成",
  running: "运行中",
  stopped: "已停止",
  pending: "等待中",
};

const phaseNames = {
  starting: "正在启动",
  reading: "正在读取原始数据",
  concatenating: "正在合并数据块",
  deduplicating: "正在排序去重",
  downcasting: "正在压缩列类型",
  writing: "正在写出 dta 文件",
  finalizing: "正在保存最终文件",
  complete: "已完成",
  empty: "没有读取到数据",
};

function $(id) {
  return document.getElementById(id);
}

function formatNumber(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  return Number(value).toLocaleString("zh-CN");
}

function formatPercent(value) {
  const number = Number(value || 0);
  return `${number.toFixed(number >= 99.95 || number === 0 ? 0 : 2)}%`;
}

function setConnection(ok, text) {
  const el = $("connectionState");
  el.textContent = text;
  el.classList.toggle("ok", ok);
  el.classList.toggle("bad", !ok);
}

function renderOverview(data) {
  const current = data.current;
  $("currentYear").textContent = data.currentYear || "--";
  $("currentState").textContent = current ? stateNames[current.status] : "等待任务";
  $("yearPercent").textContent = current ? formatPercent(current.percent) : "0%";
  $("yearRows").textContent = current
    ? `${formatNumber(current.processedRows)} / ${formatNumber(current.totalRows)} 行`
    : "-- / -- 行";
  $("overallPercent").textContent = formatPercent(data.overallPercent);
  $("yearCount").textContent = `${data.completedYears} / ${data.totalYears} 年`;
  $("updatedAt").textContent = (data.updatedAt || "").replace("T", " ");
  $("yearBar").style.width = `${current ? current.percent : 0}%`;
  $("overallBar").style.width = `${data.overallPercent || 0}%`;

  if (current) {
    const phaseText = current.runtimePhase
      ? `阶段：${phaseNames[current.runtimePhase] || current.runtimePhase}`
      : "";
    const detail = current.status === "completed"
      ? `${current.year} 年已经完成，输出文件 ${(current.outputFile && current.outputFile.sizeGB) || "--"} GB。`
      : current.status === "running"
        ? `${current.year} 年正在处理，已读 ${formatNumber(current.processedRows)} 行，总计 ${formatNumber(current.totalRows)} 行。${phaseText ? " " + phaseText : ""}`
        : `${current.year} 年尚未开始；页面会在日志更新后自动切换。`;
    $("currentDetail").textContent = detail;
  } else {
    $("currentDetail").textContent = "2013-2024 年均已完成或尚未发现任务。";
  }

  $("logBadge").textContent = data.logExists ? "日志已连接" : "等待日志文件";
  $("processState").textContent = data.cleanerRunning
    ? `清洗进程：运行中，PID ${data.cleanerProcesses.map((p) => p.pid).join(", ")}`
    : "清洗进程：已停止";
  const staleText = data.logStaleSeconds === null || data.logStaleSeconds === undefined
    ? "日志心跳：暂无日志"
    : data.logStale
      ? `日志心跳：${data.logStaleSeconds} 秒未更新，可能卡住`
      : `日志心跳：${data.logStaleSeconds} 秒前更新`;
  $("staleState").textContent = staleText;
  $("staleState").classList.toggle("warn", Boolean(data.logStale));
  $("startButton").disabled = Boolean(data.cleanerRunning);
  $("stopButton").disabled = !data.cleanerRunning;
  $("workspacePath").textContent = data.workspace || "";
  $("logPath").textContent = data.logPath || "";
}

function renderYears(years) {
  const grid = $("yearGrid");
  grid.innerHTML = "";
  for (const item of years) {
    const tile = document.createElement("article");
    tile.className = `year-tile ${item.status}`;

    const tag = document.createElement("span");
    tag.className = `tag ${item.status}`;
    tag.textContent = stateNames[item.status] || item.status;

    const top = document.createElement("div");
    top.className = "year-top";
    top.innerHTML = `<strong>${item.year}</strong>`;
    top.appendChild(tag);

    const progress = document.createElement("div");
    progress.className = "tile-progress";
    progress.innerHTML = `<div style="width: ${item.percent || 0}%"></div>`;

    const meta = document.createElement("div");
    meta.className = "tile-meta";
    const elapsed = item.elapsedMin ? `${Number(item.elapsedMin).toFixed(1)} 分钟` : "--";
    const size = item.rawFile && item.rawFile.exists ? `${item.rawFile.sizeGB} GB` : "--";
    const output = item.outputFile && item.outputFile.exists ? `${item.outputFile.sizeGB} GB` : "未生成";
    const phase = item.runtimePhase ? (phaseNames[item.runtimePhase] || item.runtimePhase) : "--";
    meta.innerHTML = `
      <span>进度：${formatPercent(item.percent)}</span>
      <span>行数：${formatNumber(item.processedRows)} / ${formatNumber(item.totalRows)}</span>
      <span>原始文件：${size}</span>
      <span>输出文件：${output}</span>
      <span>阶段：${phase}</span>
      <span>耗时：${elapsed}</span>
    `;

    tile.append(top, progress, meta);
    grid.appendChild(tile);
  }
}

function renderLog(lines) {
  const log = $("recentLog");
  if (!lines || lines.length === 0) {
    log.textContent = "还没有检测到 Python 清洗日志。启动清洗后这里会自动显示最新记录。";
    return;
  }
  log.textContent = lines.slice(-50).join("\n");
  log.scrollTop = log.scrollHeight;
}

async function refresh() {
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    setConnection(true, "实时连接");
    renderOverview(data);
    renderYears(data.years || []);
    renderLog(data.recentLog || []);
  } catch (error) {
    setConnection(false, "连接断开");
    $("recentLog").textContent = `无法读取进度：${error.message}`;
  }
}

async function controlCleaner(action) {
  const startButton = $("startButton");
  const stopButton = $("stopButton");
  startButton.disabled = true;
  stopButton.disabled = true;
  try {
    const response = await fetch("/api/control", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    await refresh();
  } catch (error) {
    setConnection(false, `操作失败：${error.message}`);
  }
}

$("startButton").addEventListener("click", () => controlCleaner("start"));
$("stopButton").addEventListener("click", () => controlCleaner("stop"));

refresh();
setInterval(refresh, refreshMs);
