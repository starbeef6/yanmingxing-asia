(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.OperationsChatSession = api;
})(typeof window === "object" ? window : null, function () {
  "use strict";
  const KEY = "xiong-daye-chat-transcript-v1";
  const MAX_SAVED_LENGTH = 1000000;
  const scopeKey = (scope) => [scope?.month, scope?.region, scope?.tier].map((x) => x || "").join("|");
  function cleanEntry(entry) {
    if (!entry || !["user", "assistant"].includes(entry.role) || typeof entry.content !== "string" || !entry.content || entry.content.length > 8000) return null;
    const scope = {};
    for (const key of ["month", "region", "tier", "start", "end"]) {
      if (typeof entry.scope?.[key] === "string" && entry.scope[key].length <= 40) scope[key] = entry.scope[key];
    }
    return {
      role: entry.role, content: entry.content, scope,
      at: typeof entry.at === "string" && Number.isFinite(Date.parse(entry.at)) ? entry.at : null,
      kind: ["conversation", "welcome", "error", "export"].includes(entry.kind) ? entry.kind : "conversation",
    };
  }
  function create(options = {}) {
    const storage = options.storage || null;
    const now = options.now || (() => new Date().toISOString());
    let records = [];
    let saved = !!storage;
    try {
      const raw = storage?.getItem(KEY);
      if (raw && raw.length <= MAX_SAVED_LENGTH) {
        const value = JSON.parse(raw);
        if (value.version === 1 && Array.isArray(value.entries)) records = value.entries.map(cleanEntry).filter(Boolean);
      }
    } catch (_) { saved = false; }
    const entries = () => records.map((entry) => ({ ...entry, scope: { ...entry.scope } }));
    function append(entry) {
      const cleaned = cleanEntry({ ...entry, at: entry.at || now() });
      if (!cleaned) return null;
      records.push(cleaned);
      try {
        const encoded = JSON.stringify({ version: 1, entries: records });
        if (!storage || encoded.length > MAX_SAVED_LENGTH) saved = false;
        else { storage.setItem(KEY, encoded); saved = true; }
      } catch (_) { saved = false; }
      // In-memory records remain complete even if browser storage is full.
      return { ...cleaned, scope: { ...cleaned.scope } };
    }
    function recent(scope, limit = 4) {
      const count = Math.min(4, Math.max(0, Math.floor(Number(limit) || 0)));
      if (!count) return [];
      return records.filter((entry) => entry.kind === "conversation" && scopeKey(entry.scope) === scopeKey(scope))
        .slice(-count).map(({ role, content }) => ({ role, content }));
    }
    function exportText() {
      const lines = ["熊大爷聊天记录", "范围：本浏览器标签页已保存的完整对话原文", `共 ${records.length} 条；时间为 UTC+8`, ""];
      for (const entry of records) {
        const time = entry.at ? new Date(entry.at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false }) : "时间未记录";
        const scope = [entry.scope.month, entry.scope.region, entry.scope.tier && `${entry.scope.tier}目标`].filter(Boolean).join(" · ");
        lines.push(`[${time}] ${entry.role === "user" ? "你" : "熊大爷"}${scope ? `（${scope}）` : ""}${entry.kind === "error" ? "［请求失败］" : ""}`);
        lines.push(entry.content, "");
      }
      return lines.join("\n");
    }
    return Object.freeze({ append, entries, recent, exportText, persisted: () => saved });
  }
  function wantsTranscript(question) {
    const topic = /(?:聊天|对话)(?:记录|历史|内容)|(?:所有|全部|完整).{0,6}(?:聊天|对话|记录)/.test(question);
    return topic && (/导出|整理|发给|发我|给我|汇总|所有|全部|完整|查看|列出|显示/.test(question) || /^(?:聊天记录|对话历史)[？?。！!]*$/.test(question.trim()));
  }
  return Object.freeze({ create, scopeKey, wantsTranscript });
});
