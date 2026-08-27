#!/usr/bin/env python3
"""
Discover a venue's official website, upcoming-shows page, and ticketing platform.

Network-free helpers used by fetch_venue_web.py. The unique grain is Google Place ID.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse

# ---------------------------------------------------------------------------
# Ticketing platforms (host fragments -> canonical name)
# ---------------------------------------------------------------------------
PLATFORM_HOSTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ticketmaster", ("ticketmaster.", "livenation.")),
    ("dice", ("dice.fm", "on.dice.fm")),
    ("eventbrite", ("eventbrite.",)),
    ("see_tickets", ("seetickets.",)),
    ("axs", ("axs.com",)),
    ("songkick", ("songkick.com",)),
    ("ticketweb", ("ticketweb.com",)),
    ("etix", ("etix.com",)),
    ("showclix", ("showclix.com",)),
    ("tixr", ("tixr.com",)),
    ("posh", ("posh.vip",)),
    ("shotgun", ("shotgun.live", "shotgun.gg")),
    ("resident_advisor", ("ra.co", "residentadvisor.net")),
    ("universe", ("universe.com",)),
    ("frontgate", ("frontgatetickets.com",)),
    ("ticketfly", ("ticketfly.com",)),
    ("eventim", ("eventim.",)),
    ("prekindle", ("prekindle.com",)),
    ("holdmyticket", ("holdmyticket.com",)),
    ("ticketspice", ("ticketspice.com",)),
    ("brownpapertickets", ("brownpapertickets.com", "bpt.me")),
    ("ticketleap", ("ticketleap.com",)),
    ("fever", ("feverup.com", "fever.app")),
    ("simpletix", ("simpletix.com",)),
    ("showpass", ("showpass.com",)),
    ("evenue", ("evenue.net",)),
    ("audienceview", ("audienceview.",)),
    ("moshtix", ("moshtix.",)),
)

# Hosts that are never the venue's official website.
SKIP_HOSTS = frozenset({
    "facebook.com", "m.facebook.com", "instagram.com", "twitter.com", "x.com",
    "youtube.com", "youtu.be", "tiktok.com", "threads.net", "linkedin.com",
    "wikipedia.org", "en.wikipedia.org", "yelp.com", "tripadvisor.com",
    "google.com", "maps.google.com", "goo.gl", "bing.com", "duckduckgo.com",
    "apple.com", "foursquare.com", "mapquest.com", "yellowpages.com",
    "timeout.com", "tripadvisor.com",
    "tickets-center.com", "boxofficeticketsales.com", "stubhub.com",
    "vividseats.com", "seatgeek.com", "ticketnetwork.com", "gametime.co",
    "tickpick.com", "viagogo.com", "tes.com",
    "songkick.com", "bandsintown.com", "concertarchives.org", "setlist.fm",
    "last.fm", "rateyourmusic.com", "discogs.com",
    "washington.org", "archive.org", "web.archive.org", "geohack.toolforge.org",
    "census.gov", "merriam-webster.com", "wiktionary.org", "imdb.com",
    "fandom.com", "steampowered.com",
})

SKIP_HOST_SUFFIXES = (
    ".gov",  # city tourism pages sometimes; keep if name matches later
)

AD_HOST_HINTS = (
    "tickets-center", "boxofficeticket", "stubhub", "vividseats", "seatgeek",
    "ticketnetwork", "gametime", "tickpick", "viagogo", "y.js",
)

SHOWS_PATH_HINTS = (
    "event", "events", "show", "shows", "calendar", "concert", "concerts",
    "ticket", "tickets", "upcoming", "lineup", "gig", "gigs", "onsale",
    "on-sale", "performances", "whats-on", "whatson", "schedule", "listings",
    "buy-tickets", "showtimes",
)

SHOWS_TEXT_HINTS = (
    "upcoming", "shows", "events", "calendar", "tickets", "concerts",
    "lineup", "on sale", "what's on", "whats on", "schedule", "buy tickets",
)

SHOWS_NEGATIVE = (
    "about", "contact", "privacy", "faq", "job", "career", "menu", "gift",
    "shop", "store", "blog", "news", "press", "login", "account", "cart",
    "terms", "rental", "private-event", "weddings", "polic", "policy",
    "policies", "accessibility", "volunteer", "donate", "sponsor",
)

COMMON_SHOWS_PATHS = (
    "/events", "/shows", "/calendar", "/tickets", "/upcoming",
    "/concerts", "/whats-on", "/event-calendar", "/on-sale",
)

EVENT_DETAIL_PATH = re.compile(r"/(e|event|events|show|shows)(/|$)", re.I)


def origin_url(url: str) -> str:
    parsed = urlparse(url)
    return parsed._replace(path="/", params="", query="", fragment="").geturl()


def canonical_website(url: str | None) -> str | None:
    """Prefer the site root when search landed on an event-detail path."""
    if not url:
        return None
    parsed = urlparse(url)
    if EVENT_DETAIL_PATH.search(parsed.path or ""):
        return origin_url(url)
    return url


NAME_STOPWORDS = frozenset({
    "the", "and", "of", "at", "club", "theatre", "theater", "hall", "center",
    "centre", "arena", "ballroom", "tavern", "bar", "cafe", "café", "room",
    "music", "live", "venue", "performing", "arts", "auditorium", "pavilion",
    "amphitheater", "amphitheatre", "house", "lounge", "grill", "kitchen",
})


def hostname(url: str | None) -> str:
    if not url:
        return ""
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def registrable_host(url: str | None) -> str:
    host = hostname(url)
    parts = host.split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


def normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return None
    # Drop tracking query noise; keep meaningful query (event ids).
    return parsed._replace(fragment="").geturl()


def name_tokens(name: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", (name or "").lower())
    return [token for token in tokens if token not in NAME_STOPWORDS and len(token) > 2]


def host_matches_name(url: str, name: str) -> bool:
    host = hostname(url).replace("-", "").replace(".", "")
    compact_name = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    if compact_name and compact_name[:6] in host:
        return True
    tokens = name_tokens(name)
    if not tokens:
        return False
    joined = "".join(tokens)
    if joined and joined in host:
        return True
    hits = sum(1 for token in tokens if token in host)
    return hits >= max(1, min(2, len(tokens)))


def classify_platform(url: str | None) -> str | None:
    host = hostname(url)
    if not host:
        return None
    for platform, fragments in PLATFORM_HOSTS:
        for fragment in fragments:
            if fragment in host or host.startswith(fragment.rstrip(".")):
                return platform
    return None


def is_skip_host(url: str) -> bool:
    host = hostname(url)
    if host in SKIP_HOSTS:
        return True
    if host.endswith(".gov"):
        return True
    if host.endswith(".wikipedia.org") or host.endswith(".facebook.com"):
        return True
    if any(hint in host for hint in AD_HOST_HINTS):
        return True
    if host.startswith("visit") and host.endswith(".org"):
        return True
    return False


def is_ticketing_host(url: str) -> bool:
    return classify_platform(url) is not None


def decode_ddg_url(href: str) -> str | None:
    """Extract the destination URL from a DuckDuckGo Lite redirect."""
    if not href:
        return None
    raw = href.strip()
    if raw.startswith("//"):
        raw = "https:" + raw
    parsed = urlparse(raw)
    query = parse_qs(parsed.query)
    if "uddg" in query:
        target = unquote(query["uddg"][0])
        if "y.js" in target or "ad_domain=" in target:
            return None
        return normalize_url(target)
    if parsed.scheme in {"http", "https"} and "duckduckgo.com" not in parsed.netloc:
        return normalize_url(raw)
    return None


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.base_href: str | None = None
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: value or "" for key, value in attrs}
        if tag == "base" and attr.get("href"):
            self.base_href = attr["href"]
        if tag == "a":
            self._href = attr.get("href") or None
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            text = re.sub(r"\s+", " ", "".join(self._text)).strip()
            self.links.append((self._href, text))
            self._href = None


def parse_html_links(html: str, base_url: str) -> list[dict[str, str]]:
    parser = _LinkParser()
    try:
        parser.feed(html or "")
    except Exception:
        pass
    resolved: list[dict[str, str]] = []
    page_base = parser.base_href or base_url
    for href, text in parser.links:
        if not href or href.startswith(("javascript:", "mailto:", "tel:")):
            continue
        absolute = urljoin(page_base, href)
        normalized = normalize_url(absolute.split("#")[0]) if "#" in absolute else normalize_url(absolute)
        if not normalized and "#" in href:
            normalized = urljoin(base_url, href)
        if not normalized:
            continue
        resolved.append({"url": normalized, "text": text, "raw": href})
    return resolved


def parse_ddg_lite_results(html: str) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for link in parse_html_links(html, "https://lite.duckduckgo.com/lite/"):
        target = decode_ddg_url(link["raw"]) or decode_ddg_url(link["url"])
        if not target or target in seen:
            continue
        host = hostname(target)
        if not host or "duckduckgo.com" in host:
            continue
        seen.add(target)
        results.append({"url": target, "title": link["text"]})
    return results


def extract_json_ld_urls(html: str) -> list[str]:
    urls: list[str] = []
    blocks = re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html or "",
        flags=re.I | re.S,
    )
    for block in blocks:
        try:
            payload = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        urls.extend(_walk_json_ld_urls(payload))
    return urls


def _walk_json_ld_urls(node: Any) -> list[str]:
    found: list[str] = []
    if isinstance(node, list):
        for item in node:
            found.extend(_walk_json_ld_urls(item))
        return found
    if not isinstance(node, dict):
        return found
    for key in ("url", "sameAs"):
        value = node.get(key)
        if isinstance(value, str):
            found.append(value)
        elif isinstance(value, list):
            found.extend(str(item) for item in value if isinstance(item, str))
    offers = node.get("offers")
    if isinstance(offers, dict):
        found.extend(_walk_json_ld_urls(offers))
    elif isinstance(offers, list):
        for offer in offers:
            found.extend(_walk_json_ld_urls(offer))
    if "@graph" in node:
        found.extend(_walk_json_ld_urls(node["@graph"]))
    return found


def score_website_candidate(url: str, title: str, venue_name: str) -> int:
    if is_skip_host(url):
        return -100
    score = 10
    if host_matches_name(url, venue_name):
        score += 50
    title_l = (title or "").lower()
    name_l = (venue_name or "").lower()
    if name_l and name_l in title_l:
        score += 20
    if is_ticketing_host(url):
        score -= 25
    host = hostname(url)
    if host.endswith(".com"):
        score += 5
    path = urlparse(url).path.rstrip("/")
    if not path:
        score += 8
    if EVENT_DETAIL_PATH.search(urlparse(url).path or ""):
        score -= 25
    if any(hint in path.lower() for hint in SHOWS_NEGATIVE):
        score -= 8
    return score


def pick_website(
    candidates: list[dict[str, str]],
    venue_name: str,
    seed_url: str | None = None,
) -> str | None:
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()
    if seed_url:
        normalized = normalize_url(seed_url)
        if normalized:
            scored.append((score_website_candidate(normalized, venue_name, venue_name) + 30, normalized))
            seen.add(normalized)
    for candidate in candidates:
        url = normalize_url(candidate.get("url"))
        if not url or url in seen:
            continue
        seen.add(url)
        scored.append((score_website_candidate(url, candidate.get("title") or "", venue_name), url))
    scored.sort(key=lambda item: item[0], reverse=True)
    chosen: str | None = None
    for score, url in scored:
        if score > 0:
            chosen = url
            break
    if chosen is None:
        for score, url in scored:
            if score > -100:
                chosen = url
                break
    return canonical_website(chosen)


def score_shows_url(url: str, text: str, homepage: str) -> int:
    parsed = urlparse(url)
    path = (parsed.path or "/").lower()
    blob = f"{path} {text.lower()} {parsed.fragment.lower()}"
    score = 0
    if registrable_host(url) == registrable_host(homepage):
        score += 20
    else:
        # Off-site calendar (Live Nation venue page, AXS venue page) is a fallback.
        if classify_platform(url):
            score += 8
        else:
            score -= 15
    for hint in SHOWS_PATH_HINTS:
        if hint in path or hint in parsed.fragment.lower():
            score += 12
            break
    for hint in SHOWS_TEXT_HINTS:
        if hint in (text or "").lower():
            score += 8
            break
    for hint in SHOWS_NEGATIVE:
        if hint in path:
            score -= 20
    if path in {"", "/"} and parsed.fragment:
        if any(hint in parsed.fragment.lower() for hint in SHOWS_PATH_HINTS):
            score += 18
    if path in {"", "/"} and not parsed.fragment:
        score -= 5
    # Individual event pages are worse than the index.
    if re.search(r"/e/|/event/\d|/events/\d", path):
        score -= 10
    return score


def detect_platforms(urls: list[str], homepage: str | None = None) -> tuple[str | None, list[str]]:
    counts: Counter[str] = Counter()
    same_host_ticket_links = 0
    home_host = registrable_host(homepage) if homepage else ""
    for url in urls:
        platform = classify_platform(url)
        if platform:
            counts[platform] += 1
            continue
        path = urlparse(url).path.lower()
        if home_host and registrable_host(url) == home_host:
            if any(hint in path for hint in ("ticket", "buy", "checkout", "cart")):
                same_host_ticket_links += 1
    if counts:
        primary, _ = counts.most_common(1)[0]
        ordered = [name for name, _ in counts.most_common()]
        if same_host_ticket_links >= 3 and "venue-direct" not in ordered:
            ordered.append("venue-direct")
        return primary, ordered
    if same_host_ticket_links >= 1:
        return "venue-direct", ["venue-direct"]
    return None, []


def pick_shows_url(
    links: list[dict[str, str]],
    homepage: str,
    html: str | None = None,
) -> str | None:
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()
    for link in links:
        url = link.get("url") or ""
        if not url or url in seen:
            continue
        seen.add(url)
        scored.append((score_shows_url(url, link.get("text") or "", homepage), url))

    # Homepage fragments from raw hrefs.
    for link in links:
        raw = link.get("raw") or ""
        if raw.startswith("#") and any(hint in raw.lower() for hint in SHOWS_PATH_HINTS):
            fragment_url = urljoin(homepage, raw)
            scored.append((score_shows_url(fragment_url, link.get("text") or "", homepage), fragment_url))

    scored.sort(key=lambda item: item[0], reverse=True)
    if scored and scored[0][0] >= 20:
        return scored[0][1]

    # Homepage itself lists a calendar when it has many event/ticket links.
    eventish = 0
    for link in links:
        blob = f"{link.get('url', '')} {link.get('text', '')}".lower()
        if any(hint in blob for hint in SHOWS_PATH_HINTS):
            eventish += 1
    if eventish >= 4:
        return homepage
    if scored and scored[0][0] >= 12:
        return scored[0][1]
    return homepage if html and eventish >= 2 else (scored[0][1] if scored else homepage)


def analyze_site(homepage: str, html: str) -> dict[str, Any]:
    links = parse_html_links(html, homepage)
    json_ld_urls = extract_json_ld_urls(html)
    all_urls = [link["url"] for link in links] + json_ld_urls
    primary, platforms = detect_platforms(all_urls, homepage)
    shows_url = pick_shows_url(links, homepage, html)
    if primary is None:
        primary = "unknown"
        platforms = ["unknown"]
    return {
        "website": homepage,
        "upcoming_shows_url": shows_url,
        "ticketing_platform": primary,
        "ticketing_platforms": platforms,
        "link_count": len(links),
    }


def unique_place_venues(listings: list[dict]) -> list[dict]:
    """One record per Google Place ID, carrying a display name and city/state."""
    places: dict[str, dict] = {}
    for listing in listings:
        place_id = listing.get("google_place_id")
        if not place_id:
            continue
        details = listing.get("place_details") or {}
        display_name = (
            details.get("display_name")
            or listing.get("google_name")
            or listing.get("canonical_name")
            or listing.get("name")
        )
        website_seed = details.get("website_uri") or listing.get("website_uri")
        if place_id not in places:
            places[place_id] = {
                "google_place_id": place_id,
                "display_name": display_name,
                "name": listing.get("name"),
                "city": listing.get("city"),
                "state": listing.get("state"),
                "website_seed": website_seed,
                "listing_count": 1,
            }
        else:
            places[place_id]["listing_count"] += 1
            if not places[place_id].get("website_seed") and website_seed:
                places[place_id]["website_seed"] = website_seed
            if not places[place_id].get("display_name") and display_name:
                places[place_id]["display_name"] = display_name
    return list(places.values())


def search_query(venue: dict) -> str:
    name = venue.get("display_name") or venue.get("name") or ""
    city = venue.get("city") or ""
    state = venue.get("state") or ""
    return f'{name} {city} {state} concert venue'.strip()


WIKI_VENUE_HINTS = (
    "venue", "nightclub", "theatre", "theater", "concert", "music", "bar",
    "club", "amphitheatre", "amphitheater", "hall", "arena", "ballroom",
    "comedy", "auditorium", "pavilion", "lounge",
)
WIKI_REJECT_HINTS = (
    "album", "planet", "mythology", "video game", "console", "fictional",
    "disambiguation", "film", "movie", "song by", "studio album",
    "rock band", "musical group",
)


def score_wiki_hit(title: str, snippet: str, venue_name: str, city: str) -> int:
    blob = f"{title} {snippet}".lower()
    title_l = (title or "").lower()
    name_l = (venue_name or "").lower()
    tokens = name_tokens(venue_name)
    token_hits = sum(1 for token in tokens if token in title_l)
    if name_l and name_l not in title_l and token_hits == 0:
        return -50
    score = 0
    if name_l and title_l == name_l:
        score += 50
    elif name_l and name_l in title_l:
        score += 25
    score += min(30, token_hits * 10)
    if city and city.lower() in blob:
        score += 20
    if any(hint in blob for hint in WIKI_VENUE_HINTS):
        score += 15
    if any(hint in blob for hint in WIKI_REJECT_HINTS):
        score -= 40
    return score


def pick_wiki_hit(hits: list[dict], venue_name: str, city: str) -> dict | None:
    scored = []
    for hit in hits:
        score = score_wiki_hit(hit.get("title") or "", hit.get("snippet") or "", venue_name, city)
        scored.append((score, hit))
    scored.sort(key=lambda item: item[0], reverse=True)
    if scored and scored[0][0] >= 25:
        return scored[0][1]
    return None


def parse_wikitext_website(wikitext: str) -> str | None:
    """Pull the official website out of a Wikipedia infobox."""
    match = re.search(r"\|\s*website\s*=\s*(.+)", wikitext or "", flags=re.I)
    if not match:
        return None
    line = match.group(1).strip()
    url_tpl = re.search(r"\{\{\s*url\s*\|\s*([^}|]+)", line, flags=re.I)
    if url_tpl:
        raw = url_tpl.group(1).strip()
        if not raw.startswith("http"):
            raw = "https://" + raw
        return normalize_url(raw)
    wiki_link = re.search(r"\[(https?://[^\s\]]+)", line)
    if wiki_link:
        return normalize_url(wiki_link.group(1))
    plain = re.search(r"(https?://[^\s|}]+)", line)
    if plain:
        return normalize_url(plain.group(1).rstrip("}'\""))
    domain = re.search(r"([a-z0-9.-]+\.[a-z]{2,})", line, flags=re.I)
    if domain:
        host = domain.group(1).lstrip(".")
        if "wikipedia" in host or "wikidata" in host:
            return None
        return normalize_url("https://" + host)
    return None


def guessed_website_urls(name: str, city: str | None = None) -> list[str]:
    compact = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    dashed = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    city_slug = re.sub(r"[^a-z0-9]", "", (city or "").lower())
    hosts: list[str] = []
    if city_slug:
        for host in (
            f"{dashed}{city_slug}",
            f"{compact}{city_slug}",
            f"{dashed}-{city_slug}",
            f"{city_slug}{dashed}",
        ):
            if host not in hosts:
                hosts.append(host)
    for host in (dashed, compact):
        if host and len(host) >= 4 and host not in hosts:
            hosts.append(host)
    # Prefer longer, more specific hosts first.
    hosts.sort(key=len, reverse=True)
    urls: list[str] = []
    for host in hosts[:6]:
        if len(host) < 4:
            continue
        urls.append(f"https://www.{host}.com/")
        urls.append(f"https://{host}.com/")
    return urls
