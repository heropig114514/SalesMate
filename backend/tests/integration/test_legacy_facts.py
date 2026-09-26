"""Responsibility: Verify that upgraded legacy rule data still enters the current analysis pipeline.
Implementation: Construct legacy extractions in an isolated PostgreSQL test database, run the migration function, and verify retained audit history, idempotency, and latest-version boundaries.
Relationships: Covers migration 0004, `selectors` mailbox projection, and rules L2/L3/L4; does not call Gmail or a model.
Directory:
- LegacyFactTests: Verify historical-format upgrades and current-business integration.
- LegacyFactTests.setUp: Establish legacy facts and an email missing mailbox transport fields.
- LegacyFactTests.test_upgrade_preserves_history_and_analysis: Verify that complete analysis runs after appending a conversion version.
- LegacyFactTests.test_newer_extraction_is_not_replaced: Verify that an existing newer extraction is not overwritten by legacy-format migration.
Variable index:
- migration: Migration 0004 module; tests and Django migrate use the same conversion function.
"""
from copy import deepcopy
from importlib import import_module
from types import SimpleNamespace

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import TestCase

from apps.crm import ingestion, jobs, rules, selectors
from apps.crm.models import Analysis, Company, Email, Extraction, Mailbox

migration = import_module("apps.crm.migrations.0004_legacy_fact_groups")


# Function: Verify historical-format upgrades and current-business integration.
# Logic: Store legacy JSON in the real test database and roll back every test transaction independently.
# Constraints: Construct synthetic rule records only and do not evaluate model quality.
class LegacyFactTests(TestCase):
    # Function: Establish legacy facts and an email missing mailbox transport fields.
    # Inputs: Test-framework instance state, with no external parameters.
    # Outputs: `user`, `mailbox`, `email`, `old`, `original_facts`, `original_payload`, and `initial_revision`.
    # Logic: Establish relationships through the production ingestion service, then exactly restore pre-migration single-value JSON and legacy email body.
    # Constraints: Legacy construction writes only in tests; production uses the frozen historical migration function.
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="migration-user")
        self.mailbox = Mailbox.objects.create(owner=self.user, address="sales@internal.example")
        payload = rules.extract_email(self.mailbox, "buyer@client.example", "采购", "需求：设备\n预算：20 万")
        ingestion.submit_emails(self.user, [payload])
        self.email = Email.objects.get()
        self.old = self.email.extractions.get()
        facts = deepcopy(self.old.facts)
        facts["intent_evidence"] = facts.pop("intent_evidences")[0]
        for field in migration.FACT_FIELDS:
            groups = facts[field]
            facts[field] = {"value": groups[0]["value"], "evidence": groups[0]["evidences"][0]} if groups else {"value": None, "evidence": None}
        self.old.prompt_version, self.old.facts = "rules-extract-v1", facts
        self.old.save(update_fields=["prompt_version", "facts"])
        self.email.payload.pop("mailbox_address")
        self.email.save(update_fields=["payload"])
        self.original_facts = deepcopy(facts)
        self.original_payload = deepcopy(self.email.payload)
        self.initial_revision = self.email.company.revision

    # Function: Verify that complete analysis can run after appending a conversion version.
    # Inputs: A successful legacy extraction and real database connection in a mocked schema editor.
    # Outputs: Assertions for intact audit history, one version increment, no change on repeated invocation, and successful rule analysis.
    # Logic: After migration, use real selector, Job, and result services to cover the complete path that previously returned browser 500.
    # Constraints: The schema editor uses `connection.alias` only; database, facts, and analysis results are not mocked.
    def test_upgrade_preserves_history_and_analysis(self):
        migration.migrate_facts(apps, SimpleNamespace(connection=connection))
        self.old.refresh_from_db()
        self.email.refresh_from_db()
        self.assertEqual(self.old.facts, self.original_facts)
        self.assertEqual(self.email.payload, self.original_payload)
        company = Company.objects.get()
        self.assertEqual(company.revision, self.initial_revision + 1)
        current = selectors.email_data(self.email)
        self.assertEqual(current["mailbox_address"], self.mailbox.address)
        self.assertEqual(current["facts"]["budget"], [{"value": "20 万", "evidences": ["预算：20 万"]}])
        self.assertEqual(current["facts"]["quantity"], [])
        self.assertEqual(current["extract_prompt_version"], migration.CONVERTED_VERSION)
        migration.migrate_facts(apps, SimpleNamespace(connection=connection))
        self.assertEqual(Extraction.objects.count(), 2)
        company.refresh_from_db()
        self.assertEqual(company.revision, self.initial_revision + 1)
        with transaction.atomic():
            jobs.enqueue(company, "manual_request")
        rules.run_company(self.user, company.pk)
        self.assertEqual(Analysis.objects.count(), 1)
        self.assertTrue(Analysis.objects.get().scores.exists())

    # Function: Verify that an existing newer extraction is not overwritten by legacy-format migration.
    # Inputs: The same email has a successful new-protocol version after its legacy extraction.
    # Outputs: Assertions that extraction count, current version, and revision remain unchanged.
    # Logic: Migration must identify current version by monotonically increasing ID and must not force overwrite by version name or legacy-record matching.
    # Constraints: New facts in the test come from deterministic rules and no external model is called.
    def test_newer_extraction_is_not_replaced(self):
        payload = rules.extract_email(self.mailbox, "buyer@client.example", "采购", "需求：设备\n预算：30 万")
        newer = Extraction.objects.create(email=self.email, prompt_version="extract-v7", status="completed", facts=payload["facts"])
        migration.migrate_facts(apps, SimpleNamespace(connection=connection))
        self.assertEqual(Extraction.objects.count(), 2)
        self.assertEqual(selectors.latest_extraction(self.email).pk, newer.pk)
        self.assertEqual(Company.objects.get().revision, self.initial_revision)
