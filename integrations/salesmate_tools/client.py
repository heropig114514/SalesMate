"""Responsibility: Provide a Django-independent business-tool HTTP client.
Implementation: Fixed paths, optional user tokens, explicit pagination/idempotency; dedicated environment configuration supports long inference timeouts while retaining the 30-second default. Reject redirects and automatic retries.
Relationships: Shared by CLI/MCP; backend agent_tools enforces permissions and business rules.
Directory:
- ToolError: Safe client errors.
- ToolClient: HTTP protocol client.
- ToolClient.__init__: Validate connection configuration.
- ToolClient.from_env: Read dedicated environment variables.
- ToolClient.request: Execute one request.
- ToolClient.catalog: Read one tool-description page.
- ToolClient.describe: Find one tool.
- ToolClient.call: Submit one call.
Variable index:
- None
"""

import os
import math
from urllib.parse import urlsplit
import requests


# Function: Carry display-safe errors.
# Logic: Never retain request tokens or raw network exceptions.
# Constraints: Do not imply that an operation never occurred.
class ToolError(RuntimeError):
    pass


# Function: Connect to the existing business backend.
# Logic: Accept HTTPS or local HTTP only.
# Constraints: No database, filesystem, or arbitrary-URL tools.
class ToolClient:
    # Function: Validate configuration.
    # Inputs: `base_url` is the service root; optional `token` omits Authorization when empty; `timeout` is seconds; `user` optionally identifies experiment ownership.
    # Outputs: An instance.
    # Logic: Reject URL credentials, queries, and fragments; timeout must be finite, positive, and at most 3600 seconds.
    # Constraints: HTTP permits explicit local addresses only.
    def __init__(self, base_url, token="", timeout=30, user=""):
        if type(timeout) not in {int, float} or not math.isfinite(timeout) or not 0 < timeout <= 3600:
            raise ToolError("SALESMATE_TOOLS_TIMEOUT 须为大于0且不超过3600的秒数。")
        url = urlsplit(base_url)
        if (
            not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in {"", "/"}
            or (
                url.scheme != "https"
                and not (
                    url.scheme == "http"
                    and url.hostname in {"localhost", "127.0.0.1", "::1"}
                )
            )
        ):
            raise ToolError(
                "SALESMATE_TOOLS_URL 须为 HTTPS 服务根地址；本机可使用 HTTP。"
            )
        if not isinstance(token, str) or any(character.isspace() for character in token):
            raise ToolError("SALESMATE_TOOLS_TOKEN 不能含空白；免登录实验服务器可留空。")
        self.base_url, self.token, self.timeout = base_url.rstrip("/"), token, timeout
        if not isinstance(user, str) or any(ch in user for ch in "\r\n"):
            raise ToolError("SALESMATE_TOOLS_USER 须为单行用户名。")
        self.user = user

    # Function: Read explicit configuration.
    # Inputs: Environment variables SALESMATE_TOOLS_URL, SALESMATE_TOOLS_TOKEN, SALESMATE_TOOLS_USER, SALESMATE_TOOLS_TIMEOUT.
    # Outputs: A client.
    # Logic: Retain the 30-second timeout default and parse overrides as floats; never load project .env or Worker credentials.
    # Constraints: URL is required; an empty token is allowed, but production backend mode still rejects anonymous requests.
    @classmethod
    def from_env(cls):
        try:
            timeout = float(os.environ.get("SALESMATE_TOOLS_TIMEOUT", "30"))
        except ValueError:
            raise ToolError("SALESMATE_TOOLS_TIMEOUT 须为秒数。") from None
        return cls(
            os.environ.get("SALESMATE_TOOLS_URL", ""),
            os.environ.get("SALESMATE_TOOLS_TOKEN", ""),
            timeout=timeout,
            user=os.environ.get("SALESMATE_TOOLS_USER", ""),
        )

    # Function: Request a fixed endpoint.
    # Inputs: `method`, `path`, query `params`, and JSON `payload`.
    # Outputs: A JSON object.
    # Logic: No retries, redirects, or persistent cookie sessions.
    # Constraints: Failures expose structured business errors or HTTP status, not raw network exceptions; retain the original idempotency key for reconciliation after timeouts.
    def request(self, method, path, params=None, payload=None):
        if (method, path) not in {("GET", "catalog/"), ("POST", "call/")}:
            raise ToolError("客户端只支持工具目录和调用接口。")
        try:
            response = requests.request(
                method,
                self.base_url + "/api/v1/agent-tools/" + path,
                headers={**({"Authorization": "Tool " + self.token} if self.token else {}),
                         **({"X-Lab-User": self.user} if self.user else {})},
                params=params,
                json=payload,
                timeout=self.timeout,
                allow_redirects=False,
            )
        except requests.RequestException:
            raise ToolError(
                "工具请求未获得可靠响应；写入结果可能未知，请保留原幂等键核对。"
            ) from None
        if not 200 <= response.status_code < 300:
            if response.status_code in {400, 401, 403, 404, 409, 429, 502, 503}:
                try:
                    error = response.json()
                except ValueError:
                    error = None
                if isinstance(error, dict) and isinstance(error.get("error"), dict):
                    raise ToolError(
                        f"工具 HTTP {response.status_code}: " + str(error["error"])
                    )
            raise ToolError(
                f"工具 HTTP {response.status_code}；请检查权限、版本和输入契约。"
            )
        try:
            result = response.json()
        except ValueError:
            raise ToolError("工具响应不是 JSON，不能确认操作结果。") from None
        if not isinstance(result, dict):
            raise ToolError("工具响应不符合对象契约。")
        return result

    # Function: Discover one page of authorized tools.
    # Inputs: `page`, `page_size`, and optional `category`.
    # Outputs: A catalog envelope.
    # Logic: Preserve server pagination.
    # Constraints: Never implicitly read business records.
    def catalog(self, page=1, page_size=100, category=None):
        params = {"page": page, "page_size": page_size}
        if category:
            params["category"] = category
        return self.request("GET", "catalog/", params=params)

    # Function: Read a specified tool schema.
    # Inputs: `name`.
    # Outputs: A tool descriptor.
    # Logic: Narrow the catalog by name prefix, then explicitly traverse at most 100 pages.
    # Constraints: Do not traverse business data.
    def describe(self, name):
        for page in range(1, 101):
            result = self.catalog(page=page, category=name.split(".")[0])
            for item in result["tools"]:
                if item["name"] == name:
                    return item
            if page * result["page_size"] >= result["count"]:
                break
        raise ToolError("工具不存在或未授权。")

    # Function: Invoke a business tool.
    # Inputs: `name`, `arguments`, and `idempotency_key`, required for production writes but optional in experiment mode.
    # Outputs: A backend receipt.
    # Logic: Submit unchanged for backend validation.
    # Constraints: Never generate idempotency keys automatically, retry, or approve proposals.
    def call(self, name, arguments, idempotency_key=None):
        payload = {"name": name, "arguments": arguments}
        if idempotency_key is not None:
            payload["idempotency_key"] = idempotency_key
        return self.request("POST", "call/", payload=payload)
