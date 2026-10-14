(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.OperationsRidge = api;
})(typeof window === "object" ? window : null, function () {
  "use strict";
  const REGIONS = Object.freeze(["北区", "西区", "南区", "四川地区", "江苏地区"]);
  const MONTHS = Object.freeze(["09", "10", "11", "12"]);
  const MODE = "fixed-calendar-ridge";
  const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
  const record = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
  const finite = (value) => typeof value === "number" && Number.isFinite(value);
  const fail = (message) => { throw new Error(message); };
  const near = (a, b) => Math.abs(a - b) <= Math.max(1, Math.abs(a), Math.abs(b)) * 1e-9;

  // UTC calendar only: results do not depend on browser timezone, DST or today's date.
  function parseDate(value) {
    if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
    const year = Number(value.slice(0, 4)), month = Number(value.slice(5, 7)), day = Number(value.slice(8, 10));
    if (year < 100 || month < 1 || month > 12 || day < 1) return null;
    const days = new Date(Date.UTC(year, month, 0)).getUTCDate();
    return day <= days ? { year, month, day, days } : null;
  }

  function validateModel(model) {
    if (!record(model) || model.schemaVersion !== 1 || model.modelVersion !== "calendar-ridge-2025q4-alpha100-v1" ||
        model.status !== "prototype_reference_unreconciled" || model.alpha !== 100 || model.forecastYear !== 2026 ||
        model.featureSchema !== "region5-month12-mondayWeekday7-relativeDay-v1" || model.targetTransform !== "log1p") {
      fail("Ridge 模型缺失或版本不兼容，暂不预计。");
    }
    for (const [name, expected] of [["regions", REGIONS], ["supportedMonths", MONTHS]]) {
      if (!Array.isArray(model[name]) || model[name].length !== expected.length || model[name].some((v, i) => v !== expected[i])) {
        fail("Ridge 模型的区域或支持月份有误。");
      }
    }
    const p = model.parameters;
    if (!record(p) || !Array.isArray(p.mean) || !Array.isArray(p.scale) || !Array.isArray(p.coefficients) ||
        p.mean.length !== 25 || p.scale.length !== 25 || p.coefficients.length !== 26 ||
        p.mean.some((v) => !finite(v) || v < 0 || v > 1) || p.scale.some((v) => !finite(v) || v <= 0) ||
        p.coefficients.some((v) => !finite(v))) fail("Ridge 模型参数不完整或无效。");
    const training = model.training;
    if (!record(training) || training.start !== "2025-10-01" || training.end !== "2025-12-31" || training.rows !== 460 ||
        training.table !== "report.dws_data_merge" || !/^[a-f\d]{64}$/.test(training.inputSha256 || "") ||
        !/^[a-f\d]{64}$/.test(model.parametersSha256 || "")) fail("Ridge 模型的训练来源不完整。");
    return model;
  }

  function monthDate(month, model) {
    if (typeof month !== "string" || !/^\d{4}-\d{2}$/.test(month)) fail("实际数据的月份有误。");
    const day = parseDate(`${month}-01`);
    if (!day || day.year !== model.forecastYear || !MONTHS.includes(month.slice(5))) fail("本月不在固定 Ridge 模型的支持范围。");
    return day;
  }

  function dailyShape(month, region, payload) {
    const model = validateModel(payload);
    const first = monthDate(month, model);
    if (!REGIONS.includes(region)) fail("Ridge 日形状只支持五个区域。");
    const p = model.parameters, regionIndex = REGIONS.indexOf(region);
    return Array.from({ length: first.days }, (_, index) => {
      const day = index + 1;
      const weekday = (new Date(Date.UTC(first.year, first.month - 1, day)).getUTCDay() + 6) % 7;
      const x = new Array(25).fill(0);
      x[regionIndex] = 1; x[5 + first.month - 1] = 1; x[17 + weekday] = 1; x[24] = day / first.days;
      let log = p.coefficients[0];
      for (let j = 0; j < 25; j += 1) log += ((x[j] - p.mean[j]) / p.scale[j]) * p.coefficients[j + 1];
      return Math.max(Math.expm1(Math.min(50, Math.max(-30, log))), 1e-9);
    });
  }

  function cumulativeShare(month, cutoffDay, region, model) {
    const shape = dailyShape(month, region, model);
    if (!Number.isSafeInteger(cutoffDay) || cutoffDay < 1 || cutoffDay > shape.length) fail("Ridge 截止日期超出所选月份。");
    if (cutoffDay === shape.length) return 1;
    const prefix = shape.slice(0, cutoffDay).reduce((a, b) => a + b, 0);
    const total = shape.reduce((a, b) => a + b, 0);
    const share = prefix / total;
    if (!finite(share) || share <= 0 || share > 1) fail("Ridge 进度无法计算，暂不预计。");
    return share;
  }

  function validateActual(data, model) {
    if (!record(data)) fail("本月尚无实际累计数据。");
    monthDate(data.month, model);
    const cutoff = parseDate(data.end);
    if (data.start !== `${data.month}-01`) fail("Ridge 预计仅适用于从月初开始的累计营业额。");
    if (!cutoff || !data.end.startsWith(`${data.month}-`) || data.end < data.start) fail("本月尚无完整的实际累计期间。");
    if (data.status !== undefined && data.status !== "OK") fail("本月尚无可用的实际数据。");
    if (!Array.isArray(data.rows) || data.rows.length !== REGIONS.length + 1) fail("全国及五区实际数据不完整。");
    const rows = new Map();
    for (const row of data.rows) {
      if (!record(row) || (row.region !== "全国" && !REGIONS.includes(row.region)) || rows.has(row.region)) fail("实际数据存在未知或重复区域。");
      if (row.status !== "OK" || !finite(row.gmv) || row.gmv < 0) fail("区域实际营业额缺失或格式有误。");
      if (own(row, "storeDays") && (!Number.isSafeInteger(row.storeDays) || row.storeDays < 0 || (row.gmv > 0 && row.storeDays === 0))) {
        fail("区域营业店天与实际营业额不一致。");
      }
      rows.set(row.region, row);
    }
    if (!rows.has("全国") || REGIONS.some((r) => !rows.has(r))) fail("全国及五区实际数据不完整。");
    const regionalSum = REGIONS.reduce((a, r) => a + rows.get(r).gmv, 0);
    if (!finite(regionalSum) || !near(rows.get("全国").gmv, regionalSum)) fail("全国实际营业额与五区合计不一致，暂不预计。");
    const countDays = data.rows.filter((r) => own(r, "storeDays")).length;
    if (countDays > 0 && countDays !== data.rows.length) fail("全国及五区的营业店天不完整。");
    if (countDays && rows.get("全国").storeDays !== REGIONS.reduce((a, r) => a + rows.get(r).storeDays, 0)) {
      fail("全国营业店天与五区合计不一致，暂不预计。");
    }
    return { rows, cutoff };
  }

  function unavailable(reason) {
    return { available: false, reason, gmv: null, attainment: null, gap: null, share: null,
             historicalDate: null, cutoff: null, mode: MODE };
  }

  // Same contract as OperationsForecast.calculate. Actuals and targets are never mutated.
  function calculate(data, payload, region, target) {
    if (region !== "全国" && !REGIONS.includes(region)) return unavailable("请选择全国或五个区域。");
    try {
      const model = validateModel(payload);
      const { rows, cutoff } = validateActual(data, model);
      const selected = region === "全国" ? REGIONS : [region];
      let share = null;
      const gmv = selected.reduce((sum, name) => {
        const p = cumulativeShare(data.month, cutoff.day, name, model);
        if (region !== "全国") share = p;
        // A closed month must return observed actual, with no numeric drift.
        return sum + (cutoff.day === cutoff.days ? rows.get(name).gmv : rows.get(name).gmv / p);
      }, 0);
      if (!finite(gmv)) return unavailable("Ridge 预计超出可计算范围。");
      const targetGmv = record(target) && finite(target.gmv) && target.gmv > 0 ? target.gmv : null;
      let attainment = targetGmv === null ? null : gmv / targetGmv;
      let gap = targetGmv === null ? null : targetGmv - gmv;
      if ((attainment !== null && !finite(attainment)) || (gap !== null && !finite(gap))) { attainment = null; gap = null; }
      return { available: true, reason: null, gmv, attainment, gap, share, historicalDate: null,
               cutoff: data.end, mode: MODE, modelVersion: model.modelVersion, alpha: model.alpha,
               referenceStatus: model.status, trainingEnd: model.training.end,
               source: { table: model.training.table, sha256: model.training.inputSha256 },
               limitations: Array.isArray(model.limitations) ? model.limitations.slice() : [] };
    } catch (error) { return unavailable(error.message || "Ridge 预计的数据未通过核验。"); }
  }

  return Object.freeze({ REGIONS, MODE, validateModel, parseDate, dailyShape, cumulativeShare, calculate });
});
