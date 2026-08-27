#!/usr/bin/env python3
"""
Look up each unique Google Places venue's official website, upcoming-shows
page, and ticketing platform (QUA-19).

Reads venues/venues_clean.json (or --input) and writes venues/venue_web.json,
one record per Google Place ID.

Usage:
    python3 fetch_venue_web.py [--limit N] [--start N] [--resume] [--workers N]
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin
from urllib.request import Request, urlopen

from venue_web import (
    COMMON_SHOWS_PATHS,
    analyze_site,
    classify_platform,
    guessed_website_urls,
    hostname,
    is_skip_host,
    parse_wikitext_website,
    pick_website,
    pick_wiki_hit,
    search_query,
    unique_place_venues,
)

VENUES_CLEAN = Path("venues/venues_clean.json")
VENUES_DETAILS = Path("venues/venues_details.json")
OUTPUT = Path("venues/venue_web.json")
PROGRESS_FILE = Path("venues/.venue_web_progress.json")

USER_AGENT = (
    "VenueMetadataBot/1.0 (+https://github.com/bradleytmcgraw/venue-metadata; "
    "QUA-19 venue website research)"
)
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
REQUEST_TIMEOUT = 15
MAX_RETRIES = 3
RETRY_DELAY = 2.0
WIKI_DELAY = 0.05
FETCH_DELAY = 0.05
SAVE_EVERY = 25

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

_wiki_lock = Lock()
_last_wiki_at = 0.0


def _headers(browser: bool = False) -> dict[str, str]:
    return {
        "User-Agent": BROWSER_UA if browser else USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }


def http_get(url: str, timeout: int = REQUEST_TIMEOUT, browser: bool = True, retries: int = MAX_RETRIES) -> tuple[str | None, str | None]:
    """Return (final_url, html) or (None, None) on failure."""
    for attempt in range(1, retries + 1):
        req = Request(url, headers=_headers(browser=browser), method="GET")
        try:
            with urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                charset = resp.headers.get_content_charset() or "utf-8"
                html = raw.decode(charset, errors="replace")
                return resp.geturl(), html
        except HTTPError as exc:
            if exc.code in {403, 404, 410}:
                log.debug("HTTP %s for %s", exc.code, url)
                return None, None
            if exc.code in {429, 500, 502, 503} and attempt < retries:
                time.sleep(RETRY_DELAY * attempt)
                continue
            log.debug("HTTP %s for %s", exc.code, url)
            return None, None
        except (URLError, TimeoutError, OSError) as exc:
            if attempt < retries:
                time.sleep(RETRY_DELAY * attempt)
                continue
            log.debug("GET failed for %s: %s", url, exc)
            return None, None
    return None, None


def wiki_get_json(url: str) -> dict | list | None:
    global _last_wiki_at
    with _wiki_lock:
        wait = WIKI_DELAY - (time.monotonic() - _last_wiki_at)
        if wait > 0:
            time.sleep(wait)
        final_url, body = http_get(url, browser=False)
        _last_wiki_at = time.monotonic()
    if not body:
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


def wikipedia_search(query: str) -> list[dict[str, str]]:
    url = (
        f"{WIKI_API}?action=query&list=search&format=json&srlimit=5&srsearch={quote(query)}"
    )
    data = wiki_get_json(url)
    if not isinstance(data, dict):
        return []
    hits = []
    for row in (data.get("query") or {}).get("search") or []:
        hits.append({"title": row.get("title") or "", "snippet": row.get("snippet") or ""})
    return hits


def wikipedia_infobox_website(title: str) -> str | None:
    url = (
        f"{WIKI_API}?action=query&prop=revisions&rvprop=content&rvslots=main"
        f"&format=json&titles={quote(title)}"
    )
    data = wiki_get_json(url)
    if not isinstance(data, dict):
        return None
    pages = (data.get("query") or {}).get("pages") or {}
    for page in pages.values():
        revisions = page.get("revisions") or []
        if not revisions:
            continue
        wikitext = ((revisions[0].get("slots") or {}).get("main") or {}).get("*") or ""
        website = parse_wikitext_website(wikitext)
        if website:
            return website
    return None


def wikidata_official_website(name: str, city: str) -> str | None:
    for query in (f"{name} {city}", name):
        url = (
            f"{WIKIDATA_API}?action=wbsearchentities&language=en&format=json"
            f"&limit=5&search={quote(query)}"
        )
        data = wiki_get_json(url)
        if not isinstance(data, dict):
            continue
        hits = []
        for row in data.get("search") or []:
            hits.append({
                "title": row.get("label") or "",
                "snippet": row.get("description") or "",
                "id": row.get("id"),
            })
        picked = pick_wiki_hit(hits, name, city)
        if not picked or not picked.get("id"):
            continue
        entity_url = f"https://www.wikidata.org/wiki/Special:EntityData/{picked['id']}.json"
        entity = wiki_get_json(entity_url)
        if not isinstance(entity, dict):
            continue
        claims = ((entity.get("entities") or {}).get(picked["id"]) or {}).get("claims") or {}
        for statement in claims.get("P856") or []:
            if statement.get("rank") == "deprecated":
                continue
            snak = statement.get("mainsnak") or {}
            if snak.get("snaktype") == "value":
                value = (snak.get("datavalue") or {}).get("value")
                if value:
                    return value
    return None


def collect_website_candidates(venue: dict) -> list[dict[str, str]]:
    name = venue.get("display_name") or venue.get("name") or ""
    city = venue.get("city") or ""
    candidates: list[dict[str, str]] = []

    wiki_hits = wikipedia_search(f"{name} {city}") or wikipedia_search(name)
    picked = pick_wiki_hit(wiki_hits, name, city)
    if picked:
        infobox = wikipedia_infobox_website(picked["title"])
        if infobox and not is_skip_host(infobox):
            candidates.append({"url": infobox, "title": name})

    wikidata_url = wikidata_official_website(name, city)
    if wikidata_url and not is_skip_host(wikidata_url):
        candidates.append({"url": wikidata_url, "title": name})

    if not candidates:
        tokens = [
            token
            for token in (venue.get("display_name") or venue.get("name") or "").lower().split()
            if len(token) > 3
        ]
        for url in guessed_website_urls(name, city)[:6]:
            final_url, html = http_get(url, timeout=5, retries=1)
            time.sleep(FETCH_DELAY)
            if not final_url or not html or len(html) < 800:
                continue
            if is_skip_host(final_url):
                continue
            blob = html[:4000].lower()
            if tokens and not any(token.strip("s") in blob for token in tokens[:3]):
                continue
            title_match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.I | re.S)
            title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""
            candidates.append({"url": final_url, "title": title or name})
            break
    return candidates


def follow_shows_page(homepage: str, analysis: dict) -> dict:
    """Fetch an on-site shows URL, or probe a few calendar paths if needed."""
    shows_url = analysis.get("upcoming_shows_url") or homepage
    same_host = hostname(shows_url) == hostname(homepage)
    homepage_norm = homepage.rstrip("/")
    shows_norm = shows_url.split("#")[0].rstrip("/")

    if same_host and shows_norm != homepage_norm:
        final_url, html = http_get(shows_url)
        time.sleep(FETCH_DELAY)
        if html and final_url:
            extra = analyze_site(final_url, html)
            if extra.get("ticketing_platform") and extra["ticketing_platform"] != "unknown":
                analysis["ticketing_platform"] = extra["ticketing_platform"]
                platforms = extra.get("ticketing_platforms") or []
                existing = analysis.get("ticketing_platforms") or []
                analysis["ticketing_platforms"] = list(dict.fromkeys(existing + platforms))
            analysis["upcoming_shows_url"] = extra.get("upcoming_shows_url") or final_url
            return analysis

    needs_probe = analysis.get("ticketing_platform") in {None, "unknown"}
    if needs_probe and same_host:
        for path in COMMON_SHOWS_PATHS[:4]:
            candidate = urljoin(homepage.rstrip("/") + "/", path.lstrip("/"))
            final_url, html = http_get(candidate)
            time.sleep(FETCH_DELAY)
            if not html or not final_url:
                continue
            if hostname(final_url) != hostname(homepage):
                continue
            extra = analyze_site(final_url, html)
            extra_platform = extra.get("ticketing_platform")
            if extra_platform and extra_platform != "unknown":
                analysis["upcoming_shows_url"] = extra.get("upcoming_shows_url") or final_url
                analysis["ticketing_platform"] = extra_platform
                platforms = extra.get("ticketing_platforms") or [extra_platform]
                existing = analysis.get("ticketing_platforms") or []
                analysis["ticketing_platforms"] = list(dict.fromkeys(existing + platforms))
                return analysis
    return analysis


def lookup_venue(venue: dict) -> dict:
    query = search_query(venue)
    candidates = collect_website_candidates(venue)
    website = pick_website(candidates, venue.get("display_name") or venue.get("name") or "", venue.get("website_seed"))

    analysis = {
        "website": website,
        "upcoming_shows_url": None,
        "ticketing_platform": "unknown",
        "ticketing_platforms": ["unknown"],
    }
    html = None
    final_url = None
    if website:
        if website.startswith("http://"):
            https_url = "https://" + website[len("http://"):]
            final_url, html = http_get(https_url)
            if final_url:
                website = final_url
        if html is None:
            final_url, html = http_get(website)
        time.sleep(FETCH_DELAY)
        if final_url and is_skip_host(final_url):
            final_url = None
            html = None
            website = None
        if final_url:
            analysis["website"] = final_url
            website = final_url
        if html and website:
            analysis = analyze_site(website, html)
            analysis = follow_shows_page(website, analysis)

    # Search-result fallback: a ticketing venue page can still be the calendar.
    if analysis.get("ticketing_platform") in {None, "unknown"}:
        for candidate in candidates:
            platform = classify_platform(candidate.get("url") or "")
            if not platform:
                continue
            analysis["ticketing_platform"] = platform
            platforms = analysis.get("ticketing_platforms") or []
            if platform not in platforms or platforms == ["unknown"]:
                analysis["ticketing_platforms"] = [platform]
            if not analysis.get("upcoming_shows_url"):
                analysis["upcoming_shows_url"] = candidate["url"]
            break

    if analysis.get("ticketing_platform") in {None, ""}:
        analysis["ticketing_platform"] = "unknown"
    if not analysis.get("ticketing_platforms"):
        analysis["ticketing_platforms"] = [analysis["ticketing_platform"]]

    return {
        "google_place_id": venue.get("google_place_id"),
        "display_name": venue.get("display_name") or venue.get("name"),
        "city": venue.get("city"),
        "state": venue.get("state"),
        "listing_count": venue.get("listing_count", 1),
        "website": analysis.get("website"),
        "upcoming_shows_url": analysis.get("upcoming_shows_url"),
        "ticketing_platform": analysis.get("ticketing_platform"),
        "ticketing_platforms": analysis.get("ticketing_platforms"),
        "search_query": query,
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE) as handle:
            return json.load(handle)
    return {"results": [], "done_ids": []}


def save_progress(progress: dict) -> None:
    tmp = PROGRESS_FILE.with_suffix(".tmp")
    with open(tmp, "w") as handle:
        json.dump(progress, handle)
    tmp.replace(PROGRESS_FILE)


def load_listings(path: Path) -> list[dict]:
    with open(path) as handle:
        return json.load(handle)


def resolve_input(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    if VENUES_DETAILS.exists():
        return VENUES_DETAILS
    return VENUES_CLEAN


def main() -> None:
    parser = argparse.ArgumentParser(description="Look up venue websites, show pages, and ticketing platforms")
    parser.add_argument("--input", "-i", type=Path, default=None, help="Listings JSON (default: details, else clean)")
    parser.add_argument("--output", "-o", type=Path, default=OUTPUT)
    parser.add_argument("--limit", type=int, default=0, help="Process only N unique places")
    parser.add_argument("--start", type=int, default=0, help="Start from unique-place index N")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    input_path = resolve_input(args.input)
    listings = load_listings(input_path)
    venues = unique_place_venues(listings)
    log.info("Loaded %s listings -> %s unique Place IDs from %s", len(listings), len(venues), input_path)

    if args.resume:
        progress = load_progress()
        done_ids = set(progress.get("done_ids") or [])
        results = progress.get("results") or []
        log.info("Resuming with %s already fetched", len(results))
    else:
        done_ids = set()
        results = []

    start = args.start
    end = len(venues)
    if args.limit > 0:
        end = min(start + args.limit, len(venues))
    batch = [venue for venue in venues[start:end] if venue["google_place_id"] not in done_ids]
    log.info("Processing %s unique places (indexes %s-%s)", len(batch), start, end - 1)

    if args.dry_run:
        for venue in batch[:20]:
            print(f"SEARCH: {search_query(venue)}")
        if len(batch) > 20:
            print(f"... and {len(batch) - 20} more")
        return

    workers = max(1, args.workers)
    completed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(lookup_venue, venue): venue for venue in batch}
        for future in as_completed(futures):
            venue = futures[future]
            try:
                record = future.result()
            except Exception:
                log.exception("Lookup failed for %s", venue.get("display_name"))
                record = {
                    "google_place_id": venue.get("google_place_id"),
                    "display_name": venue.get("display_name") or venue.get("name"),
                    "city": venue.get("city"),
                    "state": venue.get("state"),
                    "listing_count": venue.get("listing_count", 1),
                    "website": None,
                    "upcoming_shows_url": None,
                    "ticketing_platform": "unknown",
                    "ticketing_platforms": ["unknown"],
                    "search_query": search_query(venue),
                    "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "error": "lookup_failed",
                }
            results.append(record)
            done_ids.add(record["google_place_id"])
            completed += 1
            log.info(
                "[%s/%s] %s -> site=%s shows=%s tickets=%s",
                completed,
                len(batch),
                record.get("display_name"),
                record.get("website"),
                record.get("upcoming_shows_url"),
                record.get("ticketing_platform"),
            )
            if completed % SAVE_EVERY == 0:
                save_progress({"results": results, "done_ids": list(done_ids)})
                log.info("Progress saved (%s records)", len(results))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Stable order: original unique-place order.
    by_id = {row["google_place_id"]: row for row in results}
    ordered = [by_id[venue["google_place_id"]] for venue in venues if venue["google_place_id"] in by_id]
    with open(args.output, "w") as handle:
        json.dump(ordered, handle, indent=2)
        handle.write("\n")
    log.info("Wrote %s records to %s", len(ordered), args.output)

    if PROGRESS_FILE.exists():
        PROGRESS_FILE.unlink()

    with_site = sum(1 for row in ordered if row.get("website"))
    with_shows = sum(1 for row in ordered if row.get("upcoming_shows_url"))
    known_tickets = sum(1 for row in ordered if row.get("ticketing_platform") not in {None, "unknown"})
    log.info("=" * 60)
    log.info("DONE: %s unique places", len(ordered))
    log.info("  website: %s", with_site)
    log.info("  upcoming_shows_url: %s", with_shows)
    log.info("  known ticketing_platform: %s", known_tickets)


if __name__ == "__main__":
    main()
