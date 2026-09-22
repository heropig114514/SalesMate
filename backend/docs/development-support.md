# 后端与前端联调支持

本次按“缺数据用数据库虚拟占位、放宽算法联调权限”的要求实现。后端提供输入读取、结果存储、业务聚合和页面展示；Agent 的采集、抽取、匹配、评分及推荐算法保持原样。新增接口是为接入需求补充的工程实现，不表示需求文档明确规定了这些接口名称。

## 初始化与开发权限

在项目根目录使用项目 Python 执行：

```powershell
python backend/manage.py migrate
python backend/manage.py seed_development_support --username algorithm-lab
```

命令仅允许 DEBUG 或实验开放模式，首次导入创建独立虚拟客户，不覆盖真实业务记录。同一账号重复运行返回首次清单，不覆盖后续人工/算法修改，也不刷新资讯日期。事务及 `support_seed_completed` 审计保存导入清单。

批次包含 8 个客户、8 条活跃商机、8 个活动、4 条资讯、8 条信号、8 条固定占位评分，以及交易产品和方案文件；个人/公司/参考产品/方案/卖方资料仅在该账号尚无对应资料记录时初始化。活动、资讯、信号、评分使用 `data_source=synthetic`；客户及商机名称带 `【虚拟】`。占位评分不代表算法已计算。真实结果可新建记录，或显式更新已有占位并改为 `data_source=agent`。

本次已修改本机忽略的 `.env`：

```dotenv
LAB_OPEN_ACCESS=True
WORKSPACE_OWNER_ONLY=False
```

两个设置需要同时生效，重启服务后才影响已有进程。代码默认值没有改动，也未部署远端。该模式的业务接口免 Cookie、CSRF、Tool token，开放跨账号业务访问；可省略 If-Match/revision 和 Tool 幂等键。可用 `X-Lab-User: algorithm-lab` 选择资料归属，省略时按既有实验模式选取身份。详见 [实验访问说明](laboratory-access.md)。外部邮箱 OAuth、真实发信/日历确认、数据库关系和金额结构检查仍沿用原契约。

## 算法可直接使用的接口

| 能力 | HTTP（前缀 `/api/v1/sales/`） | Tool/MCP |
| --- | --- | --- |
| 公司、个人、参考产品、方案、卖方画像、交易产品 | `GET seller-context/` | `seller_context.get` |
| 单商机、客户、业务邮件、卖方资料、历史成交订单、信号和最新评分 | `GET opportunity-context/<opportunity UUID>/` | `opportunity_context.get` |
| 活跃商机优先级列表 | `GET priority-board/` | `priority_board.list` |
| 地图活动及分币种聚合 | `GET world/` | `world_insights.get` |
| 信号 CRUD 与归档 | `records/opportunity-signals/` | `opportunity_signals.list/get/create/update/archive` |
| 商机评分 CRUD 与归档 | `records/opportunity-priorities/` | `opportunity_priorities.list/get/create/update/archive` |
| 活动和资讯 CRUD 与归档 | `records/world-events/`、`records/world-news/` | `world_events.*`、`world_news.*` |

单条资源 GET/PATCH 在集合路径后加 `<record UUID>/`；归档沿用通用 command 或对应 Tool。信号/评分列表支持 `opportunity` 筛选。正式模式仍需要 Session 或已授权 Tool 凭证、版本控制；Tool 凭证通过统一 `/api/v1/agent-tools/call/` 调用，不直接替代业务页面的 Session。新工具需要新授权范围；目录、Python SDK 和 stdio MCP 共用注册表。

最小评分提交：

```json
{
  "name": "opportunity_priorities.create",
  "arguments": {
    "data": {"opportunity": "实际商机UUID", "priority_score": 82}
  }
}
```

发送到 `POST /api/v1/agent-tools/call/`。最小信号为 `opportunity` 和 `signal_type`；`signal_value` 可提交任意 JSON。公司及 owner 从商机关联关系确定，调用方不需重复填写。既有记录不能搬移到另一商机；为另一商机创建新记录即可。

评分可选字段：`score_breakdown`、`top_reasons`、`evidence`（JSON），`recommended_next_action`、`scored_at`、`score_version`、`data_source`。信号可选字段：`signal_value`、`confidence`、`source_type`、`source_id`、`evidence_text`、`detected_at`、`status`、`data_source`。不限制信号枚举、原因条数、分项结构或算法权重；分数为可空 0–100 整数，置信度为可空 0–1 数值。未知结果保留空，不强制补零。

每次评分可追加历史记录。最新结果按 `scored_at`、创建时间、ID 降序选取；优先级列表按最新分数降序，未评分排后。该记录只表示已提交结果：输入变化后不会自动重算或使其失效，需要算法侧决定何时提交新结果。原公司级 L4 `score-v2` 协议、35/35/30 算法及 Worker 调度未更改。

## 页面和边界

- `/world/` 从数据库读活动、资讯、客户国家和商机金额；同坐标金额按关联商机去重，币种分开显示，未知金额不合成数值。虚拟活动有明确标识；来源缺失允许占位，已有来源链接仍检查 HTTPS 和无凭证。
- `/priorities/` 展示活跃商机、最新评分、分项、原因、建议、结构化信号和证据。只在已授权业务邮件中定位原文；提交的信号证据会注明尚未关联原文。
- `/business/#opportunity-signals` 与 `#opportunity-priorities` 使用既有通用业务表单维护记录。
- 资讯仅展示近 14 天最多 4 条，日期过期不会偷偷刷新；活动和资讯需要显式维护。初始化不是后台新闻采集任务。
- 行程按钮导出数据库时间对应的 ICS；邀约按钮提供带本人署名的可编辑模板。没有调用 Agent 生成文案，没有发送邮件或创建外部日历事件。
- 上传文件继续沿用 PDF/TXT 和 CSV 产品导入支持，本次未新增 Office/OCR/Excel 解析或网站资料抓取。

## 验证命令

```powershell
python backend/manage.py test tests.integration.test_development_support --keepdb --noinput
python backend/manage.py makemigrations --check --dry-run
```

浏览器检查需要已初始化数据库和运行中的本地实验服务：设置 `SALESMATE_TEST_URL=http://127.0.0.1:8011`、`SALESMATE_PLAYWRIGHT_MODULE`、`SALESMATE_BROWSER_PATH`，执行 `node backend/tools/browser_world_news.cjs`。它读取实际本地 API、检查桌面/手机页面、导出、证据和请求失败；不验证真实 Agent 或外部服务。

在 `backend/` 运行 `python tools/check_docs.py` 及 `python tools/check_doc_changes.py --base HEAD --fail-on-review`，并人工核对前端说明。代码尚未提交时，这些检查不证明 Git 提交原子性。
