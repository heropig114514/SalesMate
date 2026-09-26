"""Responsibility: Verify explicit scope, versioning, and duplicate protection for news-source maintenance.
Implementation: Use real database and saved audit records while prohibiting models and web crawling throughout.
Relationships: Covers the `maintain_news_sources` command and the Agent's fixed Eurostat URL rules.
Directory:
- NewsSourceMaintenanceTests: Maintenance-contract tests.
- NewsSourceMaintenanceTests.setUp: Prepare two sources.
- NewsSourceMaintenanceTests.run_command: Execute while forbidding external calls.
- NewsSourceMaintenanceTests.test_preview_apply_and_idempotency: Verify preview and explicit idempotent application.
- NewsSourceMaintenanceTests.test_duplicate_preflight_rolls_back_batch: Reject a whole batch with duplicate sources.
- NewsSourceMaintenanceTests.test_revision_guard_even_in_lab: Enforce versioning even in lab mode.
- NewsSourceMaintenanceTests.test_unknown_source_is_rejected: Reject unknown sources.
Variable index:
- None
"""
import io
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.sales.models import AuditEvent, WorldNews


# Function: Ensure source maintenance does not implicitly refresh models or overwrite other news fields.
# Logic: Use real transactions and unique constraints, with assertions that external calls are prohibited.
# Constraints: Do not verify whether real website content remains available.
class NewsSourceMaintenanceTests(TestCase):
    # Function: Create two known migration sources.
    # Inputs: Test-framework isolated database, with no external parameters.
    # Outputs: `owner` and two Agent news records.
    # Logic: The two indicators each correspond to one unique old URL; other business fields have fixed values.
    # Constraints: Synthetic content is not written to production.
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="source-maintenance")
        self.records = [WorldNews.objects.create(owner=self.owner, title="合成新闻", category="industry", industry="制造业", published_at=timezone.now(), source_url=f"https://ec.europa.eu/eurostat/product?code=4-{day}-ap", content="原正文", summary="原摘要", data_source="agent") for day in ["18092026", "16092026"]]

    # Function: Execute the command with fixed versions and confirm no external request occurs.
    # Inputs: `apply` is the write switch and `targets` may replace the ID:revision sequence.
    # Outputs: Parsed JSON.
    # Logic: Use real command-line argument parsing; mocks are limited to prohibiting calls at the boundary.
    # Constraints: Do not relax parsing or version checks.
    def run_command(self, apply=False, targets=None):
        output = io.StringIO()
        with patch("agent.world_insights.fetch_page") as fetch, patch("agent.world_insights.generate_json") as model:
            call_command("maintain_news_sources", *(targets or [f"{row.pk}:{row.revision}" for row in self.records]), apply=apply, stdout=output)
            fetch.assert_not_called()
            model.assert_not_called()
        return json.loads(output.getvalue())

    # Function: Verify preview makes no write, explicit apply changes only source, and reruns are idempotent.
    # Inputs: Two old URLs and explicit apply.
    # Outputs: Two canonical URLs, each revision incremented once, retained content, and no duplicate audit on repeated call.
    # Logic: Actually call `save_record` and `AuditEvent`.
    # Constraints: Reapplication must pass current revision and cannot reuse a stale version.
    def test_preview_apply_and_idempotency(self):
        preview = self.run_command()
        self.assertTrue(all(item["status"] == "would_update" for item in preview["results"]))
        self.assertEqual(AuditEvent.objects.count(), 0)
        self.run_command(apply=True)
        for row in self.records:
            row.refresh_from_db()
            self.assertIn("/en/web/products-euro-indicators/w/", row.source_url)
            self.assertEqual((row.revision, row.content, row.summary), (1, "原正文", "原摘要"))
        self.assertEqual(AuditEvent.objects.count(), 2)
        result = self.run_command(apply=True)
        self.assertTrue(all(item["status"] == "unchanged" for item in result["results"]))
        self.assertEqual(AuditEvent.objects.count(), 2)

    # Function: Ensure an earlier target is not written before a later target with duplicate source is found.
    # Inputs: A duplicate record for the second news item's canonical URL.
    # Outputs: Whole batch is rejected and first item's revision is unchanged.
    # Logic: Apply only after preflighting every target; duplicates across owners are also rejected.
    # Constraints: Do not automatically merge, delete, or archive duplicates.
    def test_duplicate_preflight_rolls_back_batch(self):
        other = get_user_model().objects.create_user(username="other-source-owner")
        WorldNews.objects.create(owner=other, title="重复", category="industry", industry="制造业", published_at=timezone.now(), source_url="https://ec.europa.eu/eurostat/en/web/products-euro-indicators/w/4-16092026-ap", content="另一条", data_source="agent")
        with self.assertRaises(CommandError):
            self.run_command(apply=True)
        self.records[0].refresh_from_db()
        self.assertEqual(self.records[0].revision, 0)
        self.assertEqual(AuditEvent.objects.count(), 0)

    # Function: Ensure operations maintenance cannot overwrite concurrent changes even in lab mode.
    # Inputs: Stale preview revision and newer database version.
    # Outputs: Rejection and the source remains the old URL.
    # Logic: Explicit maintenance version checking does not depend on lab-mode business behavior that skips If-Match.
    # Constraints: Do not change lab-mode rules for ordinary business APIs.
    @override_settings(LAB_OPEN_ACCESS=True)
    def test_revision_guard_even_in_lab(self):
        WorldNews.objects.filter(pk=self.records[0].pk).update(revision=1)
        with self.assertRaises(CommandError):
            self.run_command(apply=True)
        self.records[0].refresh_from_db()
        self.assertIn("product?code=", self.records[0].source_url)

    # Function: Reject unverified migrations and input missing revision.
    # Inputs: A non-Eurostat source and invalid target argument.
    # Outputs: Command fails and writes no audit record.
    # Logic: Stop during parsing or preflight and cannot bypass into arbitrary redirect maintenance.
    # Constraints: Do not access target addresses.
    def test_unknown_source_is_rejected(self):
        WorldNews.objects.filter(pk=self.records[0].pk).update(source_url="https://example.test/article")
        with self.assertRaises(CommandError):
            self.run_command(apply=True)
        with self.assertRaises(CommandError):
            self.run_command(targets=[str(self.records[0].pk)])
        self.assertEqual(AuditEvent.objects.count(), 0)
