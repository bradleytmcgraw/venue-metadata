#!/usr/bin/env python3
"""
Clean up venues_enriched.json:
1. Deduplicate entries that are truly the same venue
2. Flag co-located venues that share a Google Place ID but are distinct venues
3. Add Beachland Ballroom as a separate entry (was concatenated with Beat Kitchen)
4. Use Google's official name as the canonical name

Outputs: venues/venues_clean.json
"""

import json
from pathlib import Path

INPUT = Path("venues/venues_enriched.json")
OUTPUT = Path("venues/venues_clean.json")

# ---------------------------------------------------------------------------
# 1. Define true duplicates: keep the first, drop the second
#    key = original name to DROP
# ---------------------------------------------------------------------------
TRUE_DUPLICATES_TO_DROP = {
    "Sherman Showcase",       # duplicate of Sherman Theater
    "Big Top Chautauqua -",   # PDF artifact duplicate
    "Big Top Chautauqua:",    # PDF artifact duplicate (keep whichever comes first)
    "Slow Down",              # misspelling of Slowdown
}

# For Big Top, we want to keep exactly one. We'll drop the second occurrence.
# Handled in the dedup logic below.

# ---------------------------------------------------------------------------
# 2. Co-located venues: different venues sharing a Google Place ID
#    We keep both but flag them and note the relationship
# ---------------------------------------------------------------------------
CO_LOCATED = {
    # google_place_id -> list of venue names that are co-located there
    "ChIJxeko4uS3t4kRuCl8NRL9YTE": {
        "note": "9:30 Club and U Street Music Hall share a building in Washington, DC",
        "venues": ["9:30 Club", "U Street Music Hall"],
    },
    "ChIJyXLceFAE9YgRds6FmV9tr-s": {
        "note": "Center Stage and Vinyl share a building in Atlanta, GA",
        "venues": ["Center Stage", "Vinyl"],
    },
    "ChIJf_DBPMDQmoARlRbpC9yd4v4": {
        "note": "Harlow's and The Starlet Room share a building in Sacramento, CA",
        "venues": ["Harlow's", "The Starlet Room"],
    },
    "ChIJbxebWGUz4ocREPGfgUpG9BY": {
        "note": "The Speakeasy is inside Circa '21 Dinner Playhouse in Rock Island, IL",
        "venues": ["Circa '21 Dinner Playhouse", "The Speakeasy"],
    },
}

# ---------------------------------------------------------------------------
# 3. Concatenated entry fix: Beachland Ballroom was merged with Beat Kitchen
# ---------------------------------------------------------------------------
BEACHLAND_ENTRY = {
    "name": "Beachland Ballroom & Tavern",
    "city": "Cleveland",
    "state": "OH",
    "raw_line": "Beachland Ballroom & Tavern | Cleveland, OH",
    "pollstar_id": None,
    "google_place_id": None,  # will need to be enriched
    "google_name": None,
    "formatted_address": None,
    "latitude": None,
    "longitude": None,
    "google_maps_uri": None,
    "types": [],
    "_needs_enrichment": True,
    "_notes": "Split from concatenated entry 'Beachland Ballroom & Tavern Beat Kitchen'",
}


def main():
    with open(INPUT) as f:
        venues = json.load(f)

    print(f"Loaded {len(venues)} venues from {INPUT}")

    cleaned = []
    dropped = []
    seen_big_top = False

    for v in venues:
        original_name = v["name"]

        # --- Handle Big Top Chautauqua duplicates (keep first occurrence only) ---
        if original_name.startswith("Big Top Chautauqua"):
            if seen_big_top:
                dropped.append({"name": original_name, "reason": "duplicate (PDF artifact)"})
                continue
            seen_big_top = True

        # --- Drop true duplicates ---
        if original_name in TRUE_DUPLICATES_TO_DROP and not original_name.startswith("Big Top"):
            dropped.append({"name": original_name, "reason": "duplicate"})
            continue

        # --- Fix the concatenated Beachland/Beat Kitchen entry ---
        if original_name == "Beachland Ballroom & Tavern Beat Kitchen":
            # Fix this entry to be just Beat Kitchen
            v["_notes"] = "Originally concatenated as 'Beachland Ballroom & Tavern Beat Kitchen'; split into two entries"
            # The Google match was already Beat Kitchen in Chicago, so that's correct
            # Keep it as-is (Google matched it to Beat Kitchen)

        # --- Use Google's official name as canonical ---
        if v.get("google_name"):
            v["canonical_name"] = v["google_name"]
        else:
            v["canonical_name"] = original_name

        # --- Flag co-located venues ---
        place_id = v.get("google_place_id")
        if place_id in CO_LOCATED:
            co_info = CO_LOCATED[place_id]
            v["co_located"] = True
            v["co_located_note"] = co_info["note"]
            v["co_located_with"] = [
                name for name in co_info["venues"] if name != original_name
            ]
        else:
            v["co_located"] = False

        cleaned.append(v)

    # --- Add Beachland Ballroom as a new separate entry ---
    BEACHLAND_ENTRY["canonical_name"] = "Beachland Ballroom & Tavern"
    BEACHLAND_ENTRY["co_located"] = False
    cleaned.append(BEACHLAND_ENTRY)

    # --- Sort by state, city, canonical_name for cleanliness ---
    cleaned.sort(key=lambda v: (v["state"], v["city"], v["canonical_name"]))

    with open(OUTPUT, "w") as f:
        json.dump(cleaned, f, indent=2)

    print(f"\nResults:")
    print(f"  Cleaned venues: {len(cleaned)}")
    print(f"  Dropped duplicates: {len(dropped)}")
    for d in dropped:
        print(f"    - {d['name']} ({d['reason']})")
    print(f"  Co-located venues flagged: {sum(1 for v in cleaned if v.get('co_located'))}")
    print(f"  New entries added: 1 (Beachland Ballroom & Tavern)")
    print(f"  Needs enrichment: {sum(1 for v in cleaned if v.get('_needs_enrichment'))}")
    print(f"\nWrote {len(cleaned)} venues to {OUTPUT}")


if __name__ == "__main__":
    main()
