"""Responsibility: Bailian client: read connection settings, request JSON mode, and return raw model text.
Implementation: Validate configuration and output budgets; optional ordered model pools persist quota exclusions and move to the next candidate only after explicit quota exhaustion. Other errors fail without retries.
Relationships: Used by L1, L3, and workspace chat providers; prompts remain owned by callers.

Directory:
- LLMError: Model configuration, network, or response format error.
- _quota_code: Recognize explicit quota exhaustion without exposing provider bodies.
- _request_json: Execute a JSON request with bounded quota-only failover.
- generate_json: Send one model request; the caller's prompt specifies the JSON fields.
- generate_chat_json: Send strictly validated chat messages in their original order and return raw model JSON text.

Variable index:
- logger: Logs selected models and sanitized outcomes without prompts or credentials.
- _CHAT_MESSAGE_KEYS: Exact role/content keys required for chat messages.
- _CHAT_ROLES: Allowed ordered-chat roles.
"""

import os
import logging
from urllib.parse import urlsplit

import requests

from .pool import ModelPool, PoolError, QUOTA_CODES

logger = logging.getLogger("salesmate.llm")


# Function: Represent model configuration, network, or response failures.
# Logic: Preserve a safe caller-facing error without raw provider bodies.
# Constraints: Does not itself retry, change models, or reveal credentials.
class LLMError(RuntimeError):
    """Model configuration, network, or response format error."""


_CHAT_ROLES = frozenset({"system", "user", "assistant"})
_CHAT_MESSAGE_KEYS = frozenset({"role", "content"})


# Function: Identify provider-confirmed exhausted allocation.
# Inputs: HTTP `response` from the single model attempt.
# Outputs: A verified quota code or None.
# Logic: Require HTTP 403/429 and an exact allowlisted JSON code, supporting nested compatible and native error envelopes.
# Constraints: Generic rate limiting, authentication failures, malformed bodies, and message text never trigger switching.
def _quota_code(response):
    if response.status_code not in (403, 429):
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    code = error.get("code") if isinstance(error, dict) else body.get("code")
    return code if isinstance(code, str) and code in QUOTA_CODES else None


# Function: Execute one JSON generation with optional quota-only model failover.
# Inputs: Ordered `messages`, positive output `max_tokens`, and Bailian environment configuration.
# Outputs: Complete model text; safe LLMError for configuration, transport, provider, exhausted pool, or state failure.
# Logic: Keep prompt, thinking, JSON mode, timeout, and token budget fixed; each eligible candidate is called at most once. Persist explicit quota failure before selecting the next candidate.
# Constraints: Never replay successful model output or business tools; no failover on timeouts, generic 429, malformed output, or authentication errors. SQLite failure stops selection.
def _request_json(messages: list[dict[str, str]], *, max_tokens: int) -> str:
    if type(max_tokens) is not int or max_tokens <= 0:
        raise LLMError("max_tokens must be a positive integer.")
    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    base_url = os.getenv("BAILIAN_BASE_URL", "").strip().rstrip("/")
    model = os.getenv("BAILIAN_MODEL", "").strip()
    if not all((api_key, base_url)) or not (model or os.getenv("BAILIAN_MODELS", "").strip()):
        raise LLMError("Set DASHSCOPE_API_KEY, BAILIAN_BASE_URL, and BAILIAN_MODEL or BAILIAN_MODELS in the project-root .env first.")
    url = urlsplit(base_url)
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.query or url.fragment or "{" in base_url):
        raise LLMError("BAILIAN_BASE_URL must be the complete HTTPS URL from the console, without placeholders.")
    if base_url.endswith("/chat/completions"):
        raise LLMError("BAILIAN_BASE_URL must not end with /chat/completions.")
    payload = {"messages": messages, "response_format": {"type": "json_object"}, "max_tokens": max_tokens}
    thinking = os.getenv("BAILIAN_ENABLE_THINKING", "").strip().lower()
    if thinking:
        if thinking not in ("true", "false"):
            raise LLMError("BAILIAN_ENABLE_THINKING must be true, false, or empty.")
        payload["enable_thinking"] = thinking == "true"
    try:
        pool = ModelPool.from_env(base_url, api_key)
        candidates = pool.models if pool else (model,)
        for candidate in candidates:
            if pool and candidate in pool.disabled():
                continue
            logger.info("llm_model_request model=%s pooled=%s", candidate, pool is not None)
            try:
                response = requests.post(base_url + "/chat/completions",
                    headers={"Authorization": "Bearer " + api_key},
                    json={**payload, "model": candidate}, timeout=(10, 90), allow_redirects=False)
            except requests.RequestException as error:
                logger.warning("llm_transport_failed model=%s error_type=%s action=inspect_endpoint", candidate, type(error).__name__)
                raise LLMError("Bailian connection failed or timed out. Check the network and endpoint.") from None
            if response.status_code != 200:
                code = _quota_code(response)
                if pool and code:
                    pool.disable(candidate, code, response.status_code)
                    logger.warning("llm_model_failover exhausted_model=%s action=select_next_eligible", candidate)
                    continue
                logger.warning("llm_provider_failed model=%s http_status=%s quota_exhausted=%s", candidate, response.status_code, code is not None)
                raise LLMError(f"Bailian returned HTTP {response.status_code}. Check region, API key, model access, quota, and JSON-mode support.")
            try:
                choice = response.json()["choices"][0]
                content = choice["message"]["content"]
                if choice.get("finish_reason") != "stop" or not isinstance(content, str):
                    raise ValueError("Incomplete response")
            except (ValueError, KeyError, IndexError, TypeError):
                logger.warning("llm_response_invalid model=%s action=check_output_budget_and_compatibility", candidate)
                raise LLMError("Bailian did not return complete text. Check the output limit and model compatibility.") from None
            logger.info("llm_model_completed model=%s", candidate)
            return content
    except PoolError as error:
        raise LLMError(str(error)) from None
    logger.error("llm_pool_exhausted candidates=%s action=restore_quota_and_reenable_model", len(candidates))
    raise LLMError("All configured Bailian models have exhausted quota. Restore quota and explicitly re-enable a model in the pool.")


# Function: Generate JSON text from a system prompt and user input.
# Inputs: `system_prompt`, `user_text`, and output `max_tokens` defaulting to 2048.
# Outputs: Raw complete model text or LLMError.
# Logic: Build the established two-message payload and use the shared quota-aware transport.
# Constraints: Does not change caller prompts, output budget, or execute business actions.
def generate_json(system_prompt: str, user_text: str, *, max_tokens: int = 2048) -> str:
    """Send one model request; the caller's prompt specifies the JSON fields."""
    return _request_json(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
        max_tokens=max_tokens,
    )


# Function: Generate JSON text from an ordered chat transcript.
# Inputs: Nonempty role/content `messages` and output `max_tokens` defaulting to 2000.
# Outputs: Raw complete model text or LLMError.
# Logic: Validate exact fields and roles before passing unchanged message order to the shared transport.
# Constraints: Model switching preserves the same transcript; no tools or business operations are replayed here.
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
