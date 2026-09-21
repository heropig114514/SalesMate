"""职责：把 Agent 工作流的最小 BackendClient 协议映射到 Django Agent HTTP API。
实现：维护身份、ETag 与租约上下文；邮件提交默认 gmail_real，QQ 调用显式指定 qq_real。
关联：Gmail/QQ Worker 复用此传输；L2–L4 继续使用原协议、参数和提示词版本。
目录：
- BackendClient：声明 L1–L4 最小后端协议。
- BackendClient.submit_emails：声明标准 L1 邮件提交接口。
- BackendClient.get_stored_email：查询天然键对应的已有抽取。
- BackendClient.get_company_grouping：读取公司归组。
- BackendClient.get_company_context：读取公司业务上下文。
- BackendClient.save_analysis_input：保存 L2 输入并跟踪版本。
- BackendClient.get_latest_analysis_input：读取最近 L2 快照。
- BackendClient.get_cached_analysis：查询指定输入和提示词的 L3 缓存。
- BackendClient.save_analysis：提交 L3 分析。
- BackendClient.save_score：提交 L4 评分。
- BackendClient.claim_jobs：领取并缓存公司租约。
- BackendClient.report_job：回报公司任务。
- BackendClient.claim_mailbox_syncs：旧 CLI 领取 Gmail 同步。
- BackendClient.report_mailbox_sync：旧 CLI 回报 Gmail 同步。
- BackendClient.get_sync_state：读取邮箱游标和 ETag。
- BackendClient.save_sync_state：条件更新邮箱游标。
- BackendClient.claim_answer_request：领取一条工作空间聊天回答请求。
- BackendClient.get_answer_context：读取请求绑定的客户和知识上下文。
- BackendClient.get_chat_tools：发现本请求读取及实验维护工具。
- BackendClient.get_chat_request_status：读取本人请求状态。
- BackendClient.read_chat_tool：执行客户或共享实验查询并校验响应。
- BackendClient.report_answer：回报带 Prompt 版本的聊天结果。
- BackendRetrievalError：表示后端读取失败。
- BackendConfigurationError：表示配置不满足调用前提。
- BackendContractError：表示响应违反协议。
- BackendRequestError：包含 HTTP 状态的安全请求异常。
- BackendRequestError.__init__：保存 HTTP 状态、错误代码和安全说明。
- _JobContext：保存公司任务与租约上下文。
- DjangoBackendClient：把简化工作流调用映射到认证 HTTP 接口。
- DjangoBackendClient.__init__：验证初始化参数并建立实例状态。
- DjangoBackendClient.close：释放当前实例的 HTTP 连接池。
- DjangoBackendClient.submit_emails：提交邮件并统计结果；实现支持默认 Gmail 或显式 QQ 来源。
- DjangoBackendClient.get_stored_email：查询天然键对应的已有抽取。
- DjangoBackendClient.get_company_grouping：读取公司归组。
- DjangoBackendClient.get_company_context：读取公司业务上下文。
- DjangoBackendClient.save_analysis_input：保存 L2 输入并跟踪版本。
- DjangoBackendClient.get_latest_analysis_input：读取最近 L2 快照。
- DjangoBackendClient.get_cached_analysis：查询指定输入和提示词的 L3 缓存。
- DjangoBackendClient.save_analysis：提交 L3 分析。
- DjangoBackendClient.save_score：提交 L4 评分。
- DjangoBackendClient.claim_jobs：领取并缓存公司租约。
- DjangoBackendClient.report_job：回报公司任务。
- DjangoBackendClient.claim_mailbox_syncs：旧 CLI 领取 Gmail 同步。
- DjangoBackendClient.report_mailbox_sync：旧 CLI 回报 Gmail 同步。
- DjangoBackendClient.get_sync_state：读取邮箱游标和 ETag。
- DjangoBackendClient.save_sync_state：条件更新邮箱游标。
- DjangoBackendClient.claim_answer_request：映射工作空间聊天领取接口并移除过渡期空公司字段。
- DjangoBackendClient.get_answer_context：映射聊天上下文接口。
- DjangoBackendClient.get_chat_tools：发现本请求读取及实验维护工具。
- DjangoBackendClient.get_chat_request_status：读取本人请求状态。
- DjangoBackendClient.read_chat_tool：执行客户或共享实验查询并校验响应。
- DjangoBackendClient.report_answer：映射聊天回答保存接口。
- DjangoBackendClient._required_string：读取并校验非空字符串。
- DjangoBackendClient._object_list：验证对象数组。
- DjangoBackendClient._retrieval_gaps：验证三字段资料缺口数组。
- DjangoBackendClient._write_headers：构造写请求所需租约与版本头。
- DjangoBackendClient._request：发起认证 HTTP 并规范化失败。
- DjangoBackendClient._company_id：验证并提取公司 ID。
- DjangoBackendClient._object：要求响应为对象。
- DjangoBackendClient._etag：提取响应版本头。
- DjangoBackendClient._error：规范化 API 错误。
- django_backend_from_environment：读取连接配置，允许任务显式提供独立身份和邮箱。
变量索引：
- logger：记录安全请求上下文与耗时。
- JsonObject：只读 JSON 映射类型别名。
- _DEFAULT_ANALYSIS_PROMPT_VERSION：从实际分析 Skill 读取的提示词版本。
- _JobContext.job_id：已领取任务 ID。
- _JobContext.company_id：任务所属公司 ID。
- _JobContext.expected_version：领取时的业务版本。
- _JobContext.lease_token：写入时必须携带的租约，不进入日志。
- __all__：公开的后端协议、异常、客户端和工厂符号。
"""

from __future__ import annotations

import copy
import logging
import os
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Mapping, Protocol
from urllib.parse import quote, urlencode

import requests

from agent.skills import load_skill
from integrations.salesmate_tools.read_contract import WORKSPACE_TOOLS

JsonObject = Mapping[str, Any]
_DEFAULT_ANALYSIS_PROMPT_VERSION = load_skill("customer-analysis").version
logger = logging.getLogger("salesmate.agent.backend_api")


# 功能：L1–L4 与只读聊天 workflow 使用的最小真实后端接口。
# 逻辑：声明 workflow 所需方法，由具体客户端提供传输。
# 约束：不记录凭证；身份与权限最终由后端校验。
class BackendClient(Protocol):
    """L1–L4 与只读聊天 workflow 使用的最小真实后端接口。"""

    # 功能：声明标准 L1 邮件提交接口。
    # 输入：`submissions` 邮件抽取载荷。
    # 输出：JsonObject。
    # 逻辑：仅声明接口，具体传输由实现者提供。
    # 约束：声明或异常构造不执行 HTTP 请求。
    def submit_emails(self, submissions: list[dict[str, Any]]) -> JsonObject: ...

    # 功能：查询天然键对应的已有抽取。
    # 输入：`mailbox_id` 目标邮箱标识，可空时使用方法的既定配置规则、`dedupe_key` 邮件天然键。
    # 输出：JsonObject | None。
    # 逻辑：仅声明接口，具体传输由实现者提供。
    # 约束：声明或异常构造不执行 HTTP 请求。
    def get_stored_email(
        self, mailbox_id: str, dedupe_key: str
    ) -> JsonObject | None: ...

    # 功能：读取公司归组。
    # 输入：`company_id` 明确的客户标识。
    # 输出：JsonObject。
    # 逻辑：仅声明接口，具体传输由实现者提供。
    # 约束：声明或异常构造不执行 HTTP 请求。
    def get_company_grouping(self, company_id: str) -> JsonObject: ...

    # 功能：读取公司业务上下文。
    # 输入：`company_id` 明确的客户标识。
    # 输出：JsonObject。
    # 逻辑：仅声明接口，具体传输由实现者提供。
    # 约束：声明或异常构造不执行 HTTP 请求。
    def get_company_context(self, company_id: str) -> JsonObject: ...

    # 功能：保存 L2 输入并跟踪版本。
    # 输入：`analysis_input` L2 分析输入。
    # 输出：None。
    # 逻辑：仅声明接口，具体传输由实现者提供。
    # 约束：声明或异常构造不执行 HTTP 请求。
    def save_analysis_input(self, analysis_input: JsonObject) -> None: ...

    # 功能：读取最近 L2 快照。
    # 输入：`company_id` 明确的客户标识。
    # 输出：JsonObject | None。
    # 逻辑：仅声明接口，具体传输由实现者提供。
    # 约束：声明或异常构造不执行 HTTP 请求。
    def get_latest_analysis_input(self, company_id: str) -> JsonObject | None: ...

    # 功能：查询指定输入和提示词的 L3 缓存。
    # 输入：`company_id` 明确的客户标识、`input_version` 指定输入版本。
    # 输出：JsonObject | None。
    # 逻辑：仅声明接口，具体传输由实现者提供。
    # 约束：声明或异常构造不执行 HTTP 请求。
    def get_cached_analysis(
        self, company_id: str, input_version: str
    ) -> JsonObject | None: ...

    # 功能：提交 L3 分析。
    # 输入：`analysis` L3 分析结果。
    # 输出：None。
    # 逻辑：仅声明接口，具体传输由实现者提供。
    # 约束：声明或异常构造不执行 HTTP 请求。
    def save_analysis(self, analysis: JsonObject) -> None: ...

    # 功能：提交 L4 评分。
    # 输入：`score` L4 评分。
    # 输出：None。
    # 逻辑：仅声明接口，具体传输由实现者提供。
    # 约束：声明或异常构造不执行 HTTP 请求。
    def save_score(self, score: JsonObject) -> None: ...

    # 功能：领取并缓存公司租约。
    # 输入：`limit` 领取数量上限。
    # 输出：list[dict[str, Any]]。
    # 逻辑：仅声明接口，具体传输由实现者提供。
    # 约束：声明或异常构造不执行 HTTP 请求。
    def claim_jobs(self, limit: int) -> list[dict[str, Any]]: ...

    # 功能：回报公司任务。
    # 输入：`report` 任务结果载荷。
    # 输出：None。
    # 逻辑：仅声明接口，具体传输由实现者提供。
    # 约束：声明或异常构造不执行 HTTP 请求。
    def report_job(self, report: JsonObject) -> None: ...

    # 功能：旧 CLI 领取 Gmail 同步。
    # 输入：`limit` 领取数量上限。
    # 输出：list[dict[str, Any]]。
    # 逻辑：仅声明接口，具体传输由实现者提供。
    # 约束：声明或异常构造不执行 HTTP 请求。
    def claim_mailbox_syncs(self, limit: int) -> list[dict[str, Any]]: ...

    # 功能：旧 CLI 回报 Gmail 同步。
    # 输入：`report` 任务结果载荷。
    # 输出：JsonObject。
    # 逻辑：仅声明接口，具体传输由实现者提供。
    # 约束：声明或异常构造不执行 HTTP 请求。
    def report_mailbox_sync(self, report: JsonObject) -> JsonObject: ...

    # 功能：读取邮箱游标和 ETag。
    # 输入：`mailbox_id` 目标邮箱标识，可空时使用方法的既定配置规则。
    # 输出：JsonObject。
    # 逻辑：仅声明接口，具体传输由实现者提供。
    # 约束：声明或异常构造不执行 HTTP 请求。
    def get_sync_state(self, mailbox_id: str) -> JsonObject: ...

    # 功能：条件更新邮箱游标。
    # 输入：`sync_state` 邮箱增量游标载荷。
    # 输出：JsonObject。
    # 逻辑：仅声明接口，具体传输由实现者提供。
    # 约束：声明或异常构造不执行 HTTP 请求。
    def save_sync_state(self, sync_state: JsonObject) -> JsonObject: ...

    # 功能：领取一条工作空间聊天回答请求。
    # 输入：无外部参数，读取实例认证与请求状态。
    # 输出：JsonObject | None。
    # 逻辑：仅声明接口，具体传输由实现者提供。
    # 约束：声明或异常构造不执行 HTTP 请求。
    def claim_answer_request(self) -> JsonObject | None: ...

    # 功能：读取请求绑定的客户和知识上下文。
    # 输入：`request_id` 聊天请求标识、`scope` internal 或 external 知识范围。
    # 输出：JsonObject。
    # 逻辑：仅声明接口，具体传输由实现者提供。
    # 约束：声明或异常构造不执行 HTTP 请求。
    def get_answer_context(self, request_id: str, scope: str) -> JsonObject: ...

    # 功能：发现请求实际获准的读取及实验维护工具目录。
    # 输入：`request_id` 当前员工的请求 UUID；具体实现读取实例认证配置。
    # 输出：JSON 对象；具体实现失败时抛出请求或契约异常。
    # 逻辑：仅声明协议，由具体客户端实现传输。
    # 约束：只允许当前员工已授权的请求；不接受身份覆盖、写工具或隐式重试。
    def get_chat_tools(self, request_id: str) -> JsonObject: ...

    # 功能：读取本人请求的权威状态。
    # 输入：`request_id` 当前员工的请求 UUID；具体实现读取实例认证配置。
    # 输出：JSON 对象；具体实现失败时抛出请求或契约异常。
    # 逻辑：仅声明协议，由具体客户端实现传输。
    # 约束：只允许当前员工已授权的请求；不接受身份覆盖、写工具或隐式重试。
    def get_chat_request_status(self, request_id: str) -> JsonObject: ...

    # 功能：执行客户读取或共享实验维护并取得登记证据。
    # 输入：`request_id` 当前员工的请求 UUID、`name` 固定读取及实验维护工具名称、`arguments` JSON 参数；具体实现读取实例认证配置。
    # 输出：JSON 对象；具体实现失败时抛出请求或契约异常。
    # 逻辑：仅声明协议，由具体客户端实现传输。
    # 约束：只允许当前员工已授权的请求；不接受身份覆盖、写工具或隐式重试。
    def read_chat_tool(
        self, request_id: str, name: str, arguments: JsonObject
    ) -> JsonObject: ...

    # 功能：回报带 Prompt 版本的聊天结果。
    # 输入：`result` 包含提示版本的聊天结果。
    # 输出：JsonObject。
    # 逻辑：仅声明接口，具体传输由实现者提供。
    # 约束：声明或异常构造不执行 HTTP 请求。
    def report_answer(self, result: JsonObject) -> JsonObject: ...


# 功能：后端数据读取失败。
# 逻辑：封装 HTTP 状态及安全说明。
# 约束：不记录凭证；身份与权限最终由后端校验。
class BackendRetrievalError(RuntimeError):
    """后端数据读取失败。"""


# 功能：真实后端适配器缺少必要配置。
# 逻辑：封装 HTTP 状态及安全说明。
# 约束：不记录凭证；身份与权限最终由后端校验。
class BackendConfigurationError(ValueError):
    """真实后端适配器缺少必要配置。"""


# 功能：后端响应无法映射为 Agent README 约定的数据。
# 逻辑：封装 HTTP 状态及安全说明。
# 约束：不记录凭证；身份与权限最终由后端校验。
class BackendContractError(RuntimeError):
    """后端响应无法映射为 Agent README 约定的数据。"""


# 功能：后端请求失败，且不暴露服务凭证。
# 逻辑：封装 HTTP 状态及安全说明。
# 约束：不记录凭证；身份与权限最终由后端校验。
class BackendRequestError(RuntimeError):
    """后端请求失败，且不暴露服务凭证。"""

    # 功能：保存 HTTP 状态、错误代码和安全说明。
    # 输入：`status_code` HTTP 状态或网络失败的零值、`code` 安全错误代码、`detail` 安全错误说明、`scope` 工具或请求错误范围。
    # 输出：无返回值，初始化实例状态。
    # 逻辑：保存状态、代码和说明，并构造安全异常消息。
    # 约束：声明或异常构造不执行 HTTP 请求。
    def __init__(
        self, status_code: int, code: str, detail: str, *, scope: str | None = None
    ):
        super().__init__(f"后端请求失败（HTTP {status_code}, {code}）：{detail}")
        self.status_code = status_code
        self.code = code
        self.detail = detail
        self.scope = scope


# 功能：保存不可变的已领任务身份、版本和租约。
# 逻辑：使用冻结 dataclass 保存 job_id/company_id/expected_version/lease_token。
# 约束：不记录凭证；身份与权限最终由后端校验。
@dataclass(frozen=True)
class _JobContext:
    job_id: str
    company_id: str
    expected_version: str
    lease_token: str


# 功能：调用当前 Django 后端，同时向 Agent 暴露简化后的同步协议。
# 逻辑：在实例内维护任务与版本状态，不共享员工认证。
# 约束：不记录凭证；身份与权限最终由后端校验。
class DjangoBackendClient:
    """调用当前 Django 后端，同时向 Agent 暴露简化后的同步协议。

    租约、ETag 和嵌套 Job payload 都属于当前 Django HTTP 传输层，
    由本类吸收；L2-L4 workflow 仍只依赖 ``BackendClient`` 的简单方法。
    """

    # 功能：验证初始化参数并建立实例状态。
    # 输入：`base_url` Agent API 根地址、`service_token` 员工服务凭证，不记录到日志、`mailbox_id` 目标邮箱标识，可空时使用方法的既定配置规则、`analysis_prompt_version` 分析提示版本，默认既有 Skill 版本、`lease_seconds` 租约秒数，默认 120 且限制 10–600、`timeout` HTTP 超时秒数，默认 30 且须大于零、`session` 可选 requests 会话，空时创建独立连接池。
    # 输出：无返回值，初始化实例状态。
    # 逻辑：校验地址、凭证和既定超时租约范围；建立私有 HTTP 连接及公司、任务、邮箱版本缓存。
    # 约束：不隐式重试；HTTP 错误与契约异常由调用者处理。
    def __init__(
        self,
        base_url: str,
        service_token: str,
        *,
        mailbox_id: str | None = None,
        analysis_prompt_version: str = _DEFAULT_ANALYSIS_PROMPT_VERSION,
        lease_seconds: int = 120,
        timeout: float = 30,
        session: requests.Session | None = None,
    ):
        if not isinstance(base_url, str) or not base_url.strip():
            raise BackendConfigurationError("base_url 不能为空。")
        if not isinstance(service_token, str) or not service_token.strip():
            raise BackendConfigurationError("service_token 不能为空。")
        if type(lease_seconds) is not int or not 10 <= lease_seconds <= 600:
            raise BackendConfigurationError("lease_seconds 必须在 10 到 600 之间。")
        if (
            not isinstance(analysis_prompt_version, str)
            or not analysis_prompt_version.strip()
        ):
            raise BackendConfigurationError("analysis_prompt_version 不能为空。")
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            raise BackendConfigurationError("timeout 必须大于 0。")

        self.base_url = base_url.rstrip("/") + "/"
        self.mailbox_id = mailbox_id
        self.analysis_prompt_version = analysis_prompt_version
        self.lease_seconds = lease_seconds
        self.timeout = float(timeout)
        self._session = session or requests.Session()
        self._authorization = f"Agent {service_token}"
        self._revisions: dict[str, str] = {}
        self._jobs: dict[str, _JobContext] = {}
        self._company_jobs: dict[str, _JobContext] = {}
        self._mailbox_revisions: dict[str, str] = {}

    # 功能：释放工作单元使用的 HTTP 连接。
    # 输入：无参数，读取实例 session。
    # 输出：无返回值。
    # 逻辑：关闭当前连接池，不影响其他客户端。
    # 约束：调用者应在所有请求和工作线程结束后调用。
    def close(self):
        self._session.close()

    # 功能：保存指定邮箱来源的抽取结果并聚合业务统计。
    # 输入：`submissions` 为 L1 载荷；`source` 为 Gmail 默认来源或显式 QQ 来源。
    # 输出：创建、更新、重复数量及受影响公司 ID。
    # 逻辑：复制载荷并绑定当前 mailbox_id，经认证 HTTP 提交后严格校验响应。
    # 约束：不修改原载荷；仅允许 gmail_real/qq_real；默认 Gmail 调用行为保持不变。
    def submit_emails(
        self, submissions: list[dict[str, Any]], *, source: str = "gmail_real"
    ) -> dict[str, Any]:
        """补充 HTTP 传输字段，并把逐封结果聚合为 GmailSyncResult 所需统计。"""
        if source not in {"gmail_real", "qq_real"}:
            raise BackendContractError("不支持的真实邮箱来源。")
        if not isinstance(self.mailbox_id, str) or not self.mailbox_id.strip():
            raise BackendConfigurationError("提交邮件前必须配置 mailbox_id。")

        payload = []
        for submission in submissions:
            if not isinstance(submission, Mapping):
                raise BackendContractError("EmailSubmission 必须是对象。")
            item = copy.deepcopy(dict(submission))
            item["mailbox_id"] = self.mailbox_id
            item["source"] = source
            payload.append(item)

        response, _ = self._request("POST", "emails/", json=payload)
        if not isinstance(response, list):
            raise BackendContractError("邮件提交响应必须是数组。")

        counts = {"created": 0, "updated": 0, "duplicate": 0}
        affected: list[str] = []
        for index, raw in enumerate(response):
            if not isinstance(raw, Mapping):
                raise BackendContractError(f"邮件提交响应第 {index} 项必须是对象。")
            status = raw.get("status")
            company_id = raw.get("company_id")
            if status not in counts:
                raise BackendContractError(f"邮件提交响应状态无效：{status}")
            if not isinstance(company_id, str) or not company_id:
                raise BackendContractError("邮件提交响应缺少 company_id。")
            counts[status] += 1
            if company_id not in affected:
                affected.append(company_id)

        return {
            "created_count": counts["created"],
            "updated_count": counts["updated"],
            "duplicate_count": counts["duplicate"],
            "affected_company_ids": affected,
        }

    # 功能：查询天然键对应的已有抽取。
    # 输入：`mailbox_id` 目标邮箱标识，可空时使用方法的既定配置规则、`dedupe_key` 邮件天然键。
    # 输出：dict[str, Any] | None。
    # 逻辑：按邮箱与天然键查询，404 返回 None；验证已有抽取的身份、状态和提示版本。
    # 约束：不隐式重试；HTTP 错误与契约异常由调用者处理。
    def get_stored_email(
        self, mailbox_id: str, dedupe_key: str
    ) -> dict[str, Any] | None:
        """按天然键读取已保存邮件，用于在 L1 前复用同版本抽取。"""
        response, _ = self._request(
            "GET",
            "failed-extractions/",
            query={"mailbox_id": mailbox_id, "dedupe_key": dedupe_key},
            allowed_statuses={404},
        )
        if response is None:
            return None
        document = self._object(response, "Stored EmailSubmission")
        if document.get("dedupe_key") != dedupe_key:
            raise BackendContractError("已保存邮件的 dedupe_key 与请求不一致。")
        if not isinstance(document.get("extract_prompt_version"), str):
            raise BackendContractError("已保存邮件缺少 extract_prompt_version。")
        if document.get("extract_status") not in {
            "completed",
            "failed",
            "skipped_non_business",
        }:
            raise BackendContractError("已保存邮件的 extract_status 无效。")
        return document

    # 功能：读取公司归组。
    # 输入：`company_id` 明确的客户标识。
    # 输出：dict[str, Any]。
    # 逻辑：读取归组及 ETag，拒绝超过已领任务的版本，并缓存该客户 revision。
    # 约束：不隐式重试；HTTP 错误与契约异常由调用者处理。
    def get_company_grouping(self, company_id: str) -> dict[str, Any]:
        response, headers = self._request(
            "GET", "grouping/", query={"company_id": company_id}
        )
        document = self._object(response, "Grouping")
        revision = self._etag(headers)
        if revision is None:
            raise BackendContractError("Grouping 响应缺少 ETag。")
        active_job = self._company_jobs.get(company_id)
        if active_job is not None and revision != active_job.expected_version:
            raise BackendContractError(
                "公司上下文版本已超过当前 Job，停止本次分析以避免使用过期任务。"
            )
        self._revisions[company_id] = revision
        return document

    # 功能：读取公司业务上下文。
    # 输入：`company_id` 明确的客户标识。
    # 输出：dict[str, Any]。
    # 逻辑：要求先读取归组；携带 If-Match 读取上下文并检查返回版本一致。
    # 约束：不隐式重试；HTTP 错误与契约异常由调用者处理。
    def get_company_context(self, company_id: str) -> dict[str, Any]:
        revision = self._revisions.get(company_id)
        if revision is None:
            raise BackendContractError("读取 CompanyContext 前必须先读取 Grouping。")
        response, headers = self._request(
            "GET",
            "context/",
            query={"company_id": company_id},
            headers={"If-Match": revision},
        )
        returned_revision = self._etag(headers)
        if returned_revision is not None and returned_revision != revision:
            raise BackendContractError("Grouping 与 CompanyContext 的 ETag 不一致。")
        return self._object(response, "CompanyContext")

    # 功能：保存 L2 输入并跟踪版本。
    # 输入：`analysis_input` L2 分析输入。
    # 输出：None。
    # 逻辑：复制输入，通过已领取任务的租约和版本头提交 analysis-inputs。
    # 约束：不隐式重试；HTTP 错误与契约异常由调用者处理。
    def save_analysis_input(self, analysis_input: Mapping[str, Any]) -> None:
        document = dict(analysis_input)
        company_id = self._company_id(document)
        self._request(
            "POST",
            "analysis-inputs/",
            json=document,
            headers=self._write_headers(company_id),
        )

    # 功能：读取最近 L2 快照。
    # 输入：`company_id` 明确的客户标识。
    # 输出：dict[str, Any] | None。
    # 逻辑：按客户查询最近输入，404 返回 None，其余成功响应要求对象。
    # 约束：不隐式重试；HTTP 错误与契约异常由调用者处理。
    def get_latest_analysis_input(self, company_id: str) -> dict[str, Any] | None:
        response, _ = self._request(
            "GET",
            "latest-analysis-input/",
            query={"company_id": company_id},
            allowed_statuses={404},
        )
        if response is None:
            return None
        return self._object(response, "AnalysisInput")

    # 功能：查询指定输入和提示词的 L3 缓存。
    # 输入：`company_id` 明确的客户标识、`input_version` 指定输入版本。
    # 输出：dict[str, Any] | None。
    # 逻辑：按客户、输入和提示版本查缓存；miss/pending 返回 None，命中必须含完整分析。
    # 约束：不隐式重试；HTTP 错误与契约异常由调用者处理。
    def get_cached_analysis(
        self, company_id: str, input_version: str
    ) -> dict[str, Any] | None:
        response, _ = self._request(
            "GET",
            "cached-analysis/",
            query={
                "company_id": company_id,
                "input_version": input_version,
                "analysis_prompt_version": self.analysis_prompt_version,
            },
        )
        document = self._object(response, "CachedAnalysis")
        if document.get("hit") is False or document.get("status") == "pending":
            return None

        nested = document.get("analysis")
        if isinstance(nested, Mapping):
            return copy.deepcopy(dict(nested))
        if "list_view" in document and "detail_view" in document:
            return document
        raise BackendContractError(
            "cached-analysis 命中时必须返回完整 Analysis；当前后端只返回缓存元数据。"
        )

    # 功能：提交 L3 分析。
    # 输入：`analysis` L3 分析结果。
    # 输出：None。
    # 逻辑：复制分析数据，附加公司已领任务的租约与版本头后提交 analyses。
    # 约束：不隐式重试；HTTP 错误与契约异常由调用者处理。
    def save_analysis(self, analysis: Mapping[str, Any]) -> None:
        document = dict(analysis)
        company_id = self._company_id(document)
        self._request(
            "POST",
            "analyses/",
            json=document,
            headers=self._write_headers(company_id),
        )

    # 功能：提交 L4 评分。
    # 输入：`score` L4 评分。
    # 输出：None。
    # 逻辑：复制评分数据，附加公司已领任务的租约与版本头后提交 scores。
    # 约束：不隐式重试；HTTP 错误与契约异常由调用者处理。
    def save_score(self, score: Mapping[str, Any]) -> None:
        document = dict(score)
        company_id = self._company_id(document)
        self._request(
            "POST",
            "scores/",
            json=document,
            headers=self._write_headers(company_id),
        )

    # 功能：领取并缓存公司租约。
    # 输入：`limit` 领取数量上限。
    # 输出：list[dict[str, Any]]。
    # 逻辑：发送领取数量和租约时长，验证响应身份并缓存任务租约及公司版本，返回简化任务列表。
    # 约束：不隐式重试；HTTP 错误与契约异常由调用者处理。
    def claim_jobs(self, limit: int) -> list[dict[str, Any]]:
        response, _ = self._request(
            "POST",
            "jobs/claim/",
            json={"limit": limit, "lease_seconds": self.lease_seconds},
        )
        if not isinstance(response, list):
            raise BackendContractError("Job claim 响应必须是数组。")

        jobs = []
        for index, raw in enumerate(response):
            if not isinstance(raw, Mapping):
                raise BackendContractError(f"Job claim 响应第 {index} 项必须是对象。")
            payload = raw.get("payload")
            company_id = raw.get("company_id")
            if company_id is None and isinstance(payload, Mapping):
                company_id = payload.get("company_id")
            job_id = raw.get("job_id")
            token = raw.get("lease_token")
            version = raw.get("expected_version")
            if not all(
                isinstance(value, (str, int))
                for value in (job_id, company_id, token, version)
            ):
                raise BackendContractError(
                    "Job 缺少 job_id、company_id、lease_token 或 expected_version。"
                )

            context = _JobContext(
                job_id=str(job_id),
                company_id=str(company_id),
                expected_version=str(version),
                lease_token=str(token),
            )
            self._jobs[context.job_id] = context
            self._company_jobs[context.company_id] = context
            self._revisions[context.company_id] = context.expected_version
            jobs.append(
                {
                    "job_id": context.job_id,
                    "trigger": raw.get("trigger"),
                    "company_id": context.company_id,
                    "enqueued_at": raw.get("enqueued_at"),
                }
            )
        return jobs

    # 功能：回报公司任务。
    # 输入：`report` 任务结果载荷。
    # 输出：None。
    # 逻辑：要求本实例已领取任务，带租约回报，成功后清理对应任务缓存。
    # 约束：不隐式重试；HTTP 错误与契约异常由调用者处理。
    def report_job(self, report: Mapping[str, Any]) -> None:
        document = dict(report)
        job_id = document.get("job_id")
        context = self._jobs.get(str(job_id))
        if context is None:
            raise BackendContractError("JobReport 没有对应的已领取任务。")
        self._request(
            "POST",
            "jobs/report/",
            json=document,
            headers={"X-Lease-Token": context.lease_token},
        )
        self._jobs.pop(context.job_id, None)
        if self._company_jobs.get(context.company_id) == context:
            self._company_jobs.pop(context.company_id, None)

    # 功能：旧 CLI 领取 Gmail 同步。
    # 输入：`limit` 领取数量上限。
    # 输出：list[dict[str, Any]]。
    # 逻辑：只领取员工明确请求的 Gmail 同步任务，校验邮箱、授权对象和数量字段。
    # 约束：不隐式重试；HTTP 错误与契约异常由调用者处理。
    def claim_mailbox_syncs(self, limit: int) -> list[dict[str, Any]]:
        """领取当前员工在网页中请求的 Gmail 同步任务。"""
        response, _ = self._request(
            "POST", "mailbox-syncs/claim/", json={"limit": limit}
        )
        if not isinstance(response, list):
            raise BackendContractError("Mailbox sync claim 响应必须是数组。")
        claims: list[dict[str, Any]] = []
        for index, raw in enumerate(response):
            if not isinstance(raw, Mapping):
                raise BackendContractError(
                    f"Mailbox sync claim 第 {index} 项必须是对象。"
                )
            required = {
                "mailbox_id": raw.get("mailbox_id"),
                "mailbox_address": raw.get("mailbox_address"),
                "authorization": raw.get("authorization"),
                "max_results": raw.get("max_results"),
            }
            if not isinstance(required["mailbox_id"], str) or not isinstance(
                required["mailbox_address"], str
            ):
                raise BackendContractError("Mailbox sync claim 缺少邮箱标识或地址。")
            if not isinstance(required["authorization"], Mapping):
                raise BackendContractError("Mailbox sync claim 缺少授权信息。")
            if type(required["max_results"]) is not int:
                raise BackendContractError("Mailbox sync claim 的 max_results 无效。")
            claims.append(copy.deepcopy(dict(raw)))
        return claims

    # 功能：旧 CLI 回报 Gmail 同步。
    # 输入：`report` 任务结果载荷。
    # 输出：dict[str, Any]。
    # 逻辑：提交一次同步结果和可能更新的授权，要求返回对象。
    # 约束：不隐式重试；HTTP 错误与契约异常由调用者处理。
    def report_mailbox_sync(self, report: Mapping[str, Any]) -> dict[str, Any]:
        """向后端回报一次 Gmail 同步，并保存可能刷新的授权。"""
        response, _ = self._request("POST", "mailbox-syncs/report/", json=dict(report))
        return self._object(response, "Mailbox sync report")

    # 功能：读取邮箱游标和 ETag。
    # 输入：`mailbox_id` 目标邮箱标识，可空时使用方法的既定配置规则。
    # 输出：dict[str, Any]。
    # 逻辑：查询邮箱游标，核验身份、整数版本与 ETag，并缓存乐观锁版本。
    # 约束：不隐式重试；HTTP 错误与契约异常由调用者处理。
    def get_sync_state(self, mailbox_id: str) -> dict[str, Any]:
        """读取邮箱历史游标，并保存后端返回的乐观锁版本。"""
        response, headers = self._request(
            "GET", "sync-state/", query={"mailbox_id": mailbox_id}
        )
        document = self._object(response, "SyncState")
        revision = self._etag(headers)
        if revision is None:
            raise BackendContractError("SyncState 响应缺少 ETag。")
        if str(document.get("mailbox_id")) != mailbox_id:
            raise BackendContractError("SyncState 的 mailbox_id 与请求不一致。")
        if type(document.get("version")) is not int:
            raise BackendContractError("SyncState 响应缺少整数 version。")
        self._mailbox_revisions[mailbox_id] = revision
        return document

    # 功能：条件更新邮箱游标。
    # 输入：`sync_state` 邮箱增量游标载荷。
    # 输出：dict[str, Any]。
    # 逻辑：要求先读取邮箱状态，以缓存的 If-Match 提交并更新返回版本。
    # 约束：不隐式重试；HTTP 错误与契约异常由调用者处理。
    def save_sync_state(self, sync_state: Mapping[str, Any]) -> dict[str, Any]:
        """保存成功邮件批次对应的 Gmail historyId 增量游标。"""
        document = dict(sync_state)
        mailbox_id = document.get("mailbox_id")
        if not isinstance(mailbox_id, str) or not mailbox_id:
            raise BackendContractError("SyncState 载荷缺少 mailbox_id。")
        revision = self._mailbox_revisions.get(mailbox_id)
        if revision is None:
            raise BackendContractError("保存 SyncState 前必须先读取当前状态。")
        response, headers = self._request(
            "POST",
            "sync-state-save/",
            json=document,
            headers={"If-Match": revision},
        )
        saved = self._object(response, "SyncState")
        returned_revision = self._etag(headers)
        if returned_revision is None:
            raise BackendContractError("保存 SyncState 的响应缺少 ETag。")
        self._mailbox_revisions[mailbox_id] = returned_revision
        return saved

    def claim_answer_request(self) -> dict[str, Any] | None:
        """功能：领取零个或一个工作空间聊天请求。
        输入：无外部参数，使用实例 HTTP 认证与服务地址。
        输出：请求字典或 None；缺字段、空字符串等非法响应抛 BackendContractError。
        逻辑：请求一次 claim 并验证稳定标识；过渡期将后端的 company_id:null 归一为无预选公司字段。
        约束：拒绝非空 company_id，不在传输层重试或改写员工身份。
        """
        response, _ = self._request("POST", "chat/requests/claim/", json={})
        document = self._object(response, "Answer request claim")
        if "request" not in document:
            raise BackendContractError("Answer request claim 响应缺少 request。")

        raw_request = document["request"]
        if raw_request is None:
            return None
        if not isinstance(raw_request, Mapping):
            raise BackendContractError(
                "Answer request claim 的 request 必须是对象或 null。"
            )

        request = copy.deepcopy(dict(raw_request))
        for field in (
            "request_id",
            "conversation_id",
            "user_message_id",
        ):
            self._required_string(request, field, "Answer request claim")
        if "company_id" in request:
            if request.pop("company_id") is not None:
                raise BackendContractError("工作空间聊天请求不能绑定公司。")
        return request

    # 功能：映射聊天上下文接口。
    # 输入：`request_id` 聊天请求标识、`scope` internal 或 external 知识范围。
    # 输出：dict[str, Any]。
    # 逻辑：请求已领任务的指定知识范围，验证请求身份、来源数组、检索状态及缺口。
    # 约束：不隐式重试；HTTP 错误与契约异常由调用者处理。
    def get_answer_context(self, request_id: str, scope: str) -> dict[str, Any]:
        """读取当前聊天请求绑定的客户及指定知识范围上下文。"""
        self._required_string(
            {"request_id": request_id}, "request_id", "Answer context 请求"
        )
        if scope not in {"internal", "external"}:
            raise BackendContractError(
                "Answer context scope 必须是 internal 或 external。"
            )

        response, _ = self._request(
            "POST",
            "chat/context/",
            json={"request_id": request_id, "scope": scope},
        )
        document = self._object(response, "Answer context")
        if document.get("request_id") != request_id:
            raise BackendContractError("Answer context 的 request_id 与请求不一致。")
        if document.get("scope") != scope:
            raise BackendContractError("Answer context 的 scope 与请求不一致。")

        customer_context = document.get("customer_context")
        context_items = document.get("context_items")
        self._object_list(customer_context, "Answer context customer_context")
        self._object_list(context_items, "Answer context context_items")

        customer_status = document.get("customer_context_status")
        allowed_customer_statuses = (
            {"completed", "failed"} if scope == "internal" else {"not_applicable"}
        )
        if customer_status not in allowed_customer_statuses:
            raise BackendContractError(
                "Answer context 的 customer_context_status 与 scope 不一致。"
            )
        if scope == "external" and customer_context:
            raise BackendContractError(
                "External answer context 不得包含 customer_context。"
            )

        if document.get("knowledge_status") not in {"completed", "failed"}:
            raise BackendContractError("Answer context 的 knowledge_status 无效。")
        self._retrieval_gaps(document.get("retrieval_gaps"))
        if type(document.get("external_available")) is not bool:
            raise BackendContractError(
                "Answer context 的 external_available 必须是布尔值。"
            )
        if scope == "external" and document["external_available"] is not True:
            raise BackendContractError(
                "External answer context 的 external_available 必须为 true。"
            )
        return document

    # 功能：发现请求实际获准的读取及实验维护工具目录。
    # 输入：`request_id` 当前员工的请求 UUID；具体实现读取实例认证配置。
    # 输出：JSON 对象；具体实现失败时抛出请求或契约异常。
    # 逻辑：一次 GET 并检查协议版本、请求、页码与数量。
    # 约束：只允许当前员工已授权的请求；不接受身份覆盖、写工具或隐式重试。
    def get_chat_tools(self, request_id: str) -> dict[str, Any]:
        """读取本次 processing 请求可用的工具目录，不从全局注册表猜测权限。"""
        self._required_string({"request_id": request_id}, "request_id", "Chat tools 请求")
        response, _ = self._request(
            "GET", "chat/tools/", query={"request_id": request_id, "page": 1, "page_size": 30}
        )
        document = self._object(response, "Chat tools")
        if (
            document.get("contract_version") != "chat-tools-v1"
            or document.get("request_id") != request_id
            or type(document.get("count")) is not int
            or type(document.get("page")) is not int
            or type(document.get("page_size")) is not int
            or document["page"] != 1
            or document["count"] > document["page_size"]
        ):
            raise BackendContractError("Chat tools 目录版本、请求或分页不一致。")
        tools = document.get("tools")
        self._object_list(tools, "Chat tools tools")
        if len(tools) != document["count"]:
            raise BackendContractError("Chat tools 目录数量不一致。")
        return document

    # 功能：读取本人请求的权威状态。
    # 输入：`request_id` 当前员工的请求 UUID；具体实现读取实例认证配置。
    # 输出：JSON 对象；具体实现失败时抛出请求或契约异常。
    # 逻辑：一次 GET 并核对请求 ID 及合法状态。
    # 约束：只允许当前员工已授权的请求；不接受身份覆盖、写工具或隐式重试。
    def get_chat_request_status(self, request_id: str) -> dict[str, Any]:
        """只查询本人聊天请求的已保存状态，不重新领取或生成回答。"""
        self._required_string({"request_id": request_id}, "request_id", "Chat status 请求")
        response, _ = self._request(
            "GET", f"chat/requests/{quote(request_id, safe='')}/"
        )
        document = self._object(response, "Chat request status")
        if (
            document.get("request_id") != request_id
            or document.get("status") not in {"pending", "processing", "completed", "failed"}
        ):
            raise BackendContractError("Chat request status 与本次请求不一致。")
        return document

    # 功能：执行客户读取或共享实验维护并取得登记证据。
    # 输入：`request_id` 当前员工的请求 UUID、`name` 固定读取及实验维护工具名称、`arguments` JSON 参数；具体实现读取实例认证配置。
    # 输出：JSON 对象；具体实现失败时抛出请求或契约异常。
    # 逻辑：核对固定名称与参数对象，一次 POST 并核对响应归属及证据数组。
    # 约束：只允许当前员工已授权的请求；不接受身份覆盖、写工具或隐式重试。
    def read_chat_tool(
        self, request_id: str, name: str, arguments: Mapping[str, Any]
    ) -> dict[str, Any]:
        """调用请求绑定的读取及实验维护工具；工具结果和证据由后端共同确认。"""
        self._required_string({"request_id": request_id}, "request_id", "Chat tool 请求")
        if name not in WORKSPACE_TOOLS:
            raise BackendContractError("Chat tool 仅支持已登记的客户读取及实验维护。")
        if not isinstance(arguments, Mapping):
            raise BackendContractError("Chat tool arguments 必须是对象。")
        response, _ = self._request(
            "POST",
            "chat/tool-reads/",
            json={"request_id": request_id, "name": name, "arguments": dict(arguments)},
        )
        document = self._object(response, "Chat tool")
        if document.get("request_id") != request_id or document.get("tool") != name:
            raise BackendContractError("Chat tool 响应与本次请求或工具不一致。")
        if document.get("status") != "completed":
            raise BackendContractError("Chat tool 未确认工具操作完成。")
        if not isinstance(document.get("data"), Mapping):
            raise BackendContractError("Chat tool data 必须是对象。")
        self._object_list(document.get("evidence_items"), "Chat tool evidence_items")
        return document

    # 功能：映射聊天回答保存接口。
    # 输入：`result` 包含提示版本的聊天结果。
    # 输出：dict[str, Any]。
    # 逻辑：复制 completed/failed 载荷并提交一次；核对 saved、duplicate 和助手消息标识。
    # 约束：不隐式重试；HTTP 错误与契约异常由调用者处理。
    def report_answer(self, result: Mapping[str, Any]) -> dict[str, Any]:
        """回报一次稳定的 completed 或 failed 聊天结果。"""
        if not isinstance(result, Mapping):
            raise BackendContractError("Report answer 载荷必须是对象。")
        payload = copy.deepcopy(dict(result))
        request_id = self._required_string(payload, "request_id", "Report answer")
        self._required_string(payload, "chat_prompt_version", "Report answer")
        status = payload.get("status")
        if status not in {"completed", "failed"}:
            raise BackendContractError(
                "Report answer 的 status 必须是 completed 或 failed。"
            )

        response, _ = self._request("POST", "chat/answers/", json=payload)
        document = self._object(response, "Report answer")
        if document.get("request_id") != request_id:
            raise BackendContractError("Report answer 响应的 request_id 与请求不一致。")
        if document.get("saved") is not True:
            raise BackendContractError("Report answer 响应未确认保存。")
        if type(document.get("duplicate")) is not bool:
            raise BackendContractError("Report answer 响应的 duplicate 必须是布尔值。")
        if "assistant_message_id" not in document:
            raise BackendContractError("Report answer 响应缺少 assistant_message_id。")
        assistant_message_id = document["assistant_message_id"]
        if status == "completed":
            if (
                not isinstance(assistant_message_id, str)
                or not assistant_message_id.strip()
            ):
                raise BackendContractError(
                    "Completed report 响应缺少 assistant_message_id。"
                )
        elif assistant_message_id is not None and (
            not isinstance(assistant_message_id, str)
            or not assistant_message_id.strip()
        ):
            raise BackendContractError(
                "Failed report 响应的 assistant_message_id 无效。"
            )
        return document

    # 功能：读取并校验非空字符串。
    # 输入：`document` 响应映射、`field` 待取字段名、`name` 安全错误定位标签。
    # 输出：str。
    # 逻辑：要求指定字段为非空字符串，失败抛 BackendContractError。
    # 约束：不隐式重试；HTTP 错误与契约异常由调用者处理。
    @staticmethod
    def _required_string(document: Mapping[str, Any], field: str, name: str) -> str:
        value = document.get(field)
        if not isinstance(value, str) or not value.strip():
            raise BackendContractError(f"{name} 缺少 {field}。")
        return value

    # 功能：验证对象数组。
    # 输入：`value` 待校验值、`name` 安全错误定位标签。
    # 输出：None。
    # 逻辑：要求 list 中每项为映射，不转换非法值。
    # 约束：不隐式重试；HTTP 错误与契约异常由调用者处理。
    @staticmethod
    def _object_list(value: object, name: str) -> None:
        if not isinstance(value, list):
            raise BackendContractError(f"{name} 必须是数组。")
        if any(not isinstance(item, Mapping) for item in value):
            raise BackendContractError(f"{name} 的每一项都必须是对象。")

    # 功能：验证三字段资料缺口数组。
    # 输入：`value` 待校验值。
    # 输出：None。
    # 逻辑：要求每项恰好有 scope/code/message，且均为非空字符串。
    # 约束：不隐式重试；HTTP 错误与契约异常由调用者处理。
    @classmethod
    def _retrieval_gaps(cls, value: object) -> None:
        if not isinstance(value, list):
            raise BackendContractError("Answer context 的 retrieval_gaps 必须是数组。")
        required_fields = {"scope", "code", "message"}
        for gap in value:
            if not isinstance(gap, Mapping) or set(gap) != required_fields:
                raise BackendContractError(
                    "Answer context retrieval gap 必须仅包含 scope、code 和 message。"
                )
            for field in required_fields:
                cls._required_string(gap, field, "Answer context retrieval gap")

    # 功能：构造写请求所需租约与版本头。
    # 输入：`company_id` 明确的客户标识。
    # 输出：dict[str, str]。
    # 逻辑：从已领取公司任务取租约和已读版本；未领取任务则拒绝写入。
    # 约束：不隐式重试；HTTP 错误与契约异常由调用者处理。
    def _write_headers(self, company_id: str) -> dict[str, str]:
        context = self._company_jobs.get(company_id)
        if context is None:
            raise BackendContractError("保存分析结果前必须先领取该公司的任务。")
        revision = self._revisions.get(company_id, context.expected_version)
        return {
            "If-Match": revision,
            "X-Job-ID": context.job_id,
            "X-Lease-Token": context.lease_token,
        }

    # 功能：发起认证 HTTP 并规范化失败。
    # 输入：`method` HTTP 方法、`path` 相对接口路径、`query` 可选查询字段、`json` 可选 JSON 载荷、`headers` HTTP 头映射、`allowed_statuses` 可按无内容处理的显式状态码集合。
    # 输出：tuple[object, Mapping[str, str]]。
    # 逻辑：合并员工认证与请求头，发送一次 HTTP；规范网络及非成功响应错误，解析成功 JSON。
    # 约束：不隐式重试；HTTP 错误与契约异常由调用者处理。
    def _request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        json: object | None = None,
        headers: Mapping[str, str] | None = None,
        allowed_statuses: set[int] | None = None,
    ) -> tuple[object, Mapping[str, str]]:
        request_headers = {"Authorization": self._authorization}
        if headers:
            request_headers.update(headers)
        url = self.base_url + path.lstrip("/")
        if query:
            url += "?" + urlencode(query)
        started = perf_counter()
        try:
            response = self._session.request(
                method,
                url,
                json=json,
                headers=request_headers,
                timeout=self.timeout,
            )
        except requests.RequestException as error:
            logger.warning(
                "backend_request_failed method=%s path=%s stage=network error_type=%s duration_ms=%s",
                method, path, type(error).__name__, round((perf_counter() - started) * 1000),
            )
            raise BackendRequestError(
                0, "network_error", type(error).__name__
            ) from None

        if allowed_statuses and response.status_code in allowed_statuses:
            return None, response.headers
        if not 200 <= response.status_code < 300:
            code, detail, scope = self._error(response)
            logger.warning(
                "backend_request_failed method=%s path=%s status=%s code=%s request_id=%s duration_ms=%s",
                method, path, response.status_code, code,
                response.headers.get("X-Request-ID"), round((perf_counter() - started) * 1000),
            )
            raise BackendRequestError(response.status_code, code, detail, scope=scope)
        logger.debug(
            "backend_request_completed method=%s path=%s status=%s duration_ms=%s",
            method, path, response.status_code, round((perf_counter() - started) * 1000),
        )
        if response.status_code == 204 or not response.content:
            return None, response.headers
        try:
            return response.json(), response.headers
        except ValueError:
            logger.warning(
                "backend_request_failed method=%s path=%s stage=response_json status=%s",
                method, path, response.status_code,
            )
            raise BackendContractError("后端成功响应不是有效 JSON。") from None

    # 功能：验证并提取公司 ID。
    # 输入：`document` 响应映射。
    # 输出：str。
    # 逻辑：要求映射含非空字符串 company_id，否则抛契约异常。
    # 约束：不隐式重试；HTTP 错误与契约异常由调用者处理。
    @staticmethod
    def _company_id(document: Mapping[str, Any]) -> str:
        company_id = document.get("company_id")
        if not isinstance(company_id, str) or not company_id:
            raise BackendContractError("载荷缺少 company_id。")
        return company_id

    # 功能：要求响应为对象。
    # 输入：`value` 待校验值、`name` 安全错误定位标签。
    # 输出：dict[str, Any]。
    # 逻辑：要求映射后返回深拷贝字典，避免调用者修改原响应。
    # 约束：不隐式重试；HTTP 错误与契约异常由调用者处理。
    @staticmethod
    def _object(value: object, name: str) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise BackendContractError(f"{name} 响应必须是对象。")
        return copy.deepcopy(dict(value))

    # 功能：提取响应版本头。
    # 输入：`headers` HTTP 头映射。
    # 输出：str | None。
    # 逻辑：兼容 ETag 头大小写并去除双引号，缺失返回 None。
    # 约束：不隐式重试；HTTP 错误与契约异常由调用者处理。
    @staticmethod
    def _etag(headers: Mapping[str, str]) -> str | None:
        value = headers.get("ETag") or headers.get("etag")
        return value.strip('"') if isinstance(value, str) and value else None

    # 功能：规范化 API 错误。
    # 输入：`response` HTTP 响应。
    # 输出：tuple[str, str]。
    # 逻辑：提取统一 error 对象的 code/detail；非法错误体返回明确的通用 HTTP 错误说明。
    # 约束：不隐式重试；HTTP 错误与契约异常由调用者处理。
    @staticmethod
    def _error(response: Any) -> tuple[str, str, str | None]:
        try:
            payload = response.json()
        except ValueError:
            return "http_error", "后端返回非 JSON 错误。", None
        error = payload.get("error") if isinstance(payload, Mapping) else None
        if not isinstance(error, Mapping):
            return "http_error", "后端请求被拒绝。", None
        code = error.get("code")
        detail = error.get("detail")
        scope = error.get("scope")
        return (
            str(code or "http_error"),
            str(detail or "后端请求被拒绝。"),
            scope if scope in {"tool", "request"} else None,
        )


# 功能：构造独立 HTTP 客户端，支持 CLI 环境身份或 Worker 的显式任务身份。
# 输入：`mailbox_id` 为目标邮箱；`service_token` 为可选任务令牌；其余连接参数读取环境。
# 输出：无共享可变状态的 DjangoBackendClient；无效配置抛 BackendConfigurationError。
# 逻辑：显式令牌时只采用传入邮箱，避免继承其他员工的环境邮箱；未提供令牌时保留 CLI 行为。
# 约束：不写入进程环境，不改变既定超时、租约或分析提示词版本。
def django_backend_from_environment(
    *, mailbox_id: str | None = None, service_token: str | None = None
) -> DjangoBackendClient:
    """从连接配置与可选独立任务身份创建后端适配器。"""
    base_url = os.getenv(
        "SALESMATE_BACKEND_AGENT_URL",
        "http://127.0.0.1:8000/api/v1/agent/",
    )
    resolved_mailbox = (
        mailbox_id
        if service_token is not None
        else mailbox_id or os.getenv("SALESMATE_MAILBOX_ID")
    )
    service_token = (
        service_token
        if service_token is not None
        else os.getenv("SALESMATE_AGENT_SERVICE_TOKEN", "")
    )
    try:
        lease_seconds = int(os.getenv("SALESMATE_JOB_LEASE_SECONDS", "120"))
        timeout = float(os.getenv("SALESMATE_BACKEND_TIMEOUT", "30"))
    except ValueError:
        raise BackendConfigurationError(
            "后端 timeout 或任务 lease_seconds 配置无效。"
        ) from None
    return DjangoBackendClient(
        base_url,
        service_token,
        mailbox_id=resolved_mailbox,
        analysis_prompt_version=_DEFAULT_ANALYSIS_PROMPT_VERSION,
        lease_seconds=lease_seconds,
        timeout=timeout,
    )


__all__ = [
    "BackendClient",
    "BackendConfigurationError",
    "BackendContractError",
    "BackendRequestError",
    "BackendRetrievalError",
    "DjangoBackendClient",
    "JsonObject",
    "django_backend_from_environment",
]
