# 小锅饭发行包

提供 Linux systemd 安装版与 Docker Compose 版，两者都使用 Docker 动态用户池。默认只监听 127.0.0.1:8890；需自行配置 HTTPS 反向代理及域名。仅支持 systemd + cgroup v2 + Docker systemd cgroup driver，不支持普通无特权 PaaS。

- Linux 版：门户/broker 是宿主 Python 服务；Node 用于防截断。安装器使用 uv 创建隔离环境。
- Docker 版：门户和 broker 使用同一个镜像；仅 broker 挂载 Docker socket（拥有宿主高权限），门户不挂载。两者使用 host network，因此仅支持 Linux。不能把 broker socket 或 Docker socket 暴露到公网。
- 所有门户、broker、Node 和 worker 都在 `xiaoguofan.slice` 中，总上限 2GiB、禁用 Swap，High=1792MiB。默认保留每池650MiB最坏情况预算，因此100仅是入口/池名额配置上限，不代表100人同时生成。
- amd64 可在构建机原生验收；arm64 为交叉构建加QEMU测试，非物理ARM服务器验证。

## 依赖

先安装 Docker Engine（含 Compose 插件）、Python 3.11+、Node.js 18+（仅Linux版）、uv（仅Linux版）。安装器不会自动改动防火墙、系统软件源或已有站点。需要 root；会创建服务用户、systemd slice、相关单元和受限数据目录。

## 安装

解压对应架构压缩包，进入目录：

```
cd xiaoguofan-v0.9.1-linux-amd64
sudo python3 install.py --mode linux --origin https://your.example/lite
```

Docker版：

```
cd xiaoguofan-v0.9.1-docker-amd64
sudo python3 install.py --mode docker --origin https://your.example/lite
```

默认安装到 `/opt/xiaoguofan-release`，数据 `/var/lib/xiaoguofan`；可用 `--root`、`--data`、`--port` 指定。origin 必须是实际外部HTTPS地址，可无路径或使用单段路径。仅用于回环隔离测试时可加 `--allow-local-http` 并提供 `http://127.0.0.1:端口`。

安装器将随机管理员密码写入数据目录 `credentials.json`（0600，不输出到日志）；反代 `/` 到 `http://127.0.0.1:8890` 后访问 `/guanlimanage`（有前缀时）或 `/admin`（无前缀时）。初始公共登录关闭。先设置 Discord OAuth、确认账号/密钥隔离，再开放公共登录。

```
sudo python3 -c 'import json; print(json.load(open("/var/lib/xiaoguofan/credentials.json"))["password"])'
```

该命令仅在自己的私密终端运行，不要把输出发群聊。

## 操作

Linux版：`systemctl status xiaoguofan-portal xiaoguofan-broker`。
Docker版：`systemctl status xiaoguofan-compose`、`docker compose --env-file /opt/xiaoguofan-release/runtime.env -f /opt/xiaoguofan-release/compose.yaml ps`。

停止Linux版：`systemctl disable --now xiaoguofan-portal xiaoguofan-broker`。
停止Docker版：`systemctl disable --now xiaoguofan-compose`。
停止后动态worker可能仍在，确认没有活动请求后运行 `python3 /opt/xiaoguofan-release/runtime.py cleanup` 清理本发行安装器管理的worker；不会删除账号数据库。

## 备份与升级

- `master.key`、SQLite数据库及其WAL、站点配置必须一起保留，丢失master.key无法解密账号。禁止用新key覆盖已有key。
- `sudo python3 /opt/xiaoguofan-release/runtime.py backup` 使用SQLite在线备份，结果保存到数据目录backups，包含全部数据文件及systemd/配置部署元数据；请自行加密异地备份。
- 升级先下载新包，使用相同root/data/origin/port/mode运行安装器并加 `--upgrade`；不支持跨模式无缝升级。安装器先备份数据及部署文件，然后停服务、更新代码/镜像、保留管理员密码及master.key。失败会保留回滚备份，安装错误不应被当成成功。
- 回滚先停服务，从 `root旁的同名-rollback-*` 恢复旧app/runtime/units，恢复备份数据库和相同master.key，重新加载systemd；禁止旧/新实例同时写一个数据库。
- 同一机器当前只支持一个小锅饭安装。检测到活动的旧门户或旧动态池时会拒绝安装，需先完整迁移，不能让安装器直接覆盖生产。

## 构建

在仓库根运行 `python3 xiaoguofan/packaging/build.py`。源代码不含生产凭据，产物位于dist/xiaoguofan；包含每架构worker镜像、Docker版额外的门户镜像、SHA256SUMS和源码提交信息。联网下载构建依赖；Linux版首次安装仍需联网安装Python依赖（不是完全离线包）。
