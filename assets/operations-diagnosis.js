(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.OperationsDiagnosis = api;
})(typeof window === "object" ? window : null, function () {
  "use strict";
  const finite = (value) => typeof value === "number" && Number.isFinite(value);
  const monthEnd = (month) => {
    if (typeof month !== "string" || !/^\d{4}-\d{2}$/.test(month)) return null;
    const [year, number] = month.split("-").map(Number);
    if (number < 1 || number > 12) return null;
    return `${month}-${String(new Date(Date.UTC(year, number, 0)).getUTCDate()).padStart(2, "0")}`;
  };
  const pct = (value) => `${(value * 100).toFixed(1)}%`;
  const forecastMode = (value) => ["fixed-calendar-ridge", "historical-shape-reference"].includes(value) ? value : null;
  const forecastBasis = (mode) => mode === "fixed-calendar-ridge"
    ? "GMV按固定参数 Ridge 模型结合本月实际校准预计，不代表全月实际"
    : mode === "historical-shape-reference"
      ? "GMV按历史进度参考预计，不代表全月实际"
      : "GMV按页面提供的参考预计，不代表全月实际";
  function metric(actual, target) {
    const knownActual = finite(actual) ? actual : null;
    const knownTarget = finite(target) ? target : null;
    if (knownActual === null || knownTarget === null || knownTarget <= 0) {
      return { status: "unknown", attainment: null, actual: knownActual, target: knownTarget };
    }
    const attainment = actual / target;
    return Number.isFinite(attainment)
      ? { status: attainment >= 1 ? "met" : "below", attainment, actual: knownActual, target: knownTarget }
      : { status: "unknown", attainment: null, actual: knownActual, target: knownTarget };
  }
  function assess(context, row) {
    const selected = row || context?.selected;
    const target = selected?.target || context?.target || {};
    const actualEnd = context?.end;
    const fullMonth = context?.start === `${context?.month}-01` && actualEnd && actualEnd === monthEnd(context?.month);
    const isCurrentSelected = selected && selected.region === context?.region;
    const average = metric(selected?.storeAverage, target.storeAverage);
    let gmv;
    if (fullMonth && selected?.status !== "NO_DATA") gmv = { ...metric(selected?.gmv, target.gmv), basis: "actual" };
    else if (selected?.status === "NO_DATA" || actualEnd) {
      const forecast = isCurrentSelected ? context?.forecast : selected?.forecast;
      gmv = forecast?.available === true
        ? { ...metric(forecast.gmv, target.gmv), basis: "forecast", forecastMode: forecastMode(forecast.mode), referenceDate: forecast.mode === "historical-shape-reference" ? forecast.historicalDate || null : null }
        : { status: "unknown", attainment: null, actual: finite(selected?.gmv) ? selected.gmv : null, target: finite(target.gmv) ? target.gmv : null, basis: "unknown" };
    } else gmv = { status: "unknown", attainment: null, actual: finite(selected?.gmv) ? selected.gmv : null, target: finite(target.gmv) ? target.gmv : null, basis: "unknown" };
    const storeDateMatches = !!actualEnd && selected?.operatingStoresDate === actualEnd;
    const monthlyDefinition = context?.storeMethod === "monthly-positive-gmv-status-1-2-distinct-store-count";
    const stores = monthlyDefinition && storeDateMatches
      ? metric(selected?.operatingStores, target.operatingStores)
      : { status: "unknown", attainment: null, actual: finite(selected?.operatingStores) ? selected.operatingStores : null, target: finite(target.operatingStores) ? target.operatingStores : null };
    let averageMetric = average;
    const quality = [];
    if (finite(selected?.gmv) && finite(selected?.storeAverage) && finite(selected?.storeDays) && selected.storeDays > 0) {
      const reconciledGmv = selected.storeAverage * selected.storeDays;
      const tolerance = Math.max(0.01, Math.abs(selected.gmv) * 1e-8);
      if (Math.abs(reconciledGmv - selected.gmv) > tolerance) {
        averageMetric = { status: "unknown", attainment: null, actual: selected.storeAverage, target: finite(target.storeAverage) ? target.storeAverage : null };
        quality.push("GMV与店日均乘营业店天未能对上，相关组合判断暂停核对");
      }
    }
    const sourceOk = context?.sourceComplete === true;
    if (context?.loading) quality.push("页面正在刷新");
    if (context?.failure) quality.push("刷新未成功，沿用当前快照");
    if (context?.sourceComplete === false) quality.push("来源不完整，按当前快照待核对");
    else if (!sourceOk) quality.push("来源完整性尚未确认");
    if (!fullMonth && gmv.basis === "forecast") quality.push(forecastBasis(gmv.forecastMode));
    if (!fullMonth && gmv.status === "unknown") quality.push("GMV没有完整全月实际或可用进度参考");
    if (!monthlyDefinition) quality.push("在营门店定义未满足月内正GMV且状态1/2去重口径");
    else if (!storeDateMatches) quality.push("门店统计日与GMV截止日错期，暂不合并判断");
    if (!finite(target.gmv) || target.gmv <= 0) quality.push("GMV目标缺失或为零");
    if (!finite(target.storeAverage) || target.storeAverage <= 0) quality.push("店日均目标缺失或为零");
    if (!finite(target.operatingStores) || target.operatingStores <= 0) quality.push("在营门店目标缺失或为零");
    return { metrics: { gmv, average: averageMetric, stores }, quality, fullMonth: !!fullMonth, sourceOk };
  }
  function diagnosisText(assessment) {
    const { gmv, average, stores } = assessment.metrics;
    const labels = { gmv: "GMV", average: "店日均", stores: "在营门店" };
    const entries = [["gmv", gmv], ["average", average], ["stores", stores]];
    const stateLabel = (key, item) => item.status === "unknown" ? `${labels[key]}待核对`
      : key === "gmv" && item.basis === "forecast"
        ? `GMV进度参考${item.status === "met" ? "达到" : "低于"}目标线`
        : `${labels[key]}${item.status === "met" ? "达标" : "未达标"}`;
    const formatValue = (key, value) => key === "gmv" ? `${(value / 10000).toFixed(2)}万元`
      : key === "average" ? `${value.toFixed(2)}元` : `${Math.round(value)}家`;
    const evidence = entries.map(([key, item]) => {
      if (item.status !== "unknown") {
        const gap = item.target - item.actual;
        const difference = Math.abs(gap);
        const amount = formatValue(key, difference);
        const label = key === "gmv" && item.basis === "forecast" ? "参考预计" : "实际";
        const gapText = Math.abs(gap) < 1e-9 ? "与目标持平"
          : gap > 0 ? `距目标差${amount}` : `超过目标${amount}`;
        return `${labels[key]}${label}${formatValue(key, item.actual)}、目标${formatValue(key, item.target)}，${gapText}`;
      }
      if (key === "gmv" && item.basis === "unknown" && finite(item.actual)) {
        return `GMV本月累计${formatValue(key, item.actual)}，未按全月目标判定`;
      }
      if (finite(item.actual) && (item.target === null || item.target <= 0)) {
        return `${labels[key]}当前值${formatValue(key, item.actual)}，目标缺失或为零`;
      }
      return `${labels[key]}待核对`;
    });
    const joint = assessment.jointStatus === "pass"
      ? `组合通过：达标${assessment.metCount}/3，达到任意两项即通过；满足组合：${assessment.satisfiedPairs.join("、")}。${assessment.metCount === 2 ? "第三项差距单列展示，不改变组合通过结果。" : ""}`
      : assessment.jointStatus === "warning"
        ? `组合预警：目前达标${assessment.metCount}/3，少于通过所需的2项。`
        : `组合待判定：已确认达标${assessment.metCount}/3，未知项可能使结果跨过2项门槛。`;
    let text = `${joint}指标情况：${evidence.join("；")}。`;
    if (assessment.pattern === "010") {
      text += "只有店日均达标。门店规模收缩或低产门店退出可能抬高均值，这是待核实的解释，不能据此断言发生了闭店或外部经营环境变差。";
    }
    if (gmv.basis === "forecast") text += `${forecastBasis(gmv.forecastMode)}。`;
    if (!assessment.sourceOk || assessment.quality.some((item) => item.includes("刷新") || item.includes("来源不完整"))) text = `按当前快照，待核对。${text}`;
    if (assessment.quality.length) text += `\n${assessment.quality.join("；")}。`;
    return text;
  }
  function diagnose(context, row) {
    const assessment = assess(context, row);
    const { gmv, average, stores } = assessment.metrics;
    const pattern = [gmv, average, stores].map((item) => item.status === "met" ? "1" : item.status === "below" ? "0" : "?").join("");
    const metricEntries = [["GMV", gmv], ["店日均", average], ["在营门店", stores]];
    const metCount = metricEntries.filter(([, item]) => item.status === "met").length;
    const unknownCount = metricEntries.filter(([, item]) => item.status === "unknown").length;
    const satisfied = metricEntries.filter(([, item]) => item.status === "met").map(([name]) => name);
    const pairs = [];
    for (let left = 0; left < satisfied.length; left += 1) {
      for (let right = left + 1; right < satisfied.length; right += 1) pairs.push(`${satisfied[left]}+${satisfied[right]}`);
    }
    const jointStatus = metCount >= 2 ? "pass" : metCount + unknownCount < 2 ? "warning" : "unknown";
    Object.assign(assessment, { pattern, metCount, required: 2, satisfiedPairs: pairs, jointStatus });
    const weak = [{ key: "gmv", item: gmv, name: "GMV" }, { key: "stores", item: stores, name: "在营门店" }, { key: "average", item: average, name: "店日均" }].find(({ item }) => item.status === "below");
    const action = pattern === "010"
      ? "本周固定前后期都营业的同一批门店，重算店日均。"
      : pattern.includes("?") ? "补齐缺失目标、同日门店数或全月进度参考后再判断。"
        : jointStatus === "pass" ? "本周按同月、同档位和同口径复核三项达成率与各项差距。"
          : weak?.key === "gmv" ? "本周按同月同档位把GMV差距拆到区域，并核对全月来源是否完整。"
            : weak?.key === "stores" ? "本周核对月内正GMV且状态1/2的去重门店清单及统计日。"
              : "本周按同一期间复核GMV、营业店天与店日均的对应关系。";
    const falsifier = jointStatus === "pass"
      ? "同口径复核后，达标数量降到2项以下，当前组合通过结论即被推翻。"
      : pattern === "010"
      ? "固定同一批门店后，店日均上涨幅度仍不低于全体门店上涨幅度。"
      : jointStatus === "unknown" ? "按当前期间补齐未知项后，若确认达标数至少2项，待判定状态即被推翻。"
        : "同口径复核后，若达标数量达到2项，当前组合预警即被推翻。";
    const known = [gmv, average, stores].map((item) => item.status === "unknown" ? null : item.status === "met");
    const ratios = [gmv, average, stores].map((item) => item.attainment === null ? null : Math.round(item.attainment * 1000) / 10);
    return {
      pattern, known, ratios, jointStatus, metCount, required: 2, satisfiedPairs: pairs,
      basis: gmv.basis,
      summary: diagnosisText(assessment), action, falsifier,
      quality: assessment.quality,
    };
  }
  return Object.freeze({ assess, diagnose, diagnosisText, monthEnd });
});
