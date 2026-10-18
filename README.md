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

## 目录

```text
xiaoguofan/             小锅饭门户 v0.5.1
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
