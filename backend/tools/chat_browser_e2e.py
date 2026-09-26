"""Responsibility: Verify the end-to-end combination of the real floating web assistant, HTTP, database, and Agent.
Implementation: Django LiveServerTestCase creates an isolated database and session, a Node browser asks real questions, and the original Agent reports over HTTP.
Relationships: browser_chat_live.cjs and tests.integration.test_chat fixtures; model output is mocked only at the invocation boundary.
Directory:
- ChatBrowserTests: Integrated acceptance tests requiring an explicit Playwright environment.
- ChatBrowserTests.test_browser_agent_round_trip: Full workspace path from customer query to citation display.
- ChatBrowserTests.test_general_browser_round_trip: General chat for an account with no customers, collapse/expand, and refresh recovery.
- ChatBrowserTests.run_round_trip: Workspace HTTP integration test for accounts with customer data and empty accounts.
- ChatBrowserTests.run_round_trip.decide: Generate a mocked model decision from real tool evidence.
Variable index:
- None
"""

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from unittest.mock import Mock

from django.contrib.auth import get_user_model
from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from agent.clients.backend_api import DjangoBackendClient
from agent.workflows.chat import process_chat_once
from apps.chat.models import AnswerRequest
from apps.crm.models import AgentCredential
from tests.integration.test_chat import fixture


# Function: Jointly verify the web floating window and real backend.
# Logic: The test static root points at real frontend assets; the workspace with customer data replaces the launch script to mount the shared floating window, while general mode uses the complete home page and floating entry point without intercepting business APIs.
# Constraints: Run separately through manage.py test tools.chat_browser_e2e and require Playwright; the database is not mocked.
@override_settings(
    STATIC_ROOT=Path(__file__).resolve().parents[1] / "frontend" / "assets"
)
class ChatBrowserTests(LiveServerTestCase):
    # Function: Submit a question from the web page and automatically display the real persisted answer and source.
    # Inputs: No external parameters; the environment supplies the browser runtime and fixtures supply synthetic employees and evidence.
    # Outputs: Browser exits successfully, the database is completed, and one citation exists.
    # Logic: Delegates to shared run_round_trip using the workspace with customer data; after the browser writes pending, the original Agent claims and reports it and the page polls to display it.
    # Constraints: Only the model function uses Mock; the test-session cookie passes only through the child-process environment and is neither printed nor written to the repository.
    @override_settings(
        ALLOWED_HOSTS=["localhost", "127.0.0.1", "testserver"],
        LOCAL_DEBUG_AUTO_LOGIN=False,
    )
    def test_browser_agent_round_trip(self):
        self.run_round_trip(general=False)

    # Function: Verify direct chat and refresh recovery for an account without customers.
    # Inputs: No external parameters; runtime supplies the browser path.
    # Outputs: Browser exits successfully and a completed record exists.
    # Logic: Uses the real home-page route and API to create a general session from an empty account; collapse/expand and re-expansion after refresh both restore the saved answer.
    # Constraints: Only model output is mocked; does not create customer fixtures or intercept business APIs.
    @override_settings(
        ALLOWED_HOSTS=["localhost", "127.0.0.1", "testserver"],
        LOCAL_DEBUG_AUTO_LOGIN=False,
    )
    def test_general_browser_round_trip(self):
        self.run_round_trip(general=True)

    # Function: Coordinate browser submission and real Agent consumption.
    # Inputs: `general` selects an empty-customer account or customer-evidence fixtures.
    # Outputs: Assertions for the persisted answer, citation count, and browser result.
    # Logic: After waiting for pending, claims through HTTP, searches customers when needed, reports, and lets the browser observe completion.
    # Constraints: The model uses Mock only and the cookie passes only through the child-process environment without printing.
    def run_round_trip(self, general):
        self.assertTrue(
            os.environ.get("SALESMATE_PLAYWRIGHT_MODULE"),
            "需要显式 Playwright 模块路径。",
        )
        self.assertTrue(
            os.environ.get("SALESMATE_BROWSER_PATH"), "需要显式浏览器路径。"
        )
        if general:
            owner = get_user_model().objects.create_user(
                username="general-browser-owner"
            )
            AgentCredential.objects.create(
                owner=owner,
                name="chat-test",
                digest=hashlib.sha256(b"chat-test-token").hexdigest(),
            )
        else:
            owner, _, _, _ = fixture()
        browser_session = APIClient()
        browser_session.force_login(owner)
        environment = {
            **os.environ,
            "CHAT_TEST_URL": self.live_server_url,
            "CHAT_TEST_SESSION": browser_session.cookies[
                settings.SESSION_COOKIE_NAME
            ].value,
            "CHAT_TEST_COOKIE_NAME": settings.SESSION_COOKIE_NAME,
            "CHAT_TEST_GENERAL": "1" if general else "0",
        }
        process = subprocess.Popen(
            ["node", str(Path(__file__).with_name("browser_chat_live.cjs"))],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
        )
        try:
            deadline = time.monotonic() + 25
            while not AnswerRequest.objects.filter(
                owner=owner, status="pending"
            ).exists():
                if time.monotonic() >= deadline and process.poll() is None:
                    process.terminate()
                if process.poll() is not None or time.monotonic() >= deadline:
                    self.fail(
                        "浏览器未创建待回答请求：" + process.communicate(timeout=5)[0]
                    )
                time.sleep(0.1)
            backend = DjangoBackendClient(
                self.live_server_url + "/api/v1/agent/", "chat-test-token"
            )

    # Function: Mock a workspace-model answer or query decision.
    # Inputs: `messages` are real prompts and `max_tokens` is the original model budget.
    # Outputs: action=tool or action=answer JSON.
    # Logic: Answers directly for empty accounts; with customer data, searches and then cites real registered evidence.
    # Constraints: Mocks the model only; every tool read and answer save uses real HTTP.
            def decide(messages, *, max_tokens):
                payload = json.loads(messages[-1]["content"])
                if general:
                    return json.dumps(
                        {
                            "action": "answer",
                            "assistant_text": "你好，我们可以一起起草邮件。",
                            "citations": [],
                        }
                    )
                if not payload["tool_results"]:
                    return json.dumps(
                        {
                            "action": "tool",
                            "name": "customers.search",
                            "arguments": {"q": "测试客户"},
                        }
                    )
                evidence = next(
                    row
                    for row in payload["authorized_evidence"]
                    if row["source_type"] == "customer_search"
                )
                citation = {
                    key: evidence[key]
                    for key in ("source_id", "source_type", "title_or_label")
                }
                return json.dumps(
                    {
                        "action": "answer",
                        "assistant_text": "找到测试客户。[1]",
                        "citations": [citation],
                    }
                )

            provider = Mock(side_effect=decide)
            result = process_chat_once(backend=backend, chat_provider=provider)
            self.assertEqual(result["status"], "completed", result)
            output, _ = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, output)
            self.assertIn("Live chat browser round trip passed", output)
            self.assertEqual(provider.call_count, 1 if general else 2)
            request = AnswerRequest.objects.get(owner=owner)
            self.assertEqual(request.status, "completed")
            self.assertEqual(request.citations.count(), 0 if general else 1)
            self.assertIsNone(request.company_id)
        finally:
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=10)
