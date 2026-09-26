"""Responsibility: Verifies that an ordinary account reads another user's shared experiment data through the tool protocol and web Agent.
Implementation: Creates fixtures in isolated PostgreSQL; uses real authenticated HTTP and Agent workflow, mocking only model decisions.
Relationships: `experiments`, `agent_tools`, and `chat.tool_reads`; MCP stdio is separately connected to this service by SDK tests.
Directory:
- ExperimentToolTests: Cross-account tool and chat end-to-end verification.
- ExperimentToolTests.setUp: Creates temporary files, two accounts, and independent credentials.
- ExperimentToolTests.call: Sends a real Tool-authenticated call.
- ExperimentToolTests.test_all_tables_and_boundaries: Iterates all tables and verifies permissions and integrity rejection.
- ExperimentToolTests.test_file_blocks_and_frozen_grants: Verifies that file chunks and old credentials do not implicitly expand authority.
- ExperimentToolTests.test_chat_agent_http_evidence_round_trip: Completes experiment reads and answer citations through real HTTP.
- ExperimentToolTests.test_chat_agent_http_evidence_round_trip.decide: Returns deterministic model decisions based on tool results.
- ExperimentToolTests.test_chat_agent_write_http_round_trip: Modifies a shared record through the real workflow and saves a receipt citation.
- ExperimentToolTests.test_chat_agent_write_http_round_trip.decide: Chooses maintenance from the read fingerprint and cites the receipt.
- ExperimentToolTests.test_real_mcp_stdio: Reads all experiment tables through a real MCP SDK subprocess.
- ExperimentToolTests.test_real_mcp_stdio.check: Checks protocol directory, invocations, and error receipts.
Variable index:
- None
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


# Function: Verifies boundaries of a shared batch under two real identity protocols.
# Logic: `LiveServerTestCase` lets the production HTTP client connect to real Django routes and the test database.
# Constraints: All accounts, tokens, files, and model outputs are isolated test artifacts and do not access the real business database.
@override_settings(ALLOWED_HOSTS=["localhost", "127.0.0.1", "testserver"], LOCAL_DEBUG_AUTO_LOGIN=False)
class ExperimentToolTests(LiveServerTestCase):
    # Function: Creates an ordinary reader and a different batch owner.
    # Inputs: Test database, temporary service URL, and file directory.
    # Outputs: `owner`, `reader`, `manifest`, `credential`, `client`, and `batch` instance state.
    # Logic: Generates two complete fixture sets; Tool and Agent use separate test tokens and authentication protocols.
    # Constraints: Rolls back the database and removes temporary files after the test; does not start a background Worker.
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

    # Function: Executes one authenticated tool call.
    # Inputs: `name` is the tool name, `arguments` are JSON parameters, and `status` is the expected HTTP status.
    # Outputs: Raw JSON response.
    # Logic: Uses real authentication and schema validation without bypassing the permission service.
    # Constraints: Does not retry automatically; error states remain available for assertions.
    def call(self, name, arguments, status=200):
        response = self.client.post("/api/v1/agent-tools/call/", {"name": name, "arguments": arguments}, format="json")
        self.assertEqual(response.status_code, status, response.data)
        return response.data

    # Function: Verifies all model-readable data is available while unlisted data and unauthorized operations are not readable.
    # Inputs: Two-account fixtures, a private decoy record, and the raw manifest.
    # Outputs: All 120 fixtures across 44 tables are readable; maintenance flags match `WRITE_MODELS`, fields are redacted, original ownership remains, and out-of-scope requests are rejected.
    # Logic: Reads every table and checks original counts; modifies one row to confirm 409, then revokes the manifest to confirm 404.
    # Constraints: Intentionally modifies and deletes only in the test database; reads must not alter fixture fingerprints.
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

    # Function: Verifies cross-account file contents and frozen tool authorization.
    # Inputs: Shared documents and attachments with issued restricted tool credentials.
    # Outputs: Text chunks, ownership, and offsets are traceable; old authorization and anonymous requests are rejected.
    # Logic: Reads both file kinds, then reduces the test token to the old tool and verifies the new tool does not automatically gain access.
    # Constraints: Does not print tokens or grant write/confirmation tools; original file content remains unchanged.
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

    # Function: Verifies another account's built-in Agent reads experiment data through real HTTP and saves citations.
    # Inputs: An ordinary reader's general conversation and deterministic model decisions.
    # Outputs: All three experiment tools succeed; answer and citations belong to the reader while source text retains the original batch ownership.
    # Logic: Actually claims a request, discovers eight tools, reads the directory, queries tables, reads files, reports an answer, then verifies persisted sources.
    # Constraints: The model boundary is mocked and does not prove real-model planning quality; all other transport, authorization, and evidence registration are real.
    def test_chat_agent_http_evidence_round_trip(self):
        conversation = Conversation.objects.create(owner=self.reader)
        request, _ = services.submit(self.reader, {"conversation_id": str(conversation.pk),
            "client_key": str(uuid.uuid4()), "content": "读取 KGSEED 实验附件并说明原归属。"})
        backend = DjangoBackendClient(self.live_server_url + "/api/v1/agent/", "experiment-agent-test")
        self.addCleanup(backend.close)

        # Function: Chooses the next step or cited answer from real tool results.
        # Inputs: `messages` is workflow-provided prompting and `max_tokens` is the unchanged model budget.
        # Outputs: A standard JSON tool decision or final answer with real source identifiers.
        # Logic: After confirming eight tool candidates, reads the directory, attachment table, and text, then cites the actually displayed file-chunk evidence.
        # Constraints: Does not construct fabricated sources; the mock applies only to the language-model boundary.
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

    # Function: Verifies that workspace model decisions drive real experiment modifications and save answers.
    # Inputs: A newly authenticated account, real HTTP backend, and test model-decision function.
    # Outputs: Customer name changes, receipt is cited, and original ownership remains unchanged.
    # Logic: Runs the complete four-step workflow of directory, exact row, modification, and answer.
    # Constraints: Replaces only the LLM decision and does not mock authorization, database, or HTTP.
    def test_chat_agent_write_http_round_trip(self):
        conversation = Conversation.objects.create(owner=self.reader)
        request, _ = services.submit(self.reader, {"conversation_id": str(conversation.pk),
            "client_key": str(uuid.uuid4()), "content": "请修改 KGSEED 实验客户名称为算法组修改。"})
        backend = DjangoBackendClient(self.live_server_url + "/api/v1/agent/", "experiment-agent-test")
        self.addCleanup(backend.close)
        company = self.manifest["truth"][0]["company_id"]

        # Function: Builds the next step from the real tool directory and read fingerprint.
        # Inputs: `messages` is workflow context and `max_tokens` is the unchanged model budget.
        # Outputs: Tool invocation or answer JSON citing the actual maintenance receipt.
        # Logic: Stops after three tool operations and does not fabricate a fingerprint from an old manifest.
        # Constraints: This function replaces model planning, not any business execution.
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

    # Function: Verifies that an MCP host can use another ordinary account to read all shared experiment tables and maintain business records.
    # Inputs: LiveServer address, test-only token, and optionally installed MCP SDK.
    # Outputs: Asserts six published tools, 120 rows across 44 tables, real CRUD, and `is_error` for an invalid table.
    # Logic: Starts a real stdio bridge subprocess; after SDK handshake, reads every table through real HTTP and PostgreSQL.
    # Constraints: Requires `integrations/salesmate_tools/requirements.txt`; explicitly skips when absent and does not misreport that the protocol was verified.
    @skipUnless(importlib.util.find_spec("mcp"), "需要单独安装固定版本 MCP SDK 才能验证 stdio")
    def test_real_mcp_stdio(self):
        from mcp import Client, StdioServerParameters

        self.credential.allowed_tools = sorted(EXPERIMENT_TOOLS | EXPERIMENT_WRITE_TOOLS)
        self.credential.save(update_fields=["allowed_tools"])

        params = StdioServerParameters(command=sys.executable,
            args=["-m", "integrations.salesmate_tools.mcp_server"], cwd=str(Path(__file__).resolve().parents[3]),
            env={**os.environ, "SALESMATE_TOOLS_URL": self.live_server_url,
                 "SALESMATE_TOOLS_TOKEN": "experiment-tool-test"})

        # Function: Completes real protocol-discovery and data-read assertions.
        # Inputs: No explicit parameters; reads outer params, self.manifest, and the live test service.
        # Outputs: No return value; assertion failure signals unexpected results.
        # Logic: The SDK manages stdio, checks counts per table, then creates, modifies, and deletes one shared customer before checking invalid-table rejection.
        # Constraints: Does not mock HTTP, permission, or protocol; closes the SDK subprocess after the test.
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
