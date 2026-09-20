# 全球洞察活动界面

入口：顶部「全球洞察」或侧栏 Global Insights → `/world/`。新闻详情继续使用 `/world/news/<id>/`。

当前是明确标注的演示模式：八个活动、在跟客户和 SGD 商机金额均为虚构；底图和城市经纬度是真实地理数据。不连接实时活动/新闻服务，不修改 CRM 或评分。四条资讯使用固定 2026-09 月日期，按浏览器当天筛选近十四天；快照过期后显示空态，不伪造新的发布时间。

## 界面与交互

- 左侧：活动列表；类型（全部/展会/销售活动）、时间（全部/30 天/本季度）及国家地区筛选取交集。地区数量反映类型和时间条件。
- 中间：本地 Natural Earth 地图；圆面积随当地商机金额变化，高亮国家代表演示在跟客户。新加坡在低精度底图中使用实际坐标标记。同城活动共用气泡及金额，列表可逐条选择。
- 全球、亚太、欧洲切换同时调整地图中心和缩放；窗口尺寸变化重新适配当前视角。
- 右侧：类型、倒计时、日期、城市、关联金额、为什么值得去、现场情况及建议动作。空筛选清空旧详情，页面不做评分或排名。
- 「加入行程」下载 `.ics` 全天活动，结束日期按日历规范采用次日；文件明确标注 Demo，不调用 Google Calendar。
- 「生成客户邀约邮件」打开可编辑的模板草稿，提供复制；不会发邮件、不调用模型、不猜测客户邮箱。
- 下方：近十四天最多四条资讯，按监管/产业/竞争/价格标注，并展示发布时间。
- 查询参数 `type/time/country/view/event` 保存筛选和选择；手机依次显示地图、活动列表、详情与资讯。

## 文件关系

| 文件 | 职责 |
| --- | --- |
| `frontend/world.html` / `assets/world-news.css` | 页面骨架及响应式布局 |
| `assets/world-news.js` | 活动筛选、详情、日历和邀约；资讯详情和 `receiveWorldNews(event)` |
| `assets/world-map.js` | 国界高亮、金额气泡、地图视角及尺寸变化 |
| `assets/world-events.js` | 八个演示活动、国家及资讯类别 |
| `assets/world-feed.js` | 新闻格式校验与版本幂等 |
| `assets/world-demo.js` | 四条固定日期资讯及推送测试夹具 |
| `assets/world-countries.geojson` / `assets/vendor/` | 本地底图、Leaflet 与许可说明 |

`node backend/tools/browser_world_news.cjs` 验证筛选、空态、地图、ICS、草稿、资讯、安全文本和桌面/手机/英文布局；使用静态页面和演示夹具，不代表真实新闻服务已验证。

## 后续 Agent 推送契约

软件辅助层已新增 `world_events.*` / `world_news.*` 工具及 Session 数据接口，字段与接入边界见 [算法侧的软件辅助接口](software-support-tools.md)。下面是现有前端 feed 契约，与数据库字段不完全相同，接入时须显式转换；当前页面仍加载演示源，尚未绑定数据库或建立推送传输。

传输与数据源分离。后续 SSE/WebSocket 适配器应先通过授权 API 读取快照，构造 `new NewsFeed('live', items)`，再把事件交给 `feed.receive(event)`。本次只实现数据接收入口，不创建不存在的 API、EventSource 或自动重连策略。真实接入还需替换页面演示数据源和演示状态文案、并补充加载/连接中断与详情按 ID 查询。

```json
{
  "type": "news.upsert",
  "item": {
    "id": "stable-news-id",
    "version": 1,
    "demo": false,
    "industry": "semiconductor",
    "location": { "name": "新加坡", "latitude": 1.35, "longitude": 103.82 },
    "published_at": "2026-09-19T09:20:00+08:00",
    "title": "由消息源确认的标题",
    "summary": "由 Agent 提供的纯文本摘要",
    "body": ["纯文本正文段落"],
    "sources": [{ "label": "原始报道", "url": "https://example.com/news" }]
  }
}
```

示例地址仅说明结构，不是真实来源。行业枚举为 `semiconductor/metrology/optics/industrial`；ID 为小写字母、数字、连字符；纬度限制在 ±85°、经度 ±180°；时间必须包含时区。真实消息必须带 HTTPS 来源，禁止含 URL 凭证。字段为纯文本，UI 转义或使用 textContent，不渲染上游 HTML。

相同 ID 的更大 version 更新现有记录；旧版本/相同内容重复事件返回 false；相同版本内容不同抛错，不覆盖现场。格式错误显示错误并向调用方抛出。日志只记录阶段、消息 ID、版本和数量，不记录新闻正文。当前不实现删除、断线补偿、收藏或跨会话已读状态。

## 验证

使用项目现有 Playwright/Chrome 环境变量执行 `node backend/tools/browser_world_news.cjs`，覆盖真实页面与本地地图、筛选、聚合、摘要、详情、刷新、返回、模拟推送、幂等与非法消息，以及小屏布局。测试不连接外部新闻或真实 Agent。

在 `backend/` 运行 `python tools/check_docs.py` 与 `python tools/check_doc_changes.py`。自动检查只覆盖既有 Python 范围，JS/CSS/HTML 的目录和说明须人工核对。依赖来源见 `frontend/assets/vendor/THIRD_PARTY.md`。
