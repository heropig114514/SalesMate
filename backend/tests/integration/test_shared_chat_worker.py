"""Responsibility: Regression-verify that new workers without fixed service tokens can receive chat answers.
Implementation: Use real database, HTTP authentication, and shared command; use mocked output only at the model boundary.
Relationships: Covers `chat_worker`, `crm.dispatch.scoped_backend`, and Agent `process_chat_once`.
Directory:
- SharedChatWorkerTests: Multi-worker chat-scheduling acceptance tests.
- SharedChatWorkerTests.setUp: Create two workers without configured service credentials and requests awaiting answers.
- SharedChatWorkerTests.test_round_robin_and_inactive: Round-robin discovery and inactive-user isolation.
- SharedChatWorkerTests.test_command_answers_both_owners: Real command handles both workers and revokes credentials.
- SharedChatWorkerTests.test_command_answers_both_owners.execute: Provide a synthetic answer at the model boundary.
- SharedChatWorkerTests.test_identity_cannot_access_other_request: Temporary identity cannot read another worker's request.
Variable index:
- None
"""

import json
import os
import uuid
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import LiveServerTestCase, override_settings

from agent.clients.backend_api import BackendRequestError
from agent.workflows.chat import process_chat_once
from apps.chat import services
from apps.chat.management.commands.chat_worker import next_owner
from apps.crm.dispatch import scoped_backend
from apps.crm.models import AgentCredential
from apps.sales.models import Conversation


# Function: Verify chat scheduling covers every active worker while retaining permission boundaries.
# Logic: Two zero-customer workers without service credentials are handled through one iteration of the same resident command.
# Constraints: Connect only to local test HTTP; model mocks do not prove real-model acceptance.
@override_settings(ALLOWED_HOSTS=["localhost", "127.0.0.1", "testserver"])
class SharedChatWorkerTests(LiveServerTestCase):
    # Function: Reproduce a new-user task omitted by the former fixed-worker Worker.
    # Inputs: No external parameters; isolated database and LiveServer URL.
    # Outputs: `owners`, `requests`, and automatically restored connection environment.
    # Logic: Each account submits ping without a permanent AgentCredential; environment intentionally contains an invalid old identity.
    # Constraints: Do not read real credentials or claim requests in advance.
    def setUp(self):
        self.owners = [
            get_user_model().objects.create_user(username=f"shared-chat-{i}")
            for i in range(2)
        ]
        self.requests = []
        for owner in self.owners:
            conversation = Conversation.objects.create(owner=owner)
            request, _ = services.submit(
                owner,
                {
                    "conversation_id": str(conversation.pk),
                    "client_key": str(uuid.uuid4()),
                    "content": "ping",
                },
            )
            self.requests.append(request)
        environment = patch.dict(
            os.environ,
            {
                "SALESMATE_BACKEND_AGENT_URL": self.live_server_url + "/api/v1/agent/",
                "SALESMATE_AGENT_SERVICE_TOKEN": "unused-old-user-token",
                "SALESMATE_MAILBOX_ID": "unused-old-mailbox",
                "NO_PROXY": "localhost,127.0.0.1",
            },
        )
        environment.start()
        self.addCleanup(environment.stop)

    # Function: Verify round-robin fairness, inactive exclusion, and exclusion of non-pending requests.
    # Inputs: Two pending requests and worker-state changes.
    # Outputs: Select the two workers in sequence and wrap at the end; inactive and processing requests are not scheduled.
    # Logic: Discovery reads the queue only and needs no preconfigured token.
    # Constraints: Do not automatically reset processing or trigger the model.
    def test_round_robin_and_inactive(self):
        first, second = self.owners
        self.assertEqual(next_owner().pk, first.pk)
        self.assertEqual(next_owner(first.pk).pk, second.pk)
        self.assertEqual(next_owner(second.pk).pk, first.pk)
        first.is_active = False
        first.save(update_fields=["is_active"])
        self.assertEqual(next_owner().pk, second.pk)
        services.claim(second)
        self.assertIsNone(next_owner())

    # Function: Verify the real command handles one question for each different worker.
    # Inputs: Two pending requests, mocked model, and real HTTP.
    # Outputs: Two completed requests with correct answers, no temporary credential remains, and old environment identity is unchanged.
    # Logic: Model returns workspace `action=answer`; two `--once` calls each handle one task, and a third empty-queue call does not call the model.
    # Constraints: Claiming, context, saving, and authentication outside the model use real implementations.
    def test_command_answers_both_owners(self):
        provider = Mock(
            return_value=json.dumps({"action": "answer", "assistant_text": "pong", "citations": []})
        )

        # Function: Replace only the model call to retain test determinism.
        # Inputs: `backend` is an independent worker client created by the command.
        # Outputs: The original workflow report result.
        # Logic: Claiming and reporting access the real test service; only provider uses a Mock.
        # Constraints: Do not bypass authentication or mock persistence.
        def execute(backend):
            return process_chat_once(backend=backend, chat_provider=provider)

        with patch(
            "apps.chat.management.commands.chat_worker.process_chat_once",
            side_effect=execute,
        ):
            for _ in range(3):
                call_command("chat_worker", once=True)
        self.assertEqual(provider.call_count, 2)
        for request in self.requests:
            request.refresh_from_db()
            self.assertEqual(request.status, "completed")
            self.assertEqual(request.assistant_message.content, "pong")
        self.assertFalse(AgentCredential.objects.exists())
        self.assertEqual(
            os.environ["SALESMATE_AGENT_SERVICE_TOKEN"], "unused-old-user-token"
        )
        self.assertEqual(os.environ["SALESMATE_MAILBOX_ID"], "unused-old-mailbox")

    # Function: Verify that a shared process does not grant access to cross-user requests.
    # Inputs: First worker's temporary client and second worker's request ID.
    # Outputs: Claims only the owner's request, cross-user context returns 404, and credential is revoked after leaving context.
    # Logic: Real AgentAuthentication and `request_for` jointly verify isolation.
    # Constraints: Do not call the model; the other request remains pending.
    def test_identity_cannot_access_other_request(self):
        with scoped_backend(self.owners[0]) as backend:
            claimed = backend.claim_answer_request()
            self.assertEqual(claimed["request_id"], str(self.requests[0].pk))
            with self.assertRaises(BackendRequestError) as error:
                backend.get_answer_context(str(self.requests[1].pk), "internal")
            self.assertEqual(error.exception.status_code, 404)
        self.assertFalse(AgentCredential.objects.exists())
        self.requests[1].refresh_from_db()
        self.assertEqual(self.requests[1].status, "pending")
