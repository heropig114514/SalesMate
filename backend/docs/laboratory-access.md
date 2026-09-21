# 算法联调的公开实验模式

本模式按项目负责人的明确选择开放所有业务数据，包含非 KGSEED 数据。它由 `LAB_OPEN_ACCESS=True` 单独控制，与 Django DEBUG、用户角色和虚构批次标记无关。代码默认关闭；本次实验服务器启用。

## 调用方式

网页直接访问 `/business/`、`/experiments/`，不需要登录。已有登录用户、新账号和匿名访问者均能查看跨账号业务记录。默认列表仍分页，归档记录需使用 `archived=all` 或页面归档筛选。

HTTP 直接调用原有 `/api/v1/` 路径，不需要 Cookie、Authorization、CSRF 或独立 Tool 凭证。公开示例：

```bash
curl https://milkdragon.dev/api/v1/sales/directory/
curl https://milkdragon.dev/api/v1/agent-tools/catalog/
curl -X POST https://milkdragon.dev/api/v1/agent-tools/call/ \
  -H 'Content-Type: application/json' \
  -d '{"name":"products.list","arguments":{}}'
```

模式开启时 API 响应含 `X-Lab-Open-Access: true`；`/api/v1/session/` 返回 `lab_open_access: true`。

现有 MCP 是本地 stdio 桥，内部转发同一套 HTTP Tool API，不是单独部署的远程 MCP URL。更新到本次代码后，安装 `integrations/salesmate_tools/requirements.txt`，在项目根目录配置：

```json
{
  "mcpServers": {
    "salesmate": {
      "command": "python",
      "args": ["-m", "integrations.salesmate_tools.mcp_server"],
      "env": {"SALESMATE_TOOLS_URL": "https://milkdragon.dev"}
    }
  }
}
```

Python 客户端也可直接 `ToolClient("https://milkdragon.dev")`。实验模式目录会声明 `idempotency_required: false`，MCP 不再要求每次写入必须提供幂等键。若需要可靠识别重复提交，仍可显式提供 UUID；已有键的内容冲突仍会拒绝，不自动重试。

## 身份与数据归属

身份只用于数据归属和日志，在本模式下不证明调用者身份。选择顺序为：公开 `X-Lab-User: tst1` 请求头、既有登录会话、可识别 Agent/Tool 凭证的原 owner、默认 `algorithm-lab` 账号。

默认实验账号首次使用时创建，密码不可用。可通过 `LAB_DEFAULT_USER` 改名。MCP 可用 `SALESMATE_TOOLS_USER=tst1`，Python 客户端可用 `ToolClient(url, user="tst1")`，均无需密码或令牌。

已有业务记录编辑保留原 owner，审计记录写入操作者。普通新建记录归属所选身份；共享实验接口的新记录仍归属批次原 owner。单账号资料（本公司设置、销售画像、引导资料）及 Worker 队列按所选身份定位，可用公开身份头选择账号。Worker 队列仍按账号领取，避免改变任务执行语义。

## 已放宽和保留的规则

| 项目 | 实验模式 |
| --- | --- |
| 浏览器、CRM Agent、Tool/MCP 业务鉴权 | 免登录；无效旧凭证不阻止业务访问 |
| 客户、邮件、业务记录、知识、会话、附件 | 开放跨账号访问，包括非 KGSEED 数据 |
| 所有者/团队管理权限 | 普通业务维护不再阻止跨账号操作 |
| If-Match、Tool revision、实验 expected | 可省略；不检查过期版本/旧指纹 |
| Tool scope、到期与撤销检查 | 不生效，发布完整工具目录 |
| Tool 内部管理提案确认 | 新调用直接执行，如团队维护和记录归档 |
| Agent L2/L3/L4 保存租约 | 无租约头可直接提交；显式提供的 Worker 租约仍核验运行状态 |
| 删除 | 共享虚构记录通过 experiments.delete 删除；普通资源继续使用既有归档/删除接口，不新增任意表删除入口 |
| 数据结构、金额、外键、引用、状态 | 保留业务正确性检查；运行中的任务和冻结单据不能随意篡改 |
| 外部发送和日历动作 | 保留既有准备、明确确认和执行流程；可用公开身份选择所属账号 |
| 密钥和管理后台 | 不公开密码/OAuth 密钥；返回 Gmail OAuth 凭据的 mailbox-syncs/claim 仍要求 Agent 机器凭据；Django admin 仍要求管理员登录 |

登录/注册自身的密码及 CSRF 流程保留，但使用业务 API 不必经过这些流程。邮箱 provider 的真实授权依然是外部调用的必要条件。聊天 Agent 的工具集合、知识预算、模型提示词及评分参数不因本开关调整；全量工具能力可直接通过 Tool/MCP 使用。

KGSEED 实验视图仍只表示清单登记的虚构记录，不把真实记录冒充为合成数据。实验模式允许普通接口修改其内容，读取返回当前指纹，已经删除的清单行不再返回。正式模式恢复后会重新严格校验清单；通过普通业务入口修改过的虚构行，需要核对并更新清单后再恢复严格展示或执行批次清理。实验专用 CRUD 会同步维护目标行指纹和审计，清理命令本身不自动放宽。

## 恢复正式鉴权

将运行环境的 `LAB_OPEN_ACCESS=False`，重启 Web、CRM/Chat/Sales Worker 和 Celery。无需删除代码或修改数据库迁移；`X-Lab-User` 随即不再授予访问权，Session/Agent/Tool 原鉴权、owner/team 隔离、版本与 Tool scope 检查恢复。开发测试通过配置覆盖同时验证开启和关闭路径。
