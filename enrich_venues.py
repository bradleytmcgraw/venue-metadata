#!/usr/bin/env python3
"""
Enrich venue data using the Google Places API (New) Text Search endpoint.

Reads venues/venues.json, queries Google Places for each venue to get:
- Google Place ID (canonical identifier)
- Official display name
- Full address
- Lat/lng coordinates

Outputs:
- venues/venues_enriched.json  (all venues with Google data)
- venues/venues_unmatched.json (venues that couldn't be matched)

Usage:
    export GOOGLE_PLACES_API_KEY="your-key-here"
    python3 enrich_venues.py [--dry-run] [--limit N] [--start N]

Options:
    --dry-run   Print what would be searched without making API calls
    --limit N   Only process N venues (useful for testing)
    --start N   Start from venue index N (0-based, for resuming)
"""

import json
import os
import sys
import time
import argparse
import logging
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
VENUES_INPUT = Path("venues/venues.json")
VENUES_OUTPUT = Path("venues/venues_enriched.json")
VENUES_UNMATCHED = Path("venues/venues_unmatched.json")
PROGRESS_FILE = Path("venues/.enrich_progress.json")

# Google Places API (New) Text Search endpoint
PLACES_URL = "https://places.googleapis.com/v1/places:searchText"

# Fields we want back (controls billing - only request what we need)
FIELD_MASK = "places.id,places.displayName,places.formattedAddress,places.location,places.types,places.googleMapsUri"

# Rate limiting: be conservative to avoid transient 400/429 errors
REQUEST_DELAY = 1.0  # seconds between requests (~60 QPM)
MAX_RETRIES = 3       # retries per venue on transient errors
RETRY_DELAY = 2.0     # seconds to wait before retrying

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# US state abbreviation to full name for better search queries
STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine",
    "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska",
    "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}


def build_search_query(venue: dict) -> str:
    """Build a search query string from venue data."""
    name = venue["name"]
    city = venue["city"]
    state = venue["state"]
    state_full = STATE_NAMES.get(state, state)
    return f"{name} {city} {state_full}"


def search_place(query: str, api_key: str) -> dict | None:
    """
    Query Google Places API (New) Text Search for a venue.
    Returns the top result or None if no match found.
    Retries on transient errors (400, 429, 500, 503).
    """
    for attempt in range(1, MAX_RETRIES + 1):
        body = json.dumps({
            "textQuery": query,
            "languageCode": "en",
        }).encode("utf-8")

        req = Request(
            PLACES_URL,
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": api_key,
                "X-Goog-FieldMask": FIELD_MASK,
            },
            method="POST",
        )

        try:
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            places = data.get("places", [])
            if not places:
                return None
            return places[0]
        except HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else ""
            if e.code in (400, 429, 500, 503) and attempt < MAX_RETRIES:
                wait = RETRY_DELAY * attempt  # exponential-ish backoff
                log.warning(
                    f"HTTP {e.code} for '{query}' (attempt {attempt}/{MAX_RETRIES}), "
                    f"retrying in {wait:.1f}s..."
                )
                time.sleep(wait)
                continue
            log.error(f"HTTP {e.code} for query '{query}': {error_body[:200]}")
            return None
        except URLError as e:
            if attempt < MAX_RETRIES:
                wait = RETRY_DELAY * attempt
                log.warning(f"URL error for '{query}' (attempt {attempt}/{MAX_RETRIES}), retrying in {wait:.1f}s...")
                time.sleep(wait)
                continue
            log.error(f"URL error for query '{query}': {e}")
            return None

    return None


def extract_place_data(place: dict) -> dict:
    """Extract the fields we care about from a Places API result."""
    location = place.get("location", {})
    display_name = place.get("displayName", {})
    return {
        "google_place_id": place.get("id"),
        "google_name": display_name.get("text"),
        "formatted_address": place.get("formattedAddress"),
        "latitude": location.get("latitude"),
        "longitude": location.get("longitude"),
        "google_maps_uri": place.get("googleMapsUri"),
        "types": place.get("types", []),
    }


def load_progress() -> dict:
    """Load previously saved progress (enriched venues so far)."""
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {"enriched": [], "unmatched": [], "last_index": -1}


def save_progress(progress: dict):
    """Save progress to disk for resume capability."""
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f, indent=2)


def main():
    parser = argparse.ArgumentParser(description="Enrich venues with Google Places data")
    parser.add_argument("--dry-run", action="store_true", help="Print queries without calling API")
    parser.add_argument("--limit", type=int, default=0, help="Process only N venues")
    parser.add_argument("--start", type=int, default=0, help="Start from index N (0-based)")
    parser.add_argument("--resume", action="store_true", help="Resume from last saved progress")
    args = parser.parse_args()

    api_key = os.environ.get("GOOGLE_PLACES_API_KEY")
    if not api_key and not args.dry_run:
        log.error("GOOGLE_PLACES_API_KEY environment variable is not set")
        sys.exit(1)

    # Load input venues
    with open(VENUES_INPUT) as f:
        venues = json.load(f)
    log.info(f"Loaded {len(venues)} venues from {VENUES_INPUT}")

    # Handle resume
    if args.resume:
        progress = load_progress()
        start_idx = progress["last_index"] + 1
        enriched = progress["enriched"]
        unmatched = progress["unmatched"]
        log.info(f"Resuming from index {start_idx} ({len(enriched)} enriched, {len(unmatched)} unmatched so far)")
    else:
        start_idx = args.start
        enriched = []
        unmatched = []

    # Determine range to process
    end_idx = len(venues)
    if args.limit > 0:
        end_idx = min(start_idx + args.limit, len(venues))

    venues_to_process = list(enumerate(venues))[start_idx:end_idx]
    log.info(f"Processing venues {start_idx} to {end_idx - 1} ({len(venues_to_process)} venues)")

    for i, venue in venues_to_process:
        query = build_search_query(venue)

        if args.dry_run:
            print(f"[{i:4d}] SEARCH: {query}")
            continue

        log.info(f"[{i:4d}/{end_idx - 1}] Searching: {query}")

        assert api_key is not None
        place = search_place(query, api_key)

        if place:
            place_data = extract_place_data(place)
            enriched_venue = {
                **venue,
                **place_data,
            }
            enriched.append(enriched_venue)
            log.info(
                f"  -> MATCH: {place_data['google_name']} "
                f"({place_data['formatted_address']})"
            )
        else:
            unmatched.append(venue)
            log.warning(f"  -> NO MATCH for: {venue['name']} ({venue['city']}, {venue['state']})")

        # Save progress every 25 venues
        if (i + 1) % 25 == 0:
            save_progress({"enriched": enriched, "unmatched": unmatched, "last_index": i})
            log.info(f"  Progress saved ({len(enriched)} enriched, {len(unmatched)} unmatched)")

        time.sleep(REQUEST_DELAY)

    if args.dry_run:
        log.info("Dry run complete, no API calls made")
        return

    # Write final outputs
    with open(VENUES_OUTPUT, "w") as f:
        json.dump(enriched, f, indent=2)
    log.info(f"Wrote {len(enriched)} enriched venues to {VENUES_OUTPUT}")

    if unmatched:
        with open(VENUES_UNMATCHED, "w") as f:
            json.dump(unmatched, f, indent=2)
        log.info(f"Wrote {len(unmatched)} unmatched venues to {VENUES_UNMATCHED}")

    # Clean up progress file
    if PROGRESS_FILE.exists():
        PROGRESS_FILE.unlink()

    # Summary
    log.info("=" * 60)
    log.info(f"DONE: {len(enriched)} matched, {len(unmatched)} unmatched out of {len(venues_to_process)} processed")
    if unmatched:
        log.info("Unmatched venues:")
        for v in unmatched:
            log.info(f"  - {v['name']} ({v['city']}, {v['state']})")


if __name__ == "__main__":
    main()
