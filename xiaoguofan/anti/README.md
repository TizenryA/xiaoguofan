# 防截断服务（独立常驻）

小锅饭的防截断转换（Antigravity synthetic tool 方案）从门户进程里拆出来的独立服务。

- `server.js`：常驻服务，只监听本机 unix socket（默认 `/run/xiaoguofan-anti/anti.sock`，可用 `XGF_ANTI_SOCKET` 或第一个参数改）。没有 TCP 端口、没有密钥、不读 `.env`。
- `session.js`：一次会话的协议实现，每个连接一个会话，互不阻塞，单会话按写入节流（慢客户端只拖慢自己）。
- `bridge.js`：兜底，一次调用一个短进程（旧行为）。门户连不上常驻服务时自动退回它，并在后台告警里显示。
- `core/`：上游转换代码（见 `NOTICE.md` / `LICENSE`），未改动逻辑。

## 协议（每个连接）

第一行是 JSON 命令：

| op | 请求 | 返回 |
|---|---|---|
| `ping` | `{"op":"ping"}` | `{"ok":true}` |
| `inject` | `{"op":"inject","body":{...}}` | `{"body":{...},"name":"agw_emit_..."}` |
| `normalize` | `{"op":"normalize","body":{...},"name":"...","model":"..."}` | 规范化后的 JSON |
| `stream` | `{"op":"stream","name":"...","model":"..."}`，随后若干行 `{"chunk":"<base64>"}`，最后 `{"end":true}` | 原样 SSE 字节流（`data: ...\n\n`） |

失败时返回一行 `{"error":"..."}` 并关闭连接。

## 运行

```bash
node anti/server.js /run/xiaoguofan-anti/anti.sock
```

systemd 单元示例见 `docs/共享引擎部署与回滚.md`。门户通过环境变量 `XGF_ANTI_SOCKET` 找到它。

## 测试

```bash
node --test anti/test/*.test.mjs
```
