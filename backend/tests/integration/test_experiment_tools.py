"""职责：验证普通账号通过工具协议和网页 Agent 读取他人共享实验数据。
实现：隔离 PostgreSQL 真实生成夹具；使用真实认证 HTTP 和 Agent 工作流，仅模拟模型决策。
关联：experiments、agent_tools、chat.tool_reads；MCP stdio 另由独立 SDK 测试连接本服务。
目录：
- ExperimentToolTests：跨账号工具与聊天端到端验证。
- ExperimentToolTests.setUp：建立临时文件、两个账号和独立凭据。
- ExperimentToolTests.call：发送真实 Tool 认证调用。
- ExperimentToolTests.test_all_tables_and_boundaries：遍历全部表并验证权限及完整性拒绝。
- ExperimentToolTests.test_file_blocks_and_frozen_grants：验证文件块和旧凭据不隐式扩权。
- ExperimentToolTests.test_chat_agent_http_evidence_round_trip：经真实 HTTP 完成实验读取与回答引用。
- ExperimentToolTests.test_chat_agent_http_evidence_round_trip.decide：按工具结果返回确定性模型决策。
- ExperimentToolTests.test_chat_agent_write_http_round_trip：经真实工作流修改共享记录并保存回执引用。
- ExperimentToolTests.test_chat_agent_write_http_round_trip.decide：根据读取指纹选择维护并引用回执。
- ExperimentToolTests.test_real_mcp_stdio：通过真实 MCP SDK 子进程读取全部实验表。
- ExperimentToolTests.test_real_mcp_stdio.check：核对协议目录、调用与错误回执。
变量索引：
- 无
"""

import asyncio
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from agent.clients.backend_api import DjangoBackendClient
from agent.workflows.chat import process_chat_once
from apps.agent_tools.models import ToolCredential
from apps.agent_tools.presets import permission_presets
from apps.agent_tools.registry import build_registry
from apps.chat import services
from apps.crm.models import AgentCredential, Company
from apps.sales.experiment_writes import WRITE_MODELS
from apps.sales.experiments import APPROVED_BATCHES, TABLES
from apps.sales.management.commands.seed_kg_lab import run_seed, verify_manifest
from apps.sales.models import AuditEvent, Conversation
from integrations.salesmate_tools.read_contract import EXPERIMENT_TOOLS, EXPERIMENT_WRITE_TOOLS, WORKSPACE_TOOLS


# 功能：验证共享批次在两个真实身份协议下的边界。
# 逻辑：LiveServerTestCase 让生产 HTTP 客户端连接真实 Django 路由和测试数据库。
# 约束：全部账号、令牌、文件和模型输出均为隔离测试用途，不访问真实业务库。
@override_settings(ALLOWED_HOSTS=["localhost", "127.0.0.1", "testserver"], LOCAL_DEBUG_AUTO_LOGIN=False)
class ExperimentToolTests(LiveServerTestCase):
    # 功能：建立普通读取者和不同的批次拥有者。
    # 输入：测试数据库、临时服务地址和文件目录。
    # 输出：owner、reader、manifest、credential、client、batch 实例状态。
    # 逻辑：生成两组完整夹具；Tool 与 Agent 使用不同测试令牌及认证协议。
    # 约束：测试结束回滚数据库并清理临时文件；不启动后台 Worker。
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="experiment-tool-test-")
        self.addCleanup(folder.cleanup)
        context = override_settings(BASE_DIR=Path(folder.name))
        context.enable()
        self.addCleanup(context.disable)
        self.owner = get_user_model().objects.create_user(username="seed-owner")
        self.reader = get_user_model().objects.create_user(username="algorithm-reader")
        self.batch = APPROVED_BATCHES[0]
        self.manifest = run_seed(self.owner, self.batch, 2)
        self.private = Company.objects.create(owner=self.owner, name=self.batch + " private", group_key="private")
        self.credential = ToolCredential.objects.create(owner=self.reader, name="experiment-test",
            digest=hashlib.sha256(b"experiment-tool-test").hexdigest(), allowed_tools=sorted(EXPERIMENT_TOOLS),
            expires_at=timezone.now() + timedelta(hours=1))
        AgentCredential.objects.create(owner=self.reader, name="experiment-agent-test",
            digest=hashlib.sha256(b"experiment-agent-test").hexdigest())
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION="Tool experiment-tool-test")

    # 功能：执行一次认证工具调用。
    # 输入：`name` 工具名称、`arguments` JSON 参数、`status` 预期 HTTP 状态。
    # 输出：原始 JSON 响应。
    # 逻辑：使用真实认证和 Schema 验证，不绕过权限服务。
    # 约束：不自动重试，错误状态保留供测试断言。
    def call(self, name, arguments, status=200):
        response = self.client.post("/api/v1/agent-tools/call/", {"name": name, "arguments": arguments}, format="json")
        self.assertEqual(response.status_code, status, response.data)
        return response.data

    # 功能：验证全部模型可读而清单外数据及未授权操作不可读。
    # 输入：两账号夹具、私有伪装记录和原始清单。
    # 输出：44 表共 120 条夹具可读、维护标记符合 WRITE_MODELS、字段脱敏、原归属保留，越界请求拒绝。
    # 逻辑：逐表读取并核对原始计数；修改一行后确认 409，撤销清单后确认 404。
    # 约束：仅在测试库故意修改和删除；读取不得修改夹具指纹。
    def test_all_tables_and_boundaries(self):
        catalog = self.client.get("/api/v1/agent-tools/catalog/", {"category": "experiments"})
        self.assertEqual({item["name"] for item in catalog.data["tools"]}, EXPERIMENT_TOOLS)
        summary = self.call("experiments.catalog", {})["data"]["batches"][0]
        self.assertEqual((len(summary["tables"]), summary["total"]), (44, 120))
        for label in TABLES:
            data = self.call("experiments.rows", {"batch": self.batch, "model": label})["data"]
            self.assertEqual(data["count"], self.manifest["table_counts"][label])
            self.assertTrue(all(row["synthetic"] and row["read_only"] == (label not in WRITE_MODELS) for row in data["results"]))
            if label == "accounts.User":
                self.assertNotIn("password", data["results"][0]["fields"])
            if label == "crm.Company":
                self.assertEqual({row["owner"]["id"] for row in data["results"]}, {self.owner.pk})
        args = {"batch": self.batch, "model": "crm.Company", "pk": str(self.private.pk)}
        self.assertEqual(self.call("experiments.rows", args)["data"]["count"], 0)
        self.call("experiments.rows", {**args, "batch": "KGSEED_unapproved"}, 404)
        self.call("experiments.rows", {**args, "model": "crm.AgentCredential"}, 404)
        self.call("experiments.rows", {**args, "owner_id": self.owner.pk}, 400)
        self.call("customers.create", {"name": "forbidden"}, 403)
        self.assertEqual(verify_manifest(self.manifest), self.manifest["table_counts"])
        company = self.manifest["truth"][0]["company_id"]
        Company.objects.filter(pk=company).update(name="drifted fixture")
        self.call("experiments.rows", {"batch": self.batch, "model": "crm.Company"}, 409)
        AuditEvent.objects.filter(event="kg_synthetic_batch_v1", object_id=self.batch).delete()
        self.call("experiments.rows", args, 404)

    # 功能：验证跨账号文件内容与冻结工具授权。
    # 输入：共享文档和附件，已签发的限定工具凭据。
    # 输出：文本块、归属、偏移可追踪；旧授权和匿名请求被拒绝。
    # 逻辑：读取两类文件，并缩减测试令牌到旧工具验证新工具不能自动取得权限。
    # 约束：不打印令牌，不授予写入或确认工具；原文件内容未变。
    def test_file_blocks_and_frozen_grants(self):
        for label in ("sales.Attachment", "accounts.SetupDocument"):
            pk = next(row["pk"] for row in self.manifest["rows"] if row["model"] == label)
            args = {"batch": self.batch, "model": label, "pk": pk, "format": "text", "offset": 0, "limit": 100}
            data = self.call("experiments.file_read", args)["data"]
            self.assertTrue(data["synthetic"])
            self.assertTrue(data["content"])
            self.assertLessEqual(len(data["content"]), 100)
            self.assertNotEqual(data["owner"]["id"], self.reader.pk)
            self.call("experiments.file_read", {**args, "pk": str(uuid.uuid4())}, 404)
        self.assertTrue(EXPERIMENT_TOOLS <= set(permission_presets(build_registry())["read_only"]["allowed_tools"]))
        self.credential.allowed_tools = ["customers.search"]
        self.credential.save(update_fields=["allowed_tools"])
        self.call("experiments.catalog", {}, 403)
        self.client.credentials()
        self.call("experiments.catalog", {}, 401)

    # 功能：验证其他账号的内置 Agent 经真实 HTTP 读取实验数据并保存引用。
    # 输入：普通读取者的通用会话和确定性模型决策。
    # 输出：三个实验工具均成功；回答和引用归属读取者，来源正文保留原批次归属。
    # 逻辑：实际领取请求、发现八工具、查目录、查表、读文件、回报回答，再核对持久化来源。
    # 约束：模型边界模拟，不证明真实模型的规划质量；其余传输、授权和证据登记均真实。
    def test_chat_agent_http_evidence_round_trip(self):
        conversation = Conversation.objects.create(owner=self.reader)
        request, _ = services.submit(self.reader, {"conversation_id": str(conversation.pk),
            "client_key": str(uuid.uuid4()), "content": "读取 KGSEED 实验附件并说明原归属。"})
        backend = DjangoBackendClient(self.live_server_url + "/api/v1/agent/", "experiment-agent-test")
        self.addCleanup(backend.close)

        # 功能：根据真实工具结果选择下一步或引用回答。
        # 输入：`messages` 是工作流提供的提示，`max_tokens` 是未改变的模型预算。
        # 输出：标准 JSON 工具决策或带真实来源标识的最终回答。
        # 逻辑：确认八工具候选后查目录、附件表及文本，最后引用实际展示的文件块证据。
        # 约束：不构造伪造来源；模拟仅作用于语言模型边界。
        def decide(messages, *, max_tokens):
            payload = json.loads(messages[-1]["content"])
            count = len(payload["tool_results"])
            self.assertEqual({item["name"] for item in payload["available_tools"]}, WORKSPACE_TOOLS)
            if count == 0:
                decision = {"action": "tool", "name": "experiments.catalog", "arguments": {}}
            elif count == 1:
                batch = payload["tool_results"][0]["data"]["batches"][0]["batch"]
                decision = {"action": "tool", "name": "experiments.rows", "arguments": {"batch": batch, "model": "sales.Attachment"}}
            elif count == 2:
                pk = payload["tool_results"][1]["data"]["results"][0]["pk"]
                decision = {"action": "tool", "name": "experiments.file_read", "arguments": {
                    "batch": self.batch, "model": "sales.Attachment", "pk": pk, "format": "text", "offset": 0, "limit": 1000}}
            else:
                evidence = next(item for item in payload["authorized_evidence"] if item["source_type"] == "experiment_file")
                self.assertIn(self.owner.username, evidence["content"])
                decision = {"action": "answer", "assistant_text": "已读取他人归属的虚构实验附件。[1]",
                    "citations": [{key: evidence[key] for key in ("source_id", "source_type", "title_or_label")}]}
            return json.dumps(decision, ensure_ascii=False)

        result = process_chat_once(backend=backend, chat_provider=decide)
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(request.tool_reads.count(), 3)
        request.refresh_from_db()
        self.assertEqual(request.assistant_message.owner_id, self.reader.pk)
        self.assertEqual(request.citations.get().source_type, "experiment_file")
        self.assertEqual(verify_manifest(self.manifest), self.manifest["table_counts"])

    # 功能：验证工作空间模型决策能驱动真实实验修改并保存回答。
    # 输入：已认证的新账号、真实 HTTP 后端和测试模型决策函数。
    # 输出：客户名称变更、回执被引用且原归属保持不变。
    # 逻辑：按目录、精确行、修改、回答的四步运行完整工作流。
    # 约束：仅替换 LLM 决策，不模拟授权、数据库或 HTTP。
    def test_chat_agent_write_http_round_trip(self):
        conversation = Conversation.objects.create(owner=self.reader)
        request, _ = services.submit(self.reader, {"conversation_id": str(conversation.pk),
            "client_key": str(uuid.uuid4()), "content": "请修改 KGSEED 实验客户名称为算法组修改。"})
        backend = DjangoBackendClient(self.live_server_url + "/api/v1/agent/", "experiment-agent-test")
        self.addCleanup(backend.close)
        company = self.manifest["truth"][0]["company_id"]

        # 功能：使用真实工具目录与读取指纹构造下一步。
        # 输入：`messages` 工作流上下文、`max_tokens` 未改变的模型预算。
        # 输出：工具调用或引用实际维护回执的回答 JSON。
        # 逻辑：三次工具操作后停止，不从旧清单伪造指纹。
        # 约束：此函数替代模型规划，不替代任何业务执行。
        def decide(messages, *, max_tokens):
            payload = json.loads(messages[-1]["content"])
            results = payload["tool_results"]
            if not results:
                decision = {"action": "tool", "name": "experiments.catalog", "arguments": {}}
            elif len(results) == 1:
                decision = {"action": "tool", "name": "experiments.rows", "arguments": {"batch": self.batch, "model": "crm.Company", "pk": company}}
            elif len(results) == 2:
                row = results[-1]["data"]["results"][0]
                decision = {"action": "tool", "name": "experiments.update", "arguments": {"batch": self.batch, "model": "crm.Company",
                    "pk": row["pk"], "expected": row["fingerprint"], "data": {"name": "算法组修改"}}}
            else:
                evidence = next(item for item in payload["authorized_evidence"] if item["source_type"] == "experiment_mutation")
                decision = {"action": "answer", "assistant_text": "共享虚构客户已修改。[1]",
                    "citations": [{key: evidence[key] for key in ("source_id", "source_type", "title_or_label")}]}
            return json.dumps(decision, ensure_ascii=False)

        result = process_chat_once(backend=backend, chat_provider=decide)
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(Company.objects.get(pk=company).name, "算法组修改")
        self.assertEqual(Company.objects.get(pk=company).owner_id, self.owner.pk)
        self.assertEqual(request.citations.get().source_type, "experiment_mutation")

    # 功能：验证 MCP 宿主能以另一个普通账号读取全部共享实验表并维护业务记录。
    # 输入：LiveServer 地址、测试专用令牌和可选安装的 MCP SDK。
    # 输出：六个工具发布、44 表总计 120 条、真实 CRUD 与非法表 is_error 的断言。
    # 逻辑：启动真实 stdio bridge 子进程，SDK 握手后经真实 HTTP 和 PostgreSQL 逐表读取。
    # 约束：需安装 integrations/salesmate_tools/requirements.txt；未安装显式跳过，不误报协议已验证。
    @skipUnless(importlib.util.find_spec("mcp"), "需要单独安装固定版本 MCP SDK 才能验证 stdio")
    def test_real_mcp_stdio(self):
        from mcp import Client, StdioServerParameters

        self.credential.allowed_tools = sorted(EXPERIMENT_TOOLS | EXPERIMENT_WRITE_TOOLS)
        self.credential.save(update_fields=["allowed_tools"])

        params = StdioServerParameters(command=sys.executable,
            args=["-m", "integrations.salesmate_tools.mcp_server"], cwd=str(Path(__file__).resolve().parents[3]),
            env={**os.environ, "SALESMATE_TOOLS_URL": self.live_server_url,
                 "SALESMATE_TOOLS_TOKEN": "experiment-tool-test"})

        # 功能：完成真实协议发现与数据读取断言。
        # 输入：无显式参数，读取外层 params、self.manifest 和实时测试服务。
        # 输出：无返回值；不符预期时断言失败。
        # 逻辑：SDK 管理 stdio，逐表核对数量后创建、修改、删除一条共享客户，最后检查非法表拒绝。
        # 约束：不模拟 HTTP、权限或协议；测试结束关闭 SDK 子进程。
        async def check():
            async with Client(params) as client:
                catalog = await client.list_tools()
                self.assertEqual({item.name for item in catalog.tools}, EXPERIMENT_TOOLS | EXPERIMENT_WRITE_TOOLS)
                self.assertIsNone(catalog.next_cursor)
                summary = await client.call_tool("experiments.catalog", {})
                self.assertFalse(summary.is_error)
                self.assertEqual(summary.structured_content["data"]["batches"][0]["total"], 120)
                total = 0
                for label in TABLES:
                    result = await client.call_tool("experiments.rows", {"batch": self.batch, "model": label})
                    self.assertFalse(result.is_error)
                    data = result.structured_content["data"]
                    self.assertEqual(data["count"], self.manifest["table_counts"][label])
                    total += data["count"]
                self.assertEqual(total, 120)
                created = await client.call_tool("experiments.create", {"batch": self.batch, "model": "crm.Company",
                    "data": {"group_key": "manual:mcp-shared", "name": "MCP 新增虚构"}, "idempotency_key": str(uuid.uuid4())})
                self.assertFalse(created.is_error, created)
                row = created.structured_content["data"]["record"]
                updated = await client.call_tool("experiments.update", {"batch": self.batch, "model": "crm.Company",
                    "pk": row["pk"], "expected": row["fingerprint"], "data": {"name": "MCP 修改虚构"}, "idempotency_key": str(uuid.uuid4())})
                self.assertFalse(updated.is_error, updated)
                row = updated.structured_content["data"]["record"]
                deleted = await client.call_tool("experiments.delete", {"batch": self.batch, "model": "crm.Company",
                    "pk": row["pk"], "expected": row["fingerprint"], "idempotency_key": str(uuid.uuid4())})
                self.assertFalse(deleted.is_error, deleted)
                denied = await client.call_tool("experiments.rows", {"batch": self.batch, "model": "crm.AgentCredential"})
                self.assertTrue(denied.is_error)

        asyncio.run(check())
