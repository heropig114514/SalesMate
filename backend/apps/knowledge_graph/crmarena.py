"""职责：为后端提供版本固定的公开 CRMArena 证据与评测记录读取。
实现：首次读取校验全部制品摘要；公共合成数据使用独立命名空间，不映射业务客户。
关联：crmarena_views 与 MCP 共用此服务；crmarena_pipeline 保留冻结算法，runtime 执行实时推理。
目录：
- CRMArenaUnavailable：制品或推理运行条件不可用。
- file_sha：文件 SHA256。
- CRMArenaStore：已验证公开制品读模型。
- CRMArenaStore.__init__：核验制品并建立 Lead 索引。
- CRMArenaStore.envelope：公共来源元数据。
- CRMArenaStore.info：返回范围、指标与示例。
- CRMArenaStore.case：定位固定评测题。
- CRMArenaStore.evidence：只读查询原始证据。
- CRMArenaStore.evaluation：显式回放已完成结果。
- get_store：缓存已验证制品。
变量索引：
- ARTIFACT_DIR：独立公开制品目录。
- DATASET_ID：调用方须显式声明的数据空间。
- logger：只记录版本、数量与错误类型，不记录通话正文。
- CRMArenaUnavailable.status_code：503。
- CRMArenaUnavailable.default_code：crmarena_unavailable。
- CRMArenaUnavailable.default_detail：制品或运行条件不可用提示。
"""
from functools import lru_cache
import hashlib
import json
import logging
from pathlib import Path
import sqlite3

from rest_framework.exceptions import APIException, NotFound

from .crmarena_pipeline import query_evidence, parse_answer

ARTIFACT_DIR = Path(__file__).resolve().parents[2] / "model_store" / "crmarena-pro-b2b-v3"
DATASET_ID = "crmarena-pro-b2b-v3"
logger = logging.getLogger("salesmate.crmarena")


# 功能：报告公开制品或实时模型未就绪。
# 逻辑：向 HTTP 和工具调用传播同一 503，诊断细节保留于服务日志。
# 约束：不下载、不重试，不切换旧答案或其他模型。
class CRMArenaUnavailable(APIException):
    status_code = 503
    default_code = "crmarena_unavailable"
    default_detail = "CRMArena 服务未就绪，请检查制品安装、模型配置与服务日志。"


# 功能：计算文件摘要。
# 输入：`path` 为本机可信配置路径。
# 输出：SHA256 十六进制值。
# 逻辑：流式读取，不修改文件。
# 约束：读取异常传播，路径不由 API 调用者提供。
def file_sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


# 功能：持有与已完成 v3 实验对应的公共图与结果。
# 逻辑：清单摘要、75 题与结果一一对应；SQLite 每次使用只读连接。
# 约束：进程存活期间制品必须不可变；不接受私有客户 ID，不查询 SalesMate 业务表。
class CRMArenaStore:
    # 功能：核验来源并建立查询索引。
    # 输入：`directory` 为制品目录，清单由项目随代码发布。
    # 输出：初始化 manifest、graph、queries、bases、answers、metrics、audit、model_audit。
    # 逻辑：校验完整清单后加载元数据；未知结构或缺失文件明确失败。
    # 约束：摘要保证匹配已发布清单，不替代对清单发布渠道的信任。
    def __init__(self, directory):
        directory = Path(directory)
        try:
            self.manifest = json.loads((directory / "manifest.json").read_text(encoding="utf8"))
            required = {"knowledge_graph.sqlite", "queries.json", "base_prompts.json", "answers.jsonl", "model_audit.json", "graph_audit.json", "metrics.json"}
            if self.manifest["dataset_id"] != DATASET_ID or set(self.manifest["files"]) != required:
                raise ValueError("Unexpected artifact contract")
            for name, expected in self.manifest["files"].items():
                if file_sha(directory / name) != expected:
                    raise ValueError("Artifact checksum mismatch: " + name)
            self.graph = directory / "knowledge_graph.sqlite"
            queries = json.loads((directory / "queries.json").read_text(encoding="utf8"))
            self.queries = {q["anchor_id"]: q for q in queries}
            self.bases = json.loads((directory / "base_prompts.json").read_text(encoding="utf8"))
            records = [json.loads(line) for line in (directory / "answers.jsonl").read_text(encoding="utf8").splitlines()]
            self.answers = {a["query_id"]: a for a in records}
            ids = {q["id"] for q in queries}
            if len(queries) != 75 or len(self.queries) != 75 or len(records) != 75 or set(self.answers) != ids or set(self.bases) != ids:
                raise ValueError("Expected 75 unique fixed cases and records")
            if any(not parse_answer(a["raw_text"])["valid"] for a in records):
                raise ValueError("Invalid saved answer contract")
            self.metrics = json.loads((directory / "metrics.json").read_text(encoding="utf8"))
            self.audit = json.loads((directory / "graph_audit.json").read_text(encoding="utf8"))
            self.model_audit = json.loads((directory / "model_audit.json").read_text(encoding="utf8"))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.exception("artifact_load_failed directory=%s error_type=%s", directory, type(exc).__name__)
            raise CRMArenaUnavailable() from exc
        logger.info("artifact_loaded dataset=%s cases=%s nodes=%s", DATASET_ID, len(self.queries), self.audit["nodes"])

    # 功能：创建每次响应的明确数据身份。
    # 输入：无外部参数；读取已核验 manifest。
    # 输出：公共合成标识、模型与图版本及研究限制。
    # 逻辑：不包含私有用户数据或磁盘路径。
    # 约束：实验判断不构成概率或业务状态写入。
    def envelope(self):
        return {"dataset_id": DATASET_ID, "synthetic": True, "usage_scope": self.manifest["usage_scope"],
                "model_id": self.manifest["model_id"], "model_revision": self.manifest["model_revision"],
                "graph_sha256": self.manifest["files"]["knowledge_graph.sqlite"],
                "limitations": ["Public synthetic research data; not private CRM leads", "BANT assessment is not win probability", "Regression grounded exact match: 8/60; not validated for automated business decisions"]}

    # 功能：展示数据范围和实际评测指标。
    # 输入：无外部参数；使用已核验图审计和指标。
    # 输出：节点/边数、固定题数量、样例 Lead 与回归分数。
    # 逻辑：证据可查询图中有关联通话的 Lead；回放和推理仅支持冻结的 75 题。
    # 约束：不将模型文件配置等同于实时模型已加载。
    def info(self):
        return {**self.envelope(), "graph": self.audit, "inference_case_count": len(self.queries),
                "lead_examples": list(self.queries)[:5], "regression": self.metrics["summary"]["test"],
                "comparison": self.metrics["comparison"], "evidence_scope": "graph leads with linked calls",
                "prediction_scope": "75 frozen public benchmark cases; no custom questions"}

    # 功能：定位已冻结的模型输入。
    # 输入：`lead_id` 为公开 Lead 标识。
    # 输出：问题记录与原提示；不在固定集合时抛 404。
    # 逻辑：不根据任意用户输入构造新的实验提示。
    # 约束：不能将 SalesMate UUID 或私有会话套用到公开样本。
    def case(self, lead_id):
        if lead_id not in self.queries:
            raise NotFound("该 Lead 不在冻结的 75 道公开评测题中。")
        query = self.queries[lead_id]
        return query, self.bases[query["id"]]

    # 功能：读取图中的精确来源证据。
    # 输入：`lead_id` 为公开 Lead 标识。
    # 输出：通话、产品价格、政策原文与来源关系；不存在或没有通话时 404。
    # 逻辑：调用冻结只读算法；结构错误记日志并转为 503。
    # 约束：不生成判断，不自动写回派生事实。
    def evidence(self, lead_id):
        try:
            packet = query_evidence(self.graph, lead_id)
        except KeyError as exc:
            raise NotFound("公开图谱中不存在该 Lead。") from exc
        except (ValueError, sqlite3.Error) as exc:
            if str(exc) == "No linked call evidence":
                raise NotFound("该公开 Lead 没有关联通话。") from exc
            logger.error("evidence_query_failed lead=%s error_type=%s", lead_id, type(exc).__name__)
            raise CRMArenaUnavailable() from exc
        return {**self.envelope(), "execution_type": "evidence_query", "evidence": packet}

    # 功能：回放已完成 Kaggle 实验的原记录。
    # 输入：`lead_id` 为冻结题对应的公开 Lead。
    # 输出：显式 recorded_evaluation、答案、来源题目及原运行标识。
    # 逻辑：读取已核验结果，保留 LLM 与抽取门控来源区别。
    # 约束：不调用模型，不把保存结果标成实时推理。
    def evaluation(self, lead_id):
        query, _ = self.case(lead_id)
        record = self.answers[query["id"]]
        return {**self.envelope(), "execution_type": "recorded_evaluation", "lead_id": lead_id,
                "query_id": query["id"], "question": query["question"], "answer": json.loads(record["raw_text"]),
                "answer_origin": record["answer_origin"], "raw_text": record["raw_text"],
                "kaggle_run_id": self.manifest["kaggle_run_id"], "kaggle_version": self.manifest["kaggle_version"],
                "recorded_seconds": record["seconds"] + record["extraction_seconds"]}


# 功能：取得进程内已验证的公共读模型。
# 输入：无外部参数；读取固定 ARTIFACT_DIR。
# 输出：CRMArenaStore；首次失败传播 503。
# 逻辑：成功实例缓存一次，升级需重启进程。
# 约束：请求不会触发下载、安装或清单更新。
@lru_cache(maxsize=1)
def get_store():
    return CRMArenaStore(ARTIFACT_DIR)
