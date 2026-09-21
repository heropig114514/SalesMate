"""职责：提供不依赖 Django 的业务工具 HTTP 客户端。
实现：固定路径、可选用户 token（仅开放实验服务器允许留空）、显式分页和幂等键；拒绝重定向与自动重试。
关联：CLI 和 MCP 共用；后端 agent_tools 执行所有权限与业务规则。
目录：
- ToolError：安全客户端错误。
- ToolClient：HTTP 协议客户端。
- ToolClient.__init__：校验连接配置。
- ToolClient.from_env：读取专用环境变量。
- ToolClient.request：执行单次请求。
- ToolClient.catalog：读取一页工具描述。
- ToolClient.describe：查找一个工具。
- ToolClient.call：提交一次调用。
变量索引：
- 无
"""

import os
from urllib.parse import urlsplit
import requests


# 功能：携带可展示错误。
# 逻辑：不保留请求 token 或原始网络异常。
# 约束：不暗示操作未发生。
class ToolError(RuntimeError):
    pass


# 功能：连接既有业务后端。
# 逻辑：仅接受 HTTPS 或本机 HTTP。
# 约束：没有数据库、文件系统或任意 URL 工具。
class ToolClient:
    # 功能：校验配置。
    # 输入：`base_url` 服务根地址、`token` 可选凭证，空值不发送 Authorization、`timeout` 秒数、`user` 可选实验归属用户名。
    # 输出：实例。
    # 逻辑：禁止 URL 凭证、查询和片段。
    # 约束：HTTP 只允许明确本机地址。
    def __init__(self, base_url, token="", timeout=30, user=""):
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

    # 功能：读取显式配置。
    # 输入：环境变量 SALESMATE_TOOLS_URL、SALESMATE_TOOLS_TOKEN、SALESMATE_TOOLS_USER。
    # 输出：客户端。
    # 逻辑：不加载项目 .env 或 Worker 凭证。
    # 约束：地址必填；令牌可空，服务端正式模式仍会拒绝匿名请求。
    @classmethod
    def from_env(cls):
        return cls(
            os.environ.get("SALESMATE_TOOLS_URL", ""),
            os.environ.get("SALESMATE_TOOLS_TOKEN", ""),
            user=os.environ.get("SALESMATE_TOOLS_USER", ""),
        )

    # 功能：执行固定接口请求。
    # 输入：`method`、`path`、`params` 查询、`payload` JSON。
    # 输出：JSON 对象。
    # 逻辑：不重试、不跟随重定向，不创建持久 Cookie 会话。
    # 约束：失败只返回后端结构化业务错误或 HTTP 状态，不回显原始网络异常；超时后须保留原幂等键核对。
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
            if response.status_code in {400, 401, 403, 404, 409, 429}:
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

    # 功能：发现一页授权工具。
    # 输入：`page`、`page_size`、`category` 可选分类。
    # 输出：目录信封。
    # 逻辑：保留服务端分页。
    # 约束：不隐式读取业务记录。
    def catalog(self, page=1, page_size=100, category=None):
        params = {"page": page, "page_size": page_size}
        if category:
            params["category"] = category
        return self.request("GET", "catalog/", params=params)

    # 功能：读取指定工具 Schema。
    # 输入：`name`。
    # 输出：工具描述。
    # 逻辑：名称前缀缩小目录范围，再显式遍历该目录最多 100 页。
    # 约束：不遍历业务数据。
    def describe(self, name):
        for page in range(1, 101):
            result = self.catalog(page=page, category=name.split(".")[0])
            for item in result["tools"]:
                if item["name"] == name:
                    return item
            if page * result["page_size"] >= result["count"]:
                break
        raise ToolError("工具不存在或未授权。")

    # 功能：调用业务工具。
    # 输入：`name`、`arguments`、`idempotency_key` 正式模式写操作必需，实验模式可省略。
    # 输出：后端回执。
    # 逻辑：原样提交，由后端校验。
    # 约束：不自动生成幂等键、不重试、不批准提案。
    def call(self, name, arguments, idempotency_key=None):
        payload = {"name": name, "arguments": arguments}
        if idempotency_key is not None:
            payload["idempotency_key"] = idempotency_key
        return self.request("POST", "call/", payload=payload)
