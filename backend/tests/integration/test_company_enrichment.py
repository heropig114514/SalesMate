"""Responsibility: Verify actual HTTP, L2/L3 saves, and invalidation boundaries for cross-account company enrichment.
Implementation: Generate complete experimental fixtures in an isolated database and call interfaces with real Agent credentials; replace only model output.
Relationships: enrichment, results, shared experimental writes, and real DjangoBackendClient; no external model access.
Directory:
- initialize: Create differently owned material sources and analysis targets.
- provider: Construct deterministic model output citing experimental material.
- EnrichmentTests: Verify resolution, saving, and revocation boundaries.
- EnrichmentTests.setUp: Initialize isolated fixtures.
- EnrichmentTests.document: Read context through HTTP and construct L2.
- EnrichmentTests.save: Submit L2 using the current lease.
- EnrichmentTests.test_cross_account_context_and_integrity: Verify no additional Tool credential is needed and tampering is rejected.
- EnrichmentTests.test_ambiguity_and_exact_matching: Verify ambiguity, full-domain matching, and restricted name matching.
- EnrichmentTests.test_crm_priority_and_l3_sources: Verify CRM precedence, experimental scale, and citation allowlists.
- EnrichmentTests.test_source_changes_revoke_cached_results: Verify approved modifications/deletions invalidate old analysis.
- EnrichmentTests.test_integrity_failure_is_explicit: Verify source drift is not treated as no match.
- EnrichmentTests.test_no_match_and_legacy_input: Verify no-match cases and the old protocol remain usable.
- EnrichmentTests.test_batch_resolution_reuses_verified_rows_and_rechecks_changes: Verify bulk equivalence, one manifest read, and revalidation on the next call.
- EnrichmentTests.test_batch_resolution_keeps_owner_only_and_integrity_failure: Verify bulk operations cannot bypass account isolation or integrity errors.
- EnrichmentLiveTests: Agent analysis loop over real networking.
- EnrichmentLiveTests.setUp: Initialize isolated fixtures.
- EnrichmentLiveTests.test_real_worker_client_and_cache: Verify L2/L3/L4 and caching without a Tool token.
Variable index:
- None
"""

import copy
import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from agent.clients.backend_api import DjangoBackendClient
from agent.workflows.customer_analysis import generate_analysis, _size_band
from agent.workflows.orchestration import analyze_company
from apps.crm import results, rules, selectors
from apps.crm.enrichment import resolve, resolve_many
from apps.crm.models import AgentCredential, Analysis, Company, Job
from apps.sales.experiments import APPROVED_BATCHES, load_batch, table_rows
from apps.sales.experiment_writes import mutate
from apps.sales.management.commands.seed_kg_lab import run_seed
from apps.sales.models import AuditEvent
from integrations.company_enrichment import input_version, employee_size


# Function: Create differently owned material sources and analysis targets.
# Inputs: `case` is a Django test instance.
# Outputs: Initialize accounts, target company, seed records, Agent client, and lease headers.
# Logic: Generate two experimental record sets; another ordinary new account creates an empty CRM company with the same full domain.
# Constraints: Temporary files are automatically reclaimed; tokens are test constants only, without ToolCredential.
def initialize(case):
    folder = tempfile.TemporaryDirectory(prefix="enrichment-test-")
    case.addCleanup(folder.cleanup)
    config = override_settings(BASE_DIR=Path(folder.name), LOCAL_DEBUG_AUTO_LOGIN=False)
    config.enable()
    case.addCleanup(config.disable)
    case.owner = get_user_model().objects.create_user(username="seed-owner")
    case.reader = get_user_model().objects.create_user(username="new-algorithm-user")
    case.batch = APPROVED_BATCHES[0]
    run_seed(case.owner, case.batch, 2)
    case.row = table_rows(load_batch(case.batch), "crm.Company")[0]
    case.company = Company.objects.create(owner=case.reader, name="实验待补全公司", group_key="enrichment-target", domains=case.row["fields"]["domains"])
    AgentCredential.objects.create(owner=case.reader, name="enrichment", digest=hashlib.sha256(b"enrichment-test").hexdigest())
    case.api = APIClient()
    case.api.credentials(HTTP_AUTHORIZATION="Agent enrichment-test")
    Job.objects.create(company=case.company, revision=case.company.revision, trigger="external_updated")
    job = case.api.post("/api/v1/agent/jobs/claim/", {"limit": 1, "lease_seconds": 120}, format="json").data[0]
    case.headers = {"HTTP_IF_MATCH": str(case.company.revision), "HTTP_X_JOB_ID": job["job_id"], "HTTP_X_LEASE_TOKEN": job["lease_token"]}


# Function: Construct deterministic model output citing experimental material.
# Inputs: `document` is L2 provided by the real backend.
# Outputs: JSON text for L3 list_view/detail_view.
# Logic: Without mail, signals remain unknown; use supplemental industry sources and deterministic headcount bands.
# Constraints: Replace only the model boundary; do not mock authentication, matching, database, or HTTP.
def provider(document):
    enrichment = document["business_context"]["company_enrichment"]
    refs = [enrichment["source"]["source_id"]] if enrichment["status"] == "matched" else []
    fact = {"text": "虚构实验资料", "source_refs": refs}
    dimension = {"facts": [fact] if refs else [], "inferences": [], "missing_fields": []}
    return json.dumps({"list_view": {
        "signal": "unknown", "signal_evidence": {"text": "没有邮件信号", "source_refs": []}, "ticket_signals": [],
        "industry": enrichment["facts"].get("industry") or "unknown", "industry_evidence": fact,
        "size_band": _size_band(employee_size(document["business_context"])[0]), "size_source": "model-placeholder",
        "headline_summary": "虚构实验公司", "score_features": {name: {"value": None, "basis": "无邮件"} for name in ("demand_clarity", "urgency", "decision_visibility")},
    }, "detail_view": {"conflicts": [], "profile": {name: dimension for name in ("industry_context", "company_ops", "intent")},
        "analysis": {name: dimension for name in ("timeline", "opportunity", "risk", "guidance")},
        "missing_fields": [], "context_completeness": {"unparsed_message_count": 0, "note": None}}}, ensure_ascii=False)


# Function: Verify resolution, saving, and revocation boundaries.
# Logic: Use real HTTP views and a transactional database to construct exact matches and attack counterexamples.
# Constraints: Restrict all writes to the test database.
class EnrichmentTests(TestCase):
    # Function: Verify bulk and independent resolution are identical and read the manifest only once.
    # Inputs: Approved fixtures, matching companies, and unmatched companies in the test instance.
    # Outputs: Complete results are equal, actual table_rows is called once, and the next call returns an integrity error after source changes.
    # Logic: Wrap the real projection function to count calls while retaining database/fingerprint checks; no successful results are mocked.
    # Constraints: Changes occur only in the isolated test database; previous-call results are not cached.
    def test_batch_resolution_reuses_verified_rows_and_rechecks_changes(self):
        other = Company.objects.create(owner=self.reader, group_key="batch-miss", domains=["unmatched.example"])
        companies = [self.company, other]
        expected = {company.pk: resolve(company) for company in companies}
        with patch("apps.crm.enrichment.experiments.table_rows", wraps=table_rows) as project:
            actual = resolve_many(companies)
        self.assertEqual(actual, expected)
        self.assertEqual(project.call_count, 1)
        Company.objects.filter(pk=self.row["pk"]).update(name="changed-source")
        self.assertEqual(resolve_many(companies)[self.company.pk]["reason"], "integrity_error")

    # Function: Verify bulk-resolution isolation and invalidation boundaries.
    # Inputs: Approved manifest, personal-isolation switch, and deleted manifest.
    # Outputs: Personal isolation prevents shared-table reads; missing manifests explicitly return unavailable; empty sets perform no reads.
    # Logic: Retain real table reads and inspect call counts and safe reasons in results.
    # Constraints: Do not convert manifest failures into ordinary no-match results or change runtime defaults.
    def test_batch_resolution_keeps_owner_only_and_integrity_failure(self):
        with self.settings(WORKSPACE_OWNER_ONLY=True), patch("apps.crm.enrichment.experiments.table_rows", wraps=table_rows) as project:
            self.assertEqual(resolve_many([self.company])[self.company.pk]["status"], "not_found")
            self.assertEqual(resolve_many([]), {})
            self.assertEqual(project.call_count, 0)
        AuditEvent.objects.filter(event="kg_synthetic_batch_v1", object_id=self.batch).delete()
        self.assertEqual(resolve_many([self.company])[self.company.pk]["reason"], "batch_unavailable")

    # Function: Initialize isolated fixtures.
    # Inputs: No external arguments; the test instance.
    # Outputs: Instance state established by initialize.
    # Logic: Create independent data for each test.
    # Constraints: Do not reuse production credentials.
    def setUp(self):
        initialize(self)

    # Function: Read context through HTTP and construct L2.
    # Inputs: The instance's api and company.
    # Outputs: (L2 dictionary, company context).
    # Logic: Use original rule-based merging for mail, then bind supplemental material through the formal shared-version function.
    # Constraints: The real Agent builder is covered separately by LiveServer tests.
    def document(self):
        grouping = self.api.get("/api/v1/agent/grouping/", {"company_id": str(self.company.pk)}).data
        response = self.api.get("/api/v1/agent/context/", {"company_id": str(self.company.pk)}, HTTP_IF_MATCH=str(self.company.revision))
        self.assertEqual(response.status_code, 200, response.data)
        context = response.data
        document = rules.build_input(grouping, context)
        document["business_context"]["company_enrichment"] = context["company_enrichment"]
        document["input_version"] = input_version(context["emails"], document["merge_version"], context["external_snapshot_version"], context["company_enrichment"])
        return document, context

    # Function: Submit L2 using the current lease.
    # Inputs: `document` is the snapshot to save.
    # Outputs: Actual HTTP response.
    # Logic: POST with Agent credentials and claim credentials.
    # Constraints: Do not bypass results.save_input.
    def save(self, document):
        return self.api.post("/api/v1/agent/analysis-inputs/", document, format="json", **self.headers)

    # Function: Verify no additional Tool credential is needed and tampering is rejected.
    # Inputs: Cross-account experimental material and an empty CRM target.
    # Outputs: Saving succeeds; forged fields/versions and private-company access fail.
    # Logic: Tamper with each field and recompute versions to exclude false security based only on digest checks.
    # Constraints: Ordinary private data of other employees remains unavailable.
    def test_cross_account_context_and_integrity(self):
        document, context = self.document()
        enrichment = context["company_enrichment"]
        self.assertEqual(enrichment["status"], "matched")
        self.assertEqual(enrichment["source"]["owner"]["id"], self.owner.pk)
        self.assertEqual(self.save(document).status_code, 200)
        for key, value in [("facts", {"employee_count": 999}), ("source", {"source_id": "private"}), ("status", "not_found")]:
            bad = copy.deepcopy(document)
            bad["business_context"]["company_enrichment"][key] = value
            bad["input_version"] = input_version(context["emails"], bad["merge_version"], context["external_snapshot_version"], bad["business_context"]["company_enrichment"])
            self.assertEqual(self.save(bad).status_code, 409)
        bad = copy.deepcopy(document)
        bad["input_version"] = "invented-version"
        self.assertEqual(self.save(bad).status_code, 409)
        response = self.api.get("/api/v1/agent/grouping/", {"company_id": self.row["pk"]})
        self.assertEqual(response.status_code, 404)
        anonymous = APIClient()
        self.assertEqual(anonymous.get("/api/v1/agent/context/", {"company_id": str(self.company.pk)}).status_code, 401)

    # Function: Verify ambiguity, full-domain matching, and restricted name matching.
    # Inputs: A second same-domain candidate, subdomains, and ordinary same-name targets.
    # Outputs: Accurate ambiguous, not_found, and matched states.
    # Logic: Add candidates through public maintenance services without directly corrupting the manifest.
    # Constraints: Do not treat q substrings or ordinary matching company names as exact correspondence.
    def test_ambiguity_and_exact_matching(self):
        self.company.domains = ["sub." + self.row["fields"]["domains"][0]]
        self.assertEqual(resolve(self.company)["status"], "not_found")
        self.company.name = self.row["fields"]["name"]
        self.assertEqual(resolve(self.company)["status"], "not_found")
        self.company.domains = []
        self.assertEqual(resolve(self.company)["match_basis"], "exact_name")
        self.company.domains = self.row["fields"]["domains"]
        mutate(self.reader, "create", self.batch, "crm.Company", data={"group_key": "duplicate-domain", "name": "另一个虚构", "domains": self.company.domains})
        self.assertEqual(resolve(self.company)["status"], "ambiguous")

    # Function: Verify CRM precedence, experimental scale, and citation allowlists.
    # Inputs: Matched L2, unknown CRM headcount, and forged citations.
    # Outputs: L3 experimental scale saves successfully, out-of-scope citations fail, and existing CRM values take precedence.
    # Logic: Run actual Agent validation and backend saving; supplemental material remains snapshot-only.
    # Constraints: Do not evaluate real-model generation quality.
    def test_crm_priority_and_l3_sources(self):
        document, _ = self.document()
        self.assertEqual(self.save(document).status_code, 200)
        analysis = generate_analysis(document, analysis_provider=provider, clock=timezone.now)
        self.assertEqual(analysis["status"], "completed", analysis)
        self.assertEqual(analysis["list_view"]["size_source"], "synthetic_sample")
        forged = copy.deepcopy(analysis)
        forged["list_view"]["industry_evidence"]["source_refs"] = ["experiment:unapproved"]
        self.assertEqual(self.api.post("/api/v1/agent/analyses/", forged, format="json", **self.headers).status_code, 400)
        response = self.api.post("/api/v1/agent/analyses/", analysis, format="json", **self.headers)
        self.assertEqual(response.status_code, 200, response.data)
        self.company.refresh_from_db()
        self.assertEqual(self.company.customer, {})
        document["business_context"]["customer"].update(employee_count=800, employee_count_source="crm")
        analysis = generate_analysis(document, analysis_provider=provider, clock=timezone.now)
        self.assertEqual(analysis["list_view"]["size_band"], "gte_500")
        self.assertEqual(analysis["list_view"]["size_source"], "crm")

    # Function: Verify approved modifications/deletions invalidate old analysis.
    # Inputs: Saved L2/L3 and legitimate experimental modifications.
    # Outputs: Cache miss, empty current results, rejection of further saves against old snapshots, and a changed new version.
    # Logic: After normal manifest maintenance, test every current-result entry point; remove the batch manifest to simulate revocation.
    # Constraints: Retain history without implicitly calling the model again.
    def test_source_changes_revoke_cached_results(self):
        document, _ = self.document()
        self.save(document)
        analysis = generate_analysis(document, analysis_provider=provider, clock=timezone.now)
        self.assertEqual(self.api.post("/api/v1/agent/analyses/", analysis, format="json", **self.headers).status_code, 200)
        self.assertTrue(results.cached_analysis(self.company, document["input_version"])["hit"])
        mutate(self.reader, "update", self.batch, "crm.Company", pk=self.row["pk"], expected=self.row["fingerprint"], data={"customer": {"employee_count": 301, "industry_from_crm": "工业检测"}})
        self.assertEqual(self.save(document).status_code, 409)
        self.assertFalse(results.cached_analysis(self.company, document["input_version"])["hit"])
        self.assertEqual(selectors.latest_result(self.company), (None, None))
        self.assertEqual(self.api.get("/api/v1/agent/latest-analysis-input/", {"company_id": str(self.company.pk)}).status_code, 404)
        self.assertEqual(self.api.post("/api/v1/agent/analyses/", analysis, format="json", **self.headers).status_code, 409)
        updated, _ = self.document()
        self.assertNotEqual(updated["input_version"], document["input_version"])
        AuditEvent.objects.filter(event="kg_synthetic_batch_v1", object_id=self.batch).delete()
        self.assertEqual(resolve(self.company)["status"], "unavailable")
        self.assertEqual(self.save(updated).status_code, 409)
        with patch("apps.sales.experiments.APPROVED_BATCHES", ()):
            self.assertEqual(selectors.latest_result(self.company), (None, None))

    # Function: Verify source drift is not treated as no match.
    # Inputs: Experimental-record modifications bypassing the formal maintenance entry point.
    # Outputs: Explicit integrity_error with no supplemental facts; L2 can still save that state.
    # Logic: Actual fingerprint checks reject corrupted data without blocking completed L1.
    # Constraints: Do not swallow unknown exceptions or fabricate sources.
    def test_integrity_failure_is_explicit(self):
        Company.objects.filter(pk=self.row["pk"]).update(name="untracked edit")
        document, context = self.document()
        self.assertEqual(context["company_enrichment"]["reason"], "integrity_error")
        self.assertEqual(context["company_enrichment"]["facts"], {})
        self.assertEqual(self.save(document).status_code, 200)

    # Function: Verify no-match cases and the old protocol remain usable.
    # Inputs: Ordinary company domains and old clients omitting supplemental fields.
    # Outputs: No incorrect enrichment; both inputs can be saved.
    # Logic: The old protocol retains original version rules; the new protocol records not_found.
    # Constraints: Do not interpret no match as absence of other private companies.
    def test_no_match_and_legacy_input(self):
        self.company.domains = ["ordinary.example"]
        self.company.save(update_fields=["domains"])
        document, context = self.document()
        self.assertEqual(context["company_enrichment"]["status"], "not_found")
        self.assertEqual(self.save(document).status_code, 200)
        del document["business_context"]["company_enrichment"]
        document["input_version"] = "legacy-input"
        self.assertEqual(self.save(document).status_code, 200)


# Function: Agent analysis loop over real networking.
# Logic: LiveServer and the real client share isolated PostgreSQL, replacing only model output.
# Constraints: Use no external models or production database.
class EnrichmentLiveTests(LiveServerTestCase):
    # Function: Initialize isolated fixtures.
    # Inputs: No external arguments; the test instance.
    # Outputs: Instance state established by initialize.
    # Logic: Commit real data for the server thread to read.
    # Constraints: The framework cleans the test database.
    def setUp(self):
        initialize(self)

    # Function: Verify L2/L3/L4 and caching without a Tool token.
    # Inputs: Real Agent token, HTTP service address, and deterministic provider.
    # Outputs: Profile completes, a repeated run hits cache, L1 facts remain empty, and CRM is not contaminated.
    # Logic: After requeuing, DjangoBackendClient claims the lease and runs actual analyze_company.
    # Constraints: Do not treat a model substitute as real LLM evaluation.
    def test_real_worker_client_and_cache(self):
        Job.objects.filter(company=self.company).update(status="pending")
        backend = DjangoBackendClient(self.live_server_url + "/api/v1/agent/", "enrichment-test")
        self.addCleanup(backend.close)
        backend.claim_jobs(1)
        first = analyze_company(str(self.company.pk), backend=backend, analysis_provider=provider, clock=timezone.now)
        self.assertEqual(first["status"], "completed", first)
        self.assertFalse(first["cache_hit"])
        second = analyze_company(str(self.company.pk), backend=backend, analysis_provider=provider, clock=timezone.now)
        self.assertTrue(second["cache_hit"], second)
        self.assertEqual(Analysis.objects.filter(snapshot__company=self.company).count(), 1)
        self.assertFalse(any(first["analysis_input"]["facts"].values()))
        self.assertEqual(first["analysis"]["list_view"]["size_source"], "synthetic_sample")
