# 发布前验证

验证日期：2026-11-17。

本技能来自已在 Debian 12、Ubuntu 26.04 上完成真实桌面与双向剪贴板验收的部署流程。此次封装后的参数化脚本进行了离线验证，没有为了制作技能重新安装或修改现有服务器。

## 可重复检查

在仓库根目录执行：

```bash
python3 -m unittest discover -s tests -v
python3 skills/deploy-xfce-xrdp/scripts/desktop/test-configure.py
```

已通过 9 项测试，覆盖：

- 两种发行版的完整配置渲染，XML、受管 INI 键、重复 Xorg 参数保留、备份和配置完成标记。
- 桌面参数及目标端口冲突的拒绝路径。
- Shell 语法与无副作用帮助入口、Python 语法。
- RDP 连接参数、文件权限、拒绝覆盖、输入换行与路径/命令注入拒绝。
- nftables IPv4/IPv6、自定义端口、阻断 verdict 和精确测试地址匹配。

技能结构另外通过 Codex `skill-creator/scripts/quick_validate.py` 校验。该工具需要 PyYAML；验证时使用独立临时虚拟环境，没有更改系统 Python。

## 剪贴板源码核验

使用此前保留的官方发行版源码，在隔离临时副本上对两个版本分别验证：

- 源归档三个 SHA256 及 vendor-patched `clipboard.c` 哈希一致。
- Debian 31 项、Ubuntu 5 项供应商 quilt 序列完整且顺序匹配。
- 8 处补丁匹配、重复执行幂等、8 种部分应用状态均被拒绝。
- 命令行原子替换与文件权限保留，失败不改输入文件。

原始源码与二进制未纳入仓库。复现该检查需按 [剪贴板文档](skills/deploy-xfce-xrdp/references/clipboard.md) 取得精确版本的官方源码。

## 独立审查与模拟使用

独立审查覆盖参数传递、登录保护、配置备份、版本锁、安装回滚、第三方代码归属和敏感数据残留。三个模拟任务通过：

1. Ubuntu 新部署：正确使用指定公网 RDP13389 和非默认 SSH2222、关闭音频并保留系统时区。
2. Debian 剪贴板故障：先区分自动按键与实际复制失败，拒绝向 `deb12u4` 安装锁定 `deb12u3` 的补丁，保留正在使用的会话。
3. Bash PATH 缺失：检查实际登录入口与覆盖顺序，保留用户 export 和交互 guard。

部署到新机器时，仍按技能完成真实 RDP、TLS、双向剪贴板、Fail2ban 日志识别与内核动作验收。
