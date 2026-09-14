#!/usr/bin/env bash
# 职责：为专用 SSH 密钥提供唯一的受限部署入口。
# 实现：仅接受一个完整 SHA，交给独立 systemd 服务执行；SSH 断开不会中断服务器上的部署事务。
# 关联：sudoers 只允许本入口；deploy-from-git.sh 再验证 SHA 是仓库当前 main。
# 目录：无函数或类。
# 变量索引：revision 为强制 SSH 命令传入的原始文本，不能含选项或 Shell 片段。
set -euo pipefail
revision="${1:-}"
[[ $# -eq 1 && "$revision" =~ ^[0-9a-f]{40}$ && $(id -u) -eq 0 ]] || exit 64
exec systemd-run --unit=salesmate-deploy --collect --wait --pipe \
    --property=Type=exec /usr/local/sbin/salesmate-deploy-from-git "$revision"
