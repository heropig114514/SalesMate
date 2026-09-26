"""Responsibility: Verify backend data semantics, permissions, version triggers, and save contracts for formal L4 scoring.
Implementation: Use isolated PostgreSQL, HTTP clients, and synthetic business records; real Agent rules consume only fixed structured signals.
Relationships: Cover sales.priority, seller interfaces, opportunity transactions, and CRM score saving; no mail or model services.
Directory:
- PriorityTests: Backend regression tests for formal scoring.
- PriorityTests.setUp: Prepare two owners, companies, and synthetic products.
- PriorityTests.order: Create synthetic orders with explicit states and amounts.
- PriorityTests.opportunity: Create synthetic opportunities with explicit products and amounts.
- PriorityTests.prepare_score: Save current L2/L3 over real HTTP and prepare formal L4 results.
- PriorityTests.test_context_aggregates_same_currency_and_isolates_owner: Verify authoritative mappings and statistical isolation.
- PriorityTests.test_incomplete_and_mixed_currency_deals_remain_unknown: Verify incomplete data produces no definite amount.
- PriorityTests.test_similarity_requires_complete_evidence: Verify unknown and confirmed mismatch remain distinct.
- PriorityTests.test_profile_api_validates_versions_and_fans_out: Verify profile permissions, versions, validation, and task propagation.
- PriorityTests.test_opportunity_mutations_refresh_current_company: Verify opportunity creation, editing, status, and archival triggers.
- PriorityTests.test_order_and_product_changes_refresh_other_companies: Verify propagation of shared statistics and catalog changes.
- PriorityTests.test_formal_score_persists_without_legacy_features: Verify new scores/explanations do not depend on old L3 features.
- PriorityTests.test_provisional_score_with_empty_details_persists: Verify real provisional scores and empty explanations can be saved and read.
- PriorityTests.test_invalid_explanations_and_sources_are_rejected: Verify invalid contributions and out-of-scope original-text evidence are rejected.
- PriorityTests.test_null_score_and_stale_lease_contract: Verify null-score semantics and rejection of obsolete-version writes.
- PriorityTests.test_formal_sorting_and_legacy_score_visibility: Verify formal-version presentation and tie ordering.
Variable index:
- None
"""

from copy import deepcopy
from decimal import Decimal
import hashlib
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from agent.workflows.lead_score import compute_priority_result
from apps.crm import jobs, rules, selectors
from apps.crm.models import AgentCredential, Analysis, AnalysisInput, Company, Mailbox, Score
from apps.sales.models import Opportunity, OrderLine, Product, SalesOrder, SellerProfile
from apps.sales.priority import priority_context, similar_won
from apps.sales.services import transition_record


# Function: Backend regression tests for formal scoring.
# Logic: Each test uses independent database transactions with same-owner/cross-owner controls, fixed to formal agent mode.
# Constraints: No model, Gmail, or production-database calls; directly created model fixtures do not verify browser state transitions.
@override_settings(ANALYSIS_PROVIDER="agent")
class PriorityTests(TestCase):
    # Function: Prepare two owners, companies, and synthetic products.
    # Inputs: No external arguments; the test framework initializes the isolated database.
    # Outputs: Initialize identities, customers, API clients, and catalog products.
    # Logic: Two same-owner companies verify dependency propagation; another owner provides unauthorized-access controls.
    # Constraints: Fixed tokens are test-only and all emails are synthetic.
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="priority-owner")
        self.other = get_user_model().objects.create_user(username="priority-other")
        self.company = Company.objects.create(owner=self.owner, group_key="domain:buyer.example", name="Buyer",
                                              customer={"industry_from_crm": "Manufacturing", "employee_count": 100, "country": "Singapore"})
        self.peer = Company.objects.create(owner=self.owner, group_key="domain:history.example", name="History",
                                           customer={"industry_from_crm": "Manufacturing"})
        self.foreign = Company.objects.create(owner=self.other, group_key="domain:foreign.example")
        self.product = Product.objects.create(owner=self.owner, sku="WMS", name="WMS", currency="SGD", unit_price="100")
        SellerProfile.objects.create(owner=self.owner, profile={"target_industries": ["Manufacturing"],
                                     "target_company_size": {"min": 20, "max": 500}, "service_regions": ["Singapore"],
                                     "time_zone": "Asia/Singapore"})
        self.browser = APIClient()
        self.browser.force_authenticate(self.owner)
        AgentCredential.objects.create(owner=self.owner, name="priority-test", digest=hashlib.sha256(b"priority-test-token").hexdigest())
        self.agent = APIClient()
        self.agent.credentials(HTTP_AUTHORIZATION="Agent priority-test-token")
        self.mailbox = Mailbox.objects.create(owner=self.owner, address="sales@internal.example")

    # Function: Create synthetic orders with explicit states and amounts.
    # Inputs: `amount` is line net amount, `currency` is currency, `company` optionally selects the customer, and `status` defaults to confirmed.
    # Outputs: SalesOrder instance.
    # Logic: Create one quantity-one line so statistics counting each order once can be checked directly.
    # Constraints: Direct database setup does not replace order HTTP state-transition tests.
    def order(self, amount="30000", currency="SGD", company=None, status="confirmed"):
        company = company or self.peer
        order = SalesOrder.objects.create(owner=company.owner, company=company, number=f"order-{SalesOrder.objects.count()}",
                                         currency=currency, status=status, confirmed_at=timezone.now() if status != "draft" else None)
        OrderLine.objects.create(owner=company.owner, order=order, product=self.product if company.owner_id == self.owner.pk else None,
                                 description="WMS", quantity=1, unit_price=amount)
        return order

    # Function: Create synthetic opportunities with explicit products and amounts.
    # Inputs: `amount` is nullable and `currency` identifies the currency.
    # Outputs: Opportunity instance.
    # Logic: Create a proposal-stage opportunity for the current customer and explicitly link canonical product names.
    # Constraints: Prepare data only without simulating state-transition side effects.
    def opportunity(self, amount="250000", currency="SGD"):
        return Opportunity.objects.create(owner=self.owner, company=self.company, title="Warehouse", status="proposal",
                                          amount=amount, currency=currency, product_names=["WMS"])

    # Function: Save current L2/L3 over real HTTP and prepare formal L4 results.
    # Inputs: No external arguments; use the test customer, mailbox, and synthetic authoritative records.
    # Outputs: Formal score payload, valid lease headers, and corresponding Analysis.
    # Logic: Ingest synthetic inquiry mail, claim a task, save L2/L3, clear old L3 features, and invoke real rules to generate a new result.
    # Constraints: Structured signals are fixed without model access; verify the backend contract, not signal-extraction accuracy.
    def prepare_score(self):
        self.opportunity()
        self.order()
        email = rules.extract_email(self.mailbox, "buyer@buyer.example", "设备询价", "需求：采购设备\n数量：12 台", "priority-one")
        response = self.agent.post("/api/v1/agent/emails/", [email], format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.company.refresh_from_db()
        job = jobs.claim(self.owner, 1, 120, company_id=self.company.pk)[0]
        grouping, context = selectors.context_pair(self.company)
        snapshot = rules.build_input(grouping, context)
        headers = {"HTTP_IF_MATCH": str(self.company.revision), "HTTP_X_JOB_ID": job["job_id"], "HTTP_X_LEASE_TOKEN": job["lease_token"]}
        response = self.agent.post("/api/v1/agent/analysis-inputs/", snapshot, format="json", **headers)
        self.assertEqual(response.status_code, 200, response.data)
        analysis = rules.generate_analysis(snapshot, grouping, context)
        analysis["list_view"]["signal"] = "unknown"
        for feature in analysis["list_view"]["score_features"].values():
            feature["value"] = None
        response = self.agent.post("/api/v1/agent/analyses/", analysis, format="json", **headers)
        self.assertEqual(response.status_code, 200, response.data)
        context["priority_context"]["signals"] = [{"type": "FORMAL_QUOTATION_REQUEST", "value": None,
                                                   "confidence": 0.9, "evidence": "设备询价", "source_id": email["dedupe_key"]}]
        output = compute_priority_result(analysis, snapshot, clock=timezone.now, priority_context=context["priority_context"])
        score = {**output["score"], "score_details": output["details"]}
        self.assertIsNotNone(score["score"])
        return score, headers, Analysis.objects.get(snapshot__company=self.company)

    # Function: Verify authoritative mappings and statistical isolation.
    # Inputs: Multiple same-currency opportunities, two valid orders, and draft/foreign-owner controls.
    # Outputs: Total amount, per-order mean, and matching conclusions follow the confirmed definitions.
    # Logic: Read real Agent context over HTTP and check profiles and ETag.
    # Constraints: Exclude drafts and other owners' orders from samples.
    def test_context_aggregates_same_currency_and_isolates_owner(self):
        self.opportunity("100000")
        self.opportunity("150000")
        self.order("20000")
        self.order("40000")
        self.order("999999", status="draft")
        self.order("999999", company=self.foreign)
        response = self.agent.get("/api/v1/agent/context/", {"company_id": str(self.company.pk)}, HTTP_IF_MATCH="0")
        self.assertEqual(response.status_code, 200, response.data)
        context = response.data["priority_context"]
        self.assertEqual(context["customer"]["company_size"], 100)
        self.assertEqual(context["deal"]["deal_value"], "250000.00")
        self.assertEqual(Decimal(context["seller"]["average_deal_value"]), Decimal("30000"))
        self.assertTrue(context["seller"]["similar_won_deals"])
        self.assertNotIn("communications", context)
        self.assertEqual(self.agent.get("/api/v1/agent/context/", {"company_id": str(self.foreign.pk)}).status_code, 404)

    # Function: Verify incomplete data produces no definite amount.
    # Inputs: Mixed currencies, unknown amounts, absent products, and inactive opportunities.
    # Outputs: No definite amount or absent matching fields are produced.
    # Logic: Change inputs individually and inspect authoritative context without fabricating defaults.
    # Constraints: Direct database changes only construct boundary cases.
    def test_incomplete_and_mixed_currency_deals_remain_unknown(self):
        first = self.opportunity("100")
        second = self.opportunity("100", "USD")
        self.assertNotIn("deal_value", priority_context(self.company)["deal"])
        second.currency, second.amount = "SGD", None
        second.save()
        self.assertNotIn("deal_value", priority_context(self.company)["deal"])
        second.status = "lost"
        second.save()
        self.assertEqual(priority_context(self.company)["deal"]["deal_value"], "100.00")
        first.product_names = []
        first.save()
        self.assertNotIn("product", priority_context(self.company)["deal"])
        self.assertNotIn("average_deal_value", priority_context(self.company)["seller"])

    # Function: Verify unknown and confirmed mismatch remain distinct.
    # Inputs: Historical samples with complete/missing industry-product data and confirmed matches.
    # Outputs: Return False, None, and True respectively.
    # Logic: Without reliable positive evidence, only complete samples support a negative conclusion.
    # Constraints: Use neither similarity thresholds nor model inference.
    def test_similarity_requires_complete_evidence(self):
        customer, deal = {"industry": "Manufacturing"}, {"product": ["WMS"]}
        self.assertIsNone(similar_won(customer, deal, []))
        self.assertFalse(similar_won(customer, deal, [{"industry": "Retail", "products": ["WMS"]}]))
        self.assertIsNone(similar_won(customer, deal, [{"industry": None, "products": ["WMS"]}]))
        self.assertTrue(similar_won(customer, deal, [{"industry": "manufacturing", "products": ["wms"]}]))

    # Function: Verify profile permissions, versions, validation, and task propagation.
    # Inputs: Current-owner profile updates, unauthorized fields, invalid timezones, and stale-version requests.
    # Outputs: Same-owner companies advance versions and queue work; other owners are unaffected and invalid requests have no side effects.
    # Logic: Use Session APIs and inspect database versions, profiles, and jobs.
    # Constraints: force_authenticate isolates authentication only and does not replace dedicated CSRF tests.
    def test_profile_api_validates_versions_and_fans_out(self):
        path = "/api/v1/sales/seller-profile/"
        response = self.browser.patch(path, {"service_regions": ["Singapore", "Malaysia"]}, format="json", HTTP_IF_MATCH="0")
        self.assertEqual(response.status_code, 200, response.data)
        for company in (self.company, self.peer):
            company.refresh_from_db()
            self.assertEqual((company.revision, company.external_version), (1, 1))
            self.assertEqual(company.jobs.get().trigger, "external_updated")
        self.foreign.refresh_from_db()
        self.assertEqual(self.foreign.revision, 0)
        self.assertEqual(self.browser.patch(path, {}, format="json", HTTP_IF_MATCH="0").status_code, 409)
        for data in ({"owner": self.other.pk}, {"time_zone": "Invalid/Zone"}, {"target_company_size": {"min": 10, "max": 1}}):
            self.assertEqual(self.browser.patch(path, data, format="json", HTTP_IF_MATCH="1").status_code, 400)
        self.assertEqual(SellerProfile.objects.get(owner=self.owner).revision, 1)
        self.browser.force_authenticate(self.other)
        self.assertEqual(self.browser.get(path).data, {"revision": 0, "profile": {}})

    # Function: Verify opportunity creation, editing, status, and archival triggers.
    # Inputs: Opportunity HTTP creation, versioned editing, state commands, and archival commands.
    # Outputs: The current company's version increments each time while other companies remain unchanged.
    # Logic: Use actual sales resource entry points and verify opportunity products enter scoring context.
    # Constraints: No frontend-form dependency; backend permissions and transactions still execute.
    def test_opportunity_mutations_refresh_current_company(self):
        response = self.browser.post("/api/v1/sales/records/opportunities/", {"company": str(self.company.pk), "title": "New",
                                     "currency": "SGD", "amount": "1000", "product_names": ["WMS"]}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        opportunity = Opportunity.objects.get()
        path = f"/api/v1/sales/records/opportunities/{opportunity.pk}/"
        response = self.browser.patch(path, {"amount": "2000"}, format="json", HTTP_IF_MATCH="0")
        self.assertEqual(response.status_code, 200, response.data)
        opportunity.refresh_from_db()
        transition_record(opportunity, self.owner, opportunity.revision, "qualified")
        opportunity.refresh_from_db()
        from apps.sales.services import archive_record
        archive_record(opportunity, self.owner, opportunity.revision, True)
        self.company.refresh_from_db()
        self.peer.refresh_from_db()
        self.assertEqual(self.company.external_version, 4)
        self.assertEqual(self.peer.external_version, 0)
        self.assertEqual(priority_context(self.company)["deal"], {})

    # Function: Verify propagation of shared statistics and catalog changes.
    # Inputs: Draft-order confirmation and catalog-product renaming.
    # Outputs: Other same-owner companies update versions and pending work; other owners remain unaffected.
    # Logic: Confirm through the real business service, then update products over HTTP; unclaimed jobs merge the latest version.
    # Constraints: Do not start Workers or models.
    def test_order_and_product_changes_refresh_other_companies(self):
        order = self.order(status="draft")
        transition_record(order, self.owner, 0, "confirmed")
        self.company.refresh_from_db()
        self.assertEqual(self.company.external_version, 1)
        response = self.browser.patch(f"/api/v1/sales/records/products/{self.product.pk}/", {"name": "WMS Next"}, format="json", HTTP_IF_MATCH="0")
        self.assertEqual(response.status_code, 200, response.data)
        self.company.refresh_from_db()
        self.assertEqual(self.company.external_version, 2)
        self.assertEqual(self.company.jobs.filter(status="pending").count(), 1)
        self.foreign.refresh_from_db()
        self.assertEqual(self.foreign.external_version, 0)

    # Function: Verify new scores/explanations do not depend on old L3 features.
    # Inputs: Real rule results with empty old features and complete formal context.
    # Outputs: Scores/explanations save atomically, repeated requests are idempotent, and details expose explanations and mail.
    # Logic: Submit twice through scores API and check database counts and company details.
    # Constraints: Automatic submission of score_details by the real Agent is not yet verified; this test attaches it explicitly.
    def test_formal_score_persists_without_legacy_features(self):
        score, headers, analysis = self.prepare_score()
        for _ in range(2):
            response = self.agent.post("/api/v1/agent/scores/", score, format="json", **headers)
            self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(analysis.scores.count(), 1)
        detail = self.browser.get(f"/api/v1/companies/{self.company.pk}/").data
        self.assertEqual(detail["score_detail"]["score_details"], score["score_details"])
        self.assertEqual(detail["score_version"], "score-v2")
        self.assertIn(score["score_details"]["evidence"][0]["source_id"], detail["grouping"]["member_dedupe_keys"])

    # Function: Verify provisional scores generated by the Agent without business material can persist unchanged.
    # Inputs: Successful prepare_score analysis and fixed procurement signals, with all business context explicitly removed.
    # Outputs: Non-null scores with completely empty explanations save; partially empty explanations still return 400.
    # Logic: Invoke real Agent rules, save over lease-authenticated HTTP, and read details.
    # Constraints: Construct isolated samples only; no model calls or changes to weights/production scoring.
    def test_provisional_score_with_empty_details_persists(self):
        _, headers, analysis = self.prepare_score()
        email = self.company.emails.first()
        output = compute_priority_result(analysis.payload, analysis.snapshot.payload, clock=timezone.now,
            priority_context={"signals": [{"type": "L2 Interested", "value": None, "confidence": 1.0,
                                           "evidence": "设备询价", "source_id": email.pk}]})
        score = {**output["score"], "score_details": output["details"]}
        self.assertIsNotNone(score["score"])
        self.assertIsNone(score["score_details"]["score_breakdown"])
        response = self.agent.post("/api/v1/agent/scores/", score, format="json", **headers)
        self.assertEqual(response.status_code, 200, response.data)
        stored = Score.objects.get(analysis=analysis).payload
        # DRF normalizes datetime timezone representation; score values, reasons, and explanation contents must remain unchanged.
        self.assertEqual({key: value for key, value in stored.items() if key != "scored_at"},
                         {key: value for key, value in score.items() if key != "scored_at"})
        detail = self.browser.get(f"/api/v1/companies/{self.company.pk}/")
        self.assertEqual(detail.data["score_detail"]["score_details"], output["details"])
        broken = deepcopy(score)
        broken["score_details"]["recommended_next_action"] = "伪造完整建议"
        self.assertEqual(self.agent.post("/api/v1/agent/scores/", broken, format="json", **headers).status_code, 400)

    # Function: Verify invalid contributions and out-of-scope original-text evidence are rejected.
    # Inputs: Noninteger contributions, duplicate features, inconsistent explanation totals, cross-company citations, and forged original text.
    # Outputs: Every invalid payload returns 400 without creating Score.
    # Logic: Copy valid rule results and violate one contract requirement at a time.
    # Constraints: Do not change scoring rules to accommodate tests.
    def test_invalid_explanations_and_sources_are_rejected(self):
        score, headers, _ = self.prepare_score()
        candidates = []
        bad = deepcopy(score)
        bad["score_reasons"][0]["contribution"] = float(bad["score_reasons"][0]["contribution"])
        candidates.append(bad)
        bad = deepcopy(score)
        bad["score_reasons"][0]["feature"] = "buying_intent"
        candidates.append(bad)
        bad = deepcopy(score)
        bad["score_details"]["score_breakdown"]["contributions"]["urgency"] += 1
        candidates.append(bad)
        for source, evidence in (("foreign:mail", "设备询价"), (score["score_details"]["evidence"][0]["source_id"], "不存在的原文")):
            bad = deepcopy(score)
            reason = next(item for item in bad["score_details"]["top_reasons"] if item["source_id"] is not None)
            reason["source_id"], reason["evidence"] = source, evidence
            bad["score_details"]["evidence"] = [{"source_id": source, "text": evidence}]
            candidates.append(bad)
        for candidate in candidates:
            response = self.agent.post("/api/v1/agent/scores/", candidate, format="json", **headers)
            self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(Score.objects.count(), 0)

    # Function: Verify null-score semantics and rejection of obsolete-version writes.
    # Inputs: Valid null scores, null scores with definite explanations, and obsolete company versions.
    # Outputs: Valid null scores save; other requests are rejected without changing saved results.
    # Logic: Save a null score, then update the profile to invalidate the original lease version.
    # Constraints: Do not treat null as zero or retry implicitly.
    def test_null_score_and_stale_lease_contract(self):
        score, headers, _ = self.prepare_score()
        score.update(score=None, score_reasons=[{"feature": "insufficient_data", "contribution": 0, "note": "资料不足"}],
                     score_details={"score_breakdown": None, "top_reasons": [], "evidence": [], "recommended_next_action": None})
        response = self.agent.post("/api/v1/agent/scores/", score, format="json", **headers)
        self.assertEqual(response.status_code, 200, response.data)
        invalid = deepcopy(score)
        invalid["score_details"]["recommended_next_action"] = "准备报价"
        self.assertEqual(self.agent.post("/api/v1/agent/scores/", invalid, format="json", **headers).status_code, 400)
        self.browser.patch("/api/v1/sales/seller-profile/", {"service_regions": ["Malaysia"]}, format="json", HTTP_IF_MATCH="0")
        self.assertEqual(self.agent.post("/api/v1/agent/scores/", score, format="json", **headers).status_code, 409)

    # Function: Verify formal-version presentation and tie ordering.
    # Inputs: Old-algorithm scores and list projections with differing urgency, IDs, and null scores.
    # Outputs: Formal mode hides old scores; new ordering matches confirmed rules.
    # Logic: Version selection uses the real database; list projections are locally mocked to isolate sort-key verification.
    # Constraints: Mocked sorting does not establish real pagination performance.
    def test_formal_sorting_and_legacy_score_visibility(self):
        snapshot = AnalysisInput.objects.create(company=self.company, revision=0, input_version="test", payload={"member_dedupe_keys": []})
        analysis = Analysis.objects.create(snapshot=snapshot, prompt_version="test", provider="rules", payload={"status": "completed"})
        Score.objects.create(analysis=analysis, score_version="rules-score-v1", value=90, payload={})
        self.assertIsNone(selectors.latest_result(self.company)[1])
        with self.settings(ANALYSIS_PROVIDER="rules"):
            self.assertIsNotNone(selectors.latest_result(self.company)[1])
        from unittest.mock import MagicMock
        query = MagicMock()
        query.exclude.return_value.filter.return_value.distinct.return_value = [self.company, self.peer, self.foreign]
        rows = [{"company_id": key, "score": value, "score_reasons": [{"feature": "urgency", "contribution": urgency}], "crm_status": "registered"}
                for key, value, urgency in (("b", 60, 10), ("a", 60, 20), ("c", None, 0))]
        with patch("apps.crm.selectors.company_row", side_effect=rows):
            self.assertEqual([item["company_id"] for item in selectors.list_companies(query, {})["results"]], ["a", "b", "c"])
