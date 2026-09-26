"""Responsibility: Bailian client: read connection settings, request JSON mode, and return raw model text.
Implementation: Validate credentials, message shape, and output budgets before one JSON-mode HTTP call; sanitize transport errors.
Relationships: Used by L1, L3, and workspace chat providers; prompts remain owned by callers.

Directory:
- LLMError: Model configuration, network, or response format error.
- _request_json: Send a validated JSON-object request through the shared Bailian transport.
- generate_json: Send one model request; the caller's prompt specifies the JSON fields.
- generate_chat_json: Send strictly validated chat messages in their original order and return raw model JSON text.

Variable index:
- _CHAT_MESSAGE_KEYS: Exact role/content keys required for chat messages.
- _CHAT_ROLES: Allowed ordered-chat roles.
"""

import os
from urllib.parse import urlsplit

import requests


class LLMError(RuntimeError):
    """Model configuration, network, or response format error."""


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
        # Do not print request headers, keys, or unchecked server response bodies.
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
    """Send one model request; the caller's prompt specifies the JSON fields."""
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
    """Send strictly validated chat messages in their original order and return raw model JSON text."""
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
