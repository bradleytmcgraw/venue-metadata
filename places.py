#!/usr/bin/env python3
"""
Shared Google Places field mask, Place Details flattening, and unique-place aggregation.

The unique grain is Google Place ID. Source listings (NIVA names) can share a Place ID
when rooms are co-located; those collapse to one google_places row.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

# Types that are too generic to use as primary_type when Place Details
# did not return primaryType.
GENERIC_TYPES = frozenset({
    "point_of_interest",
    "establishment",
    "food",
    "service",
})

# Google Places API (New) Place Details field mask.
#
# Essentials: id, types, formattedAddress, location
# Pro: displayName, businessStatus, googleMapsUri, primaryType, primaryTypeDisplayName, timeZone
# Enterprise: rating, userRatingCount, websiteUri, phone, price, opening hours
# Enterprise + Atmosphere: summaries, reviews, menu/food-service, parking, payment
FIELD_MASK = ",".join([
    "id",
    "types",
    "formattedAddress",
    "location",
    "googleMapsUri",
    "displayName",
    "businessStatus",
    "primaryType",
    "primaryTypeDisplayName",
    "timeZone",
    "rating",
    "userRatingCount",
    "websiteUri",
    "internationalPhoneNumber",
    "nationalPhoneNumber",
    "priceLevel",
    "priceRange",
    "regularOpeningHours",
    "currentOpeningHours",
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
    "servesBreakfast",
    "servesBrunch",
    "servesLunch",
    "servesDinner",
    "servesDessert",
    "servesCoffee",
    "servesVegetarianFood",
    "menuForChildren",
    "dineIn",
    "takeout",
    "delivery",
    "allowsDogs",
    "restroom",
    "goodForWatchingSports",
    "parkingOptions",
    "paymentOptions",
    "curbsidePickup",
    "generativeSummary",
])

# Scalar columns stored on google_places (excludes PK provenance handled separately).
PLACE_SCALAR_COLUMNS = [
    "display_name",
    "formatted_address",
    "latitude",
    "longitude",
    "google_maps_uri",
    "primary_type",
    "primary_type_display_name",
    "business_status",
    "time_zone",
    "rating",
    "user_rating_count",
    "website_uri",
    "international_phone_number",
    "national_phone_number",
    "price_level",
    "price_range_start_cents",
    "price_range_end_cents",
    "price_range_currency",
    "editorial_summary",
    "generative_summary",
    "weekday_descriptions",
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
    "dine_in",
    "takeout",
    "delivery",
    "curbside_pickup",
    "live_music",
    "good_for_groups",
    "good_for_children",
    "good_for_watching_sports",
    "outdoor_seating",
    "reservable",
    "allows_dogs",
    "restroom",
    "parking_free_lot",
    "parking_paid_lot",
    "parking_free_street",
    "parking_paid_street",
    "parking_free_garage",
    "parking_paid_garage",
    "parking_valet",
    "accepts_credit_cards",
    "accepts_debit_cards",
    "accepts_cash_only",
    "accepts_nfc",
]


def load_schema_sql() -> str:
    return SCHEMA_PATH.read_text()


def infer_primary_type(types: list[str] | None) -> str | None:
    """Pick the most specific type when Place Details did not supply primaryType."""
    if not types:
        return None
    for type_name in types:
        if type_name not in GENERIC_TYPES:
            return type_name
    return types[0]


def _localized_text(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("text")
    if isinstance(value, str):
        return value
    return None


def _money_to_cents(money: dict | None) -> int | None:
    if not money:
        return None
    units = float(money.get("units", 0) or 0)
    nanos = float(money.get("nanos", 0) or 0)
    return int(units * 100 + nanos / 1e7)


def extract_details(raw: dict) -> dict:
    """Flatten a Place Details (New) response into storage columns."""
    details: dict[str, Any] = {}

    details["google_place_id"] = raw.get("id")
    details["display_name"] = _localized_text(raw.get("displayName"))
    details["formatted_address"] = raw.get("formattedAddress")
    details["google_maps_uri"] = raw.get("googleMapsUri")
    details["types"] = list(raw.get("types") or [])

    location = raw.get("location") or {}
    details["latitude"] = location.get("latitude")
    details["longitude"] = location.get("longitude")

    details["business_status"] = raw.get("businessStatus")
    details["primary_type"] = raw.get("primaryType")
    details["primary_type_display_name"] = _localized_text(raw.get("primaryTypeDisplayName"))

    time_zone = raw.get("timeZone") or {}
    details["time_zone"] = time_zone.get("id") if time_zone else None

    details["rating"] = raw.get("rating")
    details["user_rating_count"] = raw.get("userRatingCount")

    details["website_uri"] = raw.get("websiteUri")
    details["international_phone_number"] = raw.get("internationalPhoneNumber")
    details["national_phone_number"] = raw.get("nationalPhoneNumber")

    details["price_level"] = raw.get("priceLevel")
    price_range = raw.get("priceRange") or {}
    if price_range:
        start = price_range.get("startPrice") or {}
        end = price_range.get("endPrice") or {}
        details["price_range_start_cents"] = _money_to_cents(start) if start else None
        details["price_range_end_cents"] = _money_to_cents(end) if end else None
        details["price_range_currency"] = start.get("currencyCode") or end.get("currencyCode")
    else:
        details["price_range_start_cents"] = None
        details["price_range_end_cents"] = None
        details["price_range_currency"] = None

    hours = raw.get("regularOpeningHours") or {}
    details["open_now"] = (raw.get("currentOpeningHours") or {}).get("openNow")
    details["weekday_descriptions"] = hours.get("weekdayDescriptions") or []
    details["opening_hours_periods"] = hours.get("periods") or []

    details["editorial_summary"] = _localized_text(raw.get("editorialSummary"))
    generative = raw.get("generativeSummary") or {}
    details["generative_summary"] = _localized_text(generative.get("overview")) if generative else None

    details["live_music"] = raw.get("liveMusic")
    details["good_for_groups"] = raw.get("goodForGroups")
    details["good_for_children"] = raw.get("goodForChildren")
    details["good_for_watching_sports"] = raw.get("goodForWatchingSports")
    details["outdoor_seating"] = raw.get("outdoorSeating")
    details["reservable"] = raw.get("reservable")
    details["allows_dogs"] = raw.get("allowsDogs")
    details["restroom"] = raw.get("restroom")

    details["serves_breakfast"] = raw.get("servesBreakfast")
    details["serves_brunch"] = raw.get("servesBrunch")
    details["serves_lunch"] = raw.get("servesLunch")
    details["serves_dinner"] = raw.get("servesDinner")
    details["serves_dessert"] = raw.get("servesDessert")
    details["serves_coffee"] = raw.get("servesCoffee")
    details["serves_vegetarian_food"] = raw.get("servesVegetarianFood")
    details["serves_beer"] = raw.get("servesBeer")
    details["serves_wine"] = raw.get("servesWine")
    details["serves_cocktails"] = raw.get("servesCocktails")
    details["menu_for_children"] = raw.get("menuForChildren")

    details["dine_in"] = raw.get("dineIn")
    details["takeout"] = raw.get("takeout")
    details["delivery"] = raw.get("delivery")
    details["curbside_pickup"] = raw.get("curbsidePickup")

    parking = raw.get("parkingOptions") or {}
    details["parking_free_lot"] = parking.get("freeParkingLot")
    details["parking_paid_lot"] = parking.get("paidParkingLot")
    details["parking_free_street"] = parking.get("freeStreetParking")
    details["parking_paid_street"] = parking.get("paidStreetParking")
    details["parking_free_garage"] = parking.get("freeGarageParking")
    details["parking_paid_garage"] = parking.get("paidGarageParking")
    details["parking_valet"] = parking.get("valetParking")

    payment = raw.get("paymentOptions") or {}
    details["accepts_credit_cards"] = payment.get("acceptsCreditCards")
    details["accepts_debit_cards"] = payment.get("acceptsDebitCards")
    details["accepts_cash_only"] = payment.get("acceptsCashOnly")
    details["accepts_nfc"] = payment.get("acceptsNfc")

    details["reviews"] = []
    for review in raw.get("reviews") or []:
        text = review.get("text") or {}
        author = review.get("authorAttribution") or {}
        details["reviews"].append({
            "author_name": author.get("displayName"),
            "author_uri": author.get("uri"),
            "rating": review.get("rating"),
            "text": text.get("text") if isinstance(text, dict) else text,
            "language": text.get("languageCode") if isinstance(text, dict) else None,
            "relative_publish_time": review.get("relativePublishTimeDescription"),
            "publish_time": review.get("publishTime"),
            "google_maps_uri": review.get("googleMapsUri"),
        })

    return details


def _union_types(*type_lists: list[str] | None) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for type_list in type_lists:
        for type_name in type_list or []:
            if type_name not in seen:
                seen.add(type_name)
                ordered.append(type_name)
    return ordered


def place_record_from_listing(listing: dict) -> dict:
    """Build a google_places record from one source listing (and optional place_details)."""
    details = listing.get("place_details") or {}
    types = _union_types(details.get("types"), listing.get("types"))

    display_name = (
        details.get("display_name")
        or listing.get("google_name")
        or listing.get("canonical_name")
        or listing.get("name")
    )

    record: dict[str, Any] = {
        "google_place_id": listing.get("google_place_id") or details.get("google_place_id"),
        "display_name": display_name,
        "formatted_address": details.get("formatted_address") or listing.get("formatted_address"),
        "latitude": details.get("latitude") if details.get("latitude") is not None else listing.get("latitude"),
        "longitude": details.get("longitude") if details.get("longitude") is not None else listing.get("longitude"),
        "google_maps_uri": details.get("google_maps_uri") or listing.get("google_maps_uri"),
        "types": types,
        "primary_type": details.get("primary_type") or infer_primary_type(types),
        "primary_type_display_name": details.get("primary_type_display_name"),
        "listing_count": 1,
        "details_fetched": bool(listing.get("place_details")),
        "reviews": list(details.get("reviews") or []),
        "opening_hours_periods": list(details.get("opening_hours_periods") or []),
    }

    weekday = details.get("weekday_descriptions")
    if isinstance(weekday, str):
        record["weekday_descriptions"] = weekday
    elif weekday:
        record["weekday_descriptions"] = json.dumps(weekday)
    else:
        record["weekday_descriptions"] = None

    for column in PLACE_SCALAR_COLUMNS:
        if column in record:
            continue
        record[column] = details.get(column)

    return record


def merge_place_record(existing: dict, incoming: dict) -> None:
    """Collapse another listing onto an existing unique Place ID row."""
    existing["listing_count"] = existing.get("listing_count", 1) + incoming.get("listing_count", 1)
    existing["types"] = _union_types(existing.get("types"), incoming.get("types"))

    incoming_has_details = bool(incoming.get("details_fetched"))
    existing_has_details = bool(existing.get("details_fetched"))

    if incoming_has_details and not existing_has_details:
        skip = {"google_place_id", "listing_count", "types"}
        for key, value in incoming.items():
            if key not in skip:
                existing[key] = value
        existing["details_fetched"] = True
        if not existing.get("primary_type"):
            existing["primary_type"] = infer_primary_type(existing.get("types"))
        return

    for key, value in incoming.items():
        if key in {"google_place_id", "listing_count", "types"}:
            continue
        if existing.get(key) is None and value is not None:
            existing[key] = value

    if not existing.get("primary_type"):
        existing["primary_type"] = infer_primary_type(existing.get("types"))


def unique_google_places(listings: list[dict]) -> dict[str, dict]:
    """Return Place-ID -> google_places record for listings that have a Place ID."""
    places: dict[str, dict] = {}
    for listing in listings:
        place_id = listing.get("google_place_id")
        if not place_id:
            continue
        record = place_record_from_listing(listing)
        if place_id not in places:
            places[place_id] = record
        else:
            merge_place_record(places[place_id], record)
    return places
