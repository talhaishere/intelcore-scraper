"""
enrichment.py
-------------
This is the "AI brain" of IntelCore.

After we pull a raw contractor from SAM.gov, we ask OpenAI to INFER useful
operational intelligence that isn't directly in the data:
  - what trade specialties they likely have
  - a clean primary trade label
  - estimated workforce size band
  - which industrial sectors they probably serve
  - a short plain-English summary for recruiters

This is OPTIONAL per scrape (a checkbox in the dashboard) because it's the
part that costs OpenAI credits. We use gpt-4o-mini which is very cheap
(fractions of a cent per contractor).
"""

import json
from openai import OpenAI


# gpt-4o-mini = cheapest capable model. Good enough for this inference task.
MODEL = "gpt-4o-mini"


class Enricher:
    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("OpenAI API key is missing.")
        self.client = OpenAI(api_key=api_key)

    def enrich(self, contractor: dict) -> dict:
        """
        Takes a cleaned contractor dict and returns AI-inferred fields.
        Returns a dict that gets merged into the contractor profile.
        If anything fails, we return safe empty defaults (never crash a scrape).
        """
        naics_text = ", ".join(
            f"{n['code']} ({n['description']})" for n in contractor.get("naics", [])
        ) or "none listed"

        # Build a compact prompt with just what the model needs
        prompt = f"""You are an industrial workforce analyst. Based on this federal
contractor's public registration data, infer likely operational details.

Company: {contractor.get('legal_name', 'Unknown')}
DBA: {contractor.get('dba_name', '')}
Location: {contractor.get('city', '')}, {contractor.get('state', '')}
NAICS codes (industry classifications): {naics_text}

Respond with ONLY a JSON object (no markdown, no backticks) with these exact keys:
{{
  "primary_trade": "a short clean trade label, e.g. 'Industrial Electrical' or 'Mechanical / Piping' or 'General Construction'",
  "trade_specialties": ["up to 4 specific specialties"],
  "workforce_size_band": "one of: 'Small (1-25)', 'Medium (26-100)', 'Large (101-500)', 'Enterprise (500+)', 'Unknown'",
  "industrial_sectors": ["up to 3 sectors, e.g. 'Utilities', 'Data Centers', 'Water Treatment'"],
  "recruiter_summary": "2 short sentences a recruiter can read to understand who this contractor is and why they might be a good workforce provider"
}}

Base your inference on the NAICS codes and company name. If data is thin, make
reasonable inferences but keep workforce_size_band as 'Unknown' when truly unclear."""

        try:
            response = self.client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4,
                max_tokens=400,
            )
            text = response.choices[0].message.content.strip()

            # Strip accidental markdown fences just in case
            text = text.replace("```json", "").replace("```", "").strip()

            inferred = json.loads(text)

            return {
                "primary_trade": inferred.get("primary_trade", "Unknown"),
                "trade_specialties": inferred.get("trade_specialties", []),
                "workforce_size_band": inferred.get("workforce_size_band", "Unknown"),
                "industrial_sectors": inferred.get("industrial_sectors", []),
                "recruiter_summary": inferred.get("recruiter_summary", ""),
                "ai_enriched": True,
            }

        except Exception as e:
            # Never let enrichment crash a scrape. Return safe defaults.
            print(f"  [enrichment warning] {contractor.get('legal_name','?')}: {e}")
            return {
                "primary_trade": "Unknown",
                "trade_specialties": [],
                "workforce_size_band": "Unknown",
                "industrial_sectors": [],
                "recruiter_summary": "",
                "ai_enriched": False,
            }

    def generate_outreach_email(self, contractor: dict) -> str:
        """
        Generates a personalized recruitment outreach email for one contractor.
        Used by the dashboard's 'Generate Email' button (via the API).
        """
        trade = contractor.get("primary_trade", "industrial services")
        name = contractor.get("dba_name") or contractor.get("legal_name", "your company")
        city = contractor.get("city", "")
        state = contractor.get("state", "")
        location = f"{city}, {state}".strip(", ")

        prompt = f"""Write a short, professional recruitment outreach email to an
industrial contractor company. We are GigStacks — a platform that connects
verified industrial workforce providers with large projects that need crews.

Contractor: {name}
Trade: {trade}
Location: {location}

The email should:
- be warm but professional, 120 words max
- explain we'd like to learn about their crew availability and capabilities
- invite them to a quick call
- NOT make guarantees or promises about work volume
- end with a simple call to action

Respond with ONLY the email body (include a subject line at the top as 'Subject: ...')."""

        try:
            response = self.client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=300,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            return f"(Could not generate email: {e})"
