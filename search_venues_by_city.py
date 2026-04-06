#!/usr/bin/env python3
"""
Discover music venues city-by-city using Google Places API (New) Text Search.

Reads venues/us_urban_centers.json (extracted from the GHSL Urban Centre Database)
and for each city runs multiple Text Search queries with a hard location
restriction (bounding box) to find music venues, concert halls, performing
arts theaters, stadiums, arenas, and other live entertainment venues.

Outputs:
- venues/city_venue_search.json  (place_id -> venue data + list of source cities)

The output is a 1:many mapping from venue (by place_id) to the cities whose
searches surfaced it.  A single venue may appear in searches for multiple
nearby cities.

Usage:
    export GOOGLE_PLACES_API_KEY="your-key-here"
    python3 search_venues_by_city.py [--dry-run] [--limit N] [--start N] [--resume]

Options:
    --dry-run    Print what would be searched without making API calls
    --limit N    Only process N cities (useful for testing)
    --start N    Start from city index N (0-based, for resuming)
    --resume     Resume from last saved progress
    --min-pop N  Minimum population for a city to be included (default: 0)
"""

import json
import math
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
CITIES_INPUT = Path("venues/us_urban_centers.json")
VENUES_OUTPUT = Path("venues/city_venue_search.json")
PROGRESS_FILE = Path("venues/.city_search_progress.json")

# Google Places API (New) Text Search endpoint
PLACES_URL = "https://places.googleapis.com/v1/places:searchText"

# Fields to return (Pro tier only - keeps cost low for discovery phase)
FIELD_MASK = (
    "places.id,"
    "places.displayName,"
    "places.formattedAddress,"
    "places.location,"
    "places.types,"
    "places.primaryType,"
    "places.googleMapsUri,"
    "nextPageToken"
)

# Rate limiting
REQUEST_DELAY = 1.0   # seconds between requests
MAX_RETRIES = 3
RETRY_DELAY = 2.0

# Location restriction: hard bounding box half-width in km from city center.
# 50 km ~ 31 miles.  With 344 cities this provides thorough national coverage.
RESTRICTION_HALF_WIDTH_KM = 50.0

# Search queries to run per city.
# Each tuple is (text_query, included_type_or_None)
# Using includedType where possible for precision, with a text query
# to catch venues whose names don't match the type exactly.
#
# Tier 1: Core music venues
# Tier 2: Big venues / sports
# Tier 3: Other performing arts & entertainment
SEARCH_QUERIES = [
    # Tier 1 - Core music venues
    ("music venue", "live_music_venue"),
    ("concert hall", "concert_hall"),
    ("performing arts theater", "performing_arts_theater"),
    ("live music bar", "bar"),
    ("live music nightclub", "night_club"),
    ("event venue", "event_venue"),
    ("amphitheater", "amphitheatre"),
    ("comedy club", "comedy_club"),
    # Tier 2 - Big venues / sports
    ("stadium", "stadium"),
    ("arena", "arena"),
    ("sports complex", "sports_complex"),
    # Tier 3 - Other performing arts & entertainment
    ("auditorium", "auditorium"),
    ("convention center", "convention_center"),
    ("philharmonic hall", "philharmonic_hall"),
    ("opera house", "opera_house"),
    ("casino entertainment", "casino"),
]

# Maximum pages to fetch per query (each page = 20 results, max 60 total)
MAX_PAGES_PER_QUERY = 3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Geo helpers
# ---------------------------------------------------------------------------
def bounding_box(lat: float, lon: float, half_width_km: float) -> dict:
    """
    Compute a lat/lng rectangle (low/high corners) centered on (lat, lon)
    with the given half-width in kilometres.

    Returns a dict suitable for the Text Search locationRestriction field:
        {"rectangle": {"low": {"latitude": ..., "longitude": ...},
                        "high": {"latitude": ..., "longitude": ...}}}
    """
    # 1 degree latitude ~ 111.32 km everywhere
    d_lat = half_width_km / 111.32
    # 1 degree longitude shrinks with cos(latitude)
    d_lon = half_width_km / (111.32 * math.cos(math.radians(lat)))

    return {
        "rectangle": {
            "low": {
                "latitude": lat - d_lat,
                "longitude": lon - d_lon,
            },
            "high": {
                "latitude": lat + d_lat,
                "longitude": lon + d_lon,
            },
        }
    }


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------
def text_search(
    query: str,
    api_key: str,
    latitude: float,
    longitude: float,
    half_width_km: float = RESTRICTION_HALF_WIDTH_KM,
    included_type: str | None = None,
    page_token: str | None = None,
) -> dict:
    """
    Call the Google Places Text Search (New) API with a hard location
    restriction (bounding box).  Only results within the box are returned.
    Retries on transient errors.
    """
    body_dict: dict = {
        "textQuery": query,
        "languageCode": "en",
        "regionCode": "US",
        "pageSize": 20,
        "locationRestriction": bounding_box(lat=latitude, lon=longitude,
                                             half_width_km=half_width_km),
    }

    if included_type:
        body_dict["includedType"] = included_type

    if page_token:
        body_dict["pageToken"] = page_token

    body = json.dumps(body_dict).encode("utf-8")

    for attempt in range(1, MAX_RETRIES + 1):
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
            with urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else ""
            if e.code in (400, 429, 500, 503) and attempt < MAX_RETRIES:
                wait = RETRY_DELAY * attempt
                log.warning(
                    f"HTTP {e.code} (attempt {attempt}/{MAX_RETRIES}), "
                    f"retrying in {wait:.1f}s... ({error_body[:150]})"
                )
                time.sleep(wait)
                continue
            log.error(f"HTTP {e.code} for query '{query}': {error_body[:200]}")
            return {}
        except URLError as e:
            if attempt < MAX_RETRIES:
                wait = RETRY_DELAY * attempt
                log.warning(
                    f"URL error (attempt {attempt}/{MAX_RETRIES}), "
                    f"retrying in {wait:.1f}s..."
                )
                time.sleep(wait)
                continue
            log.error(f"URL error for query '{query}': {e}")
            return {}

    return {}


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
        "primary_type": place.get("primaryType"),
    }


def search_city(
    city: dict,
    api_key: str,
    dry_run: bool = False,
) -> list[dict]:
    """
    Run all search queries for a single city.
    Returns a list of extracted place dicts (may contain duplicates across queries).
    """
    city_name = city["name"]
    lat = city["latitude"]
    lon = city["longitude"]

    all_places = []
    seen_ids = set()  # deduplicate within this city

    for query_text, included_type in SEARCH_QUERIES:
        if dry_run:
            type_str = f" [type={included_type}]" if included_type else ""
            bbox = bounding_box(lat, lon, RESTRICTION_HALF_WIDTH_KM)
            lo = bbox["rectangle"]["low"]
            hi = bbox["rectangle"]["high"]
            print(
                f"    SEARCH: '{query_text}'{type_str} "
                f"in [{lo['latitude']:.2f},{lo['longitude']:.2f}]-"
                f"[{hi['latitude']:.2f},{hi['longitude']:.2f}]"
            )
            continue

        log.debug(f"  Query: '{query_text}' type={included_type}")

        page = 0
        next_token = None

        while page < MAX_PAGES_PER_QUERY:
            response = text_search(
                query=query_text,
                api_key=api_key,
                latitude=lat,
                longitude=lon,
                included_type=included_type,
                page_token=next_token,
            )

            places = response.get("places", [])
            next_token = response.get("nextPageToken")

            for place in places:
                place_data = extract_place_data(place)
                pid = place_data["google_place_id"]
                if pid and pid not in seen_ids:
                    seen_ids.add(pid)
                    place_data["_source_query"] = query_text
                    place_data["_source_type"] = included_type
                    all_places.append(place_data)

            page_count = len(places)
            log.debug(
                f"    Page {page + 1}: {page_count} results, "
                f"{len(all_places)} unique so far"
            )

            time.sleep(REQUEST_DELAY)

            if not next_token or page_count == 0:
                break
            page += 1

    return all_places


# ---------------------------------------------------------------------------
# Progress / resume
# ---------------------------------------------------------------------------
def load_progress() -> dict:
    """Load previously saved progress."""
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {"venues": {}, "city_results": {}, "last_city_index": -1}


def save_progress(progress: dict):
    """Save progress to disk for resume capability."""
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f, indent=2)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Search for music venues city-by-city using Google Places API"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print queries without calling API"
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Only process N cities"
    )
    parser.add_argument(
        "--start", type=int, default=0,
        help="Start from city index N (0-based)"
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Resume from last saved progress"
    )
    parser.add_argument(
        "--min-pop", type=float, default=0,
        help="Minimum population for a city to be included"
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Enable debug logging"
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    api_key = os.environ.get("GOOGLE_PLACES_API_KEY")
    if not api_key and not args.dry_run:
        log.error("GOOGLE_PLACES_API_KEY environment variable is not set")
        sys.exit(1)

    # Load city list
    with open(CITIES_INPUT) as f:
        all_cities = json.load(f)
    log.info(f"Loaded {len(all_cities)} US urban centers from {CITIES_INPUT}")

    # Filter by minimum population
    if args.min_pop > 0:
        cities = [c for c in all_cities if c["population"] >= args.min_pop]
        log.info(
            f"Filtered to {len(cities)} cities with population >= {args.min_pop:,.0f}"
        )
    else:
        cities = all_cities

    # Sort by population descending (process biggest cities first)
    cities.sort(key=lambda c: c["population"], reverse=True)

    # Handle resume
    if args.resume:
        progress = load_progress()
        start_idx = progress["last_city_index"] + 1
        venues = progress["venues"]
        city_results = progress["city_results"]
        log.info(
            f"Resuming from city index {start_idx} "
            f"({len(venues)} unique venues found so far across "
            f"{len(city_results)} cities)"
        )
    else:
        start_idx = args.start
        venues = {}       # place_id -> venue data
        city_results = {} # city_name -> list of place_ids

    # Determine range
    end_idx = len(cities)
    if args.limit > 0:
        end_idx = min(start_idx + args.limit, len(cities))

    cities_to_process = list(enumerate(cities))[start_idx:end_idx]
    total_queries = len(cities_to_process) * len(SEARCH_QUERIES)
    log.info(
        f"Processing {len(cities_to_process)} cities (index {start_idx} to {end_idx - 1}), "
        f"~{total_queries} queries (before pagination)"
    )

    for i, city in cities_to_process:
        city_name = city["name"]
        city_key = f"{city_name}|{city['ucdb_id']}"
        pop = city["population"]

        if args.dry_run:
            print(f"\n[{i:4d}] {city_name} (pop {pop:,.0f})")
        else:
            log.info(
                f"[{i:4d}/{end_idx - 1}] Searching: {city_name} "
                f"(pop {pop:,.0f}, {city['latitude']:.4f}, {city['longitude']:.4f})"
            )

        city_places = search_city(city, api_key, dry_run=args.dry_run)

        if args.dry_run:
            continue

        # Merge results into global venue dict
        city_place_ids = []
        new_count = 0
        for place in city_places:
            pid = place["google_place_id"]
            city_place_ids.append(pid)

            if pid not in venues:
                venues[pid] = {
                    "google_place_id": pid,
                    "google_name": place["google_name"],
                    "formatted_address": place["formatted_address"],
                    "latitude": place["latitude"],
                    "longitude": place["longitude"],
                    "google_maps_uri": place["google_maps_uri"],
                    "types": place["types"],
                    "primary_type": place["primary_type"],
                    "source_cities": [],
                    "source_queries": [],
                }
                new_count += 1

            # Track which cities surfaced this venue
            if city_key not in venues[pid]["source_cities"]:
                venues[pid]["source_cities"].append(city_key)

            # Track which query found it (for analysis)
            query_key = f"{place['_source_query']}|{place['_source_type']}"
            if query_key not in venues[pid]["source_queries"]:
                venues[pid]["source_queries"].append(query_key)

        city_results[city_key] = city_place_ids

        log.info(
            f"  -> {len(city_places)} venues found for {city_name} "
            f"({new_count} new, {len(venues)} total unique)"
        )

        # Save progress every 5 cities
        if (i + 1) % 5 == 0:
            save_progress({
                "venues": venues,
                "city_results": city_results,
                "last_city_index": i,
            })
            log.info(f"  Progress saved ({len(venues)} unique venues across {len(city_results)} cities)")

    if args.dry_run:
        log.info("Dry run complete, no API calls made")
        return

    # Save final progress (in case of odd number of cities)
    save_progress({
        "venues": venues,
        "city_results": city_results,
        "last_city_index": end_idx - 1,
    })

    # Build final output
    output = {
        "metadata": {
            "total_cities_searched": len(city_results),
            "total_unique_venues": len(venues),
            "search_queries": [
                {"text_query": q, "included_type": t}
                for q, t in SEARCH_QUERIES
            ],
            "location_restriction_half_width_km": RESTRICTION_HALF_WIDTH_KM,
        },
        "venues": venues,
        "city_results": city_results,
    }

    with open(VENUES_OUTPUT, "w") as f:
        json.dump(output, f, indent=2)
    log.info(f"Wrote {len(venues)} unique venues to {VENUES_OUTPUT}")

    # Summary
    log.info("=" * 60)
    log.info(f"DONE: {len(venues)} unique venues from {len(city_results)} cities")

    # Stats on multi-city venues
    multi_city = sum(1 for v in venues.values() if len(v["source_cities"]) > 1)
    log.info(f"  {multi_city} venues appeared in searches for multiple cities")

    # Top types
    type_counts: dict[str, int] = {}
    for v in venues.values():
        pt = v.get("primary_type")
        if pt:
            type_counts[pt] = type_counts.get(pt, 0) + 1
    log.info("  Top primary types:")
    for ptype, count in sorted(type_counts.items(), key=lambda x: -x[1])[:10]:
        log.info(f"    {ptype}: {count}")


if __name__ == "__main__":
    main()
