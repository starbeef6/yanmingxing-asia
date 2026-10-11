(() => {
  "use strict";

  const MONTHS = ["2026-09", "2026-10", "2026-11", "2026-12"];
  const REGIONS = ["全国", "北区", "西区", "南区", "四川地区", "江苏地区"];
  const TIERS = ["保底", "平衡", "冲刺"];
  const $ = (id) => document.getElementById(id);
  const numberFormat = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2, minimumFractionDigits: 2 });
  const percentFormat = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 1, minimumFractionDigits: 1 });
  const storeCountFormat = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 });
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
    history: null,
    historyRequestId: 0,
    historyLoading: false,
    historyFailure: null,
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
    if ((payload.end !== null || payload.fetchedAt !== null || payload.source.expectedThrough != null) && (typeof payload.fetchedAt !== "string" ||
        !/(?:Z|[+-]\d{2}:\d{2})$/.test(payload.fetchedAt) || !Number.isFinite(Date.parse(payload.fetchedAt)))) {
      throw new Error("快照未提供有效的取数时间，请重新刷新。");
    }
    const seen = new Set();
    const countKeys = payload.rows.map((row) => row &&
      ["operatingStores", "operatingStoresDate"].filter((key) => Object.hasOwn(row, key)).length);
    const hasStoreCounts = countKeys.every((keys) => keys === 2);
    if ((!hasStoreCounts && countKeys.some((keys) => keys !== 0)) || (hasStoreCounts &&
        payload.source.operatingStoresMethod !== "cutoff-day-positive-gmv-store-count")) {
      throw new Error("门店数量的口径或字段不完整，保留上次成功结果。");
    }
    const rows = payload.rows.map((row) => {
      if (!row || !REGIONS.includes(row.region) || seen.has(row.region)) throw new Error("返回的区域数据有误，请重新刷新。");
      seen.add(row.region);
      const gmv = finite(row.gmv);
      const storeDays = finite(row.storeDays);
      const storeAverage = finite(row.storeAverage);
      const operatingStores = hasStoreCounts ? finite(row.operatingStores) : null;
      const operatingStoresDate = hasStoreCounts ? row.operatingStoresDate : null;
      if (row.status === "NO_DATA") {
        if (gmv !== null || storeDays !== null || storeAverage !== null || (hasStoreCounts &&
            (row.operatingStores !== null || operatingStoresDate !== null))) throw new Error("暂无数据的区域返回了不一致的指标，请重新刷新。");
        return { region: row.region, gmv: null, storeDays: null, storeAverage: null, operatingStores: null, operatingStoresDate: null, status: "NO_DATA" };
      }
      if (row.status !== "OK" || gmv === null || storeDays === null || storeDays < 0 || !Number.isSafeInteger(storeDays) ||
          (storeDays > 0 && storeAverage === null) || (storeDays === 0 && storeAverage !== null)) {
        throw new Error("经营指标缺失或格式有误，请重新刷新。");
      }
      const calculatedAverage = storeDays > 0 ? gmv / storeDays : null;
      if (calculatedAverage !== null && Math.abs(calculatedAverage - storeAverage) > Math.max(1, Math.abs(calculatedAverage)) * 1e-9) {
        throw new Error("店日均与营业额、营业店天不一致，保留上次成功结果。");
      }
      if (hasStoreCounts && (typeof row.operatingStores !== "number" || !Number.isSafeInteger(operatingStores) ||
          operatingStores < 0 || operatingStores > storeDays || operatingStoresDate !== payload.end || !validDate(operatingStoresDate))) {
        throw new Error("门店数量或统计日有误，保留上次成功结果。");
      }
      return { region: row.region, gmv, storeDays, storeAverage: calculatedAverage, operatingStores, operatingStoresDate, status: "OK" };
    });
    if (rows.length !== REGIONS.length || REGIONS.some((region) => !seen.has(region))) {
      throw new Error("全国及五区快照不完整，请重新刷新。");
    }
    if (payload.end === null && ((payload.fetchedAt !== null && !payload.source.expectedThrough) || rows.some((row) => row.status !== "NO_DATA"))) {
      throw new Error("尚无实际期间的快照返回了营业数据，请重新刷新。");
    }
    if (Object.hasOwn(payload.source, "dataThrough") || Object.hasOwn(payload.source, "expectedThrough")) {
      const expected = payload.source.expectedThrough;
      if (payload.source.dataThrough !== payload.end || (expected !== null &&
          (!validDate(expected) || !expected.startsWith(requestedMonth) || expected < payload.start)) ||
          (payload.end !== null && (expected === null || payload.end > expected))) {
        throw new Error("快照实际截止日与数据覆盖说明不一致，保留上次成功结果。");
      }
    }
    return { ...payload, rows, targets: payload.targets && typeof payload.targets === "object" ? payload.targets : {} };
  }

  function targetFor(region) {
    const live = state.data?.month === state.month ? state.data.targets?.[state.tier]?.[region] : null;
    const fallback = state.fallbackTargets[state.month]?.[state.tier]?.[region] || {};
    const target = validatedTarget(live);
    return target ? { ...target, operatingStores: target.operatingStores ?? fallback.operatingStores } : fallback;
  }

  function validatedTarget(value) {
    if (!value || typeof value !== "object") return null;
    const gmv = finite(value.gmv);
    const storeAverage = finite(value.storeAverage);
    const stores = finite(value.operatingStores);
    const operatingStores = stores !== null && stores >= 0 ? stores : null;
    return gmv !== null && gmv > 0 && storeAverage !== null && storeAverage > 0 ? { gmv, storeAverage, operatingStores } : null;
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
          if (!target || target.operatingStores === null) throw new Error("目标月份、档位、区域或门店数量不完整。");
          months[month][tier][region] = target;
        });
      });
    });
    return months;
  }

  async function loadFallbackTargets() {
    try {
      const response = await fetch("../assets/operations-targets.json?v=20261011-stores-target", { headers: { Accept: "application/json" } });
      if (!response.ok) return;
      state.fallbackTargets = validateFallbackTargets(await response.json());
      render();
    } catch (_) {
      // Actual values and query status remain independent of static target availability.
    }
  }

  async function loadHistoryReference() {
    const requestId = ++state.historyRequestId;
    state.historyLoading = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      if (!window.OperationsForecast) throw new Error("历史预计计算暂不可用。");
      const url = new URL("../assets/operations-history-2025.json", window.location.href);
      url.searchParams.set("refresh", `${Date.now()}-${requestId}`);
      const response = await fetch(url.href, { cache: "no-store", headers: { Accept: "application/json" }, signal: controller.signal });
      if (!response.ok) throw new Error("历史参考暂时无法读取。");
      const history = window.OperationsForecast.validateHistory(await response.json());
      if (requestId !== state.historyRequestId) return;
      state.history = history;
      state.historyFailure = null;
    } catch (_) {
      if (requestId !== state.historyRequestId) return;
      state.historyFailure = state.history ? "历史参考刷新未完成，沿用已加载的固定占比。" : "历史参考暂不可用，实际指标正常显示。";
    } finally {
      clearTimeout(timeout);
      if (requestId === state.historyRequestId) {
        state.historyLoading = false;
        render();
      }
    }
  }

  function forecastFor(region, data) {
    if (!window.OperationsForecast || !state.history) return { available: false };
    return window.OperationsForecast.calculate(data, state.history, region, targetFor(region));
  }

  function gapDisplay(forecast) {
    const gap = finite(forecast?.gap);
    if (gap === null) return { label: "预计缺口", value: "—", kind: "waiting" };
    if (Math.abs(gap) < 0.005) return { label: "距目标", value: "持平", kind: "even" };
    return { label: gap > 0 ? "预计缺口" : "预计超额", value: number(Math.abs(gap), 10000, " 万元"), kind: gap > 0 ? "shortfall" : "surplus" };
  }

  function renderForecast(data) {
    const supportedMonth = ["10", "11", "12"].includes(state.month.slice(-2));
    $("forecast-summary").hidden = !supportedMonth;
    const forecast = forecastFor(state.region, data);
    const gap = gapDisplay(forecast);
    $("forecast-value").textContent = forecast.available ? number(forecast.gmv, 10000) : "—";
    $("forecast-rate").textContent = forecast.available ? percent(forecast.attainment) : "—";
    $("forecast-gap-label").textContent = gap.label;
    $("forecast-gap").textContent = forecast.available ? gap.value : "—";
    $("forecast-gap").dataset.kind = gap.kind;
    $("forecast-share-fact").hidden = state.region === "全国" || !forecast.available;
    $("forecast-share").textContent = percent(forecast.share ?? null);
    let note = "按 2025 年同月同日走势估算；历史口径尚待核对。";
    if (!data || data.end === null) note = "本月尚无累计实际数据，暂不估算。";
    else if (!state.history) note = state.historyLoading ? "正在读取历史参考，实际指标正常显示。" : state.historyFailure || "历史参考暂不可用，实际指标正常显示。";
    else if (!forecast.available) note = "当前累计实际或历史参考不完整，暂不估算。";
    else {
      const historicalDate = forecast.historicalDate?.slice(5).replace("-", "/");
      note = state.region === "全国" ? `按 2025 年 ${historicalDate} 各区进度分别估算后汇总；历史口径尚待核对。` : `按 2025 年 ${historicalDate} 累计占比估算；历史口径尚待核对。`;
      if (state.historyFailure) note += ` ${state.historyFailure}`;
    }
    $("forecast-note").textContent = note;
    return forecast.available;
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
    $("comparison-caption").textContent = data?.end
      ? `目标为所选档位的全月目标；在营统计日 ${data.end.slice(5).replace("-", "/")}，按当天有营业额统计。`
      : "目标为所选档位的全月目标；在营按实际截止日有营业额统计。";
    $("timestamp").textContent = data?.fetchedAt ? `取数：${timestamp(data.fetchedAt)}` : "尚未取数";
    $("gmv-value").textContent = number(selected?.gmv, 10000);
    $("average-value").textContent = number(selected?.storeAverage);
    $("stores-value").textContent = selected?.operatingStores === null || selected?.operatingStores === undefined
      ? "—" : storeCountFormat.format(selected.operatingStores);
    $("stores-target").textContent = finite(target.operatingStores) === null ? "—" : `${storeCountFormat.format(target.operatingStores)} 家`;
    $("stores-target").title = finite(target.operatingStores) === null ? "目标尚未取得" : `原表目标 ${target.operatingStores} 家；按原表整数格式显示`;
    $("stores-date").textContent = selected?.operatingStoresDate
      ? `${selected.operatingStoresDate.slice(5).replace("-", "/")} · 有营业额门店`
      : data?.end ? "门店数量待同步" : "暂无实际统计日";
    $("gmv-target").textContent = number(target.gmv, 10000, " 万元");
    $("average-target").textContent = number(target.storeAverage, 1, " 元");
    const gmvRatio = ratio(selected?.gmv, target.gmv);
    const averageRatio = ratio(selected?.storeAverage, target.storeAverage);
    $("gmv-rate").textContent = percent(gmvRatio);
    $("average-rate").textContent = percent(averageRatio);
    progress("gmv-progress", gmvRatio);
    progress("average-progress", averageRatio);

    const forecastsAvailable = renderForecast(data) && REGIONS.slice(1).every((region) => forecastFor(region, data).available);
    const header = document.createElement("tr");
    const headers = forecastsAvailable
      ? ["区域", "GMV（万元）", "店日均（元）", "参考预计全月（万元）", "预计达成", "预计缺口 / 超额（万元）"]
      : ["区域", "GMV（万元）", "店日均（元）", "占全月目标", "店日均达成"];
    headers.forEach((label) => {
      const th = document.createElement("th");
      th.scope = "col";
      th.textContent = label;
      header.appendChild(th);
    });
    $("region-head").replaceChildren(header);
    $("region-head").closest("table").dataset.forecast = String(forecastsAvailable);
    $("forecast-table-hint").hidden = !forecastsAvailable;

    const ordered = REGIONS.slice(1).map((region) => rows.find((row) => row.region === region) || { region, gmv: null, storeAverage: null });
    if (rows.length) ordered.sort((a, b) => (b.gmv ?? -Infinity) - (a.gmv ?? -Infinity));
    const fragment = document.createDocumentFragment();
    ordered.forEach((row) => {
      const regionTarget = targetFor(row.region);
      const tr = document.createElement("tr");
      tr.dataset.selected = String(row.region === state.region);
      const heading = document.createElement("th");
      heading.scope = "row";
      const regionName = document.createElement("span");
      regionName.className = "region-name";
      regionName.textContent = row.region;
      const stores = document.createElement("span");
      stores.className = "region-stores";
      stores.textContent = row.operatingStores === null || row.operatingStores === undefined
        ? "在营 — 家" : `在营 ${row.operatingStores} 家`;
      stores.title = row.operatingStoresDate
        ? `${row.operatingStoresDate} 总营业额大于 0 的门店数` : "尚无同日门店数量";
      const storesTarget = document.createElement("span");
      storesTarget.className = "region-stores-target";
      storesTarget.textContent = finite(regionTarget.operatingStores) === null
        ? "目标 — 家" : `目标 ${storeCountFormat.format(regionTarget.operatingStores)} 家`;
      storesTarget.title = finite(regionTarget.operatingStores) === null ? "目标尚未取得"
        : `${state.month} ${state.tier} · 原表在营门店目标 ${regionTarget.operatingStores} 家`;
      heading.append(regionName, stores, storesTarget);
      tr.appendChild(heading);
      const forecast = forecastFor(row.region, data);
      const gap = finite(forecast.gap);
      const values = forecastsAvailable
        ? [number(row.gmv, 10000), number(row.storeAverage), number(forecast.gmv, 10000), percent(forecast.attainment), gap === null ? "—" : Math.abs(gap) < 0.005 ? "持平" : `${gap > 0 ? "缺口" : "超额"} ${number(Math.abs(gap), 10000)}`]
        : [number(row.gmv, 10000), number(row.storeAverage), percent(ratio(row.gmv, regionTarget.gmv)), percent(ratio(row.storeAverage, regionTarget.storeAverage))];
      values.forEach((value, index) => {
        const td = document.createElement("td");
        const targetMetric = index === 0 ? { value: regionTarget.gmv, divisor: 10000 }
          : index === 1 ? { value: regionTarget.storeAverage, divisor: 1 } : null;
        if (targetMetric) {
          const actual = document.createElement("span");
          actual.className = "table-actual";
          actual.textContent = value;
          const targetLine = document.createElement("span");
          targetLine.className = "table-target";
          targetLine.textContent = `目标 ${number(targetMetric.value, targetMetric.divisor)}`;
          td.append(actual, targetLine);
        } else {
          td.textContent = value;
        }
        if (forecastsAvailable && index === 4 && gap !== null) td.dataset.kind = gap > 0 ? "shortfall" : "surplus";
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
    const pendingDate = typeof data.source.expectedThrough === "string" && data.end && data.end < data.source.expectedThrough;
    if (data.end === null) {
      setStatus("waiting", "仅显示目标", data.source.expectedThrough
        ? "本月实际数据尚未取得，目标正常显示。" : "本月尚无已完成营业日，实际值暂不显示。");
    } else if (!data.rows.length || !selected || selected.status === "NO_DATA") {
      setStatus("waiting", "本月暂无数据", "所选区域暂无实际营业数据，当前仅显示目标。");
    } else if (pendingDate) {
      setStatus("waiting", `最近同步 · 截至 ${data.end.slice(5).replace("-", "/")}`, `源数据暂截至 ${data.end.slice(5).replace("-", "/")}；后续营业日待数据库提供完整日记录。`);
    } else if (incomplete) {
      setStatus("waiting", "最近同步 · 原表查询", "数据来自原表查询；历史补录随下次成功同步更新。");
    } else if (selected.storeDays === 0) {
      setStatus("ready", "最近同步 · 本月数据", "所选区域暂无正营业额记录，店日均暂不计算。");
    } else if (finite(target.gmv) === null || finite(target.storeAverage) === null) {
      setStatus("ready", "最近同步 · 本月数据", "实际数据已更新，本月所选档位的目标尚未配置。");
    } else {
      setStatus("ready", "最近同步 · 本月数据", "已读取最近成功同步的结果；历史补录随下次同步更新。");
    }
    const source = typeof data.source === "string" ? data.source : data.source?.description || data.source?.label || "";
    $("source-note").textContent = incomplete
      ? "刷新读取最近发布的结果；上游重算更新时间未提供。"
      : `${source || "GMV 为总营业额；店日均按营业店天计算。"} 刷新读取最近发布的结果。`;
  }

  async function refresh() {
    loadHistoryReference();
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
