(() => {
  "use strict";

  const MONTHS = ["2026-09", "2026-10", "2026-11", "2026-12"];
  const REGIONS = ["全国", "北区", "西区", "南区", "四川地区", "江苏地区"];
  const TIERS = ["保底", "平衡", "冲刺"];
  const $ = (id) => document.getElementById(id);
  const numberFormat = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2, minimumFractionDigits: 2 });
  const percentFormat = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 1, minimumFractionDigits: 1 });
  const params = new URLSearchParams(window.location.search);
  const state = {
    month: MONTHS.includes(params.get("month")) ? params.get("month") : "2026-10",
    region: REGIONS.includes(params.get("region")) ? params.get("region") : "全国",
    tier: TIERS.includes(params.get("tier")) ? params.get("tier") : "平衡",
    data: null,
    fallbackTargets: {},
    loading: false,
    requestId: 0,
    controller: null,
    failure: null,
  };

  function finite(value) {
    if (typeof value !== "number" && typeof value !== "string") return null;
    if (typeof value === "string" && !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(value.trim())) return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function number(value, divisor = 1, suffix = "") {
    const parsed = finite(value);
    return parsed === null ? "—" : numberFormat.format(parsed / divisor) + suffix;
  }

  function ratio(actual, target) {
    const a = finite(actual);
    const t = finite(target);
    return a === null || t === null || t <= 0 ? null : a / t;
  }

  function percent(value) {
    return value === null ? "—" : percentFormat.format(value * 100) + "%";
  }

  function snapshotUrl(month, requestId) {
    const url = new URL(`../assets/operations-data/${month}.json`, window.location.href);
    url.searchParams.set("refresh", `${Date.now()}-${requestId}`);
    return url.href;
  }

  function saveSelection() {
    const url = new URL(window.location.href);
    url.searchParams.set("month", state.month);
    url.searchParams.set("region", state.region);
    url.searchParams.set("tier", state.tier);
    window.history.replaceState(null, "", url);
  }

  function setStatus(kind, label, message) {
    $("status-badge").dataset.state = kind;
    $("status-text").textContent = label;
    $("notice").dataset.state = kind;
    $("notice").textContent = message;
  }

  function timestamp(value) {
    if (!value) return "取数时间未提供";
    const parsed = new Date(value);
    if (!Number.isFinite(parsed.getTime())) return "取数时间未提供";
    const parts = new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
    }).formatToParts(parsed);
    const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
    return `${values.month}/${values.day} ${values.hour}:${values.minute}:${values.second}（UTC+8）`;
  }

  function period(data) {
    if (!data || data.month !== state.month) return `${Number(state.month.slice(-2))} 月`;
    const date = /^\d{4}-\d{2}-\d{2}$/;
    if (!date.test(data.start) || !date.test(data.end) || !data.start.startsWith(state.month) || !data.end.startsWith(state.month)) {
      return `${Number(state.month.slice(-2))} 月`;
    }
    return `${data.start.slice(5).replace("-", "/")}—${data.end.slice(5).replace("-", "/")} 累计`;
  }

  function validateData(payload, requestedMonth) {
    if (!payload || typeof payload !== "object" || payload.month !== requestedMonth || !Array.isArray(payload.rows)) {
      throw new Error("返回的数据月份或格式有误，请重新刷新。");
    }
    if (typeof payload.sourceComplete !== "boolean" || !payload.source || typeof payload.source !== "object" || payload.source.mode !== "snapshot" ||
        payload.source.queryStrategy !== "full-period-requery" || /dws_data_merge/i.test(JSON.stringify(payload.source))) {
      throw new Error("本次数据不是已配置的完整历史重查快照，保留上次成功结果。");
    }
    const validDate = (value) => typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value) &&
      Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
    if (payload.start !== `${requestedMonth}-01` || (payload.end !== null &&
        (!validDate(payload.end) || !payload.end.startsWith(requestedMonth) || payload.end < payload.start))) {
      throw new Error("快照的实际日期范围有误，请重新刷新。");
    }
    if (payload.end !== null && (typeof payload.fetchedAt !== "string" ||
        !/(?:Z|[+-]\d{2}:\d{2})$/.test(payload.fetchedAt) || !Number.isFinite(Date.parse(payload.fetchedAt)))) {
      throw new Error("快照未提供有效的取数时间，请重新刷新。");
    }
    const seen = new Set();
    const rows = payload.rows.map((row) => {
      if (!row || !REGIONS.includes(row.region) || seen.has(row.region)) throw new Error("返回的区域数据有误，请重新刷新。");
      seen.add(row.region);
      const gmv = finite(row.gmv);
      const storeDays = finite(row.storeDays);
      const storeAverage = finite(row.storeAverage);
      if (row.status === "NO_DATA") {
        if (gmv !== null || storeDays !== null || storeAverage !== null) throw new Error("暂无数据的区域返回了不一致的指标，请重新刷新。");
        return { region: row.region, gmv: null, storeDays: null, storeAverage: null, status: "NO_DATA" };
      }
      if (row.status !== "OK" || gmv === null || storeDays === null || storeDays < 0 || !Number.isSafeInteger(storeDays) ||
          (storeDays > 0 && storeAverage === null) || (storeDays === 0 && storeAverage !== null)) {
        throw new Error("经营指标缺失或格式有误，请重新刷新。");
      }
      const calculatedAverage = storeDays > 0 ? gmv / storeDays : null;
      if (calculatedAverage !== null && Math.abs(calculatedAverage - storeAverage) > Math.max(1, Math.abs(calculatedAverage)) * 1e-9) {
        throw new Error("店日均与营业额、营业店天不一致，保留上次成功结果。");
      }
      return { region: row.region, gmv, storeDays, storeAverage: calculatedAverage, status: "OK" };
    });
    if (rows.length !== REGIONS.length || REGIONS.some((region) => !seen.has(region))) {
      throw new Error("全国及五区快照不完整，请重新刷新。");
    }
    if (payload.end === null && (payload.fetchedAt !== null || rows.some((row) => row.status !== "NO_DATA"))) {
      throw new Error("尚无实际期间的快照返回了营业数据，请重新刷新。");
    }
    return { ...payload, rows, targets: payload.targets && typeof payload.targets === "object" ? payload.targets : {} };
  }

  function targetFor(region) {
    const live = state.data?.month === state.month ? state.data.targets?.[state.tier]?.[region] : null;
    return validatedTarget(live) || state.fallbackTargets[state.month]?.[state.tier]?.[region] || {};
  }

  function validatedTarget(value) {
    if (!value || typeof value !== "object") return null;
    const gmv = finite(value.gmv);
    const storeAverage = finite(value.storeAverage);
    return gmv !== null && gmv > 0 && storeAverage !== null && storeAverage > 0 ? { gmv, storeAverage } : null;
  }

  function validateFallbackTargets(payload) {
    if (!payload || typeof payload !== "object" || !payload.months || typeof payload.months !== "object") throw new Error("目标数据格式有误。");
    const months = {};
    MONTHS.forEach((month) => {
      months[month] = {};
      TIERS.forEach((tier) => {
        months[month][tier] = {};
        REGIONS.forEach((region) => {
          const target = validatedTarget(payload.months[month]?.[tier]?.[region]);
          if (!target) throw new Error("目标月份、档位或区域不完整。");
          months[month][tier][region] = target;
        });
      });
    });
    return months;
  }

  async function loadFallbackTargets() {
    try {
      const response = await fetch("../assets/operations-targets.json", { headers: { Accept: "application/json" } });
      if (!response.ok) return;
      state.fallbackTargets = validateFallbackTargets(await response.json());
      render();
    } catch (_) {
      // Actual values and query status remain independent of static target availability.
    }
  }

  function progress(id, value) {
    $(id).style.width = value === null ? "0%" : `${Math.max(0, Math.min(100, value * 100))}%`;
  }

  function render() {
    const data = state.data?.month === state.month ? state.data : null;
    const rows = data?.rows || [];
    const selected = rows.find((row) => row.region === state.region);
    const target = targetFor(state.region);
    $("month").value = state.month;
    $("region").value = state.region;
    document.querySelectorAll('input[name="tier"]').forEach((input) => { input.checked = input.value === state.tier; });
    $("scope").textContent = `${state.region} · ${period(data)} · ${state.tier}目标`;
    $("comparison-period").textContent = `${Number(state.month.slice(-2))} 月 · ${state.tier}目标`;
    $("timestamp").textContent = data?.fetchedAt ? `取数：${timestamp(data.fetchedAt)}` : "尚未取数";
    $("gmv-value").textContent = number(selected?.gmv, 10000);
    $("average-value").textContent = number(selected?.storeAverage);
    $("gmv-target").textContent = number(target.gmv, 10000, " 万元");
    $("average-target").textContent = number(target.storeAverage, 1, " 元");
    const gmvRatio = ratio(selected?.gmv, target.gmv);
    const averageRatio = ratio(selected?.storeAverage, target.storeAverage);
    $("gmv-rate").textContent = percent(gmvRatio);
    $("average-rate").textContent = percent(averageRatio);
    progress("gmv-progress", gmvRatio);
    progress("average-progress", averageRatio);

    const ordered = REGIONS.slice(1).map((region) => rows.find((row) => row.region === region) || { region, gmv: null, storeAverage: null });
    if (rows.length) ordered.sort((a, b) => (b.gmv ?? -Infinity) - (a.gmv ?? -Infinity));
    const fragment = document.createDocumentFragment();
    ordered.forEach((row) => {
      const regionTarget = targetFor(row.region);
      const tr = document.createElement("tr");
      tr.dataset.selected = String(row.region === state.region);
      const heading = document.createElement("th");
      heading.scope = "row";
      heading.textContent = row.region;
      tr.appendChild(heading);
      [number(row.gmv, 10000), percent(ratio(row.gmv, regionTarget.gmv)), number(row.storeAverage), percent(ratio(row.storeAverage, regionTarget.storeAverage))].forEach((value) => {
        const td = document.createElement("td");
        td.textContent = value;
        tr.appendChild(td);
      });
      fragment.appendChild(tr);
    });
    $("region-rows").replaceChildren(fragment);
    $("dashboard").setAttribute("aria-busy", String(state.loading));
    $("refresh").disabled = state.loading;
    $("refresh").setAttribute("aria-busy", String(state.loading));
    $("refresh-label").textContent = state.loading ? "正在刷新" : "刷新看板";
  }

  function successStatus(data) {
    const selected = data.rows.find((row) => row.region === state.region);
    const incomplete = data.sourceComplete === false || data.sourceCompleteFalse === true || data.source?.complete === false;
    const target = targetFor(state.region);
    if (data.end === null) {
      setStatus("waiting", "仅显示目标", "本月尚无已完成营业日，实际值暂不显示。");
    } else if (!data.rows.length || !selected || selected.status === "NO_DATA") {
      setStatus("waiting", "本月暂无数据", "所选区域暂无实际营业数据，当前仅显示目标。");
    } else if (incomplete) {
      const warning = typeof data.dataQualityWarning === "string" && data.dataQualityWarning.trim() ? data.dataQualityWarning : "当前来源的历史补录覆盖尚未核验。";
      setStatus("waiting", "最近同步 · 待核验", warning);
    } else if (selected.storeDays === 0) {
      setStatus("ready", "最近同步 · 本月数据", "所选区域暂无正营业额记录，店日均暂不计算。");
    } else if (finite(target.gmv) === null || finite(target.storeAverage) === null) {
      setStatus("ready", "最近同步 · 本月数据", "实际数据已更新，本月所选档位的目标尚未配置。");
    } else {
      setStatus("ready", "最近同步 · 本月数据", "已读取最近成功同步的结果；历史补录随下次同步更新。");
    }
    const source = typeof data.source === "string" ? data.source : data.source?.description || data.source?.label || "";
    $("source-note").textContent = `${source || "GMV 为总营业额；店日均按营业店天计算。"} 刷新读取最近发布的结果。`;
  }

  async function refresh() {
    const requestId = ++state.requestId;
    state.controller?.abort();
    const controller = new AbortController();
    state.controller = controller;
    const requestedMonth = state.month;
    state.loading = true;
    state.failure = null;
    setStatus("loading", "正在读取最新同步", state.data ? "正在刷新最近同步的本月结果…" : "正在读取所选月份的数据…");
    render();
    const timeout = setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch(snapshotUrl(requestedMonth, requestId), {
        method: "GET", cache: "no-store", credentials: "same-origin", headers: { Accept: "application/json" }, signal: controller.signal,
      });
      if (!response.ok) {
        throw new Error(response.status === 404 ? "本月同步文件尚未就绪，请稍后刷新。" : "本次同步结果暂时无法读取，请稍后刷新。");
      }
      const payload = validateData(await response.json(), requestedMonth);
      if (requestId !== state.requestId || requestedMonth !== state.month) return;
      state.data = payload;
      state.failure = null;
      render();
      successStatus(payload);
    } catch (error) {
      if (requestId !== state.requestId || requestedMonth !== state.month) return;
      const reason = error.name === "AbortError" ? "本次读取超时，请重新刷新。" : error.message || "本次读取未完成，请重新刷新。";
      state.failure = reason;
      const retained = state.data?.month === requestedMonth;
      setStatus("error", retained ? "刷新未完成 · 保留上次结果" : "数据暂不可用", reason + (retained ? " 当前仍显示上次成功取数结果。" : " 当前未显示实际值。"));
    } finally {
      clearTimeout(timeout);
      if (requestId === state.requestId) {
        state.loading = false;
        render();
      }
    }
  }

  $("month").addEventListener("change", (event) => {
    if (!MONTHS.includes(event.target.value)) return;
    state.month = event.target.value;
    state.data = null;
    $("source-note").textContent = "刷新读取最近发布的结果，取数时间见右上方。";
    saveSelection();
    refresh();
  });
  $("region").addEventListener("change", (event) => {
    if (!REGIONS.includes(event.target.value)) return;
    state.region = event.target.value;
    saveSelection();
    render();
    if (state.data && !state.loading && !state.failure) successStatus(state.data);
  });
  document.querySelectorAll('input[name="tier"]').forEach((input) => {
    input.addEventListener("change", () => {
      if (!input.checked || !TIERS.includes(input.value)) return;
      state.tier = input.value;
      saveSelection();
      render();
      if (state.data && !state.loading && !state.failure) successStatus(state.data);
    });
  });
  $("refresh").addEventListener("click", refresh);
  render();
  saveSelection();
  loadFallbackTargets();
  refresh();
})();
