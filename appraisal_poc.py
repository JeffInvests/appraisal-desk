import os
import json
import time
from google import genai
from google.genai import types

# Initialize the client (automatically pulls GEMINI_API_KEY from your environment variables)
client = genai.Client()

def run_intake_agent(raw_input_data):
    system_instruction = """
    You are an expert AI administrative assistant for an independent residential real estate appraiser. 
    Your job is to ingest raw intake data, normalize it, and output a strictly validated JSON structure.
    
    You must extract and determine:
    1. cleaned_address: The standardized property address.
    2. client_type: Categorized (e.g., Attorney, Executor, Homeowner, CPA, Lender, Other).
    3. intended_use: The purpose of the appraisal.
    4. effective_date_rule: Note whether this requires a "Current" or "Retrospective" effective date.
    5. special_notes: Any deadlines or unique property traits mentioned.
    """

    prompt = f"""
    Process the following raw input from the client/intake form:
    ---
    {json.dumps(raw_input_data, indent=2)}
    ---
    """

    # Retry loop to handle brief server capacity spikes gracefully
    max_retries = 3
    for attempt in range(max_retries):
        try:
            print(f"-> Sending request to gemini-3.8-flash (Attempt {attempt + 1})...", flush=True)
            response = client.models.generate_content(
                model='gemini-3.8-flash',
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
            )
            return json.loads(response.text)
        except Exception as e:
            print(f"-> Attempt {attempt + 1} failed: {e}", flush=True)
            if attempt < max_retries - 1:
                print("-> Waiting 2 seconds before retrying...", flush=True)
                time.sleep(2)
            else:
                return {"error": str(e)}

sample_web_form_submission = {
    "source": "Web Form Quote Request",
    "address": "2816 University Dr, Fort Worth, TX 76109",
    "purpose": "Estate / date of death / probate",
    "client_type": "Executor / administrator / trustee",
    "name": "Sarah Jenkins",
    "phone": "817-555-0198",
    "notes": "Need retrospective value as of last October. House has an unpermitted detached workshop."
}

if __name__ == "__main__":
    print("Running Intake & Triage Agent...", flush=True)
    structured_output = run_intake_agent(sample_web_form_submission)
    print("--- RESULT ---", flush=True)
    print(json.dumps(structured_output, indent=4))