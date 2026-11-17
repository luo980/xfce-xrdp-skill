# 桌面安装与发行版适配

## 支持范围和运行入口

这组脚本提取自已实际验收的 Debian 12、Ubuntu 26.04 amd64 部署。参数化后的脚本已通过离线配置渲染与输入校验；在新主机上仍需完成真实 RDP 验收。其他发行版、架构或 xrdp 主版本先重新审查包及配置结构。

保持 `scripts/` 目录完整；在已核对的目标主机，以管理员身份执行：

```bash
sudo bash scripts/desktop/install-desktop.sh \
  --user desktopuser --label 'Remote Desktop' \
  --rdp-port 13389 --ssh-port 22 --bind-address 127.0.0.1
```

`desktopuser` 必须替换为现有 Linux 账号。`--ssh-port` 使用实际 SSH 服务端口；`--rdp-port` 与其不同。脚本不创建账号或密码，不调整 SSH 认证。`--bind-address` 默认 loopback，适合已有 SSH 隧道；已授权公网直连时显式传 `0.0.0.0`，同时检查云防火墙和主机防火墙。当前脚本只处理 IPv4 RDP 监听；Fail2ban 的 IPv6 动作测试不代表 RDP 已开放 IPv6。

配置器支持先执行只读检查：

```bash
sudo python3 scripts/desktop/configure-desktop.py --user desktopuser --preflight
```

两个脚本都支持 `--help`。用户名只接受常见 POSIX 小写名称；标签限 48 个字母、数字、空格及 `. _ - & ( )`，不写入命令。账户 home 必须为该用户所有、绝对路径、无符号链接及 shell 特殊字符。备份目录由安装器生成，不从主机标签拼接。

## 包和配置分支

| 项目 | Debian 12 | Ubuntu 26.04 |
|---|---|---|
| xrdp 已审查系列 | `0.9.21.1-*` | `0.10.1-*` |
| sesman | 显式 `127.0.0.1:3350` | 保留发行版 Unix socket 配置 |
| Firefox ESR | 发行版 APT | 先配置并验证 Mozilla 官方 APT 源 |
| Fcitx5 前端 | GTK2、GTK3、Qt5 | GTK2、GTK3、Qt5、Qt6 |
| chansrv 日志 | 保留发行版结构 | `[ChansrvLogging] LogLevel=INFO` |

安装器先更新 APT、确认 `firefox-esr` 候选并模拟包安装，再执行真实安装。配置器在写入前检查上述 xrdp 系列；候选变为未审查版本时停下审查。不要使用 `apt-cache policy ... | awk '... { print; exit }'`：在 `pipefail` 下，提前退出的消费者可能让上游因 SIGPIPE 使安装中断。

配置写入保留 xrdp 的重复 `param=` 行；不能用会合并重复键的普通 INI parser 重写整个文件。只替换受管键、移除受管键的重复活动定义；遇到重复受管 section 停止，要求人工审查。XML 由 ElementTree 序列化并转义，面板和输入法配置不通过 shell 插值执行。

## 首次初始化和保护顺序

安装器用于新桌面初始化。以下情况停止：已有运行中的 xrdp/sesman/Xfce、已有用户 `.xsession`/Xfce/受管输入法配置、已有本技能完成标记、预先存在的 xrdp mask、目标 RDP 端口被占用。已有桌面应检查和修复当前配置，不能用初始化器覆盖。

1. 保存 `/var/backups/xfce-xrdp-desktop.<id>/`：原包列表、unit 状态、部署参数和 APT 模拟结果。
2. mask xrdp 与 sesman，避免安装包后提前开放默认端口；保持已有 Fail2ban 可用。
3. 安装包，写入用户桌面和 TLS/RDP 配置。原配置以绝对路径结构保存在备份目录；`created-files.txt` 记录新文件。
4. 调用 `../install-fail2ban.sh`，检查两个 jail 可用。
5. unmask 并启动 xrdp；核对服务、监听和证书，然后完成真实连接验收及 Fail2ban 内核规则测试。

配置成功标记位于 `/var/lib/xfce-xrdp-desktop/<user>.json`，内含备份目录和使用的参数。该标记表示配置文件写入成功；是否已完成 GUI 验收须看本次验收记录。

若安装失败，退出处理会停止并重新 mask xrdp/sesman；若 mask 本身失败，会明确报告，立即检查真实 unit 状态。脚本不自动启动失败现场，也不自动撤销已安装的软件包。普通重新执行会拒绝已有配置和 mask，避免覆盖第一次备份。

恢复时根据打印的备份路径判断失败阶段：

- 包安装或配置未完成：查看日志及 `created-files.txt`，逐项还原已改配置、移除本次新增文件，保持 RDP 停止；确认现场恢复后再解除本次创建的 mask 并重新初始化。不要批量删除用户后来创建的数据。
- 配置完成但 Fail2ban/启动阶段失败：修复该阶段，重跑 Fail2ban 安装器并验收 jail；检查 xrdp 配置和会话为空后，明确执行 `systemctl unmask xrdp xrdp-sesman`、`systemctl enable --now xrdp`。
- 原 unit 状态在 `services-before.txt` 中；只恢复本次改动，不移除原有的管理策略。

## 桌面行为

Arc-Dark、Papirus-Dark、Noto CJK、JetBrains Mono，44px 底部面板，定制 SVG/PNG 壁纸、终端配色、Fcitx5 拼音和 Firefox ESR。关闭合成、动画、DPMS 与自动空闲注销；保留断线会话，最多两个会话。`.xsession` 保留现有 D-Bus 环境、清除 `SESSION_MANAGER` 和 `TZ`，让桌面按 `/etc/localtime` 显示时间；脚本不根据客户端所在地改服务器时区。

默认禁用 `rdpsnd` 和 FUSE，客户端也应关闭音频、麦克风、打印机和驱动器映射。需要音频时另行审查当前 PulseAudio/PipeWire 及 xrdp 模块，不能以包安装完成代替声音实测。用户说无需音频时跳过该项。

## 离线检查

```bash
bash -n scripts/desktop/install-desktop.sh
python3 scripts/desktop/test-configure.py
```

离线用临时目录和模拟系统命令验证两个发行版配置、原文件备份、动态参数、XML、保留重复 Xorg 参数、静默禁用音频及拒绝非法输入。它不安装包、不调用 sudo、不连接任何主机，也不代替目标系统运行验收。
