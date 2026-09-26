"""Responsibility: Verify refresh scope, failure semantics, and version protection for selected legacy news signals.
Implementation: Use a real database and command-line management service, placing mocked Agent output at the model boundary; do not read external resources.
Relationships: Covers the `refresh_world_news_signals` management command; explicit targets undergo real field and version checks.
Directory:
- NewsRefreshTests: Explicit refresh-management tests.
- NewsRefreshTests.setUp: Create an Agent-source record and bounded reading payload.
- NewsRefreshTests.run_refresh: Invoke the real management command with mocked source and model boundaries.
- NewsRefreshTests.test_preview_then_apply_preserves_article: Preview makes no write; apply changes signal fields only.
- NewsRefreshTests.test_empty_signal_and_idempotent_refresh: Empty signal preserves null and identical payload refresh is idempotent.
- NewsRefreshTests.test_failure_preserves_record: Agent failure leaves the legacy record unchanged.
- NewsRefreshTests.test_target_preflight: Reject invalid or duplicate targets before external calls.
- NewsRefreshTests.test_concurrent_revision_is_not_overwritten: Concurrent edits cannot be overwritten by stale refresh.
- NewsRefreshTests.test_explicit_preview_import: Explicit preview must match source and applies only the selected record.
- NewsRefreshTests.test_preview_mismatch_and_duplicates: Reject preview lacking target, with counterfeit source, or duplicate match.
Variable index:
- None
"""

import io
import json
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from agent.world_insights import InsightError
from apps.crm.access import Conflict
from apps.sales.management.commands.refresh_world_news_signals import SIGNAL_FIELDS, refresh_one
from apps.sales.models import WorldNews


# Function: Verify explicit refresh of old news does not become full-database recollection or private-opportunity linking.
# Logic: Disable lab mode and use real persistence and audit paths; source and model are mocked boundaries.
# Constraints: Passing tests does not prove model extraction or external sources are genuinely available.
@override_settings(LAB_OPEN_ACCESS=False, WORKSPACE_OWNER_ONLY=False, LOCAL_DEBUG_AUTO_LOGIN=False)
class NewsRefreshTests(TestCase):
    # Function: Prepare the historical record to refresh.
    # Inputs: No external parameters.
    # Outputs: `record` and payload conforming to the collaboration contract.
    # Logic: Legacy record signals are empty, while article text and version have explicit initial values.
    # Constraints: Amount and evidence are test samples and are not written to a running database.
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="news-refresh")
        self.record = WorldNews.objects.create(owner=self.owner, title="历史新闻", category="industry", industry="制造业", published_at=timezone.now(), source_url="https://example.org/refresh", content="旧正文", summary="旧摘要不是原文证据", data_source="agent")
        self.payload = {field: None if field == "amount" else "" for field in SIGNAL_FIELDS}
        self.payload.update(company_name="示例公司", signal_type="new_factory", evidence="示例公司投资 CNY 100 元建厂。", amount="100", currency="CNY", amount_type="total_investment", amount_scope="whole_project", amount_evidence="投资 CNY 100 元")

    # Function: Invoke the command under explicit mocked boundaries.
    # Inputs: `apply` controls whether --apply is passed; `payload` can replace model output.
    # Outputs: Command JSON and model mock for verifying evidence source.
    # Logic: Execute real arguments, target validation, and database transaction while mocking fetch_page and summarize_news.
    # Constraints: load_environment does not read local runtime configuration and external requests are not sent.
    def run_refresh(self, apply=False, payload=None):
        output = io.StringIO()
        with patch("agent.world_insights.load_environment"), patch("agent.world_insights.fetch_page", return_value=("来源原文", None, [])) as fetch, patch("agent.world_insights.summarize_news", return_value=self.payload if payload is None else payload) as model:
            call_command("refresh_world_news_signals", str(self.record.pk), apply=apply, stdout=output)
            fetch.assert_called_once_with(self.record.source_url)
        return json.loads(output.getvalue()), model

    # Function: Verify preview has no side effect and apply writes back public signals only.
    # Inputs: Explicit preview and apply calls for the same legacy news item.
    # Outputs: Preview leaves revision unchanged; apply increments revision once while article, summary, time, and source remain unchanged.
    # Logic: Verify Agent candidate does not use historical generated summary as evidence.
    # Constraints: The two calls are independent test paths, not a production implicit retry strategy.
    def test_preview_then_apply_preserves_article(self):
        report, model = self.run_refresh()
        self.assertEqual(report["results"][0]["status"], "would_update")
        self.assertEqual(model.call_args.args[0].excerpt, "")
        self.record.refresh_from_db()
        self.assertEqual((self.record.revision, self.record.company_name), (0, ""))
        original = (self.record.content, self.record.summary, self.record.source_url, self.record.published_at)
        report, _ = self.run_refresh(apply=True)
        self.record.refresh_from_db()
        self.assertEqual(report["results"][0]["status"], "updated")
        self.assertEqual((self.record.revision, self.record.company_name, str(self.record.amount)), (1, "示例公司", "100.000000"))
        self.assertEqual((self.record.content, self.record.summary, self.record.source_url, self.record.published_at), original)

    # Function: Verify unknown amount and identical payload do not create data changes.
    # Inputs: Empty signal and a subsequent identical valid signal.
    # Outputs: Empty signal keeps amount null; identical data does not increment revision a second time.
    # Logic: Determine changes from database-validated Decimal and field values.
    # Constraints: Do not infer amount from information missing in news.
    def test_empty_signal_and_idempotent_refresh(self):
        empty = {field: None if field == "amount" else "" for field in SIGNAL_FIELDS}
        report, _ = self.run_refresh(apply=True, payload=empty)
        self.assertEqual(report["results"][0]["status"], "unchanged")
        self.record.refresh_from_db()
        self.assertIsNone(self.record.amount)
        self.assertEqual(self.record.revision, 0)
        self.run_refresh(apply=True)
        report, _ = self.run_refresh(apply=True)
        self.assertEqual(report["results"][0]["status"], "unchanged")
        self.record.refresh_from_db()
        self.assertEqual(self.record.revision, 1)

    # Function: Verify extraction error does not erase historical content.
    # Inputs: Source has been read but Agent raises explicit contract error.
    # Outputs: Nonzero command error and per-item failure result; record unchanged.
    # Logic: Model is called once only; failure has no automatic retry or summary fallback.
    # Constraints: Exception is a test mock and cannot infer real model failure cause.
    def test_failure_preserves_record(self):
        output = io.StringIO()
        with patch("agent.world_insights.load_environment"), patch("agent.world_insights.fetch_page", return_value=("原文", None, [])), patch("agent.world_insights.summarize_news", side_effect=InsightError("资讯国家缺少可核对的文本依据。")) as model:
            with self.assertRaises(CommandError):
                call_command("refresh_world_news_signals", str(self.record.pk), apply=True, stdout=output)
            model.assert_called_once()
        self.assertEqual(json.loads(output.getvalue())["failed"], 1)
        self.record.refresh_from_db()
        self.assertEqual((self.record.revision, self.record.company_name), (0, ""))

    # Function: Verify target scope is fixed before any external call.
    # Inputs: Missing UUID, duplicate ID, non-Agent, and archived records.
    # Outputs: All reject and fetch_page is not called.
    # Logic: Preflight whole set and do not execute early for partially valid targets.
    # Constraints: Do not expand into whole table or fuzzy name matching.
    def test_target_preflight(self):
        with patch("agent.world_insights.fetch_page") as fetch:
            for identifiers in ((str(uuid.uuid4()),), (str(self.record.pk), str(self.record.pk))):
                with self.assertRaises(CommandError):
                    call_command("refresh_world_news_signals", *identifiers)
            for fields in ({"data_source": "manual"}, {"data_source": "agent", "archived": True}):
                WorldNews.objects.filter(pk=self.record.pk).update(**fields)
                with self.assertRaises(CommandError):
                    call_command("refresh_world_news_signals", str(self.record.pk))
            fetch.assert_not_called()

    # Function: Verify modifications during external calls are not overwritten by stale snapshot.
    # Inputs: Stale revision snapshot and newer database version.
    # Outputs: Conflict while retaining concurrent edit content.
    # Logic: Call real save_record version and transaction checks.
    # Constraints: Do not automatically refetch version and retry after conflict.
    def test_concurrent_revision_is_not_overwritten(self):
        WorldNews.objects.filter(pk=self.record.pk).update(revision=1, company_name="其他编辑")
        with patch("agent.world_insights.fetch_page", return_value=("原文", None, [])), patch("agent.world_insights.summarize_news", return_value=self.payload):
            with self.assertRaises(Conflict):
                refresh_one(self.record, True)
        self.record.refresh_from_db()
        self.assertEqual((self.record.revision, self.record.company_name), (1, "其他编辑"))

    # Function: Verify explicit preview file can reuse public result of Agent original collection flow.
    # Inputs: One payload with exact source match and extra preview for an unauthorized target.
    # Outputs: Only specified legacy news updates; no network, environment loading, or model call.
    # Logic: Preview file is mocked boundary; import undergoes actual field and version validation.
    # Constraints: Do not create new news for unknown preview records or modify original title or article.
    def test_explicit_preview_import(self):
        payload = {**self.payload, "source_url": self.record.source_url, "data_source": "agent", "content": "不能覆盖原正文"}
        document = {"preview": [{"tool": "world_news.create", "data": payload}, {"tool": "world_news.create", "data": {**payload, "source_url": "https://example.org/unselected"}}]}
        output = io.StringIO()
        with patch("pathlib.Path.read_text", return_value=json.dumps(document)), patch("agent.world_insights.fetch_page") as fetch, patch("agent.world_insights.summarize_news") as model, patch("agent.world_insights.load_environment") as environment:
            call_command("refresh_world_news_signals", str(self.record.pk), agent_preview="preview.json", apply=True, stdout=output)
            fetch.assert_not_called()
            model.assert_not_called()
            environment.assert_not_called()
        self.record.refresh_from_db()
        self.assertEqual((self.record.company_name, self.record.content, self.record.revision), ("示例公司", "旧正文", 1))
        self.assertEqual(WorldNews.objects.count(), 1)
        self.assertEqual(json.loads(output.getvalue())["results"][0]["status"], "updated")

    # Function: Reject preview with missing, ambiguous, or counterfeit source.
    # Inputs: JSON with no match, duplicate match, or non-Agent source marker.
    # Outputs: Command fails, original record and version unchanged, and no network request.
    # Logic: Must explicitly match one source_url and cannot guess by title or list position.
    # Constraints: Do not fall back to network recollection on import failure.
    def test_preview_mismatch_and_duplicates(self):
        item = {"tool": "world_news.create", "data": {**self.payload, "source_url": self.record.source_url, "data_source": "agent"}}
        invalid_source = {"tool": "world_news.create", "data": {**item["data"], "data_source": "manual"}}
        for rows in ([], [item, item], [invalid_source]):
            with self.subTest(rows=rows), patch("pathlib.Path.read_text", return_value=json.dumps({"preview": rows})), patch("agent.world_insights.fetch_page") as fetch:
                with self.assertRaises(CommandError):
                    call_command("refresh_world_news_signals", str(self.record.pk), agent_preview="preview.json", apply=True, stdout=io.StringIO())
                fetch.assert_not_called()
        self.record.refresh_from_db()
        self.assertEqual((self.record.revision, self.record.company_name), (0, ""))
