#!/bin/bash
curl -s -X POST \
  'https://places.googleapis.com/v1/places:searchText' \
  -H "Content-Type: application/json" \
  -H "X-Goog-Api-Key: $GOOGLE_PLACES_API_KEY" \
  -H "X-Goog-FieldMask: places.id,places.displayName,places.formattedAddress,places.location,places.googleMapsUri,places.types" \
  -d '{"textQuery": "Beachland Ballroom & Tavern Cleveland OH"}' | python3 -m json.tool > beachland_result.json

echo "Result saved to beachland_result.json"
cat beachland_result.json
