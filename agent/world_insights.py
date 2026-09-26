"""Discover sourced industry news and map events, then publish through business tools.

The module is intentionally separate from L1-L4 and existing chat workers. A systemd
timer runs one bounded pass; the backend remains responsible for permissions and data.
"""

from __future__ import annotations

import argparse
from difflib import SequenceMatcher
import ipaddress
import json
import logging
import math
import os
import re
import socket
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pycountry
import requests
from bs4 import BeautifulSoup

from agent.config import load_environment
from agent.llm.bailian import generate_json
from agent.skills import load_skill
from integrations.salesmate_tools.client import ToolClient, ToolError


logger = logging.getLogger("salesmate.world_insights")
SOURCES_PATH = Path(__file__).with_name("world_insights_sources.json")
GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "SalesMateWorldInsights/1.0 (+https://milkdragon.dev)"
_SKILL = load_skill("world-insights")
_CATEGORIES = frozenset({"regulation", "industry", "competition", "price"})
_SIGNAL_FIELDS = ("company_name", "signal_type", "project_name", "demand_description",
                  "potential_sales_need", "opportunity_reason", "time_window", "evidence",
                  "amount", "currency", "amount_type", "amount_scope", "amount_evidence")
_SIGNAL_TYPES = frozenset({"expansion", "new_factory", "tender", "equipment_upgrade",
                           "procurement", "other"})
_AMOUNT_TYPES = frozenset({"total_investment", "procurement_budget", "tender_amount",
                           "contract_amount", "other"})
_AMOUNT_SCOPES = frozenset({"whole_project", "equipment_procurement", "other"})
_CURRENCY_MARKERS = {"CNY": ("人民币", "元", "CNY", "RMB", "￥"),
                     "USD": ("美元", "USD", "US$"),
                     "EUR": ("欧元", "EUR", "€"),
                     "GBP": ("英镑", "GBP", "£"),
                     "JPY": ("日元", "JPY"), "KRW": ("韩元", "KRW", "₩"),
                     "SGD": ("新加坡元", "SGD", "S$"),
                     "TWD": ("新台币", "TWD", "NT$"),
                     "HKD": ("港元", "HKD", "HK$"),
                     "INR": ("卢比", "INR", "₹"),
                     "CAD": ("加元", "CAD", "C$"),
                     "AUD": ("澳元", "AUD", "A$"),
                     "CHF": ("瑞士法郎", "CHF")}
_AMOUNT_NUMBER = re.compile(r"(?<![\d])(?P<number>\d+(?:,\d{3})*(?:\.\d+)?)\s*"
                            r"(?P<unit>亿|万|千|billion|million|thousand|bn|m)?", re.I)
_AMOUNT_MULTIPLIERS = {"亿": Decimal("100000000"), "万": Decimal("10000"),
                       "千": Decimal("1000"), "billion": Decimal("1000000000"),
                       "million": Decimal("1000000"), "thousand": Decimal("1000"),
                       "bn": Decimal("1000000000"), "m": Decimal("1000000")}
_TRACKING = frozenset({"fbclid", "gclid", "mc_cid", "mc_eid"})
_COUNTRY_ALIASES = {"U.S.": "US", "USA": "US", "UK": "GB", "South Korea": "KR"}
_EVENT_WORDS = re.compile(r"\b(exhibition|expo|trade show|semicon)\b|展会|博览会", re.I)


class InsightError(RuntimeError):
    """A source, model, or tool result cannot safely be published."""


@dataclass(frozen=True)
class Candidate:
    kind: str
    industry: str
    title: str
    url: str
    excerpt: str
    published_at: datetime | None = None


def source_url(value: object) -> str | None:
    """Accept a direct public HTTPS article URL, never a local endpoint or token URL."""
    if not isinstance(value, str) or len(value) > 2000:
        return None
    try:
        parsed = urlsplit(value.strip())
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        return None
    if (parsed.scheme != "https" or not host or parsed.username or parsed.password
            or port not in {None, 443} or host == "localhost"
            or host.endswith((".local", ".internal"))):
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return None
    query = urlencode([(key, val) for key, val in parse_qsl(parsed.query)
                       if not key.lower().startswith("utm_") and key.lower() not in _TRACKING])
    return _canonical_eurostat_url(urlunsplit(("https", host, parsed.path or "/", query, "")))


def _canonical_eurostat_url(url: str) -> str:
    """Known Eurostat indicator migration only; never follow arbitrary Location headers."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return url
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if (parsed.scheme == "https" and parsed.netloc == "ec.europa.eu"
            and parsed.path == "/eurostat/product" and len(pairs) == 1
            and pairs[0][0] == "code" and re.fullmatch(r"4-\d{8}-ap", pairs[0][1])):
        return "https://ec.europa.eu/eurostat/en/web/products-euro-indicators/w/" + pairs[0][1]
    return url


def aware_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        result = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        try:
            result = parsedate_to_datetime(value.strip())
        except (TypeError, ValueError):
            return None
    return result if result.tzinfo is not None and result.utcoffset() is not None else None


def country_code(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    try:
        return pycountry.countries.lookup(_COUNTRY_ALIASES.get(value.strip(), value.strip())).alpha_2
    except LookupError:
        return ""


def _plain(value: str) -> str:
    return " ".join(BeautifulSoup(value, "html.parser").get_text(" ", strip=True).split())


def _source_quote(value: object, evidence: str, max_length: int) -> str:
    if not isinstance(value, str):
        return ""
    quote = " ".join(value.split())
    return quote if 0 < len(quote) <= max_length and quote.casefold() in evidence.casefold() else ""


def _currency_supported(quote: str, code: str) -> bool:
    folded = quote.casefold()
    if not any(marker.casefold() in folded for marker in _CURRENCY_MARKERS[code]):
        return False
    if code == "CNY" and not any(marker.casefold() in folded
                                 for marker in ("人民币", "CNY", "RMB", "￥")):
        foreign = (marker for other, markers in _CURRENCY_MARKERS.items() if other != "CNY"
                   for marker in markers)
        if any(marker.casefold() in folded for marker in foreign):
            return False
    return True


def _sales_signal(parsed: Mapping[str, Any], evidence: str) -> dict[str, Any]:
    """Keep only source-backed public opportunity hints; never invent CRM relationships."""
    result: dict[str, Any] = {field: "" for field in _SIGNAL_FIELDS}
    result["amount"] = None
    quote = _source_quote(parsed.get("evidence"), evidence, 600)
    company = parsed.get("company_name")
    signal_type = parsed.get("signal_type")
    if (not quote or not isinstance(company, str) or not 0 < len(company.strip()) <= 240
            or company.strip().casefold() not in quote.casefold()
            or not isinstance(signal_type, str) or signal_type not in _SIGNAL_TYPES):
        return result
    result["company_name"] = company.strip()
    result["signal_type"] = signal_type
    result["evidence"] = quote
    for field, limit in (("project_name", 240), ("demand_description", 500),
                         ("potential_sales_need", 500), ("opportunity_reason", 500),
                         ("time_window", 240)):
        value = parsed.get(field)
        if isinstance(value, str) and len(value.strip()) <= limit:
            result[field] = value.strip()
    if result["project_name"] and result["project_name"].casefold() not in evidence.casefold():
        result["project_name"] = ""
    if result["time_window"] and result["time_window"].casefold() not in evidence.casefold():
        result["time_window"] = ""
    if result["potential_sales_need"] and not result["opportunity_reason"]:
        result["potential_sales_need"] = ""

    amount_quote = _source_quote(parsed.get("amount_evidence"), evidence, 400)
    currency = parsed.get("currency")
    amount_type = parsed.get("amount_type")
    amount_scope = parsed.get("amount_scope")
    try:
        amount = Decimal(str(parsed.get("amount")))
    except (InvalidOperation, ValueError):
        return result
    if (not amount_quote or amount_quote.casefold() not in quote.casefold()
            or not isinstance(currency, str) or currency not in _CURRENCY_MARKERS
            or not isinstance(amount_type, str) or amount_type not in _AMOUNT_TYPES
            or not isinstance(amount_scope, str) or amount_scope not in _AMOUNT_SCOPES
            or (amount_type == "total_investment" and amount_scope != "whole_project")
            or not amount.is_finite() or amount <= 0
            or not _currency_supported(amount_quote, currency)):
        return result
    for match in _AMOUNT_NUMBER.finditer(amount_quote):
        unit = (match.group("unit") or "").lower()
        source_amount = Decimal(match.group("number").replace(",", ""))
        if source_amount * _AMOUNT_MULTIPLIERS.get(unit, Decimal(1)) == amount:
            result.update(amount=format(amount, "f"), currency=currency,
                          amount_type=amount_type, amount_scope=amount_scope,
                          amount_evidence=amount_quote)
            break
    return result


def _read_limited(response: requests.Response, limit: int) -> bytes:
    chunks, size = [], 0
    for chunk in response.iter_content(65536):
        size += len(chunk)
        if size > limit:
            raise InsightError("External response exceeds the size limit.")
        chunks.append(chunk)
    return b"".join(chunks)


def _json_get(url: str, *, params: Mapping[str, Any], headers: Mapping[str, str]) -> Any:
    try:
        with requests.get(url, params=params, headers=headers, timeout=(5, 15),
                          allow_redirects=False, stream=True) as response:
            response.raise_for_status()
            if response.status_code >= 300:
                raise InsightError("External search redirected unexpectedly.")
            return json.loads(_read_limited(response, 2_000_000))
    except (requests.RequestException, ValueError) as error:
        raise InsightError(f"External search request failed: {type(error).__name__}") from None


def search_gdelt(query: str, industry: str, kind: str, *, limit: int = 12) -> list[Candidate]:
    """Use GDELT's public global article search; sourcecountry is never event geography."""
    result = _json_get(GDELT_URL, params={"query": query, "mode": "artlist", "format": "json",
                                           "timespan": "1week", "sort": "datedesc", "maxrecords": limit},
                       headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    articles = result.get("articles") if isinstance(result, dict) else None
    if not isinstance(articles, list):
        raise InsightError("GDELT did not return an article array.")
    output = []
    for row in articles[:limit]:
        if not isinstance(row, dict):
            continue
        url = source_url(row.get("url"))
        title = _plain(str(row.get("title") or ""))
        if url and title:
            output.append(Candidate(kind, industry, title[:240], url, ""))
    return output


def read_feed(url: str, industry: str, kind: str, *, limit: int = 15) -> list[Candidate]:
    """Read an explicitly configured RSS/Atom source; do not scrape feed links as instructions."""
    if source_url(url) != url:
        raise InsightError("Invalid feed URL.")
    try:
        with requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=(5, 15),
                          allow_redirects=False, stream=True) as response:
            response.raise_for_status()
            if response.status_code >= 300:
                raise InsightError("Feed redirected unexpectedly.")
            root = ET.fromstring(_read_limited(response, 1_000_000))
    except (requests.RequestException, ET.ParseError) as error:
        raise InsightError(f"Feed read failed: {type(error).__name__}") from None

    output = []
    rss = root.findall(".//item")
    atom = root.findall(".//{http://www.w3.org/2005/Atom}entry")
    for item in (rss or atom)[:limit]:
        if rss:
            title = item.findtext("title") or ""
            link = item.findtext("link") or ""
            excerpt = item.findtext("description") or ""
            published = item.findtext("pubDate") or ""
        else:
            ns = "{http://www.w3.org/2005/Atom}"
            title = item.findtext(ns + "title") or ""
            link_node = item.find(ns + "link")
            link = link_node.get("href", "") if link_node is not None else ""
            excerpt = item.findtext(ns + "summary") or item.findtext(ns + "content") or ""
            published = item.findtext(ns + "published") or item.findtext(ns + "updated") or ""
        safe = source_url(link)
        if safe and _plain(title):
            output.append(Candidate(kind, industry, _plain(title)[:240], safe,
                                    _plain(excerpt)[:1800], aware_time(published)))
    return output


def fetch_page(url: str) -> tuple[str, datetime | None, list[dict[str, Any]]]:
    """Read bounded public HTML to verify dates and structured events."""
    canonical = _canonical_eurostat_url(url)
    if canonical != url:
        logger.info("world_source_url_normalized source_host=ec.europa.eu migration=eurostat_indicator")
        url = canonical
    if source_url(url) != url:
        raise InsightError("Unsafe article URL.")
    host = urlsplit(url).hostname
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        raise InsightError("Source hostname resolution failed.") from None
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise InsightError("Source hostname resolves to a non-public address.")
    try:
        response = requests.get(url, headers={"User-Agent": USER_AGENT},
                                timeout=(5, 15), allow_redirects=False, stream=True)
        response.raise_for_status()
        if response.status_code >= 300:
            raise InsightError("Source page redirected unexpectedly.")
        if "html" not in response.headers.get("Content-Type", "").lower():
            raise InsightError("Source is not HTML.")
        chunks, size = [], 0
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > 1_000_000:
                raise InsightError("Source page exceeds the size limit.")
            chunks.append(chunk)
        try:
            page_text = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
        except LookupError:
            page_text = b"".join(chunks).decode("utf-8", errors="replace")
        soup = BeautifulSoup(page_text, "html.parser")
    except requests.RequestException as error:
        raise InsightError(f"Source page read failed: {type(error).__name__}") from None
    finally:
        if "response" in locals():
            response.close()

    published = None
    for attrs in ({"property": "article:published_time"}, {"name": "date"},
                  {"itemprop": "datePublished"}):
        node = soup.find("meta", attrs=attrs)
        published = aware_time(node.get("content")) if node else None
        if published:
            break
    events = []
    for node in soup.find_all("script", type="application/ld+json"):
        if not node.string or len(node.string) > 200_000:
            continue
        try:
            parsed = json.loads(node.string)
        except ValueError:
            continue
        stack = parsed if isinstance(parsed, list) else [parsed]
        while stack:
            item = stack.pop()
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("@graph"), list):
                stack.extend(item["@graph"])
            kinds = item.get("@type", [])
            if "Event" in ([kinds] if isinstance(kinds, str) else kinds):
                events.append(item)
            if published is None and item.get("datePublished"):
                published = aware_time(item["datePublished"])
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    main = soup.find("article") or soup.find("main") or soup
    paragraphs = [tag.get_text(" ", strip=True) for tag in main.find_all(["p", "h1", "h2"])]
    excerpt = " ".join(part for part in paragraphs if len(part) >= 25)[:5000]
    return _plain(excerpt), published, events


def summarize_news(candidate: Candidate, excerpt: str, model: Callable[..., str]) -> dict[str, Any] | None:
    evidence = "\n".join(part for part in (candidate.title, candidate.excerpt, excerpt[:3500]) if part)
    if len(evidence) < 60:
        return None
    raw = model(_SKILL.instructions, json.dumps({"industry": candidate.industry,
                                                  "title": candidate.title, "excerpt": evidence},
                                                 ensure_ascii=False), max_tokens=_SKILL.max_tokens)
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        raise InsightError("News summary model did not return JSON.") from None
    news_fields = {"relevant", "category", "industry", "country",
                   "country_evidence", "summary", "content"}
    if (not isinstance(parsed, dict) or not news_fields <= set(parsed)
            or set(parsed) - news_fields - set(_SIGNAL_FIELDS)):
        raise InsightError("News summary fields do not match the contract.")
    if parsed["relevant"] is False:
        return None
    if parsed["relevant"] is not True or parsed["category"] not in _CATEGORIES:
        raise InsightError("Invalid news relevance or category.")
    for key, max_length in (("industry", 100), ("summary", 300), ("content", 1200)):
        if not isinstance(parsed[key], str) or not 0 < len(parsed[key].strip()) <= max_length:
            raise InsightError(f"News {key} is invalid.")
    country, marker = parsed["country"], parsed["country_evidence"]
    if not isinstance(country, str) or not isinstance(marker, str):
        raise InsightError("Invalid news country field.")
    if country:
        if (country != country_code(marker) or not marker.strip()
                or marker.casefold() not in evidence.casefold()):
            raise InsightError("News country lacks verifiable textual evidence.")
    elif marker:
        raise InsightError("An unknown country must not have location evidence.")
    payload = {"title": candidate.title, "category": parsed["category"],
               "industry": parsed["industry"].strip(), "country": country,
               "summary": parsed["summary"].strip(),
               "content": parsed["content"].strip(),
               "source_url": candidate.url, "published_at": candidate.published_at.isoformat(),
               "data_source": "agent"}
    payload.update(_sales_signal(parsed, evidence))
    return payload


class Geocoder:
    """Rate-limited, persistent cached city lookup on public Nominatim."""

    def __init__(self, path: Path, *, pause: Callable[[float], None] = time.sleep,
                 monotonic: Callable[[], float] = time.monotonic):
        self.path, self.pause, self.monotonic = path, pause, monotonic
        self.last_request = float("-inf")
        try:
            self.cache = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(self.cache, dict):
                self.cache = {}
        except (OSError, ValueError):
            self.cache = {}

    def lookup(self, city: str, country: str) -> tuple[float, float] | None:
        key = country + ":" + city.casefold()
        if key in self.cache:
            cached = self.cache[key]
            return tuple(cached) if isinstance(cached, list) and len(cached) == 2 else None
        delay = 1.1 - (self.monotonic() - self.last_request)
        if delay > 0:
            self.pause(delay)
        self.last_request = self.monotonic()
        result = _json_get(NOMINATIM_URL,
                           params={"city": city, "countrycodes": country.lower(),
                                   "format": "jsonv2", "addressdetails": 1, "limit": 1,
                                   "accept-language": "en"},
                           headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        coords = None
        if isinstance(result, list) and result and isinstance(result[0], dict):
            row = result[0]
            address = row.get("address") or {}
            place = next((address.get(field) for field in ("city", "town", "village", "municipality")
                          if address.get(field)), None)
            try:
                lat, lon = float(row["lat"]), float(row["lon"])
            except (TypeError, ValueError, KeyError):
                pass
            else:
                if (isinstance(address, dict) and address.get("country_code", "").upper() == country
                        and isinstance(place, str) and place.casefold() == city.casefold()
                        and math.isfinite(lat) and math.isfinite(lon)
                        and -90 <= lat <= 90 and -180 <= lon <= 180):
                    coords = (lat, lon)
        self.cache[key] = list(coords) if coords else None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(self.path.name + ".tmp")
        temp.write_text(json.dumps(self.cache, ensure_ascii=False), encoding="utf-8")
        temp.replace(self.path)
        return coords


def build_event(candidate: Candidate, events: list[dict[str, Any]],
                geocode: Callable[[str, str], tuple[float, float] | None],
                now: datetime) -> dict[str, Any] | None:
    """Only structured Event pages with dates and a verified city become map points."""
    for event in events:
        title = _plain(str(event.get("name") or ""))
        description = _plain(str(event.get("description") or candidate.excerpt))[:1800]
        if not title or not _EVENT_WORDS.search(" ".join((title, candidate.excerpt, description))):
            continue
        event_url = source_url(event.get("url")) if event.get("url") else None
        event_host = (urlsplit(event_url).hostname or "").removeprefix("www.") if event_url else ""
        candidate_host = (urlsplit(candidate.url).hostname or "").removeprefix("www.")
        same_page = bool(event_url and event_host == candidate_host
                         and urlsplit(event_url).path.rstrip("/") == urlsplit(candidate.url).path.rstrip("/"))
        similar_title = SequenceMatcher(None, title.casefold(), candidate.title.casefold()).ratio() >= 0.55
        if not same_page and not similar_title:
            continue
        start, end = aware_time(event.get("startDate")), aware_time(event.get("endDate"))
        date_only = False
        if not start and not end:
            raw_start, raw_end = event.get("startDate"), event.get("endDate")
            if (isinstance(raw_start, str) and isinstance(raw_end, str)
                    and re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_start)
                    and re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_end)):
                try:
                    start = datetime.fromisoformat(raw_start).replace(hour=12, tzinfo=timezone.utc)
                    end = datetime.fromisoformat(raw_end).replace(hour=12, tzinfo=timezone.utc) + timedelta(days=1)
                    date_only = True
                except ValueError:
                    pass
        if not start or not end or end <= start or end <= now or start > now + timedelta(days=365):
            continue
        location = event.get("location")
        if not isinstance(location, dict):
            continue
        address = location.get("address")
        if not isinstance(address, dict):
            continue
        city = address.get("addressLocality")
        country = address.get("addressCountry")
        if isinstance(country, dict):
            country = country.get("name") or country.get("identifier")
        code = country_code(country)
        if not isinstance(city, str) or not city.strip() or not code:
            continue
        city = city.strip()
        coords = geocode(city, code)
        if coords is None:
            continue
        if len(description) < 30:
            continue
        if date_only:
            description += "\nThe source provides a date only; start/end times are system placeholders. Check the source page."
        return {"title": title[:240], "event_type": "exhibition", "country": code,
                "city": city[:120], "latitude": coords[0], "longitude": coords[1],
                "starts_at": start.isoformat(), "ends_at": end.isoformat(),
                "registration_deadline": None, "source_url": candidate.url,
                "description": (description + "\nCoordinates: © OpenStreetMap contributors (Nominatim).").strip(),
                "onsite": [], "suggested_actions": [], "opportunity_ids": [], "data_source": "agent"}
    return None


def existing_urls(client: ToolClient, tool: str) -> set[str]:
    """Read all authorized pages so archived and older records are not recreated."""
    urls = set()
    for page in range(1, 101):
        reply = client.call(tool, {"page": page, "page_size": 100, "archived": "all"})
        data = reply.get("data") if isinstance(reply, dict) else None
        if not isinstance(reply, dict) or reply.get("status") != "completed" or not isinstance(data, dict):
            raise InsightError(f"{tool} list receipt is invalid.")
        rows, count = data.get("results"), data.get("count")
        if not isinstance(rows, list) or type(count) is not int:
            raise InsightError(f"{tool} page receipt is invalid.")
        urls.update(url for row in rows if isinstance(row, dict)
                    if (url := source_url(row.get("source_url"))))
        if page * 100 >= count:
            return urls
    raise InsightError(f"{tool} exceeds 100 pages; cannot safely deduplicate.")


def load_sources(path: Path = SOURCES_PATH) -> dict[str, list[dict[str, str]]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise InsightError(f"Source configuration read failed: {type(error).__name__}") from None
    if not isinstance(data, dict) or set(data) != {"searches", "feeds"}:
        raise InsightError("Invalid source configuration fields.")
    if not data["searches"] and not data["feeds"]:
        raise InsightError("Source configuration cannot be entirely empty.")
    for section, field in (("searches", "query"), ("feeds", "url")):
        rows = data[section]
        if not isinstance(rows, list):
            raise InsightError("Source configuration must be an array.")
        for row in rows:
            if (not isinstance(row, dict) or set(row) != {"kind", "industry", field}
                    or row["kind"] not in {"news", "event"}
                    or not all(isinstance(row[key], str) and row[key].strip() for key in row)):
                raise InsightError("Invalid source configuration entry.")
            if field == "url" and source_url(row[field]) != row[field]:
                raise InsightError("Feed must be a public HTTPS URL.")
    return data


def run_once(*, client: ToolClient | None, sources: Mapping[str, list[dict[str, str]]],
             model: Callable[..., str] = generate_json, dry_run: bool = False,
             now: datetime | None = None, geocoder: Geocoder | None = None) -> dict[str, Any]:
    """Run a bounded pass; one bad article is isolated, source outages are reported."""
    now = now or datetime.now(timezone.utc)
    if not dry_run and client is None:
        raise InsightError("Production run requires a Tool client.")
    existing = {"news": set(), "event": set()}
    if client is not None and not dry_run:
        existing["news"] = existing_urls(client, "world_news.list")
        existing["event"] = existing_urls(client, "world_events.list")
    geocoder = geocoder or Geocoder(Path(os.environ.get("SALESMATE_WORLD_GEO_CACHE", ""))
                                     if os.environ.get("SALESMATE_WORLD_GEO_CACHE")
                                     else Path.home() / ".cache" / "salesmate" / "world-geocodes.json")
    candidates, source_errors, source_successes = [], 0, 0
    for item in sources["feeds"]:
        try:
            candidates.extend(read_feed(item["url"], item["industry"], item["kind"],
                                        limit=50 if item["kind"] == "event" else 15))
            source_successes += 1
        except InsightError as error:
            source_errors += 1
            logger.warning("world_source_failed kind=feed error_type=%s", type(error).__name__)
    for item in sources["searches"]:
        try:
            candidates.extend(search_gdelt(item["query"], item["industry"], item["kind"]))
            source_successes += 1
        except InsightError as error:
            source_errors += 1
            logger.warning("world_source_failed kind=search error_type=%s", type(error).__name__)

    seen, counts, previews = set(), {"news": 0, "event": 0}, []
    item_errors, write_errors = 0, 0
    for candidate in candidates:
        canonical = source_url(candidate.url)
        if canonical is None:
            item_errors += 1
            logger.warning("world_item_failed kind=%s stage=source error_type=InsightError reason=unsafe_url", candidate.kind)
            continue
        if canonical != candidate.url:
            candidate = Candidate(candidate.kind, candidate.industry, candidate.title,
                                  canonical, candidate.excerpt, candidate.published_at)
        if counts[candidate.kind] >= (4 if candidate.kind == "news" else 2):
            continue
        if (candidate.kind, candidate.url) in seen or candidate.url in existing[candidate.kind]:
            continue
        seen.add((candidate.kind, candidate.url))
        if (candidate.kind == "news" and candidate.published_at
                and not now - timedelta(days=14) <= candidate.published_at <= now):
            continue
        stage = "source"
        try:
            try:
                excerpt, published, events = fetch_page(candidate.url)
            except InsightError:
                if candidate.kind != "news" or candidate.published_at is None or len(candidate.excerpt) < 160:
                    raise
                logger.info("world_news_feed_excerpt_used source_host=%s", urlsplit(candidate.url).hostname)
                excerpt, published, events = candidate.excerpt, candidate.published_at, []
            if candidate.kind == "news":
                stage = "news_prepare"
                published = published or candidate.published_at
                if not published or not now - timedelta(days=14) <= published <= now:
                    continue
                candidate = Candidate(candidate.kind, candidate.industry, candidate.title,
                                      candidate.url, candidate.excerpt, published)
                payload = summarize_news(candidate, excerpt, model)
            else:
                stage = "event_prepare"
                payload = build_event(candidate, events, geocoder.lookup, now)
            if payload is None:
                continue
            name = "world_news.create" if candidate.kind == "news" else "world_events.create"
            if dry_run:
                previews.append({"tool": name, "data": payload})
            else:
                stage = "write"
                key = str(uuid.uuid5(uuid.NAMESPACE_URL, name + ":" + candidate.url))
                reply = client.call(name, {"data": payload}, idempotency_key=key)
                if reply.get("status") != "completed":
                    raise InsightError("Tool write did not complete.")
            counts[candidate.kind] += 1
            logger.info("world_item_processed kind=%s source_host=%s dry_run=%s",
                        candidate.kind, urlsplit(candidate.url).hostname, dry_run)
        except Exception as error:
            item_errors += 1
            write_errors += int(stage == "write")
            logger.warning("world_item_failed kind=%s stage=%s source_host=%s error_type=%s reason=%s",
                           candidate.kind, stage, urlsplit(candidate.url).hostname,
                           type(error).__name__, str(error)[:300] if isinstance(error, InsightError) else "unavailable")
    return {"news": counts["news"], "events": counts["event"],
            "source_errors": source_errors, "source_successes": source_successes,
            "item_errors": item_errors, "write_errors": write_errors,
            "preview": previews if dry_run else []}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one World Insights discovery pass")
    parser.add_argument("--sources", type=Path, default=SOURCES_PATH)
    parser.add_argument("--dry-run", action="store_true", help="search and prepare without writing")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    load_environment()
    try:
        result = run_once(client=None if args.dry_run else ToolClient.from_env(),
                          sources=load_sources(args.sources), dry_run=args.dry_run)
    except (InsightError, ToolError) as error:
        logger.error("world_run_failed error_type=%s reason=%s", type(error).__name__, error)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    if result["write_errors"] and not (result["news"] or result["events"]):
        return 3
    return 2 if result["source_successes"] == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
