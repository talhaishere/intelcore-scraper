"""
main.py
-------
The IntelCore scraper server. Runs on Render as a Web Service.

It exposes a small API the dashboard calls:
  POST /scrape   -> "Scrape Now": pull a controlled batch of contractors
  POST /outreach -> generate an AI outreach email for one contractor
  GET  /health   -> simple health check

CONTROLLED BY DESIGN:
  - You choose trade + state + how many (capped at 100/scrape)
  - AI enrichment is opt-in per scrape (controls OpenAI cost)
  - Every run is logged so you always know what was scraped

All secret keys come from ENVIRONMENT VARIABLES set in the Render dashboard,
never hard-coded. (You paste your fresh keys into Render's Environment tab.)
"""

import os
import json
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

from sam_client import SamClient
from enrichment import Enricher
from store import Store
from contact_lookup import ContactLookup

# ---------------------------------------------------------------------------
# Configuration from environment variables (set these in Render)
# ---------------------------------------------------------------------------
SAM_API_KEY = os.environ.get("SAM_API_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
GOOGLE_PLACES_KEY = os.environ.get("GOOGLE_PLACES_KEY", "")
# A simple shared secret so only YOUR dashboard can trigger scrapes
SCRAPE_SECRET = os.environ.get("SCRAPE_SECRET", "")
# Path to the Firebase service account JSON file (we write it from an env var)
FIREBASE_SA_PATH = "/tmp/firebase-service-account.json"


# Write the Firebase service account JSON from an env var to a temp file.
# (Render lets you store the whole JSON as an env var — cleaner than a file.)
def _setup_firebase_credentials():
    sa_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "")
    if sa_json:
        with open(FIREBASE_SA_PATH, "w") as f:
            f.write(sa_json)
        return FIREBASE_SA_PATH
    # Fallback: a local file if running on your own machine
    local = os.path.join(os.path.dirname(__file__), "firebase-service-account.json")
    if os.path.exists(local):
        return local
    raise RuntimeError("No Firebase credentials found. Set FIREBASE_SERVICE_ACCOUNT env var.")


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = FastAPI(title="IntelCore Scraper", version="1.0")

# Allow the dashboard (any origin for MVP — tighten later) to call us
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class ScrapeRequest(BaseModel):
    naics_code: Optional[str] = None   # e.g. "238210" electrical contractors
    state: Optional[str] = None        # e.g. "TX"
    how_many: int = 25                 # capped at 100 in sam_client
    enrich_with_ai: bool = False       # opt-in (costs OpenAI credits)
    find_contacts: bool = False        # opt-in (costs Google Places credits)


class OutreachRequest(BaseModel):
    contractor: dict


# ---------------------------------------------------------------------------
# Helper: check the shared secret
# ---------------------------------------------------------------------------
def _check_secret(provided: Optional[str]):
    if not SCRAPE_SECRET:
        return  # no secret configured (dev mode) — allow
    if provided != SCRAPE_SECRET:
        raise HTTPException(status_code=401, detail="Invalid scrape secret.")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {
        "status": "ok",
        "sam_key_set": bool(SAM_API_KEY),
        "openai_key_set": bool(OPENAI_API_KEY),
        "google_key_set": bool(GOOGLE_PLACES_KEY),
    }


@app.post("/scrape")
def scrape(req: ScrapeRequest, x_scrape_secret: Optional[str] = Header(default=None)):
    """
    The 'Scrape Now' action. Pulls a controlled batch of contractors from
    SAM.gov, optionally enriches with AI, and saves to Firestore.
    """
    _check_secret(x_scrape_secret)

    if not SAM_API_KEY:
        raise HTTPException(status_code=500, detail="SAM_API_KEY not configured on server.")

    # 1. Pull from SAM.gov (controlled, capped)
    sam = SamClient(SAM_API_KEY)
    try:
        contractors = sam.search_entities(
            naics_code=req.naics_code,
            state=req.state,
            how_many=req.how_many,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    # 2. Optionally enrich with AI (opt-in, costs credits)
    enriched_count = 0
    if req.enrich_with_ai:
        if not OPENAI_API_KEY:
            raise HTTPException(status_code=500, detail="OPENAI_API_KEY not configured.")
        enricher = Enricher(OPENAI_API_KEY)
        for c in contractors:
            ai_fields = enricher.enrich(c)
            c.update(ai_fields)
            if ai_fields.get("ai_enriched"):
                enriched_count += 1

    # 2b. Optionally find contact info via Google Places (opt-in, costs credits)
    contacts_found = 0
    if req.find_contacts:
        if not GOOGLE_PLACES_KEY:
            raise HTTPException(status_code=500, detail="GOOGLE_PLACES_KEY not configured.")
        looker = ContactLookup(GOOGLE_PLACES_KEY)
        for c in contractors:
            contact_fields = looker.find_contact(c)
            c.update(contact_fields)
            if contact_fields.get("contact_found"):
                contacts_found += 1

    # 3. Save to Firestore with duplicate detection
    store = Store(_setup_firebase_credentials())
    created, updated, skipped = 0, 0, 0
    for c in contractors:
        result = store.save_contractor(c)
        if result == "created":
            created += 1
        elif result == "updated":
            updated += 1
        else:
            skipped += 1

    # 4. Log the run so the dashboard can show exactly what happened
    summary = {
        "naics_code": req.naics_code or "any",
        "state": req.state or "any",
        "requested": req.how_many,
        "found": len(contractors),
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "ai_enriched": enriched_count,
        "contacts_found": contacts_found,
    }
    store.log_scrape_run(summary)

    return {"ok": True, "summary": summary}


@app.post("/outreach")
def outreach(req: OutreachRequest, x_scrape_secret: Optional[str] = Header(default=None)):
    """Generate a personalized AI recruitment email for one contractor."""
    _check_secret(x_scrape_secret)

    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not configured.")

    enricher = Enricher(OPENAI_API_KEY)
    email = enricher.generate_outreach_email(req.contractor)
    return {"ok": True, "email": email}
