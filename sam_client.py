"""
sam_client.py
-------------
Talks to the official SAM.gov Entity Management API (v3).

We pull REGISTERED ENTITIES (contractor companies) — not job opportunities.
Each entity gives us: legal name, UEI, address, business types, NAICS codes
(what trade they do), and points of contact.

Free public API key limits: ~10 requests/day. Each request can return up to
100 entities. We respect that with a hard cap so we never burn the limit by
accident.
"""

import requests
import time

# Official SAM.gov Entity Management API (v3), public data
SAM_ENTITY_URL = "https://api.sam.gov/entity-information/v3/entities"

# Hard safety cap: never ask SAM for more than this many entities in one scrape.
# (Protects the free 10-requests/day limit.)
MAX_ENTITIES_PER_SCRAPE = 100


class SamClient:
    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("SAM.gov API key is missing.")
        self.api_key = api_key

    def search_entities(self, naics_code: str = None, state: str = None,
                        how_many: int = 25):
        """
        Search registered entities on SAM.gov.

        naics_code : optional NAICS code to filter by trade
                     (e.g. '238210' = electrical contractors)
        state      : optional 2-letter state code (e.g. 'TX')
        how_many   : how many entities to pull (we cap it for safety)

        Returns a list of cleaned-up contractor dictionaries.
        """
        # Safety: clamp how_many so we never blow the daily limit
        how_many = max(1, min(int(how_many), MAX_ENTITIES_PER_SCRAPE))

        params = {
            "api_key": self.api_key,
            "registrationStatus": "A",      # A = Active registrations only
            "includeSections": "entityRegistration,coreData,assertions,pointsOfContact",
            "page": 0,
            "size": how_many,
        }

        # Filter by trade (NAICS) if provided
        if naics_code:
            params["primaryNaics"] = naics_code

        # Filter by state if provided (physical address state)
        if state:
            params["physicalAddressProvinceOrStateCode"] = state.upper()

        try:
            response = requests.get(SAM_ENTITY_URL, params=params, timeout=40)
        except requests.RequestException as e:
            raise RuntimeError(f"Could not reach SAM.gov: {e}")

        # Handle the common error codes with clear messages
        if response.status_code == 401:
            raise RuntimeError("SAM.gov rejected the API key (401). Check the key.")
        if response.status_code == 403:
            raise RuntimeError("SAM.gov access denied (403). Key may lack permission.")
        if response.status_code == 429:
            raise RuntimeError("SAM.gov daily rate limit hit (429). Try again tomorrow.")
        if response.status_code >= 500:
            raise RuntimeError(f"SAM.gov server error ({response.status_code}). Try later.")
        if response.status_code != 200:
            raise RuntimeError(f"SAM.gov returned {response.status_code}: {response.text[:200]}")

        data = response.json()
        raw_entities = data.get("entityData", []) or []

        cleaned = []
        for raw in raw_entities:
            cleaned.append(self._clean_entity(raw))

        return cleaned

    def _clean_entity(self, raw: dict) -> dict:
        """
        SAM.gov's JSON is deeply nested and messy. This flattens one entity
        into a clean, simple dictionary our system can store easily.
        """
        registration = raw.get("entityRegistration", {}) or {}
        core = raw.get("coreData", {}) or {}

        # --- Physical address ---
        phys = core.get("physicalAddress", {}) or {}
        city = phys.get("city", "")
        state = phys.get("stateOrProvinceCode", "")
        zipcode = phys.get("zipCode", "")
        address_line = phys.get("addressLine1", "")

        # --- NAICS codes (what trades they do) ---
        naics_list = []
        assertions = raw.get("assertions", {}) or {}
        goods_services = assertions.get("goodsAndServices", {}) or {}
        for n in (goods_services.get("naicsList", []) or []):
            code = n.get("naicsCode", "")
            desc = n.get("naicsDescription", "")
            if code:
                naics_list.append({"code": code, "description": desc})

        # --- Points of contact ---
        contacts = []
        poc = raw.get("pointsOfContact", {}) or {}
        # SAM has several POC types; grab the most useful ones
        for poc_key in ["governmentBusinessPOC", "electronicBusinessPOC"]:
            person = poc.get(poc_key, {}) or {}
            if person:
                full_name = " ".join(filter(None, [
                    person.get("firstName", ""),
                    person.get("lastName", "")
                ])).strip()
                if full_name:
                    contacts.append({
                        "name": full_name,
                        "title": person.get("title", ""),
                        "type": poc_key,
                    })

        return {
            # Identity
            "uei": registration.get("ueiSAM", ""),
            "cage_code": registration.get("cageCode", ""),
            "legal_name": registration.get("legalBusinessName", ""),
            "dba_name": registration.get("dbaName", ""),

            # Registration
            "registration_status": registration.get("registrationStatus", ""),
            "registration_expiration": registration.get("registrationExpirationDate", ""),

            # Location
            "address": address_line,
            "city": city,
            "state": state,
            "zip": zipcode,

            # What they do
            "naics": naics_list,
            "entity_url": core.get("entityInformation", {}).get("entityURL", "")
                          if isinstance(core.get("entityInformation"), dict) else "",

            # Contacts
            "contacts": contacts,

            # Source tracking (important for the trust/lineage requirement)
            "source": "SAM.gov",
        }
