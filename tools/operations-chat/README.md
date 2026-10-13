# 熊大爷 Luna 聊天服务

模型服务只监听 `127.0.0.1:8787`，固定使用 `gpt-6-luna`、`reasoning.effort=low`、`reasoning.mode=standard`、`service_tier=default` 和本机 CLIProxyAPI 的 Responses 端点；不把凭据写入本目录。

```sh
python3 tools/operations-chat/server.py
```

访问 `http://127.0.0.1:8787/operations/`。`/healthz` 返回固定模型策略和就绪状态。服务仅接受来自 loopback 当前端口、带 loopback Origin 的同源请求。每日请求计数保存在 `tools/operations-chat/.private/`。

## 公网网页接入

正式网页 `https://yanmingxing.asia/operations/` 通过 `assets/chat-config.js` 使用固定 HTTPS 接口：

`https://operations-chat-relay-smuzijgeaq-as.a.run.app/api/chat`

`tools/operations-chat-cloud/server.py` 在 Cloud Run 中提供单请求中转。本机 `cloud_worker.py` 每 5 秒通过普通 HTTPS 拉取待处理请求，交给固定的 8787 Luna 后端，再提交回复。模型凭据只由原有本机代理持有；云端只有单独的中转通信凭据。云端实例上限为 1、最小实例数为 0，模型调用预算仍由本机后端统一限制。

公网状态入口为 `/api/status`，不使用 Cloud Run 会拦截的 `/healthz`。状态中的 `ready` 仅表示近期收到本机连接，必须实际发送消息并收到有效回复，才能确认端到端聊天正常。

通信协议 2 将“请求送达”和“开始模型调用”分开：`pull` 使用本次 worker 进程的随机会话 ID 预留任务，在 `start` 确认前允许同会话重新获取同一任务；`start` 确认可重复，但已开始的任务不会重新发给其他 worker。完成结果在本机缓存最多 32 条、180 秒，每轮优先补交尚未确认的结果，传输重试不重复请求模型。云端只缓存同等上限的完成确认。日志仅记录 UTC 时间和固定状态码，不包含问题、URL、凭据或请求头。

升级顺序为先部署兼容旧 `pull` 的云端，再重启新版本机 worker。旧协议保持原来的一次交付行为，新 worker 只有收到 `requires_start` 才使用确认流程。70 秒请求期限及原有模型预算保持不变；短时丢包可恢复，Cloud Run 实例重启会清空内存任务，本机 worker 在开始确认后崩溃也不能保证找回未完成请求。这种情况以超时结束，不自动重跑可能已执行的模型调用。

本机 worker 读取被 Git 忽略的 `.private/cloud-relay-config.json`（仅含固定服务 URL）与 `.private/cloud-relay-token`（文件权限 600）。中转凭据同时保存在项目 Secret Manager 的 `operations-chat-relay-worker-token`，专用服务账号只获得该 Secret 的读取权限。不要把私密目录、凭据或模型代理配置提交到仓库。

8787 本机页面仍保留服务器注入的同源接口。8786 预览由 `preview.py` 提供，使用相同的公开文件白名单；该来源的聊天直接访问本机 8788 网关。其他来源不会自动连接本机网关。

`public_gateway.py` 监听 `127.0.0.1:8788`。它只提供 `GET /healthz`、`POST /api/chat` 和对应预检，并只给指定网站来源设置跨域头。网关固定转发到本机 8787，不能转发任意地址；不读凭据、不公开静态文件或代理的 `/v1/`。来源白名单不是身份认证，真正的调用预算由后端统一限制为每日 100 次、每分钟 12 次和并发 1。

```sh
python3 tools/operations-chat/public_gateway.py
python3 tools/operations-chat/cloud_worker.py
```

旧临时 Cloudflare 隧道已停止，正式聊天不依赖临时隧道地址，也不公开 8317 模型代理。Mac 关机、休眠、本机代理或 worker 停止时，公网聊天会离线；静态看板仍可浏览。

后台、网关、8786 预览及云端 worker 通过 `launchctl` 独立运行，并已保存到当前用户的 `~/Library/LaunchAgents/`，登录后可以自动启动；结束 Codex 回合或关闭终端不会主动关闭它们。配置和日志在被 Git 忽略的 `.private/` 中。本机模型代理也需要运行。

本机预览入口：`http://127.0.0.1:8786/operations/`。`assets/chat-config.js` 将这一个端口及对应 `localhost` 来源连接到 8788，网关按来源精确允许；统一沿用 8787 的模型策略、并发和每日预算。

本地测试（不会发模型请求）：

```sh
python3 -m unittest discover -s tools/operations-chat/tests -v
python3 -m unittest discover -s tools/operations-chat-cloud/tests -v
```

## 看板上下文与 C(3,2)

每次问题都携带当前范围及五个大区的精简数字、目标、已有历史预测，不再依靠问题关键词猜测是否保留大区。模型只能读取传入的数据，不能声称看过用户截图或屏幕。

组合规则：GMV、店日均、在营门店中任意两项达标就通过；111/110/101/011通过，100/010/001/000预警。程序计算 jointStatus、metCount、required=2及已满足组合，模型据此说明。第三项的实际差距仍报告，但不改变已经满足两项的通过判定。未知值保持未知；月中GMV按已有历史进度参考判断，来源不完整时仅作为当前快照/进度的条件结论。

## 完整聊天记录

`assets/chat-session.js` 保留本浏览器标签页的完整原文、时间及范围，使用sessionStorage支持刷新恢复；这与只发给模型最近四条历史是两回事。“记录”按钮或要求整理聊天记录会直接生成TXT下载，不调用模型。跨区域的原文也保留，模型历史则按当前范围挑选四条。

此功能从升级后开始记录，无法恢复旧版从未保存且已关闭/刷新的内容；也不跨浏览器、标签页或设备同步。浏览器禁止存储或达到容量时，当前内存中的原文仍可完整导出，页面会提示刷新前下载。
