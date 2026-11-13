import os
import json

def generate_vcard_locally(client_data):
    """
    Directly formats a standard vCard (.vcf version 3.0) text block 
    without relying on external API calls, avoiding server traffic errors.
    """
    name = client_data.get("name", "Unknown Contact")
    # Simple name splitting for FN/N fields
    name_parts = name.split(" ", 1)
    first_name = name_parts[0]
    last_name = name_parts[1] if len(name_parts) > 1 else ""
    
    phone = client_data.get("phone", "")
    email = client_data.get("email", "")
    address = client_data.get("address", "")
    client_type = client_data.get("client_type", "")
    purpose = client_data.get("purpose", "")
    
    notes = f"Client Type: {client_type} | Intended Use: {purpose}"

    vcard_lines = [
        "BEGIN:VCARD",
        "VERSION:3.0",
        f"FN:{name}",
        f"N:{last_name};{first_name};;;",
        f"TEL;TYPE=CELL:{phone}",
        f"EMAIL;TYPE=INTERNET:{email}",
        f"ADR;TYPE=WORK:;;{address};;;;",
        f"NOTE:{notes}",
        "END:VCARD"
    ]
    
    return "\n".join(vcard_lines)

# Sample incoming lead payload (mimicking a Formspree submission from clearforkappraisals.com)
sample_lead = {
    "name": "Sarah Jenkins",
    "phone": "817-555-0198",
    "email": "sjenkins@example.com",
    "address": "2816 University Dr, Fort Worth, TX 76109",
    "client_type": "Executor / administrator / trustee",
    "purpose": "Estate / date of death / probate"
}

if __name__ == "__main__":
    print("-> Appraisal Desk: Processing Phase 2 Contact & vCard Automation Locally...", flush=True)
    
    vcard_output = generate_vcard_locally(sample_lead)
    
    # Save the output to a .vcf file
    filename = "sarah_jenkins_contact.vcf"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(vcard_output)
        
    print(f"-> Success! vCard generated instantly and saved locally as: {filename}", flush=True)
    print("\n--- GENERATED vCARD PREVIEW ---")
    print(vcard_output)