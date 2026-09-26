"""Known source migrations must preserve validation and publication deduplication."""

import socket
import unittest
from unittest.mock import Mock, patch

from agent import world_insights as world
from agent.tests.test_world_insights import FakeTools, NOW, model


OLD = "https://ec.europa.eu/eurostat/product?code=4-18092026-ap"
NEW = "https://ec.europa.eu/eurostat/en/web/products-euro-indicators/w/4-18092026-ap"
PUBLIC_DNS = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]


class NewsSourceTests(unittest.TestCase):
    def test_verified_eurostat_route_only(self):
        self.assertEqual(world.source_url(OLD), NEW)
        self.assertEqual(world.source_url(NEW), NEW)
        self.assertEqual(world.source_url(OLD + "&utm_source=rss"), NEW)
        for url in (
            "https://example.org/eurostat/product?code=4-18092026-ap",
            OLD + "&next=https%3A%2F%2Flocalhost", OLD + "&code=4-16092026-ap",
            "https://ec.europa.eu/eurostat/product?code=../private",
        ):
            with self.subTest(url=url):
                self.assertNotEqual(world.source_url(url), NEW)
        self.assertIsNone(world.source_url("https://user:secret@ec.europa.eu/eurostat/product?code=4-18092026-ap"))
        self.assertIsNone(world.source_url(OLD.replace("https:", "http:")))

    @patch("agent.world_insights.socket.getaddrinfo", return_value=PUBLIC_DNS)
    @patch("agent.world_insights.requests.get")
    def test_old_record_fetches_canonical_page_without_following_redirects(self, get, dns):
        response = Mock(status_code=200, encoding="utf-8", headers={"Content-Type": "text/html"})
        response.iter_content.return_value = [b'<meta name="date" content="2026-09-18T00:00:00Z"><article><p>Official industry statistics with enough text for extraction.</p></article>']
        get.return_value = response
        excerpt, published, events = world.fetch_page(OLD)
        self.assertIn("Official industry", excerpt)
        self.assertEqual(published.day, 18)
        self.assertEqual(get.call_args.args[0], NEW)
        self.assertFalse(get.call_args.kwargs["allow_redirects"])
        response.close.assert_called_once()

    @patch("agent.world_insights.socket.getaddrinfo", return_value=PUBLIC_DNS)
    @patch("agent.world_insights.requests.get")
    def test_other_redirects_are_still_rejected_without_second_request(self, get, dns):
        response = Mock(status_code=302, headers={"Location": "https://127.0.0.1/secrets"})
        get.return_value = response
        with self.assertRaisesRegex(world.InsightError, "redirected"):
            world.fetch_page("https://example.org/article")
        get.assert_called_once()
        response.close.assert_called_once()

    @patch("agent.world_insights.socket.getaddrinfo", return_value=[
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))])
    @patch("agent.world_insights.requests.get")
    def test_known_migration_does_not_skip_public_address_validation(self, get, dns):
        with self.assertRaisesRegex(world.InsightError, "non-public"):
            world.fetch_page(OLD)
        get.assert_not_called()

    def test_legacy_and_canonical_records_deduplicate_before_fetch(self):
        tools = FakeTools()
        tools.records["world_news.list"] = [{"source_url": OLD}]
        candidate = world.Candidate("news", "equipment", "United States equipment news", NEW, "", NOW)
        sources = {"feeds": [{"kind": "news", "industry": "equipment", "url": "https://example.org/feed"}], "searches": []}
        with (patch("agent.world_insights.read_feed", return_value=[candidate]),
              patch("agent.world_insights.fetch_page") as fetch):
            result = world.run_once(client=tools, sources=sources, model=model, now=NOW)
        self.assertEqual(result["news"], 0)
        fetch.assert_not_called()
        self.assertFalse(tools.writes)

    def test_new_publications_save_canonical_url(self):
        tools = FakeTools()
        candidate = world.Candidate("news", "equipment", "United States equipment news", OLD, "", NOW)
        sources = {"feeds": [{"kind": "news", "industry": "equipment", "url": "https://example.org/feed"}], "searches": []}
        with (patch("agent.world_insights.read_feed", return_value=[candidate]),
              patch("agent.world_insights.fetch_page", return_value=(
                  "An official United States producer announced a new inspection technology.", NOW, [])) as fetch):
            result = world.run_once(client=tools, sources=sources, model=model, now=NOW)
        self.assertEqual(result["news"], 1)
        fetch.assert_called_once_with(NEW)
        self.assertEqual(tools.writes[0][1]["data"]["source_url"], NEW)
