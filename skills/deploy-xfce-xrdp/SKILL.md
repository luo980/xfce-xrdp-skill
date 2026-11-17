---
name: deploy-xfce-xrdp
description: 通过 SSH 部署、修复和验收 Debian/Ubuntu 的 Xfce + xrdp 远程桌面，包括桌面美化、中文输入法、Windows App 连接、文本剪贴板和 Fail2ban。适用于无头 Linux 主机的 RDP 部署与连接故障；普通 SSH 环境问题仅在影响该流程时使用。
---

# Xfce + xrdp 远程桌面

交付可实际登录的远程桌面、可复用连接文件、服务保护以及维护和回滚记录。安装完成后继续验收真实客户端，服务 active 不代表桌面、剪贴板已经可用。

## 确定目标与部署方式

从用户提供的 SSH 别名或主机开始，读取 `ssh -G` 确认目标、用户和端口。沿用已有授权、连接方式及音频偏好。缺少 sudo 凭据时再询问；凭据只用于交互输入，不写入脚本、RDP、仓库或日志。

首次登录检查 `/etc/os-release`、架构、默认 shell、用户/home、磁盘/内存、系统时区/NTP、包版本、现有 Xfce/Xorg 会话、服务和监听端口。检查 Fail2ban、nftables/UFW、云防火墙及已有 SSH 规则；不要通过全量输出环境变量或启动文件泄露密钥。

独立验证 SSH 时关闭复用：`ssh -o ControlMaster=no -o ControlPath=none HOST`。公钥验证加 `BatchMode=yes` 和 `PreferredAuthentications=publickey`；密码方式按当前授权交互登录。

- **已存在桌面/连接失败**：先读 [故障与会话恢复](references/troubleshooting.md)。不要重跑初始化覆盖配置或自动结束用户会话。
- **首次部署**：读 [系统适配与安装](references/desktop-compatibility.md) 和下方流程。
- **Windows App 文本剪贴板异常**：读 [剪贴板适配](references/clipboard.md)，按实测决定是否构建修复版。

网络方式遵循用户选择：明确要求公网直连时使用指定监听地址/端口；未指定时默认 loopback + SSH 转发，并说明连接方式。脚本默认 `127.0.0.1:13389`，公网 IPv4 需显式 `--bind-address 0.0.0.0`。非默认端口不替代 TLS、登录保护和防火墙。不要因部署桌面顺便关闭 SSH 密码登录、改 DNS、改主机名或推断机房时区。

## 首次部署

1. 保留一条 SSH 管理会话。确认已有 Linux 桌面用户、可用密码和 sudo；脚本不创建账号或重置密码。读取适配文档，根据实际 OS/包候选选择分支。当前脚本支持 Debian 12 与 Ubuntu 26.04 amd64；其他系统先适配，不能删版本检查强行执行。
2. Debian 使用发行版 Firefox ESR；Ubuntu 按 [Firefox 来源](references/operations.md#firefox-软件源) 配置并核验 Mozilla APT。先审查已有 source、pin 和 key，避免覆盖同名配置。
3. 将所需脚本放进目标用户私有 staging 目录，保留 `desktop/` 与其父目录中 Fail2ban 脚本的相对结构。普通用户上传，再通过 sudo 运行。桌面脚本会记录初始状态、备份文件、安装 Xfce/主题/输入法、配置 xrdp 和 Fail2ban，保护就绪后启动 RDP。例如：

   ```bash
   sudo bash scripts/desktop/install-desktop.sh --user desktopuser \
     --label Workstation --rdp-port 13389 --ssh-port 22 \
     --bind-address 127.0.0.1
   ```

   示例账号和端口必须替换成已核实的目标值。脚本不是升级命令；部分失败先检查备份与 mask 状态，按适配文档恢复或继续。
4. 核对 `.xsession` 保留可用 D-Bus，桌面时钟读取系统时区，Fcitx5 的 GTK/Qt 前端存在。默认 Arc-Dark/Papirus-Dark、底部面板、定制壁纸，关闭合成、空闲锁屏/断会话、音频及多余设备重定向。需要音频时另行配置目标发行版的支持并实测，不能仅修改 RDP 客户端标志。
5. 验证真实监听：xrdp 在预期地址/端口；Debian xrdp 0.9 的 sesman 为 loopback TCP3350，Ubuntu 0.10 使用 Unix socket。记录 TLS 证书指纹，外部直连再测客户端到服务器的端口可达性；loopback 模式检查转发。
6. 按 [Fail2ban 与维护](references/operations.md) 检查日志匹配、实际 action 和 nftables；运行 `verify-fail2ban.py` 做文档测试地址的 IPv4/IPv6 封禁/清理验证。服务启动或 jail ticket 成功不能替代内核规则验证。

## 客户端与验收

使用 `scripts/make-rdp-client.py` 生成 RDP 文件和可双击的 macOS Windows App `.command`；同目录保存，文件不含密码。公网模式填写真实可达目标；SSH 转发模式填写 `127.0.0.1` 和本地转发端口，并保持 SSH 转发存活。生成器本身不创建隧道。读 [客户端与验收](references/validation.md) 完成具体检查。

使用可用的原生 UI 工具进入真实桌面，检查主题、面板、浏览器、窗口缩放与键盘输入。自签名证书先核对实际指纹；遵循当前 UI 工具的确认规则，不永久关闭证书校验。UI 无法操作时给用户明确的手动验收动作，并继续不依赖 UI 的检查。

验收至少包括：

- 登录成功；实际 Xfce/Xorg/chansrv 进程、TLS 会话和客户端显示一致。
- Mac→远端、远端→Mac 的文字复制粘贴。工具可能恢复本地剪贴板或错误发送快捷键；自动测试失败时让用户手动确认，不能仅凭日志宣布成功。
- 必要时检查断开后重连是否复用会话；不要为测试打断正在使用的桌面。
- 用户不需要音频时保持关闭，不要求其试听。

剪贴板修复程序只针对经核对的源码版本。安装前退出相关桌面会话；新登录后检查 `/proc/PID/exe` 与安装候选的 SHA256，并再次验证双向复制。自定义 diversion 不会自动更新，必须交付升级/回滚步骤。

## 收尾

记录目标/日期、实际系统和包版本、网络方式/端口、配置和备份路径、补丁版本/哈希、Fail2ban 与 GUI 实测、用户确认及未完成项。记录不得含密码/密钥。保存维护脚本，清理本次 staging；保留构建来源、配置备份和回滚资料。最终给出连接方法与实际验收结果。

只有用户另外要求 SSH 仅公钥登录时，才按 [可选 SSH 加固](references/operations.md#可选-ssh-仅公钥登录) 执行。当前技能可自动发现；任何额外变更仍受用户任务范围约束。
