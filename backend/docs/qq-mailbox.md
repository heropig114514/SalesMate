# QQ 邮箱接入与试用

QQ 接入与原 Gmail OAuth 并存。QQ 使用 `imap.qq.com:993` 的 TLS 连接和客户端授权码，不使用 Google OAuth、浏览器回调或自有域名。本功能只读取收件箱及已发送邮件，复用当前 L1–L4 分析和持久批次；不包含 QQ SMTP 发信，也不改动原有 Gmail 发信或日历功能。

## 部署准备

1. 在当前项目 Python 环境、仓库根目录执行 `python backend/manage.py migrate`，应用 `crm.0007_qq_mailbox` 和 `crm.0008_mailboxsyncrun_sync_options`，增加 QQ 凭证、检查点及批次范围字段。
2. 在根 `.env` 配置现有的 `SALESMATE_VAULT_KEY`。Web 与 `crm_worker` 必须使用同一个 Fernet 密钥。如果已经配置，继续使用原值；不要覆盖，否则原外部连接和 QQ 授权码将无法解密。
3. 仅在尚无密钥时生成一次：

   ```powershell
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

   将输出保存为 `.env` 的 `SALESMATE_VAULT_KEY` 并妥善备份。不要提交 Git、放进网页或分享给其他人。应用不会自动生成密钥，也不会将授权码回退为明文存储。
4. 重启 Web 和当前员工的 `crm_worker`。沿用现有 `SALESMATE_AGENT_SERVICE_TOKEN` 与内部 HTTP 地址；一个 Worker 只处理其服务令牌绑定的员工。QQ 和 Gmail 都走此 Worker；旧 `--sync-authorized-mailboxes-once` CLI 仍仅领取 Gmail。
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

QQ 授权码本身可能允许收发等操作；本程序只执行只读 EXAMINE、UID SEARCH、日期元数据 FETCH 与 BODY.PEEK[]，不改变已读状态，不发送或删除邮件。

## API 与兼容边界

- `POST /api/v1/mailboxes/qq-connect/`：请求 `address`、`authorization_code`、`sync_options`，成功 202 返回安全邮箱状态，首次同步已排队。沿用员工 Session/CSRF。
- `DELETE /api/v1/mailboxes/{mailbox_id}/qq-authorization/`：移除当前员工非活动 QQ 连接。越权 404，活动状态冲突 409。
- 原 `GET /api/v1/mailboxes/` 新增 `qq_authorized`；`gmail_authorized` 继续只表示 Google 授权。
- `GET /api/v1/mailboxes/{mailbox_id}/email-reviews/?status=saved`：按接收时间倒序查看本邮箱全部已入库邮件，包含原文、`source`、`received_at` 与分类；每页 20 封，员工归属隔离。客户列表另返回 `email_sources`，表示实际业务邮件的来源集合。
- 原 `POST /api/v1/mailboxes/{mailbox_id}/request-sync/`、批次查询及失败重试接口同时支持 Gmail 和 QQ。QQ 同步必须传 `{"sync_options":{"recent_days":7,"max_messages":20}}`（示例值，不是默认值），任意一项可省略或为 `null`，两项都为空返回 400。批次进度返回冻结的 `sync_options`。Gmail 继续使用空请求，不能传入 QQ 范围。
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
