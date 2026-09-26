"""Responsibility: Official company-level L4 priority rules without backend or model access.
Implementation: Exercise real local functions against fixed in-memory data and mocked service boundaries; assertions check outputs, errors, and interactions.
Relationships: Uses agent workflows and clients without proving live mailbox, model, or backend availability.

Directory:
- analysis_input: Build fixed L2 input for scoring tests.
- context: Build fixed customer, seller, deal, and signal context.
- scored: Run the real scorer with fixed test input and clock.
- CompanyPriorityTests: Group offline assertions and fixture behavior for CompanyPriorityTests.
- CompanyPriorityTests.test_formula_keeps_company_score_shape: Verify formula keeps company score shape.
- CompanyPriorityTests.test_breakdown_top_reasons_and_next_action: Verify breakdown top reasons and next action.
- CompanyPriorityTests.test_urgency_buckets: Verify urgency buckets.
- CompanyPriorityTests.test_missing_business_data_gives_provisional_score_but_missing_intent_is_null: Verify missing business data gives provisional score but missing intent is null.
- CompanyPriorityTests.test_amount_and_fit_change_company_score: Verify amount and fit change company score.
- CompanyPriorityTests.test_buying_intent_levels_and_waiting_without_deadline: Verify buying intent levels and waiting without deadline.
- CompanyPriorityTests.test_historical_deadline_does_not_stay_urgent_forever: Verify historical deadline does not stay urgent forever.
- CompanyPriorityTests.test_date_only_deadline_uses_calendar_day_without_invented_hour: Verify date only deadline uses calendar day without invented hour.
- CompanyPriorityTests.test_date_only_deadline_uses_seller_time_zone: Verify date only deadline uses seller time zone.
- CompanyPriorityTests.test_signal_source_must_belong_to_company: Verify signal source must belong to company.
- CompanyPriorityTests.test_l4_uses_structured_l1_stage_without_model: Verify l4 uses structured l1 stage without model.
- CompanyPriorityTests.test_l4_missing_deal_uses_known_dimensions: Verify l4 missing deal uses known dimensions.
- CompanyPriorityTests.test_ranking_is_company_level_and_null_last: Verify ranking is company level and null last.

Variable index:
- COMPANY_ID: Fixed company UUID used by fixtures.
- NOW: Fixed timezone-aware test clock.
"""

import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from agent.workflows.lead_score import (
    SCORE_VERSION,
    compute_priority_result,
    compute_score,
    rank_company_scores,
)


NOW = datetime(2026, 9, 19, 10, tzinfo=timezone.utc)
COMPANY_ID = "3bc6c0b0-177e-4fc4-aee6-591e89945276"


def analysis_input():
    return {
        "company_id": COMPANY_ID,
        "input_version": "sha256:test-priority",
        "member_dedupe_keys": ["sales@example.com:mail-1"],
    }


def context():
    return {
        "signals": [
            {
                "type": "DEADLINE",
                "value": "2026-09-20T10:00:00+00:00",
                "confidence": 0.95,
                "evidence": "请明天前给正式报价",
                "source_id": "sales@example.com:mail-1",
            },
            {
                "type": "FORMAL_QUOTATION_REQUEST",
                "value": None,
                "confidence": 0.95,
                "evidence": "请给正式报价",
                "source_id": "sales@example.com:mail-1",
            },
        ],
        "customer": {
            "industry": "Manufacturing",
            "company_size": 100,
            "country": "Singapore",
        },
        "deal": {"deal_value": "250000", "currency": "SGD", "product": ["WMS", "OHT"],
                 "status": "ACTIVE"},
        "seller": {
            "average_deal_value": "30000",
            "average_deal_currency": "SGD",
            "time_zone": "Asia/Singapore",
            "target_industries": ["Manufacturing", "Wholesale"],
            "target_company_size": {"min": 20, "max": 500},
            "service_regions": ["Singapore"],
            "products": ["WMS"],
            "similar_won_deals": True,
        },
    }


def scored(data=None):
    return compute_score(
        {"status": "completed"},
        analysis_input(),
        clock=lambda: NOW,
        priority_context=data or context(),
    )


class CompanyPriorityTests(unittest.TestCase):
    def test_formula_keeps_company_score_shape(self):
        result = scored()
        self.assertEqual(set(result), {
            "company_id", "input_version", "score", "score_reasons", "score_version", "scored_at"
        })
        self.assertEqual(result["company_id"], COMPANY_ID)
        self.assertEqual(result["score_version"], SCORE_VERSION)
        self.assertEqual(result["score"], 88)
        self.assertEqual([item["feature"] for item in result["score_reasons"]],
                         ["urgency", "buying_intent", "opportunity_value"])
        self.assertEqual(sum(item["contribution"] for item in result["score_reasons"]), 88)
        self.assertIn("sales@example.com:mail-1", result["score_reasons"][0]["note"])

    def test_breakdown_top_reasons_and_next_action(self):
        result = compute_priority_result(
            {"status": "completed"}, analysis_input(), clock=lambda: NOW,
            priority_context=context(),
        )
        self.assertEqual(result["score"]["score"], 88)
        details = result["details"]
        self.assertEqual(details["score_breakdown"]["urgency"], 90)
        self.assertEqual(details["score_breakdown"]["buying_intent"], 75)
        self.assertEqual(sum(details["score_breakdown"]["contributions"].values()), 88)
        self.assertEqual(len(details["top_reasons"]), 3)
        self.assertEqual(details["top_reasons"][0]["type"], "DEADLINE")
        self.assertEqual(details["evidence"][0]["source_id"], "sales@example.com:mail-1")
        self.assertIn("formal quote", details["recommended_next_action"])

    def test_urgency_buckets(self):
        for hours, expected in [(4, 100), (5, 90), (24, 90), (25, 80),
                                (48, 80), (72, 65), (168, 65), (336, 45), (337, 25)]:
            with self.subTest(hours=hours):
                data = context()
                data["signals"][0]["value"] = (NOW + timedelta(hours=hours)).isoformat()
                result = scored(data)
                expected_score = int(
                    (Decimal(expected) * Decimal("0.35") + Decimal(75) * Decimal("0.35") + Decimal(30))
                    .quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                )
                self.assertEqual(result["score"], expected_score)
        data = context()
        data["signals"] = data["signals"][1:]
        self.assertEqual(scored(data)["score_reasons"][0]["note"],
                         "No explicit urgent date; use the 10-point baseline")

    def test_missing_business_data_gives_provisional_score_but_missing_intent_is_null(self):
        for field in ("average_deal_currency", "products", "similar_won_deals"):
            with self.subTest(field=field):
                data = context()
                del data["seller"][field]
                result = scored(data)
                self.assertIsInstance(result["score"], int)
                self.assertIn("Provisional score", result["score_reasons"][2]["note"])
        data = context()
        data["signals"] = data["signals"][:1]
        self.assertIsNone(scored(data)["score"])
        data = context()
        data["seller"]["average_deal_currency"] = "USD"
        self.assertIsInstance(scored(data)["score"], int)
        data = context()
        data["deal"]["status"] = "CLOSED"
        self.assertIsInstance(scored(data)["score"], int)
        data = context()
        data["deal"]["deal_value"] = "0"
        self.assertIsInstance(scored(data)["score"], int)

    def test_amount_and_fit_change_company_score(self):
        high = scored()["score"]
        data = context()
        data["deal"]["deal_value"] = "14999"
        self.assertLess(scored(data)["score"], high)
        data = context()
        data["seller"]["similar_won_deals"] = False
        data["customer"]["industry"] = "Retail"
        self.assertLess(scored(data)["score"], high)

    def test_buying_intent_levels_and_waiting_without_deadline(self):
        for kind, value, expected in [
            ("GENERAL_INQUIRY", None, 69),
            ("PRODUCT_CONFIRMED", None, 76),
            ("QUANTITY_CONFIRMED", 20, 83),
            ("FORMAL_QUOTATION_REQUEST", None, 88),
            ("CONTRACT_DISCUSSION", None, 93),
            ("PURCHASE_CONFIRMATION", None, 97),
        ]:
            with self.subTest(kind=kind):
                data = context()
                data["signals"][1]["type"] = kind
                data["signals"][1]["value"] = value
                self.assertEqual(scored(data)["score"], expected)
        data = context()
        data["signals"][0]["type"] = "CUSTOMER_WAITING"
        data["signals"][0]["value"] = None
        self.assertEqual(scored(data)["score"], 60)
        self.assertEqual(scored(data)["score_reasons"][0]["note"],
                         "No explicit urgent date; use the 10-point baseline")

    def test_historical_deadline_does_not_stay_urgent_forever(self):
        data = context()
        data["signals"][0]["value"] = (NOW - timedelta(days=30)).isoformat()
        self.assertEqual(scored(data)["score_reasons"][0]["note"],
                         "No explicit urgent date; use the 10-point baseline")
        data["signals"][0]["type"] = "OVERDUE_ACTION"
        self.assertGreater(scored(data)["score"], 90)

    def test_date_only_deadline_uses_calendar_day_without_invented_hour(self):
        data = context()
        data["signals"][0]["value"] = "2026-09-19"
        self.assertEqual(scored(data)["score"], 88)  # Today scores 90; do not infer a deadline within four hours.
        data["signals"][0]["value"] = "2026-09-20"
        self.assertEqual(scored(data)["score"], 84)  # Tomorrow scores 80.
        data["signals"][0]["value"] = "2026-09-01"
        self.assertEqual(scored(data)["score_reasons"][0]["note"],
                         "No explicit urgent date; use the 10-point baseline")

    def test_date_only_deadline_uses_seller_time_zone(self):
        data = context()
        data["signals"][0]["value"] = "2026-09-20"
        clock = lambda: datetime(2026, 9, 19, 20, tzinfo=timezone.utc)
        with_zone = compute_score(
            {"status": "completed"}, analysis_input(), clock=clock, priority_context=data,
        )
        self.assertEqual(with_zone["score"], 88)  # It is already September 20 in Singapore.
        del data["seller"]["time_zone"]
        without_zone = compute_score(
            {"status": "completed"}, analysis_input(), clock=clock, priority_context=data,
        )
        self.assertEqual(without_zone["score"], 84)

    def test_signal_source_must_belong_to_company(self):
        data = context()
        data["signals"][0]["source_id"] = "another-company:mail-1"
        with self.assertRaisesRegex(ValueError, "email from this company"):
            scored(data)

    def test_l4_uses_structured_l1_stage_without_model(self):
        data = context()
        data["signals"][1]["type"] = "L4 Evaluating"
        data["communications"] = [{
            "message_id": "sales@example.com:mail-1",
            "sender": "customer",
            "content": "请明天前给正式报价；请给正式报价。",
        }]
        result = compute_score(
            {"status": "completed"}, analysis_input(), clock=lambda: NOW,
            priority_context=data,
        )
        self.assertEqual(result["score"], 88)
        data["signals"][1]["evidence"] = "已经签署合同"
        with self.assertRaisesRegex(ValueError, "evidence"):
            compute_score({"status": "completed"}, analysis_input(), clock=lambda: NOW,
                          priority_context=data)

    def test_l4_missing_deal_uses_known_dimensions(self):
        data = context()
        del data["deal"]
        data["communications"] = [{
            "message_id": "sales@example.com:mail-1", "sender": "customer",
            "content": "请明天前给正式报价；请给正式报价。", "timestamp": NOW.isoformat(),
        }]
        result = compute_score(
            {"status": "completed"}, analysis_input(), clock=lambda: NOW,
            priority_context=data,
        )
        self.assertIsInstance(result["score"], int)
        self.assertIn("Provisional score", result["score_reasons"][2]["note"])

    def test_ranking_is_company_level_and_null_last(self):
        high = scored()
        low = deepcopy(high)
        low["company_id"] = "company-low"
        low["score"] = 50
        missing = deepcopy(high)
        missing["company_id"] = "company-unscored"
        missing["score"] = None
        self.assertEqual(
            [item["company_id"] for item in rank_company_scores([missing, low, high])],
            [COMPANY_ID, "company-low", "company-unscored"],
        )


if __name__ == "__main__":
    unittest.main()
