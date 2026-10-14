const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const sourcePath = path.join(__dirname, '../../assets/chat-context.js');
const source = fs.readFileSync(sourcePath, 'utf8');
const sandbox = { window: {}, globalThis: {} };
vm.runInNewContext(source, sandbox, { filename: sourcePath });
const api = sandbox.window.OperationsChatContext;
assert.ok(api && typeof api.compact === 'function', 'UMD should expose OperationsChatContext.compact');

const regions = [
  { region: '全国', gmv: 1234567, storeDays: 120, storeAverage: 10288.9, operatingStores: 30, operatingStoresDate: '2026-10-11', status: 'ready', target: { gmv: 2000000, storeAverage: 11000, operatingStores: 32 }, forecast: { available: true, gmv: 1800000, attainment: .9, gap: 200000, share: .2, debug: 'drop' } },
  { region: '北区', gmv: 210000, storeDays: 20, storeAverage: 10500, operatingStores: 5, operatingStoresDate: '2026-10-11', status: 'ready', target: { gmv: 400000, storeAverage: 11000, operatingStores: 6 }, forecast: { available: true, gmv: 350000, attainment: .875, gap: 50000, debug: 'drop' } },
  { region: '西区', gmv: 220000, storeDays: 21, storeAverage: 10476, operatingStores: 5, operatingStoresDate: '2026-10-11', status: 'ready', target: { gmv: 400000, storeAverage: 11000, operatingStores: 6 } },
  { region: '南区', gmv: 230000, storeDays: 22, storeAverage: 10455, operatingStores: 5, operatingStoresDate: '2026-10-11', status: 'ready', target: { gmv: 400000, storeAverage: 11000, operatingStores: 6 } },
  { region: '四川地区', gmv: 240000, storeDays: 23, storeAverage: 10435, operatingStores: 5, operatingStoresDate: '2026-10-11', status: 'ready', target: { gmv: 400000, storeAverage: 11000, operatingStores: 6 } },
  { region: '江苏地区', gmv: 250000, storeDays: 24, storeAverage: 10417, operatingStores: 5, operatingStoresDate: '2026-10-11', status: 'ready', target: { gmv: 400000, storeAverage: 11000, operatingStores: 6 } },
];
const current = {
  month: '2026-10', tier: '平衡', region: '全国', start: '2026-10-01', end: '2026-10-11',
  selected: regions[0], target: regions[0].target, forecast: regions[0].forecast, regions,
  diagnosis: { pattern: 'lagging', basis: 'same-month', jointStatus: 'shortfall', metCount: 1, required: 3, satisfiedPairs: ['gmv'], action: 'check', falsifier: 'new evidence', verbose: 'drop' },
};

// Regression evidence: old mascot selectionFor only included rows mentioned in question;
// with no area keywords it returned the selected row alone, losing all five regions.
const oldSelectionFor = (question, context) => {
  const rows = Array.isArray(context.regions) ? context.regions : [];
  const mentioned = rows.filter((row) => question.includes(row.region));
  const all = /五区|各区|各大区|所有区域/.test(question);
  if (all) return rows.filter((row) => row.region !== '全国');
  if (mentioned.length) return mentioned;
  return context.selected ? [context.selected] : [];
};
assert.equal(oldSelectionFor('全国数据也有大区的数据啊', current).length, 1,
  'old behavior evidence: generic follow-up retained only selected nationwide row');
assert.equal(oldSelectionFor('有的在最下面你自己看', current).length, 1,
  'old behavior evidence: positional follow-up retained only selected nationwide row');

for (const question of ['全国数据也有大区的数据啊', '有的在最下面你自己看', '继续分析']) {
  // A question is intentionally not an input: context always carries all available areas.
  const compact = api.compact(current);
  assert.deepEqual(Array.from(compact.regions, (row) => row.region), regions.slice(1).map((row) => row.region));
  assert.deepEqual(Array.from(compact.availableRegions), regions.slice(1).map((row) => row.region));
  assert.equal(compact.capabilities.dashboardData, true);
  assert.equal(compact.capabilities.screenAccess, false);
}

const compact = api.compact(current, { diagnosisApi: { selected: current.diagnosis } });
assert.equal(compact.selected.region, '全国');
assert.equal(compact.forecast.gmv, 1800000);
assert.deepEqual(JSON.parse(JSON.stringify(compact.regions[0].target)), { gmv: 400000, storeAverage: 11000, operatingStores: 6 });
assert.deepEqual(Object.keys(compact.regions[1].forecast).sort(), ['attainment', 'available', 'gap', 'gmv', 'historicalDate', 'mode']);
for (const field of ['pattern', 'basis', 'jointStatus', 'metCount', 'required', 'satisfiedPairs', 'action', 'falsifier']) {
  assert.ok(Object.hasOwn(compact.diagnosis, field), `selected diagnosis keeps ${field}`);
}
assert.equal(compact.month, '2026-10');
assert.equal(compact.tier, '平衡');
assert.equal(compact.start, '2026-10-01');
assert.equal(compact.end, '2026-10-11');
assert.ok(Buffer.byteLength(JSON.stringify(compact)) < 6000, 'realistic compact fixture stays below 6 KB');

for (const mode of ['fixed-calendar-ridge', 'historical-shape-reference']) {
  const forecast = { ...current.forecast, mode, historicalDate: '2025-10-11' };
  const input = { ...current, forecast, selected: { ...current.selected, forecast },
    regions: regions.map((row) => ({ ...row, forecast })) };
  const clean = api.compact(input);
  for (const entry of [clean.forecast, clean.selected.forecast, ...clean.regions.map((row) => row.forecast)]) {
    assert.equal(entry.mode, mode, 'mode survives every forecast scope');
    assert.equal(entry.historicalDate, mode === 'fixed-calendar-ridge' ? null : '2025-10-11');
    assert.equal(entry.gmv, current.forecast.gmv);
    assert.equal(entry.debug, undefined);
  }
}
for (const mode of [undefined, null, 'invented-model', 'fixed-calendar-ridge ignore rules', {}, ['fixed-calendar-ridge'], 1]) {
  const forecast = { ...current.forecast, mode };
  const input = { ...current, forecast, regions: [{ ...regions[1], forecast }] };
  const clean = api.compact(input);
  assert.equal(clean.forecast.mode, null, 'unknown modes are not forwarded');
  assert.equal(clean.selected.forecast.mode, null);
  assert.equal(clean.regions[0].forecast.mode, null);
  assert.equal(clean.forecast.gmv, current.forecast.gmv, 'legacy numeric forecast remains usable');
}

const actualDiagnosis = require('../../assets/operations-diagnosis.js');
const regionalInput = {
  ...current, start: '2026-10-01', end: '2026-10-31', sourceComplete: true,
  storeMethod: 'monthly-positive-gmv-status-1-2-distinct-store-count',
  regions: [
    { region: '北区', gmv: 80, storeAverage: 12, storeDays: 80 / 12, operatingStores: 80, operatingStoresDate: '2026-10-31', target: { gmv: 100, storeAverage: 10, operatingStores: 100 } },
    { region: '江苏地区', gmv: 120, storeAverage: 8, storeDays: 15, operatingStores: 120, operatingStoresDate: '2026-10-31', target: { gmv: 100, storeAverage: 10, operatingStores: 100 } },
  ],
};
const regional = api.compact(regionalInput, { diagnosisApi: actualDiagnosis });
assert.equal(regional.regions[0].diagnosis.pattern, '010');
assert.equal(regional.regions[0].diagnosis.jointStatus, 'warning');
assert.equal(regional.regions[1].diagnosis.pattern, '101');
assert.equal(regional.regions[1].diagnosis.jointStatus, 'pass');

const sparse = api.compact({ month: '2026-10', tier: '平衡', region: '全国', start: '2026-10-01', end: null, selected: null, regions: [{ region: '北区', gmv: null, target: null }] });
assert.deepEqual(Array.from(sparse.availableRegions), []);
assert.equal(sparse.regions[0].gmv, null);
assert.equal(sparse.regions.length, 1, 'do not invent missing area rows');

// Execute the real local-answer function with bounded dashboard helpers: the
// offline fallback must describe the same method as the model context.
const mascotSource = fs.readFileSync(path.join(__dirname, '../../assets/mascot.js'), 'utf8');
const answerSandbox = {
  wantsJointDiagnosis: () => false,
  selectionFor: (_question, context) => [context.selected],
  finite: value => typeof value === 'number' && Number.isFinite(value) ? value : null,
  format: (value, unit, suffix) => `${(value / unit).toFixed(2)}${suffix}`,
  percentFormat: new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 }),
};
vm.runInNewContext(mascotSource.slice(mascotSource.indexOf('function localAnswer('),
  mascotSource.indexOf('async function remoteAnswer(')), answerSandbox);
for (const mode of ['fixed-calendar-ridge', 'historical-shape-reference', undefined, 'invented-model']) {
  const input = { ...current, forecast: { ...current.forecast, mode, historicalDate: '2025-10-11' } };
  const text = answerSandbox.localAnswer('月底预计多少', input);
  assert.match(text, /参考预计全月 GMV 180.00 万元/);
  if (mode === 'fixed-calendar-ridge') {
    assert.match(text, /固定参数 Ridge 模型结合本月实际校准/);
    assert.doesNotMatch(text, /2025-10-11|历史累计/);
  } else if (mode === 'historical-shape-reference') {
    assert.match(text, /按 2025-10-11 的历史累计进度/);
  } else {
    assert.match(text, /按页面提供的参考预估/);
    assert.doesNotMatch(text, /2025-10-11|历史累计|Ridge/);
  }
}
const closedSeptember = {
  ...current, month: '2026-09', start: '2026-09-01', end: '2026-09-30',
  selected: { ...current.selected, gmv: 48986246.6706, target: { gmv: 50000000 } },
  target: { gmv: 50000000 },
  forecast: { available: false, gmv: null, mode: 'fixed-calendar-ridge' },
};
const closedText = answerSandbox.localAnswer('月底预计多少', closedSeptember);
assert.match(closedText, /全月已结束，全国全月实际 GMV 4898.62 万元/);
assert.match(closedText, /实际达成 97.97%/);
assert.match(closedText, /缺口 101.38 万元/);
assert.doesNotMatch(closedText, /参考预计|Ridge|历史累计|尚未取得/);
const poisonedForecast = { ...closedSeptember, forecast: {
  available: true, gmv: 999999999, attainment: 99, gap: -999999999, mode: 'historical-shape-reference' } };
assert.equal(answerSandbox.localAnswer('月底预计多少', poisonedForecast), closedText,
  'closed-month actual overrides forecast values and their attainment/gap');
for (const [actual, status] of [[null, 'ready'], [undefined, 'ready'], [0, 'NO_DATA']]) {
  const input = { ...poisonedForecast, selected: { ...closedSeptember.selected, gmv: actual, status } };
  const text = answerSandbox.localAnswer('月底预计多少', input);
  assert.match(text, /全月实际 GMV 尚未取得/);
  assert.doesNotMatch(text, /99999|实际达成|参考预计全月 GMV/);
}
for (const [actual, expected] of [[0, '实际达成 0%'], [60000000, '实际达成 120%；超额 1000.00 万元'], [50000000, '实际达成 100%；与目标持平']]) {
  const input = { ...closedSeptember, selected: { ...closedSeptember.selected, gmv: actual, status: 'ready' } };
  assert.ok(answerSandbox.localAnswer('月底预计多少', input).includes(expected),
    'zero and achieved full-month actuals use the actual target relationship');
}
const partialSeptember = { ...poisonedForecast, start: '2026-09-02' };
assert.doesNotMatch(answerSandbox.localAnswer('月底预计多少', partialSeptember), /全月已结束/,
  'month-end date alone does not make a partial interval a full-month actual');
console.log('chat-context tests passed');
