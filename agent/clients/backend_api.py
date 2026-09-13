"""把 Agent 的最小 BackendClient 协议映射到 Django Agent HTTP API。"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from typing import Any, Mapping, Protocol
from urllib.parse import urlencode

import requests

JsonObject = Mapping[str, Any]


class BackendClient(Protocol):
    """L1–L4 workflow 使用的最小真实后端接口。"""

    def submit_emails(self, submissions: list[dict[str, Any]]) -> JsonObject: ...

    def get_stored_email(
        self, mailbox_id: str, dedupe_key: str
    ) -> JsonObject | None: ...

    def get_company_grouping(self, company_id: str) -> JsonObject: ...

    def get_company_context(self, company_id: str) -> JsonObject: ...

    def save_analysis_input(self, analysis_input: JsonObject) -> None: ...

    def get_latest_analysis_input(self, company_id: str) -> JsonObject | None: ...

    def get_cached_analysis(
        self, company_id: str, input_version: str
    ) -> JsonObject | None: ...

    def save_analysis(self, analysis: JsonObject) -> None: ...

    def save_score(self, score: JsonObject) -> None: ...

    def claim_jobs(self, limit: int) -> list[dict[str, Any]]: ...

    def report_job(self, report: JsonObject) -> None: ...

    def claim_mailbox_syncs(self, limit: int) -> list[dict[str, Any]]: ...

    def report_mailbox_sync(self, report: JsonObject) -> JsonObject: ...

    def get_sync_state(self, mailbox_id: str) -> JsonObject: ...

    def save_sync_state(self, sync_state: JsonObject) -> JsonObject: ...


class BackendRetrievalError(RuntimeError):
    """后端数据读取失败。"""


class BackendConfigurationError(ValueError):
    """真实后端适配器缺少必要配置。"""


class BackendContractError(RuntimeError):
    """后端响应无法映射为 Agent README 约定的数据。"""


class BackendRequestError(RuntimeError):
    """后端请求失败，且不暴露服务凭证。"""

    def __init__(self, status_code: int, code: str, detail: str):
        super().__init__(f"后端请求失败（HTTP {status_code}, {code}）：{detail}")
        self.status_code = status_code
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class _JobContext:
    job_id: str
    company_id: str
    expected_version: str
    lease_token: str


class DjangoBackendClient:
    """调用当前 Django 后端，同时向 Agent 暴露简化后的同步协议。

    租约、ETag 和嵌套 Job payload 都属于当前 Django HTTP 传输层，
    由本类吸收；L2-L4 workflow 仍只依赖 ``BackendClient`` 的简单方法。
    """

    def __init__(
        self,
        base_url: str,
        service_token: str,
        *,
        mailbox_id: str | None = None,
        analysis_prompt_version: str = "analysis-v2",
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
        if not isinstance(analysis_prompt_version, str) or not analysis_prompt_version.strip():
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

    def submit_emails(
        self, submissions: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """补充 HTTP 传输字段，并把逐封结果聚合为 GmailSyncResult 所需统计。"""
        if not isinstance(self.mailbox_id, str) or not self.mailbox_id.strip():
            raise BackendConfigurationError("提交 Gmail 邮件前必须配置 mailbox_id。")

        payload = []
        for submission in submissions:
            if not isinstance(submission, Mapping):
                raise BackendContractError("EmailSubmission 必须是对象。")
            item = copy.deepcopy(dict(submission))
            item["mailbox_id"] = self.mailbox_id
            item["source"] = "gmail_real"
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

    def save_analysis_input(self, analysis_input: Mapping[str, Any]) -> None:
        document = dict(analysis_input)
        company_id = self._company_id(document)
        self._request(
            "POST",
            "analysis-inputs/",
            json=document,
            headers=self._write_headers(company_id),
        )

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

    def save_analysis(self, analysis: Mapping[str, Any]) -> None:
        document = dict(analysis)
        company_id = self._company_id(document)
        self._request(
            "POST",
            "analyses/",
            json=document,
            headers=self._write_headers(company_id),
        )

    def save_score(self, score: Mapping[str, Any]) -> None:
        document = dict(score)
        company_id = self._company_id(document)
        self._request(
            "POST",
            "scores/",
            json=document,
            headers=self._write_headers(company_id),
        )

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
            if not all(isinstance(value, (str, int)) for value in (job_id, company_id, token, version)):
                raise BackendContractError("Job 缺少 job_id、company_id、lease_token 或 expected_version。")

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

    def report_mailbox_sync(self, report: Mapping[str, Any]) -> dict[str, Any]:
        """向后端回报一次 Gmail 同步，并保存可能刷新的授权。"""
        response, _ = self._request(
            "POST", "mailbox-syncs/report/", json=dict(report)
        )
        return self._object(response, "Mailbox sync report")

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
        try:
            response = self._session.request(
                method,
                url,
                json=json,
                headers=request_headers,
                timeout=self.timeout,
            )
        except requests.RequestException as error:
            raise BackendRequestError(0, "network_error", type(error).__name__) from None

        if allowed_statuses and response.status_code in allowed_statuses:
            return None, response.headers
        if not 200 <= response.status_code < 300:
            code, detail = self._error(response)
            raise BackendRequestError(response.status_code, code, detail)
        if response.status_code == 204 or not response.content:
            return None, response.headers
        try:
            return response.json(), response.headers
        except ValueError:
            raise BackendContractError("后端成功响应不是有效 JSON。") from None

    @staticmethod
    def _company_id(document: Mapping[str, Any]) -> str:
        company_id = document.get("company_id")
        if not isinstance(company_id, str) or not company_id:
            raise BackendContractError("载荷缺少 company_id。")
        return company_id

    @staticmethod
    def _object(value: object, name: str) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise BackendContractError(f"{name} 响应必须是对象。")
        return copy.deepcopy(dict(value))

    @staticmethod
    def _etag(headers: Mapping[str, str]) -> str | None:
        value = headers.get("ETag") or headers.get("etag")
        return value.strip('"') if isinstance(value, str) and value else None

    @staticmethod
    def _error(response: Any) -> tuple[str, str]:
        try:
            payload = response.json()
        except ValueError:
            return "http_error", "后端返回非 JSON 错误。"
        error = payload.get("error") if isinstance(payload, Mapping) else None
        if not isinstance(error, Mapping):
            return "http_error", "后端请求被拒绝。"
        code = error.get("code")
        detail = error.get("detail")
        return str(code or "http_error"), str(detail or "后端请求被拒绝。")


def django_backend_from_environment(
    *, mailbox_id: str | None = None
) -> DjangoBackendClient:
    """从已加载的 Agent 环境变量创建真实 Django 后端适配器。"""
    base_url = os.getenv(
        "SALESMATE_BACKEND_AGENT_URL",
        "http://127.0.0.1:8000/api/v1/agent/",
    )
    service_token = os.getenv("SALESMATE_AGENT_SERVICE_TOKEN", "")
    resolved_mailbox = mailbox_id or os.getenv("SALESMATE_MAILBOX_ID")
    analysis_prompt_version = os.getenv(
        "SALESMATE_ANALYSIS_PROMPT_VERSION", "analysis-v2"
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
        analysis_prompt_version=analysis_prompt_version,
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
