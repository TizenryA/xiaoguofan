# 融合来源与修改说明

本目录直接纳入 https://github.com/damoshen123/Antigravity-anti-truncation-gateway-node 的 synthetic 核心代码，基于提交 `e66dbbb4ae79b9366136b1dc41bdb9e922140941`，MIT 许可证保留在 LICENSE。

该项目声明其设计源自 Xeltra233/Antigravity-anti-truncation-gateway，感谢原作者及 Node.js 重构作者。

小锅饭适配：
- 保留 injector / normalizer / repair / model-filter / stream-decoder / stream-transformer。
- 不引入原项目 HTTP 服务、后台、Key 库、环境配置、重试和全局对话调试缓存。
- bridge.js 通过私有 stdin/stdout 接入门户，Node.js 仅为本项目内置转换组件，不监听端口，不配置固定上游 Key。
- Python 门户继续负责鉴权、本人账号选择、模型别名、请求取消及参考用量。
- 禁用 logger；流式错误事件保留；补齐末尾无换行的 SSE；对外模型名保持 [防截断] 前缀。
- 不做自动空回重试，避免无提示增加消耗。

防截断只是兼容策略，不绕过模型输出硬限制，不保证消除所有截断。仅用于 OpenAI Chat Completions 的文本模型。
