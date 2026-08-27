#!/usr/bin/env python3
"""Tests for venue website / shows page / ticketing platform lookup (QUA-19)."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from build_database import build_database
from venue_web import (
    analyze_site,
    canonical_website,
    classify_platform,
    decode_ddg_url,
    detect_platforms,
    parse_ddg_lite_results,
    parse_wikitext_website,
    pick_website,
    pick_wiki_hit,
    guessed_website_urls,
    score_shows_url,
    search_query,
    unique_place_venues,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample_listings.json"

DDG_LITE_HTML = """
<html><body>
<a href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fduckduckgo.com%2Fy.js%3Fad_domain%3Dtickets-center.com">Ad tickets</a>
<a href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.930.com%2F&rut=abc">Listing - 9:30 Club</a>
<a href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.facebook.com%2F930club%2F">9:30 Club | Facebook</a>
<a href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fen.wikipedia.org%2Fwiki%2F9%3A30_Club">9:30 Club - Wikipedia</a>
<a href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.livenation.com%2Fvenue%2FKovZpZA7knFA%2F9-30-club-events">9:30 CLUB - Live Nation</a>
</body></html>
"""

NINE_THIRTY_HTML = """
<html>
  <a href="https://www.930.com/#upcoming-shows-title">Shows</a>
  <a href="/faq/">FAQ</a>
  <a href="https://www.ticketmaster.com/circle-jerks-washington/event/150064">Tickets</a>
  <a href="https://www.ticketmaster.com/hot-in-herre/event/150065">Buy Tickets</a>
  <a href="https://www.ticketmaster.com/milk-carton-kids/event/150066">Buy Tickets</a>
</html>
"""

DICE_HTML = """
<html>
  <a href="/events">Upcoming Shows</a>
  <a href="https://dice.fm/venue/saturn-birmingham">Get tickets on Dice</a>
  <a href="https://dice.fm/event/abc123">Tickets</a>
</html>
"""

VENUE_DIRECT_HTML = """
<html>
  <a href="/calendar">Calendar</a>
  <a href="/tickets/buy">Buy tickets</a>
  <a href="/tickets/checkout">Checkout</a>
</html>
"""

AXS_HTML = """
<html>
  <a href="/events/calendar">Event Calendar</a>
  <a href="https://www.axs.com/events/123/show">Tickets</a>
</html>
"""


class DecodeAndSearchTests(unittest.TestCase):
    def test_ddg_decode_skips_ads(self):
        ad = decode_ddg_url(
            "//duckduckgo.com/l/?uddg=https%3A%2F%2Fduckduckgo.com%2Fy.js%3Fad_domain%3Dtickets-center.com"
        )
        self.assertIsNone(ad)
        good = decode_ddg_url("//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.930.com%2F&rut=abc")
        self.assertEqual(good, "https://www.930.com/")

    def test_parse_ddg_lite_prefers_organic_results(self):
        results = parse_ddg_lite_results(DDG_LITE_HTML)
        urls = [row["url"] for row in results]
        self.assertIn("https://www.930.com/", urls)
        self.assertTrue(all("y.js" not in url for url in urls))
        self.assertTrue(all("tickets-center" not in url for url in urls))

    def test_pick_website_skips_facebook_and_wikipedia(self):
        website = pick_website(parse_ddg_lite_results(DDG_LITE_HTML), "9:30 Club")
        self.assertEqual(website, "https://www.930.com/")

    def test_pick_website_skips_city_hall(self):
        website = pick_website(
            [{"url": "https://www.auburnal.gov/", "title": "City of Auburn"}],
            "Jay and Susie Gogue Performing Arts Center at Auburn University",
        )
        self.assertIsNone(website)
        website = pick_website(
            [{"url": "https://example.com/", "title": "Example"}],
            "El Cid",
        )
        self.assertIsNone(website)
        website = pick_website(
            [{"url": "https://www.facebook.com/saturn", "title": "Saturn"}],
            "Saturn",
            seed_url="https://saturnbirmingham.com/",
        )
        self.assertEqual(website, "https://saturnbirmingham.com/")

    def test_canonical_website_strips_event_detail_path(self):
        self.assertEqual(
            canonical_website("https://saturnbirmingham.com/e/some-show"),
            "https://saturnbirmingham.com/",
        )
        self.assertEqual(
            canonical_website("https://saturnbirmingham.com/e/"),
            "https://saturnbirmingham.com/",
        )
        self.assertEqual(
            canonical_website("https://www.930.com/"),
            "https://www.930.com/",
        )


class PlatformAndShowsTests(unittest.TestCase):
    def test_classify_known_platforms(self):
        self.assertEqual(classify_platform("https://www.ticketmaster.com/event/1"), "ticketmaster")
        self.assertEqual(classify_platform("https://www.livenation.com/venue/x"), "ticketmaster")
        self.assertEqual(classify_platform("https://dice.fm/event/abc"), "dice")
        self.assertEqual(classify_platform("https://www.eventbrite.com/e/show"), "eventbrite")
        self.assertEqual(classify_platform("https://www.axs.com/events/1"), "axs")
        self.assertEqual(classify_platform("https://www.seetickets.com/event/1"), "see_tickets")
        self.assertIsNone(classify_platform("https://www.930.com/shows"))

    def test_analyze_ticketmaster_homepage(self):
        result = analyze_site("https://www.930.com/", NINE_THIRTY_HTML)
        self.assertEqual(result["ticketing_platform"], "ticketmaster")
        self.assertIn("ticketmaster", result["ticketing_platforms"])
        self.assertTrue(
            result["upcoming_shows_url"].startswith("https://www.930.com/")
        )

    def test_analyze_dice_and_event_path(self):
        result = analyze_site("https://saturnbirmingham.com/", DICE_HTML)
        self.assertEqual(result["ticketing_platform"], "dice")
        self.assertIn("/events", result["upcoming_shows_url"])

    def test_analyze_venue_direct(self):
        result = analyze_site("https://examplevenue.com/", VENUE_DIRECT_HTML)
        self.assertEqual(result["ticketing_platform"], "venue-direct")
        self.assertIn("calendar", result["upcoming_shows_url"])

    def test_analyze_axs(self):
        result = analyze_site("https://www.thefillmore.com/", AXS_HTML)
        self.assertEqual(result["ticketing_platform"], "axs")

    def test_shows_score_prefers_calendar_over_about(self):
        home = "https://www.930.com/"
        calendar = score_shows_url("https://www.930.com/events", "Upcoming Shows", home)
        about = score_shows_url("https://www.930.com/about", "About", home)
        policies = score_shows_url("https://www.930.com/policies", "Policies", home)
        self.assertGreater(calendar, about)
        self.assertGreater(calendar, policies)

    def test_detect_platforms_empty_is_none(self):
        primary, platforms = detect_platforms(["https://www.930.com/about"], "https://www.930.com/")
        self.assertIsNone(primary)
        self.assertEqual(platforms, [])


class UniquePlaceGrainTests(unittest.TestCase):
    def test_co_located_listings_share_one_web_record(self):
        with open(FIXTURE) as handle:
            listings = json.load(handle)
        venues = unique_place_venues(listings)
        ids = [row["google_place_id"] for row in venues]
        self.assertEqual(len(ids), len(set(ids)))
        shared = [row for row in venues if row["google_place_id"] == "ChIJxeko4uS3t4kRuCl8NRL9YTE"]
        self.assertEqual(len(shared), 1)
        self.assertEqual(shared[0]["listing_count"], 2)
        self.assertEqual(shared[0]["website_seed"], "https://www.930.com/")
        self.assertEqual(shared[0]["display_name"], "9:30 Club")

    def test_search_query_includes_city_state(self):
        query = search_query({
            "display_name": "Saturn",
            "city": "Birmingham",
            "state": "AL",
        })
        self.assertIn("Saturn", query)
        self.assertIn("Birmingham", query)
        self.assertIn("AL", query)

    def test_parse_wikitext_website(self):
        self.assertEqual(
            parse_wikitext_website("| website = {{URL|https://emptybottle.com}}"),
            "https://emptybottle.com",
        )
        self.assertEqual(
            parse_wikitext_website("| website = {{URL|930.com|Venue Website}}"),
            "https://930.com",
        )
        self.assertEqual(
            parse_wikitext_website("| website = [http://www.theark.org theark.org]"),
            "http://www.theark.org",
        )

    def test_pick_wiki_hit_prefers_venue_over_album(self):
        hits = [
            {"title": "Merriweather Post Pavilion", "snippet": "album by Animal Collective"},
            {"title": "Merriweather Post Pavilion", "snippet": "outdoor concert venue in Columbia, Maryland"},
        ]
        picked = pick_wiki_hit(hits, "Merriweather Post Pavilion", "Columbia")
        self.assertIsNotNone(picked)
        self.assertIn("venue", picked["snippet"])

    def test_guessed_urls_include_city(self):
        urls = guessed_website_urls("Saturn", "Birmingham")
        joined = " ".join(urls)
        self.assertIn("saturnbirmingham.com", joined)


class VenueWebDatabaseTests(unittest.TestCase):
    def test_venue_web_table_loads_three_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            listings_path = tmp_path / "listings.json"
            with open(FIXTURE) as handle:
                listings = json.load(handle)
            listings_path.write_text(json.dumps(listings))
            web_sidecar = tmp_path / "venue_web.json"
            web_sidecar.write_text(json.dumps([
                {
                    "google_place_id": "ChIJxeko4uS3t4kRuCl8NRL9YTE",
                    "website": "https://www.930.com/",
                    "upcoming_shows_url": "https://www.930.com/#upcoming-shows-title",
                    "ticketing_platform": "ticketmaster",
                    "ticketing_platforms": ["ticketmaster"],
                    "fetched_at": "2026-08-27T00:00:00Z",
                }
            ]))
            db_path = tmp_path / "sample.db"
            conn = build_database(listings_path, db_path, venue_web_path=web_sidecar)
            try:
                conn.row_factory = sqlite3.Row
                columns = {
                    row[1] for row in conn.execute("PRAGMA table_info(venue_web)")
                }
                for column in ("website", "upcoming_shows_url", "ticketing_platform"):
                    self.assertIn(column, columns)
                row = conn.execute(
                    """
                    SELECT website, upcoming_shows_url, ticketing_platform
                    FROM venue_web
                    WHERE google_place_id = ?
                    """,
                    ("ChIJxeko4uS3t4kRuCl8NRL9YTE",),
                ).fetchone()
                self.assertEqual(row["website"], "https://www.930.com/")
                self.assertEqual(
                    row["upcoming_shows_url"],
                    "https://www.930.com/#upcoming-shows-title",
                )
                self.assertEqual(row["ticketing_platform"], "ticketmaster")
                missing = conn.execute(
                    "SELECT COUNT(*) FROM venue_web WHERE google_place_id = ?",
                    ("ChIJMYu3JlEaiYgR5e0OSuecpiE",),
                ).fetchone()[0]
                self.assertEqual(missing, 0)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
