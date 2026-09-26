"""Responsibility: Verify analysis-conflict reasons and the explicit latest-version rebuild flow.
Implementation: Use synthetic email, real PostgreSQL transactions, and the DRF API; rules generate test payloads only and no model is invoked.
Relationships: Covers `results`, `jobs`, `analysis_errors`, and existing detail projections; does not add Agent state.
Directory:
- AnalysisConflictTests: Analysis-concurrency contract tests.
- AnalysisConflictTests.setUp: Create a claimed job and input.
- AnalysisConflictTests.post_analysis: Submit through the real Agent route.
- AnalysisConflictTests.test_revision_conflict_and_explicit_successor: Reject old results, retain readable errors, and deduplicate new jobs.
- AnalysisConflictTests.test_lease_reasons_remain_409: Distinguish invalid, expired, and finished jobs.
- AnalysisConflictTests.test_immutable_payload_conflict: Prevent different payloads with the same key from overwriting.
- AnalysisConflictTests.test_missing_snapshot_is_not_model_error: Attribute a missing snapshot separately.
- AnalysisConflictTests.test_analysis_v5_aliases_persist_as_full_sources: Ensure normalized new Agent output is compatible with the real backend.
Variable index:
- None
"""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import uuid
from unittest.mock import Mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.crm import ingestion, jobs, results, rules, selectors
from apps.crm.models import AgentCredential, Analysis, Company, Job, Mailbox
from agent.tests.test_mvp_pipeline import _payload
from agent.workflows.customer_analysis import _source_aliases, generate_analysis


# Function: Verify version conflicts and safe rebuild during job execution.
# Logic: Use production authorization and actual persistence without an external model.
# Constraints: Successful samples are rule-constructed and do not represent real L3 effectiveness.
@override_settings(ANALYSIS_PROVIDER="agent", LAB_OPEN_ACCESS=False)
class AnalysisConflictTests(TestCase):
    # Function: Create independent business input and a valid lease.
    # Inputs: Test database and fixed synthetic content, with no external parameters.
    # Outputs: User, clients, company, job, and L2/L3 fixtures.
    # Logic: Use real ingestion, claiming, and snapshot services.
    # Constraints: Rule methods generate payloads only and do not enable production rule fallback.
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="conflict-owner")
        self.mailbox = Mailbox.objects.create(owner=self.owner, address="sales@conflict.example")
        AgentCredential.objects.create(owner=self.owner, digest=hashlib.sha256(b"conflict-test").hexdigest())
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION="Agent conflict-test")
        self.browser = APIClient()
        self.browser.force_authenticate(self.owner)
        payload = rules.extract_email(self.mailbox, "buyer@customer.example", "询价", "需求：设备\n数量：3 台", "one")
        ingestion.submit_emails(self.owner, [payload])
        self.company = Company.objects.get(owner=self.owner)
        self.job = jobs.claim(self.owner, 1, 120)[0]
        self.snapshot = rules.build_input(*selectors.context_pair(self.company))
        results.save_input(self.owner, self.snapshot, self.company.revision, self.job["job_id"], self.job["lease_token"])
        self.analysis = rules.generate_analysis(self.snapshot, *selectors.context_pair(self.company))

    # Function: Submit a real L3 HTTP request.
    # Inputs: `payload` is optional analysis and `token` is an optional lease; defaults use current fixtures.
    # Outputs: A DRF response.
    # Logic: Retain If-Match and job headers without mocking the exception handler.
    # Constraints: Use only the test client and make no network request.
    def post_analysis(self, payload=None, token=None):
        return self.client.post("/api/v1/agent/analyses/", payload or self.analysis, format="json", HTTP_IF_MATCH=str(self.company.revision), HTTP_X_JOB_ID=self.job["job_id"], HTTP_X_LEASE_TOKEN=token or self.job["lease_token"])

    # Function: Verify that only an explicit request creates a latest job after old analysis fails.
    # Inputs: A synthetic concurrent event updates the company after L3 begins.
    # Outputs: A 409 reason, no overwriting of the new version, readable error text, and deduplicated repeated clicks for the new job.
    # Logic: Retain the old-job report and successor job, then build current input after claiming again.
    # Constraints: Do not automatically retry old analysis or change job-status enums.
    def test_revision_conflict_and_explicit_successor(self):
        Company.objects.filter(pk=self.company.pk).update(revision=self.company.revision + 1)
        response = self.post_analysis()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "analysis_revision_changed")
        self.assertFalse(Analysis.objects.exists())
        message = str(response.data["error"]["detail"]["detail"])
        jobs.report(self.owner, {"job_id": self.job["job_id"], "status": "failed", "input_version": None, "produced": {"analysis": False, "score": False}, "error": {"code": "job_failed", "message": message}, "duration_ms": 1}, self.job["lease_token"])
        self.company.refresh_from_db()
        self.assertIn("资料在分析期间更新", selectors.company_row(self.company)["job_error"]["message"])
        first = self.browser.post(f"/api/v1/companies/{self.company.pk}/analyze/")
        second = self.browser.post(f"/api/v1/companies/{self.company.pk}/analyze/")
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(first.data["job_id"], second.data["job_id"])
        next_job = jobs.claim(self.owner, 1, 120)[0]
        self.assertEqual(next_job["expected_version"], self.company.revision)
        snapshot = rules.build_input(*selectors.context_pair(self.company))
        results.save_input(self.owner, snapshot, self.company.revision, next_job["job_id"], next_job["lease_token"])
        results.save_analysis(self.owner, rules.generate_analysis(snapshot, *selectors.context_pair(self.company)), self.company.revision, next_job["job_id"], next_job["lease_token"])
        self.assertEqual(Analysis.objects.get().snapshot.revision, self.company.revision)

    # Function: Distinguish invalid credentials, expired credentials, and finished jobs.
    # Inputs: The original lease and explicitly modified test states.
    # Outputs: A fixed error code for each condition and HTTP 409 throughout.
    # Logic: Send real HTTP requests one by one; an invalid credential does not expose job state.
    # Constraints: Do not renew, claim again, or save analysis.
    def test_lease_reasons_remain_409(self):
        response = self.post_analysis(token=str(uuid.uuid4()))
        self.assertEqual((response.status_code, response.data["error"]["code"]), (409, "analysis_lease_invalid"))
        Job.objects.filter(pk=self.job["job_id"]).update(lease_until=timezone.now() - timedelta(seconds=1))
        response = self.post_analysis()
        self.assertEqual((response.status_code, response.data["error"]["code"]), (409, "analysis_lease_expired"))
        Job.objects.filter(pk=self.job["job_id"]).update(status="failed")
        response = self.post_analysis()
        self.assertEqual((response.status_code, response.data["error"]["code"]), (409, "analysis_job_inactive"))
        self.assertFalse(Analysis.objects.exists())

    # Function: Prevent a different payload from overwriting saved analysis.
    # Inputs: Two different valid payloads with the same version and prompt.
    # Outputs: `analysis_result_conflict` and the original payload remains.
    # Logic: Change generated time while retaining all other facts and contracts.
    # Constraints: Idempotent identical payloads still succeed.
    def test_immutable_payload_conflict(self):
        self.assertEqual(self.post_analysis().status_code, 200)
        self.assertEqual(self.post_analysis().status_code, 200)
        changed = deepcopy(self.analysis)
        changed["generated_at"] = (timezone.now() + timedelta(seconds=1)).isoformat()
        response = self.post_analysis(changed)
        self.assertEqual((response.status_code, response.data["error"]["code"]), (409, "analysis_result_conflict"))
        self.assertEqual(Analysis.objects.count(), 1)

    # Function: Distinguish a missing input snapshot from model-field validation.
    # Inputs: A valid analysis payload and an unsaved input key.
    # Outputs: `analysis_snapshot_changed` and no saved result.
    # Logic: Change only the input key while retaining validity of other fields.
    # Constraints: Do not create a snapshot for the client or bypass validation.
    def test_missing_snapshot_is_not_model_error(self):
        changed = deepcopy(self.analysis)
        changed["input_version"] = "missing-input"
        response = self.post_analysis(changed)
        self.assertEqual((response.status_code, response.data["error"]["code"]), (409, "analysis_snapshot_changed"))

    # Function: Verify that fetched `analysis-v5` is actually compatible with the backend save contract.
    # Inputs: Real L2 and synthetic model output containing short source identifiers and incorrect completeness fields.
    # Outputs: After Agent normalization to full sources and actual counts, the backend saves v5 analysis successfully.
    # Logic: Mock model text generation only; Agent normalization, validation, DRF validation, and database writes execute real code.
    # Constraints: Do not call a real model or claim that historical customer analysis was restored.
    def test_analysis_v5_aliases_persist_as_full_sources(self):
        payload = _payload(self.snapshot)
        source = self.snapshot["member_dedupe_keys"][0]
        alias = next(key for key, value in _source_aliases(self.snapshot).items() if value == source)
        payload["detail_view"]["profile"]["intent"]["facts"][0]["source_refs"] = [alias]
        payload["detail_view"]["context_completeness"] = {"note": [], "unparsed_message_count": 999}
        provider = Mock(return_value=json.dumps(payload))
        analysis = generate_analysis(self.snapshot, analysis_provider=provider, clock=timezone.now)
        self.assertEqual(analysis["status"], "completed", analysis.get("error"))
        response = self.post_analysis(analysis)
        self.assertEqual(response.status_code, 200, response.data)
        saved = Analysis.objects.get().payload
        self.assertEqual(saved["analysis_prompt_version"], "analysis-v5")
        self.assertEqual(saved["detail_view"]["profile"]["intent"]["facts"][0]["source_refs"], [source])
        self.assertEqual(saved["detail_view"]["context_completeness"], {"unparsed_message_count": 0, "note": None})
        provider.assert_called_once()
