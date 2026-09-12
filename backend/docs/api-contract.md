# 当前 API 契约

更新：2026-09-12，版本 0.2.0。通信对象依据 SalesMate 仓库上一层的 README.md《邮件理解 Agent · 模块设计》v1.11；该仓库外原始协议文件未修改。数据库结构见 [data-model.md](data-model.md)。

## 身份与错误

浏览器使用 Django Session。先 GET `/api/v1/session/` 获取 CSRF cookie，所有写请求带 X-CSRFToken，包括匿名登录。Agent 路由仅接受 `Authorization: Agent <service-token>`；凭证绑定单个业务用户，数据库只保存摘要，不能使用浏览器 Session 或 Gmail access_token 代替。

本地设置默认启用 `LOCAL_DEBUG_AUTO_LOGIN`：DEBUG 开启且直连来自回环地址时，GET session 会为匿名浏览器建立 `LOCAL_DEBUG_USER`（默认 demo）的普通用户会话，并返回 `debug_auto_login: true`。账号必须已存在、启用且无管理员权限；否则返回 409。已有会话不更换用户。关闭该开关、关闭 DEBUG 或非回环访问返回 `debug_auto_login: false`，正常登录流程不变。

mailbox_id 由后端创建，company_id 由后端归组分配，均为 UUID。非法 UUID 返回 400；不存在或跨用户对象返回相同 404。业务邮箱地址只建立业务标识，尚未完成真实 Gmail OAuth 所有权核验。GmailAuthorization 不进入后端业务接口。

服务端生成 X-Request-ID。错误保留 `error.code / error.detail / request_id`：版本冲突返回 409 conflict，状态不允许为 409 invalid_state，输入错误为 400。日志不包含正文、查询参数、密码、授权头或租约凭证。未知异常不自动转换为规则输出。

## Agent HTTP 映射

下列路径均以 `/api/v1/agent/` 开头。JSON 核心字段与 README 同名，包含 `from`。

| README 函数 | HTTP | 请求与响应 |
|---|---|---|
| submit_emails | POST emails/ | EmailSubmission[] → 每项 dedupe_key、company_id、created/duplicate |
| resubmit_facts | POST facts/ | FactsResubmission → company_id、revision |
| get_company_grouping | GET grouping/?company_id=… | Grouping 与 ETag |
| get_company_context | GET context/?company_id=… | 带 Grouping 的 If-Match → CompanyContext |
| get_latest_analysis_input | GET latest-analysis-input/?company_id=… | 当前 revision 的 AnalysisInput，无则 404 |
| get_cached_analysis | GET cached-analysis/?company_id=…&input_version=…&analysis_prompt_version=… | CachedAnalysis；提示词参数可选，建议传入 |
| save_analysis_input | POST analysis-inputs/ | AnalysisInput → 已归档输入 |
| save_analysis | POST analyses/ | Analysis → 已归档分析 |
| save_score | POST scores/ | Score → 已归档评分 |
| get_sync_state | GET sync-state/?mailbox_id=… | SyncState 与 ETag |
| save_sync_state | POST sync-state-save/ | SyncState + If-Match → 新状态 |
| claim_jobs | POST jobs/claim/ | {limit, lease_seconds} → Job[] |
| report_job | POST jobs/report/ | JobReport + X-Lease-Token → job_id、status |
| list_failed_extractions | GET failed-extractions/?mailbox_id=… | 当前抽取失败的 dedupe_key 数组 |
| 重做原文读取补充 | GET failed-extractions/?mailbox_id=…&dedupe_key=… | 完整邮件与当前抽取 |

## 必要的 HTTP 并发扩展

README 已要求 expected_version，但尚未冻结 HTTP 表示、领取凭证与租约参数；本实现补充如下：

1. Grouping、CompanyContext、详情响应附 `ETag: "<revision>"`。读取 context 时必须带 If-Match，避免两次读取属于不同快照。
2. claim 请求显式传 lease_seconds（10–600）与 limit（1–50）。返回 Job 增加 lease_token 和 expected_version，payload.company_id 不变。
3. 保存 Input、Analysis、Score 都必须携带 If-Match、X-Job-ID、X-Lease-Token。错误凭证、过期租约和旧 revision 均拒绝写入。
4. 同公司未领取任务合并到最新 revision；运行任务不改写，新邮件建立后继任务。领取使用 PostgreSQL 行锁和 skip_locked，两个消费者不能重复领取。
5. 租约过期在下次领取时显式标记 failed，不自动重新派发。用户可点击更新分析创建新任务；没有自动续租、隐式重试或降级。

## 持久化与幂等

- 每批提交 1–100 封，先校验全部格式，然后事务保存。任一项冲突或越权整批回滚。邮件 dedupe_key 必须为 mailbox_id:gmail_message_id；同键同载荷去重，同键改本体冲突。
- 抽取失败或非业务跳过仍保存邮件，facts 必须为 null，failed 必须附错误摘要。完成事实要求完整字段及可定位 evidence。
- (dedupe_key, extract_prompt_version) 唯一。完成结果不可变；failed 仅能通过 facts 接口补交成功一次。新提示词版本保留历史，当前抽取按记录创建顺序选择。
- (company_id, input_version) 唯一，input_version 仍由 Agent 计算。后端绑定 revision 并核对成员、外部版本、未解析数量及事实全集；不得漏掉旧预算或重复事实。同键重存只忽略 built_at 差异，其余内容不同冲突。
- (快照, analysis_prompt_version) 唯一。成功结果不可覆盖；失败结果可在显式任务的有效租约内补齐成功。来源必须属于当前快照，详情不允许百分比数字，未解析邮件必须说明。
- Score 绑定具体成功 Analysis。缺失特征或未知信号必须为空分，贡献和须等于分数。同一分析、score_version、scored_at 为幂等身份，允许显式时间重评分。
- 缓存命中要求当前 revision、输入版本、成功状态及指定提示词匹配。旧结果可显示，但 stale=true 并保留 generated_at。
- JobReport 成功必须对应已保存的当前分析；声明生成评分时必须确有评分。失败必须附 error，重复或过期回报冲突。
- SyncState 初始 cursor/last_synced_at 为 null、scope 为 {}、status 为 authorization_required、version=0。Agent 自行明确首次扫描范围，并仅在全部邮件提交成功后推进游标。后端无法从 historyId 推断同步完整性。

幂等依赖以上自然键与领取凭证；当前未实现通用 Idempotency-Key 存储表。请求追踪 ID 不作为业务身份。

## 浏览器接口

路径以 `/api/v1/` 开头：

| 路径 | 方法与行为 |
|---|---|
| session/ | GET 身份与 CSRF；POST 登录；DELETE 注销 |
| mailboxes/ | GET 用户邮箱；POST 创建业务标识 |
| companies/ | GET 列表、分页、全局统计 |
| companies/{id}/ | GET 邮件、CRM、画像、分析、评分与状态 |
| companies/{id}/analyze/ | POST 显式入队，rules 模式运行占位 |
| companies/{id}/register/ | POST CRM 建档/编辑，必须带 If-Match |
| demo/runtime/ | GET provider、时区与可用能力 |
| demo/seed/ | POST 幂等导入四封独立合成样例，仅 rules |
| demo/email/ | POST 人工模拟来信及规则处理，仅 rules |

列表参数为 q、industry、size_band、signal、crm_status、page、page_size。跨维度 AND，同维度逗号多选 OR。优先级降序、空分最后、同分按最近入站实际时间降序，再以公司 ID 稳定排序。page_size 默认 20，最大 100。统计使用未过滤的授权公司集合，今日时区沿用 DJANGO_TIME_ZONE，当前 UTC。

原健康检查、accounts/me、Admin 和 Schema 路由保留。`backend/contracts/openapi.yaml` 从代码生成，不手工维护；以下命令从仓库根目录执行：

```powershell
cd backend
python manage.py spectacular --file contracts/openapi.yaml --validate --fail-on-warn
python manage.py test tests
```

切换真实 Agent 见 [agent-integration.md](agent-integration.md)。未实现真实 OAuth、Gmail 读取、模型调用、邮件发送、翻译、右栏对话助手、知识库、新闻及集团多域人工合并。
