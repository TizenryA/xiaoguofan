# 渡鸦の小锅饭

欢迎来到渡鸦の小锅饭，喵。

**本项目 Fork 并修改自 [Mag1cFall/AIStudio2API](https://github.com/Mag1cFall/AIStudio2API)。** 原项目作者为 **Mag1cFall**，协议转换与底层工作由原项目提供；本 Fork 增加了面向多用户的个人账号门户、租户隔离、Discord 登录、参考额度与 MD3 Lite 界面。感谢原作者的开源贡献。

维护与二次开发：**渡鸦4455 / [TizenryA](https://github.com/TizenryA)**。

> 虽然叫“小锅饭”，但大家各用各的账号和额度，不是共享公共账号池。本项目不是 Google 官方产品，也不保证 Google 登录、模型和额度始终可用。

## 能做什么

- Discord 登录，进入自己的账号空间。
- 上传自己的 Google AI Studio 认证 JSON，或使用桌面浏览器扩展确认导入。
- 每用户独立 worker，账号失效、空池时不借用别人的账号。
- 创建与撤销个人 API Key，接入支持兼容接口的客户端。
- 查看每账号成功、失败、调用次数和近七天趋势，导出使用记录。
- 手动标注 Free / PRO，显示模型参考额度与进度。
- Lite 只保留上传、额度、使用记录、账号管理四个入口。
- 管理后台配置 Discord、社区屏蔽、可撤销朋友白名单与资源上限。

## 0.7.5 Playground / Build 分开

按上游`channels`元数据把模型ID和显示名分为`-pg`、`-build`；防截断版本保留后缀。发起请求时剥离后缀并让本人worker通过`UPSTREAM_CHANNELS`只启用指定通道，不跨通道借用；同一租户只保留一个worker，切换通道会重建。旧无后缀ID仍走默认调度；上游无渠道证据的模型保留原样，不编造可用渠道。额度统计归一回原模型。117 Python及14 JS回归通过，部署后真实Docker运行时配置验证两条单渠道；未使用其他用户账号做Google真实生成。

## 0.7.4 防截断显示名修复

防截断别名同时为`id`及上游存在的`name`/`display_name`/`displayName`加前缀，避免按显示名展示或合并模型的客户端仍看到普通名称。原模型保持不变，开关及租户隔离逻辑不变。113 Python与14 JS回归通过；升级后在客户端重新拉取模型列表。

## 0.7.3 修正窜改猴授权

脚本1.0.1修正Cookie对象授权声明为`@grant GM_cookie` / `@grant GM.cookie`，原1.0.0误写`.list`导致官方Beta也不注入接口。已用官网下载的Beta 5.6.6242，在桌面Chromium实际安装脚本，通过真实GM_cookie读取隔离HttpOnly测试Cookie并下载JSON。不是手机或真实Google账号验收。更新脚本后需刷新AI Studio页面。

## 0.7.2 官方窜改猴脚本

上传页新增官方 Tampermonkey 脚本安装入口。AI Studio 页内手动检查→下载 JSON→返回门户上传，不使用扩展弹窗，不自动上传或持久保存凭据。脚本1.0.0使用 GM_cookie.list/GM.cookie.list，并验证必要Cookie及HttpOnly读取；官方文档注明HttpOnly目前限Beta支持，稳定版不保证可用。读不到则阻止导出。说明见 `xiaoguofan/userscript/使用说明.txt`。隔离数据下载/门户导入回归通过，未做手机官方窜改猴或真实Google登录验收。

## 0.7.1 扩展兼容修复

扩展更新到 0.3.5：兼容回调式 Chrome API 和 Promise 式 browser API；防止未取得标签页时出现 `tab is undefined`，无独立窗口支持时改为新标签。更新需重新加载扩展，确认界面版本和邮箱输入框。通过真实桌面 Chrome 扩展 API 隔离测试；手机浏览器尚未实机验收，不承诺所有浏览器可用。

## 0.7.0 界面与记录

- 额度首页、上传第三页；右上角 Discord 头像菜单，管理员金色标识。既有会话重新通过 Discord 登录后更新头像。
- MD3 风格按钮/开关、深浅色层级、动画折叠；扩展图文教程通过上传页或头像菜单进入。
- 记录包含首字耗时、总耗时和上游报告的输入/输出 Token；流式聊天自动请求 usage。旧记录不回填，未知用量不估算。非流式首字指标表示完整回复到达时间。
- 仅保存耗时和计量元数据，不保存提示词/回复正文。

## 防截断模型（0.6.0）

在 Lite「账号管理 → 防截断模式」开启，刷新客户端模型列表，即可选用 `[防截断]gemini-4-argon`、`[防截断]gemini-3.8-flash` 等前缀模型。普通模型原样保留；关闭后前缀模型不再显示或接受调用。

核心直接融合自 [Antigravity-anti-truncation-gateway-node](https://github.com/damoshen123/Antigravity-anti-truncation-gateway-node)，不是另一个外部网关。支持 OpenAI Chat Completions 普通/流式文本回复，不支持 Responses/Gemini 原生接口和图片音频模型；不自动重试。仍只使用本人账号，前缀模型与原模型合并参考额度计数。

需要 Node.js 18+，通过私有管道运行内置转换组件，不监听新端口。来源和 MIT 许可见 `xiaoguofan/portal/anti_core/NOTICE.md` 和 `LICENSE`。防截断不能保证消除所有截断，也不能突破模型硬限制。

## 源码目录

```text
xiaoguofan/             小锅饭门户 v0.7.5
  portal/               FastAPI、账号隔离与 MD3 界面
  extension/            桌面浏览器主动导入扩展源码
  tests/                Python 与扩展核心测试
  deploy/               通用安装脚本与扩展打包脚本
  docs/                 部署和安全说明
internal/, cmd/, ...    保留的 AIStudio2API 上游源码
README_upstream.md      原项目中文 README
LICENSE                 保留原作者 MIT 版权声明
THIRD_PARTY_NOTICES.md   来源与素材说明
```

小锅饭门户调用固定版本的上游 worker 镜像，**并非把根目录 Go 程序直接当作多租户门户运行**。两者部署方式不同；原版使用说明见 [原项目 README](README_upstream.md)。

## 开始部署

参阅 **[小锅饭部署说明](xiaoguofan/docs/部署说明.md)**。

需要 Linux、Docker、Python 3.11+、systemd，以及能正常访问 Google AI Studio 的网络。对公网开放时必须使用 HTTPS，并先配置自己的 Discord OAuth 应用。

## 参考额度

| 模型 | Free | PRO |
| --- | --- | --- |
| 4a / `gemini-4-argon` | 2 条 / 日 | 50 条 / 日 |
| 3.8f / `gemini-3.8-flash` | 100 条 / 日 | 100 条 / 日 |

- 每日北京时间 00:00 重置，仅本站成功生成计数。
- **这是站内参考值，不是 Google 官方余额，不拦截超出参考值的调用。**
- 账号类型由本人手动标注，不代表已核验订阅。
- 站外用量不计入；旧记录不猜测账号归属。
- 新请求由门户选定本人账号并记录归属。为维持热 worker 优先复用，参考值用尽或上一请求失败时会尝试本人其他账号；所有账号超参考仍不以参考值拒绝请求。

## 安全与边界

认证 JSON 含高敏感 Google 会话凭据，运营者可以用它访问账号。**只使用专用账号，并且仅向可信运营者上传。** 会话加密存储不代表运营者无法解密。

数据库、主密钥、管理密码、真实 Cookie、会话、线上配置和服务器私有运维资料不包含在本仓库。自行部署时请妥善保护数据目录及备份。

安卓可上传 JSON；手机 Chrome 不支持桌面扩展。远程 Google 登录功能因兼容性与风控问题已撤下，**不包含此功能**。

## 测试

```bash
cd xiaoguofan
uv sync --dev
uv run pytest -q
uv run ruff check --select E9,F,I portal tests
node --test tests/extension-core.test.cjs
python3 deploy/build-extension.py
```

Python 测试覆盖账号隔离、权限、Cookie 格式、参考计数、数据库迁移与流式代理；本地上游/测试数据用于验证软件行为，**不代表真实 Google 登录与生成验证**。

## 开源与来源

上游代码保留 MIT 许可证及 Mag1cFall 原版权声明；小锅饭新增程序源码采用 MIT，见 [LICENSE-XIAOGUOFAN](LICENSE-XIAOGUOFAN)。插画素材不自动按 MIT 授权，见 [素材与第三方说明](THIRD_PARTY_NOTICES.md)。
