# 世界消息地图预留界面

入口：共享导航的「世界消息」→ `/world/`。消息详情使用 `/world/news/<id>/`，由 Django 提供同一模板，前端按路径渲染，可刷新或直接打开。

当前明确为演示模式：初始八条消息与「模拟一条推送」的消息均为虚构，不是实时新闻、真实供应商信息或 Agent 输出。不连接新闻服务、不启动模型，不修改 CRM、邮箱或聊天流程。页面只读取本地地图资源，不包含私有客户数据；未来接入授权消息时必须由后端实施身份与数据隔离。

## 交互

- 拖动/缩放地图；点击单条气泡打开摘要，再次点击该消息气泡或「阅读完整消息」进入详情。
- 同坐标或屏幕上相邻的消息合并显示数量；先展开组内列表，再选取具体消息，也可放大区域。
- 行业筛选同时更新地图、列表和统计；摘要可以关闭或按 Escape 退出。
- 窄屏使用可滚动的底部摘要面板；列表按钮与标记均可使用键盘。
- 详情返回恢复行业与选中消息；浏览器返回和初始消息直达刷新都支持。
- 显式模拟推送通过统一 `news.upsert` 入口更新视图，并保留用户筛选和地图视角。重复消息不重复计数。模拟到达的消息只保存在本页内存，刷新后不保留；未知 ID 显示消息未找到。

## 文件关系

| 文件 | 职责 |
|---|---|
| `frontend/world.html` / `assets/world-news.css` | 页面骨架和响应式样式 |
| `assets/world-news.js` | 页面状态、路由、筛选与公开 `receiveWorldNews(event)` 入口 |
| `assets/world-map.js` | Leaflet 地图和按屏幕距离分组的标记 |
| `assets/world-feed.js` | 独立 `NewsFeed`、字段校验与版本幂等 |
| `assets/world-demo.js` | 明确标记的虚构夹具 |
| `assets/world-countries.geojson` / `assets/vendor/` | 本地底图、Leaflet 与许可说明 |

## 后续 Agent 推送契约

传输与数据源分离。后续 SSE/WebSocket 适配器应先通过授权 API 读取快照，构造 `new NewsFeed('live', items)`，再把事件交给 `feed.receive(event)`。本次只实现数据接收入口，不创建不存在的 API、EventSource 或自动重连策略。真实接入还需替换页面演示数据源和演示状态文案、移除模拟按钮，并补充加载/连接中断与详情按 ID 查询。

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
