"""
contact_lookup.py
-----------------
Uses the Google Places API (New) to find real contact info for a contractor
that we discovered on SAM.gov.

SAM.gov gives us the company name + city/state but NO phone or website.
This module fills that gap — turning "a name" into "a lead you can call."

For each contractor we search "[company name] [city] [state]" and pull:
  - phone number
  - website
  - Google-verified formatted address
  - business status (is it still operational?)
  - Google rating (a light trust signal)

This is OPTIONAL per scrape (its own checkbox) because it costs Google credits
(though Google gives a large free monthly credit for Places).
"""

import requests

PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"


class ContactLookup:
    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("Google Places API key is missing.")
        self.api_key = api_key

    def find_contact(self, contractor: dict) -> dict:
        """
        Looks up contact info for one contractor.
        Returns a dict of extra fields to merge into the contractor profile.
        Never crashes a scrape — returns safe empty values on any failure.
        """
        name = contractor.get("legal_name") or contractor.get("dba_name") or ""
        city = contractor.get("city") or ""
        state = contractor.get("state") or ""

        if not name:
            return self._empty()

        query = f"{name} {city} {state}".strip()

        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.api_key,
            # Only request the fields we actually use (keeps cost low)
            "X-Goog-FieldMask": (
                "places.displayName,"
                "places.formattedAddress,"
                "places.nationalPhoneNumber,"
                "places.websiteUri,"
                "places.businessStatus,"
                "places.rating,"
                "places.userRatingCount"
            ),
        }
        body = {"textQuery": query, "maxResultCount": 1}

        try:
            resp = requests.post(PLACES_SEARCH_URL, headers=headers,
                                 json=body, timeout=30)
            if resp.status_code != 200:
                print(f"  [contact lookup warning] {name}: HTTP {resp.status_code}")
                return self._empty()

            places = resp.json().get("places", [])
            if not places:
                # No match found on Google — that's okay, not an error
                return self._empty(found=False)

            p = places[0]
            return {
                "phone": p.get("nationalPhoneNumber", ""),
                "website": p.get("websiteUri", ""),
                "google_address": p.get("formattedAddress", ""),
                "business_status": p.get("businessStatus", ""),
                "google_rating": p.get("rating", None),
                "google_rating_count": p.get("userRatingCount", 0),
                "contact_found": True,
            }

        except Exception as e:
            print(f"  [contact lookup warning] {name}: {e}")
            return self._empty()

    def _empty(self, found=False):
        return {
            "phone": "",
            "website": "",
            "google_address": "",
            "business_status": "",
            "google_rating": None,
            "google_rating_count": 0,
            "contact_found": found,
        }
