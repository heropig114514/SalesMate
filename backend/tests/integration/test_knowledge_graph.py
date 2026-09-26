"""Responsibility: Verifies real PostgreSQL graph capture, version lineage, revocation, and API isolation.
Implementation: The isolated test database executes real triggers and transactions; mail is synthetic source text and no LLM is called.
Relationships: `knowledge_graph`; `TransactionTestCase` permits verification of real commit order and REPEATABLE READ.
Directory:
- GraphTests: Graph integration tests.
- GraphTests.setUp: Creates isolated business records.
- GraphTests.order: Creates synthetic orders and lines.
- GraphTests.email: Creates business email and extraction with verifiable evidence.
- GraphTests.active: Queries current supported specified relations.
- GraphTests.test_capture_bulk_sql_and_rollback: Verifies capture coverage and transaction rollback.
- GraphTests.test_purchase_has_independent_supports: Verifies cancelling one order does not revoke support from another.
- GraphTests.test_versions_idempotence_and_archive_restore: Verifies versions, idempotency, and archive restoration.
- GraphTests.test_latest_failed_extraction_and_review_withdraw: Verifies no fallback to an old extraction.
- GraphTests.test_conflicting_candidates_are_not_overwritten: Verifies different budget candidates remain under review.
- GraphTests.test_failure_rolls_back_and_requires_explicit_recovery: Verifies failure atomicity and explicit recovery.
- GraphTests.test_delete_and_parent_cascade: Verifies deletion and mailbox cascading.
- GraphTests.test_owner_transfer_and_api_isolation: Verifies ownership transfer, old-user revocation, and API isolation.
- GraphTests.test_stale_api_and_lineage_contract: Verifies freshness and evidence APIs.
- GraphTests.test_new_commit_during_projection_stays_pending: Verifies concurrent new events are not incorrectly acknowledged.
- GraphTests.test_new_commit_during_projection_stays_pending.change_during_build: Creates a mid-run commit from an independent connection.
- GraphTests.update_price_in_thread: Commits a price change in an independent thread.
- GraphTests.test_truncate_capture_and_missing_trigger: Verifies truncate capture and missing-trigger detection.
- GraphTests.test_same_owner_lock_prevents_double_writer: Verifies same-user concurrent locks.
- GraphTests.test_account_reset_removes_graph_and_keeps_other_owner: Verifies account purge synchronously deletes graph history.
- GraphTests.test_opportunity_attributes_keep_field_evidence: Verifies opportunity attribute evidence does not mix status fields.
Variable index:
- None
"""

from concurrent.futures import ThreadPoolExecutor
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.db import connection, connections, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.crm.models import Company, Email, Extraction, Mailbox
from apps.accounts.reset import reset_account
from apps.accounts.reset_locks import account_lock
from apps.sales.models import CompanySettings, Opportunity, OrderLine, Product, SalesOrder
from apps.knowledge_graph.models import Change, Derivation, Entity, Fact, ProjectionState, SourceVersion
from apps.knowledge_graph.projection import Projector
from apps.knowledge_graph.sync import pending_owners, request_sync, require_capture, sync_owner


# Function: Verifies graph invariants in isolated PostgreSQL.
# Logic: Each test has independent data with real triggers and SQL; API identity uses forced authentication to isolate session behavior.
# Constraints: Does not establish real LLM, real business quality, or production sessions.
@override_settings(LAB_OPEN_ACCESS=False, LOCAL_DEBUG_AUTO_LOGIN=False)
class GraphTests(TransactionTestCase):
    # Function: Creates independent test identities and base catalog.
    # Inputs: No external parameters; invoked by the test framework.
    # Outputs: Initializes `owner`, `other`, `company`, `product`, and `client`.
    # Logic: Source ORM writes must trigger real events; non-PostgreSQL fails explicitly.
    # Constraints: Test database only; does not write local business data.
    def setUp(self):
        self.assertEqual(connection.vendor, "postgresql", "Run graph tests against PostgreSQL; no SQLite substitution.")
        require_capture()
        self.owner = get_user_model().objects.create_user(username="graph-owner")
        self.other = get_user_model().objects.create_user(username="graph-other")
        self.company = Company.objects.create(owner=self.owner, group_key="graph.example", name="Graph customer")
        self.product = Product.objects.create(owner=self.owner, sku="P1", name="Device", currency="SGD", unit_price="100.00")
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    # Function: Creates a synthetic order and line.
    # Inputs: `number` is the unique order number; `status` defaults to confirmed.
    # Outputs: SalesOrder instance.
    # Logic: One product line with quantity 2, captured by a real trigger.
    # Constraints: The test constructs data directly and does not claim to test order-state transition services.
    def order(self, number, status="confirmed"):
        order = SalesOrder.objects.create(owner=self.owner, company=self.company, number=number, currency="SGD", status=status)
        OrderLine.objects.create(owner=self.owner, order=order, product=self.product, description="Device", quantity="2", unit_price="100.00")
        return order

    # Function: Builds synthetic inbound email with locatable evidence.
    # Inputs: `key` is the message key; `budget` is the source-text budget string.
    # Outputs: `Email` and `Extraction` instance pair.
    # Logic: Source text and extraction share the same evidence string and do not pass through an external model.
    # Constraints: Used to verify projection and lineage, not extraction quality.
    def email(self, key, budget="100 SGD"):
        mailbox, _ = Mailbox.objects.get_or_create(owner=self.owner, address="graph@internal.example")
        body = f"Budget: {budget}"
        email = Email.objects.create(dedupe_key=key, mailbox=mailbox, company=self.company, payload={"subject": "Request", "body_text": body}, sent_at=timezone.now(), received_at=timezone.now(), direction="inbound")
        extraction = Extraction.objects.create(email=email, prompt_version="synthetic-test-v1", status="completed", facts={"budget": [{"value": budget, "evidences": [body]}]})
        return email, extraction

    # Function: Obtains current relationships for the owner that remain supported.
    # Inputs: `predicate` is the relationship type.
    # Outputs: `Fact` queryset.
    # Logic: Excludes `unsupported` and retains facts needing review.
    # Constraints: Test helper, not a production permission interface.
    def active(self, predicate):
        return Fact.objects.filter(owner=self.owner, predicate=predicate).exclude(status="unsupported")

    # Function: Checks opportunity-attribute evidence matches actual input fields.
    # Inputs: No external parameters; an opportunity with amount and an unmatched product name.
    # Outputs: Amount, currency, and name field evidence are accurate; no unverified product relation is generated.
    # Logic: Checks the explicitly registered field set in derived evidence.
    # Constraints: Product name remains the original attribute and is not automatically merged because it resembles the catalog.
    def test_opportunity_attributes_keep_field_evidence(self):
        Opportunity.objects.create(owner=self.owner, company=self.company, title="Expansion", currency="SGD", amount="120", product_names=["Unknown device"])
        sync_owner(self.owner.pk)
        for predicate, expected in (("opportunity_amount", "amount,currency,archived"), ("requested_product_names", "product_names,archived")):
            fact = self.active(predicate).get()
            evidence = fact.supports.get(derivation__active=True).derivation.evidence
            self.assertIn(expected, [item["field"] for item in evidence])
        self.assertFalse(self.active("needs_product").exists())

    # Function: Verifies bulk ORM, direct SQL, and rollback capture.
    # Inputs: No external parameters; uses the base product.
    # Outputs: Asserts event count, ownership, and rollback invariants.
    # Logic: Runs `bulk_create`, SQL UPDATE, and an explicit rollback transaction separately.
    # Constraints: Does not simulate database triggers with Django signals.
    def test_capture_bulk_sql_and_rollback(self):
        initial = Change.objects.count()
        Product.objects.bulk_create([Product(owner=self.owner, sku="P2", name="Other", currency="SGD", unit_price="2")])
        self.assertEqual(Change.objects.count(), initial + 1)
        with connection.cursor() as cursor:
            cursor.execute("UPDATE sales_product SET name = %s WHERE id = %s", ["Updated", self.product.pk])
        self.assertEqual(Change.objects.count(), initial + 2)
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                Product.objects.filter(pk=self.product.pk).update(name="Rolled back")
                raise RuntimeError("rollback fixture")
        self.product.refresh_from_db()
        self.assertEqual(self.product.name, "Updated")
        self.assertEqual(Change.objects.count(), initial + 2)

    # Function: Verifies multi-source support is revoked independently per order.
    # Inputs: No external parameters; creates two confirmed orders and one draft.
    # Outputs: Only one purchase relation, initially with two valid support paths.
    # Logic: Cancels each order; the purchase fact becomes invalid only after the final support disappears.
    # Constraints: Drafts and email cannot create purchase facts.
    def test_purchase_has_independent_supports(self):
        first, second = self.order("O1"), self.order("O2")
        self.order("draft", "draft")
        sync_owner(self.owner.pk)
        fact = self.active("purchased").get()
        self.assertEqual(fact.supports.filter(derivation__active=True).count(), 2)
        SalesOrder.objects.filter(pk=first.pk).update(status="cancelled")
        sync_owner(self.owner.pk)
        self.assertEqual(self.active("purchased").get().pk, fact.pk)
        self.assertEqual(fact.supports.filter(derivation__active=True).count(), 1)
        SalesOrder.objects.filter(pk=second.pk).update(status="cancelled")
        sync_owner(self.owner.pk)
        fact.refresh_from_db()
        self.assertEqual(fact.status, "unsupported")

    # Function: Verifies content-version retention, duplicate builds, and archive restoration.
    # Inputs: No external parameters; product-price and customer-archive changes.
    # Outputs: Old snapshots remain, duplicate builds add no version, and support can recover after archive revocation.
    # Logic: New price from the same source creates a new fact; restoration uses the matching new source version.
    # Constraints: Does not overwrite old `SourceVersion.snapshot`.
    def test_versions_idempotence_and_archive_restore(self):
        self.order("O1")
        sync_owner(self.owner.pk)
        count = SourceVersion.objects.count()
        request_sync(self.owner.pk)
        sync_owner(self.owner.pk)
        self.assertEqual(SourceVersion.objects.count(), count)
        old = SourceVersion.objects.get(kind="sales.product", current=True)
        Product.objects.filter(pk=self.product.pk).update(unit_price="120")
        sync_owner(self.owner.pk)
        old.refresh_from_db()
        self.assertFalse(old.current)
        self.assertEqual(old.snapshot["unit_price"], "100.00")
        setting = CompanySettings.objects.create(owner=self.owner, company=self.company, archived=True)
        sync_owner(self.owner.pk)
        self.assertFalse(self.active("purchased").exists())
        CompanySettings.objects.filter(pk=setting.pk).update(archived=False)
        sync_owner(self.owner.pk)
        self.assertTrue(self.active("purchased").exists())

    # Function: Verifies failed new extraction and non-business review revoke old facts.
    # Inputs: No external parameters; one email with budget evidence.
    # Outputs: No old-fact fallback when latest extraction fails; repair restores it, and review revocation invalidates it again.
    # Logic: Determines old/new by extraction ID, not lexical version ordering.
    # Constraints: No model call or automatic re-extraction.
    def test_latest_failed_extraction_and_review_withdraw(self):
        email, extraction = self.email("email-1")
        sync_owner(self.owner.pk)
        self.assertTrue(self.active("reported_budget").exists())
        failed = Extraction.objects.create(email=email, prompt_version="synthetic-test-v2", status="failed", facts=None)
        sync_owner(self.owner.pk)
        self.assertFalse(self.active("reported_budget").exists())
        Extraction.objects.filter(pk=failed.pk).update(status="completed", facts=extraction.facts)
        sync_owner(self.owner.pk)
        self.assertTrue(self.active("reported_budget").exists())
        Email.objects.filter(pk=email.pk).update(business_classification="non_business", review_revision=1)
        sync_owner(self.owner.pk)
        self.assertFalse(self.active("reported_budget").exists())

    # Function: Preserves distinct budget candidates for the same customer.
    # Inputs: No external parameters; two emails with different budgets.
    # Outputs: Both candidates are marked `needs_review`.
    # Logic: Does not let a later-processed email overwrite an earlier one or assert they must conflict.
    # Constraints: The first version does not automatically assign mail to a specific opportunity.
    def test_conflicting_candidates_are_not_overwritten(self):
        self.email("email-1", "100 SGD")
        self.email("email-2", "200 SGD")
        sync_owner(self.owner.pk)
        self.assertEqual(set(self.active("reported_budget").values_list("value", flat=True)), {"100 SGD", "200 SGD"})
        self.assertEqual(set(self.active("reported_budget").values_list("status", flat=True)), {"needs_review"})

    # Function: Verifies projection failure rolls back wholly and requires explicit recovery.
    # Inputs: No external parameters; published graph and unlocatable fake evidence.
    # Outputs: Generation remains unchanged, failed event persists, and explicit recovery succeeds.
    # Logic: Correcting source data does not silently rerun old `failed`; `--retry-failed` maps to an explicit service recovery parameter.
    # Constraints: Failure samples are generated only in the test database.
    def test_failure_rolls_back_and_requires_explicit_recovery(self):
        sync_owner(self.owner.pk)
        generation = ProjectionState.objects.get(owner=self.owner).generation
        email, extraction = self.email("invalid")
        original = extraction.facts
        Extraction.objects.filter(pk=extraction.pk).update(facts={"budget": [{"value": "100 SGD", "evidences": ["invented"]}]})
        with self.assertRaises(ValueError):
            sync_owner(self.owner.pk)
        self.assertEqual(ProjectionState.objects.get(owner=self.owner).generation, generation)
        self.assertTrue(Change.objects.filter(owner_id=self.owner.pk, status="failed").exists())
        Extraction.objects.filter(pk=extraction.pk).update(facts=original)
        self.assertNotIn(self.owner.pk, pending_owners())
        request_sync(self.owner.pk, retry_failed=True)
        sync_owner(self.owner.pk)
        self.assertTrue(self.active("reported_budget").exists())

    # Function: Verifies entity deletion and mailbox cascade revocation.
    # Inputs: No external parameters; one projected email and a product without order references.
    # Outputs: Purchase catalog and email facts are both no longer valid, while historical versions remain auditable.
    # Logic: Real delete triggers parent events and covers child cascading.
    # Constraints: Does not send email or delete an external mailbox.
    def test_delete_and_parent_cascade(self):
        email, _ = self.email("email-1")
        sync_owner(self.owner.pk)
        Mailbox.objects.filter(pk=email.mailbox_id).delete()
        self.product.delete()
        sync_owner(self.owner.pk)
        self.assertFalse(self.active("reported_budget").exists())
        self.assertFalse(Entity.objects.filter(kind="sales.product", active=True).exists())
        self.assertTrue(SourceVersion.objects.filter(kind="crm.email", current=False).exists())

    # Function: Verifies ownership transfer revokes the old user's view without exposing another user's lineage.
    # Inputs: No external parameters; directly transfers a product without transaction references to another account.
    # Outputs: Both sides generate events; old user loses the current product and new user has an independent entity.
    # Logic: Both old and new owners rebuild; cross-user fact details return 404.
    # Constraints: Direct SQL is a test boundary; normal business retains immutable ownership constraints.
    def test_owner_transfer_and_api_isolation(self):
        sync_owner(self.owner.pk)
        Product.objects.filter(pk=self.product.pk).update(owner=self.other)
        self.assertIn(self.other.pk, pending_owners())
        sync_owner(self.owner.pk)
        sync_owner(self.other.pk)
        self.assertFalse(Entity.objects.filter(owner=self.owner, kind="sales.product", active=True).exists())
        foreign = Fact.objects.get(owner=self.other, predicate="catalog_price", status="active")
        response = self.client.get(f"/api/v1/graph/facts/{foreign.pk}/lineage/")
        self.assertEqual(response.status_code, 404)

    # Function: Verifies graph freshness, query, and historical-lineage responses.
    # Inputs: No external parameters; a created-but-unprocessed source and confirmed order.
    # Outputs: Unsynchronized is 503; after synchronization facts and lineage are readable, and source changes immediately block old-graph reads.
    # Logic: `APIClient` uses real routes, state checks, and model queries.
    # Constraints: Forced authentication does not establish real browser login.
    def test_stale_api_and_lineage_contract(self):
        self.order("O1")
        self.assertEqual(self.client.get("/api/v1/graph/facts/").status_code, 503)
        sync_owner(self.owner.pk)
        response = self.client.get("/api/v1/graph/facts/?predicate=purchased")
        self.assertEqual(response.status_code, 200)
        fact_id = response.json()["results"][0]["id"]
        lineage = self.client.get(f"/api/v1/graph/facts/{fact_id}/lineage/").json()
        self.assertIn("sales.orderline", {row["kind"] for row in lineage["results"][0]["inputs"]})
        Product.objects.filter(pk=self.product.pk).update(name="New label")
        self.assertFalse(self.client.get("/api/v1/graph/status/").json()["current"])
        self.assertEqual(self.client.get("/api/v1/graph/entities/").status_code, 503)
        self.assertEqual(APIClient().get("/api/v1/graph/status/").status_code, 403)

    # Function: Verifies new submissions during rebuilding are not incorrectly acknowledged by an old snapshot.
    # Inputs: No external parameters; a thread modifies a product through an independent database connection.
    # Outputs: After the first run, the new event remains pending; only the next run publishes the updated price.
    # Logic: Writes after the Projector reads the source snapshot, verifying real PostgreSQL MVCC behavior.
    # Constraints: Does not depend on sleep-based race timing guesses.
    def test_new_commit_during_projection_stays_pending(self):
        original = Projector.run

        # Function: Continues an old-snapshot build after committing product price in an independent connection.
        # Inputs: `projector` is a projection instance that has read input.
        # Outputs: Statistics from the original run.
        # Logic: Thread pool waits for database commit; the next request must see the new pending event.
        # Constraints: Thread database connection is explicitly closed and does not write business data.
        def change_during_build(projector):
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(self.update_price_in_thread).result(timeout=15)
            return original(projector)

        with patch.object(Projector, "run", change_during_build):
            sync_owner(self.owner.pk)
        self.assertTrue(Change.objects.filter(owner_id=self.owner.pk, status="pending").exists())
        sync_owner(self.owner.pk)
        self.assertEqual(self.active("catalog_price").get().value["amount"], "150.00")

    # Function: Commits one product-price update in a thread's independent connection.
    # Inputs: No external parameters; reads the current test product primary key.
    # Outputs: None; closes the thread connection after commit.
    # Logic: A real autocommit write concurrent with the build transaction.
    # Constraints: Exceptions propagate to the `Future` and are not silently ignored.
    def update_price_in_thread(self):
        try:
            Product.objects.filter(pk=self.product.pk).update(unit_price="150")
        finally:
            connections.close_all()

    # Function: Verifies TRUNCATE creates a rebuild event and disabled capture is detectable.
    # Inputs: No external parameters; all source tables in the isolated database.
    # Outputs: Truncation events exist, and require_capture rejects disabled triggers.
    # Logic: Check the disabled trigger and restore it in finally before explicit TRUNCATE CASCADE.
    # Constraints: Test database only; never run this test SQL against real business databases.
    def test_truncate_capture_and_missing_trigger(self):
        sync_owner(self.owner.pk)
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE sales_product DISABLE TRIGGER salesmate_kg_capture")
            try:
                with self.assertRaises(RuntimeError):
                    require_capture()
            finally:
                cursor.execute("ALTER TABLE sales_product ENABLE TRIGGER salesmate_kg_capture")
            cursor.execute("TRUNCATE sales_product CASCADE")
        self.assertTrue(Change.objects.filter(owner_id=self.owner.pk, operation="TRUNCATE").exists())
        sync_owner(self.owner.pk)
        self.assertFalse(Entity.objects.filter(owner=self.owner, kind="sales.product", active=True).exists())

    # Function: Verify graph builds for one user cannot write concurrently.
    # Inputs: No external arguments; a second database connection holds that user's transaction lock.
    # Outputs: While the lock is held, sync_owner returns None and events remain pending.
    # Logic: The secondary connection acquires the same advisory key and releases/closes in finally.
    # Constraints: Lock only the test user; do not test throughput.
    def test_same_owner_lock_prevents_double_writer(self):
        other = connection.copy(alias="graph-lock-test")
        try:
            other.set_autocommit(False)
            with other.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", [f"salesmate-kg:{self.owner.pk}"])
            self.assertIsNone(sync_owner(self.owner.pk))
            self.assertTrue(Change.objects.filter(owner_id=self.owner.pk, status="pending").exists())
        finally:
            other.rollback()
            other.close()

    # Function: Verify account reset leaves no graph evidence and does not delete another account's graph.
    # Inputs: No external arguments; both users' graphs and the current user's original mail.
    # Outputs: Clear the user's entities, snapshots, events, and supports while preserving the other user's graph.
    # Logic: Run real reset_account with its exclusive lock, covering raw SQL deletion and M2M input edges.
    # Constraints: Operate only on test accounts/sessions without actual files or external services.
    def test_account_reset_removes_graph_and_keeps_other_owner(self):
        self.email("email-reset")
        sync_owner(self.owner.pk)
        Product.objects.create(owner=self.other, sku="OTHER", name="Other", currency="SGD", unit_price="1")
        sync_owner(self.other.pk)
        session = SessionStore()
        session.create()
        with account_lock(self.owner.pk, exclusive=True):
            reset_account(self.owner, uuid.uuid4(), session)
        self.assertFalse(Entity.objects.filter(owner=self.owner).exists())
        self.assertFalse(SourceVersion.objects.filter(owner=self.owner).exists())
        self.assertFalse(Change.objects.filter(owner_id=self.owner.pk).exists())
        self.assertFalse(Derivation.inputs.through.objects.filter(derivation__owner=self.owner).exists())
        self.assertTrue(Entity.objects.filter(owner=self.other).exists())
