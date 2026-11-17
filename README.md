# Xfce + xrdp 部署技能

一个可通过 SSH 部署和验收 Linux 远程桌面的 Codex 技能。它将已在 Debian 12、Ubuntu 26.04 上实际使用的流程整理为技能说明、可配置脚本和维护参考。

包含 Xfce 美化、中文输入法、Firefox ESR、xrdp TLS、macOS Windows App 连接文件、文本剪贴板兼容修复和 Fail2ban/nftables 验证。默认不启用音频。脚本支持首次初始化；已有桌面的故障恢复走诊断流程。

## 安装技能

```bash
git clone https://github.com/luo980/xfce-xrdp-skill.git
mkdir -p ~/.codex/skills
ln -s "$(pwd)/xfce-xrdp-skill/skills/deploy-xfce-xrdp" \
  ~/.codex/skills/deploy-xfce-xrdp
```

也可将 `skills/deploy-xfce-xrdp` 整个目录复制到技能目录。已有同名技能时先检查来源。安装路径可按自己的 Codex 配置调整。

在新对话中使用：

```text
使用 $deploy-xfce-xrdp，通过 SSH 给 my-server 部署远程桌面。
使用 13389 公网直连，不需要音频，完成双向剪贴板验收。
```

## 内容

- [技能入口](skills/deploy-xfce-xrdp/SKILL.md)：目标确认、安装、验证与交付。
- `scripts/desktop/`：有备份和首次安装限制的系统配置脚本。
- `scripts/clipboard/`：按发行版和精确源版本锁定的构建、安装、回滚脚本。
- `scripts/install-fail2ban.sh`、`verify-fail2ban.py`：日志规则和真实 nftables 动作验证。
- `scripts/make-rdp-client.py`：生成无密码 RDP 配置及可双击 macOS 入口。
- `references/`：系统差异、软件源、剪贴板、验收、维护和故障排查。

本仓库不包含主机配置、凭据、完整第三方源码或编译产物。运行时应核对目标系统和软件包版本；版本锁不代表对未来版本的兼容承诺。开发检查见 [验证说明](VALIDATION.md)。第三方代码片段的来源及许可见 [归属说明](THIRD_PARTY_NOTICES.md)。
