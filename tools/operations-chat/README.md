# 熊大爷 Luna 聊天服务

模型服务只监听 `127.0.0.1:8787`，固定使用 `gpt-6-luna`、`reasoning.effort=low`、`reasoning.mode=standard`、`service_tier=default` 和本机 CLIProxyAPI 的 Responses 端点；不把凭据写入本目录。

```sh
python3 tools/operations-chat/server.py
```

访问 `http://127.0.0.1:8787/operations/`。`/healthz` 返回固定模型策略和就绪状态。服务仅接受来自 loopback 当前端口、带 loopback Origin 的同源请求。每日请求计数保存在 `tools/operations-chat/.private/`。

## 公网网页接入

GitHub Pages 是静态页面，不能自己运行模型接口。网页通过 `assets/chat-config.js` 指定 HTTPS `/api/chat`；本机预览仍保留服务器注入的本地接口。

`public_gateway.py` 监听 `127.0.0.1:8788`。它只提供 `GET /healthz`、`POST /api/chat` 和对应预检，并只给指定网站来源设置跨域头。网关固定转发到本机 8787，不能转发任意地址；不读凭据、不公开静态文件或代理的 `/v1/`。来源白名单不是身份认证，真正的调用预算由后端统一限制为每日 100 次、每分钟 12 次和并发 1。

```sh
python3 tools/operations-chat/public_gateway.py
cloudflared tunnel --url http://127.0.0.1:8788 --protocol quic --no-autoupdate
```

隧道只能指向受限网关 **8788**，不能指向模型代理 8317。将本次 HTTPS 地址加 `/api/chat` 填入 `assets/chat-config.js` 后，需要发布静态网页。临时隧道地址可能在隧道重启后变化，届时必须更新并发布配置。Mac 关机、休眠、代理或隧道停止都会影响聊天；此方式不提供长期稳定的云端服务。

当前登录会话中，后台、网关和隧道通过 `launchctl` 独立运行，配置和日志在被 Git 忽略的 `.private/` 中，结束 Codex 回合不会主动关闭它们。它们没有注册为开机自动运行；重启机器后需要恢复服务和当次隧道地址。

本地测试（不会发模型请求）：

```sh
python3 -m unittest discover -s tools/operations-chat/tests -v
```
