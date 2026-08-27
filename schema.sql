-- Google Places master data (QUA-14).
-- Unique venue entities keyed by Google Place ID.
-- Queryable as google_places, "Google Places", or "VenueMaster Metadata".

PRAGMA foreign_keys = ON;

-- Unique Google Places venue. One row per Place ID.
CREATE TABLE IF NOT EXISTS google_places (
    google_place_id TEXT PRIMARY KEY,

    -- Identity
    display_name TEXT NOT NULL,
    formatted_address TEXT,
    latitude REAL,
    longitude REAL,
    google_maps_uri TEXT,

    -- Venue / business type
    primary_type TEXT,                       -- e.g. live_music_venue, night_club, bar
    primary_type_display_name TEXT,          -- e.g. "Live Music Venue"

    -- Status
    business_status TEXT,                    -- OPERATIONAL, CLOSED_PERMANENTLY, CLOSED_TEMPORARILY
    time_zone TEXT,                          -- e.g. America/New_York

    -- Ratings
    rating REAL,                             -- 1.0 - 5.0
    user_rating_count INTEGER,

    -- Contact
    website_uri TEXT,
    international_phone_number TEXT,
    national_phone_number TEXT,

    -- Price
    price_level TEXT,                        -- e.g. PRICE_LEVEL_MODERATE
    price_range_start_cents INTEGER,
    price_range_end_cents INTEGER,
    price_range_currency TEXT,

    -- Summaries
    editorial_summary TEXT,
    generative_summary TEXT,

    -- Opening hours (human-readable JSON array of weekday descriptions)
    weekday_descriptions TEXT,

    -- Menu and food-service attributes (Places Atmosphere fields)
    serves_breakfast INTEGER,                -- BOOLEAN
    serves_brunch INTEGER,
    serves_lunch INTEGER,
    serves_dinner INTEGER,
    serves_dessert INTEGER,
    serves_coffee INTEGER,
    serves_vegetarian_food INTEGER,
    serves_beer INTEGER,
    serves_wine INTEGER,
    serves_cocktails INTEGER,
    menu_for_children INTEGER,

    -- Service options
    dine_in INTEGER,
    takeout INTEGER,
    delivery INTEGER,
    curbside_pickup INTEGER,

    -- Atmosphere
    live_music INTEGER,
    good_for_groups INTEGER,
    good_for_children INTEGER,
    good_for_watching_sports INTEGER,
    outdoor_seating INTEGER,
    reservable INTEGER,
    allows_dogs INTEGER,
    restroom INTEGER,

    -- Parking
    parking_free_lot INTEGER,
    parking_paid_lot INTEGER,
    parking_free_street INTEGER,
    parking_paid_street INTEGER,
    parking_free_garage INTEGER,
    parking_paid_garage INTEGER,
    parking_valet INTEGER,

    -- Payment
    accepts_credit_cards INTEGER,
    accepts_debit_cards INTEGER,
    accepts_cash_only INTEGER,
    accepts_nfc INTEGER,

    -- Provenance
    listing_count INTEGER NOT NULL DEFAULT 1,  -- source listings that share this Place ID
    details_fetched INTEGER NOT NULL DEFAULT 0 -- 1 if Place Details payload was applied
);

-- Acceptance-criteria names for the unique Places entity.
CREATE VIEW IF NOT EXISTS "Google Places" AS SELECT * FROM google_places;
CREATE VIEW IF NOT EXISTS "VenueMaster Metadata" AS SELECT * FROM google_places;

-- All Google place types for a venue (many-to-many).
CREATE TABLE IF NOT EXISTS google_place_types (
    google_place_id TEXT NOT NULL REFERENCES google_places(google_place_id),
    type TEXT NOT NULL,
    PRIMARY KEY (google_place_id, type)
);

-- Structured regular opening-hour periods.
CREATE TABLE IF NOT EXISTS google_place_hours (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    google_place_id TEXT NOT NULL REFERENCES google_places(google_place_id),
    open_day INTEGER,       -- 0=Sunday, 1=Monday, ..., 6=Saturday
    open_hour INTEGER,
    open_minute INTEGER,
    close_day INTEGER,
    close_hour INTEGER,
    close_minute INTEGER
);

-- Individual Google reviews for a place.
CREATE TABLE IF NOT EXISTS google_place_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    google_place_id TEXT NOT NULL REFERENCES google_places(google_place_id),
    author_name TEXT,
    author_uri TEXT,
    rating INTEGER,                          -- 1-5
    text TEXT,
    language TEXT,
    relative_publish_time TEXT,
    publish_time TEXT,
    google_maps_uri TEXT
);

-- Source listings (NIVA / Pollstar names) mapped onto Google Places.
-- Distinct rooms that share a building can share one Place ID.
CREATE TABLE IF NOT EXISTS venues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    google_place_id TEXT REFERENCES google_places(google_place_id),
    name TEXT NOT NULL,                      -- original listing name
    canonical_name TEXT,
    city TEXT NOT NULL,
    state TEXT NOT NULL,
    pollstar_id TEXT,
    co_located INTEGER DEFAULT 0,
    co_located_with TEXT,                    -- JSON array of co-located listing names
    co_located_note TEXT,
    raw_line TEXT
);

CREATE INDEX IF NOT EXISTS idx_google_places_primary_type
    ON google_places(primary_type);
CREATE INDEX IF NOT EXISTS idx_google_places_business_status
    ON google_places(business_status);
CREATE INDEX IF NOT EXISTS idx_google_places_rating
    ON google_places(rating);
CREATE INDEX IF NOT EXISTS idx_google_place_types_type
    ON google_place_types(type);
CREATE INDEX IF NOT EXISTS idx_google_place_hours_place_id
    ON google_place_hours(google_place_id);
CREATE INDEX IF NOT EXISTS idx_google_place_reviews_place_id
    ON google_place_reviews(google_place_id);
-- Official website, upcoming-shows page, and ticketing platform (QUA-19).
-- One row per unique Google Place ID; populated from venues/venue_web.json.
CREATE TABLE IF NOT EXISTS venue_web (
    google_place_id TEXT PRIMARY KEY REFERENCES google_places(google_place_id),
    website TEXT,
    upcoming_shows_url TEXT,
    ticketing_platform TEXT,
    ticketing_platforms TEXT,            -- JSON array of detected platforms
    fetched_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_venues_google_place_id
    ON venues(google_place_id);
CREATE INDEX IF NOT EXISTS idx_venues_city_state
    ON venues(city, state);
CREATE INDEX IF NOT EXISTS idx_venue_web_ticketing_platform
    ON venue_web(ticketing_platform);
