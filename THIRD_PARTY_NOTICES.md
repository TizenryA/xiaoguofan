# 第三方与来源说明

## 上游

本仓库 Fork 并修改自 https://github.com/Mag1cFall/AIStudio2API 。原作者 Mag1cFall，采用 MIT；根目录 LICENSE 保持原版权声明。原中文 README 保存在 README_upstream.md，上游源码和提交历史保留。

小锅饭门户是新增的多租户外围应用，不是上游官方多租户功能。固定依赖上游 v0.2.12 容器，镜像及摘要见 xiaoguofan/portal/workers.py。更新上游时须重新验证门户调用兼容性。

## 新增程序

xiaoguofan/ 中门户、扩展、测试、部署脚本由渡鸦4455维护，采用 MIT，见 LICENSE-XIAOGUOFAN。依赖库按各自许可证使用。

## 防截断核心

`xiaoguofan/portal/anti_core/synthetic/` 融合自 damoshen123/Antigravity-anti-truncation-gateway-node，提交 e66dbbb4ae79b9366136b1dc41bdb9e922140941，MIT 许可保留于该目录 LICENSE。其设计来源为 Xeltra233/Antigravity-anti-truncation-gateway。小锅饭适配详见同目录 NOTICE.md。

## 插画与品牌

登录欢迎图、猫娘拒绝图为站点维护者提供的展示素材，保留相关权利，**不因程序源码使用 MIT 而自动获得 MIT 授权**。本项目不声称素材为上游作者所作；其他部署者请自行确认使用权或替换为自有图片。

Google、Discord 及相关产品名称和商标属于其各自权利人。本站并非其官方服务。
