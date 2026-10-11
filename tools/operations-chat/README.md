# 熊大爷本机模型聊天服务

服务只监听 `127.0.0.1:8787`，固定使用 `gpt-6-luna`、`reasoning.effort=low`、`service_tier=default` 和本机 CLIProxyAPI 的 Responses 端点；不把凭据写入本目录。公网 GitHub Pages 尚未接入这个本地模型服务。

```sh
python3 tools/operations-chat/server.py
```

访问 `http://127.0.0.1:8787/operations/`。`/healthz` 返回固定模型策略和就绪状态。服务仅接受来自 loopback 当前端口、带 loopback Origin 的同源请求。每日请求计数保存在 `tools/operations-chat/.private/`。

本地测试（不会发模型请求）：

```sh
python3 -m unittest discover -s tools/operations-chat/tests -v
```
