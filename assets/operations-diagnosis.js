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
  function metric(actual, target) {
    if (!finite(actual) || !finite(target) || target <= 0) return { status: "unknown", attainment: null };
    const attainment = actual / target;
    return Number.isFinite(attainment) ? { status: attainment >= 1 ? "met" : "below", attainment }
      : { status: "unknown", attainment: null };
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
        ? { ...metric(forecast.gmv, target.gmv), basis: "forecast", referenceDate: forecast.historicalDate || null }
        : { status: "unknown", attainment: null, basis: "unknown" };
    } else gmv = { status: "unknown", attainment: null, basis: "unknown" };
    const storeDateMatches = !!actualEnd && selected?.operatingStoresDate === actualEnd;
    const monthlyDefinition = context?.storeMethod === "monthly-positive-gmv-status-1-2-distinct-store-count";
    const stores = monthlyDefinition && storeDateMatches
      ? metric(selected?.operatingStores, target.operatingStores)
      : { status: "unknown", attainment: null };
    let averageMetric = average;
    const quality = [];
    if (finite(selected?.gmv) && finite(selected?.storeAverage) && finite(selected?.storeDays) && selected.storeDays > 0) {
      const reconciledGmv = selected.storeAverage * selected.storeDays;
      const tolerance = Math.max(0.01, Math.abs(selected.gmv) * 1e-8);
      if (Math.abs(reconciledGmv - selected.gmv) > tolerance) {
        averageMetric = { status: "unknown", attainment: null };
        quality.push("GMV与店日均乘营业店天未能对上，相关组合判断暂停核对");
      }
    }
    const sourceOk = context?.sourceComplete === true;
    if (context?.loading) quality.push("页面正在刷新");
    if (context?.failure) quality.push("刷新未成功，沿用当前快照");
    if (context?.sourceComplete === false) quality.push("来源不完整，按当前快照待核对");
    else if (!sourceOk) quality.push("来源完整性尚未确认");
    if (!fullMonth && gmv.basis === "forecast") quality.push("GMV按历史进度参考预计，不代表全月实际");
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
    const known = entries.filter(([, item]) => item.status !== "unknown");
    const met = known.filter(([, item]) => item.status === "met").map(([key]) => key);
    const below = known.filter(([, item]) => item.status === "below").map(([key]) => key);
    const states = (keys) => keys.map((key) => labels[key]).join("、");
    const stateLabel = (key, item) => item.status === "unknown" ? `${labels[key]}待核对`
      : key === "gmv" && item.basis === "forecast"
        ? `GMV进度参考${item.status === "met" ? "达到" : "低于"}目标线`
        : `${labels[key]}${item.status === "met" ? "达标" : "未达标"}`;
    let text;
    if (known.length < 3) {
      const knownLine = known.length ? `当前可比结果：${known.map(([key, item]) => stateLabel(key, item)).join("，")}。`
        : "三项暂时都没有可比的完整值。";
      text = `${knownLine}其余指标尚不能判定，先补齐相应目标、来源或同日数据再看。`;
    } else if (met.length === 3) {
      text = gmv.basis === "forecast"
        ? "店日均与在营门店达到当前档位目标，GMV进度参考也达到目标线；GMV全月实际仍未确认，三项进度达标不代表利润或现金流一定达标。"
        : "GMV、店日均、在营门店三项均达到当前档位目标；这表示当前三项指标达标，不代表利润或现金流一定达标。";
    } else if (met.length === 2) {
      const weak = below[0];
      const weakLabel = stateLabel(weak, entries.find(([key]) => key === weak)[1]);
      const metLabels = met.map((key) => stateLabel(key, entries.find(([name]) => name === key)[1]));
      text = `${metLabels.join("，")}；${weakLabel}是当前需关注项。两项达标只能降低相应风险，不能抵消第三项未达标或进度未到线。`;
    } else if (met.length === 1 && met[0] === "average" && below.includes("gmv") && below.includes("stores")) {
      text = `只有店日均达到目标，而${stateLabel("gmv", gmv)}、${stateLabel("stores", stores)}。门店规模收缩或低产门店退出可能抬高均值，这是待核实的解释，不能据此断言发生了闭店或外部经营环境变差。`;
    } else if (met.length === 1) {
      text = `只有${stateLabel(met[0], entries.find(([name]) => name === met[0])[1])}；${below.map((key) => stateLabel(key, entries.find(([name]) => name === key)[1])).join("，")}。先把未达标项作为本周核查重点，单项达标不足以证明整体经营健康。`;
    } else text = `${stateLabel("gmv", gmv)}，${stateLabel("average", average)}，${stateLabel("stores", stores)}。先核对各项数据和目标口径，再拆解差距；仅凭这三项不能归因于市场环境或具体经营动作。`;
    const g = assessment.metrics.gmv, a = assessment.metrics.average, n = assessment.metrics.stores;
    const attainment = [];
    if (g.attainment !== null) attainment.push(`GMV${pct(g.attainment)}${g.basis === "forecast" ? "（进度参考）" : ""}`);
    if (a.attainment !== null) attainment.push(`店日均${pct(a.attainment)}`);
    if (n.attainment !== null) attainment.push(`在营门店${pct(n.attainment)}`);
    if (attainment.length) text += ` 达成率：${attainment.join("，")}。`;
    if (g.basis === "forecast") text += "GMV判断仅为进度参考，不能当作全月实际结论。";
    if (!assessment.sourceOk || assessment.quality.some((item) => item.includes("刷新") || item.includes("来源不完整"))) text = `按当前快照，待核对。${text}`;
    if (assessment.quality.length) text += `\n${assessment.quality.join("；")}。`;
    return text;
  }
  function diagnose(context, row) {
    const assessment = assess(context, row);
    const { gmv, average, stores } = assessment.metrics;
    const pattern = [gmv, average, stores].map((item) => item.status === "met" ? "1" : item.status === "below" ? "0" : "?").join("");
    const met = [gmv, average, stores].filter((item) => item.status === "met").length;
    const weak = [{ key: "gmv", item: gmv, name: "GMV" }, { key: "stores", item: stores, name: "在营门店" }, { key: "average", item: average, name: "店日均" }].find(({ item }) => item.status === "below");
    const action = pattern === "010"
      ? "本周固定前后期都营业的同一批门店，重算店日均。"
      : pattern.includes("?") ? "补齐缺失目标、同日门店数或全月进度参考后再判断。"
        : met === 3 ? "本周按相同月份、档位和来源继续复核这三项指标。"
          : weak?.key === "gmv" ? "本周按同月同档位把GMV差距拆到区域，并核对全月来源是否完整。"
            : weak?.key === "stores" ? "本周核对月内正GMV且状态1/2的去重门店清单及统计日。"
              : "本周按同一期间复核GMV、营业店天与店日均的对应关系。";
    const falsifier = pattern === "010"
      ? "固定同一批门店后，店日均上涨幅度仍不低于全体门店上涨幅度。"
      : pattern.includes("?") ? "刷新后若三项均有同档位目标、完整来源和同日数据，可推翻当前未知判断。"
        : weak?.key === "gmv" ? "若完整月末实际GMV已达到所选档位目标，GMV薄弱项判断被推翻。"
          : weak?.key === "stores" ? "若同一统计日的合规去重门店数已达到目标，在营门店薄弱项判断被推翻。"
            : weak ? "若同一期间的店日均已达到目标，店日均薄弱项判断被推翻。"
              : "若重新核对后的任一达成率低于100%，三项均达标结论被推翻。";
    const known = [gmv, average, stores].map((item) => item.status === "unknown" ? null : item.status === "met");
    const ratios = [gmv, average, stores].map((item) => item.attainment === null ? null : Math.round(item.attainment * 1000) / 10);
    return {
      pattern, known, ratios,
      basis: gmv.basis,
      summary: diagnosisText(assessment), action, falsifier,
      quality: assessment.quality,
    };
  }
  return Object.freeze({ assess, diagnose, diagnosisText, monthEnd });
});
