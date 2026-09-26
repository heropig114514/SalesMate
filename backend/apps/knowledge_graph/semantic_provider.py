"""Responsibility: Invoke an explicitly configured administrator-managed local model to generate graph candidates from external text.
Implementation: Allow only loopback HTTP and constrain output with JSON Schema; explicit inference profiles control prompt ordering and caching, without retries or fallbacks.
Relationships: episodes handles permissions and validation; this module does not write to the database and maintains inference profiles separately from authorization checks.
Directory:
- generate: Perform one local structured generation and return audit metadata.
Variable index:
- logger: Model-boundary logs excluding original text, prompts, and response bodies.
- MAX_OUTPUT_TOKENS: Generation budget for this semantic graph protocol.
"""
import hashlib
import json
import logging
import os
import time
from urllib.parse import urlsplit

import requests

from .semantic_contract import VERSION, response_schema
from .inference_profiles import get_profile, prepare_messages

logger = logging.getLogger("salesmate.graph.semantic_provider")
MAX_OUTPUT_TOKENS = 1536


# Function: Perform bounded generation through a local model.
# Inputs: `prompt`: authorized message list; GRAPH_LLM_URL/MODEL must be explicit, while GRAPH_LLM_PROFILE defaults to baseline.
# Outputs: JSON object plus model/parameter/prompt-digest audit data; HTTP, format, or budget failures raise exceptions.
# Logic: Validate configuration and prepare complete messages; prohibit redirects and environment proxies, requiring normal termination and the specified model identity.
# Constraints: Support only local JSON-Schema-capable services with context sliding disabled; timeout remains 300 seconds and output budget 1536 tokens, without repair or model substitution.
def generate(prompt):
    endpoint = os.environ.get("GRAPH_LLM_URL", "")
    model = os.environ.get("GRAPH_LLM_MODEL", "")
    url = urlsplit(endpoint)
    if url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost", "::1"} or url.username or url.password or url.query or url.fragment or not model:
        raise RuntimeError("Configure a loopback GRAPH_LLM_URL and GRAPH_LLM_MODEL")
    profile_name = os.environ.get("GRAPH_LLM_PROFILE", "baseline")
    profile = get_profile(profile_name)
    prepared = prepare_messages(prompt, profile)
    payload = {"model": model, "messages": prepared, "temperature": 0, "seed": 2026,
               "max_tokens": MAX_OUTPUT_TOKENS, "stream": False, "cache_prompt": profile["cache"],
               "response_format": {"type": "json_schema", "json_schema": {"name": "crm_graph_observation", "strict": True, "schema": response_schema()}}}
    started = time.monotonic()
    logger.info("semantic_generation_started model=%s protocol=%s profile=%s", model, VERSION, profile_name)
    try:
        with requests.Session() as session:
            session.trust_env = False
            response = session.post(endpoint.rstrip("/") + "/chat/completions", json=payload, timeout=(10, 300), allow_redirects=False)
            if response.status_code != 200:
                raise RuntimeError("Local model HTTP failure: " + str(response.status_code))
            result = response.json()
        choice = result["choices"][0]
        if choice["finish_reason"] != "stop" or result.get("model") != model:
            raise ValueError("Model identity or generation completion mismatch")
        parsed = json.loads(choice["message"]["content"])
    except Exception as exc:
        logger.error("semantic_generation_failed error_type=%s action=inspect_local_model_no_automatic_retry", type(exc).__name__)
        raise
    elapsed = time.monotonic() - started
    logger.info("semantic_generation_completed seconds=%.3f", elapsed)
    return parsed, {"protocol": VERSION, "requested_model": model, "returned_model": result["model"],
                    "temperature": 0, "seed": 2026, "max_output_tokens": MAX_OUTPUT_TOKENS, "output_contract": "json_schema",
                    "prompt_sha256": hashlib.sha256(json.dumps(prepared, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
                    "inference_profile": profile_name, "requested_inference_config": profile,
                    "seconds": elapsed, "usage": result.get("usage"), "timings": result.get("timings"),
                    "finish_reason": choice["finish_reason"]}
