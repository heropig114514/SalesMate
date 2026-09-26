"""Responsibility: Map the minimal BackendClient workflow protocol to the Django Agent HTTP API.
Implementation: Maintain identity, ETag, and lease context; email submission defaults to gmail_real, with qq_real explicitly selected for QQ.
Relationships: Gmail/QQ workers share this transport; L2-L4 retain their protocols, parameters, and prompt versions.
Directory:
- BackendClient: Declare the minimal L1-L4 backend protocol.
- BackendClient.submit_emails: Declare the standard L1 email submission interface.
- BackendClient.get_stored_email: Look up an existing extraction by natural key.
- BackendClient.get_company_grouping: Read company grouping.
- BackendClient.get_company_context: Read company business context.
- BackendClient.save_analysis_input: Save L2 input and track its version.
- BackendClient.get_latest_analysis_input: Read the latest L2 snapshot.
- BackendClient.get_cached_analysis: Look up the L3 cache for an input and prompt version.
- BackendClient.save_analysis: Submit L3 analysis.
- BackendClient.save_score: Submit L4 scores.
- BackendClient.claim_jobs: Claim and cache company leases.
- BackendClient.report_job: Report a company job result.
- BackendClient.claim_mailbox_syncs: Claim Gmail synchronization through the legacy CLI.
- BackendClient.report_mailbox_sync: Report Gmail synchronization through the legacy CLI.
- BackendClient.get_sync_state: Read the mailbox cursor and ETag.
- BackendClient.save_sync_state: Conditionally update the mailbox cursor.
- BackendClient.claim_answer_request: Claim one workspace chat answer request.
- BackendClient.get_answer_context: Read customer and knowledge context bound to a request.
- BackendClient.get_chat_tools: Discover this request's read and experiment maintenance tools.
- BackendClient.get_chat_request_status: Read the current employee's request status.
- BackendClient.read_chat_tool: Execute a customer or shared experiment query and validate the response.
- BackendClient.report_answer: Report a chat result with its prompt version.
- BackendRetrievalError: Represent backend retrieval failure.
- BackendConfigurationError: Represent configuration that does not meet call prerequisites.
- BackendContractError: Represent a response contract violation.
- BackendRequestError: Safe request exception containing an HTTP status.
- BackendRequestError.__init__: Store HTTP status, error code, and safe detail.
- _JobContext: Store company job and lease context.
- DjangoBackendClient: Map simplified workflow calls to authenticated HTTP endpoints.
- DjangoBackendClient.__init__: Validate initialization parameters and establish instance state.
- DjangoBackendClient.close: Release this instance's HTTP connection pool.
- DjangoBackendClient.submit_emails: Submit emails and aggregate results, supporting default Gmail or explicitly selected QQ sources.
- DjangoBackendClient.get_stored_email: Look up an existing extraction by natural key.
- DjangoBackendClient.get_company_grouping: Read company grouping.
- DjangoBackendClient.get_company_context: Read company business context.
- DjangoBackendClient.save_analysis_input: Save L2 input and track its version.
- DjangoBackendClient.get_latest_analysis_input: Read the latest L2 snapshot.
- DjangoBackendClient.get_cached_analysis: Look up the L3 cache for an input and prompt version.
- DjangoBackendClient.save_analysis: Submit L3 analysis.
- DjangoBackendClient.save_score: Submit L4 scores.
- DjangoBackendClient.claim_jobs: Claim and cache company leases.
- DjangoBackendClient.report_job: Report a company job result.
- DjangoBackendClient.claim_mailbox_syncs: Claim Gmail synchronization through the legacy CLI.
- DjangoBackendClient.report_mailbox_sync: Report Gmail synchronization through the legacy CLI.
- DjangoBackendClient.get_sync_state: Read the mailbox cursor and ETag.
- DjangoBackendClient.save_sync_state: Conditionally update the mailbox cursor.
- DjangoBackendClient.claim_answer_request: Map workspace chat claims and remove the transitional null company field.
- DjangoBackendClient.get_answer_context: Map the chat context endpoint.
- DjangoBackendClient.get_chat_tools: Discover this request's read and experiment maintenance tools.
- DjangoBackendClient.get_chat_request_status: Read the current employee's request status.
- DjangoBackendClient.read_chat_tool: Execute a customer or shared experiment query and validate the response.
- DjangoBackendClient.report_answer: Map the chat answer persistence endpoint.
- DjangoBackendClient._required_string: Read and validate a nonempty string.
- DjangoBackendClient._object_list: Validate an array of objects.
- DjangoBackendClient._retrieval_gaps: Validate an array of three-field retrieval gaps.
- DjangoBackendClient._write_headers: Build lease and version headers required for writes.
- DjangoBackendClient._request: Send authenticated HTTP requests and normalize failures.
- DjangoBackendClient._company_id: Validate and extract the company ID.
- DjangoBackendClient._object: Require an object response.
- DjangoBackendClient._etag: Extract the response version header.
- DjangoBackendClient._error: Normalize API errors.
- django_backend_from_environment: Read connection settings, allowing an explicit independent task identity and mailbox.
Variable index:
- logger: Record safe request context and elapsed time.
- JsonObject: Type alias for a read-only JSON mapping.
- _DEFAULT_ANALYSIS_PROMPT_VERSION: Prompt version read from the actual analysis skill.
- _JobContext.job_id: Claimed job ID.
- _JobContext.company_id: Company ID associated with the job.
- _JobContext.expected_version: Business version at claim time.
- _JobContext.lease_token: Lease required for writes; excluded from logs.
- __all__: Public backend protocol, exceptions, client, and factory symbols.
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


# Function: Minimal real backend interface for L1-L4 and read-only chat workflows.
# Logic: Declare workflow methods; concrete clients provide transport.
# Constraints: Do not log credentials; the backend ultimately validates identity and permissions.
class BackendClient(Protocol):
    """Minimal real backend interface for L1-L4 and read-only chat workflows."""

    # Function: Declare the standard L1 email submission interface.
    # Inputs: `submissions`: email extraction payloads.
    # Outputs: JsonObject.
    # Logic: Declare the interface only; implementations provide transport.
    # Constraints: Declarations and exception construction do not issue HTTP requests.
    def submit_emails(self, submissions: list[dict[str, Any]]) -> JsonObject: ...

    # Function: Look up an existing extraction by natural key.
    # Inputs: `mailbox_id`: target mailbox, using the method's configured rules when optional; `dedupe_key`: email natural key.
    # Outputs: JsonObject | None.
    # Logic: Declare the interface only; implementations provide transport.
    # Constraints: Declarations and exception construction do not issue HTTP requests.
    def get_stored_email(
        self, mailbox_id: str, dedupe_key: str
    ) -> JsonObject | None: ...

    # Function: Read company grouping.
    # Inputs: `company_id`: explicit customer identifier.
    # Outputs: JsonObject.
    # Logic: Declare the interface only; implementations provide transport.
    # Constraints: Declarations and exception construction do not issue HTTP requests.
    def get_company_grouping(self, company_id: str) -> JsonObject: ...

    # Function: Read company business context.
    # Inputs: `company_id`: explicit customer identifier.
    # Outputs: JsonObject.
    # Logic: Declare the interface only; implementations provide transport.
    # Constraints: Declarations and exception construction do not issue HTTP requests.
    def get_company_context(self, company_id: str) -> JsonObject: ...

    # Function: Save L2 input and track its version.
    # Inputs: `analysis_input`: L2 analysis input.
    # Outputs: None.
    # Logic: Declare the interface only; implementations provide transport.
    # Constraints: Declarations and exception construction do not issue HTTP requests.
    def save_analysis_input(self, analysis_input: JsonObject) -> None: ...

    # Function: Read the latest L2 snapshot.
    # Inputs: `company_id`: explicit customer identifier.
    # Outputs: JsonObject | None.
    # Logic: Declare the interface only; implementations provide transport.
    # Constraints: Declarations and exception construction do not issue HTTP requests.
    def get_latest_analysis_input(self, company_id: str) -> JsonObject | None: ...

    # Function: Look up the L3 cache for an input and prompt version.
    # Inputs: `company_id`: explicit customer identifier; `input_version`: requested input version.
    # Outputs: JsonObject | None.
    # Logic: Declare the interface only; implementations provide transport.
    # Constraints: Declarations and exception construction do not issue HTTP requests.
    def get_cached_analysis(
        self, company_id: str, input_version: str
    ) -> JsonObject | None: ...

    # Function: Submit L3 analysis.
    # Inputs: `analysis`: L3 analysis result.
    # Outputs: None.
    # Logic: Declare the interface only; implementations provide transport.
    # Constraints: Declarations and exception construction do not issue HTTP requests.
    def save_analysis(self, analysis: JsonObject) -> None: ...

    # Function: Submit L4 scores.
    # Inputs: `score`: L4 score.
    # Outputs: None.
    # Logic: Declare the interface only; implementations provide transport.
    # Constraints: Declarations and exception construction do not issue HTTP requests.
    def save_score(self, score: JsonObject) -> None: ...

    # Function: Claim and cache company leases.
    # Inputs: `limit`: maximum number of claims.
    # Outputs: list[dict[str, Any]].
    # Logic: Declare the interface only; implementations provide transport.
    # Constraints: Declarations and exception construction do not issue HTTP requests.
    def claim_jobs(self, limit: int) -> list[dict[str, Any]]: ...

    # Function: Report a company job result.
    # Inputs: `report`: job result payload.
    # Outputs: None.
    # Logic: Declare the interface only; implementations provide transport.
    # Constraints: Declarations and exception construction do not issue HTTP requests.
    def report_job(self, report: JsonObject) -> None: ...

    # Function: Claim Gmail synchronization through the legacy CLI.
    # Inputs: `limit`: maximum number of claims.
    # Outputs: list[dict[str, Any]].
    # Logic: Declare the interface only; implementations provide transport.
    # Constraints: Declarations and exception construction do not issue HTTP requests.
    def claim_mailbox_syncs(self, limit: int) -> list[dict[str, Any]]: ...

    # Function: Report Gmail synchronization through the legacy CLI.
    # Inputs: `report`: job result payload.
    # Outputs: JsonObject.
    # Logic: Declare the interface only; implementations provide transport.
    # Constraints: Declarations and exception construction do not issue HTTP requests.
    def report_mailbox_sync(self, report: JsonObject) -> JsonObject: ...

    # Function: Read the mailbox cursor and ETag.
    # Inputs: `mailbox_id`: target mailbox, using the method's configured rules when optional.
    # Outputs: JsonObject.
    # Logic: Declare the interface only; implementations provide transport.
    # Constraints: Declarations and exception construction do not issue HTTP requests.
    def get_sync_state(self, mailbox_id: str) -> JsonObject: ...

    # Function: Conditionally update the mailbox cursor.
    # Inputs: `sync_state`: incremental mailbox cursor payload.
    # Outputs: JsonObject.
    # Logic: Declare the interface only; implementations provide transport.
    # Constraints: Declarations and exception construction do not issue HTTP requests.
    def save_sync_state(self, sync_state: JsonObject) -> JsonObject: ...

    # Function: Claim one workspace chat answer request.
    # Inputs: No external parameters; read instance authentication and request state.
    # Outputs: JsonObject | None.
    # Logic: Declare the interface only; implementations provide transport.
    # Constraints: Declarations and exception construction do not issue HTTP requests.
    def claim_answer_request(self) -> JsonObject | None: ...

    # Function: Read customer and knowledge context bound to a request.
    # Inputs: `request_id`: chat request identifier; `scope`: internal or external knowledge scope.
    # Outputs: JsonObject.
    # Logic: Declare the interface only; implementations provide transport.
    # Constraints: Declarations and exception construction do not issue HTTP requests.
    def get_answer_context(self, request_id: str, scope: str) -> JsonObject: ...

    # Function: Discover the read and experiment maintenance tools actually authorized for the request.
    # Inputs: `request_id`: current employee's request UUID; concrete implementations read instance authentication settings.
    # Outputs: JSON object; concrete implementations raise request or contract errors on failure.
    # Logic: Declare the protocol only; concrete clients implement transport.
    # Constraints: Allow only requests authorized for the current employee; reject identity overrides, write tools, and implicit retries.
    def get_chat_tools(self, request_id: str) -> JsonObject: ...

    # Function: Read the authoritative status of the current employee's request.
    # Inputs: `request_id`: current employee's request UUID; concrete implementations read instance authentication settings.
    # Outputs: JSON object; concrete implementations raise request or contract errors on failure.
    # Logic: Declare the protocol only; concrete clients implement transport.
    # Constraints: Allow only requests authorized for the current employee; reject identity overrides, write tools, and implicit retries.
    def get_chat_request_status(self, request_id: str) -> JsonObject: ...

    # Function: Execute customer reads or shared experiment maintenance and obtain registered evidence.
    # Inputs: `request_id`: current employee's request UUID; `name`: fixed read or experiment maintenance tool name; `arguments`: JSON parameters; concrete implementations read instance authentication settings.
    # Outputs: JSON object; concrete implementations raise request or contract errors on failure.
    # Logic: Declare the protocol only; concrete clients implement transport.
    # Constraints: Allow only requests authorized for the current employee; reject identity overrides, write tools, and implicit retries.
    def read_chat_tool(
        self, request_id: str, name: str, arguments: JsonObject
    ) -> JsonObject: ...

    # Function: Report a chat result with its prompt version.
    # Inputs: `result`: chat result including its prompt version.
    # Outputs: JsonObject.
    # Logic: Declare the interface only; implementations provide transport.
    # Constraints: Declarations and exception construction do not issue HTTP requests.
    def report_answer(self, result: JsonObject) -> JsonObject: ...


# Function: Backend data retrieval failed.
# Logic: Encapsulate HTTP status and safe details.
# Constraints: Do not log credentials; the backend ultimately validates identity and permissions.
class BackendRetrievalError(RuntimeError):
    """Backend data retrieval failed."""


# Function: The real backend adapter lacks required configuration.
# Logic: Encapsulate HTTP status and safe details.
# Constraints: Do not log credentials; the backend ultimately validates identity and permissions.
class BackendConfigurationError(ValueError):
    """The real backend adapter lacks required configuration."""


# Function: The backend response cannot be mapped to the data contract in the Agent README.
# Logic: Encapsulate HTTP status and safe details.
# Constraints: Do not log credentials; the backend ultimately validates identity and permissions.
class BackendContractError(RuntimeError):
    """The backend response cannot be mapped to the data contract in the Agent README."""


# Function: Backend request failure without exposing service credentials.
# Logic: Encapsulate HTTP status and safe details.
# Constraints: Do not log credentials; the backend ultimately validates identity and permissions.
class BackendRequestError(RuntimeError):
    """Backend request failure without exposing service credentials."""

    # Function: Store HTTP status, error code, and safe detail.
    # Inputs: `status_code`: HTTP status, or zero for network failures; `code`: safe error code; `detail`: safe error details; `scope`: tool or request error scope.
    # Outputs: No return value; initialize instance state.
    # Logic: Store status, code, and details, and construct a safe exception message.
    # Constraints: Declarations and exception construction do not issue HTTP requests.
    def __init__(
        self, status_code: int, code: str, detail: str, *, scope: str | None = None
    ):
        super().__init__(f"Backend request failed (HTTP {status_code}, {code}): {detail}")
        self.status_code = status_code
        self.code = code
        self.detail = detail
        self.scope = scope


# Function: Store immutable claimed job identity, version, and lease.
# Logic: Use a frozen dataclass for job_id/company_id/expected_version/lease_token.
# Constraints: Do not log credentials; the backend ultimately validates identity and permissions.
@dataclass(frozen=True)
class _JobContext:
    job_id: str
    company_id: str
    expected_version: str
    lease_token: str


# Function: Call the current Django backend while exposing a simplified synchronous protocol to the agent.
# Logic: Maintain task and version state per instance without sharing employee authentication.
# Constraints: Do not log credentials; the backend ultimately validates identity and permissions.
class DjangoBackendClient:
    """Call the current Django backend while exposing a simplified synchronous protocol to the agent.

    Leases, ETags, and nested job payloads belong to the Django HTTP transport layer
    and are handled here; L2-L4 workflows depend only on simple ``BackendClient`` methods.
    """

    # Function: Validate initialization parameters and establish instance state.
    # Inputs: `base_url`: Agent API root; `service_token`: employee service credential, never logged; `mailbox_id`: target mailbox, following configured rules when optional; `analysis_prompt_version`: analysis prompt version, defaulting to the existing skill version; `lease_seconds`: lease duration, default 120, range 10-600; `timeout`: positive HTTP timeout in seconds, default 30; `session`: optional requests session, otherwise an independent pool is created.
    # Outputs: No return value; initialize instance state.
    # Logic: Validate URL, credentials, and existing timeout/lease bounds; create private HTTP transport and company, job, and mailbox version caches.
    # Constraints: No implicit retries; callers handle HTTP and contract errors.
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
            raise BackendConfigurationError("base_url must not be empty.")
        if not isinstance(service_token, str) or not service_token.strip():
            raise BackendConfigurationError("service_token must not be empty.")
        if type(lease_seconds) is not int or not 10 <= lease_seconds <= 600:
            raise BackendConfigurationError("lease_seconds must be between 10 and 600.")
        if (
            not isinstance(analysis_prompt_version, str)
            or not analysis_prompt_version.strip()
        ):
            raise BackendConfigurationError("analysis_prompt_version must not be empty.")
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            raise BackendConfigurationError("timeout must be greater than 0.")

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

    # Function: Release the HTTP connection used by the work unit.
    # Inputs: No parameters; read the instance session.
    # Outputs: No return value.
    # Logic: Close this connection pool without affecting other clients.
    # Constraints: Call after all requests and worker threads have finished.
    def close(self):
        self._session.close()

    # Function: Save extractions for the selected mailbox source and aggregate business statistics.
    # Inputs: `submissions`: L1 payloads; `source`: default Gmail source or explicitly selected QQ source.
    # Outputs: Created, updated, and duplicate counts, plus affected company IDs.
    # Logic: Copy payloads and bind the current mailbox_id; submit through authenticated HTTP and strictly validate the response.
    # Constraints: Do not mutate original payloads; allow only gmail_real/qq_real; retain default Gmail behavior.
    def submit_emails(
        self, submissions: list[dict[str, Any]], *, source: str = "gmail_real"
    ) -> dict[str, Any]:
        """Add HTTP transport fields and aggregate per-email results into GmailSyncResult statistics."""
        if source not in {"gmail_real", "qq_real"}:
            raise BackendContractError("Unsupported live mailbox provider.")
        if not isinstance(self.mailbox_id, str) or not self.mailbox_id.strip():
            raise BackendConfigurationError("mailbox_id is required before submitting email.")

        payload = []
        for submission in submissions:
            if not isinstance(submission, Mapping):
                raise BackendContractError("EmailSubmission must be an object.")
            item = copy.deepcopy(dict(submission))
            item["mailbox_id"] = self.mailbox_id
            item["source"] = source
            payload.append(item)

        response, _ = self._request("POST", "emails/", json=payload)
        if not isinstance(response, list):
            raise BackendContractError("Email submission response must be an array.")

        counts = {"created": 0, "updated": 0, "duplicate": 0}
        affected: list[str] = []
        for index, raw in enumerate(response):
            if not isinstance(raw, Mapping):
                raise BackendContractError(f"Email submission response item {index} must be an object.")
            status = raw.get("status")
            company_id = raw.get("company_id")
            if status not in counts:
                raise BackendContractError(f"Invalid email submission response status: {status}")
            if not isinstance(company_id, str) or not company_id:
                raise BackendContractError("Email submission response is missing company_id.")
            counts[status] += 1
            if company_id not in affected:
                affected.append(company_id)

        return {
            "created_count": counts["created"],
            "updated_count": counts["updated"],
            "duplicate_count": counts["duplicate"],
            "affected_company_ids": affected,
        }

    # Function: Look up an existing extraction by natural key.
    # Inputs: `mailbox_id`: target mailbox, using the method's configured rules when optional; `dedupe_key`: email natural key.
    # Outputs: dict[str, Any] | None.
    # Logic: Query by mailbox and natural key; return None on 404; validate extraction identity, status, and prompt version.
    # Constraints: No implicit retries; callers handle HTTP and contract errors.
    def get_stored_email(
        self, mailbox_id: str, dedupe_key: str
    ) -> dict[str, Any] | None:
        """Read stored email by natural key to reuse an extraction with the same version before L1."""
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
            raise BackendContractError("Saved email dedupe_key does not match the request.")
        if not isinstance(document.get("extract_prompt_version"), str):
            raise BackendContractError("Saved email is missing extract_prompt_version.")
        if document.get("extract_status") not in {
            "completed",
            "failed",
            "skipped_non_business",
        }:
            raise BackendContractError("Saved email extract_status is invalid.")
        return document

    # Function: Read company grouping.
    # Inputs: `company_id`: explicit customer identifier.
    # Outputs: dict[str, Any].
    # Logic: Read grouping and ETag, reject versions newer than the claimed job, and cache the customer revision.
    # Constraints: No implicit retries; callers handle HTTP and contract errors.
    def get_company_grouping(self, company_id: str) -> dict[str, Any]:
        response, headers = self._request(
            "GET", "grouping/", query={"company_id": company_id}
        )
        document = self._object(response, "Grouping")
        revision = self._etag(headers)
        if revision is None:
            raise BackendContractError("Grouping response is missing ETag.")
        active_job = self._company_jobs.get(company_id)
        if active_job is not None and revision != active_job.expected_version:
            raise BackendContractError(
                "Company context version exceeds the current job; analysis stopped to avoid stale input."
            )
        self._revisions[company_id] = revision
        return document

    # Function: Read company business context.
    # Inputs: `company_id`: explicit customer identifier.
    # Outputs: dict[str, Any].
    # Logic: Require grouping to be read first; request context with If-Match and verify matching response versions.
    # Constraints: No implicit retries; callers handle HTTP and contract errors.
    def get_company_context(self, company_id: str) -> dict[str, Any]:
        revision = self._revisions.get(company_id)
        if revision is None:
            raise BackendContractError("Read Grouping before CompanyContext.")
        response, headers = self._request(
            "GET",
            "context/",
            query={"company_id": company_id},
            headers={"If-Match": revision},
        )
        returned_revision = self._etag(headers)
        if returned_revision is not None and returned_revision != revision:
            raise BackendContractError("Grouping and CompanyContext ETags do not match.")
        return self._object(response, "CompanyContext")

    # Function: Save L2 input and track its version.
    # Inputs: `analysis_input`: L2 analysis input.
    # Outputs: None.
    # Logic: Copy input and submit analysis-inputs using the claimed job's lease and version headers.
    # Constraints: No implicit retries; callers handle HTTP and contract errors.
    def save_analysis_input(self, analysis_input: Mapping[str, Any]) -> None:
        document = dict(analysis_input)
        company_id = self._company_id(document)
        self._request(
            "POST",
            "analysis-inputs/",
            json=document,
            headers=self._write_headers(company_id),
        )

    # Function: Read the latest L2 snapshot.
    # Inputs: `company_id`: explicit customer identifier.
    # Outputs: dict[str, Any] | None.
    # Logic: Query the latest customer input; return None on 404 and require an object for other successful responses.
    # Constraints: No implicit retries; callers handle HTTP and contract errors.
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

    # Function: Look up the L3 cache for an input and prompt version.
    # Inputs: `company_id`: explicit customer identifier; `input_version`: requested input version.
    # Outputs: dict[str, Any] | None.
    # Logic: Query the cache by customer, input, and prompt version; return None for miss/pending and require complete analysis on a hit.
    # Constraints: No implicit retries; callers handle HTTP and contract errors.
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
            "A cached-analysis hit must return the full Analysis; the backend returned metadata only."
        )

    # Function: Submit L3 analysis.
    # Inputs: `analysis`: L3 analysis result.
    # Outputs: None.
    # Logic: Copy analysis data, attach the company's claimed job lease and version headers, and submit analyses.
    # Constraints: No implicit retries; callers handle HTTP and contract errors.
    def save_analysis(self, analysis: Mapping[str, Any]) -> None:
        document = dict(analysis)
        company_id = self._company_id(document)
        self._request(
            "POST",
            "analyses/",
            json=document,
            headers=self._write_headers(company_id),
        )

    # Function: Submit L4 scores.
    # Inputs: `score`: L4 score.
    # Outputs: None.
    # Logic: Copy score data, attach the company's claimed job lease and version headers, and submit scores.
    # Constraints: No implicit retries; callers handle HTTP and contract errors.
    def save_score(self, score: Mapping[str, Any]) -> None:
        document = dict(score)
        company_id = self._company_id(document)
        self._request(
            "POST",
            "scores/",
            json=document,
            headers=self._write_headers(company_id),
        )

    # Function: Claim and cache company leases.
    # Inputs: `limit`: maximum number of claims.
    # Outputs: list[dict[str, Any]].
    # Logic: Send the claim limit and lease duration; validate response identity, cache job leases and company versions, and return simplified jobs.
    # Constraints: No implicit retries; callers handle HTTP and contract errors.
    def claim_jobs(self, limit: int) -> list[dict[str, Any]]:
        response, _ = self._request(
            "POST",
            "jobs/claim/",
            json={"limit": limit, "lease_seconds": self.lease_seconds},
        )
        if not isinstance(response, list):
            raise BackendContractError("Job claim response must be an array.")

        jobs = []
        for index, raw in enumerate(response):
            if not isinstance(raw, Mapping):
                raise BackendContractError(f"Job claim response item {index} must be an object.")
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
                    "Job is missing job_id, company_id, lease_token, or expected_version."
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

    # Function: Report a company job result.
    # Inputs: `report`: job result payload.
    # Outputs: None.
    # Logic: Require this instance to have claimed the job; report with the lease and clear the corresponding cache on success.
    # Constraints: No implicit retries; callers handle HTTP and contract errors.
    def report_job(self, report: Mapping[str, Any]) -> None:
        document = dict(report)
        job_id = document.get("job_id")
        context = self._jobs.get(str(job_id))
        if context is None:
            raise BackendContractError("JobReport has no corresponding claimed job.")
        self._request(
            "POST",
            "jobs/report/",
            json=document,
            headers={"X-Lease-Token": context.lease_token},
        )
        self._jobs.pop(context.job_id, None)
        if self._company_jobs.get(context.company_id) == context:
            self._company_jobs.pop(context.company_id, None)

    # Function: Claim Gmail synchronization through the legacy CLI.
    # Inputs: `limit`: maximum number of claims.
    # Outputs: list[dict[str, Any]].
    # Logic: Claim only Gmail synchronization explicitly requested by an employee; validate mailbox, authorization object, and count fields.
    # Constraints: No implicit retries; callers handle HTTP and contract errors.
    def claim_mailbox_syncs(self, limit: int) -> list[dict[str, Any]]:
        """Claim Gmail synchronization requested by the current employee in the web UI."""
        response, _ = self._request(
            "POST", "mailbox-syncs/claim/", json={"limit": limit}
        )
        if not isinstance(response, list):
            raise BackendContractError("Mailbox sync claim response must be an array.")
        claims: list[dict[str, Any]] = []
        for index, raw in enumerate(response):
            if not isinstance(raw, Mapping):
                raise BackendContractError(
                    f"Mailbox sync claim item {index} must be an object."
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
                raise BackendContractError("Mailbox sync claim is missing the mailbox ID or address.")
            if not isinstance(required["authorization"], Mapping):
                raise BackendContractError("Mailbox sync claim is missing authorization.")
            if type(required["max_results"]) is not int:
                raise BackendContractError("Mailbox sync claim has invalid max_results.")
            claims.append(copy.deepcopy(dict(raw)))
        return claims

    # Function: Report Gmail synchronization through the legacy CLI.
    # Inputs: `report`: job result payload.
    # Outputs: dict[str, Any].
    # Logic: Submit one synchronization result and any updated authorization; require an object response.
    # Constraints: No implicit retries; callers handle HTTP and contract errors.
    def report_mailbox_sync(self, report: Mapping[str, Any]) -> dict[str, Any]:
        """Report one Gmail synchronization to the backend and save any refreshed authorization."""
        response, _ = self._request("POST", "mailbox-syncs/report/", json=dict(report))
        return self._object(response, "Mailbox sync report")

    # Function: Read the mailbox cursor and ETag.
    # Inputs: `mailbox_id`: target mailbox, using the method's configured rules when optional.
    # Outputs: dict[str, Any].
    # Logic: Query the mailbox cursor; validate identity, integer version, and ETag; cache the optimistic lock version.
    # Constraints: No implicit retries; callers handle HTTP and contract errors.
    def get_sync_state(self, mailbox_id: str) -> dict[str, Any]:
        """Read the mailbox history cursor and store the optimistic lock version returned by the backend."""
        response, headers = self._request(
            "GET", "sync-state/", query={"mailbox_id": mailbox_id}
        )
        document = self._object(response, "SyncState")
        revision = self._etag(headers)
        if revision is None:
            raise BackendContractError("SyncState response is missing ETag.")
        if str(document.get("mailbox_id")) != mailbox_id:
            raise BackendContractError("SyncState mailbox_id does not match the request.")
        if type(document.get("version")) is not int:
            raise BackendContractError("SyncState response is missing an integer version.")
        self._mailbox_revisions[mailbox_id] = revision
        return document

    # Function: Conditionally update the mailbox cursor.
    # Inputs: `sync_state`: incremental mailbox cursor payload.
    # Outputs: dict[str, Any].
    # Logic: Require a prior mailbox state read; submit with cached If-Match and update the returned version.
    # Constraints: No implicit retries; callers handle HTTP and contract errors.
    def save_sync_state(self, sync_state: Mapping[str, Any]) -> dict[str, Any]:
        """Save the Gmail historyId incremental cursor corresponding to a successful email batch."""
        document = dict(sync_state)
        mailbox_id = document.get("mailbox_id")
        if not isinstance(mailbox_id, str) or not mailbox_id:
            raise BackendContractError("SyncState payload is missing mailbox_id.")
        revision = self._mailbox_revisions.get(mailbox_id)
        if revision is None:
            raise BackendContractError("Read the current SyncState before saving.")
        response, headers = self._request(
            "POST",
            "sync-state-save/",
            json=document,
            headers={"If-Match": revision},
        )
        saved = self._object(response, "SyncState")
        returned_revision = self._etag(headers)
        if returned_revision is None:
            raise BackendContractError("Save SyncState response is missing ETag.")
        self._mailbox_revisions[mailbox_id] = returned_revision
        return saved

    def claim_answer_request(self) -> dict[str, Any] | None:
        """Function: Claim zero or one workspace chat request.
        Inputs: No external parameters; use instance HTTP authentication and service URL.
        Outputs: Request dictionary or None; invalid responses such as missing fields or empty strings raise BackendContractError.
        Logic: Issue one claim and validate stable identifiers; normalize transitional company_id:null to absence of a preselected company field.
        Constraints: Reject non-null company_id; do not retry or rewrite employee identity in the transport layer.
        """
        response, _ = self._request("POST", "chat/requests/claim/", json={})
        document = self._object(response, "Answer request claim")
        if "request" not in document:
            raise BackendContractError("Answer request claim response is missing request.")

        raw_request = document["request"]
        if raw_request is None:
            return None
        if not isinstance(raw_request, Mapping):
            raise BackendContractError(
                "Answer request claim request must be an object or null."
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
                raise BackendContractError("Workspace chat requests must not be bound to a company.")
        return request

    # Function: Map the chat context endpoint.
    # Inputs: `request_id`: chat request identifier; `scope`: internal or external knowledge scope.
    # Outputs: dict[str, Any].
    # Logic: Request the claimed task's specified knowledge scope; validate request identity, source arrays, retrieval status, and gaps.
    # Constraints: No implicit retries; callers handle HTTP and contract errors.
    def get_answer_context(self, request_id: str, scope: str) -> dict[str, Any]:
        """Read customer and selected knowledge-scope context bound to the current chat request."""
        self._required_string(
            {"request_id": request_id}, "request_id", "Answer context request"
        )
        if scope not in {"internal", "external"}:
            raise BackendContractError(
                "Answer context scope must be internal or external."
            )

        response, _ = self._request(
            "POST",
            "chat/context/",
            json={"request_id": request_id, "scope": scope},
        )
        document = self._object(response, "Answer context")
        if document.get("request_id") != request_id:
            raise BackendContractError("Answer context request_id does not match the request.")
        if document.get("scope") != scope:
            raise BackendContractError("Answer context scope does not match the request.")

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
                "Answer context customer_context_status does not match the scope."
            )
        if scope == "external" and customer_context:
            raise BackendContractError(
                "External answer context must not include customer_context."
            )

        if document.get("knowledge_status") not in {"completed", "failed"}:
            raise BackendContractError("Answer context knowledge_status is invalid.")
        self._retrieval_gaps(document.get("retrieval_gaps"))
        if type(document.get("external_available")) is not bool:
            raise BackendContractError(
                "Answer context external_available must be a boolean."
            )
        if scope == "external" and document["external_available"] is not True:
            raise BackendContractError(
                "External answer context external_available must be true."
            )
        return document

    # Function: Discover the read and experiment maintenance tools actually authorized for the request.
    # Inputs: `request_id`: current employee's request UUID; concrete implementations read instance authentication settings.
    # Outputs: JSON object; concrete implementations raise request or contract errors on failure.
    # Logic: Issue one GET and validate protocol version, request, page, and count.
    # Constraints: Allow only requests authorized for the current employee; reject identity overrides, write tools, and implicit retries.
    def get_chat_tools(self, request_id: str) -> dict[str, Any]:
        """Read tools available to this processing request; do not infer permissions from the global registry."""
        self._required_string({"request_id": request_id}, "request_id", "Chat tools request")
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
            raise BackendContractError("Chat tools catalog version, request, or pagination does not match.")
        tools = document.get("tools")
        self._object_list(tools, "Chat tools tools")
        if len(tools) != document["count"]:
            raise BackendContractError("Chat tools catalog count does not match.")
        return document

    # Function: Read the authoritative status of the current employee's request.
    # Inputs: `request_id`: current employee's request UUID; concrete implementations read instance authentication settings.
    # Outputs: JSON object; concrete implementations raise request or contract errors on failure.
    # Logic: Issue one GET and verify request ID and valid status.
    # Constraints: Allow only requests authorized for the current employee; reject identity overrides, write tools, and implicit retries.
    def get_chat_request_status(self, request_id: str) -> dict[str, Any]:
        """Query only the current employee's saved chat request state without reclaiming or generating an answer."""
        self._required_string({"request_id": request_id}, "request_id", "Chat status request")
        response, _ = self._request(
            "GET", f"chat/requests/{quote(request_id, safe='')}/"
        )
        document = self._object(response, "Chat request status")
        if (
            document.get("request_id") != request_id
            or document.get("status") not in {"pending", "processing", "completed", "failed"}
        ):
            raise BackendContractError("Chat request status does not match this request.")
        return document

    # Function: Execute customer reads or shared experiment maintenance and obtain registered evidence.
    # Inputs: `request_id`: current employee's request UUID; `name`: fixed read or experiment maintenance tool name; `arguments`: JSON parameters; concrete implementations read instance authentication settings.
    # Outputs: JSON object; concrete implementations raise request or contract errors on failure.
    # Logic: Validate the fixed name and argument object; issue one POST and verify response ownership and evidence arrays.
    # Constraints: Allow only requests authorized for the current employee; reject identity overrides, write tools, and implicit retries.
    def read_chat_tool(
        self, request_id: str, name: str, arguments: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Invoke request-bound read and experiment maintenance tools; the backend confirms both tool results and evidence."""
        self._required_string({"request_id": request_id}, "request_id", "Chat tool request")
        if name not in WORKSPACE_TOOLS:
            raise BackendContractError("Chat tool supports only registered customer reads and experiment maintenance.")
        if not isinstance(arguments, Mapping):
            raise BackendContractError("Chat tool arguments must be an object.")
        response, _ = self._request(
            "POST",
            "chat/tool-reads/",
            json={"request_id": request_id, "name": name, "arguments": dict(arguments)},
        )
        document = self._object(response, "Chat tool")
        if document.get("request_id") != request_id or document.get("tool") != name:
            raise BackendContractError("Chat tool response does not match this request or tool.")
        if document.get("status") != "completed":
            raise BackendContractError("Chat tool did not confirm completion.")
        if not isinstance(document.get("data"), Mapping):
            raise BackendContractError("Chat tool data must be an object.")
        self._object_list(document.get("evidence_items"), "Chat tool evidence_items")
        return document

    # Function: Map the chat answer persistence endpoint.
    # Inputs: `result`: chat result including its prompt version.
    # Outputs: dict[str, Any].
    # Logic: Copy and submit the completed/failed payload once; verify saved, duplicate, and assistant message identifiers.
    # Constraints: No implicit retries; callers handle HTTP and contract errors.
    def report_answer(self, result: Mapping[str, Any]) -> dict[str, Any]:
        """Report one stable completed or failed chat result."""
        if not isinstance(result, Mapping):
            raise BackendContractError("Report answer payload must be an object.")
        payload = copy.deepcopy(dict(result))
        request_id = self._required_string(payload, "request_id", "Report answer")
        self._required_string(payload, "chat_prompt_version", "Report answer")
        status = payload.get("status")
        if status not in {"completed", "failed"}:
            raise BackendContractError(
                "Report answer status must be completed or failed."
            )

        response, _ = self._request("POST", "chat/answers/", json=payload)
        document = self._object(response, "Report answer")
        if document.get("request_id") != request_id:
            raise BackendContractError("Report answer response request_id does not match the request.")
        if document.get("saved") is not True:
            raise BackendContractError("Report answer response did not confirm persistence.")
        if type(document.get("duplicate")) is not bool:
            raise BackendContractError("Report answer response duplicate must be a boolean.")
        if "assistant_message_id" not in document:
            raise BackendContractError("Report answer response is missing assistant_message_id.")
        assistant_message_id = document["assistant_message_id"]
        if status == "completed":
            if (
                not isinstance(assistant_message_id, str)
                or not assistant_message_id.strip()
            ):
                raise BackendContractError(
                    "Completed report response is missing assistant_message_id."
                )
        elif assistant_message_id is not None and (
            not isinstance(assistant_message_id, str)
            or not assistant_message_id.strip()
        ):
            raise BackendContractError(
                "Failed report response has an invalid assistant_message_id."
            )
        return document

    # Function: Read and validate a nonempty string.
    # Inputs: `document`: response mapping; `field`: field to read; `name`: safe error location label.
    # Outputs: str.
    # Logic: Require the specified field to be a nonempty string; otherwise raise BackendContractError.
    # Constraints: No implicit retries; callers handle HTTP and contract errors.
    @staticmethod
    def _required_string(document: Mapping[str, Any], field: str, name: str) -> str:
        value = document.get(field)
        if not isinstance(value, str) or not value.strip():
            raise BackendContractError(f"{name} is missing {field}.")
        return value

    # Function: Validate an array of objects.
    # Inputs: `value`: value to validate; `name`: safe error location label.
    # Outputs: None.
    # Logic: Require each list item to be a mapping; do not coerce invalid values.
    # Constraints: No implicit retries; callers handle HTTP and contract errors.
    @staticmethod
    def _object_list(value: object, name: str) -> None:
        if not isinstance(value, list):
            raise BackendContractError(f"{name} must be an array.")
        if any(not isinstance(item, Mapping) for item in value):
            raise BackendContractError(f"{name} must contain only objects.")

    # Function: Validate an array of three-field retrieval gaps.
    # Inputs: `value`: value to validate.
    # Outputs: None.
    # Logic: Require exactly scope/code/message in each item, all nonempty strings.
    # Constraints: No implicit retries; callers handle HTTP and contract errors.
    @classmethod
    def _retrieval_gaps(cls, value: object) -> None:
        if not isinstance(value, list):
            raise BackendContractError("Answer context retrieval_gaps must be an array.")
        required_fields = {"scope", "code", "message"}
        for gap in value:
            if not isinstance(gap, Mapping) or set(gap) != required_fields:
                raise BackendContractError(
                    "Answer context retrieval gap must contain only scope, code, and message."
                )
            for field in required_fields:
                cls._required_string(gap, field, "Answer context retrieval gap")

    # Function: Build lease and version headers required for writes.
    # Inputs: `company_id`: explicit customer identifier.
    # Outputs: dict[str, str].
    # Logic: Read the lease and observed version from a claimed company job; reject writes without a claim.
    # Constraints: No implicit retries; callers handle HTTP and contract errors.
    def _write_headers(self, company_id: str) -> dict[str, str]:
        context = self._company_jobs.get(company_id)
        if context is None:
            raise BackendContractError("Claim the company job before saving analysis results.")
        revision = self._revisions.get(company_id, context.expected_version)
        return {
            "If-Match": revision,
            "X-Job-ID": context.job_id,
            "X-Lease-Token": context.lease_token,
        }

    # Function: Send authenticated HTTP requests and normalize failures.
    # Inputs: `method`: HTTP method; `path`: relative endpoint path; `query`: optional query fields; `json`: optional JSON payload; `headers`: HTTP header mapping; `allowed_statuses`: explicit statuses that may be treated as no content.
    # Outputs: tuple[object, Mapping[str, str]].
    # Logic: Merge employee authentication and request headers; issue one HTTP request, normalize network/non-success errors, and parse successful JSON.
    # Constraints: No implicit retries; callers handle HTTP and contract errors.
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
            raise BackendContractError("Backend success response is not valid JSON.") from None

    # Function: Validate and extract the company ID.
    # Inputs: `document`: response mapping.
    # Outputs: str.
    # Logic: Require a mapping with a nonempty company_id string; otherwise raise a contract error.
    # Constraints: No implicit retries; callers handle HTTP and contract errors.
    @staticmethod
    def _company_id(document: Mapping[str, Any]) -> str:
        company_id = document.get("company_id")
        if not isinstance(company_id, str) or not company_id:
            raise BackendContractError("Payload is missing company_id.")
        return company_id

    # Function: Require an object response.
    # Inputs: `value`: value to validate; `name`: safe error location label.
    # Outputs: dict[str, Any].
    # Logic: Require a mapping and return a deep-copied dictionary so callers cannot mutate the original response.
    # Constraints: No implicit retries; callers handle HTTP and contract errors.
    @staticmethod
    def _object(value: object, name: str) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise BackendContractError(f"{name} response must be an object.")
        return copy.deepcopy(dict(value))

    # Function: Extract the response version header.
    # Inputs: `headers`: HTTP header mapping.
    # Outputs: str | None.
    # Logic: Handle ETag header case variations and strip double quotes; return None when missing.
    # Constraints: No implicit retries; callers handle HTTP and contract errors.
    @staticmethod
    def _etag(headers: Mapping[str, str]) -> str | None:
        value = headers.get("ETag") or headers.get("etag")
        return value.strip('"') if isinstance(value, str) and value else None

    # Function: Normalize API errors.
    # Inputs: `response`: HTTP response.
    # Outputs: tuple[str, str].
    # Logic: Extract code/detail from the standard error object; return explicit generic HTTP details for malformed error bodies.
    # Constraints: No implicit retries; callers handle HTTP and contract errors.
    @staticmethod
    def _error(response: Any) -> tuple[str, str, str | None]:
        try:
            payload = response.json()
        except ValueError:
            return "http_error", "Backend returned a non-JSON error.", None
        error = payload.get("error") if isinstance(payload, Mapping) else None
        if not isinstance(error, Mapping):
            return "http_error", "Backend request was rejected.", None
        code = error.get("code")
        detail = error.get("detail")
        scope = error.get("scope")
        return (
            str(code or "http_error"),
            str(detail or "Backend request was rejected."),
            scope if scope in {"tool", "request"} else None,
        )


# Function: Construct an independent HTTP client using CLI environment identity or explicit worker task identity.
# Inputs: `mailbox_id`: target mailbox; `service_token`: optional task token; read remaining connection settings from the environment.
# Outputs: DjangoBackendClient with no shared mutable state; invalid settings raise BackendConfigurationError.
# Logic: With an explicit token, use only the supplied mailbox to avoid inheriting another employee's environment mailbox; otherwise retain CLI behavior.
# Constraints: Do not modify process environment or existing timeout, lease, or analysis prompt version settings.
def django_backend_from_environment(
    *, mailbox_id: str | None = None, service_token: str | None = None
) -> DjangoBackendClient:
    """Create a backend adapter from connection settings and an optional independent task identity."""
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
            "Backend timeout or job lease_seconds configuration is invalid."
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
