"use strict";
// A changed endpoint or widget must invalidate every page's cached asset URL.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const root = path.resolve(__dirname, "../..");
const assets = ["mascot.css", "mascot.js", "chat-config.js"];
const fingerprints = Object.fromEntries(assets.map(name => [name,
  crypto.createHash("sha256").update(fs.readFileSync(path.join(root, "assets", name))).digest("hex").slice(0, 12)]));
function allHtml(dir) {
  return fs.readdirSync(dir, {withFileTypes:true}).flatMap(entry => {
    if (entry.name.startsWith(".") || entry.name === "node_modules" || entry.name === "tools") return [];
    const file = path.join(dir, entry.name);
    return entry.isDirectory() ? allHtml(file) : entry.name.endsWith(".html") ? [file] : [];
  });
}
let checked = 0;
for (const file of allHtml(root)) {
  const html = fs.readFileSync(file, "utf8");
  if (!/src=["'][^"']*\bmascot\.js\b/.test(html)) continue;
  for (const name of assets) {
    const urls = [...html.matchAll(/(?:src|href)=["']([^"']+)["']/g)].map(match => match[1]);
    const url = urls.find(value => value.split("?")[0].endsWith("/" + name));
    assert.ok(url, `${path.relative(root,file)} misses ${name}`);
    assert.equal(new URL(url, "https://example.invalid/").searchParams.get("v"), fingerprints[name],
      `${path.relative(root,file)} has stale ${name}; update all widget entry pages after source changes`);
  }
  checked += 1;
}
assert.ok(checked >= 2, "Homepage and operating dashboard must both be checked");
console.log(`PASS ${checked} chat entry pages: source fingerprints match and invalidate stale caches`);
