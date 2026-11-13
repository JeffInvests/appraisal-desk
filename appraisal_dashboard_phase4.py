import streamlit as st
import json
import os
import pandas as pd
from datetime import datetime, timedelta

# Configure page layout and professional header
st.set_page_config(page_title="Appraisal Desk Control Deck | Clearfork Appraisals", page_icon="🏠", layout="wide")

# Custom CSS Injection for Executive Styling & Clean UI Flow
st.markdown("""
    <style>
    .main {
        background-color: #f8f9fa;
    }
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
    }
    .stTabs [data-baseweb="tab"] {
        height: 50px;
        white-space: pre-wrap;
        background-color: #ffffff;
        border-radius: 6px 6px 0px 0px;
        font-weight: 600;
        color: #333333;
        border: 1px solid #e0e0e0;
    }
    .stTabs [aria-selected="true"] {
        background-color: #1f77b4 !important;
        color: #ffffff !important;
    }
    div.stButton > button:first-child {
        background-color: #1f77b4;
        color: white;
        font-weight: bold;
        border-radius: 6px;
        border: none;
        padding: 0.5rem 1rem;
    }
    div.stButton > button:first-child:hover {
        background-color: #15578a;
    }
    </style>
""", unsafe_allow_html=True)

# ----------------------------------------------------
# SIDEBAR: DESK PROFILE
# ----------------------------------------------------
st.sidebar.header("Desk Profile")
st.sidebar.text("Appraiser: Terrence Bilodeau")
st.sidebar.text("License: TX 1360232-CR")
st.sidebar.text("Coverage: Tarrant & Surrounding")
st.sidebar.markdown("---")
st.sidebar.caption("Written fee quote and firm delivery date are issued before every assignment is accepted.")

st.title("⚖️ Appraisal Desk Control Deck")
st.markdown("### *Clearfork Appraisals · Dual-License (Appraiser/Broker) Workflow Engine*")

# ----------------------------------------------------
# SESSION STATE INITIALIZATION
# ----------------------------------------------------
if "active_lead" not in st.session_state:
    st.session_state.active_lead = {
        "name": "Sarah Jenkins",
        "phone": "817-555-0198",
        "email": "sjenkins@example.com",
        "address": "2816 University Dr, Fort Worth, TX 76109",
        "purpose": "Estate / date of death / probate",
        "notes": "Retrospective date-of-death valuation. Check for unpermitted workshop."
    }

if "tad_records" not in st.session_state:
    st.session_state.tad_records = {
        "tad_gla": 2350,
        "ansi_gla": 2450,
        "lot_size": "0.23 Acres",
        "tax_year": 2025,
        "assessed_value": "$485,000",
        "source": "Tarrant Appraisal District (TAD) Public Records"
    }

if "fee_schedule" not in st.session_state:
    st.session_state.fee_schedule = {
        "Date of Death / Probate": 600,
        "Divorce or Marital Property Division": 600,
        "IRS Appraisals (Gift/Estate Tax)": 650,
        "Expert Witness & Litigation Support": 1000,
        "Immigration Asset Documentation": 550,
        "Pre-Listing Appraisal": 500,
        "Property Tax Protest (ARB)": 500,
        "PMI / MIP Cancellation": 500,
        "Measurement & Floor Plans (ANSI Z765)": 250,
        "Partial Release": 750,
        "Partial Interest / Partition Suit": 850,
        "Portfolio & Multi-Property": 1200,
        "Bail Bond & Surety Collateral": 600,
        "Trust Funding & Administration": 600,
        "Gift Tax (Form 709)": 650,
        "Bankruptcy Schedule A/B": 600,
        "Pre-Purchase for Buyers": 500
    }

PIPELINE_COLUMNS = [
    "Client Name", "Phone", "Email", "Property Address", "Assignment Type",
    "Status", "Target Deadline", "Quoted Fee ($)", "Notes"
]
PIPELINE_STATUSES = ["Requested", "Fee Quoted", "Active / In Progress", "Completed"]

if "pipeline_data" not in st.session_state:
    st.session_state.pipeline_data = pd.DataFrame([
        {
            "Client Name": "Sarah Jenkins",
            "Phone": "817-555-0198",
            "Email": "sjenkins@example.com",
            "Property Address": "1204 West Magnolia Ave, Fort Worth, TX",
            "Assignment Type": "Property Tax Protest (ARB)",
            "Status": "Fee Quoted",
            "Target Deadline": (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d"),
            "Quoted Fee ($)": st.session_state.fee_schedule["Property Tax Protest (ARB)"],
            "Notes": "Client requested notarized market value as of Jan 1."
        },
        {
            "Client Name": "Michael Chang",
            "Phone": "817-555-0142",
            "Email": "mchang@example.com",
            "Property Address": "4520 Bellaire Dr S, Fort Worth, TX",
            "Assignment Type": "Date of Death / Probate",
            "Status": "Active / In Progress",
            "Target Deadline": (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d"),
            "Quoted Fee ($)": st.session_state.fee_schedule["Date of Death / Probate"],
            "Notes": "Retrospective valuation for probate inventory."
        }
    ], columns=PIPELINE_COLUMNS)


def load_lead_into_workspace(name, phone, email, address, purpose, notes):
    """Push a lead into the active workspace AND the keyed Step 5 widgets,
    so the review form actually refreshes (keyed widgets ignore `value=` after first render)."""
    st.session_state.active_lead = {
        "name": name, "phone": phone, "email": email,
        "address": address, "purpose": purpose, "notes": notes
    }
    st.session_state["rev_name"] = name
    st.session_state["rev_phone"] = phone
    st.session_state["rev_email"] = email
    st.session_state["rev_addr"] = address
    st.session_state["rev_notes"] = notes
    if purpose in st.session_state.fee_schedule:
        st.session_state["active_intended_use"] = purpose


# ----------------------------------------------------
# OPERATIONAL SNAPSHOT (always visible above the tabs)
# ----------------------------------------------------
_pipe = st.session_state.pipeline_data
_open = _pipe[_pipe["Status"] != "Completed"]
m1, m2, m3, m4 = st.columns(4)
m1.metric("Open Pipeline Orders", len(_open))
m2.metric("Open Quoted Value", f"${int(_open['Quoted Fee ($)'].sum()):,}")
m3.metric("Avg. Response Target", "< 1 Hour")
m4.metric("License Status", "Active CR")
st.markdown("---")

# Navigation Tabs representing the professional appraisal workflow pipeline
tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
    "📍 1. Property Intake",
    "🏛️ 2. TAD & MLS Audit",
    "📁 3. Comp Importer",
    "💵 4. Fee Schedule",
    "📋 5. Adjustment & Export Grid",
    "🗂️ 6. Pipeline & Quote Tracker",
    "📚 7. Service Catalog"
])

with tab1:
    st.subheader("🏡 Step 1: Verified Fort Worth Property Database")
    st.markdown("Select from verified Fort Worth residential properties across established neighborhoods to initiate an assignment.")

    ftw_properties = [
        {
            "address": "2816 University Dr, Fort Worth, TX 76109",
            "neighborhood": "University / TCU",
            "tad_gla": 2350,
            "ansi_gla": 2450,
            "lot": "0.23 Acres",
            "notes": "Classic Fort Worth property; potential accessory structure considerations."
        },
        {
            "address": "2908 Tanglewood Trail, Fort Worth, TX 76109",
            "neighborhood": "Tanglewood",
            "tad_gla": 3050,
            "ansi_gla": 3120,
            "lot": "0.35 Acres",
            "notes": "Custom home build, established luxury submarket."
        },
        {
            "address": "2215 Irwin Ave, Fort Worth, TX 76110",
            "neighborhood": "Fairmount Historic District",
            "tad_gla": 1820,
            "ansi_gla": 1890,
            "lot": "0.18 Acres",
            "notes": "Historic overlay guidelines apply; ANSI Z765 measurement critical."
        }
    ]

    with st.container(border=True):
        selected_prop = st.selectbox(
            "Choose Target Property",
            ftw_properties,
            format_func=lambda x: f"{x['address']} ({x['neighborhood']})",
            key="target_prop_select"
        )

        col_x, col_y = st.columns(2)
        with col_x:
            mock_client_name = st.text_input("Simulated Client Name", value="Arthur Pendelton")
            mock_client_phone = st.text_input("Simulated Phone", value="817-555-4921")
        with col_y:
            mock_client_email = st.text_input("Simulated Email", value="apendelton@example.com")
            assigned_purpose = st.selectbox(
                "Intended Use / Service Type",
                list(st.session_state.fee_schedule.keys()),
                index=0,
                key="prop_intended_use"
            )

        if st.button("🚀 Load Property & Specs into Active Workspace"):
            load_lead_into_workspace(
                mock_client_name, mock_client_phone, mock_client_email,
                selected_prop["address"], assigned_purpose, selected_prop["notes"]
            )
            st.session_state.tad_records = {
                "tad_gla": selected_prop["tad_gla"],
                "ansi_gla": selected_prop["ansi_gla"],
                "lot_size": selected_prop["lot"],
                "tax_year": 2025,
                "assessed_value": "$510,000",
                "source": "Tarrant Appraisal District (TAD) Public Records"
            }
            st.success(f"Successfully loaded **{selected_prop['address']}** into active workflow!")

with tab2:
    st.subheader("🏛️ Step 2: TAD Public Records & MLS Source Audit")
    st.markdown("Verify official Tarrant Appraisal District (TAD) measurements against actual ANSI Z765 dimensions and broker data feeds.")

    col_t1, col_t2 = st.columns(2)
    with col_t1:
        with st.container(border=True):
            st.markdown("### 📋 County Tax Records")
            tad_gla_input = st.number_input("TAD Assessed GLA (SqFt)", value=st.session_state.tad_records["tad_gla"])
            ansi_gla_input = st.number_input("Actual ANSI Z765 Measured GLA (SqFt)", value=st.session_state.tad_records["ansi_gla"])
            lot_size_input = st.text_input("Site / Lot Size", value=st.session_state.tad_records["lot_size"])

            diff = ansi_gla_input - tad_gla_input
            if diff != 0:
                st.warning(f"⚠️ **ANSI vs. County Discrepancy:** Measured GLA differs from TAD records by **{diff:+d} SqFt**.")
            else:
                st.success("✅ Measured GLA matches TAD records.")

    with col_t2:
        with st.container(border=True):
            st.markdown("### 🌐 Market Comps Log")
            zillow_comp_notes = st.text_area(
                "Verified Sales Data Log",
                value="Comp 1: 2840 Tanglewood Trail | Sold $750,000 | Confirmed via NTREIS MLS on 09/14/2026\nComp 2: 3112 Park Ridge Dr | Sold $720,000 | Confirmed via NTREIS MLS on 08/02/2026",
                height=170
            )

    if st.button("💾 Save Audited Public Records Data"):
        st.session_state.tad_records = {
            "tad_gla": tad_gla_input,
            "ansi_gla": ansi_gla_input,
            "lot_size": lot_size_input,
            "tax_year": 2025,
            "assessed_value": "$510,000",
            "source": "Tarrant Appraisal District (TAD) Public Records"
        }
        st.success("Successfully updated TAD public records and MLS sourcing metadata!")

with tab3:
    st.subheader("📁 Step 3: Import Real MLS / NTREIS Comparable Sheets")
    st.markdown("Upload standard CSV exports from your direct broker MLS feed to populate comparative market data.")

    with st.container(border=True):
        uploaded_file = st.file_uploader("Upload MLS Comparable Sheet (.csv)", type=["csv"])

        if uploaded_file is not None:
            df_comps = pd.read_csv(uploaded_file)
            st.success(f"Successfully loaded {len(df_comps)} comparable records from MLS CSV!")
            st.dataframe(df_comps.head(5))
            st.session_state.parsed_comps = df_comps.to_dict(orient="records")
        else:
            st.info("ℹ️ Using default UAD 3.6 comparable template until an MLS CSV is uploaded.")
            st.session_state.parsed_comps = [
                {"Address": "2840 Tanglewood Trail", "Sale Price": 750000, "Distance": "0.2 mi", "GLA": "3,100 sqft", "Weight": "Most Weight"},
                {"Address": "3112 Park Ridge Dr", "Sale Price": 720000, "Distance": "0.4 mi", "GLA": "2,950 sqft", "Weight": "Less Weight"},
                {"Address": "4100 Bella Flora Dr", "Sale Price": 810000, "Distance": "1.8 mi", "GLA": "3,400 sqft", "Weight": "No Weight (Set Aside)"}
            ]

with tab4:
    st.subheader("💵 Step 4: Complete Service Fee Schedule")
    st.markdown("Customize baseline fees across all Clearfork Appraisals service types to drive automated pricing rules.")

    fees = st.session_state.fee_schedule
    new_fees = {}

    with st.container(border=True):
        col_f1, col_f2 = st.columns(2)
        items = list(fees.items())
        half = len(items) // 2

        with col_f1:
            for service_name, current_price in items[:half]:
                new_fees[service_name] = st.number_input(f"{service_name} ($)", value=int(current_price), step=25, key=f"fee_{service_name}")
        with col_f2:
            for service_name, current_price in items[half:]:
                new_fees[service_name] = st.number_input(f"{service_name} ($)", value=int(current_price), step=25, key=f"fee_{service_name}")

    if st.button("💾 Save Complete Fee Schedule"):
        st.session_state.fee_schedule = new_fees
        st.success("Full service fee schedule successfully updated!")

with tab5:
    st.subheader("📋 Step 5: Active File Review & UAD 3.6 Adjustment Engine")
    st.warning("⚠️ **NOTICE:** Items highlighted in the colored boxes below require **manual appraiser input/review** per UAD 3.6 standards.")

    current_lead = st.session_state.active_lead
    tad_data = st.session_state.tad_records
    fee_sched = st.session_state.fee_schedule

    col1, col2 = st.columns([1, 1])

    with col1:
        st.markdown("### Subject Property & Client Data")
        client_name = st.text_input("Client Name", value=current_lead["name"], key="rev_name")
        client_phone = st.text_input("Phone Number", value=current_lead["phone"], key="rev_phone")
        client_email = st.text_input("Email", value=current_lead["email"], key="rev_email")
        property_address = st.text_input("Property Address", value=current_lead["address"], key="rev_addr")
        intended_use = st.selectbox("Intended Use", list(fee_sched.keys()), index=list(fee_sched.keys()).index(current_lead["purpose"]) if current_lead["purpose"] in fee_sched else 0, key="active_intended_use")

        with st.container(border=True):
            st.error("✏️ **MANUAL ENTRY REQUIRED:** Property Notes & Unique Traits")
            notes = st.text_area("Property Notes", value=current_lead["notes"], key="rev_notes", help="Enter specific physical defects, unpermitted additions, or custom features here.")

    with col2:
        baseline_fee = f"${fee_sched.get(intended_use, 500)}"
        st.info(f"**Selected Assignment Type:** {intended_use}")
        st.success(f"**Quoted Baseline Fee:** {baseline_fee} [Fees & Turnaround](https://www.clearforkappraisals.com/#quote)")

        st.markdown("### UAD 3.6 Compliance & Standards")

        with st.container(border=True):
            st.error("✏️ **PROFESSIONAL RATING REQUIRED (C1–C6 / Q1–Q6):**")
            condition_rating = st.selectbox("Condition Rating", ["C1", "C2", "C3 - Well Maintained", "C4 - Minor Wear", "C5", "C6"], key="cond_rating")
            quality_rating = st.selectbox("Quality Rating", ["Q1", "Q2", "Q3 - Custom/Better", "Q4 - Standard", "Q5", "Q6"], key="qual_rating")

        time_adjustment_basis = st.selectbox("Time Adjustment Basis", ["Contract Date (UAD 3.6 Mandate)", "Legacy Closing Date"], key="time_adj")
        st.caption("🛡️ **Compliance Note:** Brokerage market data is kept separate from appraisal valuation opinions under strict [USPAP](https://www.talcb.texas.gov/) independence standards.")

    st.markdown("---")

    with st.container(border=True):
        st.error("✏️ **APPRAISER RECONCILIATION & ADJUSTMENT ENGINE (UAD 3.6):**")
        st.markdown("Enter dollar adjustments for each comparable sale below. The engine automatically calculates **Net Adjustments**, **Gross Adjustments**, and checks compliance against UAD tolerance thresholds.")

        comp_list = st.session_state.get("parsed_comps", [])
        comps_payload = []

        for i, comp in enumerate(comp_list[:3]):
            st.markdown(f"#### Comparable #{i+1}")
            c_addr, c_price, c_weight = st.columns([2, 1, 1])
            with c_addr:
                addr = st.text_input(f"Comp {i+1} Address", value=str(comp.get("Address", f"Comp {i+1}")), key=f"comp_addr_{i}")
            with c_price:
                sale_price = st.number_input(f"Sale Price ($)", value=int(comp.get("Sale Price", 750000)), step=5000, key=f"comp_price_{i}")
            with c_weight:
                weight = st.selectbox(f"UAD 3.6 Weight", ["Most Weight", "Less Weight", "No Weight (Set Aside)"], index=0 if i==0 else (1 if i==1 else 2), key=f"comp_wt_{i}")

            adj_col1, adj_col2, adj_col3 = st.columns(3)
            with adj_col1:
                time_adj = st.number_input(f"Time Adj ($)", value=0, step=1000, key=f"time_adj_{i}")
            with adj_col2:
                cond_adj = st.number_input(f"Condition/Quality Adj ($)", value=0, step=1000, key=f"cond_adj_{i}")
            with adj_col3:
                gla_adj = st.number_input(f"GLA / Site Adj ($)", value=0, step=1000, key=f"gla_adj_{i}")

            total_net_adj = time_adj + cond_adj + gla_adj
            total_gross_adj = abs(time_adj) + abs(cond_adj) + abs(gla_adj)

            net_pct = (total_net_adj / sale_price) * 100 if sale_price > 0 else 0
            gross_pct = (total_gross_adj / sale_price) * 100 if sale_price > 0 else 0
            indicated_value = sale_price + total_net_adj

            res_col1, res_col2, res_col3 = st.columns(3)
            with res_col1:
                st.metric("Indicated Value", f"${indicated_value:,.0f}")
            with res_col2:
                st.metric("Net Adjustment", f"${total_net_adj:+,.0f} ({net_pct:.1f}%)")
            with res_col3:
                st.metric("Gross Adjustment", f"${total_gross_adj:,.0f} ({gross_pct:.1f}%)")

            if gross_pct > 25.0:
                st.warning(f"⚠️ **UAD Guideline Alert:** Comp #{i+1} gross adjustments ({gross_pct:.1f}%) exceed standard 25% guideline. Verify comp selection or document explanation.")
            if abs(net_pct) > 15.0:
                st.warning(f"⚠️ **UAD Guideline Alert:** Comp #{i+1} net adjustments ({net_pct:.1f}%) exceed standard 15% guideline.")

            st.markdown("---")

            comps_payload.append({
                "address": addr,
                "sale_price": sale_price,
                "assigned_weight": weight,
                "adjustments": {
                    "time": time_adj,
                    "condition": cond_adj,
                    "gla_site": gla_adj
                },
                "calculated_metrics": {
                    "net_adjustment": total_net_adj,
                    "gross_adjustment": total_gross_adj,
                    "indicated_value": indicated_value
                }
            })

    st.markdown("### 📥 Interactive Export & Download Center")
    st.markdown("Generate your file assets and instantly download them via browser pop-up buttons below:")

    safe_name = client_name.replace(" ", "_")
    street_part = property_address.split(",")[0].replace(" ", "_")
    folder_name = f"Appraisal_Files_Active/{datetime.now().strftime('%Y-%m-%d')}_{safe_name}_{street_part}"
    safe_filename_name = client_name.lower().replace(" ", "_")

    vcard_data = f"""BEGIN:VCARD
VERSION:3.0
FN:{client_name}
TEL;TYPE=CELL:{client_phone}
EMAIL;TYPE=INTERNET:{client_email}
ADR;TYPE=WORK:;;{property_address};;;;
NOTE:Assignment: {intended_use} | Fee: {baseline_fee}
END:VCARD"""

    uad_package = {
        "appraiser_credentials": {
            "name": "Terrence Bilodeau",
            "designation": "Texas Certified Residential Real Estate Appraiser",
            "talcb_license_number": "1360232-CR",
            "broker_license_status": "Active (Maintained separately per 22 TAC §153.8)"
        },
        "subject_property": {
            "address": property_address,
            "client_name": client_name,
            "phone": client_phone,
            "intended_use": intended_use,
            "quoted_fee": baseline_fee,
            "condition_rating": condition_rating,
            "quality_rating": quality_rating,
            "notes": notes
        },
        "public_records_and_sourcing": tad_data,
        "uad_36_compliance": {
            "time_adjustment_anchor": time_adjustment_basis,
            "comparables": comps_payload
        }
    }
    json_data = json.dumps(uad_package, indent=4)

    markdown_report = f"""# Appraisal Assignment Audit Summary
**Clearfork Appraisals**  
*Terrence Bilodeau, Texas Certified Residential Appraiser (TALCB #1360232-CR)*  
*Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*

---

## 1. Client & Subject Property Overview
* **Client Name:** {client_name}
* **Phone:** {client_phone}
* **Email:** {client_email}
* **Subject Address:** {property_address}
* **Intended Use / Assignment:** {intended_use}
* **Quoted Fee:** {baseline_fee}

## 2. Property & Public Record Metrics
* **TAD Assessed GLA:** {tad_data.get('tad_gla')} SqFt
* **ANSI Z765 Measured GLA:** {tad_data.get('ansi_gla')} SqFt
* **Site / Lot Size:** {tad_data.get('lot_size')}
* **Data Provenance:** {tad_data.get('source')}

## 3. UAD 3.6 Compliance & Ratings
* **Condition Rating:** {condition_rating}
* **Quality Rating:** {quality_rating}
* **Time Adjustment Anchor:** {time_adjustment_basis}
* **Appraiser Notes:** {notes}

---

## 4. Comparable Sales & Adjustment Reconciliation
"""
    for i, comp in enumerate(comps_payload):
        markdown_report += f"""### Comparable #{i+1}: {comp['address']}
* **Sale Price:** ${comp['sale_price']:,}
* **Assigned Weight:** {comp['assigned_weight']}
* **Adjustments:**
  * Time: ${comp['adjustments']['time']:,}
  * Condition / Quality: ${comp['adjustments']['condition']:,}
  * GLA / Site: ${comp['adjustments']['gla_site']:,}
* **Calculated Metrics:**
  * Net Adjustment: ${comp['calculated_metrics']['net_adjustment']:,}
  * Gross Adjustment: ${comp['calculated_metrics']['gross_adjustment']:,}
  * **Indicated Value:** **${comp['calculated_metrics']['indicated_value']:,}**

---
"""

    markdown_report += """## 5. Professional Standards & Disclosures
All assignments are performed with impartiality, objectivity, and independence in compliance with the Uniform Standards of Professional Appraisal Practice (USPAP). Brokerage market data is maintained separately under 22 TAC §153.8.
"""

    if st.button("💾 Save Local Backup Folder"):
        os.makedirs(f"{folder_name}/photos_and_sketches", exist_ok=True)
        with open(f"{folder_name}/{safe_filename_name}_contact.vcf", "w", encoding="utf-8") as f:
            f.write(vcard_data)
        with open(f"{folder_name}/uad36_assignment_payload.json", "w", encoding="utf-8") as f:
            f.write(json_data)
        with open(f"{folder_name}/{safe_filename_name}_audit_summary.md", "w", encoding="utf-8") as f:
            f.write(markdown_report)
        st.success(f"Saved local backup folder at `{folder_name}/`!")

    dl_col1, dl_col2, dl_col3 = st.columns(3)
    with dl_col1:
        st.download_button(
            label="📄 Download Audit Report (.md)",
            data=markdown_report,
            file_name=f"{safe_filename_name}_audit_summary.md",
            mime="text/markdown"
        )
    with dl_col2:
        st.download_button(
            label="⚙️ Download UAD JSON Payload",
            data=json_data,
            file_name="uad36_assignment_payload.json",
            mime="application/json"
        )
    with dl_col3:
        st.download_button(
            label="📇 Download Contact vCard (.vcf)",
            data=vcard_data,
            file_name=f"{safe_filename_name}_contact.vcf",
            mime="text/vcard"
        )

# ----------------------------------------------------
# TAB 6: PIPELINE & QUOTE TRACKER
# ----------------------------------------------------
with tab6:
    st.subheader("🗂️ Inbound Requests & Pipeline Tracker")
    st.markdown("Track incoming fee quote requests, monitor turnaround deadlines, and move any file straight into the Step 5 review workspace.")

    view_filter = st.radio(
        "Show", ["Open files", "All files", "Completed only"],
        horizontal=True, key="pipeline_view"
    )

    pipe_df = st.session_state.pipeline_data
    if view_filter == "Open files":
        mask = pipe_df["Status"] != "Completed"
    elif view_filter == "Completed only":
        mask = pipe_df["Status"] == "Completed"
    else:
        mask = pd.Series(True, index=pipe_df.index)

    st.caption("Edit Status, Deadline, Fee or Notes directly in the table; changes save automatically.")
    edited_view = st.data_editor(
        pipe_df[mask],
        use_container_width=True,
        hide_index=False,
        disabled=["Client Name", "Phone", "Email", "Property Address", "Assignment Type"],
        column_config={
            "Status": st.column_config.SelectboxColumn("Status", options=PIPELINE_STATUSES, required=True),
            "Quoted Fee ($)": st.column_config.NumberColumn("Quoted Fee ($)", min_value=0, step=25, format="$%d"),
        },
        key="pipeline_editor"
    )
    # Write edits back to the full dataframe by index
    st.session_state.pipeline_data.loc[edited_view.index, edited_view.columns] = edited_view

    # Load a pipeline file into the Step 5 workspace
    st.markdown("### Send a File to the Active Workspace")
    if len(st.session_state.pipeline_data) > 0:
        load_idx = st.selectbox(
            "Pipeline file",
            st.session_state.pipeline_data.index,
            format_func=lambda i: f"{st.session_state.pipeline_data.loc[i, 'Client Name']} · {st.session_state.pipeline_data.loc[i, 'Property Address']}",
            key="pipeline_load_select"
        )
        if st.button("📋 Load Selected File into Step 5 Review"):
            row = st.session_state.pipeline_data.loc[load_idx]
            load_lead_into_workspace(
                row["Client Name"], row["Phone"], row["Email"],
                row["Property Address"], row["Assignment Type"], row["Notes"]
            )
            st.success(f"Loaded **{row['Client Name']}** into the active workspace. Open tab 5 to continue.")
    else:
        st.info("Pipeline is empty.")

    st.markdown("---")
    st.markdown("### Log New Inbound Request / Quote")

    with st.form("new_request_form", clear_on_submit=True):
        f_col1, f_col2 = st.columns(2)
        with f_col1:
            new_client = st.text_input("Client Name")
            new_phone = st.text_input("Phone")
            new_email = st.text_input("Email")
            new_address = st.text_input("Property Address (Street, City, ZIP)")
            new_type = st.selectbox("Assignment Type", list(st.session_state.fee_schedule.keys()))
        with f_col2:
            new_status = st.selectbox("Pipeline Status", PIPELINE_STATUSES)
            new_deadline = st.date_input("Target Delivery Deadline", datetime.now() + timedelta(days=5))
            fee_override = st.number_input(
                "Fee override ($) — leave at 0 to use the Fee Schedule (tab 4)",
                min_value=0, value=0, step=25
            )
        new_notes = st.text_area("Notes (deadlines, referral source, client context)")

        submitted = st.form_submit_button("Add to Pipeline")

    if submitted:
        if new_client and new_address:
            fee_value = fee_override if fee_override > 0 else st.session_state.fee_schedule.get(new_type, 500)
            new_row = {
                "Client Name": new_client,
                "Phone": new_phone,
                "Email": new_email,
                "Property Address": new_address,
                "Assignment Type": new_type,
                "Status": new_status,
                "Target Deadline": new_deadline.strftime("%Y-%m-%d"),
                "Quoted Fee ($)": int(fee_value),
                "Notes": new_notes
            }
            st.session_state.pipeline_data = pd.concat(
                [st.session_state.pipeline_data, pd.DataFrame([new_row])],
                ignore_index=True
            )
            st.rerun()
        else:
            st.error("Please fill out at least the Client Name and Property Address.")

    st.download_button(
        "📊 Export Pipeline (.csv)",
        data=st.session_state.pipeline_data.to_csv(index=False),
        file_name=f"pipeline_{datetime.now().strftime('%Y-%m-%d')}.csv",
        mime="text/csv"
    )

# ----------------------------------------------------
# TAB 7: SERVICE CATALOG
# ----------------------------------------------------
with tab7:
    st.subheader("📚 Clearfork Appraisals Service Catalog Reference")
    st.markdown("Quick reference matching the public service definitions. Fees shown come from your live Fee Schedule (tab 4).")

    services = {
        "Property Tax Protest (ARB)": "Notarized, independent opinion of market value as of January 1, built to meet Tax Code conditions.",
        "Date of Death / Probate": "Retrospective appraisal establishing what a Tarrant County home was worth on the date of passing for probate.",
        "Divorce or Marital Property Division": "Objective market valuation of marital real estate and defense of methodology.",
        "Pre-Listing Appraisal": "Independent opinion of market value plus an ANSI Z765-2021 floor plan.",
        "Bail Bond & Surety Collateral": "Rapid-turnaround valuation of property pledged to secure a bond or court requirement."
    }

    for svc, desc in services.items():
        fee = st.session_state.fee_schedule.get(svc)
        label = f"{svc} — ${fee}" if fee is not None else svc
        with st.expander(label):
            st.write(desc)

st.markdown("---")
st.markdown("*Appraisal Desk v1.1 · Dual-License Compliant Workflow Engine*")