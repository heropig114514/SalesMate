"""Responsibility: Verify database invariants for complete business schemas and automatic graph construction from external information.
Implementation: Real PostgreSQL capture, projection, and HTTP with verifiable synthetic model responses; independent local smoke checks are separate.
Relationships: The new knowledge_graph input path and existing graph; no real external services or business-database changes.
Directory:
- SemanticGraphTests: Isolated database integration tests.
- SemanticGraphTests.setUp: Create same-user business records and isolated identities.
- SemanticGraphTests.records: Create partial product observations.
- SemanticGraphTests.test_catalog_and_all_triggers: Verify catalog and capture coverage.
- SemanticGraphTests.test_partial_records_link_and_idempotence: Partial fields, automatic linking, and idempotency.
- SemanticGraphTests.test_missing_reference_resolves_later: Unknown foreign-key targets later enter the business graph.
- SemanticGraphTests.test_conflict_and_retraction_keep_independent_support: Multiple sources and conflicting-candidate retraction.
- SemanticGraphTests.test_text_links_with_evidence: Model candidates, entity references, and evidence lineage.
- SemanticGraphTests.test_bad_model_output_is_atomic: Invalid references/quotes cannot contaminate the graph.
- SemanticGraphTests.test_context_change_rejects_commit: Business changes during inference reject commit.
- SemanticGraphTests.test_context_change_rejects_commit.change: Simulate a new committed business value during model execution.
- SemanticGraphTests.test_api_owner_scope_and_invalid_input: HTTP isolation and input contracts.
- SemanticGraphTests.test_expanded_owner_paths_and_archive: Capture ownership and archival of new sources.
- SemanticGraphTests.test_reset_removes_episodes: Account cleanup covers new sources.
- SemanticGraphTests.test_local_provider_rejects_remote: Local-model endpoints reject remote hosts.
- SemanticGraphTests.test_mail_observation_tracks_source_validity: Mail observations invalidate with source-classification or text changes.
- EntityResolutionTests: Independent entity-resolution unit tests.
- EntityResolutionTests.test_unique_name_and_type_preserves_raw_output: Name alignment does not rewrite original model suggestions.
- EntityResolutionTests.test_ambiguous_name_stays_separate: Ambiguous identical names are not arbitrarily merged.
- EntityResolutionTests.test_known_neighbour_disambiguates: Shared relationship endpoints constrain same-name candidates.
- GenerationContractTests: Independent protocol tests for generation constraints.
- GenerationContractTests.test_relations_require_local_keys_and_null_value: Reject invalid relationship shapes within valid JSON.
Variable index:
- None
"""
import copy
import uuid
import jsonschema
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.db import connection
from django.test import TransactionTestCase, SimpleTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from apps.accounts.reset import reset_account
from apps.crm.models import Company, AnalysisInput, Analysis, Score, Mailbox, Email
from apps.sales.models import Product, Quote
from apps.knowledge_graph.business_schema import catalog, source_models
from apps.knowledge_graph.episodes import ingest, retract
from apps.knowledge_graph.models import Change, Entity, Episode, Fact
from apps.knowledge_graph.projection import identity
from apps.knowledge_graph.sync import require_capture, sync_owner
from apps.knowledge_graph.semantic_provider import generate
from apps.knowledge_graph.management.commands.graph_ingest import email_payload
from apps.knowledge_graph.entity_resolution import resolve_entities
from apps.knowledge_graph.semantic_contract import response_schema


# Function: Verify automatic graph construction and isolation on real PostgreSQL.
# Logic: Independent accounts and real database transactions per test; replace only model generation.
# Constraints: Mocked-model tests do not establish Qwen semantic quality or actual inference speed.
@override_settings(LAB_OPEN_ACCESS=False, LOCAL_DEBUG_AUTO_LOGIN=False)
class SemanticGraphTests(TransactionTestCase):
    # Function: Prepare business entities and API identities.
    # Inputs: No external arguments; read the isolated test database.
    # Outputs: owner, other, company, product, now, and client state.
    # Logic: Perform real synchronization after creating sources; all triggers must be installed.
    # Constraints: Fail without PostgreSQL; do not simulate a pass using SQLite.
    def setUp(self):
        self.assertEqual(connection.vendor, "postgresql")
        self.owner = get_user_model().objects.create_user(username="semantic-owner")
        self.other = get_user_model().objects.create_user(username="semantic-other")
        self.company = Company.objects.create(owner=self.owner, group_key="acme.example", name="Acme")
        self.product = Product.objects.create(owner=self.owner, sku="EDGE", name="Edge", currency="SGD", unit_price="100")
        self.now = timezone.now()
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        sync_owner(self.owner.pk)

    # Function: Generate product observations containing partial fields only.
    # Inputs: `price` is an optional price string.
    # Outputs: Structured input list.
    # Logic: Provide only sku and optional price, omitting business-required fields such as name/currency.
    # Constraints: Verify observations do not force creation of business products.
    def records(self, price="120"):
        return [{"key": "product", "schema": "sales.product", "fields": {"sku": "EDGE", "unit_price": price}}]

    # Function: Verify capture is installed for all declared sources.
    # Inputs: No external arguments; read model catalogs and real pg_trigger.
    # Outputs: Assertions for source counts, sensitive-field exclusion, and schema fields.
    # Logic: Check runtime prerequisites for the complete catalog.
    # Constraints: Structural coverage does not establish semantic extraction quality in every domain.
    def test_catalog_and_all_triggers(self):
        require_capture()
        self.assertEqual(len(source_models()), 48)
        self.assertNotIn("encrypted_credentials", catalog()["sales.connection"])
        self.assertNotIn("accounts.user", catalog())
        self.assertEqual(catalog()["sales.quote"]["company"]["target"], "crm.company")

    # Function: Verify missing fields, identity alignment, and repeated requests.
    # Inputs: No external arguments; inputs contain only sku/price.
    # Outputs: Reuse the product entity, preserve business prices, and create no additional records for the same key.
    # Logic: Structured input invokes no model; fields persist as independent observations.
    # Constraints: Observed prices do not mean the catalog has been updated.
    def test_partial_records_link_and_idempotence(self):
        with patch("apps.knowledge_graph.episodes.generate") as model:
            first = ingest(self.owner.pk, "partial", self.now, records=self.records())
            second = ingest(self.owner.pk, "partial", self.now, records=self.records())
        model.assert_not_called()
        self.assertEqual(first.pk, second.pk)
        product_id = identity("entity", self.owner.pk, "sales.product", str(self.product.pk))
        self.assertTrue(Fact.objects.filter(subject_id=product_id, predicate="field:unit_price", value="120", status="active").exists())
        self.product.refresh_from_db()
        self.assertEqual(str(self.product.unit_price), "100.00")
        self.assertEqual(Product.objects.count(), 1)
        with self.assertRaises(Exception):
            ingest(self.owner.pk, "partial", self.now, records=self.records("121"))

    # Function: Verify incomplete relationships resolve when authoritative records arrive.
    # Inputs: No external arguments; a quote references a nonexistent customer UUID.
    # Outputs: An external placeholder initially exists; the foreign key later targets the new business customer.
    # Logic: Stable source IDs match, and the synchronizer reprojects without another model call.
    # Constraints: External quotes exist only in the graph; no real Quote is created.
    def test_missing_reference_resolves_later(self):
        customer_id = uuid.uuid4()
        ingest(self.owner.pk, "quote", self.now, records=[{"key": "q", "schema": "sales.quote", "fields": {"number": "Q-NEW", "company": str(customer_id)}}])
        self.assertTrue(Entity.objects.filter(owner=self.owner, kind="external.crm.company", active=True).exists())
        Company.objects.create(id=customer_id, owner=self.owner, group_key="later.example", name="Later")
        sync_owner(self.owner.pk)
        target = identity("entity", self.owner.pk, "crm.company", str(customer_id))
        self.assertTrue(Fact.objects.filter(predicate="field:company", object_id=target, status="active").exists())
        self.assertEqual(Quote.objects.count(), 0)

    # Function: Verify independent support for equal values from multiple sources and conflicting candidates.
    # Inputs: No external arguments; three price observations.
    # Outputs: Different values require review; retraction restores the remaining unique value and source.
    # Logic: Identical assertions reuse Fact, while different Episodes retain independent Derivations.
    # Constraints: Newer timestamps do not automatically overwrite old values.
    def test_conflict_and_retraction_keep_independent_support(self):
        first = ingest(self.owner.pk, "one", self.now, records=self.records())
        ingest(self.owner.pk, "two", self.now, records=self.records())
        third = ingest(self.owner.pk, "three", self.now, records=self.records("130"))
        fact = Fact.objects.get(owner=self.owner, predicate="field:unit_price", value="120")
        self.assertEqual(fact.status, "needs_review")
        self.assertEqual(fact.supports.filter(derivation__active=True).count(), 2)
        retract(self.owner.pk, first.pk)
        retract(self.owner.pk, third.pk)
        fact.refresh_from_db()
        self.assertEqual(fact.status, "active")
        self.assertEqual(fact.supports.filter(derivation__active=True).count(), 1)

    # Function: Verify model text candidates attach to original text and existing entities.
    # Inputs: No external arguments; synthetic model output provides a budget with an original-text quote.
    # Outputs: The existing company gains an assertion with the actual quote in source lineage.
    # Logic: Mock generation only; all other validation, database, and synchronization steps run normally.
    # Constraints: Do not claim the actual model understood the budget.
    def test_text_links_with_evidence(self):
        company_id = str(identity("entity", self.owner.pk, "crm.company", str(self.company.pk)))
        result = {"entities": [{"key": "c", "kind": "crm.company", "name": "Acme", "existing_id": company_id, "quote": "Acme"}],
                  "facts": [{"subject": "c", "predicate": "reported_budget", "object": None, "value": "800 SGD", "quote": "budget is 800 SGD"}]}
        with patch("apps.knowledge_graph.episodes.generate", return_value=(result, {"mode": "mock"})):
            ingest(self.owner.pk, "text", self.now, text="Acme budget is 800 SGD")
        fact = Fact.objects.get(subject_id=company_id, predicate="reported_budget", status="active")
        self.assertEqual(fact.supports.get(derivation__active=True).derivation.evidence[-1]["quote"], "budget is 800 SGD")

    # Function: Verify unauthorized model references and forged quotes cannot persist.
    # Inputs: No external arguments; invalid entity references and quotes absent from original text.
    # Outputs: Episode count remains zero after each failure.
    # Logic: Run complete contract validation through the real input service.
    # Constraints: Mock bad responses only; no remote-model access.
    def test_bad_model_output_is_atomic(self):
        bad = {"entities": [{"key": "c", "kind": "crm.company", "name": "Acme", "existing_id": str(uuid.uuid4()), "quote": "Acme"}], "facts": []}
        for index in range(2):
            result = copy.deepcopy(bad)
            if index:
                result["entities"][0].update(existing_id=None, quote="not present")
            with patch("apps.knowledge_graph.episodes.generate", return_value=(result, {})), self.assertRaises(Exception):
                ingest(self.owner.pk, str(index), self.now, text="Acme")
        self.assertEqual(Episode.objects.count(), 0)

    # Function: Verify context changes during model calls are not ignored.
    # Inputs: No external arguments; a model stub updates a product.
    # Outputs: Linking commit is rejected without a new source.
    # Logic: Business triggers create pending state; commit requires a current snapshot.
    # Constraints: Same-thread simulation of an external-commit boundary, not a real multiprocess load test.
    def test_context_change_rejects_commit(self):
        # Function: Change business sources at the model boundary.
        # Inputs: `prompt` is the message list passed to the model.
        # Outputs: Valid empty extraction with a committed business change.
        # Logic: Verify the input service detects events added during generation.
        # Constraints: Change only synthetic products in the test database.
        def change(prompt):
            Product.objects.filter(pk=self.product.pk).update(name="Changed")
            return {"entities": [], "facts": []}, {}
        with patch("apps.knowledge_graph.episodes.generate", side_effect=change), self.assertRaises(Exception):
            ingest(self.owner.pk, "stale", self.now, text="Acme")
        self.assertEqual(Episode.objects.count(), 0)

    # Function: Verify HTTP isolation and strict field constraints.
    # Inputs: No external arguments; two identities and invalid schema fields.
    # Outputs: Own creation succeeds; unauthorized access returns 404 and unknown fields 400.
    # Logic: Use real DRF views; forced authentication isolates only login.
    # Constraints: Does not establish real-browser CSRF session validation.
    def test_api_owner_scope_and_invalid_input(self):
        payload = {"source_key": "api", "observed_at": self.now.isoformat(), "records": self.records()}
        response = self.client.post("/api/v1/graph/episodes/", payload, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(f"/api/v1/graph/episodes/{response.data['id']}/").status_code, 404)
        self.client.force_authenticate(self.owner)
        payload["source_key"] = "invalid-fields"
        payload["records"][0]["fields"]["owner"] = self.other.pk
        self.assertEqual(self.client.post("/api/v1/graph/episodes/", payload, format="json").status_code, 400)

    # Function: Verify nested ownership capture and archival retraction for new sources.
    # Inputs: No external arguments; an analysis chain and quote.
    # Outputs: Score changes have correct ownership; archived quotes are no longer active entities.
    # Logic: Real PostgreSQL functions resolve snapshot/analysis foreign keys.
    # Constraints: Synthetic analysis payloads do not represent actual model results.
    def test_expanded_owner_paths_and_archive(self):
        snapshot = AnalysisInput.objects.create(company=self.company, input_version="v", revision=1, payload={})
        analysis = Analysis.objects.create(snapshot=snapshot, prompt_version="v", payload={}, provider="rules")
        Score.objects.create(analysis=analysis, payload={}, score_version="v", value=1)
        self.assertTrue(Change.objects.filter(owner_id=self.owner.pk, kind="crm.score", status="pending").exists())
        quote = Quote.objects.create(owner=self.owner, company=self.company, number="Q1", currency="SGD")
        sync_owner(self.owner.pk)
        self.assertTrue(Entity.objects.filter(kind="sales.quote", source_id=str(quote.pk), active=True).exists())
        quote.archived = True
        quote.save()
        sync_owner(self.owner.pk)
        self.assertFalse(Entity.objects.filter(kind="sales.quote", source_id=str(quote.pk), active=True).exists())

    # Function: Verify new sources belong to account-reset scope.
    # Inputs: No external arguments; one synthetic observation and an account session.
    # Outputs: No Episode or capture-event residue remains after reset.
    # Logic: Call the existing real reset service.
    # Constraints: Synthetic accounts only; concurrent file cleanup is not tested.
    def test_reset_removes_episodes(self):
        ingest(self.owner.pk, "reset", self.now, records=self.records())
        session = SessionStore()
        session.create()
        reset_account(self.owner, uuid.uuid4(), session)
        self.assertFalse(Episode.objects.filter(owner=self.owner).exists())
        self.assertFalse(Change.objects.filter(owner_id=self.owner.pk).exists())

    # Function: Verify the model adapter does not send input to remote services.
    # Inputs: No external arguments; simulate non-loopback administrator configuration.
    # Outputs: Explicit rejection before requesting.
    # Logic: An HTTP Session must not be created.
    # Constraints: Endpoint validation only; does not establish availability of a real local model.
    def test_local_provider_rejects_remote(self):
        with patch.dict("os.environ", {"GRAPH_LLM_URL": "https://example.com/v1", "GRAPH_LLM_MODEL": "model"}), patch("requests.Session") as session:
            with self.assertRaises(RuntimeError):
                generate([])
            session.assert_not_called()

    # Function: Verify automatic mail input retains source lifecycle constraints.
    # Inputs: No external arguments; synthetic mail and a budget-model stub.
    # Outputs: Classification withdrawal, restoration, and body modification respectively retract, restore, and retract observation support.
    # Logic: The projector rechecks source fingerprints/business eligibility; source changes do not automatically retry models.
    # Constraints: Mock model output while verifying real database triggers/evidence dependencies, not real mail-extraction quality.
    def test_mail_observation_tracks_source_validity(self):
        mailbox = Mailbox.objects.create(owner=self.owner, address="local@example.com")
        email = Email.objects.create(dedupe_key="semantic-mail", mailbox=mailbox, company=self.company,
            direction="inbound", sent_at=self.now, received_at=self.now, payload={"subject": "Acme", "body_text": "Budget 800 SGD"})
        company_id = str(identity("entity", self.owner.pk, "crm.company", str(self.company.pk)))
        result = {"entities": [{"key": "c", "kind": "crm.company", "name": "Acme", "existing_id": company_id, "quote": "Acme"}],
                  "facts": [{"subject": "c", "predicate": "reported_budget", "object": None, "value": "800 SGD", "quote": "Budget 800 SGD"}]}
        with patch("apps.knowledge_graph.episodes.generate", return_value=(result, {})):
            ingest(self.owner.pk, **email_payload(email))
        fact = Fact.objects.get(owner=self.owner, predicate="reported_budget")
        self.assertEqual(fact.status, "active")
        email.business_classification = "non_business"
        email.save()
        sync_owner(self.owner.pk)
        fact.refresh_from_db()
        self.assertEqual(fact.status, "unsupported")
        email.business_classification = "business"
        email.save()
        sync_owner(self.owner.pk)
        fact.refresh_from_db()
        self.assertEqual(fact.status, "active")
        email.payload["body_text"] = "Budget changed"
        email.save()
        sync_owner(self.owner.pk)
        fact.refresh_from_db()
        self.assertEqual(fact.status, "unsupported")


# Function: Verify the independent deterministic entity-resolution contract.
# Logic: Use in-memory candidates and relationships only, without models or databases.
# Constraints: Verify the resolver, not whether same-name real-world identities are actually identical.
class EntityResolutionTests(SimpleTestCase):
    # Function: Verify unique name/type matches reuse existing entities.
    # Inputs: No external arguments; a same-name entity whose mocked model output selected no ID.
    # Outputs: Resolved output references the existing ID while the original model object remains null.
    # Logic: Candidates with matching names but different types are excluded.
    # Constraints: Deterministic alignment test, not a claim that the model performed alignment itself.
    def test_unique_name_and_type_preserves_raw_output(self):
        raw = {"entities": [{"key": "m", "name": "Mira", "kind": "crm.contact", "existing_id": None}], "facts": []}
        context = {"entities": [{"id": "contact", "label": "Mira", "kind": "crm.contact"}, {"id": "company", "label": "Mira", "kind": "crm.company"}], "facts": []}
        resolved, decisions = resolve_entities(raw, context)
        self.assertEqual(resolved["entities"][0]["existing_id"], "contact")
        self.assertIsNone(raw["entities"][0]["existing_id"])
        self.assertEqual(decisions[0]["method"], "unique_exact_name_and_type")

    # Function: Verify ambiguity remains when multiple candidates lack additional evidence.
    # Inputs: No external arguments; two same-name contacts of the same type.
    # Outputs: Neither candidate is selected.
    # Logic: Without a shared known neighbor, candidate order cannot determine identity.
    # Constraints: Independent observations may duplicate entities; later human review or stronger evidence is needed.
    def test_ambiguous_name_stays_separate(self):
        raw = {"entities": [{"key": "m", "name": "Mira", "kind": "crm.contact", "existing_id": None}], "facts": []}
        context = {"entities": [{"id": "a", "label": "Mira", "kind": "crm.contact"}, {"id": "b", "label": "Mira", "kind": "crm.contact"}], "facts": []}
        result, _ = resolve_entities(raw, context)
        self.assertIsNone(result["entities"][0]["existing_id"])

    # Function: Verify shared known neighbors constrain same-name candidates.
    # Inputs: No external arguments; only one of two Mira contacts already links to the target company.
    # Outputs: Resolve to the contact sharing the same known relationship endpoint.
    # Logic: Relationship types may differ; use the shared endpoint as identity context and record that rationale explicitly.
    # Constraints: The link remains an inference, not unique external identity proof.
    def test_known_neighbour_disambiguates(self):
        raw = {"entities": [{"key": "m", "name": "Mira", "kind": "crm.contact", "existing_id": None},
                            {"key": "c", "name": "Acme", "kind": "crm.company", "existing_id": "c"}],
               "facts": [{"subject": "m", "object": "c"}]}
        context = {"entities": [{"id": "a", "label": "Mira", "kind": "crm.contact"}, {"id": "b", "label": "Mira", "kind": "crm.contact"}],
                   "facts": [{"subject": "a", "object": "c"}]}
        result, decisions = resolve_entities(raw, context)
        self.assertEqual(result["entities"][0]["existing_id"], "a")
        self.assertEqual(decisions[0]["method"], "exact_name_and_known_neighbour")


# Function: Verify the external JSON Schema contract underlying constrained decoding.
# Logic: Use an independent JSON Schema validator for valid/invalid model-response shapes.
# Constraints: Generation structure only; does not replace real llama.cpp smoke checks, entity-existence checks, or semantic validation.
class GenerationContractTests(SimpleTestCase):
    # Function: Prevent relationships from emitting business IDs, names, or non-null property values.
    # Inputs: No external arguments; a fixed relationship response and three historical invalid shapes.
    # Outputs: Valid local references pass; all three invalid shapes fail the generation contract.
    # Logic: The validator consumes the schema independently of application validate_extraction.
    # Constraints: Local-target existence and endpoint types remain application checks; this test does not prove relationship correctness.
    def test_relations_require_local_keys_and_null_value(self):
        schema = response_schema()
        result = {"entities": [], "facts": [{"subject": "e1", "predicate": "works_for", "object": "e2", "value": None, "quote": "Mira works for Acme"}]}
        jsonschema.validate(result, schema)
        for field, value in (("object", "Acme"), ("value", "Acme"), ("predicate", "invented_relation")):
            invalid = copy.deepcopy(result)
            invalid["facts"][0][field] = value
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(invalid, schema)
