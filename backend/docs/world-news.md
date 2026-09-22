# 全球洞察活动界面

入口 `/world/`，新闻详情 `/world/news/<UUID>/`。页面已读取后端数据库；原有静态演示源与未接入的浏览器消息推送模块已删除。虚拟数据须由 `seed_development_support` 显式入库，页面不会自动创建或在接口失败时回退演示数据。初始化及算法接入见 [联调支持](development-support.md)。

## 数据和交互

- 活动从 `/api/v1/sales/world/` 显式读取所有分页；资讯从 `records/world-news/` 读取近 14 天最多 4 条，详情直接按 ID 查询。
- 客户资料的明确国家字段决定地图高亮。无法识别的国家统计为缺项，不依据邮箱或地址猜测。
- 活动通过 `opportunity_ids` 关联真实数据库商机。金额只取未归档客户的未归档活跃商机，按 UUID 去重并分币种累加。未知金额不伪造为零；页面支持切换币种，不做汇率换算。
- 同国家、同坐标的活动共用地图气泡，后端为该位置全部匹配活动计算商机关联并集，避免重复计额；活动详情显示单个活动关联金额。前端类型/时间筛选隐藏活动，地图金额仍表示该位置快照中的关联在手总额。
- 正金额气泡面积与当前显示金额成比例，最大直径为 62 像素；最大值随筛选结果调整。圆心严格锚定活动经纬度，城市标签不参与圆心布局。中心点（多活动时为数量）指示实际位置；选中、悬停或键盘聚焦时显示该城市所选币种的聚合金额，选中不会放大气泡面积。零金额和未知金额保留位置标记并使用不同提示。
- 类型、30 天/本季度、国家筛选联动活动列表和地区数量。全球/亚太/欧洲切换调整地图中心及缩放。URL 的 `type/time/country/view/currency/event` 保存选择。
- 页面展示来源标签；`synthetic` 显示虚拟占位。已入库资讯使用原发布时间，过期显示空态，不自动刷新日期。
- 「加入行程」导出实际起止时刻的 UTC ICS，文本按规范转义和折行，虚拟活动标题带 Synthetic。未连接外部日历。
- 「生成客户邀约邮件」打开可编辑模板，署名取所选身份的个人资料；无自动收件人、模型调用或发送。
- 所有业务文本均转义。请求失败显示错误且无静态回退；没有 SSE/WebSocket 或自动重连。

## 文件关系

| 文件 | 职责 |
| --- | --- |
| `frontend/world.html`、`assets/world-news.css` | 页面骨架与响应式布局 |
| `assets/world-news.js` | 数据库分页、筛选、详情、日历及邀约模板 |
| `assets/world-map.js` | 真实国界、客户国家高亮、金额气泡及视角 |
| `assets/world-countries.geojson`、`assets/vendor/` | 本地底图、Leaflet 及许可 |
| `apps/sales/world.py` | 权限内活动查询、商机去重及币种聚合 |
| `apps/sales/insights.py` | 活动资讯的基础关系、来源和时间校验 |

活动和资讯由原通用 CRUD 或 `world_events.*` / `world_news.*` Tool 维护，字段见 [软件辅助接口](software-support-tools.md)。实验模式或显式 synthetic 记录允许空来源；已填来源必须为无凭证 HTTPS URL，服务端不访问该地址。

## 验证

`tests.integration.test_development_support` 验证实际数据库聚合、占位幂等和正式/实验权限。使用本地运行的实验服务、显式 `SALESMATE_TEST_URL` 及 Playwright/Chrome 环境运行 `node backend/tools/browser_world_news.cjs`，检查数据库页面、国家筛选、同位置聚合、ICS、模板、新闻详情、评分证据、手机溢出及 503 错误态；不连接真实 Agent、新闻、邮箱或日历。

`node backend/tools/browser_world_map.cjs` 使用相同 Playwright/Chrome 环境，在隔离静态服务器检查实际 Leaflet 投影与 DOM 圆心、4:1 金额面积比例、同坐标聚合、零/未知金额、鼠标/键盘交互以及视角、缩放和手机尺寸变化；其金额为浏览器夹具，不代表线上数据库或外部 Agent 已验证。

在 `backend/` 执行 `python tools/check_docs.py` 与 `python tools/check_doc_changes.py --base HEAD --fail-on-review`。Python 之外的 JS/CSS/HTML 顶部说明和目录人工核对。
