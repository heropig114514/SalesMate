"""Responsibility: Verify key constraints of the synthetic fine-tuning corpus and evaluation program.
Implementation: Uses the production schema and validator to check labels, local-key invariance, failure semantics, and attribution of identity resolution.
Relationships: tools.build_semantic_corpus and tools.semantic_finetune_kernel; unit tests do not simulate model or Kaggle success.
Directory:
- SemanticFinetuneTests: Independent corpus and evaluation-contract tests.
- SemanticFinetuneTests.setUp: Establish controlled schema and validator functions.
- SemanticFinetuneTests.test_all_gold_cases_validate: All scenario labels validate.
- SemanticFinetuneTests.test_key_names_do_not_change_score: Renaming local keys does not change semantic score.
- SemanticFinetuneTests.test_false_employment_is_counted: Misclassifying a contact as employed loses score.
- SemanticFinetuneTests.test_invalid_evidence_fails: Forged source text cannot count as correct.
- SemanticFinetuneTests.test_resolver_credit_is_separate: Model misalignment and program alignment are scored separately.
Variable index:
- None
"""
import copy
from django.test import SimpleTestCase
from apps.knowledge_graph.business_schema import catalog
from apps.knowledge_graph.semantic_contract import validate_extraction
from apps.knowledge_graph.entity_resolution import resolve_entities
from tools.build_semantic_corpus import example, FAMILIES
from tools.semantic_finetune_kernel import score


# Function: Verify the fine-tuning corpus and evaluation contract.
# Logic: Uses real functions and synthetic dictionaries without accessing databases or models.
# Constraints: Passing tests do not prove training convergence, quantization quality, or accuracy on real mail.
class SemanticFinetuneTests(SimpleTestCase):
    # Function: Prepare business schema and production validator functions.
    # Inputs: No external parameters; reads initialized Django model metadata.
    # Outputs: schema and contract instance state.
    # Logic: Does not copy or simplify actual validation logic.
    # Constraints: Performs no business-row queries.
    def setUp(self):
        self.schema = catalog()
        self.contract = {"validate_extraction": validate_extraction, "resolve_entities": resolve_entities}

    # Function: Validate all scenario labels across three splits.
    # Inputs: No external parameters; reads twelve fixed generators.
    # Outputs: All labels and program resolution are correct and split entity names do not overlap.
    # Logic: Runs the same functions as production evaluation for each scenario.
    # Constraints: Shared templates are known, so this cannot claim independence of wording or domain distribution.
    def test_all_gold_cases_validate(self):
        identities = []
        for split in ("train", "dev", "test"):
            names = set()
            for family in FAMILIES:
                row = example(split, 0, family, self.schema)
                metrics = score(row["gold"], row, self.contract)
                self.assertTrue(metrics["valid"] and metrics["facts_exact"] and metrics["resolved_identity_exact"])
                names.update(item["name"] for item in row["gold"]["entities"])
            identities.append(names)
        self.assertFalse(identities[0] & identities[1] or identities[0] & identities[2])

    # Function: Verify that scores do not depend on arbitrary local numbering.
    # Inputs: No external parameters; renames only e1 for the same fact.
    # Outputs: Exact fact and identity matches remain valid.
    # Logic: Normalizes endpoints by entity type and name rather than array order.
    # Constraints: Does not relax name, value, or quoted-evidence requirements.
    def test_key_names_do_not_change_score(self):
        row = example("test", 0, "need_budget", self.schema)
        result = copy.deepcopy(row["gold"])
        result["entities"][0]["key"] = "e9"
        for item in result["facts"]:
            item["subject"] = "e9"
        self.assertTrue(score(result, row, self.contract)["facts_exact"])

    # Function: Verify that a source-backed but semantically overstrong employment relation loses gold-standard score.
    # Inputs: No external parameters; changes a contact field:company to works_for.
    # Outputs: Evidence structure remains valid but produces one false positive and one false negative.
    # Logic: Distinguishes protocol validity from semantic correctness.
    # Constraints: Does not treat source-text validation as human confirmation.
    def test_false_employment_is_counted(self):
        row = example("test", 0, "contact_not_employee", self.schema)
        result = copy.deepcopy(row["gold"])
        result["facts"][0]["predicate"] = "works_for"
        metrics = score(result, row, self.contract)
        self.assertTrue(metrics["valid"])
        self.assertEqual((metrics["tp"], metrics["fp"], metrics["fn"]), (0, 1, 1))

    # Function: Reject evidence absent from the input.
    # Inputs: No external parameters; replaces an attribute quotation with fabricated text.
    # Outputs: valid and facts_exact are both False for the whole case.
    # Logic: Calls production validation and does not repair output before scoring.
    # Constraints: Failed samples remain in the summary denominator.
    def test_invalid_evidence_fails(self):
        row = example("test", 0, "need_budget", self.schema)
        result = copy.deepcopy(row["gold"])
        result["facts"][0]["quote"] = "invented source"
        metrics = score(result, row, self.contract)
        self.assertFalse(metrics["valid"] or metrics["facts_exact"])

    # Function: Evaluate identity-linking contributions from model and program separately.
    # Inputs: No external parameters; the model omits one uniquely known entity ID.
    # Outputs: Raw identity matching fails, resolved matching passes, and the original dictionary remains unchanged.
    # Logic: Simulates the ID omission observed in real smoke testing without simulating training success.
    # Constraints: Resolved metrics cannot be described as independent model capability.
    def test_resolver_credit_is_separate(self):
        row = example("test", 0, "need_budget", self.schema)
        result = copy.deepcopy(row["gold"])
        result["entities"][0]["existing_id"] = None
        metrics = score(result, row, self.contract)
        self.assertFalse(metrics["raw_identity_exact"])
        self.assertTrue(metrics["resolved_identity_exact"])
        self.assertIsNone(result["entities"][0]["existing_id"])
