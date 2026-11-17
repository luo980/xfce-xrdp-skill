# 客户端连接与验收

## 生成连接文件

```bash
python3 scripts/make-rdp-client.py --host desktop.example.net \
  --port 13389 --name workstation --user desktopuser \
  --output-dir "$HOME/Desktop"
```

生成 `workstation.rdp`（0600）和 `Connect-workstation.command`（0700）。拒绝覆盖同名文件；两文件放同一目录。双击 command 通过 macOS `open -a 'Windows App'` 打开配置；它不创建 SSH 隧道、不杀进程、不重置会话。

RDP 开启动态分辨率与文本剪贴板；默认关闭音频、麦克风、摄像头、磁盘、打印机及其他设备重定向。可选 `--audio` 只修改客户端播放设置，不能代替服务器音频支持。用户名可省略，密码始终不保存。

若服务器仅监听 loopback，先创建持久转发。例如本地 23389 转发远端 13389：

```bash
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
  -L 127.0.0.1:23389:127.0.0.1:13389 my-server
```

然后生成 `--host 127.0.0.1 --port 23389` 的客户端配置。检查本地端口是否已被占用；转发进程退出后 RDP 入口就不可用。用户明确指定直连时不创建隧道。

## 服务和端口

```bash
systemctl is-active xrdp xrdp-sesman fail2ban
systemctl is-enabled xrdp fail2ban
ss -lnt
ss -lx
openssl x509 -in /etc/xrdp/cert.pem -noout -subject -fingerprint -sha256
```

按实际版本检查 sesman TCP/Unix socket；依赖启动的 sesman 单独显示 disabled 不等于故障。客户端端口探测成功只证明 TCP 连通，之后必须做实际 RDP 登录。

## 实际桌面

通过原生 UI 工具或用户操作登录，观察主题、底部面板、浏览器、输入法和窗口大小变化。读取服务日志确认实际 TLS 版本、会话创建与分辨率协商。不要把命令执行成功或应用已打开当作最终可用。

检查当前用户的 `xfce4-session`、`xfwm4`、`xfce4-panel`、`fcitx5`、`xrdp-chansrv`，并关联当前 display。时间显示要与系统时区一致；不要从 DNS、浏览器定位或 IP 地理库猜机房时区。

## 双向文字剪贴板

用临时文本 `RDP-check-中文-123`，每个方向单独验证：

1. Mac 文本编辑器选中后 Cmd+C，远端 Mousepad 用菜单 Paste。
2. 远端修改文本，菜单 Select All / Copy，然后 Mac 文本框 Cmd+V。

有些自动化 paste 会临时替换并恢复 Mac 剪贴板，有些远端快捷键会被客户端拦截；菜单动作或用户手动复制更容易区分自动化错误与协议问题。只查看 `cliprdr` 日志不能替代实际粘贴结果。用户确认可作为人工验收结果写入记录。

安装修复版后必须新登录并核对进程：

```bash
pgrep -a -u desktopuser xrdp-chansrv
sudo sha256sum /proc/CHANSRV_PID/exe
sha256sum /usr/sbin/xrdp-chansrv
```

进程 PID 先从实际会话获取，不要照抄旧记录。两个哈希应与本次构建候选一致，再重复双向复制。

## 最终记录

记录系统/包版本、连接方式/端口/用户名、备份路径、证书指纹、补丁源版本/哈希、服务及 Fail2ban 检查、真实 GUI 结果、用户人工确认和待办。音频未要求则写关闭，不需要试听。已有用户会话继续保留。
