(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.OperationsChatContext = api;
})(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";

  const AREA_NAMES = new Set(["北区", "西区", "南区", "四川地区", "江苏地区"]);
  const NUMBER_FIELDS = ["gmv", "storeDays", "storeAverage", "operatingStores"];
  const DIAGNOSIS_FIELDS = ["pattern", "basis", "jointStatus", "metCount", "required", "satisfiedPairs", "action", "falsifier"];

  function numberOrNull(value) {
    if (typeof value !== "number" && typeof value !== "string") return null;
    if (typeof value === "string" && !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(value.trim())) return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function targetFor(value) {
    const target = value && typeof value === "object" ? value : {};
    return {
      gmv: numberOrNull(target.gmv),
      storeAverage: numberOrNull(target.storeAverage),
      operatingStores: numberOrNull(target.operatingStores),
    };
  }

  function forecastFor(value) {
    const forecast = value && typeof value === "object" ? value : {};
    return {
      available: forecast.available === true,
      gmv: numberOrNull(forecast.gmv),
      attainment: numberOrNull(forecast.attainment),
      gap: numberOrNull(forecast.gap),
      historicalDate: typeof forecast.historicalDate === "string" ? forecast.historicalDate : null,
    };
  }

  function compactRow(row) {
    const result = { region: row.region };
    for (const field of NUMBER_FIELDS) result[field] = numberOrNull(row[field]);
    result.operatingStoresDate = typeof row.operatingStoresDate === "string" ? row.operatingStoresDate : null;
    result.status = typeof row.status === "string" ? row.status : null;
    result.target = targetFor(row.target);
    result.forecast = forecastFor(row.forecast);
    return result;
  }

  function selectedDiagnosis(current, diagnosisApi) {
    let candidates = [
      diagnosisApi?.selected,
      diagnosisApi?.diagnosis,
      current?.selectedDiagnosis,
      current?.diagnosis,
      current?.selected?.diagnosis,
    ];
    const method = diagnosisApi?.getSelectedDiagnosis || diagnosisApi?.getSelected || diagnosisApi?.diagnose;
    if (typeof method === "function") {
      try {
        candidates.unshift(method.call(diagnosisApi, current));
      } catch (_) {
        // Older diagnosis helpers may not support the current context shape.
      }
    }
    const diagnosis = candidates.find((item) => item && typeof item === "object") || {};
    const result = {};
    for (const field of DIAGNOSIS_FIELDS) {
      if (Object.prototype.hasOwnProperty.call(diagnosis, field)) result[field] = diagnosis[field];
    }
    if (typeof diagnosis.summary === "string" && diagnosis.summary.length <= 240) result.summary = diagnosis.summary;
    return result;
  }

  function compact(current, options) {
    const context = current && typeof current === "object" ? current : {};
    const diagnosisApi = options?.diagnosisApi || (typeof window !== "undefined" ? window.OperationsDiagnosis : null);
    const sourceRows = Array.isArray(context.regions) ? context.regions : [];
    // OperationsContext may include a nationwide summary row; this list is only the five areas.
    const areaRows = sourceRows.filter((row) => row && AREA_NAMES.has(row.region));
    const regions = areaRows.map(compactRow);
    const availableRegions = regions
      .filter((row) => NUMBER_FIELDS.some((field) => row[field] !== null))
      .map((row) => row.region);
    const selectedSource = context.selected && typeof context.selected === "object"
      ? { ...context.selected, target: context.target || context.selected.target, forecast: context.forecast || context.selected.forecast }
      : null;
    const result = {
      month: typeof context.month === "string" ? context.month : null,
      tier: typeof context.tier === "string" ? context.tier : null,
      region: typeof context.region === "string" ? context.region : null,
      start: typeof context.start === "string" ? context.start : null,
      end: typeof context.end === "string" ? context.end : null,
      loading: context.loading === true,
      failure: typeof context.failure === "string" ? context.failure : null,
      sourceComplete: typeof context.sourceComplete === "boolean" ? context.sourceComplete : null,
      storeMethod: typeof context.storeMethod === "string" ? context.storeMethod : null,
      storeDefinition: typeof context.storeDefinition === "string" ? context.storeDefinition : null,
      selected: selectedSource ? compactRow(selectedSource) : null,
      target: targetFor(context.target || selectedSource?.target),
      forecast: forecastFor(context.forecast),
      regions,
      availableRegions,
      diagnosis: selectedDiagnosis(context, diagnosisApi),
      capabilities: { dashboardData: true, screenAccess: false },
    };
    return result;
  }

  return Object.freeze({ compact });
});
