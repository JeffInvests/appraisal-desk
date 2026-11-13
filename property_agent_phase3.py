import os
import json

def match_property_data(intake_payload):
    """
    Phase 3 MLS & Property Data Matcher.
    Takes incoming address and assignment details, structures the property profile,
    and sets up research parameters for comparable data retrieval.
    """
    address = intake_payload.get("address", "")
    intended_use = intake_payload.get("purpose", "")
    
    # Logic to handle retrospective effective date requirements (e.g., probate/estate)
    is_retrospective = "estate" in intended_use.lower() or "death" in intended_use.lower() or "probate" in intended_use.lower()
    
    # Structured Property Data Packet (simulating public records / MLS integration)
    property_profile = {
        "subject_property": {
            "address": address,
            "market_area": "Fort Worth / Tarrant County",
            "property_type": "Single-Family Residential",
            "estimated_ GLA_sqft": 2450, # Gross Living Area
            "site_size": "0.23 Acres",
            "measurement_standard": "ANSI Z765 Required",
            "special_features": ["Detached unpermitted workshop", "Covered patio"]
        },
        "assignment_parameters": {
            "intended_use": intended_use,
            "valuation_type": "Retrospective Date-of-Death" if is_retrospective else "Current Value",
            "market_research_scope": "Past 6 to 12 months from effective date",
            "estimated_baseline_fee": "$600" if is_retrospective else "$500"
        },
        "comparable_search_criteria": {
            "radius_miles": 1.0,
            "max_age_months": 12,
            "property_type_filter": "Single-Family Detached",
            "sqft_variance_pct": 20
        }
    }
    
    return property_profile

# Sample intake data payload from Phase 1 / Formspree
sample_intake = {
    "source": "Web Form Quote Request",
    "address": "2816 University Dr, Fort Worth, TX 76109",
    "purpose": "Estate / date of death / probate",
    "client_type": "Executor / administrator / trustee",
    "name": "Sarah Jenkins",
    "phone": "817-555-0198",
    "notes": "Need retrospective value as of last October. House has an unpermitted detached workshop."
}

if __name__ == "__main__":
    print("-> Appraisal Desk: Processing Phase 3 Property Data & MLS Matcher...", flush=True)
    
    result_profile = match_property_data(sample_intake)
    
    # Save the structured property profile locally
    filename = "property_research_profile.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(result_profile, f, indent=4)
        
    print(f"-> Success! Property profile structured and saved locally as: {filename}", flush=True)
    print("\n--- STRUCTURED PROPERTY PROFILE PREVIEW ---")
    print(json.dumps(result_profile, indent=4))
    