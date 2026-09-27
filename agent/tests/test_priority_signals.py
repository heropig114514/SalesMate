"""Deadline integration regressions: source dates reach scoring without a model."""

import unittest
from datetime import datetime, timezone

from agent.tests.fake_backend import FakeBackend
from agent.workflows.analysis_input import build_analysis_input
from agent.workflows.priority_signals import deadline_signals, _explicit_date
from agent.workflows.lead_score import compute_score


def email(text, *, direction='inbound', evidence=None):
    return {'direction': direction, 'extract_status': 'completed',
            'dedupe_key': 'sales@example.com:1', 'subject': '', 'body_text': text,
            'facts': {'delivery_time': [{'value': text, 'evidences': [evidence or text]}]}}


class DeadlineTests(unittest.TestCase):
    def test_explicit_formats_without_inferred_timezone(self):
        cases = {
            '28 September 2026 at 11:00 Singapore time': '2026-09-28T11:00:00+08:00',
            '28 Sep 2026 at 11:00 UTC+08:00': '2026-09-28T11:00:00+08:00',
            '2026-09-28T03:00:00Z': '2026-09-28T03:00:00+00:00',
            '2026-09-28 at 11:00+08:00': '2026-09-28T11:00:00+08:00',
            '28 September 2026': '2026-09-28',
            '28 September 2026 at 11:00': None,
            'tomorrow at 11:00 Singapore time': None,
            '28 September at 11:00 Singapore time': None,
            '31 September 2026': None,
            '2026-09-28 at 11:00+15:00': None,
            '28 September 2026 at 3pm': None,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(_explicit_date(text), expected)

    def test_short_evidence_retains_source_action_and_date(self):
        text = 'Please send the quotation by 28 September 2026 at 17:00 Singapore time.'
        result = deadline_signals([email(text, evidence='by 28 September 2026 at 17:00 Singapore time')])
        self.assertEqual(result[0]['value'], '2026-09-28T17:00:00+08:00')
        self.assertEqual(result[0]['evidence'], text)

    def test_conditional_negated_foreign_and_outbound_dates_are_not_deadlines(self):
        for text in [
            'If approved, delivery would be 28 September 2026.',
            'The deadline of 28 September 2026 is cancelled.',
            'No delivery date is confirmed for 28 September 2026.',
        ]:
            self.assertEqual(deadline_signals([email(text)]), [])
        self.assertEqual(deadline_signals([email('Please reply by 28 September 2026.', direction='outbound')]), [])
        self.assertEqual(deadline_signals([email('Hello.', evidence='28 September 2026')]), [])

    def test_latest_update_replaces_older_deadline_including_unknown_replacement(self):
        original = email('Please reply by 28 September 2026 at 11:00 Singapore time.')
        updated = email('Please reply by 12 October 2026 at 17:00 Singapore time.')
        self.assertEqual(deadline_signals([original, updated])[0]['value'], '2026-10-12T17:00:00+08:00')
        for text in ['The response deadline is postponed; the replacement date is unknown.',
                     'Please reply next month.', 'No response deadline is set.']:
            self.assertEqual(deadline_signals([original, email(text)]), [])

    def test_response_and_delivery_are_separate_and_ambiguous_range_is_ignored(self):
        result = deadline_signals([
            email('Please reply by 28 September 2026 at 11:00 Singapore time.'),
            email('Delivery is required by 30 October 2026.')])
        self.assertEqual(len(result), 2)
        self.assertEqual(deadline_signals([email('Delivery between 28 September 2026 and 30 September 2026.')]), [])

    def test_real_l2_to_l4_chain_uses_deadline_without_changing_weights(self):
        text = 'Please send the quotation by 28 September 2026 at 11:00 Singapore time.'
        class Backend(FakeBackend):
            def get_company_context(self, company_id):
                result = super().get_company_context(company_id)
                first = result['emails'][0]
                first['body_text'] = text
                first['facts']['delivery_time'] = [{'value': text, 'evidences': [text]}]
                first['facts']['intent_hint'] = 'L3 Qualified'
                first['facts']['intent_evidences'] = [text]
                return result
        clock = lambda: datetime(2026, 9, 28, 1, tzinfo=timezone.utc)
        snapshot = build_analysis_input('company-demo', backend=Backend(), clock=clock)
        priority = snapshot.priority_context
        actual = compute_score({'status': 'completed'}, snapshot, clock=clock, priority_context=priority)
        self.assertEqual(actual['score'], 80)  # (100 + 60) / 2 without CRM opportunity data.
        self.assertIn('DEADLINE', actual['score_reasons'][0]['note'])
        expired = compute_score({'status': 'completed'}, snapshot,
                                clock=lambda: datetime(2026, 9, 29, tzinfo=timezone.utc),
                                priority_context=priority)
        self.assertEqual(expired['score'], 35)  # Expired deadlines no longer inflate urgency.
        self.assertNotIn('priority_context', snapshot.to_dict())


if __name__ == '__main__':
    unittest.main()
