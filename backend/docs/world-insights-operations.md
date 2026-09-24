# 全球洞察采集服务运维

后端存储和页面适配不自动启用采集。Agent 入口、来源、模型参数和定时配置保持原实现；后端无需新增采集 HTTP 接口。正式部署按以下顺序进行。

1. 发布后端与前端静态资源，并按既有发布流程备份和迁移数据库。`sales.0009_shared_insights` 若报告重复，先由维护者核对冲突组；迁移不擅自删改数据。
2. 使用固定采集账号，在已登录 Session 和 CSRF 保护下调用 `POST /api/v1/agent-tools/credentials/` 创建最小权限凭证。沿用既有授权接口，不使用 Agent Worker 凭证或扩大权限的预设。

```json
{
  "name": "world-insights-collector",
  "expires_in_hours": 720,
  "allowed_tools": [
    "world_news.list", "world_news.create",
    "world_events.list", "world_events.create"
  ]
}
```

3. 将返回的一次性 token 安全写入服务器 `/opt/salesmate/shared/world-insights.env`，变量名为 `SALESMATE_TOOLS_URL`（HTTPS 服务根地址）和 `SALESMATE_TOOLS_TOKEN`，仅服务账号可读；不写 Git、日志或终端共享输出。模型仍使用现有百炼环境配置，依赖使用仓库现有 `agent/requirements.txt`（包含 pycountry）。本地测试接口和生产地址不能混用。
4. 先执行 `python -m agent.world_insights --dry-run` 核对来源和日期。它会访问来源、地理编码与模型，但不写后端，不能代替真实入库验证。
5. 在发布服务器由部署维护者安装已有 service/timer：

```bash
sudo install -m 644 agent/deploy/salesmate-world-insights.service /etc/systemd/system/
sudo install -m 644 agent/deploy/salesmate-world-insights.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now salesmate-world-insights.timer
sudo systemctl start salesmate-world-insights.service
sudo journalctl -u salesmate-world-insights.service -n 100 --no-pager
```

timer 沿用每天 UTC 03:00、15:00 和最多 20 分钟随机延迟。首次真实运行会创建共享记录，需要在已准备好正式发布的环境执行；本文不是执行记录。

## 轮换与验收

- 凭证最多有效 720 小时。到期前由维护者为同一个采集账号创建新凭证，更新受保护文件；下一次 oneshot 启动读取新值，验证成功后撤销旧凭证。不要通过改为永久令牌或开放实验模式绕开到期。
- 用 A 作为采集账号、B 作为普通员工，在 `LAB_OPEN_ACCESS=false` 下核对新闻列表/详情、活动列表/地图及 Tool 读取。B 能读公共事实，不能修改 A 的记录，也不能看到 A 私有商机 ID、客户和金额。owner_only 模式同样共享公共事实。
- 监控 `news/events/source_successes/source_errors/item_errors` 和实际入库量。当前 Agent 部分失败可能退出 0，仅看 systemd 成功状态不够；跨账号同源竞争返回 409，不伪装成功。不要将无新记录直接视为故障，来源可能没有满足日期/地点条件的数据。
- 活动原文、现场情况和建议为全员共享内容；私人客户备注不应录入这些字段。当前没有另设全局资讯管理员，更新/归档仍限 owner（实验模式例外）。
- 采集账号的个人数据重置仍遵循既有 owner 删除规则；不要重置这个账号来轮换凭证。共享读取并不把记录所有权转移为无主公共数据。

## 本机 PostgreSQL

此工作区现有 PostgreSQL 位于 WSL `Ubuntu-24.04`。可用项目一键启动入口：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-local.ps1 -WslDistro Ubuntu-24.04
```

该入口会启动 Web 和配置适用的 Worker，详见 [本地开发](local-development.md)。只为测试启动数据库时，可执行 `wsl -d Ubuntu-24.04 -u root -- service postgresql start`，并在测试期间保持一个 WSL 会话；不需要改数据库地址或切换 SQLite。
