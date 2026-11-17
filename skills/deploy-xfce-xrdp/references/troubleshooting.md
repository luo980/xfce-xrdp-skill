# 桌面与 SSH 启动故障

## 停在“正在配置远程电脑”或连接失败

先分层检查：

1. SSH 别名实际目标、客户端目标/端口、隧道模式及转发进程，避免同时使用旧隧道配置和新直连配置。
2. `ss -lnt` 的监听地址、外部 TCP 连通、云防火墙/UFW/nftables 和 Fail2ban 是否封禁当前来源。仅解封已确认被误封的具体地址。
3. `journalctl -u xrdp -u xrdp-sesman`、`/var/log/xrdp.log`、`xrdp-sesman.log` 的 TLS、认证、session/display 创建阶段。
4. Xorg/Xfce/chansrv 进程、用户 Xauthority、D-Bus 和 `.xsession-errors`。已有桌面仍活着时优先恢复；不要直接 `pkill -u USER` 或重新初始化 home。
5. 客户端状态。连接窗口异常时可取消并重新打开；只有确认不会影响其他会话后才退出整个 Windows App。必要时用户手动接管 GUI，后台继续看日志。

旧 display 存活但 sesman 丢失登记时，按实际 xrdp 支持方式恢复对应 display；旧部署中的 `Recover existing desktop (:10)` 是特定配置，不是所有主机都可用的固定菜单。Log Out/重启后旧 display 不再存在，应正常创建 Xorg 会话。

## 剪贴板单向失败

先确认客户端 `redirectclipboard` 和服务 `cliprdr`，排除手动复制与自动化按键差异。检查当前 chansrv 的实际程序和日志，然后读 [精确版本补丁流程](clipboard.md)。不要以替换二进制、重启全部服务作为第一步。

## SSH 公钥失败

确认 `ssh -G` 的用户、地址、端口和 IdentityFile；关闭复用后检查客户端认证结果。服务端查用户 home、`.ssh` 与 `authorized_keys` 所有权/权限、有效 sshd Include/Match、`AuthorizedKeysFile`、账户状态及日志。只处理该用户的明确问题，避免递归改整个 home 的权限。

## SSH 登录未加载 `.bashrc`

仅当用户提出该问题或它影响部署时处理。检查账户默认 shell 和启动文件；敏感 export 的值应遮蔽，不输出整个环境。

真实交互验证用 `ssh -tt HOST` 且不附命令；`ssh -tt HOST 'cmd'` 仍是远程命令。登录后检查 `$0`、`$-`、`shopt -q login_shell`、目标变量是否导出到子进程。

Bash 登录只读取 `~/.bash_profile`、`~/.bash_login`、`~/.profile` 中第一个可读文件。修复有效入口中的加载链，不能创建一个更高优先级文件遮蔽已有配置。三个都缺失时，可创建 POSIX `.profile`：

```sh
if [ -n "${BASH_VERSION:-}" ] && [ -r "$HOME/.bashrc" ]; then
    . "$HOME/.bashrc"
fi
```

需要在交互与 SSH 远程命令间共享的纯 export 放在 `.bashrc` 交互 guard 前。补全、提示符和 ble.sh 只在 `$-` 包含 `i` 时初始化。PATH 追加应避免重复；不要通过删除整个 guard 或全局设置 `BASH_ENV=~/.bashrc` 解决。

备份后运行 `bash -n`/`sh -n`；新交互 SSH 验证子进程继承，非交互 `ssh HOST true` 应无启动文件额外输出。当前 Bash 会话可手动 `source ~/.profile`；保留用户现有环境内容。
