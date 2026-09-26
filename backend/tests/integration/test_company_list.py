"""Responsibility: Verify business equivalence and query scale for bulk company-list queries.
Implementation: Create synthetic companies, emails, versions, and scores in isolated PostgreSQL; compare independent projections with bulk results and verify query count remains bounded as scale grows.
Relationships: List and detail views share projections from crm.selectors; does not access models, mailboxes, or production databases.
Directory:
- CompanyListTests: Verify bulk-list contract.
- CompanyListTests.setUp: Create two owners and a test mailbox.
- CompanyListTests.company: Generate a synthetic company with analysis and score.
- CompanyListTests.test_batch_matches_independent_projection: Compare complete rows and email/contact version semantics.
- CompanyListTests.test_query_count_does_not_grow_per_company: Verify query budget for two and thirty companies.
- CompanyListTests.test_filter_page_and_stats_preserve_global_scope: Verify filtering, global ordering, pagination, and pre-filter statistics.
- CompanyListTests.test_excludes_archived_hidden_and_foreign_companies: Verify archival, classification, and identity scope.
- CompanyListTests.test_invalid_latest_analysis_is_not_replaced_by_older_result: Verify an invisible analysis does not fall back to an older result.
- CompanyListTests.test_score_versions_and_repeated_reads: Verify version filtering and visibility of changes between requests.
- CompanyListTests.test_invalid_pagination_still_rejected: Verify invalid pagination still fails explicitly.
- CompanyListTests.test_list_does_not_load_mail_body_or_old_extraction_facts: Verify required-field projection and latest-summary consistency.
Variable index:
- None
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.crm import ingestion, rules, selectors
from apps.crm.models import Analysis, AnalysisInput, Company, Contact, Email, Extraction, Job, Mailbox, Score
from apps.sales.models import CompanySettings


# Function: Verify company-list behavior and bulk-query scale.
# Logic: Use real ORM and known synthetic records without mocking query counts; use formal scoring mode as the baseline.
# Constraints: Write only to the isolated test database and do not call models; passing does not establish production load capacity.
@override_settings(ANALYSIS_PROVIDER="agent", LAB_OPEN_ACCESS=False, LOCAL_DEBUG_AUTO_LOGIN=False)
class CompanyListTests(TestCase):
    # Function: Verify the list does not load complete mail bodies or historical extraction facts.
    # Inputs: Real isolated email record containing a large body and two summaries.
    # Outputs: Complete list row equals independent read, payload remains deferred, and constructing the row adds no query.
    # Logic: Verify field trimming through real ORM projection and captured SQL queries rather than comparing synthetic return values only.
    # Constraints: Body remains its database value; detail views and full Agent context must still read the body.
    def test_list_does_not_load_mail_body_or_old_extraction_facts(self):
        company = self.company(1)
        email = company.emails.get()
        email.payload = {**email.payload, "body_text": "large-body-" * 10000}
        email.save(update_fields=["payload"])
        Extraction.objects.create(email=email, prompt_version="new-summary", status="completed", facts={"message_summary": "Latest summary"})
        expected = selectors.company_row(company)
        projection = selectors.list_projection([company])[company.pk]
        self.assertIn("payload", projection["emails"][0].get_deferred_fields())
        with CaptureQueriesContext(connection) as queries:
            actual = selectors.company_row(company, projection)
        self.assertEqual(len(queries), 0)
        self.assertEqual(actual, expected)
        self.assertEqual(actual["headline_summary"], "Latest summary")
        self.assertEqual(selectors.context_pair(company, include_priority=False)[1]["emails"][0]["body_text"], email.payload["body_text"])

    # Function: Create test ownership.
    # Inputs: No explicit parameters; reads the isolated test database.
    # Outputs: Instance state for owner, other, and mailbox.
    # Logic: Do not use real accounts or service tokens.
    # Constraints: TestCase rolls records back.
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="list-owner")
        self.other = get_user_model().objects.create_user(username="list-other")
        self.mailbox = Mailbox.objects.create(owner=self.owner, address="owner@list.example")

    # Function: Create one company with valid email, successful analysis, and formal score.
    # Inputs: `index` determines independent domain, score, and signal; `owner` may specify another test owner and defaults to self.owner.
    # Outputs: A Company object.
    # Logic: Ingest email through the real service and use explicit synthetic payloads for analysis and scoring, verifying list-projection fields only.
    # Constraints: Does not claim synthetic analysis passed model evaluation; scores are test data only.
    def company(self, index, owner=None):
        owner = owner or self.owner
        mailbox = self.mailbox if owner == self.owner else Mailbox.objects.create(owner=owner, address=f"other{index}@list.example")
        payload = rules.extract_email(mailbox, f"buyer@company{index}.example", "设备询价", "需求：设备", f"case-{index}")
        ingestion.submit_emails(owner, [payload])
        email = Email.objects.get(pk=payload["dedupe_key"])
        company = email.company
        company.name = f"Company {index}"
        company.save(update_fields=["name"])
        snapshot = AnalysisInput.objects.create(company=company, revision=company.revision, input_version="fixture",
            payload={"member_dedupe_keys": [email.pk]})
        analysis = Analysis.objects.create(snapshot=snapshot, prompt_version="fixture", provider="agent",
            payload={"status": "completed", "generated_at": "2026-09-26T00:00:00Z", "list_view": {
                "signal": "high" if index % 2 else "low", "industry": "test", "size_band": "small"}})
        Score.objects.create(analysis=analysis, score_version="score-v2", value=index,
            payload={"score_reasons": [{"feature": "urgency", "contribution": index}], "scored_at": "2026-09-26T00:00:00Z"})
        return company

    # Function: Verify bulk and independent paths return the same complete row.
    # Inputs: Real email, manually assigned primary contact, contact without email, non-business contact, and an updated extraction version.
    # Outputs: Complete dictionaries are equal, with consistent current summary, primary contact, and sources.
    # Logic: After prefetching multiple companies together, compare each row with the independent company_row used by detail views.
    # Constraints: Does not replace ORM or result-generation functions.
    def test_batch_matches_independent_projection(self):
        first, second = self.company(1), self.company(2)
        email = first.emails.get()
        Extraction.objects.create(email=email, prompt_version="new-fixture", status="completed", facts={"message_summary": "Current summary"})
        manual = Contact.objects.create(company=first, email="manual@company1.example", name="Manual")
        CompanySettings.objects.create(owner=self.owner, company=first, primary_contact=manual)
        hidden_contact = Contact.objects.create(company=first, email="hidden@company1.example")
        Email.objects.create(company=first, mailbox=self.mailbox, contact=hidden_contact, dedupe_key="hidden-fixture",
            business_classification="non_business", payload={}, sent_at=timezone.now(), received_at=timezone.now(), direction="inbound")
        companies = [first, second]
        projection = selectors.list_projection(companies)
        for company in companies:
            self.assertEqual(selectors.company_row(company), selectors.company_row(company, projection[company.pk]))
        row = selectors.company_row(first, projection[first.pk])
        self.assertEqual(row["headline_summary"], "Current summary")
        self.assertEqual([c["contact_email"] for c in row["contacts"] if c["is_primary"]], [manual.email])
        self.assertNotIn(hidden_contact.email, [c["contact_email"] for c in row["contacts"]])

    # Function: Verify query count does not grow linearly with company count.
    # Inputs: First two, then thirty companies, each with email, extraction, job, analysis, and score.
    # Outputs: The two query counts are equal and no greater than eight; thirty-company statistics and pagination count are correct.
    # Logic: Capture real database execution and do not fabricate query budgets through mocks.
    # Constraints: Samples contain no experimental supplementary material; supplementary-material verification retains independent semantics.
    def test_query_count_does_not_grow_per_company(self):
        self.company(1)
        self.company(2)
        with CaptureQueriesContext(connection) as small:
            selectors.list_companies(Company.objects.filter(owner=self.owner), {})
        for index in range(3, 31):
            self.company(index)
        with CaptureQueriesContext(connection) as large:
            response = selectors.list_companies(Company.objects.filter(owner=self.owner), {})
        self.assertEqual(len(small), len(large))
        self.assertLessEqual(len(large), 8)
        self.assertEqual((response["count"], len(response["results"]), response["stats"]["new_emails_today"]), (30, 20, 30))

    # Function: Verify global filtering and sorting occur before pagination, and statistics precede filtering.
    # Inputs: Six companies with increasing scores and alternating signals; one email's received date is set to yesterday.
    # Outputs: The second page retains the correct score, filtered count, and statistics for the complete authorized set.
    # Logic: High-signal companies have scores 1, 3, and 5; page 2 with size 1 must return 3.
    # Constraints: Does not alter production sort weights, timezone, or default pagination.
    def test_filter_page_and_stats_preserve_global_scope(self):
        companies = [self.company(index) for index in range(1, 7)]
        companies[0].emails.update(received_at=timezone.now() - timedelta(days=1))
        response = selectors.list_companies(Company.objects.filter(owner=self.owner), {"signal": "high", "page": "2", "page_size": "1"})
        self.assertEqual(response["count"], 3)
        self.assertEqual(response["results"][0]["score"], 3)
        self.assertEqual(response["stats"], {"companies": 6, "unregistered": 6, "new_emails_today": 5})
        self.assertEqual(selectors.list_companies(Company.objects.filter(owner=self.owner), {"q": "BUYER@COMPANY4.EXAMPLE"})["count"], 1)

    # Function: Verify bulk querying does not expand authorization or the visible set.
    # Inputs: Normal, archived, non-business-only, email-free, and foreign-owner companies.
    # Outputs: Only the normal authorized company is visible, and statistics exclude other records.
    # Logic: Real relationship filters run before bulk read; empty results still return zero statistics.
    # Constraints: Caller supplies owner scope; bulk loading must not reuse the whole database.
    def test_excludes_archived_hidden_and_foreign_companies(self):
        visible, archived, hidden = [self.company(index) for index in range(1, 4)]
        CompanySettings.objects.create(owner=self.owner, company=archived, archived=True)
        hidden.emails.update(business_classification="non_business")
        Company.objects.create(owner=self.owner, group_key="empty")
        self.company(9, self.other)
        result = selectors.list_companies(Company.objects.filter(owner=self.owner), {})
        self.assertEqual([row["company_id"] for row in result["results"]], [str(visible.pk)])
        self.assertEqual(result["stats"]["companies"], 1)
        self.assertEqual(selectors.list_companies(Company.objects.none(), {})["count"], 0)

    # Function: Verify an invisible newer analysis does not display an older profile.
    # Inputs: One valid older analysis and a newer successful analysis referencing a hidden source.
    # Outputs: Both bulk and independent paths hide analysis and score; later unmatched experimental material is also hidden.
    # Logic: Select the latest successful analysis first, then validate visibility while retaining established rejection semantics.
    # Constraints: Does not fall back, delete history, or call a real model.
    def test_invalid_latest_analysis_is_not_replaced_by_older_result(self):
        company = self.company(1)
        snapshot = AnalysisInput.objects.create(company=company, revision=company.revision, input_version="new",
            payload={"member_dedupe_keys": ["no-longer-visible"]})
        Analysis.objects.create(snapshot=snapshot, prompt_version="new", provider="agent", payload={"status": "completed"})
        projection = selectors.list_projection([company])
        self.assertEqual(selectors.latest_result(company, projection[company.pk]), (None, None))
        self.assertEqual(selectors.company_row(company), selectors.company_row(company, projection[company.pk]))
        snapshot.payload = {"member_dedupe_keys": [], "business_context": {"company_enrichment": {"status": "matched"}}}
        snapshot.save(update_fields=["payload"])
        with self.settings(WORKSPACE_OWNER_ONLY=True):
            self.assertEqual(selectors.latest_result(company, selectors.list_projection([company])[company.pk]), (None, None))

    # Function: Verify score versions, latest failed analysis, and updates between requests.
    # Inputs: A formal score followed by an older rules score, a failed analysis, and a completed job.
    # Outputs: Agent shows the formal score, rules shows the latest score, and the next request sees latest job state.
    # Logic: Request projections are not stored in persistent cache; scores belong only to the selected successful analysis.
    # Constraints: Switches provider only within test coverage and does not change runtime defaults.
    def test_score_versions_and_repeated_reads(self):
        company = self.company(1)
        analysis = Analysis.objects.get(snapshot__company=company)
        Score.objects.create(analysis=analysis, score_version="rules-score-v1", value=99, payload={"score_reasons": [], "scored_at": "fixture"})
        Analysis.objects.create(snapshot=analysis.snapshot, prompt_version="failed", provider="agent", payload={"status": "failed"})
        query = Company.objects.filter(owner=self.owner)
        self.assertEqual(selectors.list_companies(query, {})["results"][0]["score"], 1)
        with self.settings(ANALYSIS_PROVIDER="rules"):
            self.assertEqual(selectors.list_companies(query, {})["results"][0]["score"], 99)
        Job.objects.filter(company=company).update(status="completed")
        self.assertEqual(selectors.list_companies(query, {})["results"][0]["job_status"], "completed")

    # Function: Verify pagination-parameter constraints remain.
    # Inputs: Non-integer, zero page, negative size, and size over the upper limit.
    # Outputs: Each input raises ValidationError.
    # Logic: Retain existing integer-conversion and range-rejection rules.
    # Constraints: Do not relax the upper limit or silently correct user input.
    def test_invalid_pagination_still_rejected(self):
        for params in ({"page": "invalid"}, {"page": "0"}, {"page_size": "-1"}, {"page_size": "101"}):
            with self.subTest(params=params), self.assertRaises(ValidationError):
                selectors.list_companies(Company.objects.none(), params)
