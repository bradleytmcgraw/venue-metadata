#!/usr/bin/env python3
"""
Build a SQLite database of unique Google Places venues plus source listings.

The unique entity is google_places (also queryable as "Google Places" /
"VenueMaster Metadata"), keyed by Google Place ID. Source listings live in
venues and may share a Place ID when rooms are co-located.

Reads venues/venues_details.json when present (listings + Place Details),
otherwise venues/venues_clean.json (search-level Places metadata).

Usage:
    python3 build_database.py [--output venues.db]
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from pathlib import Path

from places import (
    PLACE_SCALAR_COLUMNS,
    load_schema_sql,
    unique_google_places,
)

VENUES_DETAILS = Path("venues/venues_details.json")
VENUES_CLEAN = Path("venues/venues_clean.json")
VENUE_WEB = Path("venues/venue_web.json")
DEFAULT_DB = Path("venues.db")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

PLACE_INSERT_COLUMNS = ["google_place_id", *PLACE_SCALAR_COLUMNS, "listing_count", "details_fetched"]


def resolve_input_path(explicit: Path | None = None) -> Path:
    if explicit is not None:
        return explicit
    if VENUES_DETAILS.exists():
        return VENUES_DETAILS
    return VENUES_CLEAN


def _insert_place(cursor: sqlite3.Cursor, place: dict) -> None:
    placeholders = ",".join("?" * len(PLACE_INSERT_COLUMNS))
    columns = ",".join(PLACE_INSERT_COLUMNS)
    values = []
    for column in PLACE_INSERT_COLUMNS:
        value = place.get(column)
        if column == "details_fetched":
            value = 1 if value else 0
        values.append(value)
    cursor.execute(
        f"INSERT INTO google_places ({columns}) VALUES ({placeholders})",
        values,
    )


def build_database(
    input_path: Path,
    db_path: Path,
    venue_web_path: Path | None = None,
) -> sqlite3.Connection:
    """Build the SQLite database and return an open connection (caller closes)."""
    with open(input_path) as handle:
        listings = json.load(handle)
    log.info("Loaded %s listings from %s", len(listings), input_path)

    if db_path.exists():
        db_path.unlink()
        log.info("Removed existing database at %s", db_path)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    cursor = conn.cursor()
    cursor.executescript(load_schema_sql())
    log.info("Created database schema")

    places = unique_google_places(listings)
    log.info("Unique Google Places: %s", len(places))

    type_count = 0
    review_count = 0
    hours_count = 0

    for place_id, place in places.items():
        _insert_place(cursor, place)

        for type_name in place.get("types") or []:
            cursor.execute(
                "INSERT OR IGNORE INTO google_place_types (google_place_id, type) VALUES (?, ?)",
                (place_id, type_name),
            )
            type_count += 1

        for review in place.get("reviews") or []:
            cursor.execute(
                """
                INSERT INTO google_place_reviews (
                    google_place_id, author_name, author_uri, rating, text,
                    language, relative_publish_time, publish_time, google_maps_uri
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    place_id,
                    review.get("author_name"),
                    review.get("author_uri"),
                    review.get("rating"),
                    review.get("text"),
                    review.get("language"),
                    review.get("relative_publish_time"),
                    review.get("publish_time"),
                    review.get("google_maps_uri"),
                ),
            )
            review_count += 1

        for period in place.get("opening_hours_periods") or []:
            open_info = period.get("open") or {}
            close_info = period.get("close") or {}
            cursor.execute(
                """
                INSERT INTO google_place_hours (
                    google_place_id, open_day, open_hour, open_minute,
                    close_day, close_hour, close_minute
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    place_id,
                    open_info.get("day"),
                    open_info.get("hour"),
                    open_info.get("minute"),
                    close_info.get("day"),
                    close_info.get("hour"),
                    close_info.get("minute"),
                ),
            )
            hours_count += 1

    web_count = 0
    web_skipped = 0
    if venue_web_path is None:
        venue_web_path = VENUE_WEB if VENUE_WEB.exists() else None
    web_path = venue_web_path if venue_web_path and venue_web_path.exists() else None
    if web_path:
        with open(web_path) as handle:
            web_rows = json.load(handle)
        for row in web_rows:
            place_id = row.get("google_place_id")
            if not place_id or place_id not in places:
                web_skipped += 1
                continue
            platforms = row.get("ticketing_platforms")
            cursor.execute(
                """
                INSERT OR REPLACE INTO venue_web (
                    google_place_id, website, upcoming_shows_url,
                    ticketing_platform, ticketing_platforms, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    place_id,
                    row.get("website"),
                    row.get("upcoming_shows_url"),
                    row.get("ticketing_platform"),
                    json.dumps(platforms) if isinstance(platforms, list) else platforms,
                    row.get("fetched_at"),
                ),
            )
            web_count += 1
        log.info("Loaded venue_web rows from %s", web_path)

    venue_count = 0
    skipped_fk = 0
    for listing in listings:
        place_id = listing.get("google_place_id")
        if place_id and place_id not in places:
            skipped_fk += 1
            place_id = None

        co_located_with = (
            json.dumps(listing.get("co_located_with"))
            if listing.get("co_located_with")
            else None
        )
        cursor.execute(
            """
            INSERT INTO venues (
                google_place_id, name, canonical_name, city, state,
                pollstar_id, co_located, co_located_with, co_located_note, raw_line
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                place_id,
                listing.get("name"),
                listing.get("canonical_name"),
                listing.get("city"),
                listing.get("state"),
                listing.get("pollstar_id"),
                1 if listing.get("co_located") else 0,
                co_located_with,
                listing.get("co_located_note"),
                listing.get("raw_line"),
            ),
        )
        venue_count += 1

    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    log.info("=" * 60)
    log.info("Database built: %s", db_path)
    log.info("  Listings: %s", venue_count)
    log.info("  Google Places: %s", len(places))
    log.info("  Types: %s", type_count)
    log.info("  Reviews: %s", review_count)
    log.info("  Hours: %s", hours_count)
    if skipped_fk:
        log.warning("  Listings with unknown Place ID skipped for FK: %s", skipped_fk)
    log.info("  Venue web rows inserted: %s", web_count)
    if web_skipped:
        log.warning("  Venue web rows skipped (unknown Place ID): %s", web_skipped)

    for table in [
        "google_places",
        "google_place_types",
        "google_place_reviews",
        "google_place_hours",
        "venues",
        "venue_web",
    ]:
        count = cursor.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        log.info("  Table %s: %s rows", table, count)

    view_count = cursor.execute('SELECT COUNT(*) FROM "Google Places"').fetchone()[0]
    log.info('  View "Google Places": %s rows', view_count)
    log.info("  Database size: %.1f KB", db_path.stat().st_size / 1024)

    return conn


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build SQLite database of unique Google Places venues"
    )
    parser.add_argument("--output", "-o", type=Path, default=DEFAULT_DB, help="Output database path")
    parser.add_argument(
        "--input",
        "-i",
        type=Path,
        default=None,
        help="Input JSON file (default: venues_details.json if present, else venues_clean.json)",
    )
    args = parser.parse_args()

    conn = build_database(resolve_input_path(args.input), args.output)
    conn.close()


if __name__ == "__main__":
    main()
