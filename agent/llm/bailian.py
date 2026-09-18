"""百炼客户端：读取连接配置，请求 JSON 模式，返回模型原始文本。"""

import os
from urllib.parse import urlsplit

import requests


class LLMError(RuntimeError):
    """模型配置、网络或响应格式错误。"""


_CHAT_ROLES = frozenset({"system", "user", "assistant"})
_CHAT_MESSAGE_KEYS = frozenset({"role", "content"})


def _request_json(messages: list[dict[str, str]], *, max_tokens: int) -> str:
    """Send a validated JSON-object request through the shared Bailian transport."""
    if type(max_tokens) is not int or max_tokens <= 0:
        raise LLMError("max_tokens 必须是正整数。")
    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    base_url = os.getenv("BAILIAN_BASE_URL", "").strip().rstrip("/")
    model = os.getenv("BAILIAN_MODEL", "").strip()
    if not all((api_key, base_url, model)):
        raise LLMError("请先在项目根目录 .env 填写 DASHSCOPE_API_KEY、BAILIAN_BASE_URL、BAILIAN_MODEL。")
    url = urlsplit(base_url)
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.query or url.fragment or "{" in base_url):
        raise LLMError("BAILIAN_BASE_URL 必须是控制台提供的完整 HTTPS 地址，不能含占位符。")
    if base_url.endswith("/chat/completions"):
        raise LLMError("BAILIAN_BASE_URL 不要包含末尾的 /chat/completions。")

    payload = {
        "model": model,
        "messages": messages,
        "response_format": {"type": "json_object"},
        "max_tokens": max_tokens,
    }
    thinking = os.getenv("BAILIAN_ENABLE_THINKING", "").strip().lower()
    if thinking:
        if thinking not in ("true", "false"):
            raise LLMError("BAILIAN_ENABLE_THINKING 只能填 true、false 或留空。")
        payload["enable_thinking"] = thinking == "true"

    try:
        response = requests.post(
            base_url + "/chat/completions",
            headers={"Authorization": "Bearer " + api_key},
            json=payload,
            timeout=(10, 90),
            allow_redirects=False,
        )
    except requests.RequestException:
        raise LLMError("百炼网络连接失败或超时，请检查网络及接入地址。") from None
    if response.status_code != 200:
        # 不打印请求头、Key 或未经检查的服务端响应正文。
        raise LLMError(
            f"百炼返回 HTTP {response.status_code}。请检查地域、Key、模型权限、额度和 JSON 模式支持。"
        )
    try:
        choice = response.json()["choices"][0]
        content = choice["message"]["content"]
        if choice.get("finish_reason") != "stop" or not isinstance(content, str):
            raise ValueError("结果未完整返回")
    except (ValueError, KeyError, IndexError, TypeError):
        raise LLMError("百炼没有返回完整文本结果，请检查输出长度和模型兼容性。") from None
    return content


def generate_json(system_prompt: str, user_text: str, *, max_tokens: int = 2048) -> str:
    """执行一次模型请求；具体 JSON 字段通过调用方的提示词规定。"""
    return _request_json(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
        max_tokens=max_tokens,
    )


def generate_chat_json(
    messages: list[dict[str, str]],
    *,
    max_tokens: int = 2000,
) -> str:
    """按原顺序发送严格校验的聊天消息并返回模型原始 JSON 文本。"""
    if type(messages) is not list or not messages:
        raise LLMError("messages 必须是非空列表。")

    validated_messages = []
    for message in messages:
        if type(message) is not dict or set(message) != _CHAT_MESSAGE_KEYS:
            raise LLMError("每条消息必须且只能包含 role 和 content。")
        role = message["role"]
        content = message["content"]
        if type(role) is not str or role not in _CHAT_ROLES:
            raise LLMError("消息 role 只能是 system、user 或 assistant。")
        if type(content) is not str or not content.strip():
            raise LLMError("消息 content 必须是非空字符串。")
        validated_messages.append({"role": role, "content": content})

    return _request_json(validated_messages, max_tokens=max_tokens)
