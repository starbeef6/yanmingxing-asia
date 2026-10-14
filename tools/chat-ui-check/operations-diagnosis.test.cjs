const assert = require("node:assert/strict");
const diagnosis = require("../../assets/operations-diagnosis.js");

const month = "2026-10";
const target = { gmv: 100, storeAverage: 10, operatingStores: 100 };
function context(pattern = "111", overrides = {}) {
  const values = pattern.split("").map((bit) => bit === "1" ? 1.2 : 0.8);
  const gmv = 100 * values[0];
  const average = 10 * values[1];
  const row = {
    region: "北区", status: "OK", gmv, storeDays: gmv / average, storeAverage: average,
    operatingStores: 100 * values[2], operatingStoresDate: `${month}-31`, target: { ...target },
  };
  return {
    month, start: `${month}-01`, end: `${month}-31`, region: "北区", selected: row, target: { ...target },
    loading: false, failure: null, sourceComplete: true,
    storeMethod: "monthly-positive-gmv-status-1-2-distinct-store-count",
    forecast: { available: false }, ...overrides,
  };
}

for (const pattern of ["000", "001", "010", "011", "100", "101", "110", "111"]) {
  const result = diagnosis.diagnose(context(pattern));
  assert.equal(result.pattern, pattern, `pattern ${pattern}`);
  assert.equal(result.known.filter((value) => value !== null).length, 3, `all three values known for ${pattern}`);
  const metCount = [...pattern].filter((bit) => bit === "1").length;
  assert.equal(result.metCount, metCount, `met count ${pattern}`);
  assert.equal(result.required, 2, `2-of-3 threshold ${pattern}`);
  assert.equal(result.jointStatus, metCount >= 2 ? "pass" : "warning", `joint result ${pattern}`);
  assert.equal(result.satisfiedPairs.length, metCount < 2 ? 0 : metCount * (metCount - 1) / 2, `achieved pairs ${pattern}`);
  if (metCount >= 2) assert.match(result.summary, /组合通过/);
  else assert.match(result.summary, /预警/);
  assert.doesNotMatch(result.summary, /不能抵消第三项/);
  if (pattern === "110") assert.match(result.summary, /在营门店实际80家、目标100家，距目标差20家/);
  if (pattern === "111") assert.equal(result.falsifier, "同口径复核后，达标数量降到2项以下，当前组合通过结论即被推翻。");
  assert.ok(result.summary.length > 20);
}

const oneMetTwoUnknown = context("100");
oneMetTwoUnknown.selected.target.storeAverage = 0;
oneMetTwoUnknown.selected.target.operatingStores = null;
assert.equal(diagnosis.diagnose(oneMetTwoUnknown).jointStatus, "unknown");

const zeroMetOneUnknown = context("000");
zeroMetOneUnknown.selected.target.operatingStores = null;
assert.equal(diagnosis.diagnose(zeroMetOneUnknown).jointStatus, "warning");

const missing = context("111");
missing.selected.target.storeAverage = null;
const missingResult = diagnosis.diagnose(missing);
assert.equal(missingResult.pattern, "1?1");
assert.equal(missingResult.jointStatus, "pass");
assert.match(missingResult.summary, /组合通过/);

const zeroTarget = context("111");
zeroTarget.selected.target.gmv = 0;
assert.equal(diagnosis.diagnose(zeroTarget).pattern, "?11");

const incomplete = diagnosis.diagnose(context("100", { sourceComplete: false }));
assert.match(incomplete.summary, /^按当前快照，待核对/);
assert.equal(incomplete.pattern, "100");
assert.match(diagnosis.diagnose(context("100", { loading: true })).summary, /^按当前快照，待核对/);
assert.match(diagnosis.diagnose(context("100", { failure: "刷新失败" })).summary, /^按当前快照，待核对/);

const outOfSync = context("111");
outOfSync.selected.operatingStoresDate = "2026-10-30";
assert.equal(diagnosis.diagnose(outOfSync).pattern, "11?");

const wrongStoreDefinition = context("111", { storeMethod: "cutoff-day-positive-gmv-store-count" });
assert.equal(diagnosis.diagnose(wrongStoreDefinition).pattern, "11?");

const inconsistent = context("111");
inconsistent.selected.storeDays += 1;
assert.equal(diagnosis.diagnose(inconsistent).pattern, "1?1");

const midMonth = context("000", { end: "2026-10-12", forecast: { available: false } });
const unknownForecast = diagnosis.diagnose(midMonth);
assert.equal(unknownForecast.metrics, undefined);
assert.equal(unknownForecast.pattern, "?0?");
assert.match(unknownForecast.summary, /待判定/);

const forecastContext = context("000", {
  end: "2026-10-12", forecast: { available: true, gmv: 120, historicalDate: "2025-10-12" },
});
const progress = diagnosis.diagnose(forecastContext);
assert.equal(progress.pattern, "10?");
assert.equal(progress.basis, "forecast");
assert.match(progress.summary, /参考预计/);
assert.match(progress.summary, /按页面提供的参考预计/);
assert.doesNotMatch(progress.summary, /历史进度/);

const ridgeContext = { ...forecastContext,
  forecast: { ...forecastContext.forecast, mode: 'fixed-calendar-ridge' } };
const ridgeAssessment = diagnosis.assess(ridgeContext);
const ridgeDiagnosis = diagnosis.diagnose(ridgeContext);
assert.equal(ridgeDiagnosis.pattern, progress.pattern, 'method metadata does not change 2-of-3 logic');
assert.equal(ridgeAssessment.metrics.gmv.forecastMode, 'fixed-calendar-ridge');
assert.equal(ridgeAssessment.metrics.gmv.referenceDate, null, 'Ridge does not adopt a stale historical date');
assert.match(ridgeDiagnosis.summary, /固定参数 Ridge 模型结合本月实际校准/);
assert.doesNotMatch(ridgeDiagnosis.summary, /历史进度|2025-10/);

const historyContext = { ...forecastContext,
  forecast: { ...forecastContext.forecast, mode: 'historical-shape-reference' } };
assert.match(diagnosis.diagnose(historyContext).summary, /按历史进度参考预计/);
assert.equal(diagnosis.assess(historyContext).metrics.gmv.referenceDate, '2025-10-12');
for (const mode of ['unknown-model', null, {}, ['fixed-calendar-ridge']]) {
  const unknown = { ...forecastContext, forecast: { ...forecastContext.forecast, mode } };
  const assessed = diagnosis.assess(unknown);
  assert.equal(assessed.metrics.gmv.forecastMode, null);
  assert.equal(assessed.metrics.gmv.referenceDate, null);
  assert.match(diagnosis.diagnose(unknown).summary, /按页面提供的参考预计/);
  assert.doesNotMatch(diagnosis.diagnose(unknown).summary, /历史进度|固定参数 Ridge/);
}

const otherRegionRow = { ...ridgeContext.selected, region: '西区',
  forecast: { ...forecastContext.forecast, mode: 'historical-shape-reference' } };
assert.match(diagnosis.diagnose(ridgeContext, otherRegionRow).summary, /按历史进度参考预计/,
  'regional diagnosis uses the row method instead of national/current method');

const scalePattern = diagnosis.diagnose(context("010"));
assert.equal(scalePattern.action, "本周固定前后期都营业的同一批门店，重算店日均。");
assert.equal(scalePattern.falsifier, "固定同一批门店后，店日均上涨幅度仍不低于全体门店上涨幅度。");

const partialMonthButMonthEnd = context("111", { start: "2026-10-02" });
assert.equal(diagnosis.diagnose(partialMonthButMonthEnd).pattern, "?11");

console.log("operations joint diagnosis checks passed");
