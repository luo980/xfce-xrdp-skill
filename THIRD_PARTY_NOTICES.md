# 第三方代码归属

`skills/deploy-xfce-xrdp/scripts/clipboard/*/patch-xrdp-clipboard.py` 中的 C 代码上下文来自 [neutrinolabs/xrdp](https://github.com/neutrinolabs/xrdp) 的 `sesman/chansrv/clipboard.c`：

- Copyright (C) Jay Sorg 2009–2012
- Copyright (C) Laxmikant Rashinkar 2012
- Apache License 2.0；完整文本见 [LICENSES/Apache-2.0.txt](LICENSES/Apache-2.0.txt)。

脚本保留原始来源及 SPDX 标记。所做变更是文本格式公告和 CLIPRDR 消息长度兼容修复，具体差异由构建脚本生成并留存。构建时下载的发行版源码和依赖继续遵循各自的许可与归属；本仓库不包含这些完整源码或二进制。
