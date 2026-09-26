"""Responsibility: Verify that new experiment sources can be displayed within existing chat budgets.
Implementation: Build paginated evidence exceeding the source-count limit and check file visibility and pagination boundaries.
Relationships: Experiment tool projection in workflows.chat; backend integration tests cover real authorization and HTTP.
Directory:
- ExperimentChatTests: Tests of workflow experiment boundaries only.
- ExperimentChatTests.test_file_evidence_survives_full_page: File evidence remains visible despite earlier full pages of row records.
- ExperimentChatTests.test_rows_keep_existing_page_budget: Experiment rows retain the established pagination budget and reject writes.
Variable index:
- None
"""

import unittest

from agent.workflows.chat import ChatValidationError, _workspace_arguments, _workspace_prompt_evidence


# Function: Verify display and parameter constraints for new experiment tools.
# Logic: Test only model input budgets and tool selection; do not simulate successful authorization.
# Constraints: No network, database, or real model calls.
class ExperimentChatTests(unittest.TestCase):
    # Function: Ensure the most recently read file remains a visible citation source.
    # Inputs: Twenty row records followed by one file source.
    # Outputs: The file appears in both the complete allowlist and displayed excerpts; source count stays within the original limit of 12.
    # Logic: Sort through the actual source selection function without changing budgets to pass.
    # Constraints: Verify display behavior only, not authentic file content or user read permission.
    def test_file_evidence_survives_full_page(self):
        rows = [{"source_id": str(index), "source_type": "experiment_row", "title_or_label": "虚构行",
                 "content": "虚构记录"} for index in range(20)]
        file = {"source_id": "file", "source_type": "experiment_file", "title_or_label": "虚构附件", "content": "虚构正文"}
        visible, prompt = _workspace_prompt_evidence(rows + [file], "读取实验附件")
        self.assertEqual(len(visible), 12)
        self.assertEqual(visible[0], file)
        self.assertEqual(prompt[0], file)

    # Function: Ensure new pagination tools use existing budgets.
    # Inputs: Default page size, an over-budget value, and a write-tool name.
    # Outputs: Default size is 20; excessive sizes and write tools raise ChatValidationError.
    # Logic: Check the model-action argument entry point directly; the backend separately validates the complete schema.
    # Constraints: Do not mutate the original dictionary or treat client validation as authorization.
    def test_rows_keep_existing_page_budget(self):
        args = {"batch": "KGSEED_20260921_01", "model": "crm.Company"}
        _, parsed = _workspace_arguments("experiments.rows", args)
        self.assertEqual(parsed["page_size"], 20)
        self.assertNotIn("page_size", args)
        for name, values in (("experiments.rows", {**args, "page_size": 21}), ("customers.create", {})):
            with self.assertRaises(ChatValidationError):
                _workspace_arguments(name, values)
