#!/usr/bin/env python3
"""
Fetch detailed Google Places data for all venues using the Place Details (New) API.

Uses the google_place_id from venues_clean.json to fetch rich detail data including:
- Rating, review count, and individual reviews
- Business status, price level, price range
- Opening hours, phone number, website
- Atmosphere attributes (live music, outdoor seating, etc.)
- Editorial summary, generative summary

Outputs:
- venues/venues_details.json  (all venues with detailed Google Places data)

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

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
VENUES_INPUT = Path("venues/venues_clean.json")
VENUES_OUTPUT = Path("venues/venues_details.json")
PROGRESS_FILE = Path("venues/.details_progress.json")

# Google Places API (New) Place Details endpoint
# GET https://places.googleapis.com/v1/places/{PLACE_ID}
PLACES_BASE_URL = "https://places.googleapis.com/v1/places"

# Fields to request — grouped by billing tier:
#
# Essentials (IDs Only): id, name, photos
# Essentials: formattedAddress, location, types, addressComponents, shortFormattedAddress
# Pro: displayName, businessStatus, googleMapsUri, primaryType, primaryTypeDisplayName, timeZone, utcOffsetMinutes
# Enterprise: rating, userRatingCount, websiteUri, internationalPhoneNumber, nationalPhoneNumber,
#             priceLevel, priceRange, regularOpeningHours, currentOpeningHours
# Enterprise + Atmosphere: editorialSummary, reviews, goodForGroups, goodForChildren,
#             liveMusic, outdoorSeating, reservable, servesBeer, servesWine, servesCocktails,
#             dineIn, takeout, delivery, allowsDogs, restroom, goodForWatchingSports,
#             parkingOptions, paymentOptions, curbsidePickup, generativeSummary
FIELD_MASK = ",".join([
    # Pro tier
    "displayName",
    "businessStatus",
    "primaryType",
    "primaryTypeDisplayName",
    "timeZone",
    # Enterprise tier
    "rating",
    "userRatingCount",
    "websiteUri",
    "internationalPhoneNumber",
    "nationalPhoneNumber",
    "priceLevel",
    "priceRange",
    "regularOpeningHours",
    "currentOpeningHours",
    # Enterprise + Atmosphere tier
    "editorialSummary",
    "reviews",
    "goodForGroups",
    "goodForChildren",
    "liveMusic",
    "outdoorSeating",
    "reservable",
    "servesBeer",
    "servesWine",
    "servesCocktails",
    "dineIn",
    "takeout",
    "delivery",
    "allowsDogs",
    "restroom",
    "goodForWatchingSports",
    "parkingOptions",
    "paymentOptions",
    "generativeSummary",
])

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


def extract_details(raw: dict) -> dict:
    """
    Extract and flatten the fields we care about from the raw Place Details response.
    """
    details = {}

    # Basic info
    dn = raw.get("displayName", {})
    details["display_name"] = dn.get("text")
    details["business_status"] = raw.get("businessStatus")
    details["primary_type"] = raw.get("primaryType")
    ptdn = raw.get("primaryTypeDisplayName", {})
    details["primary_type_display_name"] = ptdn.get("text")

    # Time zone
    tz = raw.get("timeZone", {})
    details["time_zone"] = tz.get("id") if tz else None

    # Ratings & reviews
    details["rating"] = raw.get("rating")
    details["user_rating_count"] = raw.get("userRatingCount")

    # Contact
    details["website_uri"] = raw.get("websiteUri")
    details["international_phone_number"] = raw.get("internationalPhoneNumber")
    details["national_phone_number"] = raw.get("nationalPhoneNumber")

    # Price
    details["price_level"] = raw.get("priceLevel")
    price_range = raw.get("priceRange", {})
    if price_range:
        start = price_range.get("startPrice", {})
        end = price_range.get("endPrice", {})
        details["price_range_start_cents"] = int(float(start.get("units", 0)) * 100 + float(start.get("nanos", 0)) / 1e7) if start else None
        details["price_range_end_cents"] = int(float(end.get("units", 0)) * 100 + float(end.get("nanos", 0)) / 1e7) if end else None
        details["price_range_currency"] = start.get("currencyCode") or end.get("currencyCode")
    else:
        details["price_range_start_cents"] = None
        details["price_range_end_cents"] = None
        details["price_range_currency"] = None

    # Opening hours
    hours = raw.get("regularOpeningHours", {})
    details["open_now"] = raw.get("currentOpeningHours", {}).get("openNow")
    details["weekday_descriptions"] = hours.get("weekdayDescriptions", [])
    details["opening_hours_periods"] = hours.get("periods", [])

    # Editorial / generative summaries
    es = raw.get("editorialSummary", {})
    details["editorial_summary"] = es.get("text") if es else None
    gs = raw.get("generativeSummary", {})
    if gs:
        overview = gs.get("overview", {})
        details["generative_summary"] = overview.get("text") if overview else None
    else:
        details["generative_summary"] = None

    # Atmosphere booleans
    details["live_music"] = raw.get("liveMusic")
    details["good_for_groups"] = raw.get("goodForGroups")
    details["good_for_children"] = raw.get("goodForChildren")
    details["good_for_watching_sports"] = raw.get("goodForWatchingSports")
    details["outdoor_seating"] = raw.get("outdoorSeating")
    details["reservable"] = raw.get("reservable")
    details["serves_beer"] = raw.get("servesBeer")
    details["serves_wine"] = raw.get("servesWine")
    details["serves_cocktails"] = raw.get("servesCocktails")
    details["dine_in"] = raw.get("dineIn")
    details["takeout"] = raw.get("takeout")
    details["delivery"] = raw.get("delivery")
    details["allows_dogs"] = raw.get("allowsDogs")
    details["restroom"] = raw.get("restroom")
    details["curbside_pickup"] = raw.get("curbsidePickup")

    # Parking options
    parking = raw.get("parkingOptions", {})
    if parking:
        details["parking_free"] = parking.get("freeParkingLot")
        details["parking_paid"] = parking.get("paidParkingLot")
        details["parking_street"] = parking.get("freeStreetParking")
        details["parking_garage"] = parking.get("paidStreetParking")
        details["parking_valet"] = parking.get("valetParking")
    else:
        details["parking_free"] = None
        details["parking_paid"] = None
        details["parking_street"] = None
        details["parking_garage"] = None
        details["parking_valet"] = None

    # Payment options
    payment = raw.get("paymentOptions", {})
    if payment:
        details["accepts_credit_cards"] = payment.get("acceptsCreditCards")
        details["accepts_debit_cards"] = payment.get("acceptsDebitCards")
        details["accepts_cash_only"] = payment.get("acceptsCashOnly")
        details["accepts_nfc"] = payment.get("acceptsNfc")
    else:
        details["accepts_credit_cards"] = None
        details["accepts_debit_cards"] = None
        details["accepts_cash_only"] = None
        details["accepts_nfc"] = None

    # Reviews (keep the full array for later DB storage)
    reviews_raw = raw.get("reviews", [])
    details["reviews"] = []
    for r in reviews_raw:
        review = {
            "author_name": r.get("authorAttribution", {}).get("displayName"),
            "author_uri": r.get("authorAttribution", {}).get("uri"),
            "rating": r.get("rating"),
            "text": r.get("text", {}).get("text"),
            "language": r.get("text", {}).get("languageCode"),
            "relative_publish_time": r.get("relativePublishTimeDescription"),
            "publish_time": r.get("publishTime"),
            "google_maps_uri": r.get("googleMapsUri"),
        }
        details["reviews"].append(review)

    return details


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

    for i, venue in venues_to_process:
        place_id = venue.get("google_place_id")
        venue_name = venue.get("canonical_name") or venue.get("name")

        if not place_id:
            log.warning(f"[{i:4d}] No google_place_id for: {venue_name}")
            fail_count += 1
            continue

        if args.dry_run:
            print(f"[{i:4d}] FETCH: {place_id} ({venue_name})")
            continue

        log.info(f"[{i:4d}/{end_idx - 1}] Fetching: {venue_name} ({place_id})")

        raw = fetch_place_details(place_id, api_key)

        if raw:
            details = extract_details(raw)
            result = {
                **venue,
                "place_details": details,
            }
            results.append(result)
            success_count += 1

            review_count = len(details.get("reviews", []))
            rating = details.get("rating", "N/A")
            log.info(f"  -> OK: rating={rating}, reviews={review_count}, status={details.get('business_status', 'N/A')}")
        else:
            # Still include the venue, just without details
            result = {
                **venue,
                "place_details": None,
            }
            results.append(result)
            fail_count += 1
            log.warning(f"  -> FAILED to fetch details for: {venue_name}")

        # Save progress periodically
        if (i + 1) % SAVE_EVERY == 0:
            save_progress({"results": results, "last_index": i})
            log.info(f"  Progress saved ({len(results)} fetched so far)")

        time.sleep(REQUEST_DELAY)

    if args.dry_run:
        log.info("Dry run complete, no API calls made")
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
    log.info(f"DONE: {success_count} succeeded, {fail_count} failed out of {total} processed")


if __name__ == "__main__":
    main()
