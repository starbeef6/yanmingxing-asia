"use strict";

// Execute the actual dashboard script and event handlers with a bounded DOM and
// fake asset fetches. No browser, network, credentials or training data access.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = path.resolve(__dirname, "../..");
const source = fs.readFileSync(path.join(root, "assets/operations.js"), "utf8");
const html = fs.readFileSync(path.join(root, "operations/index.html"), "utf8");
const Ridge = require(path.join(root, "assets/operations-ridge.js"));
const History = require(path.join(root, "assets/operations-forecast.js"));
const read = (name) => JSON.parse(fs.readFileSync(path.join(root, "assets", name), "utf8"));
const model = read("operations-ridge-model.json");
const history = read("operations-history-2025.json");
const targets = read("operations-targets.json");
const clone = (v) => JSON.parse(JSON.stringify(v));
const near = (a, b) => assert.ok(Math.abs(a - b) <= Math.max(1, Math.abs(a), Math.abs(b)) * 1e-10, `${a} != ${b}`);
const format = (v, divisor = 1) => new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2, minimumFractionDigits: 2 }).format(v / divisor);
const settle = async () => { for (let i = 0; i < 8; i += 1) await new Promise((resolve) => setImmediate(resolve)); };

class Element {
  constructor(tag = "div") {
    this.tagName = tag; this.children = []; this.listeners = {}; this.dataset = {}; this.style = {};
    this.attributes = {}; this.hidden = false; this.value = ""; this.checked = false; this._text = "";
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  append(...nodes) {
    for (const node of nodes) {
      if (node.tagName === "fragment") this.append(...node.children);
      else { node.parent = this; this.children.push(node); }
    }
  }
  appendChild(node) { this.append(node); return node; }
  replaceChildren(...nodes) { this._text = ""; this.children = []; this.append(...nodes); }
  closest(tag) { let node = this; while (node) { if (node.tagName === tag) return node; node = node.parent; } return null; }
  addEventListener(name, listener) { (this.listeners[name] ||= []).push(listener); }
  async fire(name) {
    await Promise.all((this.listeners[name] || []).map((handler) => handler({ target: this })));
    await settle();
  }
}

function snapshot(month = "2026-10", day = 13) {
  const end = `${month}-${String(day).padStart(2, "0")}`;
  const rows = Ridge.REGIONS.map((region, i) => {
    const gmv = 12345.67 * (i + 1) + day * 17, storeDays = 100 * (i + 1);
    return { region, gmv, storeDays, storeAverage: gmv / storeDays, status: "OK",
             operatingStores: 5 + i, operatingStoresDate: end };
  });
  const sum = (key) => rows.reduce((value, row) => value + row[key], 0);
  rows.unshift({ region: "全国", gmv: sum("gmv"), storeDays: sum("storeDays"),
                 storeAverage: sum("gmv") / sum("storeDays"), status: "OK",
                 operatingStores: sum("operatingStores"), operatingStoresDate: end });
  return { month, start: `${month}-01`, end, fetchedAt: "2026-10-14T08:06:58Z", sourceComplete: false, status: "OK",
           source: { mode: "snapshot", queryStrategy: "full-period-requery", dataThrough: end, expectedThrough: end,
                     operatingStoresMethod: "monthly-positive-gmv-status-1-2-distinct-store-count" }, rows, targets: {} };
}

function emptySnapshot(month = "2026-11") {
  return { month, start: `${month}-01`, end: null, fetchedAt: null, sourceComplete: false,
           source: { mode: "snapshot", queryStrategy: "full-period-requery", dataThrough: null, expectedThrough: null },
           rows: ["全国", ...Ridge.REGIONS].map((region) => ({ region, gmv: null, storeDays: null, storeAverage: null, status: "NO_DATA" })), targets: {} };
}

function dashboard(options = {}) {
  const elements = new Map([...html.matchAll(/\bid="([^"]+)"/g)].map((m) => [m[1], new Element()]));
  const table = new Element("table"); table.append(elements.get("region-head"), elements.get("region-rows"));
  const tiers = ["保底", "平衡", "冲刺"].map((value) => Object.assign(new Element("input"), { value }));
  const document = {
    getElementById: (id) => { assert.ok(elements.has(id), `Unknown dashboard ID ${id}`); return elements.get(id); },
    querySelectorAll: (selector) => { assert.equal(selector, 'input[name="tier"]'); return tiers; },
    createElement: (tag) => new Element(tag), createDocumentFragment: () => new Element("fragment"),
  };
  const month = options.month || "2026-10";
  const fixture = options.snapshot || snapshot(month, month === "2026-09" ? 30 : 13);
  const responses = { snapshot: fixture, ridge: options.ridge === undefined ? model : options.ridge,
                      history: options.history === undefined ? history : options.history };
  const calls = [], contexts = [], timers = new Map();
  let timerId = 0;
  const window = {
    location: { search: `?month=${month}&region=${encodeURIComponent(options.region || "全国")}&tier=${encodeURIComponent(options.tier || "平衡")}`,
                href: `https://yanmingxing.asia/operations/?month=${month}` },
    history: { replaceState: (_, __, url) => { window.location.href = String(url); window.location.search = new URL(url).search; } },
    OperationsRidge: options.withoutRidge ? undefined : Ridge, OperationsForecast: History,
    dispatchEvent: (event) => { contexts.push(event.detail); },
  };
  const fetch = async (url, config) => {
    const parsed = new URL(url); calls.push({ url: parsed.href, path: parsed.pathname, config });
    let payload;
    if (parsed.pathname.endsWith("operations-ridge-model.json")) {
      if (options.modelGate) await options.modelGate;
      payload = responses.ridge;
    } else if (parsed.pathname.endsWith("operations-history-2025.json")) payload = responses.history;
    else if (parsed.pathname.endsWith("operations-targets.json")) payload = targets;
    else if (/operations-data\/.+\.json$/.test(parsed.pathname)) payload = responses.snapshot;
    else throw new Error(`Unexpected asset request ${url}`);
    if (payload instanceof Error) throw payload;
    return { ok: payload !== null, status: payload === null ? 404 : 200, json: async () => clone(payload) };
  };
  const sandbox = { window, document, fetch, Intl, URL, URLSearchParams, Date, AbortController,
    CustomEvent: class { constructor(type, opts) { this.type = type; this.detail = opts.detail; } },
    setTimeout: (fn, ms) => { const id = ++timerId; timers.set(id, { fn, ms }); return id; },
    clearTimeout: (id) => timers.delete(id), console,
  };
  vm.runInNewContext(source, sandbox, { filename: "operations.js" });
  return { elements, tiers, calls, contexts, table, responses, timers, window,
           context: () => window.OperationsContext.get(), element: (id) => elements.get(id) };
}

let count = 0;
async function test(name, action) {
  try { await action(); count += 1; } catch (error) { console.error(`FAIL ${name}`); throw error; }
}

(async () => {
  await test("loaded Ridge takes priority and renders matching national/five-region forecasts", async () => {
    const ui = dashboard(); await settle(); const data = ui.responses.snapshot;
    const context = ui.context(); assert.equal(context.forecast.mode, Ridge.MODE);
    near(context.forecast.gmv, Ridge.calculate(data, model, "全国").gmv);
    near(context.forecast.gmv, context.regions.filter((r) => r.region !== "全国").reduce((sum, r) => sum + r.forecast.gmv, 0));
    assert.ok(Object.isFrozen(context)); assert.ok(Object.isFrozen(context.selected.forecast));
    for (const region of ["全国", ...Ridge.REGIONS]) {
      ui.element("region").value = region; await ui.element("region").fire("change");
      const expected = Ridge.calculate(data, model, region, targets.months["2026-10"]["平衡"][region]);
      near(ui.context().forecast.gmv, expected.gmv);
      assert.equal(ui.element("forecast-value").textContent, format(expected.gmv, 10000));
      assert.equal(ui.element("gmv-value").textContent, format(data.rows.find((r) => r.region === region).gmv, 10000));
      assert.match(ui.element("forecast-note").textContent, /Ridge 固定模型/);
      assert.equal(ui.element("forecast-share-fact").hidden, region === "全国");
    }
    assert.equal(ui.table.dataset.forecast, "true");
    assert.equal(ui.element("region-rows").children.length, 5);
    assert.match(ui.element("region-head").textContent, /参考预计全月/);
    assert.match(ui.element("forecast-share-label").textContent, /模型累计进度/);
  });

  await test("target tier changes only target comparisons, not actual GMV or model forecast", async () => {
    const ui = dashboard(); await settle(); const original = ui.context().forecast.gmv;
    const actual = ui.context().selected.gmv; const targetValues = [];
    for (const tier of ui.tiers) {
      tier.checked = true; await tier.fire("change");
      assert.equal(ui.context().tier, tier.value); assert.equal(ui.context().selected.gmv, actual);
      near(ui.context().forecast.gmv, original);
      const target = targets.months["2026-10"][tier.value]["全国"];
      near(ui.context().forecast.attainment, original / target.gmv);
      near(ui.context().forecast.gap, target.gmv - original); targetValues.push(target.gmv);
    }
    assert.equal(new Set(targetValues).size, 3);
  });

  await test("refresh recalibrates updated actuals without refitting or reloading cached model", async () => {
    const ui = dashboard(); await settle(); const before = ui.context(); const fixedModel = JSON.stringify(model);
    const modelRequests = () => ui.calls.filter((c) => c.path.endsWith("operations-ridge-model.json")).length;
    assert.equal(modelRequests(), 1);
    const updated = clone(ui.responses.snapshot);
    updated.rows.forEach((r) => { r.gmv *= 1.25; r.storeAverage = r.gmv / r.storeDays; });
    ui.responses.snapshot = updated; await ui.element("refresh").fire("click");
    near(ui.context().selected.gmv, before.selected.gmv * 1.25);
    near(ui.context().forecast.gmv, before.forecast.gmv * 1.25);
    assert.equal(modelRequests(), 1); assert.equal(JSON.stringify(model), fixedModel);
    assert.equal(ui.element("refresh").disabled, false); assert.equal(ui.element("dashboard").attributes["aria-busy"], "false");
    assert.equal(ui.timers.size, 0);
  });

  await test("actuals render while model fetch is delayed and switch to Ridge once it loads", async () => {
    let resolve; const gate = new Promise((done) => { resolve = done; });
    const ui = dashboard({ modelGate: gate }); await settle();
    assert.equal(ui.element("gmv-value").textContent, format(ui.responses.snapshot.rows[0].gmv, 10000));
    assert.equal(ui.context().forecast.mode, History.MODE);
    resolve(); await settle(); assert.equal(ui.context().forecast.mode, Ridge.MODE);
    assert.match(ui.element("forecast-note").textContent, /Ridge 固定模型/);
  });

  await test("missing/corrupt model explicitly displays original historical method", async () => {
    const corrupt = clone(model); corrupt.parameters.scale[0] = 0;
    for (const options of [{ ridge: null }, { ridge: corrupt }, { withoutRidge: true }]) {
      const ui = dashboard(options); await settle();
      assert.equal(ui.context().forecast.mode, History.MODE);
      near(ui.context().forecast.gmv, History.calculate(ui.responses.snapshot, history, "全国").gmv);
      assert.match(ui.element("forecast-note").textContent, /当前使用原历史进度法/);
      assert.match(ui.element("forecast-share-label").textContent, /去年同期进度/);
      assert.equal(ui.element("gmv-value").textContent, format(ui.responses.snapshot.rows[0].gmv, 10000));
    }
  });

  await test("model/history failures leave all actual metric cards and targets usable", async () => {
    const ui = dashboard({ ridge: null, history: null }); await settle();
    assert.equal(ui.context().forecast.available, false); assert.equal(ui.element("forecast-value").textContent, "—");
    assert.equal(ui.element("gmv-value").textContent, format(ui.responses.snapshot.rows[0].gmv, 10000));
    assert.equal(ui.element("average-value").textContent, format(ui.responses.snapshot.rows[0].storeAverage));
    assert.equal(ui.element("stores-value").textContent, String(ui.responses.snapshot.rows[0].operatingStores));
    assert.notEqual(ui.element("gmv-target").textContent, "—"); assert.equal(ui.table.dataset.forecast, "false");
    assert.match(ui.element("forecast-note").textContent, /实际指标正常显示/);
  });

  await test("completed September displays exact full-month actual for every scope", async () => {
    const ui = dashboard({ month: "2026-09" }); await settle();
    assert.match(ui.element("forecast-title").textContent, /全月 GMV · 已结束/);
    assert.match(ui.element("forecast-note").textContent, /全月已结束/);
    for (const row of ui.context().regions) { assert.equal(row.forecast.mode, Ridge.MODE); assert.equal(row.forecast.gmv, row.gmv); }
    assert.equal(ui.context().forecast.gmv, ui.context().selected.gmv);
  });

  await test("changing month to future empty November cannot retain October actual or forecast", async () => {
    const ui = dashboard(); await settle(); assert.equal(ui.context().forecast.available, true);
    ui.responses.snapshot = emptySnapshot(); ui.element("month").value = "2026-11";
    await ui.element("month").fire("change");
    assert.equal(ui.context().month, "2026-11"); assert.equal(ui.context().end, null);
    assert.equal(ui.context().forecast.available, false); assert.equal(ui.context().selected.gmv, null);
    ["gmv-value", "average-value", "stores-value", "forecast-value"].forEach((id) => assert.equal(ui.element(id).textContent, "—"));
    assert.notEqual(ui.element("gmv-target").textContent, "—");
    assert.match(ui.element("forecast-note").textContent, /尚无累计实际数据/);
  });

  await test("invalid actual payload never produces a fake forecast", async () => {
    const inconsistent = snapshot(); inconsistent.rows[0].gmv += 100; inconsistent.rows[0].storeAverage = inconsistent.rows[0].gmv / inconsistent.rows[0].storeDays;
    const ui = dashboard({ snapshot: inconsistent }); await settle();
    assert.equal(ui.context().forecast.available, false); assert.equal(ui.element("forecast-value").textContent, "—");
    const missing = snapshot(); missing.rows[1].gmv = null;
    const rejected = dashboard({ snapshot: missing }); await settle();
    assert.equal(rejected.context().forecast.available, false); assert.equal(rejected.element("forecast-value").textContent, "—");
    assert.match(rejected.element("status-text").textContent, /数据暂不可用/);
  });

  console.log(`operations-dashboard Ridge integration: ${count} tests passed`);
})().catch((error) => { console.error(error); process.exitCode = 1; });
