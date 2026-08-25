#!/usr/bin/env python3
"""
Fetch detailed Google Places data for unique Place IDs using Place Details (New).

Uses google_place_id from venues_clean.json. Co-located listings that share a
Place ID are fetched once. Details include:
- Rating, review count, and individual reviews
- Business status, price level, price range, types
- Opening hours, phone number, website
- Menu and food-service attributes (servesBreakfast, menuForChildren, etc.)
- Atmosphere attributes (live music, outdoor seating, etc.)
- Editorial summary, generative summary

Outputs:
- venues/venues_details.json  (listings with nested place_details)

Usage:
    export GOOGLE_PLACES_API_KEY="your-key-here"
    python3 fetch_place_details.py [--dry-run] [--limit N] [--start N] [--resume]
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

from places import FIELD_MASK, extract_details

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
VENUES_INPUT = Path("venues/venues_clean.json")
VENUES_OUTPUT = Path("venues/venues_details.json")
PROGRESS_FILE = Path("venues/.details_progress.json")

# Google Places API (New) Place Details endpoint
# GET https://places.googleapis.com/v1/places/{PLACE_ID}
PLACES_BASE_URL = "https://places.googleapis.com/v1/places"

# Rate limiting
REQUEST_DELAY = 0.1   # seconds between requests (Places Details is generous)
MAX_RETRIES = 3
RETRY_DELAY = 2.0
SAVE_EVERY = 25       # save progress every N venues

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def fetch_place_details(place_id: str, api_key: str) -> dict | None:
    """
    Fetch detailed place data from Google Places API (New) Place Details endpoint.
    Returns the full response dict or None on failure.
    """
    url = f"{PLACES_BASE_URL}/{place_id}"

    for attempt in range(1, MAX_RETRIES + 1):
        req = Request(
            url,
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": api_key,
                "X-Goog-FieldMask": FIELD_MASK,
            },
            method="GET",
        )

        try:
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data
        except HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else ""
            if e.code in (429, 500, 503) and attempt < MAX_RETRIES:
                wait = RETRY_DELAY * attempt
                log.warning(
                    f"HTTP {e.code} for '{place_id}' (attempt {attempt}/{MAX_RETRIES}), "
                    f"retrying in {wait:.1f}s..."
                )
                time.sleep(wait)
                continue
            log.error(f"HTTP {e.code} for place '{place_id}': {error_body[:300]}")
            return None
        except URLError as e:
            if attempt < MAX_RETRIES:
                wait = RETRY_DELAY * attempt
                log.warning(f"URL error for '{place_id}' (attempt {attempt}/{MAX_RETRIES}), retrying in {wait:.1f}s...")
                time.sleep(wait)
                continue
            log.error(f"URL error for place '{place_id}': {e}")
            return None

    return None


def load_progress() -> dict:
    """Load previously saved progress."""
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {"results": [], "last_index": -1}


def save_progress(progress: dict):
    """Save progress to disk for resume capability."""
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f)  # no indent to keep file smaller during progress


def main():
    parser = argparse.ArgumentParser(description="Fetch Google Places detail data for venues")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be fetched without calling API")
    parser.add_argument("--limit", type=int, default=0, help="Process only N venues")
    parser.add_argument("--start", type=int, default=0, help="Start from index N (0-based)")
    parser.add_argument("--resume", action="store_true", help="Resume from last saved progress")
    args = parser.parse_args()

    api_key = os.environ.get("GOOGLE_PLACES_API_KEY")
    if not api_key and not args.dry_run:
        log.error("GOOGLE_PLACES_API_KEY environment variable is not set")
        sys.exit(1)

    # Load venues
    with open(VENUES_INPUT) as f:
        venues = json.load(f)
    log.info(f"Loaded {len(venues)} venues from {VENUES_INPUT}")

    # Handle resume
    if args.resume:
        progress = load_progress()
        start_idx = progress["last_index"] + 1
        results = progress["results"]
        log.info(f"Resuming from index {start_idx} ({len(results)} already fetched)")
    else:
        start_idx = args.start
        results = []

    # Determine range
    end_idx = len(venues)
    if args.limit > 0:
        end_idx = min(start_idx + args.limit, len(venues))

    venues_to_process = list(enumerate(venues))[start_idx:end_idx]
    total = len(venues_to_process)
    log.info(f"Processing venues {start_idx} to {end_idx - 1} ({total} venues)")
    log.info(f"Field mask: {FIELD_MASK}")

    success_count = 0
    fail_count = 0
    cache_hits = 0
    details_by_place_id: dict[str, dict | None] = {}
    for previous in results:
        previous_id = previous.get("google_place_id")
        if previous_id and previous_id not in details_by_place_id:
            details_by_place_id[previous_id] = previous.get("place_details")

    for i, venue in venues_to_process:
        place_id = venue.get("google_place_id")
        venue_name = venue.get("canonical_name") or venue.get("name")

        if not place_id:
            log.warning(f"[{i:4d}] No google_place_id for: {venue_name}")
            fail_count += 1
            continue

        if args.dry_run:
            cached = " (cached unique Place ID)" if place_id in details_by_place_id else ""
            print(f"[{i:4d}] FETCH: {place_id} ({venue_name}){cached}")
            details_by_place_id.setdefault(place_id, None)
            continue

        if place_id in details_by_place_id:
            details = details_by_place_id[place_id]
            cache_hits += 1
            log.info(f"[{i:4d}/{end_idx - 1}] Reusing Place ID {place_id} for: {venue_name}")
        else:
            log.info(f"[{i:4d}/{end_idx - 1}] Fetching: {venue_name} ({place_id})")
            raw = fetch_place_details(place_id, api_key)
            details = extract_details(raw) if raw else None
            details_by_place_id[place_id] = details
            time.sleep(REQUEST_DELAY)

        result = {
            **venue,
            "place_details": details,
        }
        results.append(result)

        if details:
            success_count += 1
            review_count = len(details.get("reviews", []))
            rating = details.get("rating", "N/A")
            log.info(
                f"  -> OK: rating={rating}, reviews={review_count}, "
                f"status={details.get('business_status', 'N/A')}, "
                f"primary_type={details.get('primary_type', 'N/A')}"
            )
        else:
            fail_count += 1
            log.warning(f"  -> FAILED to fetch details for: {venue_name}")

        # Save progress periodically
        if (i + 1) % SAVE_EVERY == 0:
            save_progress({"results": results, "last_index": i})
            log.info(f"  Progress saved ({len(results)} fetched so far)")

    if args.dry_run:
        unique_ids = len(details_by_place_id)
        log.info("Dry run complete, no API calls made (%s unique Place IDs)", unique_ids)
        return

    # Write final output
    with open(VENUES_OUTPUT, "w") as f:
        json.dump(results, f, indent=2)
    log.info(f"Wrote {len(results)} venues to {VENUES_OUTPUT}")

    # Clean up progress file
    if PROGRESS_FILE.exists():
        PROGRESS_FILE.unlink()

    # Summary
    log.info("=" * 60)
    log.info(
        f"DONE: {success_count} succeeded, {fail_count} failed, "
        f"{cache_hits} Place ID cache hits out of {total} processed"
    )


if __name__ == "__main__":
    main()
