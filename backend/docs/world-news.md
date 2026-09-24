# 全球洞察活动界面

入口 `/world/`，新闻详情 `/world/news/<UUID>/`。页面已读取后端数据库；原有静态演示源与未接入的浏览器消息推送模块已删除。虚拟数据须由 `seed_development_support` 显式入库，页面不会自动创建或在接口失败时回退演示数据。初始化及算法接入见 [联调支持](development-support.md)。

## 数据和交互

- 活动从 `/api/v1/sales/world/` 显式读取所有分页；资讯从 `records/world-news/` 读取近 14 天最多 4 条，详情直接按 ID 查询。
- 客户资料的明确国家字段决定地图高亮。无法识别的国家统计为缺项，不依据邮箱或地址猜测。
- 人工、Agent 和 synthetic 的资讯/活动都是共享事实；正式模式及 `WORKSPACE_OWNER_ONLY=true` 下，所有已登录员工可读取，匿名仍拒绝。仅 owner 可更新/归档；没有隐式授予团队管理员新的权限。实验开放模式沿用既有读写规则。
- 活动通过 `opportunity_ids` 关联真实数据库商机；所有列表、详情、地图和 Tool 输出均只返回访问者有权读取的 ID。客户名称和金额只取访问者可见、未归档客户的未归档活跃商机，按 UUID 去重并分币种累加。未知金额不伪造为零；页面支持切换币种，不做汇率换算。
- 活动标题、description、onsite、suggested_actions 是公共内容，不是私人备注区；包含内部客户信息的人工历史内容需在正式共享发布前核对。后端不会用关键词猜测或自动改写原文。
- 地图币种选择仅列出活动 `map_amounts` 中实际存在的币种，不使用其他无关商机的币种。气泡面积仍只按所选币种计算；标签保留该位置所有已知币种金额，缺少所选币种时明确注明（例如 `No USD amount`），仅无任何已知金额时显示 `Amount unknown`。不把其他币种金额用于所选币种面积，也不自动换汇。
- 同国家、同坐标的活动共用地图气泡，后端为该位置全部匹配活动计算商机关联并集，避免重复计额；活动详情显示单个活动关联金额。前端类型/时间筛选隐藏活动，地图金额仍表示该位置快照中的关联在手总额。
- 正金额气泡面积与当前显示金额成比例，最大直径为 62 像素；最大值随筛选结果调整。圆心严格锚定活动经纬度，城市标签不参与圆心布局。中心点（多活动时为数量）指示实际位置；选中、悬停或键盘聚焦时分币种显示该城市已知的聚合金额，选中不会放大气泡面积。零金额和未知金额保留位置标记并使用不同提示。
- 气泡填充不透明度为普通状态 20%、选中状态 30%，保留清晰边框和中心标记，避免填充遮住底图及相邻点位。
- 类型、30 天/本季度、国家筛选联动活动列表和地区数量。全球/亚太/欧洲切换调整地图中心及缩放。URL 的 `type/time/country/view/currency/event` 保存选择。
- 页面展示来源标签；`synthetic` 显示虚拟占位。已入库资讯使用原发布时间，过期显示空态，不自动刷新日期。
- 「加入行程」对 `datetime` 导出实际时刻的 UTC ICS，对 `date` 导出 `VALUE=DATE` 全天日历。界面显示包含末日的 `starts_on/ends_on`，ICS 的结束日排除；来源 10 月 27—29 日显示到 29 日，日历 DTEND 为 30 日，不展示 UTC 中午占位钟点。文本仍转义和折行，虚拟活动标题带 Synthetic；未连接外部日历。
- 「生成客户邀约邮件」打开可编辑模板，署名取所选身份的个人资料；无自动收件人、模型调用或发送。
- 所有业务文本均转义。请求失败显示错误且无静态回退；没有 SSE/WebSocket 或自动重连。

## 文件关系

| 文件 | 职责 |
| --- | --- |
| `frontend/world.html`、`assets/world-news.css` | 页面骨架与响应式布局 |
| `assets/world-news.js` | 数据库分页、筛选、详情、日历及邀约模板 |
| `assets/world-dates.js` | 日期精度、日期筛选边界与全天 ICS 属性 |
| `assets/world-map.js` | 真实国界、客户国家高亮、金额气泡及视角 |
| `assets/world-countries.geojson`、`assets/vendor/` | 本地底图、Leaflet 及许可 |
| `apps/sales/world.py` | 权限内活动查询、商机去重及币种聚合 |
| `apps/sales/insights.py` | 活动资讯的基础关系、来源和时间校验 |
| `apps/sales/insight_dates.py` | 兼容现有 Agent 明确日期占位协议，不修改 Agent |

活动和资讯由原通用 CRUD 或 `world_events.*` / `world_news.*` Tool 维护，字段见 [软件辅助接口](software-support-tools.md)。实验模式或显式 synthetic 记录允许空来源；已填来源必须为无凭证 HTTPS URL，服务端不访问该地址。

## 兼容、去重和发布

- 新增可写 `time_precision=date|datetime`，默认 datetime。date 使用 UTC 午夜或中午的一致边界承载日期，`ends_at` 的 UTC 日期为排除式边界；只读 `starts_on/ends_on` 为包含末日的来源日期，普通 datetime 输出 null。
- Agent 代码和载荷不变。仅当 `data_source=agent`、description 包含完整独立行 `来源仅提供日期；起止钟点是系统占位值，请以来源页为准。`、起止均为 UTC 中午且递增时，后端兼容适配为 date。完整标记与时间不符会拒绝，不猜测其他文章或午夜活动的精度。迁移为历史明确标记记录补精度，不改写原时间。
- 数据库对非空 Agent 新闻来源 URL 唯一，对 Agent 活动的 URL + starts_at 联合唯一；人工/synthetic 不受采集去重约束。归档不释放唯一性；重复返回 409，不覆盖原记录。URL 按保存字符串比较，不合并重定向、不同查询参数或近似标题；现有 Agent 已负责其 URL 规范化。
- 当前 Agent 会把来源相同的记录跳过（包括归档），不会自动更新原资讯；活动不同届次虽然后端允许保存，现有 Agent 的 URL 去重仍可能跳过，本文不宣称已改变其采集策略。并发写入遇到 409 时，现有 Agent 会计入 item_errors，后端不伪装为成功。
- 发布需应用 `sales.0009_shared_insights`。迁移先核对重复，存在重复则明确失败，需人工确认处理后重跑；不会自动删除、合并或归档。部署前备份按项目既有流程执行。本次代码修改不代表线上已迁移。

定时任务部署与凭证轮换见 [采集服务运维](world-insights-operations.md)。

## 验证

`tests.integration.test_development_support` 验证实际数据库聚合、占位幂等和正式/实验权限。使用本地运行的实验服务、显式 `SALESMATE_TEST_URL` 及 Playwright/Chrome 环境运行 `node backend/tools/browser_world_news.cjs`，检查数据库页面、国家筛选、同位置聚合、ICS、模板、新闻详情、评分证据、手机溢出及 503 错误态；不连接真实 Agent、新闻、邮箱或日历。

`python backend/manage.py test tests.integration.test_shared_insights tests.integration.test_support_tools tests.integration.test_development_support --noinput` 在隔离 PostgreSQL 测试库验收双账号共享、全入口关系投影、写隔离、匿名/Tool 限权、日期兼容、历史迁移以及并发唯一性和 409；不会迁移开发库或部署采集任务。

`node backend/tools/browser_world_map.cjs` 使用相同 Playwright/Chrome 环境，在隔离静态服务器检查实际 Leaflet 投影与 DOM 圆心、4:1 金额面积比例、同坐标聚合、半透明填充、零/未知金额、鼠标/键盘交互以及视角、缩放和手机尺寸变化；完整页面的模拟接口另覆盖币种、URL/刷新和空活动，并在 Pacific/Kiritimati 与 America/Los_Angeles 验证日期末日、实际下载的全天 ICS、普通时刻 ICS、邀约日期及手机布局。其数据为浏览器夹具，不代表线上数据库或外部 Agent 已验证。

在 `backend/` 执行 `python tools/check_docs.py` 与 `python tools/check_doc_changes.py --base HEAD --fail-on-review`。Python 之外的 JS/CSS/HTML 顶部说明和目录人工核对。
