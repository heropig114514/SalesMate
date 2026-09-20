# QQ 邮箱接入与试用

## 当前状态：暂时停用

QQ 收信与发信默认关闭（`QQ_MAIL_ENABLED=false`）。页面隐藏连接、同步与发信入口；后端拒绝 QQ 连接、新同步、重试、发信准备、批准和外部核对。工具目录不再发布 `actions.prepare_qq`。Gmail 保持可用。

既有 QQ 邮件、画像、授权密文和同步检查点保留；历史邮件可继续通过客户详情及已保存邮件 API 查询。已排队同步暂停领取；已有已批准发信任务若进入 Worker，会在外部调用前记为失败，不自动重发。历史邮件的本地修复及分析仍可执行，不连接 QQ。

需要恢复时，在目标环境显式设置 `QQ_MAIL_ENABLED=true` 并重启 Web、CRM 和销售 Worker/Celery 进程，使各进程配置一致；原排队同步随后可被领取。恢复后应先核对队列及连接状态。下文描述启用时的完整使用流程。

QQ 接入与原 Gmail OAuth 并存。QQ 使用 `imap.qq.com:993` 的 TLS 连接和客户端授权码，不使用 Google OAuth、浏览器回调或自有域名。收信读取收件箱及已发送邮件，复用当前 L1–L4 分析和持久批次；发信使用独立的 QQ SMTP 连接和人工确认动作。原有 Gmail 发信及日历功能保留。

## 部署准备

1. 在当前项目 Python 环境、仓库根目录执行 `python backend/manage.py migrate`，应用 `crm.0007_qq_mailbox` 和 `crm.0008_mailboxsyncrun_sync_options`，增加 QQ 凭证、检查点及批次范围字段。
2. 在根 `.env` 配置现有的 `SALESMATE_VAULT_KEY`。Web 与 `crm_worker` 必须使用同一个 Fernet 密钥。如果已经配置，继续使用原值；不要覆盖，否则原外部连接和 QQ 授权码将无法解密。
3. 仅在尚无密钥时生成一次：

   ```powershell
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

   将输出保存为 `.env` 的 `SALESMATE_VAULT_KEY` 并妥善备份。不要提交 Git、放进网页或分享给其他人。应用不会自动生成密钥，也不会将授权码回退为明文存储。
4. 重启 Web 和共享 `crm_worker`。沿用内部 HTTP 地址；Worker 自动发现所有有效员工的排队任务，每个执行单元使用独立临时身份，不依赖固定员工的 `SALESMATE_AGENT_SERVICE_TOKEN`。QQ 和 Gmail 都走此 Worker；旧 `--sync-authorized-mailboxes-once` CLI 仍仅领取 Gmail。
5. 服务器须能出站访问 `imap.qq.com:993`。网页无需 OAuth 回调域名，但公网提交授权码仍应使用 HTTPS；不要为此关闭既有 HTTPS 和安全 Cookie 设置。

## 页面操作

1. 在 QQ 邮箱网页版「设置 → 账号与安全 → 安全设置」开启 IMAP/SMTP 服务并生成 16 位授权码。详见 [QQ 官方说明](https://help.mail.qq.com/detail/106/985)。填写授权码，不是 QQ 登录密码。
2. 登录 SalesMate，点击顶部或客户列表的「QQ 邮箱」，输入完整 `@qq.com` 或 `@foxmail.com` 地址与授权码，并填写「最近 N 天」或「最多 N 封」至少一项，再点击「验证连接并同步」。两项无预填，填写正整数；两项都填时同时生效。请使用实际收发邮件所用的邮箱地址；本功能不推断别名关系。
3. 后端先验证 QQ 登录与收发文件夹，成功才加密保存并请求首次同步。表单提交后清空授权码；邮箱列表和进度接口不会返回授权码或密文。
4. 在工作台查看本批次范围、逐封邮件及公司画像进度。后续点击「同步 QQ」或刷新收件箱，每次重新选择 QQ 范围，取消不创建新批次。Gmail 沿用原同步规则。每页 20 封、L1 最多四路并发，沿用现有提示词和参数。
5. 同步失败后查看批次与 Worker 日志，再点「重试未完成邮件」。普通新增同步不自动重试旧失败记录。批次活动期间不可替换或移除 QQ 凭证。
6. 「移除连接」只删除本地 QQ 密文，保留历史邮件、画像和游标；如需撤销授权码在其他客户端的权限，到 QQ 邮箱官方页面将该授权码设为失效。

核对邮件时，在 QQ 账号下点击「查看已同步邮件」。该入口只读取指定邮箱已入库的邮件，默认包含业务、非业务和待复核邮件，显示来源、接收时间、分类及可展开的正文；不触发同步或模型调用。客户工作台按公司归组且只展示业务邮件，不能用它核对完整邮箱列表。以前导入的演示邮件会在客户列表标为「演示样例」，不会被当作 QQ 邮件来源；既有样例及历史邮件均保留。

「最近 N 天」按请求确认时刻往前 N × 24 小时计算，以 IMAP INTERNALDATE 为依据，并冻结时间上下界；不是邮件可自行填写的 Date 头。「最多 N 封」是本批次收件箱和已发送的合计待处理数量，按内部日期从新到旧选择，失败尝试也占一封。已经完成当前版本抽取的邮件不占封数，普通同步不自动重试失败邮件。只填天数可能仍匹配大量邮件，若需控制单次处理量请同时填写封数。

筛选会读取候选邮件的 UID 和日期元数据，元数据数量可能大于封数上限；只有选中邮件才读取正文并进入分析，已有 pending 也不能绕过限制。未选中的邮件留待后续手动同步；扩大天数可补采旧邮件，不会被最大 UID 跳过。重试保留原失败 ID 或冻结时间范围。升级前没有范围的旧批次若尚未登记任何 ID，需要重新选择范围后同步；已登记失败邮件仍可明确重试。已处理历史邮件不会因新范围而被删除。

QQ 授权码本身可能允许收发等操作；收信同步只执行只读 EXAMINE、UID SEARCH、日期元数据 FETCH 与 BODY.PEEK[]，不改变已读状态，不发送或删除邮件。

## API 与兼容边界

- `POST /api/v1/mailboxes/qq-connect/`：请求 `address`、`authorization_code`、`sync_options`，成功 202 返回安全邮箱状态，首次同步已排队。沿用员工 Session/CSRF。
- `DELETE /api/v1/mailboxes/{mailbox_id}/qq-authorization/`：移除当前员工非活动 QQ 连接。越权 404，活动状态冲突 409。
- 原 `GET /api/v1/mailboxes/` 新增 `qq_authorized`；`gmail_authorized` 继续只表示 Google 授权。
- `GET /api/v1/mailboxes/{mailbox_id}/email-reviews/?status=saved`：按接收时间倒序查看本邮箱全部已入库邮件，包含原文、`source`、`received_at` 与分类；每页 20 封，员工归属隔离。客户列表另返回 `email_sources`，表示实际业务邮件的来源集合。
- 原 `POST /api/v1/mailboxes/{mailbox_id}/request-sync/`、批次查询及失败重试接口同时支持 Gmail 和 QQ。QQ 同步必须传 `{"sync_options":{"recent_days":7,"max_messages":20}}`（示例值，不是默认值），任意一项可省略或为 `null`，两项都为空返回 400。批次进度返回冻结的 `sync_options`。Gmail 同样必须提供显式范围，默认最多 50 封，超过时需明确批准；Gmail 的最近 N 封先限量再跳过已同步邮件，不向更早邮件补足。QQ 既有“已完成邮件不占封数”的语义保持不变。
- QQ 邮件 `source=qq_real`；为兼容既有 L1/HTTP 契约，`gmail_message_id` 字段承载 `qq:{文件夹Base64URL}:{UIDVALIDITY}:{UID}`，不是 Gmail 服务端 ID。`dedupe_key` 仍由邮箱地址与该 ID 组成，QQ 的 `thread_id=null`。
- 文件夹是独立身份空间；不会仅凭可重复的 MIME Message-ID 合并不同文件夹中的物理副本。已导入邮件移出文件夹后仍保留本地历史。
- 检查点推进前先持久登记消息，原文在模型调用前落库，写入失败后可复用已成功抽取。UIDVALIDITY 改变或已发送目录改名会明确停止，不静默重置、重扫或冒充同步成功。
- 只支持服务返回的唯一 `\\Sent` 特殊文件夹或 QQ 已知 Sent/Sent Messages/已发送名称。无法识别时完整报错，不改为只同步收件箱。

## 验证

```powershell
python -m unittest agent.tests.test_qq_mail
python backend/manage.py test tests.integration.test_qq_mail
python backend/tools/check_docs.py
```

测试模拟 IMAP 与 LLM，数据库、协议校验、权限及持久状态使用真实实现。它们不证明真实 QQ 授权、网络连通性、邮箱实际文件夹布局或百炼输出已验证；首次试用需用自己的邮箱在页面确认。

## QQ 发信

1. 应用 `sales.0004_qq_smtp_send` 迁移，重启 Web。发信执行进程 `python backend/manage.py sales_worker` 与 Web 使用同一数据库和 `SALESMATE_VAULT_KEY`。该进程处理所有员工已经明确批准的外部动作，启动前应核对待执行队列。
2. 打开「业务管理 → 外部连接 → 新增 → 连接 QQ 发信」，输入邮箱与客户端授权码。后端仅验证 `smtp.qq.com:465` 的 TLS/SMTP 登录，成功后独立加密保存；不自动复用收信授权，不发送测试邮件。无需 OAuth 回调或自有域名，服务器须能出站连接该服务。
3. 在客户的助手会话中保存含收件人、主题和正文的邮件草稿；打开「外部动作 → 新增」，选择「QQ 发送邮件」及对应发信连接、客户、草稿，可附带已审核报价。
4. 生成计划后审阅完整发件账号、收件人、主题和正文，再点「确认并加入执行队列」。准备计划不会发信；执行使用已展示的冻结内容，不因后来编辑草稿而改变。连接版本或报价变化会拒绝执行。
5. 所有收件人获准后才提交正文，任一收件人被拒绝则整封不提交。SMTP 明确拒绝记录为失败，正文提交中断记录为「结果待核对」。同一动作不会自动重发。
6. 「执行成功 / QQ 服务器已接受」仅表示 SMTP 接受提交，不保证最终送达。若结果未知，可点击「到外部服务核对结果」：只读查询 QQ 已发送目录中的唯一 Message-ID，并核对收发地址及主题。此查询需要 IMAP 和服务器保留发送副本；查不到仍保持未知，不认定未发送。程序不会自行追加发送副本。

API：`POST /api/v1/sales/connections/qq/` 接受 `address`、只写 `authorization_code`，成功 201；非法输入 400，SMTP 认证失败 409，未完成动作占用连接时禁止更换凭证。它继承员工 Session/CSRF，响应不返回密文。`POST /api/v1/sales/records/actions/` 新增 `tool=qq.send`，沿用既有准备、确认、执行与核对契约。

发信自动检查：`python backend/manage.py test tests.integration.test_qq_send`；网络完全模拟，覆盖登录不发信、加密、权限、冻结内容、全部收件人门槛、失败/未知分类及只读核对。真实 QQ SMTP 认证、最终投递和服务器保存副本需在试用中另行验证。

Lightsail 可将 `backend/deploy/lightsail/salesmate-sales.service` 安装到 `/etc/systemd/system/`，执行 `systemctl daemon-reload` 并启用 `salesmate-sales`。安装前核对已批准队列；更新网站前同时停止该进程，完成迁移和就绪检查后再启动。该服务依赖 `salesmate-web` 和 PostgreSQL，不自动重启失败进程；日志通过 `journalctl -u salesmate-sales` 查看，不包含邮件正文或凭证。
