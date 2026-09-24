"""职责：验证 CRMArena 制品、HTTP/MCP 边界与两阶段推理编排。
实现：临时合成 SQLite 和75个索引记录；真实 DRF 路由，模拟 GPU 生成，不访问业务数据库。
关联：knowledge_graph.crmarena 系列、agent_tools 和安装器；真实双T4质量仍以 Kaggle 实验为准。
目录：
- make_bundle：创建独立合成制品。
- CRMArenaTests：接口与服务测试。
- CRMArenaTests.setUp：安装夹具并绑定认证客户端。
- CRMArenaTests.test_evidence_and_recorded_result：验证来源与回放区分。
- CRMArenaTests.test_strict_inputs_auth_and_unknown_lead：验证请求及匿名边界。
- CRMArenaTests.test_corruption_and_installation：验证制品校验与拒绝覆盖。
- CRMArenaTests.test_live_unavailable_and_busy：验证未配置与并发错误。
- CRMArenaTests.test_live_pipeline_and_failure_gate：验证生成预算及明确弃答。
- CRMArenaTests.test_invalid_model_output_is_not_repaired：验证最终输出失败。
- CRMArenaTests.test_runtime_failure_requires_restart：验证运行失败不自动重试。
- CRMArenaTests.test_mcp_catalog_call_and_scope：验证工具发现、调用和授权。
变量索引：
- LEAD：夹具公开 Lead ID。
- BODY：包含可验证产品、数量、预算和天数的合成通话。
"""
from datetime import timedelta
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.agent_tools.models import ToolCredential
from apps.knowledge_graph.crmarena import CRMArenaStore, CRMArenaUnavailable, DATASET_ID, file_sha
from apps.knowledge_graph.crmarena_pipeline import POLICY_TITLES, node_id
from apps.knowledge_graph.crmarena_runtime import Runtime, infer, CRMArenaOutputError
from tools.install_crmarena import install

LEAD = "00Q000000000000001"
BODY = "We request one unit of Widget Alpha. Our budget is $100. Install within one day."


# 功能：创建不含真实业务数据的独立测试包。
# 输入：`directory` 为 TemporaryDirectory 路径。
# 输出：无；写清单、图、75题索引、原提示、原答案和最小审计。
# 逻辑：仅首题拥有完整图证据，产品标价80；其余记录用于基数契约；提交后显式关闭 SQLite 句柄以便 Windows 清理。
# 约束：模拟制品不是公开实验成果，不用于评价模型准确率。
def make_bundle(directory):
    graph = directory / "knowledge_graph.sqlite"
    with closing(sqlite3.connect(graph)) as db:
        db.executescript("CREATE TABLE nodes(id TEXT PRIMARY KEY, source_table TEXT, source_id TEXT, row_sha256 TEXT, attributes_json TEXT); CREATE TABLE edges(id TEXT, source TEXT, target TEXT, relation TEXT, derivation TEXT, provenance_json TEXT);")
        nodes = [("Lead", LEAD, {}), ("VoiceCallTranscript__c", "CALL", {"LeadId__c": LEAD, "Body__c": BODY}),
                 ("Product2", "PRODUCT", {"Name": "Widget Alpha", "Description": "A test widget"}),
                 ("Pricebook2", "BOOK", {"Name": "Test Book"}),
                 ("PricebookEntry", "PRICE", {"Product2Id": "PRODUCT", "Pricebook2Id": "BOOK", "UnitPrice": "80.00"})]
        nodes += [("Knowledge__kav", "POLICY" + str(i), {"Title": title, "FAQ_Answer__c": "1. Widget Alpha test clause."}) for i, title in enumerate(POLICY_TITLES)]
        db.executemany("INSERT INTO nodes VALUES(?,?,?,?,?)", [(node_id(t, i), t, i, "fixture-sha", json.dumps(a)) for t, i, a in nodes])
        edges = [(node_id("VoiceCallTranscript__c", "CALL"), node_id("Lead", LEAD), "VoiceCallTranscript__c.LeadId__c"),
                 (node_id("VoiceCallTranscript__c", "CALL"), node_id("Product2", "PRODUCT"), "VoiceCallTranscript__c.mentionsProduct"),
                 (node_id("PricebookEntry", "PRICE"), node_id("Product2", "PRODUCT"), "PricebookEntry.Product2Id")]
        db.executemany("INSERT INTO edges VALUES(?,?,?,?,?,?)", [(str(i), a, b, rel, "fixture", "{}") for i, (a, b, rel) in enumerate(edges)])
        db.commit()
    queries = [{"id": str(i), "anchor_id": "00Q" + str(i + 1).zfill(15), "question": "Assess BANT", "split": "test"} for i in range(75)]
    call_id = node_id("VoiceCallTranscript__c", "CALL")
    bases = {q["id"]: {"provided_ids": [call_id], "messages": [{"role": "system", "content": "Fixture system"}, {"role": "user", "content": BODY}]} for q in queries}
    answer = {"failed_factors": [], "evidence_ids": [call_id], "insufficient_evidence": False}
    records = [{"query_id": q["id"], "raw_text": json.dumps(answer), "answer_origin": "llm", "seconds": 1., "extraction_seconds": 2.} for q in queries]
    documents = {"queries.json": queries, "base_prompts.json": bases,
                 "graph_audit.json": {"nodes": 10, "edges": 3}, "model_audit.json": {"local_sha256": {}},
                 "metrics.json": {"summary": {"test": {}}, "comparison": {}}}
    for name, value in documents.items():
        (directory / name).write_text(json.dumps(value), encoding="utf8")
    (directory / "answers.jsonl").write_text("\n".join(json.dumps(r) for r in records), encoding="utf8")
    manifest = {"dataset_id": DATASET_ID, "usage_scope": "fixture_only", "model_id": "fixture", "model_revision": "fixture",
                "kaggle_run_id": 0, "kaggle_version": 0, "files": {p.name: file_sha(p) for p in directory.iterdir()}}
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf8")


# 功能：在隔离制品和内存身份下验证完整服务边界。
# 逻辑：禁用实验匿名身份，实际 HTTP 路由与 MCP 服务分派共用同一 Store。
# 约束：禁止业务数据库访问；GPU 生成由明确测试桩模拟，不代表真实新推理已验证。
@override_settings(LAB_OPEN_ACCESS=False, LOCAL_DEBUG_AUTO_LOGIN=False, CRMARENA_MODEL_DIR="")
class CRMArenaTests(SimpleTestCase):
    # 功能：创建独立图包及认证测试客户端。
    # 输入：无外部参数；测试框架调用。
    # 输出：directory、store、client、actor、payload、url、loader 状态。
    # 逻辑：临时目录自动清理，替换的仅为制品位置入口。
    # 约束：不修改真实公开包或用户账户。
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        make_bundle(self.directory)
        self.store = CRMArenaStore(self.directory)
        self.actor = User(id=7, username="fixture", is_active=True)
        self.client = APIClient()
        self.client.force_authenticate(self.actor)
        self.payload = {"dataset_id": DATASET_ID, "lead_id": LEAD}
        self.url = "/api/v1/graph/crmarena/"
        self.loader = patch("apps.knowledge_graph.crmarena_views.get_store", return_value=self.store)
        self.loader.start()
        self.addCleanup(self.loader.stop)

    # 功能：验证取证与历史答案有不同执行类型且不写图。
    # 输入：无外部参数；读取夹具图与首题。
    # 输出：精确来源、价格及回放标志必须符合预期。
    # 逻辑：前后摘要相同，原回答保持不变。
    # 约束：不声称回放调用了模型。
    def test_evidence_and_recorded_result(self):
        before = file_sha(self.store.graph)
        evidence = self.client.post(self.url + "evidence/", self.payload, format="json")
        self.assertEqual(evidence.status_code, 200)
        self.assertEqual(evidence.data["execution_type"], "evidence_query")
        self.assertEqual(evidence.data["evidence"]["documents"][0]["text"], BODY)
        self.assertEqual(evidence.data["evidence"]["products"][0]["prices"][0]["attributes"]["UnitPrice"], "80.00")
        saved = self.client.post(self.url + "evaluation/", self.payload, format="json")
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.data["execution_type"], "recorded_evaluation")
        self.assertEqual(saved.data["answer"]["failed_factors"], [])
        self.assertEqual(file_sha(self.store.graph), before)

    # 功能：核验非法类型、命名空间、未知题与正式鉴权。
    # 输入：无外部参数；提交明确非法的请求变体。
    # 输出：400、404、403，且匿名请求不会加载制品。
    # 逻辑：经真实 DRF 权限与 serializer 校验。
    # 约束：强制认证不替代真实 Session 登录测试。
    def test_strict_inputs_auth_and_unknown_lead(self):
        for change in [{"lead_id": 123456789012345}, {"dataset_id": "private"}, {"question": "override"}, {"lead_id": "../../secret"}]:
            self.assertEqual(self.client.post(self.url + "evidence/", {**self.payload, **change}, format="json").status_code, 400)
        unknown = {**self.payload, "lead_id": "00Q999999999999999"}
        self.assertEqual(self.client.post(self.url + "evidence/", unknown, format="json").status_code, 404)
        self.assertEqual(self.client.post(self.url + "predict/", unknown, format="json").status_code, 404)
        with patch("apps.knowledge_graph.crmarena_views.get_store") as loader:
            self.assertEqual(APIClient().get(self.url).status_code, 403)
            loader.assert_not_called()

    # 功能：验证摘要校验及安装器拒绝覆盖。
    # 输入：无外部参数；临时目标复制可信清单后安装夹具。
    # 输出：相同文件重复安装无变化，篡改后安装和加载均失败。
    # 逻辑：保持清单不变，在目标答案文件追加数据。
    # 约束：只破坏一次性测试目录。
    def test_corruption_and_installation(self):
        with tempfile.TemporaryDirectory() as location:
            target = Path(location)
            (target / "manifest.json").write_bytes((self.directory / "manifest.json").read_bytes())
            install(self.directory, target)
            install(self.directory, target)
            (target / "answers.jsonl").write_text("corrupt", encoding="utf8")
            with self.assertRaises(FileExistsError):
                install(self.directory, target)
            with self.assertRaises(CRMArenaUnavailable):
                CRMArenaStore(target)

    # 功能：验证实时未配置和忙状态不回放记录。
    # 输入：无外部参数；独立冷运行器与手动持有的锁。
    # 输出：503 或429，JSON 中不出现 answer。
    # 逻辑：请求仍经过真实预测路由。
    # 约束：不访问本机 GPU。
    def test_live_unavailable_and_busy(self):
        engine = Runtime()
        with patch("apps.knowledge_graph.crmarena_views.runtime", engine):
            response = self.client.post(self.url + "predict/", self.payload, format="json")
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("answer", response.data)
            engine.lock.acquire()
            try:
                self.assertEqual(self.client.post(self.url + "predict/", self.payload, format="json").status_code, 429)
            finally:
                engine.lock.release()

    # 功能：检查预算、来源校验、Decimal 计算和抽取门控。
    # 输入：无外部参数；固定合成通话及可手算金额。
    # 输出：调用预算为512/128，金额80.00，错误引用只调用一次抽取。
    # 逻辑：模型桩仅提供原始文本，真实校验和计算函数运行。
    # 约束：测试不评价模型语言生成质量。
    def test_live_pipeline_and_failure_gate(self):
        facts = {"items": [{"product_id": "PRODUCT", "quantity": 1, "quote": "one unit of Widget Alpha"}],
                 "budget": {"amount": "100", "quote": "Our budget is $100"}, "deadline": {"days": 1, "quote": "within one day"}}
        raw = {"raw_text": json.dumps(facts), "seconds": 1, "input_tokens": 100, "output_tokens": 50}
        generator = Mock(side_effect=[raw, {"raw_text": self.store.answers["0"]["raw_text"], "seconds": 1}])
        result = infer(self.store, LEAD, generator)
        self.assertEqual([call.args[1] for call in generator.call_args_list], [512, 128])
        self.assertEqual(result["execution_type"], "live_inference")
        self.assertEqual(result["calculation"]["gross_subtotal"], "80.00")
        self.assertEqual(result["calculation"]["discount_scenarios"][3]["total"], "68.00")
        facts["items"][0]["quote"] = "fabricated quote"
        generator = Mock(return_value={**raw, "raw_text": json.dumps(facts)})
        rejected = infer(self.store, LEAD, generator)
        self.assertEqual(generator.call_count, 1)
        self.assertEqual(rejected["answer_origin"], "extraction_failure_gate")
        self.assertTrue(rejected["answer"]["insufficient_evidence"])

    # 功能：验证格式损坏和错误引用不能冒充成功。
    # 输入：无外部参数；合法空抽取及两种非法最终回答。
    # 输出：每种回答均抛 CRMArenaOutputError。
    # 逻辑：不给 JSON 修复器或历史回放机会。
    # 约束：空抽取本身允许未知，不推定商品数量。
    def test_invalid_model_output_is_not_repaired(self):
        extract = {"raw_text": '{"items": [], "budget": null, "deadline": null}', "seconds": 1}
        for answer in ["not json", '{"failed_factors": [], "evidence_ids": ["foreign"], "insufficient_evidence": false}']:
            with self.assertRaises(CRMArenaOutputError):
                infer(self.store, LEAD, Mock(side_effect=[extract, {"raw_text": answer, "seconds": 1}]))

    # 功能：验证模型初始化失败会显式锁定失败状态。
    # 输入：无外部参数；模拟一次 CUDA RuntimeError。
    # 输出：两次请求均503但仅尝试加载一次，锁始终释放。
    # 逻辑：必须修复并重启才能重新加载。
    # 约束：不模拟成功 GPU 推理。
    def test_runtime_failure_requires_restart(self):
        engine = Runtime()
        with patch.object(engine, "load", side_effect=RuntimeError("fixture CUDA failure")) as loader:
            for _ in range(2):
                with self.assertRaises(CRMArenaUnavailable):
                    engine.predict(self.store, LEAD)
            self.assertEqual(loader.call_count, 1)
            self.assertTrue(engine.status()["restart_required"])
            self.assertFalse(engine.lock.locked())

    # 功能：验证真实工具目录、调用回执和授权范围。
    # 输入：无外部参数；使用内存凭证且仅授权 evidence。
    # 输出：目录只显示授权工具，evidence完成，evaluation被403拒绝。
    # 逻辑：实际 services.invoke、Schema、dispatch 与 HTTP 服务均参与。
    # 约束：凭证认证用 force_authenticate，实际令牌摘要验证由既有测试覆盖。
    def test_mcp_catalog_call_and_scope(self):
        credential = ToolCredential(owner=self.actor, allowed_tools=["crmarena.evidence"], expires_at=timezone.now() + timedelta(hours=1))
        self.client.force_authenticate(self.actor, credential)
        catalog = self.client.get("/api/v1/agent-tools/catalog/?category=crmarena")
        self.assertEqual(catalog.status_code, 200)
        self.assertEqual([t["name"] for t in catalog.data["tools"]], ["crmarena.evidence"])
        self.assertTrue(catalog.data["tools"][0]["annotations"]["readOnlyHint"])
        call = {"name": "crmarena.evidence", "arguments": self.payload}
        response = self.client.post("/api/v1/agent-tools/call/", call, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["execution_type"], "evidence_query")
        response = self.client.post("/api/v1/agent-tools/call/", {**call, "name": "crmarena.evaluation"}, format="json")
        self.assertEqual(response.status_code, 403)
