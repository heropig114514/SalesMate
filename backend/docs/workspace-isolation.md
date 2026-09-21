# 个人空间与邮件事实兼容

2026-09-21 按项目负责人要求，算法联调结束后恢复各账号自己的视图。部署配置为：

```dotenv
WORKSPACE_OWNER_ONLY=True
LAB_OPEN_ACCESS=False
LOCAL_DEBUG_AUTO_LOGIN=False
```

`WORKSPACE_OWNER_ONLY` 优先于实验模式。网页需要真实登录；业务记录、邮箱原文、分析、聊天、知识和文件按账号隔离。团队共享与 KGSEED 共享入口停用：实验目录为空，批次读取、导出、附件、修改及 MCP 实验工具均拒绝；数据库中的原数据和归属不变。自有 KGSEED 客户仍可在普通业务页面中查看。

账号隔离时，分析不再使用跨账号实验资料。包含这类资料的旧分析缓存按现有来源版本规则失效，需明确更新分析以生成当前可用来源的结果。不会为恢复视图自动批量调用模型。

Agent 的任务 API 继续使用 `Authorization: Agent <该账号的服务令牌>`，任务写回携带领取到的租约和公司版本。业务工具 API/MCP 使用对应账号的 Tool 授权，限定工具名单和有效期。MCP 配置需提供 `SALESMATE_TOOLS_TOKEN`；仅提供 `SALESMATE_TOOLS_USER` 不再能选择账号。Session 下的工具调用也遵守当前账号权限。写操作恢复原有版本及幂等契约。

工作台右上角的 `M` 是 Gmail 连接标识，不是登录头像。邮箱列表始终只返回当前账号的连接；切换账号立即清空旧邮箱显示，迟到的旧账号响应不再覆盖当前界面。

## 邮件事实版本

`extract_prompt_version` 表示生成来源；虚构样例仍保留 `批次:fixture-extract-v1`，不会伪装成真实模型输出。新样例同时记录 `extract_schema_version`。已审计的 `KGSEED_20260921_01:fixture-extract-v1` 使用 extract-v7 结构，由后端和 Agent 共享的明确兼容映射识别，不修改已有数据库行或清单指纹。未知历史结构仍需要升级，事实字段、枚举和证据校验保持生效。

客户详情默认只读，分析失败不会阻止查看已保存邮件。详情的 `extraction_upgrade` 给出版本和修复状态；真正的旧事实可点击“升级邮件事实”，或使用：

- `customers.extraction_status`：只读预览，参数 `company_id`。
- `customers.upgrade_extractions`：显式升级，参数 `company_id`、当前 `revision`，并提供工具幂等键。
- 原 HTTP `GET/POST /api/v1/companies/{id}/extraction-upgrade/`；POST 空正文及 `If-Match`。

升级读取已保存正文，保留历史抽取，完成后排队分析；不重新拉邮箱。兼容样例升级是无操作。失败修复需要明确重试，不自动循环调用模型。

占位评分仍显示未知，页面文案为“尚未生成评分”。合成向量不等同于语义检索模型；虚构邮箱不包含 OAuth 授权。旧公司绑定聊天仍可读历史，继续提问使用已有的新建工作空间会话入口，不静默改变历史问题语境。

精确批次清理仍保留指纹与外部引用校验。真正运行分析后产生的清单外记录，需要按实际血缘处理后再清理；本次不忽略引用约束、不自动删除历史结果。
