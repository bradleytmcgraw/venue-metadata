#!/usr/bin/env python3
"""Tests for the unique Google Places venue table (QUA-14)."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from build_database import build_database
from places import (
    FIELD_MASK,
    extract_details,
    infer_primary_type,
    unique_google_places,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample_listings.json"
CLEAN_VENUES = ROOT / "venues" / "venues_clean.json"

MENU_COLUMNS = [
    "serves_breakfast",
    "serves_brunch",
    "serves_lunch",
    "serves_dinner",
    "serves_dessert",
    "serves_coffee",
    "serves_vegetarian_food",
    "serves_beer",
    "serves_wine",
    "serves_cocktails",
    "menu_for_children",
]


RAW_PLACE_DETAILS = {
    "id": "ChIJJwUxINqXyFYRRIxyYuv2048",
    "displayName": {"text": "Chilkoot Charlie's", "languageCode": "en"},
    "types": ["night_club", "bar", "restaurant", "food", "point_of_interest", "establishment"],
    "primaryType": "night_club",
    "primaryTypeDisplayName": {"text": "Night Club", "languageCode": "en"},
    "formattedAddress": "2435 Spenard Rd, Anchorage, AK 99503, USA",
    "location": {"latitude": 61.1981733, "longitude": -149.9049457},
    "googleMapsUri": "https://maps.google.com/?cid=99",
    "businessStatus": "OPERATIONAL",
    "timeZone": {"id": "America/Anchorage"},
    "rating": 4.3,
    "userRatingCount": 2100,
    "websiteUri": "https://example.com",
    "internationalPhoneNumber": "+1 907-555-0100",
    "nationalPhoneNumber": "(907) 555-0100",
    "priceLevel": "PRICE_LEVEL_MODERATE",
    "priceRange": {
        "startPrice": {"currencyCode": "USD", "units": "10"},
        "endPrice": {"currencyCode": "USD", "units": "30"},
    },
    "regularOpeningHours": {
        "weekdayDescriptions": ["Monday: 10 AM – 2 AM"],
        "periods": [
            {
                "open": {"day": 1, "hour": 10, "minute": 0},
                "close": {"day": 2, "hour": 2, "minute": 0},
            }
        ],
    },
    "servesBreakfast": False,
    "servesBrunch": True,
    "servesLunch": True,
    "servesDinner": True,
    "servesDessert": True,
    "servesCoffee": False,
    "servesVegetarianFood": True,
    "servesBeer": True,
    "servesWine": True,
    "servesCocktails": True,
    "menuForChildren": False,
    "dineIn": True,
    "takeout": True,
    "delivery": False,
    "curbsidePickup": False,
    "liveMusic": True,
    "parkingOptions": {
        "freeParkingLot": True,
        "paidParkingLot": False,
        "freeStreetParking": True,
        "paidStreetParking": False,
        "valetParking": False,
        "freeGarageParking": False,
        "paidGarageParking": True,
    },
    "paymentOptions": {
        "acceptsCreditCards": True,
        "acceptsDebitCards": True,
        "acceptsCashOnly": False,
        "acceptsNfc": True,
    },
    "reviews": [
        {
            "authorAttribution": {"displayName": "Sam", "uri": "https://maps.google.com/sam"},
            "rating": 4,
            "text": {"text": "Loud and fun.", "languageCode": "en"},
            "relativePublishTimeDescription": "a month ago",
            "publishTime": "2026-07-01T00:00:00Z",
            "googleMapsUri": "https://maps.google.com/?cid=99",
        }
    ],
}


class ExtractDetailsTests(unittest.TestCase):
    def test_field_mask_includes_type_and_menu_attributes(self):
        for field in (
            "primaryType",
            "types",
            "menuForChildren",
            "servesBreakfast",
            "servesLunch",
            "servesDinner",
            "servesVegetarianFood",
        ):
            self.assertIn(field, FIELD_MASK.split(","))

    def test_extract_details_flattens_menu_and_type_fields(self):
        details = extract_details(RAW_PLACE_DETAILS)
        self.assertEqual(details["google_place_id"], "ChIJJwUxINqXyFYRRIxyYuv2048")
        self.assertEqual(details["display_name"], "Chilkoot Charlie's")
        self.assertEqual(details["primary_type"], "night_club")
        self.assertEqual(details["primary_type_display_name"], "Night Club")
        self.assertTrue(details["serves_beer"])
        self.assertTrue(details["serves_brunch"])
        self.assertFalse(details["menu_for_children"])
        self.assertTrue(details["serves_vegetarian_food"])
        self.assertTrue(details["parking_paid_garage"])
        self.assertTrue(details["parking_free_lot"])
        self.assertEqual(details["price_range_start_cents"], 1000)
        self.assertEqual(details["price_range_end_cents"], 3000)
        self.assertEqual(details["price_range_currency"], "USD")
        self.assertEqual(len(details["reviews"]), 1)
        self.assertEqual(details["reviews"][0]["text"], "Loud and fun.")

    def test_infer_primary_type_skips_generic_tags(self):
        self.assertEqual(
            infer_primary_type(["point_of_interest", "establishment", "bar"]),
            "bar",
        )
        self.assertEqual(infer_primary_type(["establishment"]), "establishment")
        self.assertIsNone(infer_primary_type([]))


class UniquePlaceAggregationTests(unittest.TestCase):
    def setUp(self):
        with open(FIXTURE) as handle:
            self.listings = json.load(handle)

    def test_co_located_listings_collapse_to_one_place(self):
        places = unique_google_places(self.listings)
        self.assertEqual(len(places), 2)
        shared = places["ChIJxeko4uS3t4kRuCl8NRL9YTE"]
        self.assertEqual(shared["listing_count"], 2)
        self.assertEqual(shared["display_name"], "9:30 Club")
        self.assertEqual(shared["primary_type"], "live_music_venue")
        self.assertIn("live_music_venue", shared["types"])
        self.assertIn("night_club", shared["types"])
        self.assertTrue(shared["details_fetched"])
        self.assertTrue(shared["serves_beer"])
        self.assertFalse(shared["menu_for_children"])

    def test_listings_without_place_id_are_omitted(self):
        places = unique_google_places(self.listings)
        self.assertNotIn(None, places)
        self.assertNotIn("", places)


class GooglePlacesDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls._tmp.name) / "sample.db"
        cls.conn = build_database(FIXTURE, cls.db_path)
        cls.conn.row_factory = sqlite3.Row

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls._tmp.cleanup()

    def test_google_places_is_keyed_by_place_id(self):
        info = {row[1]: row for row in self.conn.execute("PRAGMA table_info(google_places)")}
        self.assertEqual(info["google_place_id"][5], 1)  # pk flag
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM google_places").fetchone()[0],
            2,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(DISTINCT google_place_id) FROM google_places"
            ).fetchone()[0],
            2,
        )

    def test_acceptance_name_views_exist(self):
        google_places = self.conn.execute('SELECT COUNT(*) FROM "Google Places"').fetchone()[0]
        venue_master = self.conn.execute(
            'SELECT COUNT(*) FROM "VenueMaster Metadata"'
        ).fetchone()[0]
        native = self.conn.execute("SELECT COUNT(*) FROM google_places").fetchone()[0]
        self.assertEqual(google_places, native)
        self.assertEqual(venue_master, native)

    def test_menu_and_type_columns_are_stored(self):
        columns = {
            row[1]
            for row in self.conn.execute("PRAGMA table_info(google_places)")
        }
        for column in MENU_COLUMNS + ["primary_type", "primary_type_display_name"]:
            self.assertIn(column, columns)

        row = self.conn.execute(
            """
            SELECT primary_type, serves_beer, serves_dinner, menu_for_children, live_music
            FROM google_places
            WHERE google_place_id = ?
            """,
            ("ChIJxeko4uS3t4kRuCl8NRL9YTE",),
        ).fetchone()
        self.assertEqual(row["primary_type"], "live_music_venue")
        self.assertEqual(row["serves_beer"], 1)
        self.assertEqual(row["serves_dinner"], 1)
        self.assertEqual(row["menu_for_children"], 0)
        self.assertEqual(row["live_music"], 1)

    def test_types_are_stored_per_place(self):
        types = {
            row[0]
            for row in self.conn.execute(
                "SELECT type FROM google_place_types WHERE google_place_id = ?",
                ("ChIJxeko4uS3t4kRuCl8NRL9YTE",),
            )
        }
        self.assertIn("live_music_venue", types)
        self.assertIn("night_club", types)
        # Shared Place ID must not duplicate type rows.
        count = self.conn.execute(
            "SELECT COUNT(*) FROM google_place_types WHERE google_place_id = ?",
            ("ChIJxeko4uS3t4kRuCl8NRL9YTE",),
        ).fetchone()[0]
        self.assertEqual(count, len(types))

    def test_reviews_and_hours_key_off_place_id(self):
        reviews = self.conn.execute(
            "SELECT COUNT(*) FROM google_place_reviews WHERE google_place_id = ?",
            ("ChIJxeko4uS3t4kRuCl8NRL9YTE",),
        ).fetchone()[0]
        hours = self.conn.execute(
            "SELECT COUNT(*) FROM google_place_hours WHERE google_place_id = ?",
            ("ChIJxeko4uS3t4kRuCl8NRL9YTE",),
        ).fetchone()[0]
        self.assertEqual(reviews, 1)
        self.assertEqual(hours, 1)

    def test_listings_remain_separate_and_reference_places(self):
        listing_count = self.conn.execute("SELECT COUNT(*) FROM venues").fetchone()[0]
        self.assertEqual(listing_count, 4)
        shared_listings = self.conn.execute(
            "SELECT name FROM venues WHERE google_place_id = ? ORDER BY name",
            ("ChIJxeko4uS3t4kRuCl8NRL9YTE",),
        ).fetchall()
        self.assertEqual(
            [row[0] for row in shared_listings],
            ["9:30 Club", "U Street Music Hall"],
        )
        unmatched = self.conn.execute(
            "SELECT google_place_id FROM venues WHERE name = ?",
            ("Unmatched Listing",),
        ).fetchone()
        self.assertIsNone(unmatched[0])

    def test_place_id_primary_key_rejects_duplicates(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO google_places (google_place_id, display_name) VALUES (?, ?)",
                ("ChIJMYu3JlEaiYgR5e0OSuecpiE", "Duplicate Saturn"),
            )
        self.conn.rollback()


class CleanVenuesIntegrationTests(unittest.TestCase):
    def test_clean_listings_build_unique_place_id_table(self):
        if not CLEAN_VENUES.exists():
            self.skipTest("venues/venues_clean.json is not present")

        with open(CLEAN_VENUES) as handle:
            listings = json.load(handle)
        expected_listings = len(listings)
        expected_places = len({
            listing["google_place_id"]
            for listing in listings
            if listing.get("google_place_id")
        })

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "venues.db"
            conn = build_database(CLEAN_VENUES, db_path)
            try:
                places = conn.execute("SELECT COUNT(*) FROM google_places").fetchone()[0]
                distinct = conn.execute(
                    "SELECT COUNT(DISTINCT google_place_id) FROM google_places"
                ).fetchone()[0]
                venue_rows = conn.execute("SELECT COUNT(*) FROM venues").fetchone()[0]
                view_rows = conn.execute('SELECT COUNT(*) FROM "Google Places"').fetchone()[0]
                typed = conn.execute(
                    "SELECT COUNT(*) FROM google_places WHERE primary_type IS NOT NULL"
                ).fetchone()[0]
                type_rows = conn.execute("SELECT COUNT(*) FROM google_place_types").fetchone()[0]
            finally:
                conn.close()

        self.assertEqual(places, expected_places)
        self.assertEqual(distinct, expected_places)
        self.assertEqual(view_rows, expected_places)
        self.assertEqual(venue_rows, expected_listings)
        self.assertEqual(typed, expected_places)
        self.assertGreater(type_rows, expected_places)
        self.assertLess(places, expected_listings)


if __name__ == "__main__":
    unittest.main()
