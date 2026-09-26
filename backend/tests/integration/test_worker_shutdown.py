"""Responsibility: Verify that deployment shutdown does not interrupt the current external action or claim later actions.
Implementation: Mock actions and signal calls while actually executing Worker loops and handler restoration; do not send OS signals or mail.
Relationships: Covers `common.shutdown` and `sales_worker`; CRM executor draining depends on the same stop state.
Directory:
- ShutdownTests: Shutdown-boundary tests.
- ShutdownTests.test_handler_restored_after_exception: Restore signal handler after an exception.
- ShutdownTests.test_sales_finishes_current_action_before_exit: Complete the current action without starting later actions.
- ShutdownTests.test_crm_waits_for_pending_result: Wait for an in-flight unit and retain its exception during shutdown.
Variable index:
- None
"""
import signal
from unittest import TestCase
from unittest.mock import Mock, patch
from django.test import override_settings
from common.shutdown import graceful_shutdown
from apps.sales.management.commands import sales_worker
from apps.crm.management.commands import crm_worker


# Function: Verify conversion from signals to business boundaries.
# Logic: Directly call the temporary handler and do not send SIGTERM to the real process.
# Constraints: Replace all business network calls and queries.
class ShutdownTests(TestCase):
    # Function: Ensure exceptional exit still restores the original handler.
    # Inputs: Original SIGTERM state and a synthetic business exception.
    # Outputs: Stop flag is true and original handler is restored.
    # Logic: Call the current handler inside the context and then raise an exception.
    # Constraints: Do not change permanent signal behavior of the test process.
    def test_handler_restored_after_exception(self):
        previous = signal.getsignal(signal.SIGTERM)
        with self.assertRaises(RuntimeError):
            with graceful_shutdown() as stop:
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
                self.assertTrue(stop["requested"])
                raise RuntimeError("synthetic")
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)

    # Function: Require every in-flight CRM unit result to be read during deployment shutdown.
    # Inputs: A mocked shared scheduler selects an owner and schedules a sync Future, then receives a stop request; variants return success or raise.
    # Outputs: `Future.result` is awaited once and exceptions continue to propagate.
    # Logic: Mock shared executor and scheduling boundaries, set stop state only while polling, and perform no database or network work.
    # Constraints: Do not treat executor exit as business success or swallow errors from unfinished units.
    @override_settings(ANALYSIS_PROVIDER="agent")
    def test_crm_waits_for_pending_result(self):
        for failure in (False, True):
            with self.subTest(failure=failure), patch.object(crm_worker, "load_environment"), patch.object(crm_worker, "close_old_connections"), patch.object(crm_worker, "next_owner", side_effect=lambda kind, after: Mock(pk=1) if kind == "sync" else None), patch.object(crm_worker, "work_executor") as executor, patch.object(crm_worker.time, "sleep", side_effect=lambda seconds: signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)):
                future = executor.return_value.__enter__.return_value.submit.return_value
                if failure:
                    future.result.side_effect = RuntimeError("synthetic")
                    with self.assertRaises(RuntimeError):
                        crm_worker.Command().handle(once=False, poll=1, analysis_workers=2)
                else:
                    crm_worker.Command().handle(once=False, poll=1, analysis_workers=2)
                future.result.assert_called_once_with()

    # Function: Exit only after the current mail action returns and do not claim the second queue item.
    # Inputs: A synthetic two-action queue; the first action triggers the stop handler while executing.
    # Outputs: `run_action` runs once and returns normally; the Worker does not wait for another loop.
    # Logic: The Mock side effect calls the handler before returning success, then checks call boundaries and handler restoration.
    # Constraints: Do not connect to a database or external service.
    def test_sales_finishes_current_action_before_exit(self):
        previous = signal.getsignal(signal.SIGTERM)
        with patch.object(sales_worker, "ToolAction") as actions, patch.object(sales_worker, "notify_due"), patch.object(sales_worker, "close_old_connections"), patch.object(sales_worker.time, "sleep") as sleep, patch.object(sales_worker, "run_action", side_effect=lambda key: (signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None), "succeeded")) as execute:
            actions.objects.filter.return_value.order_by.return_value.values_list.return_value = ["first", "second"]
            sales_worker.Command().handle(once=False, poll=5)
        execute.assert_called_once_with("first")
        sleep.assert_not_called()
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)
