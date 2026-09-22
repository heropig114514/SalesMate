# 基础信息与首次引导

本次实现需求文档的界面修复、四步资料录入和全球洞察展示。跟进优先级公式、权重、阈值和算法输入保持原样。

## 保存与隔离

新注册账号建立 `SalesSetup(completed=False)`；Session 返回 `onboarding_required`。完成或跳过最后一步将其设为 true 后进入 `/#inbox`。历史账号不在迁移中自动生成引导状态，可从 Company Setting 补填。

| 路径（`/api/v1/` 下） | 操作 | 行为 |
| --- | --- | --- |
| `accounts/company-profile/` | GET/PATCH | 既有公司资料；新增可空 `size_band`，保留公司名必填和版本校验 |
| `accounts/onboarding/` | GET | 当前账号的 personal、products、solutions、completed、revision 和附件元数据；读取不建档 |
| `accounts/onboarding/` | PATCH | 提交某一步完整对象或数组，携带 `If-Match`；拒绝未知字段，冲突返回 409 |
| `accounts/onboarding/documents/` | POST multipart | 只接收 `file`；最多 5 MiB，PDF 签名或 UTF-8 TXT 校验；返回 id/name/content_type |
| `accounts/onboarding/documents/<uuid>/` | GET | 当前账号私有在线预览；加 `?download=1` 强制下载；匿名 403，其他账号 404 |

所有接口沿用 Session、CSRF 和 owner 约束。文件保存在数据库 `BinaryField`，没有公共媒体 URL；响应启用 `nosniff`、禁止缓存及 CSP sandbox。上传会立刻保存文件，产品和方案列表的关联仍需要用户显式保存。移除列表行只修改关联，不删除已上传文件。

个人字段：name、title、email、phone、regions[]、industries[]。填写邮箱不代表 Gmail 已授权。公司行业与收件箱的四项行业枚举一致，规模使用已有区间值；历史自由文本值仍保留。

每个产品包含稳定 UUID id、name、category、specifications[]、price_min/price_max（未知为 null）、currency、scenarios[]、document_id（可为 null），以及可空 linked_product_id（显式关联本人未归档交易产品）。保存/编辑须保留已有 id 和关联；旧资料 ID 在读取时确定性补齐、对应数组保存时持久化。价格是参考资料，不是实际报价；下限不能大于上限。支持 SGD/USD/CNY/EUR/JPY，最多 200 行。

每个方案包含稳定 UUID id、name 与 document_id，最多 100 行。所有文件引用必须属于当前账号。PDF/文本可在线读取；没有接入其他 Office 文件解析或自动模型阅读。算法调用方可使用 [软件辅助工具](software-support-tools.md) 按条增删改查、上传和分块读文件，无需浏览器 Session；上述原浏览器路由继续使用 Session。

CSV 模板字段为 `name,category,specifications,price_min,price_max,currency,scenarios`，多规格/场景用 `|` 分隔，支持引号和引号内换行。浏览器最多读取 1 MiB，先验证全部行再加入草稿，最终保存仍通过后端字段及价格验证。

## 密码及页面状态

密码规则保留最少 8 字符，注册接口上限为 128；取消常见密码、纯数字和用户名相似性限制。密码哈希、CSRF、普通账号权限和重名校验不变。请求编号保留在服务端响应与浏览器错误元数据/诊断日志，不拼入面向用户的提示。

搜索框设置 `autocomplete=off`；账号切换时清空筛选、公司列表、详情和助手内存，过期的账号列表响应不再更新页面。这不删除浏览器历史，也不改变后端数据授权规则。

## 数据库及验证

新增迁移 `accounts.0003_salessetup_companyprofile_size_band_setupdocument` 只建表及增加可空规模字段。部署或启动前按项目迁移流程执行 `python manage.py migrate`。新增资料不注入既有评分输入，避免改变当前算法行为与结论。

后端检查：`python manage.py test tests.integration.test_onboarding tests.integration.test_company_profile tests.integration.test_registration tests.contracts.test_schema`。这些测试使用隔离数据库，覆盖持久化、版本冲突、文件格式/大小、账号隔离、CSRF 和注册引导生命周期。

浏览器检查：`node backend/tools/browser_onboarding.cjs`、`browser_workspace.cjs`、`browser_i18n.cjs` 使用真实 HTML/JS 与模拟业务 API。`browser_world_news.cjs` 已改为读取实际本地实验数据库，须指定本地服务地址并初始化占位数据，见 [联调支持](development-support.md)。这些检查均不代表 Gmail、真实新闻采集、AI 或外部日历已完成联调。
