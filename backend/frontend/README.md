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
| `assets/assistant-markdown.js` / `markdown-it.vendor.js` | 基于固定版本 markdown-it 的安全助手正文渲染，独立 ESM 发行包同源加载 |
| `assets/world-news.js/css` / `world-map.js` | 数据库全球洞察活动、金额地图、详情与行业资讯 |
| `priorities.html` / `assets/priority-board.js/css` | 商机评分、解释、信号和证据；展示结果，不计算算法 |
| `assets/onboarding.js/css` / `company-settings.js` | 个人、公司、产品、方案四步引导及设置；资料通过账号接口持久化 |

顶栏使用 light DOM，沿用全局设计变量和原生链接，不另建路由系统、不查询账户、不产生写入。首页不标记为“社媒情报”；聊天浮窗保持当前页面和产品分区。社媒情报入口对应现有邮件与客户分析；没有增加未接入的社交渠道。

产品顶栏只展示全球洞察、社媒情报和销售业务，已移除“实验数据”入口。内部 `/experiments/` 页面及其数据接口继续供已有联调链接使用；隐藏入口不修改数据或后端权限。

产品导航为 Dashboard、Channels（原 Emails and analysis）、Customers；商机、报价、订单、工单作为 Customers 的子入口。移除左侧 Opportunity Priority（商机优先级）、World news、通知、沟通与资料、设置与协作及旧销售业务分组；邮件客户详情不再显示上方客户导航条。顶部产品分区及业务页面保留原路由，既有数据与功能未删除。

## 修改约定

- 先调整 `design-system.css` 的语义变量；不要在页面里重新定义一套主题色。
- 页面样式只处理对应的布局和状态。保留 DOM ID、`hidden`、原生表单和业务模块事件约定。
- 聊天依据采用外层总开关和内层原文折叠；引用卡片沿用主题变量，原文区独立滚动。测试分别定位两层 `details`，并验证默认收起及展开可读。
- 新组件提供键盘焦点、明确链接与选中状态；需要监听全局事件的组件在移除时清理监听器。
- 保留服务端 QQ 开关、未知数据状态和虚拟来源标识。全球洞察读取数据库，虚拟批次明确标注。
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
node backend/tools/browser_onboarding.cjs
```

除 `browser_world_news.cjs` 外，这些检查使用本地静态资源和模拟 API；全球洞察检查需要已初始化且运行中的本地实验服务，以及显式 `SALESMATE_TEST_URL`（参见 [联调支持](../docs/development-support.md)）。QQ 检查显式模拟开关，不能据此声称真实邮箱外部收发已验证。桌面及手机截图写入忽略的 `backend/artifacts` 目录。

在 `backend` 目录运行 `python tools/check_docs.py` 和 `python tools/check_doc_changes.py --base HEAD --fail-on-review`。现有检查器覆盖 Python；JS、CSS、HTML 的顶部说明与实现一致性需人工核对。

## 2026-09-20 需求界面

注册密码为 8–128 字符，不要求特定字符组合。请求编号保留在错误对象和诊断日志，普通提示不显示。搜索框禁用浏览器历史补全；登录身份变化时清空搜索和列表/聊天内存，旧账号列表响应不再渲染。

新注册账号在下次读取 Session 时进入 `/settings/company/?onboarding=1`。个人、公司、产品、方案各步都可跳过；完成或跳过最后一步后进入社媒情报收件箱。已有账号从 Company Setting 进入编辑，不在迁移时强制引导。公司资料沿用独立版本接口；其他步骤使用 `accounts/onboarding/`，版本冲突保留本页草稿。

产品支持逐条添加/编辑/移除及 UTF-8 CSV 批量导入（下载模板），规格与场景多项用 `|` 分隔。CSV 最多 1 MiB，产品总数最多 200；解析失败不部分导入。规格书和方案支持 PDF / UTF-8 TXT（每份最多 5 MiB），通过登录鉴权的私有 URL 在线阅读或下载。产品和方案行需显式保存；上传成功的文件已经保存到当前账号。

这些资料是独立的用户提供背景信息；本次不把它们注入评分输入、不改变评分算法，不自动创建交易报价、授权 Gmail、调用模型或发送邮件。详情和接口见 [引导资料契约](../docs/onboarding.md)。


## 0919 产品界面补齐

逐项对照与实现边界见 [产品需求验收表](../docs/product-ui-0919.md)。Channel 新增会话气泡、当前客户回复草稿和 Evidence 来源预览；Dashboard/Channels/Customers 去除重复状态栏及指定统计卡。现有全局聊天、评分算法和外部发送确认保持原契约。

新增验收：`node backend/tools/browser_product0919.cjs`（模拟 API、桌面/手机/英文、来源转义、缺失引用及只读交互）。

## 聊天 Markdown

助手的历史与新回答使用 markdown-it 15.0.2 渲染标题、粗体/斜体、删除线、嵌套列表、引用、链接、行内/围栏代码和表格。普通换行保留；长代码和宽表格独立横向滚动，样式沿用主题变量。用户输入与来源证据继续按纯文本展示，API 和存储原文不变。

原始 HTML 不执行；链接仅接受 HTTP、HTTPS、mailto 或解析为这些协议的相对地址，并隔离新标签页。图片显示为描述链接，避免自动请求模型提供的远程资源。当前不提供公式、Mermaid、代码语法高亮或 token 流式修复；未闭合围栏按 CommonMark 作为代码展示。项目仍使用现有完成结果轮询，未调整算法、后端或失败重试语义。

选型：Streamdown 面向 React 的 LLM 流式渲染；当前无 React 和构建链，因此使用 markdown-it 官方独立浏览器 ESM。来源、版本和完整性记录见 [第三方资源](assets/vendor/THIRD_PARTY.md)。新增验收包含在 `node backend/tools/browser_chat.cjs`：格式语义、安全链接/HTML/图片、未闭合围栏、桌面/手机溢出、纯文本用户消息与来源，以及原有聊天生命周期。
