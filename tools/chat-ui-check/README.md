# 熊大爷发布与故障回归

所有调用仍使用固定 Luna/low/standard/default 策略，不因网络故障自动重复模型请求。

修改 `assets/mascot.css`、`mascot.js` 或 `chat-config.js` 后，先执行：

```sh
python3 tools/chat-ui-check/stamp-chat-assets.py --write
node tools/chat-ui-check/chat-release.test.cjs
node tools/chat-ui-check/mascot-stability.test.cjs
node tools/chat-ui-check/chat-config.test.cjs
python3 -m unittest discover -s tools/operations-chat/tests
python3 -m unittest discover -s tools/operations-chat-cloud/tests
```

指纹脚本更新所有挂载熊大爷的 HTML 入口，首页和经营页都必须刷新缓存版本。不能只检查经营页或仅依靠服务状态中的 ready：它只证明近期心跳，最终要在公网真实取得回复。

手机检查包括输入框计算字号至少16px、可见窗口缩放／位移／键盘后关闭键和发送键仍在范围内；仅对话消息滚动，顶部和输入区保持可用。重试保留同一条问题，不新增重复错误气泡。状态 GET 不调用模型，不传认证凭据。

服务端故障注入覆盖领取响应、开始确认、结果上报丢包及恢复；同一任务只能调用后端一次，已有结果只补交。协议兼容升级须先部署 Cloud Run，再重启本机 worker，保留原通信凭据。Cloud Run 实例丢失内存状态或 Mac 关机仍需重新提问，不能宣称所有故障都可无损恢复。

仓库的 `Chat regression checks` 工作流会在相关源码变更时运行相同测试，不接触真实模型或凭据。发布者必须检查该工作流成功及公网真实回复；当前 Pages 仍使用原发布方式，这项检查本身不会阻止 Pages 构建。
