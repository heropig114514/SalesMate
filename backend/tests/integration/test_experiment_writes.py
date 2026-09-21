"""职责：验证跨账号实验维护的事务、权限、幂等与后续清理。
实现：真实 PostgreSQL 夹具和 Session/Tool/Agent HTTP，所有写入限隔离数据库。
关联：experiment_writes、experiments、agent_tools、chat.tool_reads 与 seed_kg_lab。
目录：
- ExperimentWriteTests：维护权限集成验证。
- ExperimentWriteTests.setUp：创建两个账号及完整批次。
- ExperimentWriteTests.call：经统一 HTTP 入口维护。
- ExperimentWriteTests.row：读取精确共享行。
- ExperimentWriteTests.test_crud_replay_conflict_cleanup：跨账号 CRUD、幂等、旧指纹与清理。
- ExperimentWriteTests.test_boundaries_and_references：私有行、归属、跨批次关系与删除引用保护。
- ExperimentWriteTests.test_update_all_allowed_models：逐模型验证合法维护与完整性。
- ExperimentWriteTests.test_tool_scope_and_chat_mutation：冻结凭据及请求绑定 Agent 维护回执。
- ExperimentWriteTests.test_cleanup_after_relation_edit：关联改到后建记录后仍可完整清理。
- ExperimentWriteConcurrencyTests：独立数据库连接的并发写入验证。
- ExperimentWriteConcurrencyTests.test_same_fingerprint_has_one_winner：同一旧指纹只允许一次成功。
- ExperimentWriteConcurrencyTests.test_same_fingerprint_has_one_winner.write：独立连接执行竞争写入。
变量索引：
- 无
"""

import hashlib
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

from django.apps import apps
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from apps.agent_tools.models import ToolCredential
from apps.chat import services
from apps.crm.models import AgentCredential
from apps.crm.access import Conflict
from apps.sales.experiment_writes import WRITE_MODELS, mutate
from apps.sales.experiments import APPROVED_BATCHES, load_batch, table_rows
from apps.sales.management.commands.seed_kg_lab import run_delete, verify_manifest
from apps.sales.models import Conversation, Product
from integrations.salesmate_tools.read_contract import EXPERIMENT_WRITE_TOOLS
from tests.integration.test_experiments import ExperimentTests


# 功能：验证所有登录账号的实验业务维护。
# 逻辑：真实请求覆盖共享写入口和所有可写模型，不运行外部 Worker。
# 约束：隔离数据库与临时附件；不证明线上外部模型规划质量。
class ExperimentWriteTests(TestCase):
    # 功能：建立不同归属的真实关联测试数据。
    # 输入：测试数据库和临时目录。
    # 输出：owner、reader、manifest、private、base、client 实例状态。
    # 逻辑：复用完整 44 表夹具，写账号与归属账号不同。
    # 约束：每例事务回滚，附件由基准构建函数登记清理。
    def setUp(self):
        ExperimentTests.setUp(self)

    # 功能：提交有幂等键的维护请求。
    # 输入：`operation` 操作、`model` 模型、`status` 预期状态、`key` 可选幂等键、`args` 业务参数。
    # 输出：原始响应字典。
    # 逻辑：使用当前客户端认证，失败断言包含响应以便定位。
    # 约束：不绕过 Schema 或处理器，不自动重试。
    def call(self, operation, model, status=200, key=None, **args):
        response = self.client.post("/api/v1/agent-tools/call/", {"name": "experiments." + operation,
            "arguments": {"batch": APPROVED_BATCHES[0], "model": model, **args},
            "idempotency_key": str(key or uuid.uuid4())}, format="json")
        self.assertEqual(response.status_code, status, response.data)
        return response.data

    # 功能：读取目标表中的共享投影。
    # 输入：`model` 模型、`pk` 可选精确主键。
    # 输出：一条带指纹的行。
    # 逻辑：调用实际实验读取接口。
    # 约束：只适用于预先存在的记录。
    def row(self, model, pk=None):
        response = self.client.get(self.base + model + "/", {"pk": pk} if pk else {})
        self.assertEqual(response.status_code, 200, response.data)
        return response.data["results"][0]

    # 功能：验证完整 CRUD、幂等与可清理性。
    # 输入：新账号与另一个账号拥有的批次。
    # 输出：数量恢复、审计三个操作、归属不变、旧指纹拒绝及清理预览通过。
    # 逻辑：新增产品，重放相同幂等键，跨账号读取，修改后拒绝旧版本，再删除。
    # 约束：不删除既有测试场景，只操作新产品。
    def test_crud_replay_conflict_cleanup(self):
        key = uuid.uuid4()
        data = {"sku": "KGSEED-new-product", "name": "共享新增", "currency": "USD", "unit_price": "3.50"}
        first = self.call("create", "sales.Product", key=key, data=data)
        replay = self.call("create", "sales.Product", key=key, data=data)
        self.assertTrue(replay["replayed"])
        row = first["data"]["record"]
        self.assertEqual(row["owner"]["id"], self.owner.pk)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.row("sales.Product", row["pk"])["fields"]["name"], "共享新增")
        self.client.force_authenticate(self.reader)
        updated = self.call("update", "sales.Product", pk=row["pk"], expected=row["fingerprint"], data={"name": "已修改"})["data"]["record"]
        self.call("update", "sales.Product", 409, pk=row["pk"], expected=row["fingerprint"], data={"name": "过期提交"})
        self.call("delete", "sales.Product", pk=row["pk"], expected=updated["fingerprint"])
        self.assertFalse(Product.objects.filter(pk=row["pk"]).exists())
        manifest = load_batch(APPROVED_BATCHES[0]).changes
        self.assertEqual(len(manifest["mutations"]), 3)
        self.assertEqual(manifest["original_rows"], self.manifest["rows"])
        self.assertEqual(verify_manifest(manifest), manifest["table_counts"])
        self.assertEqual(run_delete(self.owner, APPROVED_BATCHES[0], False)["action"], "delete_preview")

    # 功能：验证精确授权与引用保护。
    # 输入：共享客户、私有客户及非业务安全模型。
    # 输出：越界拒绝且清单内容未变化。
    # 逻辑：拒绝私有主键、owner 修改、外键指向私有客户、身份模型和被引用客户删除。
    # 约束：错误不泄露私有正文，不触发级联。
    def test_boundaries_and_references(self):
        company = self.row("crm.Company")
        self.call("update", "crm.Company", 404, pk=str(self.private.pk), expected="0" * 64, data={"name": "越界"})
        self.call("update", "crm.Company", 400, pk=company["pk"], expected=company["fingerprint"], data={"owner_id": self.reader.pk})
        self.call("create", "crm.Contact", 400, data={"company_id": str(self.private.pk), "email": "test@example.invalid"})
        self.call("delete", "crm.Company", 409, pk=company["pk"], expected=company["fingerprint"])
        self.call("create", "accounts.User", 403, data={"username": "forbidden"})
        self.assertEqual(load_batch(APPROVED_BATCHES[0]).changes, self.manifest)
        self.client.force_authenticate(None)
        self.call("create", "sales.Product", 401, data={})

    # 功能：逐一验证所有声明可修改模型实际可保存。
    # 输入：每表首条完整虚构记录。
    # 输出：28 模型均成功，指纹与清单一致且原归属保留。
    # 逻辑：空字段更新验证原始模型约束及版本递增，不使用 mock。
    # 约束：没有修改业务值；只在隔离库推进维护审计和模型版本。
    def test_update_all_allowed_models(self):
        for model in sorted(WRITE_MODELS):
            with self.subTest(model=model):
                row = self.row(model)
                result = self.call("update", model, pk=row["pk"], expected=row["fingerprint"], data={})
                self.assertEqual(result["data"]["record"]["owner"], row["owner"])
        entry = load_batch(APPROVED_BATCHES[0])
        self.assertEqual(verify_manifest(entry.changes), entry.changes["table_counts"])

    # 功能：验证 Tool scope 和内置 Agent 的真实维护链路。
    # 输入：仅读令牌、后续显式写授权及处理中的聊天请求。
    # 输出：旧授权拒绝，授权后成功；Agent 重放只修改一次并保存稳定回执。
    # 逻辑：HTTP Tool 认证与 Agent 认证分别运行，只有模型决策不在本例范围内。
    # 约束：不会将 Tool token 误用为 Agent token，不调用真实模型。
    def test_tool_scope_and_chat_mutation(self):
        credential = ToolCredential.objects.create(owner=self.reader, name="write-test",
            digest=hashlib.sha256(b"write-test-token").hexdigest(), allowed_tools=["experiments.rows"],
            expires_at=timezone.now() + timedelta(hours=1))
        row = self.row("crm.Company")
        self.client.force_authenticate(None)
        self.client.credentials(HTTP_AUTHORIZATION="Tool write-test-token")
        args = {"pk": row["pk"], "expected": row["fingerprint"], "data": {"name": "由工具修改"}}
        self.call("update", "crm.Company", 403, **args)
        credential.allowed_tools = sorted(EXPERIMENT_WRITE_TOOLS)
        credential.save(update_fields=["allowed_tools"])
        row = self.call("update", "crm.Company", **args)["data"]["record"]
        conversation = Conversation.objects.create(owner=self.reader)
        request, _ = services.submit(self.reader, {"conversation_id": str(conversation.pk),
            "content": "请修改 KGSEED 虚构客户名称", "client_key": str(uuid.uuid4())})
        services.claim(self.reader)
        AgentCredential.objects.create(owner=self.reader, name="write-agent", digest=hashlib.sha256(b"write-agent-token").hexdigest())
        self.client.credentials(HTTP_AUTHORIZATION="Agent write-agent-token")
        payload = {"request_id": str(request.pk), "name": "experiments.update", "arguments": {
            "batch": APPROVED_BATCHES[0], "model": "crm.Company", "pk": row["pk"],
            "expected": row["fingerprint"], "data": {"name": "由聊天修改"}}}
        for index in range(2):
            response = self.client.post("/api/v1/agent/chat/tool-reads/", payload, format="json")
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data["replayed"], bool(index))
            self.assertEqual(response.data["evidence_items"][0]["source_type"], "experiment_mutation")
        self.assertEqual(len(load_batch(APPROVED_BATCHES[0]).changes["mutations"]), 2)

    # 功能：验证编辑外键后的批次删除顺序。
    # 输入：原报价明细及后创建的产品。
    # 输出：批次完整删除、清单与附件清理，非批次客户保持存在。
    # 逻辑：将早创建明细改为引用新产品，触发原逆创建顺序无法处理的依赖。
    # 约束：只在隔离数据库执行真实删除，不修改生产清单。
    def test_cleanup_after_relation_edit(self):
        product = self.call("create", "sales.Product", data={"sku": "cleanup-new", "name": "清理测试", "currency": "USD", "unit_price": "1.00"})["data"]["record"]
        line = self.row("sales.QuoteLine")
        self.call("update", "sales.QuoteLine", pk=line["pk"], expected=line["fingerprint"], data={"product_id": product["pk"]})
        result = run_delete(self.owner, APPROVED_BATCHES[0], True)
        self.assertEqual(result["action"], "deleted")
        self.assertTrue(type(self.private).objects.filter(pk=self.private.pk).exists())
        self.assertFalse(Product.objects.filter(pk=product["pk"]).exists())
        for row in self.manifest["rows"]:
            self.assertFalse(apps.get_model(row["model"]).objects.filter(pk=row["pk"]).exists())


# 功能：验证共享记录并发版本边界。
# 逻辑：两个真实连接同时提交同一指纹，检查行锁和提交后清单重读。
# 约束：不 mock 锁或数据库，不访问外部服务。
class ExperimentWriteConcurrencyTests(TransactionTestCase):
    # 功能：保证两个竞争更新不会覆盖彼此。
    # 输入：完整测试批次、两个同时开始的维护请求。
    # 输出：恰好一次成功、一次 409，审计仅追加一次且清单核验通过。
    # 逻辑：Barrier 同步请求开始，服务的所有者及清单锁保证读取最新指纹。
    # 约束：线程各用独立连接，等待最多十秒，失败不自动重试。
    def test_same_fingerprint_has_one_winner(self):
        ExperimentTests.setUp(self)
        row = table_rows(load_batch(APPROVED_BATCHES[0]), "crm.Company")[0]
        barrier = Barrier(2)

        # 功能：提交一次并发更新。
        # 输入：`name` 新客户名称；闭包读取批次、读取者、旧指纹及同步屏障。
        # 输出：200 或 409 状态整数。
        # 逻辑：独立连接中调用真实维护事务，finally 关闭连接。
        # 约束：仅捕获预期版本冲突，未知异常向测试传播。
        def write(name):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                mutate(self.reader, "update", APPROVED_BATCHES[0], "crm.Company", {"name": name}, row["pk"], row["fingerprint"])
                return 200
            except Conflict:
                return 409
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(write, name) for name in ("并发甲", "并发乙")]
            self.assertEqual(sorted(future.result(timeout=10) for future in futures), [200, 409])
        manifest = load_batch(APPROVED_BATCHES[0]).changes
        self.assertEqual(len(manifest["mutations"]), 1)
        self.assertEqual(verify_manifest(manifest), manifest["table_counts"])
