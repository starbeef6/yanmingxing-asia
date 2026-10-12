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
assert.deepEqual(Object.keys(compact.regions[1].forecast).sort(), ['attainment', 'available', 'gap', 'gmv', 'historicalDate']);
for (const field of ['pattern', 'basis', 'jointStatus', 'metCount', 'required', 'satisfiedPairs', 'action', 'falsifier']) {
  assert.ok(Object.hasOwn(compact.diagnosis, field), `selected diagnosis keeps ${field}`);
}
assert.equal(compact.month, '2026-10');
assert.equal(compact.tier, '平衡');
assert.equal(compact.start, '2026-10-01');
assert.equal(compact.end, '2026-10-11');
assert.ok(Buffer.byteLength(JSON.stringify(compact)) < 6000, 'realistic compact fixture stays below 6 KB');

const sparse = api.compact({ month: '2026-10', tier: '平衡', region: '全国', start: '2026-10-01', end: null, selected: null, regions: [{ region: '北区', gmv: null, target: null }] });
assert.deepEqual(Array.from(sparse.availableRegions), []);
assert.equal(sparse.regions[0].gmv, null);
assert.equal(sparse.regions.length, 1, 'do not invent missing area rows');
console.log('chat-context tests passed');
