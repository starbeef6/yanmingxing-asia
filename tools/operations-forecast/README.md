# 固定 Ridge 网页预测

网页优先加载 `assets/operations-ridge-model.json`，由 `operations-ridge.js` 在浏览器计算；不需要新的查询服务器或在线训练服务。实际数据仍来自现有同步的月初累计快照，取 `data.end` 为截止日。

模型版本：`calendar-ridge-2025q4-alpha100-v1`。仅支持 2026 年 9–12 月、北区／西区／南区／四川／江苏。2025 年四季度 460 条区域日汇总训练；使用 2025 年前向验证选定 α=100。模型包含固定系数、训练期均值和标准差；不包含原始业务明细或数据库密码。

每区：预计全月 GMV＝实际累计 GMV ÷ 模型对应月份、星期和截止日的累计份额。全国为五区预测之和。目标档位只改变预计达成和缺口；月末预测等于实际。刷新更新实际时自动重算，不会重训模型。模型加载失败时沿用原历史参考法，并在页面明确标注。

本版本是获准上线的初步模型：训练参考源与旧正式月报存在口径差异，9 月验证仅一个月份，且明细是月后导出，不能证明补录数据在当时已可用。9 月旧法比较使用跨月代理，不是完整同比验证；不能把 0.88% 回放误差当作未来保证。

验证：

```sh
node tools/operations-forecast/test_ridge.cjs
node tools/operations-forecast/test_dashboard.cjs
node tools/operations-forecast/test_forecast.cjs
python3 tools/chat-ui-check/run-checks.py
```

导出器 `export_ridge.py --help` 给出重现参数，训练输入与选择结果必须显式提供；日常同步不调用导出器。需要新模型时先离线验证，再更新模型版本与网页缓存版本。原历史算法和参考表完整保留，原“成果1”恢复点未改动。
