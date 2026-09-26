"""Responsibility: Verify authorization, source idempotency, and transaction boundaries shared by graph HTTP tools and MCP.
Implementation: Use real isolated PostgreSQL and Tool authentication; replace only model generation with synthetic responses.
Relationships: Covers `agent_tools.graph/services` and `knowledge_graph`; MCP transport has separate SDK protocol tests.
Directory:
- GraphToolTests: Graph-tool database integration tests.
- GraphToolTests.setUp: Create restricted credential and two independent identities.
- GraphToolTests.call: Call through real Tool HTTP entry point.
- GraphToolTests.test_source_idempotence_and_retraction: Source replay, conflict, retraction, and query.
- GraphToolTests.test_model_outside_transaction: Confirm no database transaction at model boundary.
- GraphToolTests.test_model_outside_transaction.generate: Return synthetic candidate with original-text evidence.
- GraphToolTests.test_authorization_and_owner_scope: Reject unauthorized and quality-independent permission escapes.
- GraphToolTests.test_invalid_inputs_and_failed_model: Contract rejection and no database write on failure.
Variable index:
- None
"""
import hashlib
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from apps.agent_tools.models import ToolCredential, ToolCall
from apps.agent_tools.registry import build_registry
from apps.knowledge_graph.models import Episode
from apps.knowledge_graph.sync import sync_owner


# Function: Verify graph-tool permissions and replayable input.
# Logic: TransactionTestCase permits checks of real transaction boundaries; all data is synthetic.
# Constraints: Model mocks verify interface integration only and cannot prove actual weight-extraction quality.
@override_settings(LAB_OPEN_ACCESS=False, LOCAL_DEBUG_AUTO_LOGIN=False)
class GraphToolTests(TransactionTestCase):
    # Function: Prepare identities and dedicated credential.
    # Inputs: Isolated test database and migrated graph capture.
    # Outputs: Instance state for `owner`, `other`, `client`, `credential`, and `payload`.
    # Logic: Credential contains graph tools only; synchronize empty graph to verify identity-scoped reads.
    # Constraints: Do not read business database or external model.
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="graph-tools")
        self.other = get_user_model().objects.create_user(username="graph-other")
        self.credential = ToolCredential.objects.create(owner=self.owner, name="graph-test",
            digest=hashlib.sha256(b"synthetic-graph-token").hexdigest(),
            allowed_tools=[name for name in build_registry() if name.startswith("graph.")],
            expires_at=timezone.now() + timedelta(hours=1))
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION="Tool synthetic-graph-token")
        self.payload = {"source_key": "partial", "observed_at": timezone.now().isoformat(),
                        "records": [{"key": "c", "schema": "crm.company", "fields": {"name": "Synthetic Acme"}}]}
        sync_owner(self.owner.pk)
        sync_owner(self.other.pk)

    # Function: Call authenticated HTTP Tool entry point.
    # Inputs: `name` is tool name, `args` are arguments, and `status` is expected HTTP status with default 200.
    # Outputs: Response JSON.
    # Logic: Use fixed route and check real status and error envelope.
    # Constraints: Do not bypass authentication or directly call internal dispatch.
    def call(self, name, args, status=200):
        response = self.client.post("/api/v1/agent-tools/call/", {"name": name, "arguments": args}, format="json")
        self.assertEqual(response.status_code, status, response.data)
        return response.data

    # Function: Verify structured graph building, duplicate sources, and retraction.
    # Inputs: Partial-field observations and same source key.
    # Outputs: Assertions for unique source, current graph, conflict, and repeated retraction.
    # Logic: Use complete HTTP flow, confirming source writes do not create outer ToolCall and can trace fact lineage.
    # Constraints: Retain missing business fields and do not create CRM orders or contacts.
    def test_source_idempotence_and_retraction(self):
        first = self.call("graph.ingest", self.payload)["data"]
        self.assertTrue(first["sync"]["current"])
        self.assertEqual(self.call("graph.ingest", self.payload)["data"]["id"], first["id"])
        self.assertEqual(Episode.objects.count(), 1)
        self.assertEqual(ToolCall.objects.count(), 0)
        self.call("graph.ingest", {**self.payload, "records": [{"key": "c", "schema": "crm.company", "fields": {"name": "Different"}}]}, 409)
        self.assertEqual(len(self.call("graph.schema", {})["data"]["schemas"]), 48)
        self.assertTrue(self.call("graph.status", {})["data"]["current"])
        self.assertTrue(self.call("graph.entities", {})["data"]["results"])
        facts = self.call("graph.facts", {})["data"]["results"]
        self.call("graph.lineage", {"fact_id": facts[0]["id"]})
        self.call("graph.episodes", {})
        self.call("graph.episode", {"episode_id": first["id"]})
        for _ in range(2):
            self.assertTrue(self.call("graph.retract", {"episode_id": first["id"]})["data"]["retracted"])

    # Function: Verify natural-language inference occurs outside database transaction.
    # Inputs: Simple text and mocked model callback.
    # Outputs: Assertions for transaction state and saved source.
    # Logic: Callback actually checks connection state; empty facts serve only transaction-contract verification.
    # Constraints: Do not interpret mock output as model accuracy.
    def test_model_outside_transaction(self):
        # Function: Provide deterministic model boundary and verify transaction.
        # Inputs: `prompt` is the message actually constructed by service.
        # Outputs: Valid entity candidate and mocked audit.
        # Logic: Assert no transaction at call time; entity is supported by input source text.
        # Constraints: Do not perform real inference.
        def generate(prompt):
            self.assertFalse(connection.in_atomic_block)
            self.assertTrue(prompt)
            return {"entities": [], "facts": []}, {"test_only": True}
        with patch("apps.knowledge_graph.episodes.generate", side_effect=generate):
            self.call("graph.ingest", {"source_key": "text", "observed_at": self.payload["observed_at"], "text": "No business facts."})
        self.assertEqual(Episode.objects.count(), 1)

    # Function: Verify credential scope, another user's sources, and owner injection.
    # Inputs: External identity source and narrowed authorization.
    # Outputs: Assertions for 404, 400, and 403.
    # Logic: Create own source first, then query from another identity; registry and execution both obey allowlist constraints.
    # Constraints: Unauthorized and nonexistent both return the same 404.
    def test_authorization_and_owner_scope(self):
        episode = self.call("graph.ingest", self.payload)["data"]["id"]
        self.credential.owner = self.other
        self.credential.save(update_fields=["owner"])
        self.call("graph.episode", {"episode_id": episode}, 404)
        self.call("graph.retract", {"episode_id": episode}, 404)
        self.call("graph.ingest", {**self.payload, "owner": self.owner.pk}, 400)
        self.credential.allowed_tools = ["graph.schema"]
        self.credential.save(update_fields=["allowed_tools"])
        self.call("graph.ingest", self.payload, 403)

    # Function: Verify invalid envelopes and generation failure do not pollute the observation layer.
    # Inputs: One-of conflict, transport idempotency UUID, and model exception.
    # Outputs: Assertions for 400/502 and zero sources.
    # Logic: Validate contract first, then mock failure at one model boundary.
    # Constraints: Do not retry, repair, or silently fall back.
    def test_invalid_inputs_and_failed_model(self):
        self.call("graph.ingest", {**self.payload, "text": "extra"}, 400)
        response = self.client.post("/api/v1/agent-tools/call/", {"name": "graph.ingest", "arguments": self.payload,
            "idempotency_key": "00000000-0000-4000-8000-000000000001"}, format="json")
        self.assertEqual(response.status_code, 400)
        with patch("apps.knowledge_graph.episodes.generate", side_effect=RuntimeError("synthetic failure")) as generate:
            self.call("graph.ingest", {"source_key": "fail", "observed_at": self.payload["observed_at"], "text": "Sample"}, 502)
            self.assertEqual(generate.call_count, 1)
        self.assertFalse(Episode.objects.exists())
