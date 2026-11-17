# 软件源、登录保护与维护

## Firefox 软件源

Debian 12 使用发行版 `firefox-esr`。Ubuntu 26.04 的普通 Firefox 可能是 Snap 过渡包；本流程使用 Mozilla 官方 ESR deb。先检查现有仓库与候选：

```bash
apt-cache policy firefox-esr
```

按 [Mozilla 官方安装文档](https://support.mozilla.org/en-US/kb/install-firefox-linux) 获取 `https://packages.mozilla.org/apt/repo-signing-key.gpg`，下载到临时文件，先用 `gpg --show-keys --with-colons` 检查主密钥指纹，再安装到 `/etc/apt/keyrings/packages.mozilla.org.asc`。已核验的指纹为：

```text
35BAA0B33E9EB396F59CA838C0BA5CE6DC6315A3
```

若官方轮换密钥，先核对新的官方说明；不能忽略不匹配继续安装。不要覆盖已有来源不明的同名文件，先备份并审查。

`/etc/apt/sources.list.d/mozilla.sources`：

```text
Types: deb
URIs: https://packages.mozilla.org/apt
Suites: mozilla
Components: main
Signed-By: /etc/apt/keyrings/packages.mozilla.org.asc
```

`/etc/apt/preferences.d/mozilla-firefox-esr`：

```text
Package: firefox-esr firefox-esr-l10n-*
Pin: origin packages.mozilla.org
Pin-Priority: 1000
```

`apt-get update` 验证签名后再次检查候选。桌面安装器要求已存在可安装的 ESR 候选，不会自行添加软件源。

## Fail2ban

`scripts/install-fail2ban.sh` 适配 Debian/Ubuntu 的 Fail2ban ≥ 1.0.2，保护 `sshd` 和 `remote-xrdp` 两个 jail。参数：`--ssh-port`、`--xrdp-port`、可重复 `--allow-ip IP/CIDR`，以及显式 `--allow-current-ssh`。

默认每服务 600 秒内 5 次失败封禁 3600 秒，只有 loopback 自动豁免。不要在动态出口下把当前公网地址自动加入永久白名单。SSH 转发模式的 RDP 可能只看到 loopback 来源，其入口认证保护由 SSH 承担。

配置和证据检查：

```bash
sudo fail2ban-client -t
sudo fail2ban-client status sshd
sudo fail2ban-client status remote-xrdp
sudo fail2ban-client get sshd actions
sudo fail2ban-client get remote-xrdp actions
sudo nft list ruleset
```

修改 action 后完整 restart Fail2ban；已有经验表明 reload 可能留下 ban ticket 而内核 action 未正确重建。不要清空现有 nftables 规则。

xrdp 失败日志来自 `xrdp-sesman.log` 的 INFO `AUTHFAIL`，以末尾 `time=epoch` 为时间、实际末尾 `ip=` 为来源。过滤器包含 IPv4/IPv6、用户名注入地址、unknown/缺失地址等 12 条合成测试。无有效来源 IP 的行不能用于封禁；遇到此情况先查 xrdp 来源地址传递，不能从用户名猜 IP。

OpenSSH 10 可能使用 `sshd-auth` 或 `sshd-session` 日志前缀。安装器覆盖三种前缀与 journal 匹配；在目标机实际安装版本上检查失败记录是否被识别，成功记录不应匹配。不要反复制造真实密码失败来代替过滤器测试。

真实动作验收：

```bash
sudo python3 scripts/verify-fail2ban.py --ssh-port 22 --xrdp-port 13389
```

验证器使用未被占用的文档测试地址，检查两个 jail 的 IPv4/IPv6 ban ticket、nft 集合、精确目标端口及阻断 verdict，再清理自己添加的地址。它会跳过现有封禁、连接地址和本机地址。通过表示 action 和内核规则正确；日志识别、云防火墙及远端网络可达性仍分别验证。异常时按输出的具体 jail/IP 清理，不能全局解封。

```bash
sudo fail2ban-client set remote-xrdp unbanip DOCUMENT_TEST_IP
```

## 可选 SSH 仅公钥登录

只在用户明确要求时执行；普通桌面部署保留当前 SSH 认证政策。

1. 保持原管理连接；用 `ControlMaster=no`、`ControlPath=none`、`PreferredAuthentications=publickey`、`BatchMode=yes` 建立新的独立连接。没有独立公钥成功证据就不禁用密码。
2. 检查所有 Include 和 Match，备份配置。按实际配置优先级设置 `AuthenticationMethods publickey`、`PasswordAuthentication no`、`KbdInteractiveAuthentication no`。保留 `UsePAM yes`，sudo/xrdp 仍使用 Linux 密码。
3. 执行 `sshd -t`，用 `sshd -T -C user=…,host=…,addr=…` 检查实际用户/来源的有效值；不要仅看新文件内容。
4. reload SSH 后重复独立公钥登录。只有需要确认禁用密码时才做一次无公钥拒绝探测，防止触发真实账号封禁。验证失败时从保留的管理连接恢复备份。

## 维护

保留每次备份目录、软件源、配置、精确包版本和验证记录。安装脚本是首次初始化入口，不能用于日常 apt 更新。剪贴板本地 diversion 的升级策略见 [剪贴板文档](clipboard.md)。

需要重启服务时先检查会话；普通断开保留桌面，Log Out 才结束。故障修复优先恢复现有桌面，重置/杀会话应依据用户授权和实际进程状态。服务配置重启成功后继续确认客户端实际结果。
