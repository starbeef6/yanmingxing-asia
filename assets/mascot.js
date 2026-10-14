(() => {
  "use strict";

  if (document.getElementById("xiong-daye-widget")) return;
  const assetBase = document.currentScript?.src || new URL("../assets/mascot.js", location.href).href;
  const pandaUrl = new URL("mascot/xiong-daye.png", assetBase).href;
  const numberFormat = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 });
  const percentFormat = new Intl.NumberFormat("zh-CN", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const messages = [];
  let transcriptStorage = null;
  try { transcriptStorage = window.sessionStorage; } catch (_) {}
  const transcript = window.OperationsChatSession?.create({ storage: transcriptStorage });
  let context = null;
  let conversationScope = null;
  let opened = false;
  let busy = false;
  let controller = null;
  let retryMessage = null;
  let retryScope = null;
  let failedBubble = null;
  let serviceState = "unknown";
  let serviceCheckedAt = 0;
  let statusPromise = null;

  const root = document.createElement("aside");
  root.id = "xiong-daye-widget";
  root.className = "xd-widget";
  root.innerHTML = `
    <button class="xd-trigger" type="button" aria-label="和熊大爷聊聊看板" aria-expanded="false" aria-controls="xd-chat-panel">
      <span class="xd-panda-wrap"><img class="xd-panda" alt="" draggable="false" width="116" height="116"></span>
      <span class="xd-trigger-hint" aria-hidden="true">聊聊看板</span>
    </button>
    <section id="xd-chat-panel" class="xd-panel" aria-label="熊大爷对话" hidden>
      <header class="xd-header">
        <img class="xd-avatar" alt="" width="46" height="46">
        <div class="xd-brand"><h2>熊大爷<span>现包饺子</span></h2><p class="xd-mode">看板问答</p></div>
        <button class="xd-history" type="button" aria-label="导出完整聊天记录" title="导出完整聊天记录">记录</button>
        <button class="xd-minimize xd-icon-button" type="button" aria-label="收起对话，让熊大爷回到屏幕边缘">
          <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path d="M6 12h12" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>
        </button>
      </header>
      <p class="xd-scope" aria-live="polite"></p>
      <div class="xd-conversation" role="log" aria-label="对话消息" aria-live="polite" aria-relevant="additions text" tabindex="0"></div>
      <div class="xd-suggestions" aria-label="常用问题">
        <button type="button" data-question="目前GMV是多少？">GMV</button>
        <button type="button" data-question="店日均达标了吗？">店日均</button>
        <button type="button" data-question="在营门店有多少？">在营门店</button>
        <button type="button" data-question="三项一起看">三项一起看</button>
      </div>
      <form class="xd-form">
        <label class="xd-sr-only" for="xd-message-input">输入你想问熊大爷的问题</label>
        <textarea id="xd-message-input" class="xd-input" rows="1" maxlength="2000" placeholder="问问熊大爷…" enterkeyhint="send"></textarea>
        <button class="xd-send xd-icon-button" type="submit" aria-label="发送消息" disabled>
          <svg viewBox="0 0 24 24" width="21" height="21" aria-hidden="true"><path d="M12 18V6m-5 5 5-5 5 5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </button>
      </form>
      <div class="xd-bottom"><span class="xd-status" role="status"></span><span class="xd-counter" aria-label="输入字数">0 / 2000</span></div>
    </section>`;
  document.body.append(root);

  const $ = (selector) => root.querySelector(selector);
  const trigger = $(".xd-trigger");
  const panel = $(".xd-panel");
  const input = $(".xd-input");
  const conversation = $(".xd-conversation");
  const sendButton = $(".xd-send");
  $(".xd-panda").src = pandaUrl;
  $(".xd-avatar").src = pandaUrl;

  function finite(value) {
    return typeof value === "number" && Number.isFinite(value) ? value : null;
  }

  function format(value, divisor = 1, suffix = "") {
    return finite(value) === null ? "尚未取得" : `${numberFormat.format(value / divisor)}${suffix}`;
  }

  function ratio(actual, target) {
    return finite(actual) !== null && finite(target) !== null && target > 0
      ? `${percentFormat.format(actual / target * 100)}%` : null;
  }

  function latestContext() {
    try {
      return window.OperationsContext?.get?.() || context;
    } catch (_) {
      return context;
    }
  }

  function configuredEndpoint() {
    const configured = window.XiongDayeChat?.endpoint;
    if (typeof configured !== "string" || !configured.trim()) return null;
    try {
      const url = new URL(configured, location.href);
      const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
      if (url.username || url.password || !(url.protocol === "https:" || (loopback && url.protocol === "http:"))) return false;
      return url.href;
    } catch (_) {
      return false;
    }
  }

  function statusEndpoint(endpoint) {
    try {
      const target = new URL(endpoint);
      const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(target.hostname);
      const candidate = window.XiongDayeChat?.statusEndpoint || new URL(loopback ? "/healthz" : "/api/status", target).href;
      const url = new URL(candidate, location.href);
      return url.origin === target.origin && !url.username && !url.password
        && ["/api/status", "/healthz"].includes(url.pathname) ? url.href : null;
    } catch (_) { return null; }
  }

  function renderServiceState() {
    const endpoint = configuredEndpoint();
    const labels = { unknown: "连接待检查", checking: "检查连接中", ready: "在线对话", offline: "服务未连接", unreachable: "连接暂不可用" };
    $(".xd-mode").textContent = endpoint === null ? "看板问答" : labels[serviceState];
    $(".xd-status").textContent = busy
      ? serviceState === "checking" ? "正在连接…" : "正在回复…"
      : serviceState === "offline" || serviceState === "unreachable"
        ? retryMessage ? "连接恢复后可重试，原提问已保留" : "当前未连上对话服务" : "";
  }

  async function checkService(force = false) {
    const endpoint = configuredEndpoint();
    if (endpoint === null) return true;
    const url = endpoint && statusEndpoint(endpoint);
    if (!url || window.navigator?.onLine === false) {
      serviceState = "offline";
      renderServiceState();
      return false;
    }
    if (statusPromise) return statusPromise;
    if (!force && Date.now() - serviceCheckedAt < 15000) return serviceState === "ready";
    serviceState = "checking";
    renderServiceState();
    const statusController = new AbortController();
    const timeout = setTimeout(() => statusController.abort(), 8000);
    statusPromise = (async () => {
      try {
        const response = await fetch(url, { method: "GET", credentials: "omit", cache: "no-store", redirect: "error",
          referrerPolicy: "no-referrer", headers: { Accept: "application/json" }, signal: statusController.signal });
        if (!response.ok || !/application\/json/i.test(response.headers.get("content-type") || "")) throw new Error("status unavailable");
        const raw = await response.text();
        if (raw.length > 16384) throw new Error("status unavailable");
        const status = JSON.parse(raw);
        serviceState = status.ready === true ? "ready" : "offline";
      } catch (_) {
        serviceState = "unreachable";
      } finally {
        clearTimeout(timeout);
        serviceCheckedAt = Date.now();
        statusPromise = null;
        renderServiceState();
      }
      return serviceState === "ready";
    })();
    return statusPromise;
  }

  function refreshScope() {
    context = latestContext();
    const parts = [context?.month, context?.region, context?.tier ? `${context.tier}目标` : null].filter(Boolean);
    if (context?.end) parts.push(`截至 ${context.end.slice(5).replace("-", "/")}`);
    $(".xd-scope").textContent = parts.length ? parts.join(" · ") : "跟随当前看板";
    renderServiceState();
  }

  function appendMessage(role, content, options = {}) {
    if (options.record !== false) transcript?.append({ role, content, scope: options.scope || latestContext(), kind: options.kind || (options.error ? "error" : "conversation") });
    const item = document.createElement("div");
    item.className = `xd-message xd-message-${role}`;
    const speaker = document.createElement("span");
    speaker.className = "xd-sr-only";
    speaker.textContent = role === "user" ? "你：" : "熊大爷：";
    const text = document.createElement("p");
    text.textContent = content;
    if (options.restored && options.scope) {
      const label = document.createElement("span");
      label.className = "xd-message-scope";
      label.textContent = [options.scope.month, options.scope.region, options.scope.tier].filter(Boolean).join(" · ");
      item.append(label);
    }
    item.append(speaker, text);
    if (options.error) {
      item.classList.add("xd-message-error");
      const retry = document.createElement("button");
      retry.type = "button";
      retry.className = "xd-retry";
      retry.textContent = "重试";
      retry.addEventListener("click", () => {
        if (!busy && retryMessage) send(retryMessage, true);
      });
      item.append(retry);
    }
    conversation.append(item);
    conversation.scrollTop = conversation.scrollHeight;
    return item;
  }

  function showTranscript() {
    if (!transcript) {
      appendMessage("assistant", "聊天记录组件尚未加载，请重新打开此页面。", { kind: "export" });
      return;
    }
    const count = transcript.entries().length;
    const text = transcript.exportText();
    const notice = `已整理本标签页保存的 ${count} 条完整对话原文。不同区域的对话也会保留。${transcript.persisted() ? "刷新后仍会保留。" : "浏览器未允许保存，刷新前请先下载。"}`;
    const bubble = appendMessage("assistant", notice, { kind: "export" });
    const download = document.createElement("a");
    download.className = "xd-download-chat";
    download.textContent = "下载完整聊天记录";
    download.href = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
    download.download = `熊大爷聊天记录_${new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 10)}.txt`;
    bubble.append(download);
    conversation.scrollTop = conversation.scrollHeight;
  }

  function setOpen(value) {
    opened = value;
    root.classList.toggle("xd-open", value);
    trigger.setAttribute("aria-expanded", String(value));
    trigger.setAttribute("aria-label", value ? "收起熊大爷对话" : "和熊大爷聊聊看板");
    panel.hidden = !value;
    if (value) {
      refreshScope();
      checkService(true);
      // Opening the companion must not unexpectedly summon Safari's keyboard
      // or auto-zoom. Touch users focus the visible composer explicitly.
      if (window.matchMedia?.("(pointer: fine)").matches) requestAnimationFrame(() => input.focus({ preventScroll: true }));
    } else {
      trigger.focus({ preventScroll: true });
    }
  }

  function selectionFor(question, current) {
    const aliases = [
      ["四川", "四川地区"], ["江苏", "江苏地区"], ["北区", "北区"],
      ["西区", "西区"], ["南区", "南区"], ["全国", "全国"],
    ];
    const rows = Array.isArray(current.regions) ? current.regions : [];
    const mentioned = aliases.filter(([alias]) => question.includes(alias)).map(([, region]) => region);
    const allRegions = /大区|五区|五个(?:区|区域)|各区|各个区域|所有区域|全部区域|每个区域|最下面|下方|底部/.test(question);
    if (allRegions) {
      return rows.filter((row) => row.region !== "全国" || mentioned.includes("全国"));
    }
    if (mentioned.length) return rows.filter((row) => mentioned.includes(row.region));
    return current.selected ? [{ ...current.selected, target: current.target || current.selected.target }] : [];
  }

  function wantsJointDiagnosis(question) {
    return /为什么|原因|怎么会|怎么回事|只高|高但|三项|三個|三个指标|一起看|联合|关系|健康|经营怎么样|经营如何|整体经营/.test(question);
  }

  function metricLines(row, metrics, targetsOnly = false) {
    const target = row.target || {};
    const lines = [];
    if (metrics.gmv) {
      const actual = format(row.gmv, 10000, " 万元");
      const goal = format(target.gmv, 10000, " 万元");
      const attainment = ratio(row.gmv, target.gmv);
      lines.push(targetsOnly ? `GMV目标 ${goal}` : `GMV ${actual}${finite(target.gmv) !== null ? `；目标 ${goal}` : ""}${attainment ? `；占全月目标 ${attainment}` : ""}`);
    }
    if (metrics.average) {
      const actual = format(row.storeAverage, 1, " 元");
      const goal = format(target.storeAverage, 1, " 元");
      const attainment = ratio(row.storeAverage, target.storeAverage);
      lines.push(targetsOnly ? `店日均目标 ${goal}` : `店日均 ${actual}${finite(target.storeAverage) !== null ? `；目标 ${goal}` : ""}${attainment ? `；达成 ${attainment}` : ""}`);
    }
    if (metrics.stores) {
      const actual = format(row.operatingStores, 1, " 家");
      const goal = finite(target.operatingStores) === null ? "尚未取得" : `${Math.round(target.operatingStores)} 家`;
      const date = row.operatingStoresDate ? `（截至 ${row.operatingStoresDate}）` : "";
      lines.push(targetsOnly ? `在营门店目标 ${goal}` : `有营业额在营门店 ${actual}${date}${finite(target.operatingStores) !== null ? `；目标 ${goal}` : ""}`);
    }
    return `${row.region}\n${lines.join("\n")}`;
  }

  function localAnswer(question, current) {
    if (/^(?:你好|您好|嗨|哈喽|hello|hi|早上好|晚上好|熊大爷)[\s，,！!。.]*(?:熊大爷)?[？！!。.\s]*$/i.test(question) || /你叫什么|你的名字|你是谁/.test(question)) {
      return "你好，我是熊大爷。你可以问我看板里的 GMV、店日均、在营门店和目标；自由聊天还未开通。";
    }
    if (!current || !current.month) return "看板数据还在准备。等页面显示数据后，我就能帮你查看 GMV、店日均、门店数量和目标。";
    const chineseMonths = { 一: 1, 二: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9, 十: 10, 十一: 11, 十二: 12 };
    const datedMonth = question.match(/\b(20\d{2})[-/](0?[1-9]|1[0-2])(?:[-/]\d{1,2})?(?!\d)/);
    const numericMonth = question.match(/(?:(20\d{2})年\s*)?(1[0-2]|0?[1-9])\s*月/);
    const chineseMonth = question.match(/(?:(20\d{2})年\s*)?(十二|十一|十|[一二三四五六七八九])\s*月/);
    const yearMention = question.match(/\b(20\d{2})\s*年?/);
    const monthMatch = datedMonth || numericMonth || chineseMonth;
    const requestedYear = Number(monthMatch?.[1] || yearMention?.[1] || current.month.slice(0, 4));
    const requestedMonth = monthMatch ? Number(chineseMonth === monthMatch ? chineseMonths[monthMatch[2]] : monthMatch[2]) : Number(current.month.slice(5));
    if (requestedYear !== Number(current.month.slice(0, 4)) || requestedMonth !== Number(current.month.slice(5))) {
      const period = `${requestedYear}-${String(requestedMonth).padStart(2, "0")}`;
      return `现在看板显示 ${current.month}，尚未显示 ${period} 的数据。请先切换到对应期间，我会跟着页面的数据回答。`;
    }
    const requestedTier = ["保底", "平衡", "冲刺"].find((tier) => question.includes(tier));
    if (requestedTier && requestedTier !== current.tier) return `现在使用${current.tier}目标。请先在看板切换到${requestedTier}，我就能按同一档位给你核对。`;
    if (wantsJointDiagnosis(question)) {
      const rows = selectionFor(question, current);
      if (!rows.length) return "还没有可用的区域数据，先等看板刷新完成再做三项联合判断。";
      if (!window.OperationsDiagnosis) return "三项联合诊断规则尚未加载，暂时不能下判断。";
      const lines = rows.map((row) => {
        const snapshot = { ...current, region: row.region, selected: row, target: row.target, forecast: row.forecast };
        const result = window.OperationsDiagnosis.diagnose(snapshot, row);
        return `${row.region}：${result.summary}\n本周核查：${result.action}\n验证条件：${result.falsifier}`;
      });
      const scope = `${current.month} · ${current.tier}目标`;
      const freshness = current.loading ? "\n页面正在刷新，按当前快照待核对。" : current.failure ? "\n刷新未成功，按当前快照待核对。" : "";
      return `${scope}${freshness}\n${lines.join("\n\n")}`;
    }
    if (/同比|环比|上个月|去年营业额/.test(question)) return "当前看板没有提供同口径的对比期实际值，我不能据此计算同比或环比。";
    if (/店日均/.test(question) && /怎么算|怎么计算|公式|口径/.test(question)) {
      return "店日均＝同一期间的 GMV ÷ 有正营业额的营业店天。每家门店每个营业日计一个店天，全国和大区都按营业额与店天合计计算。";
    }
    const rows = selectionFor(question, current);
    const scope = `${current.month} · ${current.tier}目标${current.start && current.end ? `\n实际期间 ${current.start}—${current.end}` : ""}`;
    const freshness = current.loading ? "\n页面正在刷新，以下是当前显示的结果。" : current.failure ? "\n本次刷新未成功，以下是页面保留的结果。" : "";
    if (/预计|预测|预估|月底|全月能|历史进度/.test(question)) {
      if (rows.length !== 1 || rows[0].region !== current.region) return "请先在看板选择要查看的区域，我会读取该区域当前的月底预估。";
      const forecast = current.forecast;
      if (!forecast?.available || finite(forecast.gmv) === null) return `${scope}\n${current.region}的参考预估尚未取得，暂时不能给出月底营业额。`;
      const gap = finite(forecast.gap);
      const gapText = gap === null ? "" : Math.abs(gap) < 0.005 ? "；与目标持平" : `；预计${gap > 0 ? "缺口" : "超额"} ${format(Math.abs(gap), 10000, " 万元")}`;
      const attainment = finite(forecast.attainment) === null ? "" : `；预计达成 ${percentFormat.format(forecast.attainment * 100)}%`;
      const basis = forecast.mode === "fixed-calendar-ridge"
        ? "\n按固定参数 Ridge 模型结合本月实际校准估算，仅作参考，不代表全月实际。"
        : forecast.mode === "historical-shape-reference"
          ? forecast.historicalDate ? `\n按 ${forecast.historicalDate} 的历史累计进度估算，仅作参考。` : "\n按页面历史累计进度估算，仅作参考。"
          : "\n按页面提供的参考预估，仅作参考，不代表全月实际。";
      return `${scope}${freshness}\n${current.region}参考预计全月 GMV ${format(forecast.gmv, 10000, " 万元")}${attainment}${gapText}${basis}`;
    }
    const metrics = {
      gmv: /gmv|营收|营业额|收入/i.test(question) && !/店日均营业额/.test(question),
      average: /店日均|店铺日均|日均/.test(question),
      stores: /门店|店数|在营|几家|多少家/.test(question),
    };
    if (!Object.values(metrics).some(Boolean) && /情况|表现|对照|概况|目标|达标|差距|完成|五区|各区/.test(question)) {
      metrics.gmv = metrics.average = metrics.stores = true;
    }
    if (!Object.values(metrics).some(Boolean)) return "我现在能帮你查看看板数字，自由聊天还未开通。你可以问我 GMV、店日均、在营门店或月底预估。";
    if (!rows.length) return "这个区域的数据尚未取得。请先在看板选择对应区域，再问我。";
    const targetsOnly = /目标/.test(question) && !/实际|达成|差距|比较|达标|完成/.test(question);
    const incomplete = current.sourceComplete === false ? "\n来源口径尚待核对。" : "";
    return `${scope}${freshness}\n\n${rows.map((row) => metricLines(row, metrics, targetsOnly)).join("\n\n")}${incomplete}`;
  }

  async function remoteAnswer(question, current, endpoint) {
    if (endpoint === false) throw new Error("对话服务地址未配置正确，请稍后再试。");
    if (!await checkService(serviceState !== "ready")) {
      throw new Error(serviceState === "offline" ? "对话服务暂未连接，原提问已保留；连接恢复后点重试。" : "当前无法连上对话服务，原提问已保留；请检查网络后重试。");
    }
    controller = new AbortController();
    const timeoutMs = window.XiongDayeChat?.timeoutMs === 75000 ? 75000 : 45000;
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(endpoint, {
        method: "POST", credentials: "omit", cache: "no-store", redirect: "error",
        referrerPolicy: "no-referrer", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: question, messages: messages.slice(0, -1).slice(-4), context: compactContext(question, current) }),
        signal: controller.signal,
      });
      if (!response.ok) {
        if (response.status === 503) { serviceState = "offline"; serviceCheckedAt = 0; }
        const errors = {
          429: "熊大爷正在处理其他对话，请稍后重试。",
          503: "聊天服务暂未连接，请稍后重试。",
          504: "等待回复超时，请重试。",
        };
        throw new Error(errors[response.status] || "对话服务暂时不可用，可以稍后重试。");
      }
      if (!/application\/json/i.test(response.headers.get("content-type") || "")) throw new Error("暂时没有收到有效回复，请重试。");
      const responseText = await response.text();
      if (responseText.length > 64000) throw new Error("回复过长，请换一个简短问题。");
      const payload = JSON.parse(responseText);
      const reply = typeof payload.reply === "string" ? payload.reply : payload.message;
      if (typeof reply !== "string" || !reply.trim() || reply.length > 8000) throw new Error("暂时没有收到有效回复，请重试。");
      serviceState = "ready";
      serviceCheckedAt = Date.now();
      return reply.trim();
    } catch (error) {
      if (error.name === "AbortError") throw new Error(`回复等待超过 ${timeoutMs / 1000} 秒，请重试。`);
      if (error instanceof TypeError || error instanceof SyntaxError) {
        serviceState = "unreachable";
        serviceCheckedAt = 0;
        throw new Error("暂时连接不上对话服务，原提问已保留；请重试。");
      }
      throw error;
    } finally {
      clearTimeout(timeout);
      controller = null;
    }
  }

  function compactContext(_question, current) {
    return window.OperationsChatContext?.compact(current, { diagnosisApi: window.OperationsDiagnosis }) || current || {};
  }

  function updateInput() {
    $(".xd-counter").textContent = `${input.value.length} / 2000`;
    sendButton.disabled = busy || !input.value.trim();
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 90)}px`;
  }

  function setBusy(value) {
    busy = value;
    root.classList.toggle("xd-busy", value);
    $(".xd-form").setAttribute("aria-busy", String(value));
    root.querySelectorAll(".xd-suggestions button, .xd-retry").forEach((button) => { button.disabled = value; });
    renderServiceState();
    updateInput();
  }

  async function send(question, retry = false) {
    const message = typeof question === "string" ? question.trim().slice(0, 2000) : "";
    if (!message || busy) return;
    if (!opened) setOpen(true);
    refreshScope();
    const snapshot = latestContext();
    const scopeKey = `${snapshot?.month || ""}|${snapshot?.region || ""}|${snapshot?.tier || ""}`;
    if (window.OperationsChatSession?.wantsTranscript(message)) {
      appendMessage("user", message, { kind: "export", scope: snapshot });
      input.value = "";
      updateInput();
      showTranscript();
      return;
    }
    if (retry && retryScope && scopeKey !== retryScope) {
      messages.splice(0, messages.length);
      conversationScope = scopeKey;
      failedBubble?.querySelector(".xd-retry")?.remove();
      failedBubble = null;
      retryMessage = null;
      retryScope = null;
      appendMessage("assistant", "看板范围已改变，请按当前范围重新提问。");
      return;
    }
    if (conversationScope && scopeKey !== conversationScope) messages.splice(0, messages.length);
    if (!messages.length) messages.push(...(transcript?.recent(snapshot) || []));
    conversationScope = scopeKey;
    const retryingBubble = retry ? failedBubble : null;
    if (failedBubble && !retry) failedBubble.querySelector(".xd-retry")?.remove();
    failedBubble = retryingBubble;
    retryMessage = null;
    retryScope = null;
    if (!retry) {
      appendMessage("user", message, { scope: snapshot });
      messages.push({ role: "user", content: message });
      input.value = "";
    }
    const endpoint = configuredEndpoint();
    setBusy(true);
    try {
      const answer = endpoint !== null ? await remoteAnswer(message, snapshot, endpoint) : localAnswer(message, snapshot);
      const latest = latestContext();
      const latestScope = `${latest?.month || ""}|${latest?.region || ""}|${latest?.tier || ""}`;
      if (latestScope !== scopeKey) {
        messages.splice(0, messages.length);
        retryMessage = null;
        retryScope = null;
        appendMessage("assistant", "看板范围已改变，请按当前范围重新提问。");
        return;
      }
      appendMessage("assistant", answer, { scope: snapshot });
      retryingBubble?.remove();
      failedBubble = null;
      messages.push({ role: "assistant", content: answer });
      if (messages.length > 40) messages.splice(0, messages.length - 40);
    } catch (error) {
      const latest = latestContext();
      if (`${latest?.month || ""}|${latest?.region || ""}|${latest?.tier || ""}` !== scopeKey) return;
      retryMessage = message;
      retryScope = scopeKey;
      const notice = error.message || "暂时没有收到回复，请重试。";
      if (retryingBubble) {
        retryingBubble.querySelector("p").textContent = notice;
        transcript?.append({ role: "assistant", content: notice, scope: snapshot, kind: "error" });
        failedBubble = retryingBubble;
      } else failedBubble = appendMessage("assistant", notice, { error: true, scope: snapshot });
    } finally {
      setBusy(false);
      if (opened && window.matchMedia?.("(pointer: fine)").matches) input.focus({ preventScroll: true });
    }
  }

  trigger.addEventListener("click", () => setOpen(!opened));
  $(".xd-minimize").addEventListener("click", () => setOpen(false));
  $(".xd-history").addEventListener("click", () => { if (!busy) showTranscript(); });
  panel.addEventListener("scroll", () => {
    // Older browsers may scroll an overflow:hidden ancestor when focusing a
    // child. Keep the frame fixed; only the conversation is scrollable.
    if (panel.scrollTop || panel.scrollLeft) { panel.scrollTop = 0; panel.scrollLeft = 0; }
  }, { passive: true });
  root.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && opened) { event.preventDefault(); setOpen(false); }
  });
  input.addEventListener("input", updateInput);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault(); send(input.value);
    }
  });
  $(".xd-form").addEventListener("submit", (event) => { event.preventDefault(); send(input.value); });
  $(".xd-suggestions").addEventListener("click", (event) => {
    const questionButton = event.target.closest("button[data-question]");
    if (questionButton) send(questionButton.dataset.question);
  });
  window.addEventListener("operations:context", (event) => {
    context = event.detail;
    const nextScope = `${context?.month || ""}|${context?.region || ""}|${context?.tier || ""}`;
    if (conversationScope && nextScope !== conversationScope) {
      messages.splice(0, messages.length);
      conversationScope = nextScope;
      retryMessage = null;
      retryScope = null;
      failedBubble?.querySelector(".xd-retry")?.remove();
    }
    refreshScope();
  });

  function fitViewport() {
    const wasAtBottom = conversation.scrollHeight - conversation.scrollTop - conversation.clientHeight < 48;
    const viewport = window.visualViewport;
    const height = viewport?.height || window.innerHeight;
    const width = viewport?.width || window.innerWidth;
    root.classList.toggle("xd-compact", height < 460);
    root.style.setProperty("--xd-viewport-height", `${height}px`);
    root.style.setProperty("--xd-viewport-width", `${width}px`);
    root.style.setProperty("--xd-viewport-left", `${Math.max(0, viewport?.offsetLeft || 0)}px`);
    root.style.setProperty("--xd-viewport-top", `${Math.max(0, viewport?.offsetTop || 0)}px`);
    if (opened && wasAtBottom) requestAnimationFrame(() => { conversation.scrollTop = conversation.scrollHeight; });
  }
  window.addEventListener("resize", fitViewport, { passive: true });
  window.visualViewport?.addEventListener("resize", fitViewport, { passive: true });
  window.visualViewport?.addEventListener("scroll", fitViewport, { passive: true });
  window.addEventListener("online", () => { if (opened) checkService(true); });
  window.addEventListener("offline", () => { serviceState = "offline"; serviceCheckedAt = 0; renderServiceState(); });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") { fitViewport(); if (opened) checkService(true); }
  });
  fitViewport();
  refreshScope();
  const savedMessages = transcript?.entries() || [];
  if (savedMessages.length) {
    savedMessages.forEach((entry) => appendMessage(entry.role, entry.content, { record: false, scope: entry.scope, restored: true }));
  } else appendMessage("assistant", "我是熊大爷，可以聊经营，也可以随便聊聊。", { kind: "welcome" });
})();
