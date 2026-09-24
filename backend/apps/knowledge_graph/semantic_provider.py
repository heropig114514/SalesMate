"""职责：调用管理员显式配置的本机模型，生成外部文本图谱候选。
实现：仅允许回环 HTTP 接口并以 JSON Schema 约束生成；一次请求，无重试、修复或远程回退。
关联：episodes 负责权限与校验；本模块不写库，不复用或改变 CRMArena 冻结推理条件。
目录：
- generate：执行一次本机结构化生成并返回审计元数据。
变量索引：
- logger：模型边界日志，不记录原文、提示或响应正文。
- MAX_OUTPUT_TOKENS：本语义建图协议的生成预算。
"""
import hashlib
import json
import logging
import os
import time
from urllib.parse import urlsplit

import requests

from .semantic_contract import VERSION, response_schema

logger = logging.getLogger("salesmate.graph.semantic_provider")
MAX_OUTPUT_TOKENS = 1536


# 功能：通过本机模型执行有界生成。
# 输入：`prompt` 为已授权的消息列表；环境变量 GRAPH_LLM_URL 和 GRAPH_LLM_MODEL 必须显式配置。
# 输出：JSON 对象及模型/参数/提示摘要审计；HTTP、格式或预算失败抛异常。
# 逻辑：禁止重定向和环境代理；JSON Schema 限定输出结构，要求正常停止及指定模型标识。
# 约束：仅支持带 JSON Schema 的本机服务；部署须关闭上下文滑动；超时300秒、输出1536 tokens，不修复或更换模型。
def generate(prompt):
    endpoint = os.environ.get("GRAPH_LLM_URL", "")
    model = os.environ.get("GRAPH_LLM_MODEL", "")
    url = urlsplit(endpoint)
    if url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost", "::1"} or url.username or url.password or url.query or url.fragment or not model:
        raise RuntimeError("Configure a loopback GRAPH_LLM_URL and GRAPH_LLM_MODEL")
    payload = {"model": model, "messages": prompt, "temperature": 0, "seed": 2026,
               "max_tokens": MAX_OUTPUT_TOKENS, "stream": False,
               "response_format": {"type": "json_schema", "json_schema": {"name": "crm_graph_observation", "strict": True, "schema": response_schema()}}}
    started = time.monotonic()
    logger.info("semantic_generation_started model=%s protocol=%s", model, VERSION)
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
                    "prompt_sha256": hashlib.sha256(json.dumps(prompt, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
                    "seconds": elapsed, "usage": result.get("usage"), "finish_reason": choice["finish_reason"]}
