# 清空账号内部数据

此功能保留 `accounts_user` 原记录、账号 ID、用户名、密码哈希和登录状态。认证组和身份权限也保留。清理本账号的公司与个人业务资料、引导及文档、客户、邮件副本、提取/分析/评分、销售记录、聊天和草稿、知识库及向量、业务审计、通知、连接和服务凭证、附件。

连接授权清除后需要重新连接邮箱；不会删除外部邮箱中的原邮件，也不会撤回已发送邮件。操作不修改历史备份或运维日志。

## 前端调用

```http
POST /api/v1/accounts/me/reset/
X-CSRFToken: <当前 CSRF token>
Idempotency-Key: <本次操作的 UUID>
```

使用当前 Session 登录态，无须正文、密码、`owner_id` 或 `company_id`。同一操作重试必须复用 UUID。不同 UUID 表示用户再次明确发起清空；后端保留已提交键，较早请求重放也不会删除后来新建的数据。

完成响应为 HTTP 200：

```json
{"status":"completed","generation":1,"owner_id":7,"reset_id":"本次操作 UUID"}
```

| 状态 | 含义与处理 |
| --- | --- |
| 400 | 操作键不是 UUID；修正请求格式。 |
| 403 | 未登录或 CSRF 未通过。 |
| 409 | 当前账号仍有工作执行，或存在他人业务记录对本账号数据的外键引用；本次数据库删除没有提交。 |
| 503 | 文件清理或归属规则未完成；查看日志后显式继续，不自动重试，不显示全部完成。 |

成功响应使用 `Clear-Site-Data: "cache"` 清理浏览器 HTTP 缓存，保留登录 cookie。API 响应统一 `Cache-Control: no-store`。账号请求带 `X-Account-ID`、`X-Account-Data-Version`、`X-Account-Reset-Status` 响应头；前端写请求携带已读取的 `X-Account-Data-Version`，旧页面写入返回 409。既有 Agent/Tool 客户端不强制发送版本头，其旧凭证已被删除。

前端 Profile 下的“清空账号数据”按钮先说明删除范围并要求一次确认。成功后清理 `salesmate:<账号 ID>:` 命名空间下的 Web Storage、Cache Storage、可枚举的 IndexedDB，广播同账号标签页并刷新，释放页面内存、未保存草稿及旧查询结果。当前业务并未将正文保存在 IndexedDB；后续新增存储须遵守这一命名空间约定。其他账号的存储不会删除。

文件清理失败时，身份和会话 GET 仍可访问，其他业务接口暂停。前端根据 `X-Account-Reset-Status: cleaning` 显示“继续清空”入口，刷新后也能恢复。同一页面的操作键保存在 Session Storage，直至缓存全部清理成功。恢复操作只继续未完成的清理阶段，不再重复删除数据库。

## 后端执行与边界

PostgreSQL 账号独占 advisory lock 覆盖清空全过程；HTTP、CRM/聊天工作单元、外部销售动作及到期提醒持有共享锁。清空遇到活跃任务立即返回 409，用户稍后显式重试；不停止其他账号的 Worker。SQLite 只支持原有本地预览，明确拒绝账户重置。

数据库事务先冻结每张表的账号限定主键集合，再以参数化 DELETE 删除。外键仅延迟到事务内最后统一验证，没有关闭约束，也不会由 ORM 级联扩大删除范围。成员、共享授权和依赖通知可以解除；可变负责人关联置空。他人独立业务仍引用待删除记录时，事务整体回滚，须先显式处理共享引用。

业务删除和文件清单、数据版本在同一事务提交。随后删除 `private_uploads/<账号 ID>/` 中清单对应文件，并清理该账号全部数据库会话的业务缓存与 OAuth 状态，保留 Django 认证字段。文件失败时持久状态阻止重新生成业务数据；显式重试可以继续。系统没有单独使用 Django cache 保存业务内容；分析缓存与向量均随本账号数据库记录删除，不使用全局缓存清空命令。

`AccountReset` 仅保留账号协调元数据：数据版本、幂等键、清理状态和未完成文件路径；成功后文件清单为空，不保留业务正文。新增业务模型有直接 owner 时自动纳入；没有直接 owner 时必须在 `INDIRECT_OWNERS` 声明归属，缺失规则明确失败。

上线需应用 `accounts.0004_accountreset`（依赖现有引导迁移 `0003`），并一起重启 Web、CRM、聊天和销售 Worker，使它们使用相同锁协议。数据库初始化前不可直接切换新 Web。服务端模板和静态资源也需按现有部署流程更新。

## 验证

```powershell
.venv/Scripts/python.exe backend/manage.py test tests.integration.test_account_reset --noinput
node backend/tools/browser_account_reset.cjs
```

浏览器测试沿用已有工具环境变量 `SALESMATE_PLAYWRIGHT_MODULE` 和 `SALESMATE_BROWSER_PATH`。后端测试使用隔离 PostgreSQL 测试库、真实会话和临时文件；浏览器测试使用真实存储与页面模块，但模拟业务 HTTP，不代表生产账号已被清空。
