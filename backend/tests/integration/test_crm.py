"""Responsibility: Verify the mail-understanding workflow and isolation boundaries against the default test database.
Implementation: Verify synchronization persists its batch and returns 202 without simulating the removed Web scheduler. Django TestCase creates an isolated database; synthetic mail covers interfaces, transactions, leases, caching, and CSRF.
Relationships: Use the independent Agent's HTTP protocol; rules generate test payloads without mocking the database.
Directory:
- CRMTests: Verify key business invariants required by local frontend/backend and Agent contracts.
- CRMTests.setUp: Create two users and one scoped Agent credential.
- CRMTests.email: Construct explicitly labeled synthetic mail.
- CRMTests.submit: Submit mail and return the persisted company.
- CRMTests.prepare: Build consistent input for the submitted company and claim a lease.
- CRMTests.test_rule_flow_preserves_budget_history_and_page_projection: Verify real rules-mode persistence and seven-dimensional detail queries.
- CRMTests.test_agent_http_contract_end_to_end: Verify every main Agent save step in the README chains through HTTP.
- CRMTests.test_duplicate_email_is_idempotent_and_immutable: Verify duplicate-ingestion idempotency and immutable message bodies.
- CRMTests.test_public_domain_grouping: Verify public-mailbox contacts form separate groups.
- CRMTests.test_batch_ownership_failure_rolls_back: Verify an unauthorized batch item rolls back all writes.
- CRMTests.test_user_isolation_and_agent_authentication: Verify both page and Agent-context cross-user access are rejected.
- CRMTests.test_failed_extraction_can_be_completed_once: Verify failed facts can be successfully resubmitted only once.
- CRMTests.test_failed_extraction_updates_during_next_sync: Verify the next batch sync can directly complete failed extraction.
- CRMTests.test_non_business_email_is_saved_without_job: Verify non-business mail remains stored without creating analysis tasks.
- CRMTests.test_unlocatable_evidence_and_unknown_fields_rejected: Verify evidence absent from original text and unknown fields are rejected.
- CRMTests.test_old_revision_cannot_save_after_new_email: Verify old tasks cannot save input after new mail arrives.
- CRMTests.test_lease_token_and_expiration: Verify invalid or expired leases cannot write.
- CRMTests.test_job_claim_excludes_running_work: Verify repeated claiming does not claim running tasks.
- CRMTests.test_registration_invalidates_cache_and_checks_version: Verify CRM profile updates invalidate old caches and support unknown headcounts.
- CRMTests.test_null_score_and_list_sorting: Verify null-score semantics and unscored-last list ordering.
- CRMTests.test_sync_state_compare_and_swap: Verify optimistic locking of synchronization cursors.
- CRMTests.test_employee_gmail_connection_and_sync_queue: Verify employee mailbox isolation, sync claiming, and reports.
- CRMTests.test_employee_gmail_oauth_browser_routes: Verify browser OAuth entry and callback redirection.
- CRMTests.test_employee_gmail_oauth_reuses_pkce_verifier: Verify callback PKCE-verifier reuse and acceptance of scope supersets containing read-only permission.
- CRMTests.test_agent_mode_has_no_implicit_rule_fallback: Verify Agent mode neither invokes rules nor imports samples.
- CRMTests.test_session_login_requires_csrf_and_valid_password: Verify real CSRF protection for anonymous login and authenticated writes.
- CRMTests.test_demo_seed_is_repeatable_without_duplicate_data: Verify repeated demo imports preserve mail and timestamps.
- CRMTests.test_analysis_rejects_foreign_evidence_and_deal_probability: Verify forged sources/closing probabilities are rejected while business percentages can persist.
- CRMTests.test_analysis_input_requires_complete_fact_multiset: Verify consolidation neither drops historical facts nor counts facts twice.
- CRMTests.test_invalid_identifiers_return_400: Verify invalid UUIDs produce controlled input errors.
- CRMTests.test_explicit_reanalysis_completes_missing_score: Verify explicit reanalysis completes scores for partially completed rules tasks.
Variable index:
- None
"""
from copy import deepcopy
from datetime import timedelta
import hashlib
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.crm import jobs, results, rules, selectors
from apps.crm.access import Conflict
from apps.crm.models import AgentCredential, Analysis, AnalysisInput, Company, Email, GmailCredential, Job, Mailbox
from apps.crm.response_schemas import JobResponseSerializer, GroupingResponseSerializer, CompanyContextResponseSerializer


# Function: Verify key business invariants required by local frontend/backend and Agent contracts.
# Logic: Each test uses independent users, mailboxes, and an isolated database; rules provide deterministic assertions and sync requests verify durable queuing only.
# Constraints: No Gmail or real-model connections; generated test payloads do not evaluate Agent effectiveness.
@override_settings(ANALYSIS_PROVIDER="rules")
class CRMTests(TestCase):
    # Function: Create two users and one scoped Agent credential.
    # Inputs: Invoked by the test framework without external arguments.
    # Outputs: Initialize user, other, mailbox, agent, and browser.
    # Logic: Business tests force browser authentication; dedicated CSRF tests sign in through the real path.
    # Constraints: All passwords and tokens are fictional test data.
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="sales", password="test-password-123")
        self.other = get_user_model().objects.create_user(username="other", password="test-password-456")
        self.mailbox = Mailbox.objects.create(owner=self.user, address="sales@internal.example")
        AgentCredential.objects.create(owner=self.user, name="test", digest=hashlib.sha256(b"test-agent-token").hexdigest())
        self.agent = APIClient()
        self.agent.credentials(HTTP_AUTHORIZATION="Agent test-agent-token")
        self.browser = APIClient()
        self.browser.force_authenticate(self.user)

    # Function: Construct explicitly labeled synthetic mail.
    # Inputs: `message_id` identifies duplicates; `sender` is optional; `body` optionally provides test text.
    # Outputs: Complete EmailSubmission.
    # Logic: Extract fixed labels through rules while retaining original-text evidence.
    # Constraints: No external reads or silent experimental-sample changes.
    def email(self, message_id="one", sender="buyer@optics.example", body=None):
        return rules.extract_email(self.mailbox, sender, "设备询价", body or "公司：测试光学\n行业：光学检测\n需求：采购设备\n数量：12 台\n预算：30 万\n交期：下月\n决策流程：经理审批", message_id)

    # Function: Submit mail and return the persisted company.
    # Inputs: `payload` can override the default synthetic email.
    # Outputs: Company.
    # Logic: Submit through Agent HTTP and query the company only after success.
    # Constraints: Later steps cannot proceed after protocol failure.
    def submit(self, payload=None):
        response = self.agent.post("/api/v1/agent/emails/", [payload or self.email()], format="json")
        self.assertEqual(response.status_code, 200, response.data)
        return Company.objects.get(pk=response.data[0]["company_id"])

    # Function: Build consistent input for the submitted company and claim a lease.
    # Inputs: `company` is the database company.
    # Outputs: job、snapshot、grouping、context.
    # Logic: Use real claiming services and deterministic consolidation.
    # Constraints: Direct service calls are test-only; separate complete-protocol tests cover HTTP transport.
    def prepare(self, company):
        job = jobs.claim(self.user, 1, 120)[0]
        grouping, context = selectors.context_pair(company)
        snapshot = rules.build_input(grouping, context)
        return job, snapshot, grouping, context

    # Function: Verify real rules-mode persistence and seven-dimensional detail queries.
    # Inputs: Test user and two emails changing the budget.
    # Outputs: Assert profiles, scores, fact history, and sources.
    # Logic: Trigger the rules consumer through HTTP and verify L2 retains both budgets instead of overwriting one.
    # Constraints: Rule output does not establish real-model effectiveness.
    def test_rule_flow_preserves_budget_history_and_page_projection(self):
        company = self.submit()
        self.submit(self.email("two", body="预算：26 万\n数量：12 台"))
        response = self.browser.post(f"/api/v1/companies/{company.pk}/analyze/")
        self.assertEqual(response.status_code, 200, response.data)
        detail = self.browser.get(f"/api/v1/companies/{company.pk}/").data
        self.assertEqual(detail["provider"], "rules")
        self.assertEqual(detail["email_count"], 2)
        self.assertFalse(detail["stale"])
        self.assertEqual(detail["score"], 33)
        self.assertEqual(len(detail["analysis"]["detail_view"]["profile"]), 3)
        self.assertEqual(len(detail["analysis"]["detail_view"]["analysis"]), 4)
        self.assertEqual([item["value"] for item in AnalysisInput.objects.get().payload["facts"]["budget"]], ["30 万", "26 万"])
        self.assertEqual(Job.objects.get().status, "completed")

    # Function: Verify every main Agent save step in the README chains through HTTP.
    # Inputs: Synthetic L1 payload omitting optional time/thread fields and a claimed task.
    # Outputs: Successful reports and provider=agent results.
    # Logic: Read ETag, verify actual query responses against schemas, then send Input, Analysis, Score, and Report in order.
    # Constraints: Rules only generate contract examples; no real Agent calls.
    def test_agent_http_contract_end_to_end(self):
        payload = self.email()
        payload.update(sent_at=None, received_at=None, thread_id=None)
        company = self.submit(payload)
        claimed = self.agent.post("/api/v1/agent/jobs/claim/", {"limit": 1, "lease_seconds": 120}, format="json")
        job = claimed.data[0]
        grouping_response = self.agent.get("/api/v1/agent/grouping/", {"company_id": str(company.pk)})
        context_response = self.agent.get("/api/v1/agent/context/", {"company_id": str(company.pk)}, HTTP_IF_MATCH=grouping_response["ETag"])
        self.assertEqual(context_response.status_code, 200, context_response.data)
        grouping, context = grouping_response.data, context_response.data
        for schema, payload in [(JobResponseSerializer, job), (GroupingResponseSerializer, grouping), (CompanyContextResponseSerializer, context)]:
            checked = schema(data=payload)
            self.assertTrue(checked.is_valid(), checked.errors)
        snapshot = rules.build_input(grouping, context)
        headers = {"HTTP_IF_MATCH": grouping_response["ETag"], "HTTP_X_JOB_ID": job["job_id"], "HTTP_X_LEASE_TOKEN": job["lease_token"]}
        response = self.agent.post("/api/v1/agent/analysis-inputs/", snapshot, format="json", **headers)
        self.assertEqual(response.status_code, 200, response.data)
        analysis = rules.generate_analysis(snapshot, grouping, context)
        analysis["analysis_prompt_version"] = "agent-contract-test-v1"
        response = self.agent.post("/api/v1/agent/analyses/", analysis, format="json", **headers)
        self.assertEqual(response.status_code, 200, response.data)
        response = self.agent.post("/api/v1/agent/scores/", rules.compute_score(analysis), format="json", **headers)
        self.assertEqual(response.status_code, 200, response.data)
        response = self.agent.post("/api/v1/agent/jobs/report/", {"job_id": job["job_id"], "status": "completed", "input_version": snapshot["input_version"], "produced": {"analysis": True, "score": True, "emails_submitted": 0}, "error": None, "duration_ms": 1}, format="json", HTTP_X_LEASE_TOKEN=job["lease_token"])
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(Analysis.objects.get().provider, "agent")

    # Function: Verify duplicate-ingestion idempotency and immutable message bodies.
    # Inputs: Submit the same payload twice, then change its body.
    # Outputs: duplicate status, one email, and 409.
    # Logic: Compare persisted row counts and backend state.
    # Constraints: Do not evade deduplication by using a separate new ID.
    def test_duplicate_email_is_idempotent_and_immutable(self):
        payload = self.email()
        self.submit(payload)
        duplicate = self.agent.post("/api/v1/agent/emails/", [payload], format="json")
        self.assertEqual(duplicate.data[0]["status"], "duplicate")
        self.assertEqual(Email.objects.count(), 1)
        payload["body_text"] += "\n新内容"
        rejected = self.agent.post("/api/v1/agent/emails/", [payload], format="json")
        self.assertEqual(rejected.status_code, 409)

    # Function: Verify public-mailbox contacts form separate groups.
    # Inputs: Two gmail.com contacts and two contacts sharing a corporate domain.
    # Outputs: Three company groups.
    # Logic: Group the corporate domain together while keeping public-mailbox contacts separate.
    # Constraints: Do not test unimplemented manual subdomain merging.
    def test_public_domain_grouping(self):
        for index, address in enumerate(["a@gmail.com", "b@gmail.com", "a@optics.example", "b@optics.example"]):
            self.submit(self.email(str(index), address))
        self.assertEqual(Company.objects.count(), 3)

    # Function: Verify an unauthorized batch item rolls back all writes.
    # Inputs: A valid first email and a second mailbox belonging to another user.
    # Outputs: 404 with no new persisted mail.
    # Logic: Construct a second item with valid protocol structure but invalid ownership.
    # Constraints: Isolation cannot depend on frontend entry-point hiding.
    def test_batch_ownership_failure_rolls_back(self):
        foreign = Mailbox.objects.create(owner=self.other, address="other@example.com")
        first = self.email()
        second = rules.extract_email(foreign, "b@example.com", "询价", "需求：设备", "foreign")
        response = self.agent.post("/api/v1/agent/emails/", [first, second], format="json")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(Email.objects.count(), 0)
        self.assertEqual(Company.objects.count(), 0)

    # Function: Verify both page and Agent-context cross-user access are rejected.
    # Inputs: Another user's session accesses the created company.
    # Outputs: 404, empty lists, and 401 for tokenless Agent requests.
    # Logic: Browser sessions cannot replace independent Agent credentials.
    # Constraints: Do not print other users' data.
    def test_user_isolation_and_agent_authentication(self):
        company = self.submit()
        self.browser.force_authenticate(self.other)
        self.assertEqual(self.browser.get(f"/api/v1/companies/{company.pk}/").status_code, 404)
        self.assertEqual(self.browser.get("/api/v1/companies/").data["count"], 0)
        anonymous = APIClient()
        self.assertEqual(anonymous.get("/api/v1/agent/grouping/", {"company_id": str(company.pk)}).status_code, 401)
        anonymous.force_login(self.other)
        self.assertEqual(anonymous.post("/api/v1/agent/jobs/claim/", {"limit": 1, "lease_seconds": 120}, format="json").status_code, 401)

    # Function: Verify failed facts can be successfully resubmitted only once.
    # Inputs: A failed email without facts and existing locatable facts.
    # Outputs: First success, second conflict, and an incremented revision.
    # Logic: Repeat through the real Agent facts route.
    # Constraints: Retain exactly one message body without data loss.
    def test_failed_extraction_can_be_completed_once(self):
        payload = self.email()
        facts = deepcopy(payload["facts"])
        payload.update(extract_status="failed", extract_error="simulated model failure", facts=None)
        company = self.submit(payload)
        resubmit = {"dedupe_key": payload["dedupe_key"], "extract_prompt_version": payload["extract_prompt_version"], "extract_status": "completed", "extract_error": None, "facts": facts}
        first = self.agent.post("/api/v1/agent/facts/", resubmit, format="json")
        self.assertEqual(first.status_code, 200, first.data)
        second = self.agent.post("/api/v1/agent/facts/", resubmit, format="json")
        self.assertEqual(second.status_code, 409)
        company.refresh_from_db()
        self.assertEqual(company.revision, 2)
        self.assertEqual(Email.objects.count(), 1)

    # Function: Verify rescanning the same email can directly change failed to completed.
    # Inputs: Failed, successful, and repeated-success payloads for the same message body.
    # Outputs: created, updated, and duplicate states with only one analysis task.
    # Logic: Recover through the batch emails endpoint actually used by Gmail synchronization.
    # Constraints: Message body and extract_prompt_version must remain identical.
    def test_failed_extraction_updates_during_next_sync(self):
        completed = self.email("retry")
        failed = deepcopy(completed)
        failed.update(extract_status="failed", extract_error="temporary model error", facts=None)
        first = self.agent.post("/api/v1/agent/emails/", [failed], format="json")
        second = self.agent.post("/api/v1/agent/emails/", [completed], format="json")
        third = self.agent.post("/api/v1/agent/emails/", [completed], format="json")
        self.assertEqual(first.data[0]["status"], "created")
        self.assertEqual(second.data[0]["status"], "updated")
        self.assertEqual(third.data[0]["status"], "duplicate")
        self.assertEqual(Job.objects.count(), 1)

    # Function: Verify persisted non-business mail does not enter the company-analysis queue.
    # Inputs: A complete message body marked skipped_non_business by L1.
    # Outputs: Mail is created normally, Extraction retains skipped status, and Job count is zero.
    # Logic: Check task-trigger conditions through the formal batch-ingestion endpoint.
    # Constraints: Non-business mail still participates in history and deduplication.
    def test_non_business_email_is_saved_without_job(self):
        payload = self.email("non-business")
        payload.update(non_business_hint=True, non_business_reason="automated",
                       extract_status="skipped_non_business", facts=None, extract_error=None)
        response = self.agent.post("/api/v1/agent/emails/", [payload], format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data[0]["status"], "created")
        self.assertEqual(Email.objects.count(), 1)
        self.assertEqual(Job.objects.count(), 0)

    # Function: Verify evidence absent from original text and unknown fields are rejected.
    # Inputs: Bodies differing only in whitespace/invisible formatting, tampered budget evidence, and undeclared access_token.
    # Outputs: Formatting differences ingest normally; forged evidence and unknown fields return 400.
    # Logic: First check Agent-consistent evidence location, then cover fact and protocol boundaries.
    # Constraints: The token value is fictional.
    def test_unlocatable_evidence_and_unknown_fields_rejected(self):
        compatible = self.email("formatting")
        compatible["body_text"] = compatible["body_text"].replace(
            "需求：采购设备", "需求：采\u200b购\n设备"
        )
        accepted = self.agent.post(
            "/api/v1/agent/emails/", [compatible], format="json"
        )
        self.assertEqual(accepted.status_code, 200, accepted.data)

        payload = self.email()
        payload["facts"]["budget"][0]["evidences"][0] = "原文不存在的证据"
        self.assertEqual(self.agent.post("/api/v1/agent/emails/", [payload], format="json").status_code, 400)
        payload = self.email()
        payload["access_token"] = "synthetic-not-a-token"
        self.assertEqual(self.agent.post("/api/v1/agent/emails/", [payload], format="json").status_code, 400)
        self.assertEqual(Email.objects.count(), 1)

    # Function: Verify old tasks cannot save input after new mail arrives.
    # Inputs: Claim an old revision, then submit another message.
    # Outputs: Conflict and an independent pending successor task.
    # Logic: Compare real company revision, not only input_version.
    # Constraints: Do not hide concurrent overwrites by terminating old consumers.
    def test_old_revision_cannot_save_after_new_email(self):
        company = self.submit()
        job, snapshot, _, _ = self.prepare(company)
        self.submit(self.email("two"))
        with self.assertRaises(Conflict):
            results.save_input(self.user, snapshot, job["expected_version"], job["job_id"], job["lease_token"])
        self.assertEqual(Job.objects.filter(status="pending").count(), 1)

    # Function: Verify invalid or expired leases cannot write.
    # Inputs: Correct task with an incorrect token, followed by manually advanced expiry.
    # Outputs: Two Conflicts; another claim marks failure without automatically retrying.
    # Logic: Update test records directly to simulate time boundaries.
    # Constraints: Test database timestamps only; do not change the production clock.
    def test_lease_token_and_expiration(self):
        company = self.submit()
        job, snapshot, _, _ = self.prepare(company)
        with self.assertRaises(Conflict):
            results.save_input(self.user, snapshot, company.revision, job["job_id"], "wrong")
        Job.objects.filter(pk=job["job_id"]).update(lease_until=timezone.now() - timedelta(seconds=1))
        with self.assertRaises(Conflict):
            results.save_input(self.user, snapshot, company.revision, job["job_id"], job["lease_token"])
        self.assertEqual(jobs.claim(self.user, 1, 120), [])
        self.assertEqual(Job.objects.get().status, "failed")

    # Function: Verify repeated claiming does not claim running tasks.
    # Inputs: Claim the same pending work twice consecutively.
    # Outputs: One result first, then an empty list.
    # Logic: Claim through the current test database's transaction path.
    # Constraints: Sequential test only; does not establish multiprocess scheduling capacity.
    def test_job_claim_excludes_running_work(self):
        self.submit()
        self.assertEqual(len(jobs.claim(self.user, 1, 120)), 1)
        self.assertEqual(jobs.claim(self.user, 1, 120), [])

    # Function: Verify CRM profile updates invalidate old caches and support unknown headcounts.
    # Inputs: Existing rules result followed by profile registration after switching to agent mode.
    # Outputs: Old analysis becomes stale, external_version updates, and old-revision writes are rejected.
    # Logic: Inspect stale-result flags through the page without processing new work automatically.
    # Constraints: Do not conceal cache invalidation with newly generated rule results.
    def test_registration_invalidates_cache_and_checks_version(self):
        company = self.submit()
        rules.run_company(self.user, company.pk)
        version = AnalysisInput.objects.get().input_version
        self.assertTrue(results.cached_analysis(company, version, rules.ANALYSIS_VERSION)["hit"])
        self.assertFalse(results.cached_analysis(company, version, "different-prompt")["hit"])
        body = {"company_name": "测试光学", "industry_from_crm": "光学检测", "employee_count": None, "employee_count_source": None}
        with override_settings(ANALYSIS_PROVIDER="agent"):
            response = self.browser.post(f"/api/v1/companies/{company.pk}/register/", body, format="json", HTTP_IF_MATCH=str(company.revision))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["stale"])
        company.refresh_from_db()
        self.assertFalse(results.cached_analysis(company, version)["hit"])
        rejected = self.browser.post(f"/api/v1/companies/{company.pk}/register/", body, format="json", HTTP_IF_MATCH="0")
        self.assertEqual(rejected.status_code, 409)

    # Function: Verify null-score semantics and unscored-last list ordering.
    # Inputs: One company with complete rule fields and another missing delivery/decision data.
    # Outputs: Scored companies precede unscored ones; missing data does not fabricate zero scores.
    # Logic: Complete both rule workflows before querying the list.
    # Constraints: Does not evaluate formal Agent ranking quality.
    def test_null_score_and_list_sorting(self):
        complete = self.submit()
        incomplete = self.submit(self.email("other", "b@other.example", "需求：采购设备"))
        rules.run_company(self.user, complete.pk)
        rules.run_company(self.user, incomplete.pk)
        response = self.browser.get("/api/v1/companies/")
        self.assertEqual(response.data["results"][0]["company_id"], str(complete.pk))
        self.assertIsNone(response.data["results"][1]["score"])
        self.assertEqual(response.data["results"][1]["score_reasons"][0]["feature"], "insufficient_data")

    # Function: Verify optimistic locking of synchronization cursors.
    # Inputs: Initial state and repeated advancement using the same version.
    # Outputs: First version=1, then 409.
    # Logic: Read/write through Agent routes without Gmail calls.
    # Constraints: Verify backend cursor concurrency, not completed Gmail synchronization.
    def test_sync_state_compare_and_swap(self):
        state = self.agent.get("/api/v1/agent/sync-state/", {"mailbox_id": str(self.mailbox.pk)}).data
        state.update(cursor="historyId:123", status="ok", last_synced_at=timezone.now().isoformat(), scope={"labels": ["INBOX", "SENT"], "since": timezone.now().isoformat()})
        first = self.agent.post("/api/v1/agent/sync-state-save/", state, format="json", HTTP_IF_MATCH="0")
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(first.data["version"], 1)
        self.assertEqual(self.agent.post("/api/v1/agent/sync-state-save/", state, format="json", HTTP_IF_MATCH="0").status_code, 409)

    # Function: Verify Gmail connections appear only on their employee's page and can be claimed/reported by the corresponding Agent.
    # Inputs: Test Gmail credentials, browser sync requests, and Agent service calls; independent Workers consume execution.
    # Outputs: Token-free browser response, one Agent claim, and final completed sync state.
    # Logic: One Mailbox connects page state and Agent queue; a second employee cannot operate it. The browser explicitly requests at most 20 test messages, and the claim preserves those frozen conditions.
    # Constraints: No real Google connection; all credential contents are test data.
    def test_employee_gmail_connection_and_sync_queue(self):
        credentials = {
            "token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "fake-client",
            "client_secret": "fake-secret",
            "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
        }
        GmailCredential.objects.create(mailbox=self.mailbox, credentials=credentials)

        listed = self.browser.get("/api/v1/mailboxes/")
        self.assertEqual(listed.status_code, 200, listed.data)
        self.assertTrue(listed.data[0]["gmail_authorized"])
        self.assertNotIn("authorization", listed.data[0])
        self.assertNotIn("fake-access-token", str(listed.data))

        queued = self.browser.post(
            f"/api/v1/mailboxes/{self.mailbox.pk}/request-sync/",
            {"sync_options": {"max_messages": 20}}, format="json"
        )
        self.assertEqual(queued.status_code, 202, queued.data)
        self.assertEqual(queued.data["sync_state"]["status"], "sync_requested")

        claimed = self.agent.post(
            "/api/v1/agent/mailbox-syncs/claim/", {"limit": 5}, format="json"
        )
        self.assertEqual(claimed.status_code, 200, claimed.data)
        self.assertEqual(len(claimed.data), 1)
        self.assertEqual(claimed.data[0]["authorization"]["token"], "fake-access-token")
        self.assertEqual(
            self.agent.post(
                "/api/v1/agent/mailbox-syncs/claim/", {"limit": 5}, format="json"
            ).data,
            [],
        )

        report = self.agent.post(
            "/api/v1/agent/mailbox-syncs/report/",
            {
                "mailbox_id": str(self.mailbox.pk),
                "status": "completed",
                "sync_result": {"fetched_count": 3, "created_count": 2},
                "error": None,
                "authorization": credentials,
            },
            format="json",
        )
        self.assertEqual(report.status_code, 200, report.data)
        self.assertEqual(report.data["sync_state"]["status"], "completed")
        self.assertIn("last_synced_at", report.data["sync_state"])

        other_browser = APIClient()
        other_browser.force_authenticate(self.other)
        self.assertEqual(
            other_browser.post(
                f"/api/v1/mailboxes/{self.mailbox.pk}/request-sync/",
                {"sync_options": {"max_messages": 20}}, format="json"
            ).status_code,
            404,
        )

    # Function: Verify browser authorization entry and Google callback return to the current employee's workspace.
    # Inputs: `finish` mocks OAuth mailbox completion; independent Workers consume execution.
    # Outputs: Authorization URL JSON and a 302 redirect carrying authorization status.
    # Logic: Views enforce session boundaries; gmail_oauth encapsulates Google networking.
    # Constraints: No Google requests or verification of third-party OAuth SDK behavior.
    @override_settings(
        GOOGLE_OAUTH_CLIENT_ID="fake-web-client.apps.googleusercontent.com",
        GOOGLE_OAUTH_CLIENT_SECRET="fake-web-secret",
        GOOGLE_OAUTH_REDIRECT_URI="http://testserver/api/v1/mailboxes/gmail-callback/",
    )
    @patch("apps.crm.views.gmail_oauth.finish_authorization")
    def test_employee_gmail_oauth_browser_routes(self, finish):
        started = self.browser.post("/api/v1/mailboxes/gmail-authorize/")
        self.assertEqual(started.status_code, 200, started.data)
        self.assertTrue(
            started.data["authorization_url"].startswith(
                "https://accounts.google.com/o/oauth2/auth?"
            )
        )
        self.assertIn("gmail.readonly", started.data["authorization_url"])

        finish.return_value = self.mailbox
        callback = self.browser.get(
            "/api/v1/mailboxes/gmail-callback/?code=fake&state=fake"
        )
        self.assertEqual(callback.status_code, 302)
        self.assertIn("gmail=authorized", callback["Location"])
        self.assertIn("sales%40internal.example", callback["Location"])

    # Function: Verify consistent PKCE code_verifier and compatibility with Google's already-authorized scope supersets.
    # Inputs: `flow_factory` creates two Flows; `build` mocks Gmail profile; token exchange raises a scope Warning carrying a usable token.
    # Outputs: Callback succeeds, credentials persist without queuing, and the second Flow receives the first Flow's verifier.
    # Logic: Restore the verifier at callback and accept tokens when returned permissions still include gmail.readonly.
    # Constraints: All credentials are fictional; no external requests.
    @override_settings(
        GOOGLE_OAUTH_CLIENT_ID="fake-web-client.apps.googleusercontent.com",
        GOOGLE_OAUTH_CLIENT_SECRET="fake-web-secret",
        GOOGLE_OAUTH_REDIRECT_URI="http://testserver/api/v1/mailboxes/gmail-callback/",
    )
    @patch("apps.crm.gmail_oauth.build")
    @patch("apps.crm.gmail_oauth.Flow.from_client_config")
    def test_employee_gmail_oauth_reuses_pkce_verifier(self, flow_factory, build):
        begin_flow = Mock()
        begin_flow.code_verifier = "test-pkce-verifier"
        begin_flow.authorization_url.return_value = (
            "https://accounts.google.com/o/oauth2/auth?state=test-state",
            "test-state",
        )
        finish_flow = Mock()
        finish_flow.credentials.to_json.return_value = '{"token":"fake-token"}'
        scope_warning = Warning(
            'Scope has changed from "gmail.readonly" to "gmail.readonly gmail.insert".'
        )
        scope_warning.token = {
            "access_token": "fake-token",
            "expires_at": 1_900_000_000,
            "scope": [
                "https://www.googleapis.com/auth/gmail.readonly",
                "https://www.googleapis.com/auth/gmail.insert",
            ],
        }
        scope_warning.new_scope = scope_warning.token["scope"]
        finish_flow.fetch_token.side_effect = scope_warning
        flow_factory.side_effect = [begin_flow, finish_flow]
        build.return_value.users.return_value.getProfile.return_value.execute.return_value = {
            "emailAddress": self.mailbox.address
        }

        started = self.browser.post("/api/v1/mailboxes/gmail-authorize/")
        self.assertEqual(started.status_code, 200, started.data)
        self.assertEqual(
            self.browser.session["salesmate_gmail_oauth_code_verifier"],
            "test-pkce-verifier",
        )
        authorization_options = begin_flow.authorization_url.call_args.kwargs
        self.assertNotIn("include_granted_scopes", authorization_options)

        callback = self.browser.get(
            "/api/v1/mailboxes/gmail-callback/?code=test-code&state=test-state"
        )

        self.assertEqual(callback.status_code, 302)
        self.assertIn("gmail=authorized", callback["Location"])
        self.assertTrue(GmailCredential.objects.filter(mailbox=self.mailbox).exists())
        self.assertFalse(self.mailbox.sync_runs.exists())
        finish_flow.fetch_token.assert_called_once_with(code="test-code")
        self.assertEqual(finish_flow.oauth2session.token, scope_warning.token)
        self.assertEqual(
            flow_factory.call_args_list[1].kwargs["code_verifier"],
            "test-pkce-verifier",
        )
        self.assertFalse(
            flow_factory.call_args_list[1].kwargs["autogenerate_code_verifier"]
        )

    # Function: Verify Agent mode neither invokes rules nor imports samples.
    # Inputs: Request analysis after explicitly changing configuration.
    # Outputs: Task pending, empty results, and rejected simulation entry point.
    # Logic: Inspect persistent state rather than configuration values alone.
    # Constraints: No external Agent connection.
    @override_settings(ANALYSIS_PROVIDER="agent")
    def test_agent_mode_has_no_implicit_rule_fallback(self):
        company = self.submit()
        response = self.browser.post(f"/api/v1/companies/{company.pk}/analyze/")
        self.assertEqual(response.data["status"], "pending")
        self.assertEqual(Analysis.objects.count(), 0)
        self.assertEqual(self.browser.post("/api/v1/demo/seed/").status_code, 409)

    # Function: Verify real CSRF protection for anonymous login and authenticated writes.
    # Inputs: An independent browser client with enforce_csrf_checks enabled.
    # Outputs: Missing tokens are rejected; valid tokens permit login and business-mailbox creation.
    # Logic: Disable local automatic sessions, authenticate through real Django passwords, then read the rotated cookie.
    # Constraints: Passwords are fictional; no real accounts.
    @override_settings(LOCAL_DEBUG_AUTO_LOGIN=False)
    def test_session_login_requires_csrf_and_valid_password(self):
        client = APIClient(enforce_csrf_checks=True)
        client.get("/api/v1/session/")
        credentials = {"username": "sales", "password": "test-password-123"}
        self.assertEqual(client.post("/api/v1/session/", credentials, format="json").status_code, 403)
        response = client.post("/api/v1/session/", credentials, format="json", HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["authenticated"])
        self.assertEqual(client.post("/api/v1/mailboxes/", {"address": "new@example.com"}, format="json").status_code, 403)
        self.assertEqual(client.post("/api/v1/mailboxes/", {"address": "new@example.com"}, format="json", HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value).status_code, 201)

    # Function: Verify repeated demo imports preserve mail and timestamps.
    # Inputs: Two consecutive explicit seed operations.
    # Outputs: Four messages first, zero second, with existing contents unchanged.
    # Logic: Deduplicate fixed IDs and compare actual persisted payloads.
    # Constraints: Do not treat samples as research datasets.
    def test_demo_seed_is_repeatable_without_duplicate_data(self):
        first = self.browser.post("/api/v1/demo/seed/")
        self.assertEqual(first.status_code, 200, first.data)
        before = list(Email.objects.order_by("dedupe_key").values_list("payload", flat=True))
        second = self.browser.post("/api/v1/demo/seed/")
        self.assertEqual(second.data["created_emails"], 0)
        self.assertEqual(list(Email.objects.order_by("dedupe_key").values_list("payload", flat=True)), before)

    # Function: Verify forged sources/closing probabilities are rejected while original-text business percentages persist.
    # Inputs: After valid L2, alter sources, insert closing probabilities, and insert payment percentages separately.
    # Outputs: The first two return 400; payment-percentage analysis saves successfully.
    # Logic: Verify through formal Agent HTTP writes.
    # Constraints: No external model calls.
    def test_analysis_rejects_foreign_evidence_and_deal_probability(self):
        company = self.submit()
        job, snapshot, grouping, context = self.prepare(company)
        results.save_input(self.user, snapshot, company.revision, job["job_id"], job["lease_token"])
        headers = {"HTTP_IF_MATCH": str(company.revision), "HTTP_X_JOB_ID": job["job_id"], "HTTP_X_LEASE_TOKEN": job["lease_token"]}
        analysis = rules.generate_analysis(snapshot, grouping, context)
        analysis["detail_view"]["profile"]["intent"]["facts"][0]["source_refs"] = ["foreign-email"]
        self.assertEqual(self.agent.post("/api/v1/agent/analyses/", analysis, format="json", **headers).status_code, 400)
        analysis = rules.generate_analysis(snapshot, grouping, context)
        analysis["detail_view"]["profile"]["intent"]["facts"][0]["text"] = "成交可能 80%"
        self.assertEqual(self.agent.post("/api/v1/agent/analyses/", analysis, format="json", **headers).status_code, 400)
        analysis = rules.generate_analysis(snapshot, grouping, context)
        analysis["detail_view"]["profile"]["intent"]["facts"][0]["text"] = "首付款 30%，验收后支付 60%，剩余 10% 为质保金"
        response = self.agent.post("/api/v1/agent/analyses/", analysis, format="json", **headers)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(Analysis.objects.count(), 1)

    # Function: Verify consolidation neither drops historical facts nor counts facts twice.
    # Inputs: Valid snapshots of stored mail with budget facts removed or duplicated separately.
    # Outputs: Two 400 responses and no persisted snapshot.
    # Logic: Reconcile against the complete service-returned set to prevent partial consolidation posing as complete.
    # Constraints: Modify submitted copies only, not original mail or extracted facts.
    def test_analysis_input_requires_complete_fact_multiset(self):
        company = self.submit()
        job, snapshot, _, _ = self.prepare(company)
        headers = {"HTTP_IF_MATCH": str(company.revision), "HTTP_X_JOB_ID": job["job_id"], "HTTP_X_LEASE_TOKEN": job["lease_token"]}
        omitted = deepcopy(snapshot)
        del omitted["facts"]["budget"]
        duplicated = deepcopy(snapshot)
        duplicated["facts"]["budget"] *= 2
        for payload in [omitted, duplicated]:
            response = self.agent.post("/api/v1/agent/analysis-inputs/", payload, format="json", **headers)
            self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(AnalysisInput.objects.count(), 0)

    # Function: Verify invalid UUIDs produce controlled input errors.
    # Inputs: Queries with invalid company and mailbox identifiers.
    # Outputs: Two 400 responses.
    # Logic: Ensure ORM UUID-conversion exceptions do not escape as debug 500 errors.
    # Constraints: Do not query other users' entities.
    def test_invalid_identifiers_return_400(self):
        self.assertEqual(self.agent.get("/api/v1/agent/grouping/", {"company_id": "invalid"}).status_code, 400)
        self.assertEqual(self.agent.get("/api/v1/agent/sync-state/", {"mailbox_id": "invalid"}).status_code, 400)

    # Function: Verify explicit reanalysis completes scores for partially completed rules tasks.
    # Inputs: An existing successful analysis with its score removed in the test, then requeued.
    # Outputs: Reuse original analysis, add a score, and complete the task normally.
    # Logic: Distinguish analysis-cache hits from complete outputs to prevent permanently missing scores.
    # Constraints: Score deletion only constructs a test boundary; production does not delete or retry automatically.
    def test_explicit_reanalysis_completes_missing_score(self):
        company = self.submit()
        rules.run_company(self.user, company.pk)
        analysis = Analysis.objects.get()
        analysis.scores.all().delete()
        response = self.browser.post(f"/api/v1/companies/{company.pk}/analyze/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(Analysis.objects.count(), 1)
        self.assertTrue(analysis.scores.exists())
