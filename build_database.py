#!/usr/bin/env python3
"""
Build a SQLite database from venue data + Google Places detail data.

Reads venues/venues_details.json and creates a normalized SQLite database with:
- venues: Core venue data (name, location, Google metadata)
- venue_details: Detailed Google Places data (rating, hours, phone, website, etc.)
- venue_reviews: Individual Google reviews
- venue_types: Google place types per venue
- venue_hours: Structured opening hours periods

Usage:
    python3 build_database.py [--output venues.db]
"""

import json
import sqlite3
import argparse
import logging
from pathlib import Path

VENUES_INPUT = Path("venues/venues_details.json")
DEFAULT_DB = Path("venues.db")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


SCHEMA = """
-- Core venue table
CREATE TABLE IF NOT EXISTS venues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,                      -- original name from NIVA PDF
    canonical_name TEXT,                     -- preferred display name (from Google)
    google_name TEXT,                        -- official name per Google
    city TEXT NOT NULL,
    state TEXT NOT NULL,                     -- 2-letter US state abbreviation
    formatted_address TEXT,
    latitude REAL,
    longitude REAL,
    google_place_id TEXT,
    google_maps_uri TEXT,
    pollstar_id TEXT,
    co_located BOOLEAN DEFAULT FALSE,
    co_located_with TEXT,                    -- JSON array of co-located venue names
    co_located_note TEXT,
    raw_line TEXT                             -- original line from PDF
);

-- Detailed Google Places data
CREATE TABLE IF NOT EXISTS venue_details (
    venue_id INTEGER PRIMARY KEY REFERENCES venues(id),
    business_status TEXT,                    -- OPERATIONAL, CLOSED_PERMANENTLY, CLOSED_TEMPORARILY
    primary_type TEXT,                       -- e.g. "live_music_venue", "night_club"
    primary_type_display_name TEXT,          -- e.g. "Live Music Venue", "Night Club"
    time_zone TEXT,                          -- e.g. "America/New_York"
    rating REAL,                             -- 1.0 - 5.0
    user_rating_count INTEGER,
    website_uri TEXT,
    international_phone_number TEXT,
    national_phone_number TEXT,
    price_level TEXT,                        -- e.g. "PRICE_LEVEL_MODERATE"
    price_range_start_cents INTEGER,
    price_range_end_cents INTEGER,
    price_range_currency TEXT,
    editorial_summary TEXT,
    generative_summary TEXT,
    -- Atmosphere booleans
    live_music BOOLEAN,
    good_for_groups BOOLEAN,
    good_for_children BOOLEAN,
    good_for_watching_sports BOOLEAN,
    outdoor_seating BOOLEAN,
    reservable BOOLEAN,
    serves_beer BOOLEAN,
    serves_wine BOOLEAN,
    serves_cocktails BOOLEAN,
    dine_in BOOLEAN,
    takeout BOOLEAN,
    delivery BOOLEAN,
    allows_dogs BOOLEAN,
    restroom BOOLEAN,
    curbside_pickup BOOLEAN,
    -- Parking
    parking_free BOOLEAN,
    parking_paid BOOLEAN,
    parking_street BOOLEAN,
    parking_garage BOOLEAN,
    parking_valet BOOLEAN,
    -- Payment
    accepts_credit_cards BOOLEAN,
    accepts_debit_cards BOOLEAN,
    accepts_cash_only BOOLEAN,
    accepts_nfc BOOLEAN,
    -- Opening hours (human-readable, JSON array of day descriptions)
    weekday_descriptions TEXT               -- JSON array: ["Monday: 11 AM – 2 AM", ...]
);

-- Individual Google reviews
CREATE TABLE IF NOT EXISTS venue_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    venue_id INTEGER NOT NULL REFERENCES venues(id),
    author_name TEXT,
    author_uri TEXT,
    rating INTEGER,                          -- 1-5
    text TEXT,
    language TEXT,
    relative_publish_time TEXT,              -- e.g. "2 months ago"
    publish_time TEXT,                       -- ISO 8601 timestamp
    google_maps_uri TEXT
);

-- Google place types per venue (many-to-many)
CREATE TABLE IF NOT EXISTS venue_types (
    venue_id INTEGER NOT NULL REFERENCES venues(id),
    type TEXT NOT NULL,
    PRIMARY KEY (venue_id, type)
);

-- Structured opening hours periods
CREATE TABLE IF NOT EXISTS venue_hours (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    venue_id INTEGER NOT NULL REFERENCES venues(id),
    open_day INTEGER,       -- 0=Sunday, 1=Monday, ..., 6=Saturday
    open_hour INTEGER,
    open_minute INTEGER,
    close_day INTEGER,
    close_hour INTEGER,
    close_minute INTEGER
);

-- Useful indexes
CREATE INDEX IF NOT EXISTS idx_venues_state ON venues(state);
CREATE INDEX IF NOT EXISTS idx_venues_city_state ON venues(city, state);
CREATE INDEX IF NOT EXISTS idx_venues_google_place_id ON venues(google_place_id);
CREATE INDEX IF NOT EXISTS idx_venue_details_rating ON venue_details(rating);
CREATE INDEX IF NOT EXISTS idx_venue_details_business_status ON venue_details(business_status);
CREATE INDEX IF NOT EXISTS idx_venue_reviews_venue_id ON venue_reviews(venue_id);
CREATE INDEX IF NOT EXISTS idx_venue_types_type ON venue_types(type);
CREATE INDEX IF NOT EXISTS idx_venue_hours_venue_id ON venue_hours(venue_id);
"""


def build_database(input_path: Path, db_path: Path):
    """Build the SQLite database from the venues_details.json file."""

    # Load data
    with open(input_path) as f:
        venues = json.load(f)
    log.info(f"Loaded {len(venues)} venues from {input_path}")

    # Remove existing DB
    if db_path.exists():
        db_path.unlink()
        log.info(f"Removed existing database at {db_path}")

    # Create database
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    cursor = conn.cursor()

    # Create schema
    cursor.executescript(SCHEMA)
    log.info("Created database schema")

    venue_count = 0
    detail_count = 0
    review_count = 0
    type_count = 0
    hours_count = 0

    for venue in venues:
        # Insert venue
        co_located_with = json.dumps(venue.get("co_located_with")) if venue.get("co_located_with") else None

        cursor.execute("""
            INSERT INTO venues (
                name, canonical_name, google_name, city, state,
                formatted_address, latitude, longitude,
                google_place_id, google_maps_uri, pollstar_id,
                co_located, co_located_with, co_located_note, raw_line
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            venue.get("name"),
            venue.get("canonical_name"),
            venue.get("google_name"),
            venue.get("city"),
            venue.get("state"),
            venue.get("formatted_address"),
            venue.get("latitude"),
            venue.get("longitude"),
            venue.get("google_place_id"),
            venue.get("google_maps_uri"),
            venue.get("pollstar_id"),
            venue.get("co_located", False),
            co_located_with,
            venue.get("co_located_note"),
            venue.get("raw_line"),
        ))
        venue_id = cursor.lastrowid
        venue_count += 1

        # Insert types
        for t in venue.get("types", []):
            cursor.execute(
                "INSERT OR IGNORE INTO venue_types (venue_id, type) VALUES (?, ?)",
                (venue_id, t)
            )
            type_count += 1

        # Insert details
        details = venue.get("place_details")
        if details:
            weekday_desc = json.dumps(details.get("weekday_descriptions")) if details.get("weekday_descriptions") else None

            cursor.execute("""
                INSERT INTO venue_details (
                    venue_id, business_status, primary_type, primary_type_display_name,
                    time_zone, rating, user_rating_count, website_uri,
                    international_phone_number, national_phone_number,
                    price_level, price_range_start_cents, price_range_end_cents, price_range_currency,
                    editorial_summary, generative_summary,
                    live_music, good_for_groups, good_for_children, good_for_watching_sports,
                    outdoor_seating, reservable, serves_beer, serves_wine, serves_cocktails,
                    dine_in, takeout, delivery, allows_dogs, restroom, curbside_pickup,
                    parking_free, parking_paid, parking_street, parking_garage, parking_valet,
                    accepts_credit_cards, accepts_debit_cards, accepts_cash_only, accepts_nfc,
                    weekday_descriptions
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                venue_id,
                details.get("business_status"),
                details.get("primary_type"),
                details.get("primary_type_display_name"),
                details.get("time_zone"),
                details.get("rating"),
                details.get("user_rating_count"),
                details.get("website_uri"),
                details.get("international_phone_number"),
                details.get("national_phone_number"),
                details.get("price_level"),
                details.get("price_range_start_cents"),
                details.get("price_range_end_cents"),
                details.get("price_range_currency"),
                details.get("editorial_summary"),
                details.get("generative_summary"),
                details.get("live_music"),
                details.get("good_for_groups"),
                details.get("good_for_children"),
                details.get("good_for_watching_sports"),
                details.get("outdoor_seating"),
                details.get("reservable"),
                details.get("serves_beer"),
                details.get("serves_wine"),
                details.get("serves_cocktails"),
                details.get("dine_in"),
                details.get("takeout"),
                details.get("delivery"),
                details.get("allows_dogs"),
                details.get("restroom"),
                details.get("curbside_pickup"),
                details.get("parking_free"),
                details.get("parking_paid"),
                details.get("parking_street"),
                details.get("parking_garage"),
                details.get("parking_valet"),
                details.get("accepts_credit_cards"),
                details.get("accepts_debit_cards"),
                details.get("accepts_cash_only"),
                details.get("accepts_nfc"),
                weekday_desc,
            ))
            detail_count += 1

            # Insert reviews
            for review in details.get("reviews", []):
                cursor.execute("""
                    INSERT INTO venue_reviews (
                        venue_id, author_name, author_uri, rating, text,
                        language, relative_publish_time, publish_time, google_maps_uri
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    venue_id,
                    review.get("author_name"),
                    review.get("author_uri"),
                    review.get("rating"),
                    review.get("text"),
                    review.get("language"),
                    review.get("relative_publish_time"),
                    review.get("publish_time"),
                    review.get("google_maps_uri"),
                ))
                review_count += 1

            # Insert opening hours periods
            for period in details.get("opening_hours_periods", []):
                open_info = period.get("open", {})
                close_info = period.get("close", {})
                cursor.execute("""
                    INSERT INTO venue_hours (
                        venue_id, open_day, open_hour, open_minute,
                        close_day, close_hour, close_minute
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    venue_id,
                    open_info.get("day"),
                    open_info.get("hour"),
                    open_info.get("minute"),
                    close_info.get("day"),
                    close_info.get("hour"),
                    close_info.get("minute"),
                ))
                hours_count += 1

    conn.commit()

    # Print summary stats
    log.info("=" * 60)
    log.info(f"Database built: {db_path}")
    log.info(f"  Venues:  {venue_count}")
    log.info(f"  Details: {detail_count}")
    log.info(f"  Reviews: {review_count}")
    log.info(f"  Types:   {type_count}")
    log.info(f"  Hours:   {hours_count}")

    # Print some DB stats
    for table in ["venues", "venue_details", "venue_reviews", "venue_types", "venue_hours"]:
        count = cursor.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        log.info(f"  Table {table}: {count} rows")

    db_size = db_path.stat().st_size
    log.info(f"  Database size: {db_size / 1024:.1f} KB")

    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Build SQLite database from venue data")
    parser.add_argument("--output", "-o", type=Path, default=DEFAULT_DB, help="Output database path")
    parser.add_argument("--input", "-i", type=Path, default=VENUES_INPUT, help="Input JSON file")
    args = parser.parse_args()

    build_database(args.input, args.output)


if __name__ == "__main__":
    main()
