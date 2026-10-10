(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.OperationsForecast = api;
})(typeof window === "object" ? window : null, function () {
  "use strict";

  const REGIONS = Object.freeze(["北区", "西区", "南区", "四川地区", "江苏地区"]);
  const MONTH_DAYS = Object.freeze({ "10": 31, "11": 30, "12": 31 });
  const MODE = "historical-shape-reference";
  const TOLERANCE = 1e-9;
  const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
  const record = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
  const finite = (value) => typeof value === "number" && Number.isFinite(value);

  function fail(message) {
    throw new Error(message);
  }

  function sameKeys(value, expected) {
    return record(value) && Object.keys(value).length === expected.length && expected.every((key) => own(value, key));
  }

  function validateHistory(payload) {
    if (!record(payload) || payload.schemaVersion !== 1 || payload.referenceYear !== 2025 ||
        payload.status !== "reference_unreconciled") fail("历史进度参考的版本或来源状态有误。");
    if (!Array.isArray(payload.regions) || payload.regions.length !== REGIONS.length ||
        new Set(payload.regions).size !== REGIONS.length || REGIONS.some((region) => !payload.regions.includes(region))) {
      fail("历史进度参考缺少区域或区域重复。");
    }
    if (!record(payload.source) || typeof payload.source.filename !== "string" || !payload.source.filename.trim() ||
        typeof payload.source.sha256 !== "string" || !/^[a-f\d]{64}$/i.test(payload.source.sha256) ||
        payload.source.table !== "report.dws_data_merge") fail("历史进度参考缺少可核验的来源记录。");
    if ((own(payload, "ratioUnit") && payload.ratioUnit !== "fraction") ||
        (own(payload, "dayIndex") && payload.dayIndex !== "dayOfMonth - 1")) fail("历史进度参考的占比单位或日期索引有误。");
    if (!sameKeys(payload.months, Object.keys(MONTH_DAYS))) fail("历史进度参考的月份不完整。");

    const months = {};
    Object.keys(MONTH_DAYS).forEach((month) => {
      const entry = payload.months[month];
      const days = MONTH_DAYS[month];
      if (!record(entry) || entry.historicalMonth !== `2025-${month}` || entry.days !== days ||
          !sameKeys(entry.curves, REGIONS)) fail(`${month} 月历史进度参考的天数或区域有误。`);
      const curves = {};
      REGIONS.forEach((region) => {
        const curve = entry.curves[region];
        if (!Array.isArray(curve) || curve.length !== days) fail(`${region} ${month} 月历史进度参考的日期不完整。`);
        let previous = 0;
        curves[region] = Array.from(curve, (share, index) => {
          if (!finite(share) || share <= 0 || share > 1 + TOLERANCE) {
            fail(`${region} ${month} 月历史进度占比无效。`);
          }
          const normalized = Math.min(1, share);
          if (normalized < previous) fail(`${region} ${month} 月累计占比出现倒退。`);
          previous = normalized;
          if (index === days - 1 && Math.abs(share - 1) > TOLERANCE) {
            fail(`${region} ${month} 月末累计占比不是 100%。`);
          }
          return index === days - 1 ? 1 : normalized;
        });
      });
      months[month] = { ...entry, curves };
    });
    return { ...payload, regions: REGIONS.slice(), source: { ...payload.source }, months };
  }

  function unavailable(reason) {
    return {
      available: false, reason, gmv: null, attainment: null, gap: null,
      share: null, historicalDate: null, cutoff: null, mode: MODE,
    };
  }

  function parseDate(value) {
    if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
    const year = Number(value.slice(0, 4));
    const month = Number(value.slice(5, 7));
    const day = Number(value.slice(8, 10));
    if (year < 100 || month < 1 || month > 12 || day < 1) return null;
    const days = new Date(Date.UTC(year, month, 0)).getUTCDate();
    return day <= days ? { year, month, day } : null;
  }

  function near(a, b) {
    return Math.abs(a - b) <= Math.max(1, Math.abs(a), Math.abs(b)) * TOLERANCE;
  }

  function validateActual(data) {
    if (!record(data) || typeof data.month !== "string" || !/^\d{4}-\d{2}$/.test(data.month) ||
        !parseDate(`${data.month}-01`)) fail("实际数据的月份有误。");
    if (data.start !== `${data.month}-01`) fail("参考预计仅适用于从月初开始的累计营业额。");
    if (!parseDate(data.end) || !data.end.startsWith(`${data.month}-`) || data.end < data.start) {
      fail("本月尚无完整的实际累计期间。");
    }
    if (data.status !== undefined && data.status !== "OK") fail("本月尚无可用的实际数据。");
    if (!Array.isArray(data.rows) || data.rows.length !== REGIONS.length + 1) fail("全国及五区实际数据不完整。");
    const byRegion = new Map();
    data.rows.forEach((row) => {
      if (!record(row) || (row.region !== "全国" && !REGIONS.includes(row.region)) || byRegion.has(row.region)) {
        fail("实际数据存在未知区域或重复区域。");
      }
      if (row.status !== "OK" || !finite(row.gmv) || row.gmv < 0) fail("区域实际营业额缺失或格式有误。");
      if (own(row, "storeDays") && (!Number.isSafeInteger(row.storeDays) || row.storeDays < 0 ||
          (row.gmv > 0 && row.storeDays === 0))) fail("区域营业店天与实际营业额不一致。");
      byRegion.set(row.region, row);
    });
    if (!byRegion.has("全国") || REGIONS.some((region) => !byRegion.has(region))) fail("全国及五区实际数据不完整。");
    const total = byRegion.get("全国");
    const sum = REGIONS.reduce((value, region) => value + byRegion.get(region).gmv, 0);
    if (!finite(sum) || !near(total.gmv, sum)) fail("全国实际营业额与五区合计不一致，暂不预计。");
    const daysPresent = data.rows.filter((row) => own(row, "storeDays")).length;
    if (daysPresent > 0 && daysPresent !== data.rows.length) fail("全国及五区的营业店天不完整。");
    if (daysPresent && total.storeDays !== REGIONS.reduce((value, region) => value + byRegion.get(region).storeDays, 0)) {
      fail("全国营业店天与五区合计不一致，暂不预计。");
    }
    return byRegion;
  }

  function calculate(data, history, region, target) {
    if (region !== "全国" && !REGIONS.includes(region)) return unavailable("请选择全国或五个区域。");
    let validated;
    let rows;
    try {
      validated = validateHistory(history);
      rows = validateActual(data);
    } catch (error) {
      return unavailable(error.message || "参考预计的数据未通过核验。");
    }
    const month = data.month.slice(5);
    const entry = validated.months[month];
    if (!entry) return unavailable("本月没有提供去年同期历史进度参考。");
    const day = Number(data.end.slice(8, 10));
    if (day > entry.days) return unavailable("历史进度参考没有对应的累计截止日。");
    const selected = region === "全国" ? REGIONS : [region];
    const forecasts = selected.map((name) => rows.get(name).gmv / entry.curves[name][day - 1]);
    const gmv = forecasts.reduce((sum, value) => sum + value, 0);
    if (!finite(gmv)) return unavailable("参考预计超出可计算范围。");
    const targetGmv = record(target) && finite(target.gmv) && target.gmv > 0 ? target.gmv : null;
    let attainment = targetGmv === null ? null : gmv / targetGmv;
    let gap = targetGmv === null ? null : targetGmv - gmv;
    if ((attainment !== null && !finite(attainment)) || (gap !== null && !finite(gap))) {
      // An unusable target must not hide a valid forecast or alter the actual values.
      attainment = null;
      gap = null;
    }
    return {
      available: true, reason: null, gmv, attainment, gap,
      // Nationwide is the sum of five separate forecasts; it has no single historical curve.
      share: region === "全国" ? null : entry.curves[region][day - 1],
      historicalDate: `${validated.referenceYear}-${month}-${String(day).padStart(2, "0")}`,
      cutoff: data.end, mode: MODE,
      referenceStatus: validated.status, source: { ...validated.source },
    };
  }

  return Object.freeze({ validateHistory, calculate, REGIONS, MODE });
});
