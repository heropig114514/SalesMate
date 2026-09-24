"""职责：按冻结双 T4 条件执行单题 CRMArena 推理。
实现：仅加载管理员配置的已下载权重，核验摘要与环境；每进程串行生成，失败后要求重启。
关联：crmarena 提供证据与冻结题，pipeline 执行原始抽取、计算和生成；HTTP/MCP 共用同一入口。
目录：
- CRMArenaBusy：并发推理忙错误。
- CRMArenaOutputError：模型最终输出违约错误。
- infer：执行一次不含下载的单题流程。
- Runtime：进程内串行模型运行器。
- Runtime.__init__：建立冷启动状态与锁。
- Runtime.status：读取无副作用运行状态。
- Runtime.load：离线核验并加载原 FP16 双卡模型。
- Runtime.predict：串行执行并记录状态转换。
变量索引：
- logger：模型加载与推理边界日志，不记录提示、令牌或通话。
- runtime：进程唯一运行器；部署限定一个 GPU 服务进程。
- CRMArenaBusy.status_code：429。
- CRMArenaBusy.default_code：crmarena_busy。
- CRMArenaBusy.default_detail：忙时提示。
- CRMArenaOutputError.status_code：502。
- CRMArenaOutputError.default_code：crmarena_output_invalid。
- CRMArenaOutputError.default_detail：最终契约不符合时的提示。
"""
from datetime import datetime, timezone
import importlib.metadata
import json
import logging
from pathlib import Path
import threading

from django.conf import settings
from rest_framework.exceptions import APIException

from .crmarena import CRMArenaUnavailable, file_sha
from . import crmarena_pipeline as pipeline

logger = logging.getLogger("salesmate.crmarena.runtime")


# 功能：拒绝同时占用同一模型的请求。
# 逻辑：忙时立即返回 429，不排队也不自动重试。
# 约束：不是用户配额控制，多进程 GPU 竞争须由部署配置避免。
class CRMArenaBusy(APIException):
    status_code = 429
    default_code = "crmarena_busy"
    default_detail = "模型正在处理另一请求，请在其结束后显式重新调用。"


# 功能：标识最终输出不满足原 JSON 或引用契约。
# 逻辑：返回 502，保留服务器上的异常类别。
# 约束：不修复答案，也不回放旧记录。
class CRMArenaOutputError(APIException):
    status_code = 502
    default_code = "crmarena_output_invalid"
    default_detail = "模型最终输出不符合 JSON 或来源引用契约，本次推理失败。"


# 功能：执行冻结输入的抽取、校验、计算与最终生成。
# 输入：`store` 为已验证制品，`lead_id` 为固定题 Lead，`generate_fn` 接受消息和输出预算。
# 输出：实时结果、原回答、候选事实校验与计算轨迹，或明确的输出异常。
# 逻辑：抽取失败沿用实验门控；通过后才运行最终模型，严格解析且检查通话引用归属。
# 约束：不读取官方标签、不写回业务状态、不修改冻结提示或修复 JSON；generate_fn 的异常向上传播。
def infer(store, lead_id, generate_fn):
    query, base = store.case(lead_id)
    packet = store.evidence(lead_id)["evidence"]
    if len(packet["documents"]) != 1 or [d["id"] for d in packet["documents"]] != base["provided_ids"]:
        raise CRMArenaUnavailable("图证据与冻结输入不一致，请核验制品。")
    extraction_raw = generate_fn(pipeline.extract_prompt(packet), pipeline.CONFIG["extract_tokens"])
    extraction = pipeline.validate_extraction(extraction_raw["raw_text"], packet)
    calculation = None
    if not extraction["valid"]:
        origin = "extraction_failure_gate"
        raw = {"raw_text": json.dumps({"failed_factors": [], "evidence_ids": [], "insufficient_evidence": True}),
               "seconds": 0., "input_tokens": 0, "output_tokens": 0}
        logger.info("extraction_rejected query=%s errors=%s", query["id"], extraction["errors"])
    else:
        calculation = pipeline.calculate(extraction, packet)
        raw = generate_fn(pipeline.answer_prompt(base, packet, extraction, calculation), pipeline.CONFIG["answer_tokens"])
        origin = "llm"
    parsed = pipeline.parse_answer(raw["raw_text"])
    if not parsed["valid"] or (not parsed["abstain"] and (not parsed["evidence_ids"] or not set(parsed["evidence_ids"]) <= set(base["provided_ids"]))):
        raise CRMArenaOutputError()
    return {**store.envelope(), "execution_type": "live_inference", "lead_id": lead_id,
            "query_id": query["id"], "question": query["question"], "answer_origin": origin,
            "answer": json.loads(raw["raw_text"]), "raw_text": raw["raw_text"],
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "extraction": {"validation": extraction, **extraction_raw}, "calculation": calculation,
            "generation": {k: v for k, v in raw.items() if k != "raw_text"}}


# 功能：管理可审计的单进程实时模型。
# 逻辑：锁覆盖首次加载和两阶段推理；服务运行错误置 failure，后续请求要求重启。
# 约束：不自动安装依赖或下载模型；不降级单卡、CPU、量化或其他模型。
class Runtime:
    # 功能：建立进程内初始状态。
    # 输入：无外部参数。
    # 输出：lock、tokenizer、model、failure 实例状态。
    # 逻辑：构造时不导入 torch，不访问 GPU。
    # 约束：模型应由单一服务进程持有。
    def __init__(self):
        self.lock = threading.Lock()
        self.tokenizer = None
        self.model = None
        self.failure = None

    # 功能：查询已观察的运行状态，不触发加载。
    # 输入：无外部参数；读取 settings.CRMARENA_MODEL_DIR 与实例状态。
    # 输出：configured、loaded、busy、failed 和运行前提。
    # 逻辑：configured 只表示设置了路径，不能表示权重或 GPU 可用。
    # 约束：不向客户端公开磁盘路径、错误细节或凭据。
    def status(self):
        return {"configured": bool(settings.CRMARENA_MODEL_DIR), "loaded": self.model is not None,
                "busy": self.lock.locked(), "failed": self.failure is not None,
                "requirements": "2 Tesla T4; FP16; torch 2.10.0+cu128; transformers 4.56.2; accelerate 1.10.1; verified local weights",
                "restart_required": self.failure is not None}

    # 功能：核验环境和原权重后离线分层加载。
    # 输入：`store` 提供固定 model_audit；读取管理员配置模型路径。
    # 输出：初始化 tokenizer 和 model；失败传播给 predict 处理。
    # 逻辑：核验版本、双 T4、每个模型与 tokenizer 文件 SHA；恢复实验相同映射和 greedy 配置。
    # 约束：调用前持有 lock；仅 local_files_only，不执行 pip 或网络下载。
    def load(self, store):
        if not settings.CRMARENA_MODEL_DIR:
            raise CRMArenaUnavailable("未配置 CRMARENA_MODEL_DIR；实时推理需要本地固定权重与双 T4。")
        for name, expected in {"torch": "2.10.0+cu128", "transformers": "4.56.2", "accelerate": "1.10.1", "huggingface-hub": "0.36.2"}.items():
            if importlib.metadata.version(name) != expected:
                raise RuntimeError("Dependency version mismatch: " + name)
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        if not torch.cuda.is_available() or torch.cuda.device_count() != 2 or torch.version.cuda != "12.8":
            raise RuntimeError("Expected exactly two CUDA 12.8 GPUs")
        if any(torch.cuda.get_device_name(i) != "Tesla T4" for i in range(2)):
            raise RuntimeError("Frozen runtime requires two Tesla T4 GPUs")
        directory = Path(settings.CRMARENA_MODEL_DIR)
        for name, expected in store.model_audit["local_sha256"].items():
            if file_sha(directory / name) != expected:
                raise RuntimeError("Model file SHA mismatch: " + name)
        config = json.loads((directory / "config.json").read_text(encoding="utf8"))
        if config["num_hidden_layers"] != 36 or config["tie_word_embeddings"] is not True:
            raise RuntimeError("Model architecture mismatch")
        torch.manual_seed(pipeline.CONFIG["seed"])
        torch.set_num_threads(2)
        logger.info("model_load_started revision=%s devices=2", pipeline.CONFIG["revision"])
        tokenizer = AutoTokenizer.from_pretrained(directory, local_files_only=True, trust_remote_code=False)
        placement = {"model.embed_tokens": 0, "model.rotary_emb": 0, "model.norm": 1, "lm_head": 0}
        placement.update({"model.layers." + str(i): 0 if i < 18 else 1 for i in range(36)})
        model = AutoModelForCausalLM.from_pretrained(directory, torch_dtype=torch.float16, attn_implementation="sdpa", device_map=placement,
                                                   local_files_only=True, trust_remote_code=False).eval()
        if model.hf_device_map != placement or {str(p.device) for p in model.parameters()} != {"cuda:0", "cuda:1"} or model.model.embed_tokens.weight is not model.lm_head.weight:
            raise RuntimeError("Model device map or tied weights mismatch")
        if any(p.dtype != torch.float16 for p in model.parameters()):
            raise RuntimeError("Expected all FP16 parameters")
        model.generation_config.do_sample = False
        model.generation_config.temperature = None
        model.generation_config.top_p = None
        model.generation_config.top_k = None
        self.tokenizer, self.model = tokenizer, model
        logger.info("model_loaded revision=%s devices=%s", pipeline.CONFIG["revision"], model.hf_device_map)

    # 功能：执行单题并管理并发、加载与失败状态。
    # 输入：`store` 为已核验公开制品，`lead_id` 为固定题。
    # 输出：infer 结果；忙为 429，条件不足为 503，输出违约为 502。
    # 逻辑：先验证题目范围，非阻塞获取锁；失败类别记日志；finally 释放锁。
    # 约束：模型配置/运行失败后要求修复并重启；格式违约不重试；不记录原始通话。
    def predict(self, store, lead_id):
        store.case(lead_id)
        if not self.lock.acquire(blocking=False):
            raise CRMArenaBusy()
        try:
            if self.failure is not None:
                raise CRMArenaUnavailable("实时模型此前加载或执行失败，请检查日志、修复并重启服务。")
            if self.model is None:
                self.load(store)
            logger.info("inference_started lead=%s", lead_id)
            result = infer(store, lead_id, lambda messages, budget: pipeline.generate(messages, self.tokenizer, self.model, budget))
            logger.info("inference_completed lead=%s origin=%s", lead_id, result["answer_origin"])
            return result
        except CRMArenaOutputError:
            logger.warning("inference_invalid_output lead=%s", lead_id)
            raise
        except CRMArenaUnavailable:
            logger.warning("inference_not_ready lead=%s configured=%s", lead_id, bool(settings.CRMARENA_MODEL_DIR))
            raise
        except (ImportError, OSError, RuntimeError, ValueError, KeyError) as exc:
            self.failure = type(exc).__name__
            logger.exception("inference_failed lead=%s error_type=%s restart_required=true", lead_id, type(exc).__name__)
            raise CRMArenaUnavailable() from exc
        finally:
            self.lock.release()


runtime = Runtime()
