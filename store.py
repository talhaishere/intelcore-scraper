"""
store.py
--------
Handles saving contractors into Firestore.

Includes basic ENTITY RESOLUTION: before adding a contractor, we check if one
with the same UEI (SAM.gov's unique ID) already exists. If it does, we update
it instead of creating a duplicate. This is the simple version of the PRD's
"entity resolution" — using the deterministic UEI match, which is the most
reliable signal.

Firestore collection: 'contractors'
Each doc id = the UEI (guarantees no duplicates by design).
"""

import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime, timezone


class Store:
    def __init__(self, service_account_path: str):
        # Initialize the Firebase Admin SDK (only once)
        if not firebase_admin._apps:
            cred = credentials.Certificate(service_account_path)
            firebase_admin.initialize_app(cred)
        self.db = firestore.client()

    def save_contractor(self, contractor: dict) -> str:
        """
        Saves or updates one contractor.
        Returns: 'created', 'updated', or 'skipped'.
        Uses UEI as the document ID so the same company never duplicates.
        """
        uei = contractor.get("uei")

        # No UEI = we can't reliably dedupe. Fall back to a name+state key.
        if not uei:
            name = (contractor.get("legal_name") or "").strip().lower()
            state = (contractor.get("state") or "").strip().lower()
            if not name:
                return "skipped"  # nothing to identify it by
            doc_id = f"noUEI_{name}_{state}".replace(" ", "_")[:200]
        else:
            doc_id = uei

        doc_ref = self.db.collection("contractors").document(doc_id)
        existing = doc_ref.get()

        now = datetime.now(timezone.utc).isoformat()

        if existing.exists:
            # Update: refresh data + freshness, but DON'T overwrite the
            # recruitment status the team has been working.
            update_data = dict(contractor)
            update_data["last_seen"] = now
            update_data["freshness_updated"] = now
            # Preserve fields the team manages
            update_data.pop("status", None)
            update_data.pop("assigned_to", None)
            update_data.pop("notes", None)
            doc_ref.set(update_data, merge=True)
            return "updated"
        else:
            # Create: new contractor enters the pipeline as 'new'
            contractor["status"] = "new"           # pipeline stage
            contractor["assigned_to"] = ""          # recruiter
            contractor["notes"] = ""
            contractor["first_seen"] = now
            contractor["last_seen"] = now
            contractor["freshness_updated"] = now
            # verification state (from the PRD's verification levels)
            contractor["verification_state"] = "Detected"
            doc_ref.set(contractor)
            return "created"

    def log_scrape_run(self, summary: dict):
        """Records each scrape run so the dashboard can show history & control."""
        summary["timestamp"] = datetime.now(timezone.utc).isoformat()
        self.db.collection("scrape_runs").add(summary)
