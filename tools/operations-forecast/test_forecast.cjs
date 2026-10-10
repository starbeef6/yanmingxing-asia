"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = path.resolve(__dirname, "../..");
const Forecast = require(path.join(root, "assets/operations-forecast.js"));
const read = (name) => JSON.parse(fs.readFileSync(path.join(root, "assets", name), "utf8"));
const history = read("operations-history-2025.json");
const actual = read("operations-data/2026-10.json");
const targets = read("operations-targets.json");
const clone = (value) => JSON.parse(JSON.stringify(value));
const near = (a, b) => assert.ok(Math.abs(a - b) <= Math.max(1, Math.abs(a), Math.abs(b)) * 1e-10, `${a} != ${b}`);
let count = 0;
function test(name, action) {
  try { action(); count += 1; }
  catch (error) { console.error(`FAIL ${name}`); throw error; }
}
const forecast = (data = actual, curve = history, region = "全国", target = { gmv: 60566300 }) =>
  Forecast.calculate(data, curve, region, target);
const invalid = (result) => {
  assert.equal(result.available, false);
  ["gmv", "attainment", "gap", "share", "historicalDate", "cutoff"].forEach((key) => assert.equal(result[key], null));
  assert.equal(typeof result.reason, "string");
  assert.equal(result.mode, "historical-shape-reference");
};
const mutateHistory = (action) => { const value = clone(history); action(value); return value; };
const mutateActual = (action) => { const value = clone(actual); action(value); return value; };

test("真实历史参考通过严格校验并保留来源", () => {
  const value = Forecast.validateHistory(history);
  assert.notEqual(value, history);
  assert.equal(value.status, "reference_unreconciled");
  assert.equal(value.source.sha256, history.source.sha256);
  assert.deepEqual(value.regions, Forecast.REGIONS);
});

test("浏览器和 Node 使用同一纯计算 API", () => {
  const sandbox = { window: {} };
  vm.runInNewContext(fs.readFileSync(path.join(root, "assets/operations-forecast.js"), "utf8"), sandbox);
  assert.equal(typeof sandbox.window.OperationsForecast.calculate, "function");
  near(sandbox.window.OperationsForecast.calculate(actual, history, "全国", { gmv: 60566300 }).gmv, forecast().gmv);
});

test("真实生产快照按实际 end 取同月同日，未以今天或取数时间替代", () => {
  const day = Number(actual.end.slice(8, 10));
  const selected = actual.rows.find((row) => row.region === "北区");
  const result = forecast(actual, history, "北区");
  assert.equal(result.available, true);
  near(result.share, history.months["10"].curves["北区"][day - 1]);
  near(result.gmv, selected.gmv / result.share);
  assert.equal(result.historicalDate, `2025-10-${actual.end.slice(8, 10)}`);
  assert.equal(result.cutoff, actual.end);
  const differentTimestamp = mutateActual((value) => { value.fetchedAt = "2099-12-31T23:59:59Z"; });
  assert.deepEqual(forecast(differentTimestamp), forecast());
});

const productionDay9 = {
  month: "2026-10", start: "2026-10-01", end: "2026-10-09", status: "OK", sourceComplete: false,
  rows: [
    { region: "全国", gmv: 14750140.35, storeDays: 7862, status: "OK" },
    { region: "北区", gmv: 3168643.76, storeDays: 1866, status: "OK" },
    { region: "西区", gmv: 4442032.09, storeDays: 2185, status: "OK" },
    { region: "南区", gmv: 3930893.35, storeDays: 2199, status: "OK" },
    { region: "四川地区", gmv: 2081026.38, storeDays: 927, status: "OK" },
    { region: "江苏地区", gmv: 1127544.77, storeDays: 685, status: "OK" },
  ],
};
test("已核验 10 月 9 日占比，避免第 8 或第 10 日错位", () => {
  const result = forecast(productionDay9, history, "北区");
  near(result.share, 0.2840301115080114);
  near(result.gmv, 3168643.76 / 0.2840301115080114);
  assert.equal(result.historicalDate, "2025-10-09");
});

test("全国逐区预测后相加，不平均五区占比", () => {
  const result = forecast(productionDay9);
  const shares = [0.2840301115080114, 0.3004821892964482, 0.2817908167303215, 0.2996649301465558, 0.27424536465073523];
  const expected = productionDay9.rows.slice(1).reduce((sum, row, index) => sum + row.gmv / shares[index], 0);
  near(result.gmv, expected);
  assert.equal(result.share, null);
  const incorrect = productionDay9.rows[0].gmv / (shares.reduce((sum, value) => sum + value, 0) / shares.length);
  assert.ok(Math.abs(result.gmv - incorrect) > 1000);
});

test("保底、平衡、冲刺只改变目标比较，不改变参考预计 GMV", () => {
  ["全国", ...Forecast.REGIONS].forEach((region) => {
    const results = ["保底", "平衡", "冲刺"].map((tier) => forecast(productionDay9, history, region, targets.months["2026-10"][tier][region]));
    assert.ok(results.every((result) => result.available));
    assert.equal(results[0].gmv, results[1].gmv);
    assert.equal(results[1].gmv, results[2].gmv);
    assert.ok(results[0].attainment > results[1].attainment && results[1].attainment > results[2].attainment);
    assert.ok(results[0].gap < results[1].gap && results[1].gap < results[2].gap);
  });
});

test("缺口保持符号，达标后的超额不被截成零", () => {
  const low = forecast(productionDay9, history, "全国", { gmv: 1 });
  assert.ok(low.gap < 0);
  const high = forecast(productionDay9, history, "全国", { gmv: 1e9 });
  assert.ok(high.gap > 0);
  near(low.gap, 1 - low.gmv);
});

test("月末占比 100% 时全国与各区域预计等于实际", () => {
  const value = clone(productionDay9);
  value.end = "2026-10-31";
  value.rows.forEach((row) => {
    const result = forecast(value, history, row.region);
    assert.equal(result.available, true);
    near(result.gmv, row.gmv);
  });
});

["11", "12"].forEach((month) => test(`${month} 月采用各自曲线及日数，不复用十月`, () => {
  const value = clone(productionDay9);
  value.month = `2026-${month}`;
  value.start = `2026-${month}-01`;
  value.end = `2026-${month}-09`;
  const result = forecast(value, history, "西区");
  assert.equal(result.available, true);
  near(result.share, history.months[month].curves["西区"][8]);
  assert.equal(result.historicalDate, `2025-${month}-09`);
}));

test("正常零营业额可预计为零，仍不修改实际记录", () => {
  const value = clone(productionDay9);
  value.rows.forEach((row) => { row.gmv = 0; row.storeDays = 0; });
  const result = forecast(value);
  assert.equal(result.available, true);
  assert.equal(result.gmv, 0);
  assert.equal(result.attainment, 0);
  assert.equal(result.gap, 60566300);
});

test("完整输入不会被模型修改，来源不会写回实际 snapshot", () => {
  const before = JSON.stringify({ actual, history, targets });
  forecast(actual, history, "全国", targets.months["2026-10"]["平衡"]["全国"]);
  Forecast.validateHistory(history);
  assert.equal(JSON.stringify({ actual, history, targets }), before);
});

[undefined, {}, { gmv: 0 }, { gmv: -1 }, { gmv: "60566300" }, { gmv: NaN }, { gmv: Number.MIN_VALUE }].forEach((target, index) =>
  test(`目标未就绪或无效 ${index} 不阻断预计 GMV`, () => {
    const result = Forecast.calculate(productionDay9, history, "全国", target);
    assert.equal(result.available, true);
    assert.equal(result.attainment, null);
    assert.equal(result.gap, null);
  }));

[
  ["版本错误", (value) => { value.schemaVersion = 2; }],
  ["年份错误", (value) => { value.referenceYear = 2024; }],
  ["来源状态错误", (value) => { value.status = "verified"; }],
  ["区域重复", (value) => { value.regions[1] = value.regions[0]; }],
  ["区域缺失", (value) => { value.regions.pop(); }],
  ["月份缺失", (value) => { delete value.months["11"]; }],
  ["额外月份", (value) => { value.months["09"] = value.months["10"]; }],
  ["来源校验值缺失", (value) => { delete value.source.sha256; }],
  ["来源表错误", (value) => { value.source.table = "report.dws_data_comprehensive"; }],
  ["占比单位错误", (value) => { value.ratioUnit = "percent"; }],
  ["日期索引错误", (value) => { value.dayIndex = "dayOfMonth"; }],
  ["月天数错误", (value) => { value.months["11"].days = 31; }],
  ["历史月份错误", (value) => { value.months["10"].historicalMonth = "2025-11"; }],
  ["日缺失", (value) => { value.months["10"].curves["北区"].pop(); }],
  ["曲线缺区域", (value) => { delete value.months["10"].curves["江苏地区"]; }],
  ["额外全国曲线", (value) => { value.months["10"].curves["全国"] = value.months["10"].curves["北区"]; }],
  ["占比零分母", (value) => { value.months["10"].curves["北区"][0] = 0; }],
  ["占比非有限", (value) => { value.months["10"].curves["北区"][0] = Infinity; }],
  ["占比字符串", (value) => { value.months["10"].curves["北区"][0] = "0.1"; }],
  ["占比超过一", (value) => { value.months["10"].curves["北区"][30] = 1.001; }],
  ["累计倒退", (value) => { value.months["10"].curves["北区"][8] = 0.1; }],
  ["月末非一", (value) => { value.months["10"].curves["北区"][30] = 0.999; }],
].forEach(([name, action]) => test(`历史校验拒绝${name}，实际模块仍可独立显示`, () => {
  const value = mutateHistory(action);
  assert.throws(() => Forecast.validateHistory(value));
  invalid(forecast(actual, value));
  assert.ok(actual.rows.every((row) => Number.isFinite(row.gmv)));
}));

test("稀疏曲线的空洞日期不可绕过数组校验", () => {
  const value = clone(history);
  delete value.months["10"].curves["北区"][8];
  assert.throws(() => Forecast.validateHistory(value));
});

test("月末轻微浮点误差可接受并规范到一，不修改原始占比", () => {
  const value = clone(history);
  value.months["10"].curves["北区"][30] = 1 + 5e-10;
  const validated = Forecast.validateHistory(value);
  assert.equal(validated.months["10"].curves["北区"][30], 1);
  assert.equal(value.months["10"].curves["北区"][30], 1 + 5e-10);
});

[
  ["非月初累计", (value) => { value.start = "2026-10-02"; }],
  ["实际截止缺失", (value) => { value.end = null; }],
  ["截止日超出日历", (value) => { value.end = "2026-10-32"; }],
  ["截止日跨月", (value) => { value.end = "2026-11-01"; }],
  ["状态无实际", (value) => { value.status = "NO_DATA"; }],
  ["全国缺失", (value) => { value.rows.shift(); }],
  ["单区缺失", (value) => { value.rows.pop(); }],
  ["单区重复", (value) => { value.rows[5] = clone(value.rows[4]); }],
  ["GMV 非数字", (value) => { value.rows[1].gmv = String(value.rows[1].gmv); }],
  ["GMV 非有限", (value) => { value.rows[1].gmv = NaN; }],
  ["GMV 负数", (value) => { value.rows[1].gmv = -1; }],
  ["单区无实际", (value) => { value.rows[1].status = "NO_DATA"; }],
  ["全国合计失配", (value) => { value.rows[0].gmv += 1; }],
  ["店天缺失", (value) => { delete value.rows[2].storeDays; }],
  ["店天非整数", (value) => { value.rows[2].storeDays = 1.5; }],
  ["店天合计失配", (value) => { value.rows[0].storeDays += 1; }],
  ["正营业额零店天", (value) => { value.rows[1].storeDays = 0; }],
].forEach(([name, action]) => test(`实际校验拒绝${name}，不产生部分全国预计`, () => {
  const value = mutateActual(action);
  invalid(forecast(value));
  invalid(forecast(value, history, "北区"));
}));

test("数据没有营业店天字段时，不凭空补充店天", () => {
  const value = clone(productionDay9);
  value.rows.forEach((row) => { delete row.storeDays; });
  assert.equal(forecast(value).available, true);
});

test("全国小浮点累计误差可接受，大额失配不可接受", () => {
  const small = clone(productionDay9);
  small.rows[0].gmv += 1e-6;
  assert.equal(forecast(small).available, true);
  const large = clone(productionDay9);
  large.rows[0].gmv += 0.1;
  invalid(forecast(large));
});

test("九月没有历史曲线，不偷用十月或按天平均外推", () => {
  const value = read("operations-data/2026-09.json");
  invalid(forecast(value));
  assert.ok(forecast(value).reason.includes("没有提供"));
});

["11", "12"].forEach((month) => test(`未来 ${month} 月未形成实际，只有目标也不预计`, () => {
  const noActual = clone(productionDay9);
  noActual.month = `2026-${month}`;
  noActual.start = `2026-${month}-01`;
  noActual.end = null;
  noActual.fetchedAt = null;
  noActual.status = "NO_DATA";
  noActual.rows.forEach((row) => {
    row.gmv = null;
    row.storeDays = null;
    row.status = "NO_DATA";
  });
  invalid(forecast(noActual));
}));

test("未知区域、缺失 history/data 都返回不可用，不抛出给 UI", () => {
  invalid(forecast(actual, history, "上海"));
  invalid(forecast(null));
  invalid(forecast(actual, null));
});

test("极小有效占比导致溢出时不返回 Infinity", () => {
  const value = clone(history);
  value.months["10"].curves["北区"][0] = Number.MIN_VALUE;
  const data = clone(productionDay9);
  data.end = "2026-10-01";
  invalid(forecast(data, value));
});

const readout = ["全国", ...Forecast.REGIONS].map((region) => {
  const result = forecast(productionDay9, history, region, targets.months["2026-10"]["平衡"][region]);
  return { region, cutoff: result.cutoff, historicalDate: result.historicalDate, share: result.share,
    forecastGmv: result.gmv, attainment: result.attainment, gap: result.gap };
});
console.log(`PASS ${count} historical forecast regression checks`);
console.log(JSON.stringify({ productionDay9: readout }, null, 2));
