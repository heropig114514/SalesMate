# 前端结构与视觉维护

页面由 Django 提供，业务逻辑沿用浏览器 ES Modules。产品风格参考仓库外 `frontend_example` 的“全球洞察”和“社媒情报”示例：深色蓝灰背景、紫色强调、细边框及紧凑的信息卡片。

## 共享组件

| 文件 | 职责 |
| --- | --- |
| `assets/design-system.css` | 颜色、语义状态、字号基线、顶栏与交互焦点；所有入口最先加载 |
| `assets/product-header.js` | 原生 `salesmate-header` Web Component，展示产品分区并跟随地址标记当前分区 |
| `assets/workspace.js` / `workspace.css` | 精简左侧导航、真实待办统计、业务页客户上下文、手机布局 |
| `assets/app.js` / `app.css` | 登录、客户邮件卡片、筛选、详情与邮箱管理 |
| `assets/business.js` / `business.css` | 根据服务端字段契约渲染业务表格、编辑及确认表单 |
| `assets/assistant-widget.js` / `assistant-widget.css` | 各工作空间页面共享的悬浮入口、可收起底部聊天条与移动布局 |
| `assets/world-news.css` / `world-map.js` | 全球洞察、本地地图及消息摘要；行业色来自共享 CSS 变量 |

顶栏使用 light DOM，沿用全局设计变量和原生链接，不另建路由系统、不查询账户、不产生写入。首页不标记为“社媒情报”；聊天浮窗保持当前页面和产品分区。社媒情报入口对应现有邮件与客户分析；没有增加未接入的社交渠道。

产品导航为 Dashboard、Channels（原 Emails and analysis）、Customers；商机、报价、订单、工单作为 Customers 的子入口。移除左侧 World news、通知、沟通与资料、设置与协作及旧销售业务分组；邮件客户详情不再显示上方客户导航条。顶部产品分区及业务页面保留原路由，既有数据与功能未删除。

## 修改约定

- 先调整 `design-system.css` 的语义变量；不要在页面里重新定义一套主题色。
- 页面样式只处理对应的布局和状态。保留 DOM ID、`hidden`、原生表单和业务模块事件约定。
- 聊天依据采用外层总开关和内层原文折叠；引用卡片沿用主题变量，原文区独立滚动。测试分别定位两层 `details`，并验证默认收起及展开可读。
- 新组件提供键盘焦点、明确链接与选中状态；需要监听全局事件的组件在移除时清理监听器。
- 保留服务端 QQ 开关、未知数据状态和演示来源标识。全球消息仍是明确标注的虚构演示。
- 入口及有变化的共享模块使用一致的资源版本查询参数，避免部署后加载旧组件。
- 此实现无需新增 npm 依赖或编译步骤；后续若引入框架，可按独立业务页面逐步迁移，继续使用这套语义变量和 API。

## 验证

在项目根目录设置 `SALESMATE_PLAYWRIGHT_MODULE` 为 Playwright 模块路径、`SALESMATE_BROWSER_PATH` 为 Chrome/Chromium 可执行路径，再运行：

```powershell
node backend/tools/browser_workspace.cjs
node backend/tools/browser_chat.cjs
node backend/tools/browser_processing.cjs
node backend/tools/browser_qq_send.cjs
node backend/tools/browser_world_news.cjs
```

这些检查使用本地静态资源和模拟 API；QQ 检查显式模拟开关，不能据此声称真实邮箱外部收发已验证。桌面及手机截图写入忽略的 `backend/artifacts` 目录。

在 `backend` 目录运行 `python tools/check_docs.py` 和 `python tools/check_doc_changes.py --base HEAD --fail-on-review`。现有检查器覆盖 Python；JS、CSS、HTML 的顶部说明与实现一致性需人工核对。
