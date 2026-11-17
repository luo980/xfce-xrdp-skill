# Windows App 文本剪贴板兼容修复

先在真实 RDP 会话测试 Mac→Linux、Linux→Mac 的文字复制。确认客户端开启剪贴板、服务端 `cliprdr=true`、`xrdp-chansrv` 正在运行，再判断是否需要源码补丁。自动化粘贴工具可能会恢复本地剪贴板；结果不明确时用远端编辑器和 Mac 文本框手动验证。

本技能保留两个已审查版本的构建包，运行前核对目标机 `/etc/os-release`、`dpkg --print-architecture` 和 `dpkg-query -W xrdp`。版本不匹配时停止使用该补丁包，保留发行版更新并重新审查源码。

| 目录（相对技能目录） | 系统与架构 | 精确 xrdp 源包版本 | 必须完整应用的供应商 quilt 补丁 |
|---|---|---|---|
| `scripts/clipboard/debian-12/` | Debian 12 amd64 | `0.9.21.1-1+deb12u3` | 31 项，顺序见目录内 `debian-patches-series.txt` |
| `scripts/clipboard/ubuntu-26.04/` | Ubuntu 26.04 amd64 | `0.10.1-4.1ubuntu0.1` | 5 项，包含 `CVE-2025-68670-Buffer-overflow-parsing-domain.patch` |

## 修改内容与边界

`patch-xrdp-clipboard.py` 严格匹配 8 处上下文：两处文本格式公告仅保留 Unicode 文本格式，六处删除 CLIPRDR 声明长度以外的额外 4 字节零。保留 UTF-16 字符串结尾、编码转换和图片处理逻辑。0.9 与 0.10 的格式常量不同，两个脚本分别匹配对应源版本。

补丁脚本允许全部原始状态或全部已修复状态，拒绝部分应用及未知上下文。构建脚本还检查未修改 `clipboard.c` 的固定 SHA256、完整 quilt 应用顺序和精确包版本。仅有版本号相同不足以跳过这些检查。

## 获取源码与构建

首选为目标发行版启用匹配仓库的 `deb-src`，使用其 archive keyring 验证 APT 元数据，随后以普通用户构建。脚本执行 `apt-get --only-source source xrdp=精确版本`，由 `dpkg-source` 应用供应商完整补丁。

构建依赖由管理员安装：

```bash
sudo apt-get install --no-install-recommends \
  build-essential dpkg-dev autoconf automake libtool pkgconf \
  libssl-dev libpam0g-dev libopus-dev libx11-dev libxfixes-dev \
  libxrandr-dev python3 xz-utils
```

Debian 12 分支还需要 FUSE 2 开发库；Ubuntu 分支关闭 FUSE：

```bash
# 仅 Debian 12 执行
sudo apt-get install --no-install-recommends libfuse-dev
```

先查看 APT 安装计划，避免在兼容性不明的系统上套用依赖。两个构建脚本不会安装包、修改服务或替换现有程序。

选择表中匹配的脚本目录。普通用户运行：

```bash
clipboard_dir=/path/to/deploy-xfce-xrdp/scripts/clipboard/ubuntu-26.04
bash "$clipboard_dir/build-xrdp-chansrv.sh" "$HOME"
```

构建目录为 `$HOME/xrdp-chansrv-build.XXXXXXXX/`，输出 `output/` 包含二进制、源码差异、供应商补丁序列、动态库/ELF 检查、来源说明和 SHA256。构建使用发行版硬化参数，内部 xrdp 库静态链接、系统库动态链接；不复用另一主机的二进制。

没有 `deb-src` 时，可从官方归档准备 `source-archives.sha256` 中列出的三个文件，再传第二个参数：

```bash
bash "$clipboard_dir/build-xrdp-chansrv.sh" "$HOME" /path/to/verified-source-archives
```

该回退路径依赖官方 HTTPS 参考文件与固定 SHA256，**不是本次 APT 仓库签名验证**。脚本将实际验证方式写入 `build-info.txt`。仓库不打包源归档；官方位置为 [Debian security xrdp](https://security.debian.org/debian-security/pool/updates/main/x/xrdp/) 和 [Ubuntu archive xrdp](https://archive.ubuntu.com/ubuntu/pool/universe/x/xrdp/)。临时启用的 `deb-src` 配置在保存来源记录后恢复。

## 安装与验收

关闭 RDP 窗口只会断开连接；需要从 Xfce **Log Out** 结束所有 RDP 会话。安装脚本在仍有 `xrdp-chansrv` 进程时拒绝替换。

```bash
sudo bash "$clipboard_dir/install-chansrv.sh" /path/to/build/output
```

脚本核对 OS、架构、包版本、SHA256、PIE、BIND_NOW 和目标机依赖，然后将原程序通过 `dpkg-divert` 保存为 `/usr/sbin/xrdp-chansrv.distrib`，安装本机构建的候选。备份写入 `/var/backups/xrdp-clipboard.XXXXXXXX/`，状态写入 `/var/lib/xrdp-clipboard-fix/installed.json`；不会重启服务。

建立新桌面会话，核对运行中的程序，再实际验证双向复制：

```bash
sudo sha256sum /usr/sbin/xrdp-chansrv
for clipboard_pid in $(pgrep -x xrdp-chansrv); do
  sudo sha256sum "/proc/$clipboard_pid/exe"
done
```

安装文件与新会话运行文件的哈希应一致，并与构建 `SHA256SUMS` 对应。记录两种复制方向的实际结果；服务 active、进程存在或协议格式被接受都不能替代文字粘贴验收。用户不需要音频时，不为剪贴板测试开启音频。

## 回滚与后续更新

先 Log Out，再执行：

```bash
sudo bash "$clipboard_dir/rollback-chansrv.sh"
```

脚本核对本地 diversion、安装状态和自定义文件哈希，恢复当前 `.distrib` 文件；若期间发行版已更新，恢复的是更新后的发行版程序。回滚审计独立保存，不重启服务。

自定义 diversion 会使包更新写入 `.distrib`，正在使用的自定义文件不会自动获得新安全修复。升级 xrdp 前先回滚；升级后重新实测剪贴板，仍有故障时为新源版本重新审查和构建。不要修改版本检查来强行安装旧补丁，也不要为保留补丁而降级安全包。

## 来源

- [xrdp 0.9.21.1 clipboard.c](https://github.com/neutrinolabs/xrdp/blob/v0.9.21.1/sesman/chansrv/clipboard.c) 与 [xrdp 0.10.1 clipboard.c](https://github.com/neutrinolabs/xrdp/blob/v0.10.1/sesman/chansrv/clipboard.c)：上下文与各版本定义。
- [MS-RDPECLIP 3.1.5.1](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpeclip/ed611844-4eb3-4b29-ac8c-2576151b4d0c)：声明长度与消息处理依据。
- 补丁文件保留 Apache-2.0 SPDX、上游作者和来源链接；这里的本地兼容修复不表示上游已合并同一方案。
