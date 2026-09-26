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
        raise LLMError("max_tokens must be a positive integer.")
    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    base_url = os.getenv("BAILIAN_BASE_URL", "").strip().rstrip("/")
    model = os.getenv("BAILIAN_MODEL", "").strip()
    if not all((api_key, base_url, model)):
        raise LLMError("Set DASHSCOPE_API_KEY, BAILIAN_BASE_URL, and BAILIAN_MODEL in the project-root .env first.")
    url = urlsplit(base_url)
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.query or url.fragment or "{" in base_url):
        raise LLMError("BAILIAN_BASE_URL must be the complete HTTPS URL from the console, without placeholders.")
    if base_url.endswith("/chat/completions"):
        raise LLMError("BAILIAN_BASE_URL must not end with /chat/completions.")

    payload = {
        "model": model,
        "messages": messages,
        "response_format": {"type": "json_object"},
        "max_tokens": max_tokens,
    }
    thinking = os.getenv("BAILIAN_ENABLE_THINKING", "").strip().lower()
    if thinking:
        if thinking not in ("true", "false"):
            raise LLMError("BAILIAN_ENABLE_THINKING must be true, false, or empty.")
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
        raise LLMError("Bailian connection failed or timed out. Check the network and endpoint.") from None
    if response.status_code != 200:
        # 不打印请求头、Key 或未经检查的服务端响应正文。
        raise LLMError(
            f"Bailian returned HTTP {response.status_code}. Check region, API key, model access, quota, and JSON-mode support."
        )
    try:
        choice = response.json()["choices"][0]
        content = choice["message"]["content"]
        if choice.get("finish_reason") != "stop" or not isinstance(content, str):
            raise ValueError("Incomplete response")
    except (ValueError, KeyError, IndexError, TypeError):
        raise LLMError("Bailian did not return complete text. Check the output limit and model compatibility.") from None
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
        raise LLMError("messages must be a nonempty list.")

    validated_messages = []
    for message in messages:
        if type(message) is not dict or set(message) != _CHAT_MESSAGE_KEYS:
            raise LLMError("Each message must contain only role and content.")
        role = message["role"]
        content = message["content"]
        if type(role) is not str or role not in _CHAT_ROLES:
            raise LLMError("Message role must be system, user, or assistant.")
        if type(content) is not str or not content.strip():
            raise LLMError("Message content must be a nonempty string.")
        validated_messages.append({"role": role, "content": content})

    return _request_json(validated_messages, max_tokens=max_tokens)
