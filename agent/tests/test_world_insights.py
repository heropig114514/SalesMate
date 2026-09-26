"""Offline contract tests for the World Insights collector."""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from agent.world_insights import (
    Candidate,
    Geocoder,
    InsightError,
    _read_limited,
    build_event,
    existing_urls,
    main,
    run_once,
    source_url,
    summarize_news,
)


NOW = datetime(2026, 9, 24, 8, tzinfo=timezone.utc)
NEWS_URL = "https://example.org/news/inspection"
EVENT_URL = "https://events.example.org/semiconductor-expo"


class FakeTools:
    def __init__(self):
        self.records = {"world_news.list": [], "world_events.list": []}
        self.writes = []

    def call(self, name, arguments, idempotency_key=None):
        if name.endswith(".list"):
            rows = self.records[name]
            start = (arguments["page"] - 1) * arguments["page_size"]
            return {"status": "completed", "data": {
                "results": rows[start:start + arguments["page_size"]], "count": len(rows)}}
        self.writes.append((name, arguments, idempotency_key))
        return {"status": "completed", "data": {"id": "saved"}}


def model(_system, _user, *, max_tokens):
    assert max_tokens > 0
    return json.dumps({
        "relevant": True, "category": "industry", "industry": "semiconductor equipment",
        "country": "US", "country_evidence": "United States",
        "summary": "美国半导体设备公司推出新检测技术。",
        "content": "该技术面向晶圆检测；来源片段未提供商业化时间。",
    })


class WorldInsightsTests(unittest.TestCase):
    def test_response_limit_rejects_stream_before_full_download(self):
        class StreamingResponse:
            def iter_content(self, _size):
                yield b"a" * 64
                yield b"b" * 64
                raise AssertionError("read past limit")

        with self.assertRaises(InsightError):
            _read_limited(StreamingResponse(), 100)

    def test_existing_urls_reads_all_pages_including_archived(self):
        tools = FakeTools()
        tools.records["world_news.list"] = [
            {"source_url": f"https://example.org/news/{number}"} for number in range(101)
        ]
        urls = existing_urls(tools, "world_news.list")
        self.assertEqual(len(urls), 101)
        self.assertIn("https://example.org/news/100", urls)

    def test_publish_news_and_map_event_then_skip_existing_sources(self):
        tools = FakeTools()
        news = Candidate("news", "semiconductor equipment", "United States inspection technology",
                         NEWS_URL, "", NOW - timedelta(hours=2))
        event = Candidate("event", "semiconductor equipment", "Semiconductor Expo 2026",
                          EVENT_URL, "", NOW)
        details = [{
            "@type": "Event", "name": "Semiconductor Expo 2026", "url": EVENT_URL,
            "startDate": (NOW + timedelta(days=5)).isoformat(),
            "endDate": (NOW + timedelta(days=7)).isoformat(),
            "description": "An exhibition of equipment and measurement technology for semiconductor manufacturing.",
            "location": {"address": {"addressLocality": "Singapore", "addressCountry": "SG"}},
        }]
        sources = {"searches": [{"kind": "news", "industry": "equipment", "query": "a"}],
                   "feeds": [{"kind": "event", "industry": "equipment", "url": EVENT_URL}]}
        with tempfile.TemporaryDirectory() as root:
            geo = Geocoder(Path(root) / "geocodes.json")
            with (patch("agent.world_insights.search_gdelt", return_value=[news]),
                  patch("agent.world_insights.read_feed", return_value=[event]),
                  patch("agent.world_insights.fetch_page", side_effect=lambda url: (
                      ("Event page", None, details) if url == EVENT_URL else
                      ("A United States equipment producer reported a new inspection technology.", news.published_at, [])
                  )), patch.object(geo, "lookup", return_value=(1.3521, 103.8198))):
                result = run_once(client=tools, sources=sources, model=model, geocoder=geo, now=NOW)
        self.assertEqual((result["news"], result["events"], result["item_errors"]), (1, 1, 0))
        self.assertEqual([row[0] for row in tools.writes], ["world_events.create", "world_news.create"])
        self.assertEqual(tools.writes[1][1]["data"]["country"], "US")
        self.assertEqual(tools.writes[1][1]["data"]["summary"], "美国半导体设备公司推出新检测技术。")
        self.assertEqual(tools.writes[1][1]["data"]["content"], "该技术面向晶圆检测；来源片段未提供商业化时间。")
        self.assertEqual(tools.writes[1][1]["data"]["company_name"], "")
        self.assertIsNone(tools.writes[1][1]["data"]["amount"])
        self.assertEqual(tools.writes[0][1]["data"]["city"], "Singapore")
        self.assertEqual(tools.writes[0][1]["data"]["latitude"], 1.3521)
        self.assertTrue(all(row[2] for row in tools.writes))
        tools.records["world_news.list"] = [{"source_url": NEWS_URL}]
        tools.records["world_events.list"] = [{"source_url": EVENT_URL}]
        self.assertEqual(existing_urls(tools, "world_news.list"), {NEWS_URL})
        with (patch("agent.world_insights.search_gdelt", return_value=[news]),
              patch("agent.world_insights.read_feed", return_value=[event]),
              patch("agent.world_insights.fetch_page") as fetch):
            result = run_once(client=tools, sources=sources, model=model, now=NOW)
        self.assertEqual((result["news"], result["events"]), (0, 0))
        fetch.assert_not_called()

    def test_reject_unverified_geography_dates_and_urls(self):
        for url in ("http://example.org", "https://localhost/a", "https://127.0.0.1/a",
                    "https://user:token@example.org/a", "https://example.org:invalid/a"):
            self.assertIsNone(source_url(url))
        candidate = Candidate("event", "equipment", "Semiconductor Expo 2026", EVENT_URL, "")
        event = {"name": "Semiconductor Expo 2026", "startDate": "2026-10-01T09:00:00",
                 "endDate": "2026-10-02T17:00:00+08:00",
                 "description": "A semiconductor manufacturing and metrology equipment exhibition.",
                 "location": {"address": {"addressLocality": "Singapore", "addressCountry": "SG"}}}
        self.assertIsNone(build_event(candidate, [event], lambda *_: (1.3, 103.8), NOW))
        event["startDate"] = "2026-10-01T09:00:00+08:00"
        event["name"] = "Unrelated exhibition in another city"
        self.assertIsNone(build_event(candidate, [event], lambda *_: (1.3, 103.8), NOW))
        news = Candidate("news", "equipment", "United States equipment technology",
                         NEWS_URL, "", NOW)
        self.assertIsNone(summarize_news(news, "Short", model))

    def test_source_backed_sales_signal_and_amount_are_submitted(self):
        tools = FakeTools()
        candidate = Candidate("news", "optical inspection", "启明光学新建检测基地",
                              NEWS_URL, "", NOW)
        excerpt = ("启明光学计划在 2027 年新建检测基地，总投资 2 亿元，"
                   "其中设备采购预算 5000 万元，预计 2027 年投产。")

        def signal_model(system, user, *, max_tokens):
            parsed = json.loads(model(system, user, max_tokens=max_tokens))
            parsed.update(country="", country_evidence="", company_name="启明光学",
                          signal_type="new_factory", project_name="检测基地",
                          demand_description="披露了设备采购预算。",
                          potential_sales_need="可能需要光学检测设备。",
                          opportunity_reason="新基地设有设备采购预算。",
                          time_window="2027 年投产", evidence=excerpt,
                          amount="50000000", currency="CNY",
                          amount_type="procurement_budget", amount_scope="equipment_procurement",
                          amount_evidence="设备采购预算 5000 万元")
            return json.dumps(parsed, ensure_ascii=False)

        sources = {"feeds": [{"kind": "news", "industry": "optical inspection", "url": NEWS_URL}],
                   "searches": []}
        with (patch("agent.world_insights.read_feed", return_value=[candidate]),
              patch("agent.world_insights.fetch_page", return_value=(excerpt, NOW, []))):
            result = run_once(client=tools, sources=sources, model=signal_model, now=NOW)
        self.assertEqual((result["news"], result["item_errors"]), (1, 0))
        data = tools.writes[0][1]["data"]
        self.assertEqual(data["company_name"], "启明光学")
        self.assertEqual(data["signal_type"], "new_factory")
        self.assertEqual(data["amount"], "50000000")
        self.assertEqual(data["currency"], "CNY")
        self.assertEqual(data["amount_scope"], "equipment_procurement")

        def unsupported_amount(system, user, *, max_tokens):
            parsed = json.loads(signal_model(system, user, max_tokens=max_tokens))
            parsed["amount"] = "200000000"
            return json.dumps(parsed, ensure_ascii=False)

        unsupported = summarize_news(candidate, excerpt, unsupported_amount)
        self.assertIsNone(unsupported["amount"])
        self.assertEqual(unsupported["amount_evidence"], "")

        def malformed_signal(system, user, *, max_tokens):
            parsed = json.loads(signal_model(system, user, max_tokens=max_tokens))
            parsed["signal_type"] = ["new_factory"]
            return json.dumps(parsed, ensure_ascii=False)

        preserved_news = summarize_news(candidate, excerpt, malformed_signal)
        self.assertEqual(preserved_news["company_name"], "")
        self.assertEqual(preserved_news["title"], candidate.title)

    def test_dated_feed_excerpt_survives_article_redirect(self):
        tools = FakeTools()
        candidate = Candidate("news", "equipment", "United States inspection technology",
                              NEWS_URL, "An official United States report describes a new optical inspection system "
                              "for semiconductor equipment manufacturing and provides measured performance data. "
                              "The release describes the process and the publication date in its RSS entry.", NOW)
        sources = {"searches": [], "feeds": [{"kind": "news", "industry": "equipment", "url": NEWS_URL}]}
        with (patch("agent.world_insights.read_feed", return_value=[candidate]),
              patch("agent.world_insights.fetch_page", side_effect=InsightError("redirect"))):
            result = run_once(client=tools, sources=sources, model=model, now=NOW)
        self.assertEqual(result["news"], 1)
        self.assertEqual(result["item_errors"], 0)

    def test_date_only_exhibition_is_marked_as_scheduled_date_without_claimed_clock_time(self):
        candidate = Candidate("event", "semiconductors", "NEPCON ASIA 2026", EVENT_URL,
                              "Electronics manufacturing exhibition in Shenzhen, China.")
        event = {"name": "NEPCON ASIA 2026", "url": EVENT_URL,
                 "startDate": "2026-10-27", "endDate": "2026-10-29",
                 "description": "Semiconductor packaging and test equipment event for regional manufacturers.",
                 "location": {"address": {"addressLocality": "Shenzhen", "addressCountry": "China"}}}
        payload = build_event(candidate, [event], lambda *_: (22.54, 114.05), NOW)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["country"], "CN")
        self.assertIn("The source provides a date only", payload["description"])
        self.assertEqual(payload["starts_at"], "2026-10-27T12:00:00+00:00")

    def test_one_source_failure_does_not_fail_another_empty_source(self):
        sources = {"feeds": [{"kind": "news", "industry": "equipment", "url": NEWS_URL}],
                   "searches": [{"kind": "news", "industry": "equipment", "query": "equipment"}]}
        with (patch("agent.world_insights.read_feed", return_value=[]),
              patch("agent.world_insights.search_gdelt", side_effect=InsightError("unavailable"))):
            result = run_once(client=None, sources=sources, dry_run=True, now=NOW)
        self.assertEqual(result["source_successes"], 1)
        self.assertEqual(result["source_errors"], 1)
        self.assertEqual((result["news"], result["events"]), (0, 0))

    def test_rejected_write_is_visible_and_exits_nonzero_when_nothing_was_saved(self):
        class RejectingTools(FakeTools):
            def call(self, name, arguments, idempotency_key=None):
                if name == "world_news.create":
                    raise InsightError("unsupported fields")
                return super().call(name, arguments, idempotency_key)

        candidate = Candidate("news", "equipment", "United States inspection technology",
                              NEWS_URL, "", NOW)
        sources = {"feeds": [{"kind": "news", "industry": "equipment", "url": NEWS_URL}],
                   "searches": []}
        with (patch("agent.world_insights.read_feed", return_value=[candidate]),
              patch("agent.world_insights.fetch_page", return_value=(
                  "A United States equipment producer reported a new optical inspection technology.",
                  NOW, []))):
            result = run_once(client=RejectingTools(), sources=sources, model=model, now=NOW)
        self.assertEqual((result["news"], result["item_errors"], result["write_errors"]), (0, 1, 1))
        with (patch("agent.world_insights.load_environment"),
              patch("agent.world_insights.load_sources", return_value=sources),
              patch("agent.world_insights.ToolClient.from_env", return_value=RejectingTools()),
              patch("agent.world_insights.run_once", return_value=result)):
            self.assertEqual(main([]), 3)


if __name__ == "__main__":
    unittest.main()
