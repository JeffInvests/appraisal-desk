"""
Appraisal Desk Control Deck | Clearfork Appraisals
Internal back-office pipeline + workfile tool.

Run:   streamlit run appraisal_desk.py
Needs: streamlit >= 1.35, pandas
Data:  appraisal_desk.db (SQLite, created next to this file; set APPRAISAL_DB to relocate)
"""
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from urllib.parse import quote_plus

import pandas as pd
import streamlit as st

APP_VERSION = "v2.0"
DB_PATH = os.environ.get("APPRAISAL_DB", "appraisal_desk.db")
BACKUP_ROOT = "Appraisal_Files_Active"

APPRAISER = {
    "name": "Terrence Bilodeau",
    "designation": "Texas Certified Residential Real Estate Appraiser",
    "license": "1360232-CR",
}

STATUSES = ["Inquiry", "Fee Quoted", "Engaged", "In Progress", "Delivered", "Closed", "Lost"]
TERMINAL = ["Closed", "Lost"]
SOURCES = ["Phone", "Text / SMS", "Email", "Referral", "Website form", "Repeat client", "Other"]

DEFAULT_FEES = {
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
    "Pre-Purchase for Buyers": 500,
}

COUNTIES = ["Tarrant", "Parker", "Johnson", "Denton", "Dallas", "Wise", "Hood", "Ellis"]
CAD_LINKS = {
    "Tarrant": ("Tarrant Appraisal District (TAD)", "https://www.tad.org"),
    "Parker": ("Parker CAD", "https://www.parkercad.org"),
    "Johnson": ("Johnson CAD", "https://www.johnsoncad.com"),
    "Denton": ("Denton CAD", "https://www.dentoncad.com"),
    "Dallas": ("Dallas CAD", "https://www.dallascad.org"),
}
CITY_COUNTY = {
    # Tarrant
    "fort worth": "Tarrant", "arlington": "Tarrant", "southlake": "Tarrant", "keller": "Tarrant",
    "colleyville": "Tarrant", "grapevine": "Tarrant", "north richland hills": "Tarrant",
    "richland hills": "Tarrant", "hurst": "Tarrant", "euless": "Tarrant", "bedford": "Tarrant",
    "mansfield": "Tarrant", "burleson": "Tarrant", "haltom city": "Tarrant", "watauga": "Tarrant",
    "benbrook": "Tarrant", "saginaw": "Tarrant", "azle": "Tarrant", "crowley": "Tarrant",
    "haslet": "Tarrant", "white settlement": "Tarrant", "river oaks": "Tarrant", "lake worth": "Tarrant",
    "kennedale": "Tarrant", "forest hill": "Tarrant", "everman": "Tarrant", "westworth village": "Tarrant",
    "pantego": "Tarrant", "dalworthington gardens": "Tarrant", "blue mound": "Tarrant",
    # Parker
    "weatherford": "Parker", "aledo": "Parker", "willow park": "Parker", "hudson oaks": "Parker",
    "springtown": "Parker", "annetta": "Parker",
    # Johnson
    "cleburne": "Johnson", "joshua": "Johnson", "alvarado": "Johnson", "keene": "Johnson",
    # Denton
    "denton": "Denton", "lewisville": "Denton", "flower mound": "Denton", "roanoke": "Denton",
    "trophy club": "Denton", "justin": "Denton", "argyle": "Denton", "northlake": "Denton",
    "corinth": "Denton", "highland village": "Denton", "the colony": "Denton", "frisco": "Denton",
    # Dallas
    "dallas": "Dallas", "irving": "Dallas", "grand prairie": "Dallas", "garland": "Dallas",
    "richardson": "Dallas", "duncanville": "Dallas", "desoto": "Dallas", "mesquite": "Dallas",
    # Wise / Hood / Ellis
    "decatur": "Wise", "bridgeport": "Wise", "granbury": "Hood", "waxahachie": "Ellis",
}
CITIES_SORTED = sorted(CITY_COUNTY, key=len, reverse=True)

CONDITION_RATINGS = ["C1", "C2", "C3 - Well Maintained", "C4 - Minor Wear", "C5", "C6"]
QUALITY_RATINGS = ["Q1", "Q2", "Q3 - Custom/Better", "Q4 - Standard", "Q5", "Q6"]
TIME_BASES = ["Contract Date (UAD 3.6 Mandate)", "Legacy Closing Date"]
WEIGHTS = ["Most Weight", "Less Weight", "No Weight (Set Aside)"]

COMP_COLS = ["Address", "Sale Price", "Sale Date", "Distance", "GLA", "Weight",
             "Time Adj", "Cond/Qual Adj", "GLA/Site Adj", "Other Adj"]
ADJ_COLS = ["Time Adj", "Cond/Qual Adj", "GLA/Site Adj", "Other Adj"]
NUM_COLS = ["Sale Price", "GLA"] + ADJ_COLS

LEAD_COLS = {
    "created_at": "TEXT", "updated_at": "TEXT", "closed_at": "TEXT",
    "client_name": "TEXT", "phone": "TEXT", "email": "TEXT",
    "street": "TEXT", "city": "TEXT", "state": "TEXT", "zip": "TEXT", "county": "TEXT",
    "assignment_type": "TEXT", "status": "TEXT", "deadline": "TEXT", "fee": "INTEGER",
    "source": "TEXT", "notes": "TEXT", "raw_message": "TEXT",
    "tad_gla": "INTEGER", "ansi_gla": "INTEGER", "lot_size": "TEXT", "assessed_value": "TEXT",
    "tax_year": "INTEGER", "records_source": "TEXT",
    "condition_rating": "TEXT", "quality_rating": "TEXT", "time_basis": "TEXT",
    "market_log": "TEXT", "comps_json": "TEXT", "reconciliation_notes": "TEXT",
}


# ======================================================================
# DATA LAYER
# ======================================================================
def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_iso(s):
    try:
        return date.fromisoformat(str(s)[:10])
    except (ValueError, TypeError):
        return None


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


@st.cache_resource
def init_db():
    with db() as c:
        c.execute("CREATE TABLE IF NOT EXISTS leads (id INTEGER PRIMARY KEY AUTOINCREMENT)")
        existing = {r["name"] for r in c.execute("PRAGMA table_info(leads)")}
        for col, typ in LEAD_COLS.items():
            if col not in existing:
                c.execute(f"ALTER TABLE leads ADD COLUMN {col} {typ}")
        c.execute("""CREATE TABLE IF NOT EXISTS activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER, ts TEXT, message TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS fees (
            service TEXT PRIMARY KEY, fee INTEGER, sort INTEGER)""")
        if c.execute("SELECT COUNT(*) FROM fees").fetchone()[0] == 0:
            for i, (svc, fee) in enumerate(DEFAULT_FEES.items()):
                c.execute("INSERT INTO fees (service, fee, sort) VALUES (?,?,?)", (svc, fee, i))
    return True


def get_fees():
    with db() as c:
        rows = c.execute("SELECT service, fee FROM fees ORDER BY sort").fetchall()
    return {r["service"]: int(r["fee"]) for r in rows}


def save_fees(fees):
    with db() as c:
        c.execute("DELETE FROM fees")
        for i, (svc, fee) in enumerate(fees.items()):
            c.execute("INSERT INTO fees (service, fee, sort) VALUES (?,?,?)", (svc, int(fee), i))


def log_activity(lid, message):
    with db() as c:
        c.execute("INSERT INTO activity (lead_id, ts, message) VALUES (?,?,?)", (lid, now_str(), message))


def get_activity(lid, limit=15):
    with db() as c:
        return c.execute(
            "SELECT ts, message FROM activity WHERE lead_id=? ORDER BY id DESC LIMIT ?", (lid, limit)
        ).fetchall()


def create_lead(data):
    now = now_str()
    row = {"created_at": now, "updated_at": now, "state": "TX", "status": "Inquiry", "fee": 0, **data}
    row = {k: v for k, v in row.items() if k in LEAD_COLS}
    cols = ", ".join(row)
    ph = ", ".join("?" for _ in row)
    with db() as c:
        cur = c.execute(f"INSERT INTO leads ({cols}) VALUES ({ph})", list(row.values()))
        lid = cur.lastrowid
        c.execute("INSERT INTO activity (lead_id, ts, message) VALUES (?,?,?)",
                  (lid, now, f"Lead created ({row.get('source') or 'unknown source'})"))
    return lid


def update_lead(lid, log=None, **fields):
    fields = {k: v for k, v in fields.items() if k in LEAD_COLS}
    fields["updated_at"] = now_str()
    sets = ", ".join(f"{k}=?" for k in fields)
    with db() as c:
        c.execute(f"UPDATE leads SET {sets} WHERE id=?", [*fields.values(), lid])
        if log:
            c.execute("INSERT INTO activity (lead_id, ts, message) VALUES (?,?,?)", (lid, now_str(), log))


def get_lead(lid):
    with db() as c:
        r = c.execute("SELECT * FROM leads WHERE id=?", (lid,)).fetchone()
    return dict(r) if r else None


def list_leads():
    with db() as c:
        return pd.read_sql_query("SELECT * FROM leads", c)


def delete_lead(lid):
    with db() as c:
        c.execute("DELETE FROM leads WHERE id=?", (lid,))
        c.execute("DELETE FROM activity WHERE lead_id=?", (lid,))


def change_status(lid, new_status):
    lead = get_lead(lid)
    old = lead["status"]
    if old == new_status:
        return True, "Status unchanged."
    if new_status in ("Engaged", "In Progress"):
        if not lead.get("fee"):
            return False, "Set a quoted fee before engaging this file (Workfile → Overview)."
        if not parse_iso(lead.get("deadline")):
            return False, "Set a firm delivery date before engaging this file (Workfile → Overview)."
    fields = {"status": new_status}
    if new_status == "Closed":
        fields["closed_at"] = now_str()
    elif old == "Closed":
        fields["closed_at"] = None
    update_lead(lid, log=f"Status: {old} → {new_status}", **fields)
    return True, f"Status updated to {new_status}."


def find_open_duplicates(street):
    norm = re.sub(r"[^a-z0-9]", "", (street or "").lower())
    if not norm:
        return []
    df = list_leads()
    if df.empty:
        return []
    df = df[~df["status"].isin(TERMINAL)]
    df = df[df["street"].fillna("").apply(lambda s: re.sub(r"[^a-z0-9]", "", s.lower())) == norm]
    return df.to_dict("records")


def full_address(lead):
    parts = [lead.get("street") or ""]
    if lead.get("city"):
        parts.append(lead["city"])
    tail = " ".join(x for x in [lead.get("state") or "TX", lead.get("zip") or ""] if x)
    parts.append(tail)
    return ", ".join(p for p in parts if p)


def seed_demo():
    fees = get_fees()
    today = date.today()

    def fee(svc):
        return fees.get(svc, 500)

    demo = [
        ("Sarah Jenkins", "817-555-0198", "sjenkins@example.com", "1204 West Magnolia Ave", "Fort Worth", "76104",
         "Property Tax Protest (ARB)", "Fee Quoted", 5, "Phone", "Client requested notarized market value as of Jan 1."),
        ("Michael Chang", "817-555-0142", "mchang@example.com", "4520 Bellaire Dr S", "Fort Worth", "76109",
         "Date of Death / Probate", "In Progress", 3, "Referral", "Retrospective valuation for probate inventory."),
        ("Dana Whitfield", "682-555-0177", "dwhitfield@example.com", "310 Oak Hollow Ct", "Southlake", "76092",
         "Divorce or Marital Property Division", "Inquiry", 8, "Website form", "Attorney referral; wants call back."),
        ("Luis Ortega", "972-555-0123", "lortega@example.com", "88 Ranch House Rd", "Weatherford", "76087",
         "Pre-Listing Appraisal", "Engaged", -1, "Text / SMS", "Inspection access Thursday."),
        ("Priya Patel", "214-555-0190", "ppatel@example.com", "2204 Meadow Ln", "Arlington", "76013",
         "PMI / MIP Cancellation", "Closed", -10, "Repeat client", "Delivered and paid."),
    ]
    for name, ph, em, st_, city, z, svc, status, days, src, notes in demo:
        lid = create_lead({
            "client_name": name, "phone": ph, "email": em, "street": st_, "city": city, "zip": z,
            "county": CITY_COUNTY.get(city.lower(), ""), "assignment_type": svc, "status": status,
            "deadline": (today + timedelta(days=days)).isoformat(), "fee": fee(svc), "source": src,
            "notes": notes,
        })
        if status == "Closed":
            update_lead(lid, closed_at=now_str())


# ======================================================================
# MESSAGE PARSER (text / email -> lead draft)
# ======================================================================
STREET_SUFFIXES = (r"(?:Street|St|Avenue|Ave|Drive|Dr|Road|Rd|Boulevard|Blvd|Lane|Ln|Court|Ct|Trail|Trl|Way|"
                   r"Place|Pl|Circle|Cir|Parkway|Pkwy|Terrace|Ter|Highway|Hwy|Loop|Run|Pass|Path|Crossing|Xing)")
ADDRESS_RE = re.compile(
    rf"\b\d{{1,6}}\s+(?:[NSEW]\.?\s+)?(?:[A-Za-z0-9'\-]+\.?\s+){{0,4}}?{STREET_SUFFIXES}\b\.?"
    rf"(?:\s*,?\s*(?:Apt|Unit|Ste|Suite|#)\s*[\w-]+)?", re.I)
PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[\s.\-]?)?\(?(\d{3})\)?[\s.\-]?(\d{3})[\s.\-]?(\d{4})(?!\d)")
EMAIL_RE = re.compile(r"[\w.+\-]+@[\w\-]+\.[\w.\-]+")
ZIP_RE = re.compile(r"\b(7[5-6]\d{3})\b")
NAME_RE = re.compile(r"(?i:\b(?:this is|my name is|i am|i'm|im|name's))\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)")
SIGN_RE = re.compile(r"(?:^|\s)[-–—~]\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s*$")

WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}
MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}

SERVICE_KEYWORDS = [
    ("Gift Tax (Form 709)", r"gift tax|709"),
    ("IRS Appraisals (Gift/Estate Tax)", r"\birs\b|estate tax"),
    ("Date of Death / Probate", r"probate|estate|date of death|passed away|deceased|inherit|late (mother|father|mom|dad)"),
    ("Divorce or Marital Property Division", r"divorce|marital|separation"),
    ("Property Tax Protest (ARB)", r"tax protest|protest|\barb\b|appraisal district|over.?assess|notice of appraised|\btad\b"),
    ("Pre-Listing Appraisal", r"pre-?listing|before (i|we) list|about to list|fsbo"),
    ("PMI / MIP Cancellation", r"\bpmi\b|\bmip\b|mortgage insurance"),
    ("Bail Bond & Surety Collateral", r"\bbail\b|\bbond\b|surety"),
    ("Trust Funding & Administration", r"\btrust\b"),
    ("Immigration Asset Documentation", r"immigration|uscis"),
    ("Bankruptcy Schedule A/B", r"bankruptcy|chapter (7|13)"),
    ("Expert Witness & Litigation Support", r"expert witness|litigation|testify|deposition"),
    ("Partial Interest / Partition Suit", r"partition|partial interest"),
    ("Partial Release", r"partial release"),
    ("Portfolio & Multi-Property", r"portfolio|multiple properties|rental properties"),
    ("Measurement & Floor Plans (ANSI Z765)", r"floor ?plan|ansi|measure"),
    ("Pre-Purchase for Buyers", r"pre-?purchase|buying|buyer"),
]


def parse_deadline(text, today=None):
    """Return a date found in the message, or None."""
    today = today or date.today()
    t = text.lower()

    def mk(month, day, year=None):
        try:
            y = year if year else today.year
            if y < 100:
                y += 2000
            d = date(y, month, day)
            if not year and d < today:
                d = date(y + 1, month, day)
            return d
        except ValueError:
            return None

    m = re.search(r"\b(?:by|before|due|deadline|on)\s+(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", t)
    if m:
        d = mk(int(m.group(1)), int(m.group(2)), int(m.group(3)) if m.group(3) else None)
        if d:
            return d
    m = re.search(r"\b(?:by|before|due|on)?\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b", t)
    if m:
        d = mk(MONTHS[m.group(1)], int(m.group(2)))
        if d:
            return d
    m = re.search(r"\b(next|this|by|before|on)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", t)
    if m:
        wd = WEEKDAYS[m.group(2)]
        if m.group(1) == "next":
            next_monday = today + timedelta(days=7 - today.weekday())
            return next_monday + timedelta(days=wd)
        ahead = (wd - today.weekday()) % 7 or 7
        return today + timedelta(days=ahead)
    if "tomorrow" in t:
        return today + timedelta(days=1)
    if re.search(r"\b(asap|rush|urgent|immediately)\b", t):
        return today + timedelta(days=2)
    if "next week" in t:
        return today + timedelta(days=7)
    if re.search(r"end of (the )?month", t):
        nxt = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
        return nxt - timedelta(days=1)
    return None


def parse_message(text, fees):
    """Extract a lead draft from a pasted text / email. Everything is editable before saving."""
    found = {}
    draft = {"raw_message": text}

    m = EMAIL_RE.search(text)
    if m:
        draft["email"] = m.group(0)
        found["email"] = True
    m = PHONE_RE.search(text)
    if m:
        draft["phone"] = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        found["phone"] = True

    addr_end = None
    m = ADDRESS_RE.search(text)
    if m:
        draft["street"] = re.sub(r"\s+", " ", m.group(0)).strip(" ,.")
        addr_end = m.end()
        found["address"] = True

    cands = []
    for name in CITIES_SORTED:
        cm = re.search(rf"\b{re.escape(name)}\b", text, re.I)
        if cm:
            cands.append((cm.start(), name))
    if cands:
        after = [c for c in cands if addr_end is None or c[0] >= addr_end]
        pos, name = min(after) if after else min(cands)
        draft["city"] = name.title()
        draft["county"] = CITY_COUNTY[name]
        found["city"] = True
    m = ZIP_RE.search(text)
    if m:
        draft["zip"] = m.group(1)

    m = NAME_RE.search(text) or SIGN_RE.search(text)
    if m and m.group(1).split()[0].lower() != "terrence":
        draft["client_name"] = m.group(1)
        found["name"] = True

    for svc, pat in SERVICE_KEYWORDS:
        if svc in fees and re.search(pat, text, re.I):
            draft["assignment_type"] = svc
            found["service"] = True
            break

    dl = parse_deadline(text)
    if dl:
        draft["deadline"] = dl
        found["deadline"] = True

    draft["_found"] = found
    return draft


# ======================================================================
# COMPS / ADJUSTMENT MATH
# ======================================================================
def to_num(x):
    try:
        s = re.sub(r"[^\d.\-]", "", str(x))
        return float(s) if s not in ("", "-", ".") else 0.0
    except ValueError:
        return 0.0


def empty_comps():
    return pd.DataFrame([{c: ("" if c in ("Address", "Sale Date", "Distance") else 0.0) for c in COMP_COLS}
                         | {"Weight": WEIGHTS[0]} for _ in range(3)], columns=COMP_COLS)


def load_comps(lead):
    try:
        recs = json.loads(lead.get("comps_json") or "[]")
    except json.JSONDecodeError:
        recs = []
    if not recs:
        return empty_comps()
    df = pd.DataFrame(recs)
    for c in COMP_COLS:
        if c not in df:
            df[c] = 0.0 if c in NUM_COLS else ""
    return df[COMP_COLS]


def map_comp_csv(raw):
    syn = {
        "Address": ["address", "street address", "property address", "full address"],
        "Sale Price": ["sale price", "sold price", "close price", "closed price", "sp", "price"],
        "Sale Date": ["sale date", "close date", "closing date", "contract date", "settled date"],
        "Distance": ["distance", "dist", "proximity"],
        "GLA": ["gla", "sqft", "sq ft", "living area", "square feet", "total sqft", "building sqft", "sf"],
    }
    lookup = {str(c).strip().lower(): c for c in raw.columns}
    out = pd.DataFrame(index=raw.index)
    for target, names in syn.items():
        src = next((lookup[n] for n in names if n in lookup), None)
        out[target] = raw[src] if src is not None else ("" if target in ("Address", "Sale Date", "Distance") else 0)
    for c in ("Sale Price", "GLA"):
        out[c] = out[c].apply(to_num)
    for c in ("Address", "Sale Date", "Distance"):
        out[c] = out[c].astype(str).replace("nan", "")
    out["Weight"] = WEIGHTS[0]
    for c in ADJ_COLS:
        out[c] = 0.0
    return out[COMP_COLS]


def compute_comps(df):
    d = df.copy()
    for c in NUM_COLS:
        d[c] = pd.to_numeric(d[c], errors="coerce").fillna(0.0)
    d["Address"] = d["Address"].fillna("").astype(str)
    d["Weight"] = d["Weight"].fillna(WEIGHTS[0])
    d = d[(d["Address"].str.strip() != "") | (d["Sale Price"] > 0)].copy()
    d["Net Adj"] = d[ADJ_COLS].sum(axis=1)
    d["Gross Adj"] = d[ADJ_COLS].abs().sum(axis=1)
    price = d["Sale Price"].where(d["Sale Price"] > 0)
    d["Net %"] = (d["Net Adj"] / price * 100).fillna(0.0)
    d["Gross %"] = (d["Gross Adj"] / price * 100).fillna(0.0)
    d["Indicated Value"] = d["Sale Price"] + d["Net Adj"]

    def flags(r):
        out = []
        if r["Gross %"] > 25:
            out.append("Gross > 25%")
        if abs(r["Net %"]) > 15:
            out.append("Net > 15%")
        return ", ".join(out)

    d["Flags"] = d.apply(flags, axis=1) if len(d) else []
    return d.reset_index(drop=True)


# ======================================================================
# REPORT BUILDERS
# ======================================================================
def slug(s):
    return re.sub(r"[^A-Za-z0-9_\-]+", "_", s or "").strip("_") or "file"


def build_vcard(lead):
    return (f"BEGIN:VCARD\nVERSION:3.0\nFN:{lead.get('client_name') or ''}\n"
            f"TEL;TYPE=CELL:{lead.get('phone') or ''}\nEMAIL;TYPE=INTERNET:{lead.get('email') or ''}\n"
            f"ADR;TYPE=WORK:;;{full_address(lead)};;;;\n"
            f"NOTE:Assignment: {lead.get('assignment_type')} | Fee: ${lead.get('fee') or 0}\nEND:VCARD")


def build_json(lead, comps):
    pkg = {
        "appraiser_credentials": {
            "name": APPRAISER["name"], "designation": APPRAISER["designation"],
            "talcb_license_number": APPRAISER["license"],
        },
        "subject_property": {
            "address": full_address(lead), "county": lead.get("county"),
            "client_name": lead.get("client_name"), "phone": lead.get("phone"),
            "intended_use": lead.get("assignment_type"), "quoted_fee": lead.get("fee"),
            "delivery_deadline": lead.get("deadline"),
            "condition_rating": lead.get("condition_rating"), "quality_rating": lead.get("quality_rating"),
            "notes": lead.get("notes"),
        },
        "public_records_and_sourcing": {
            "tad_gla": lead.get("tad_gla"), "ansi_gla": lead.get("ansi_gla"),
            "lot_size": lead.get("lot_size"), "assessed_value": lead.get("assessed_value"),
            "tax_year": lead.get("tax_year"), "source": lead.get("records_source"),
        },
        "uad_36_compliance": {
            "time_adjustment_anchor": lead.get("time_basis"),
            "comparables": [
                {
                    "address": r["Address"], "sale_price": r["Sale Price"], "sale_date": r["Sale Date"],
                    "assigned_weight": r["Weight"],
                    "adjustments": {"time": r["Time Adj"], "condition_quality": r["Cond/Qual Adj"],
                                    "gla_site": r["GLA/Site Adj"], "other": r["Other Adj"]},
                    "calculated_metrics": {"net_adjustment": r["Net Adj"], "gross_adjustment": r["Gross Adj"],
                                           "indicated_value": r["Indicated Value"]},
                }
                for _, r in comps.iterrows()
            ],
        },
    }
    return json.dumps(pkg, indent=4, default=float)


def build_markdown(lead, comps):
    md = f"""# Appraisal Assignment Audit Summary
**Clearfork Appraisals**  
*{APPRAISER['name']}, {APPRAISER['designation']} (TALCB #{APPRAISER['license']})*  
*Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*

---

## 1. Client & Subject Property Overview
* **Client Name:** {lead.get('client_name')}
* **Phone:** {lead.get('phone') or '—'}
* **Email:** {lead.get('email') or '—'}
* **Subject Address:** {full_address(lead)}
* **County:** {lead.get('county') or '—'}
* **Intended Use / Assignment:** {lead.get('assignment_type')}
* **Quoted Fee:** ${lead.get('fee') or 0}
* **Delivery Deadline:** {lead.get('deadline') or '—'}

## 2. Property & Public Record Metrics
* **County Assessed GLA:** {lead.get('tad_gla') or 0} SqFt
* **ANSI Z765 Measured GLA:** {lead.get('ansi_gla') or 0} SqFt
* **Site / Lot Size:** {lead.get('lot_size') or '—'}
* **Assessed Value ({lead.get('tax_year') or '—'}):** {lead.get('assessed_value') or '—'}
* **Data Provenance:** {lead.get('records_source') or '—'}

## 3. UAD 3.6 Compliance & Ratings
* **Condition Rating:** {lead.get('condition_rating') or '—'}
* **Quality Rating:** {lead.get('quality_rating') or '—'}
* **Time Adjustment Anchor:** {lead.get('time_basis') or '—'}
* **Appraiser Notes:** {lead.get('notes') or '—'}

---

## 4. Comparable Sales & Adjustment Reconciliation
"""
    if comps.empty:
        md += "_No comparables entered._\n"
    for i, r in comps.iterrows():
        md += f"""### Comparable #{i + 1}: {r['Address']}
* **Sale Price:** ${r['Sale Price']:,.0f}  ({r['Sale Date'] or 'date n/a'})
* **Assigned Weight:** {r['Weight']}
* **Adjustments:** Time ${r['Time Adj']:,.0f} · Condition/Quality ${r['Cond/Qual Adj']:,.0f} · GLA/Site ${r['GLA/Site Adj']:,.0f} · Other ${r['Other Adj']:,.0f}
* **Net Adjustment:** ${r['Net Adj']:,.0f} ({r['Net %']:.1f}%)
* **Gross Adjustment:** ${r['Gross Adj']:,.0f} ({r['Gross %']:.1f}%)
* **Indicated Value:** **${r['Indicated Value']:,.0f}**{('  ⚠ ' + r['Flags']) if r['Flags'] else ''}

"""
    if lead.get("reconciliation_notes"):
        md += f"---\n\n## 5. Reconciliation Notes\n{lead['reconciliation_notes']}\n"
    return md


# ======================================================================
# ==== UI ====
# ======================================================================
PAGE_PIPELINE = "📊 Pipeline"
PAGE_NEW = "➕ New Lead"
PAGE_WORKFILE = "📋 Workfile"
PAGE_FEES = "💵 Fee Schedule"
PAGES = [PAGE_PIPELINE, PAGE_NEW, PAGE_WORKFILE, PAGE_FEES]

CSS = """
<style>
.stTabs [data-baseweb="tab-list"] { gap: 8px; }
.stTabs [data-baseweb="tab"] {
    height: 46px; white-space: pre-wrap; background-color: #ffffff;
    border-radius: 6px 6px 0 0; font-weight: 600; border: 1px solid #e0e0e0;
}
.stTabs [aria-selected="true"] { background-color: #1f77b4 !important; color: #ffffff !important; }
div.stButton > button[kind="primary"] { font-weight: 700; }
</style>
"""


def goto(page, lead_id=None):
    st.session_state["_goto"] = page
    if lead_id is not None:
        st.session_state["active_lead_id"] = int(lead_id)
    st.rerun()


def flash(kind, msg):
    st.session_state["_flash"] = (kind, msg)


def show_flash():
    f = st.session_state.pop("_flash", None)
    if f:
        {"success": st.success, "error": st.error, "warning": st.warning}.get(f[0], st.info)(f[1])


def due_label(deadline, status):
    d = parse_iso(deadline)
    if status in TERMINAL:
        return "—"
    if not d:
        return "No date"
    delta = (d - date.today()).days
    if delta < 0:
        return f"🔴 {abs(delta)}d overdue"
    if delta == 0:
        return "🟠 Today"
    if delta <= 3:
        return f"🟠 {d:%b %d} ({delta}d)"
    return f"{d:%b %d} ({delta}d)"


# ----------------------------------------------------------------------
# PIPELINE PAGE (home)
# ----------------------------------------------------------------------
def render_detail(lid):
    lead = get_lead(lid)
    if not lead:
        return
    st.markdown("---")
    st.markdown(f"### {lead['client_name']} · {lead['street']}")
    c1, c2, c3 = st.columns([1.2, 1, 1.4])

    with c1:
        digits = re.sub(r"\D", "", lead.get("phone") or "")
        st.markdown(f"**Phone:** [{lead['phone']}](tel:{digits})" if digits else "**Phone:** —")
        st.markdown(f"**Email:** [{lead['email']}](mailto:{lead['email']})" if lead.get("email") else "**Email:** —")
        maps = f"https://www.google.com/maps/search/?api=1&query={quote_plus(full_address(lead))}"
        st.markdown(f"**Property:** [{full_address(lead)}]({maps})")
        st.markdown(f"**County:** {lead.get('county') or '—'} · **Source:** {lead.get('source') or '—'}")
        st.markdown(f"**Service:** {lead['assignment_type']}")
        st.markdown(f"**Fee:** ${lead.get('fee') or 0:,} · **Due:** {due_label(lead['deadline'], lead['status'])}")
        if lead.get("notes"):
            st.caption(lead["notes"])

    with c2:
        new_status = st.selectbox("Status", STATUSES, index=STATUSES.index(lead["status"]), key=f"qs_{lid}")
        if st.button("Update status", key=f"qs_btn_{lid}"):
            ok, msg = change_status(lid, new_status)
            flash("success" if ok else "error", msg)
            st.rerun()
        if st.button("📋 Open Workfile", key=f"open_{lid}", type="primary"):
            goto(PAGE_WORKFILE, lid)

    with c3:
        with st.form(f"noteform_{lid}", clear_on_submit=True):
            note = st.text_input("Log a call / note", placeholder="Left voicemail, client wants Friday delivery…")
            if st.form_submit_button("Add to activity log") and note.strip():
                log_activity(lid, note.strip())
                update_lead(lid)
                flash("success", "Note logged.")
                st.rerun()
        for r in get_activity(lid, 8):
            st.caption(f"{r['ts'][:16]} — {r['message']}")

    with st.expander("Danger zone"):
        confirm = st.checkbox("Permanently delete this file and its activity log", key=f"delchk_{lid}")
        if st.button("Delete file", key=f"del_{lid}", disabled=not confirm):
            delete_lead(lid)
            flash("success", "File deleted.")
            st.rerun()


def page_pipeline():
    st.title("📊 Pipeline")
    df = list_leads()
    if df.empty:
        st.info("No files yet. Add your first lead from **➕ New Lead** (manual entry, or paste a text/email), "
                "or load sample leads from the sidebar to look around.")
        if st.button("➕ Add a lead", type="primary"):
            goto(PAGE_NEW)
        return

    today = date.today()
    df["_due"] = df["deadline"].apply(parse_iso)
    active = ~df["status"].isin(TERMINAL)
    overdue = active & df["_due"].apply(lambda d: d is not None and d < today)
    soon = active & df["_due"].apply(lambda d: d is not None and 0 <= (d - today).days <= 3)
    updated = pd.to_datetime(df["updated_at"], errors="coerce")
    stale = (df["status"] == "Fee Quoted") & (updated < datetime.now() - timedelta(days=3))
    inquiry = df["status"] == "Inquiry"
    closed_dt = pd.to_datetime(df["closed_at"], errors="coerce")
    closed_month = (df["status"] == "Closed") & (closed_dt.dt.year == today.year) & (closed_dt.dt.month == today.month)

    m = st.columns(6)
    m[0].metric("Open files", int(active.sum()))
    m[1].metric("Open fee value", f"${int(df.loc[active, 'fee'].fillna(0).sum()):,}")
    m[2].metric("🔴 Overdue", int(overdue.sum()))
    m[3].metric("🟠 Due ≤ 3 days", int(soon.sum()))
    m[4].metric("Quotes waiting > 3d", int(stale.sum()))
    m[5].metric("Closed this month", f"${int(df.loc[closed_month, 'fee'].fillna(0).sum()):,}")

    f1, f2, f3, f4 = st.columns([1.6, 2, 1.6, 1.4])
    with f1:
        preset = st.radio("View", ["Open files", "Needs attention", "Closed / Lost", "Everything"],
                          key="pipe_view")
    with f2:
        query = st.text_input("Search", placeholder="Client, address, phone, notes…", key="pipe_search")
    with f3:
        svc_filter = st.multiselect("Service", sorted(df["assignment_type"].dropna().unique()), key="pipe_svc")
    with f4:
        county_filter = st.multiselect("County", sorted(c for c in df["county"].dropna().unique() if c),
                                       key="pipe_county")

    if preset == "Open files":
        mask = active
    elif preset == "Needs attention":
        mask = active & (overdue | soon | stale | inquiry)
    elif preset == "Closed / Lost":
        mask = ~active
    else:
        mask = pd.Series(True, index=df.index)
    if query.strip():
        q = query.strip().lower()
        hay = (df["client_name"].fillna("") + " " + df["street"].fillna("") + " " + df["city"].fillna("") + " "
               + df["phone"].fillna("") + " " + df["email"].fillna("") + " " + df["notes"].fillna(""))
        mask &= hay.str.lower().str.contains(re.escape(q))
    if svc_filter:
        mask &= df["assignment_type"].isin(svc_filter)
    if county_filter:
        mask &= df["county"].isin(county_filter)

    shown = df[mask].copy()
    shown["_sort"] = shown["_due"].apply(lambda d: d or date(2999, 1, 1))
    shown = shown.sort_values(["_sort", "id"], ascending=[True, False] if preset == "Closed / Lost" else [True, True])

    if shown.empty:
        st.info("Nothing matches those filters.")
        return

    view = pd.DataFrame({
        "ID": shown["id"].values,
        "Due": [due_label(d, s) for d, s in zip(shown["deadline"], shown["status"])],
        "Client": shown["client_name"].values,
        "Property": shown["street"].values,
        "City": shown["city"].values,
        "County": shown["county"].values,
        "Service": shown["assignment_type"].values,
        "Status": shown["status"].values,
        "Fee": shown["fee"].fillna(0).astype(int).values,
        "Source": shown["source"].values,
        "Updated": shown["updated_at"].str[:16].values,
    })
    st.caption(f"{len(view)} file(s) · click a row to work it")
    event = st.dataframe(
        view, hide_index=True, on_select="rerun", selection_mode="single-row", key="pipeline_table",
        column_config={"Fee": st.column_config.NumberColumn("Fee", format="$%d"),
                       "ID": st.column_config.NumberColumn("ID", width="small")},
    )
    rows = event.selection.rows if event and event.selection else []
    if rows:
        render_detail(int(view.iloc[rows[0]]["ID"]))
    else:
        st.caption("Select a row to see contact info, change status, log a call, or open the workfile.")

    st.download_button(
        "📊 Export pipeline (.csv)",
        data=df.drop(columns=["comps_json", "raw_message", "_due"], errors="ignore").to_csv(index=False),
        file_name=f"pipeline_{today.isoformat()}.csv", mime="text/csv")


# ----------------------------------------------------------------------
# NEW LEAD PAGE (manual + text parser share one review form)
# ----------------------------------------------------------------------
def lead_form(prefix, d, default_source, submit_label="➕ Add to Pipeline"):
    fees = get_fees()
    services = list(fees)
    svc = d.get("assignment_type")
    svc_idx = services.index(svc) if svc in services else 0
    county_opts = ["Auto (from city)"] + COUNTIES
    county_idx = county_opts.index(d["county"]) if d.get("county") in county_opts else 0
    src_idx = SOURCES.index(default_source) if default_source in SOURCES else 0

    with st.form(f"lead_form_{prefix}"):
        c1, c2 = st.columns(2)
        with c1:
            name = st.text_input("Client name *", value=d.get("client_name", ""), key=f"{prefix}_name")
            phone = st.text_input("Phone", value=d.get("phone", ""), key=f"{prefix}_phone")
            email = st.text_input("Email", value=d.get("email", ""), key=f"{prefix}_email")
            service = st.selectbox("Service / intended use", services, index=svc_idx, key=f"{prefix}_svc")
            fee_override = st.number_input("Quoted fee override ($) — 0 uses the Fee Schedule",
                                           min_value=0, value=0, step=25, key=f"{prefix}_fee")
        with c2:
            street = st.text_input("Street address *", value=d.get("street", ""), key=f"{prefix}_street")
            cc, cz = st.columns([2, 1])
            city = cc.text_input("City", value=d.get("city", ""), key=f"{prefix}_city")
            zip_ = cz.text_input("ZIP", value=d.get("zip", ""), key=f"{prefix}_zip")
            county = st.selectbox("County", county_opts, index=county_idx, key=f"{prefix}_county")
            status = st.selectbox("Status", STATUSES, index=0, key=f"{prefix}_status")
            deadline = st.date_input("Target delivery date", value=d.get("deadline") or date.today() + timedelta(days=5),
                                     key=f"{prefix}_deadline")
        source = st.selectbox("Lead source", SOURCES, index=src_idx, key=f"{prefix}_source")
        notes = st.text_area("Notes", value=d.get("notes", ""), key=f"{prefix}_notes",
                             placeholder="Referral context, access instructions, known issues…")
        allow_dup = st.checkbox("Create even if this address already has an open file", key=f"{prefix}_dup")
        submitted = st.form_submit_button(submit_label, type="primary")

    if not submitted:
        return None
    return {
        "data": {
            "client_name": name.strip(), "phone": phone.strip(), "email": email.strip(),
            "street": street.strip(), "city": city.strip(), "zip": zip_.strip(),
            "county": (CITY_COUNTY.get(city.strip().lower(), "") if county == county_opts[0] else county),
            "assignment_type": service, "status": status, "deadline": deadline.isoformat(),
            "fee": int(fee_override) if fee_override > 0 else fees.get(service, 0),
            "source": source, "notes": notes.strip(), "raw_message": d.get("raw_message", ""),
        },
        "allow_dup": allow_dup,
    }


def save_new_lead(result, counter_key):
    data = result["data"]
    if not data["client_name"] or not data["street"]:
        st.error("Client name and street address are required.")
        return
    dups = find_open_duplicates(data["street"])
    if dups and not result["allow_dup"]:
        d = dups[0]
        st.error(f"An open file already exists for this address: **{d['client_name']}** — {d['assignment_type']} "
                 f"({d['status']}, file #{d['id']}). Tick the box to create it anyway.")
        return
    lid = create_lead(data)
    st.session_state["last_created"] = lid
    st.session_state[counter_key] = st.session_state.get(counter_key, 0) + 1
    st.session_state.pop("draft", None)
    st.rerun()


def page_new_lead():
    st.title("➕ New Lead")

    lc = st.session_state.get("last_created")
    if lc:
        lead = get_lead(lc)
        if lead:
            st.success(f"✅ Added **{lead['client_name']}** — {lead['street']} to the pipeline (file #{lc}).")
            b1, b2, b3 = st.columns([1, 1, 4])
            if b1.button("📊 View in pipeline"):
                st.session_state.pop("last_created")
                goto(PAGE_PIPELINE)
            if b2.button("📋 Open workfile"):
                st.session_state.pop("last_created")
                goto(PAGE_WORKFILE, lc)
        else:
            st.session_state.pop("last_created", None)

    tab_manual, tab_text = st.tabs(["✍️ Manual Entry", "📨 Paste Text / Email"])

    with tab_manual:
        n = st.session_state.get("manual_n", 0)
        res = lead_form(f"m{n}", {}, "Phone")
        if res:
            save_new_lead(res, "manual_n")

    with tab_text:
        st.markdown("Paste a text message, email, or voicemail transcript. The parser pulls out the name, phone, "
                    "email, address, city/county, service type and deadline — you review and correct before it's saved.")
        raw = st.text_area("Raw message", height=140, key="raw_msg",
                           placeholder="Hey Terrence, need a probate appraisal for my mom's house at "
                                       "2816 University Dr in Fort Worth. Need it by next Friday. - Sarah Jenkins 817-555-0198")
        if st.button("🔍 Parse message"):
            if raw.strip():
                st.session_state["draft"] = parse_message(raw, get_fees())
                st.session_state["draft_n"] = st.session_state.get("draft_n", 0) + 1
            else:
                st.error("Paste a message first.")

        draft = st.session_state.get("draft")
        if draft:
            found = draft.get("_found", {})
            labels = [("name", "Name"), ("phone", "Phone"), ("email", "Email"), ("address", "Address"),
                      ("city", "City/County"), ("service", "Service"), ("deadline", "Deadline")]
            st.markdown("**Detected:** " + " · ".join(("✅ " if found.get(k) else "❌ ") + v for k, v in labels))
            if not found.get("deadline"):
                st.caption("No deadline found in the message — defaulted to 5 days out. Set a firm date before engaging.")
            res = lead_form(f"p{st.session_state['draft_n']}", draft, "Text / SMS", "➕ Save to Pipeline")
            if res:
                save_new_lead(res, "draft_n_unused")


# ----------------------------------------------------------------------
# WORKFILE PAGE
# ----------------------------------------------------------------------
def page_workfile():
    st.title("📋 Workfile")
    df = list_leads()
    if df.empty:
        st.info("No files yet. Add a lead first.")
        return
    df = df.sort_values("id", ascending=False)
    ids = df["id"].tolist()
    active_id = st.session_state.get("active_lead_id")
    idx = ids.index(active_id) if active_id in ids else 0
    labels = {r.id: f"#{r.id} · {r.client_name} · {r.street} ({r.status})" for r in df.itertuples()}
    lid = st.selectbox("File", ids, index=idx, format_func=lambda i: labels[i])
    st.session_state["active_lead_id"] = lid
    lead = get_lead(lid)

    st.markdown(f"### {lead['client_name']} — {full_address(lead)}")
    st.caption(f"{lead['assignment_type']} · **{lead['status']}** · Fee ${lead.get('fee') or 0:,} · "
               f"Due {due_label(lead['deadline'], lead['status'])}")

    t_over, t_rec, t_comps, t_exp = st.tabs(
        ["📌 Overview", "🏛️ Property Records", "📁 Comps & Adjustments", "📤 Export"])

    fees = get_fees()

    # ---- Overview ----
    with t_over:
        services = list(fees)
        if lead["assignment_type"] and lead["assignment_type"] not in services:
            services.append(lead["assignment_type"])
        with st.form(f"overview_{lid}"):
            c1, c2 = st.columns(2)
            with c1:
                name = st.text_input("Client name", value=lead["client_name"] or "")
                phone = st.text_input("Phone", value=lead["phone"] or "")
                email = st.text_input("Email", value=lead["email"] or "")
                service = st.selectbox("Service", services,
                                       index=services.index(lead["assignment_type"]) if lead["assignment_type"] in services else 0)
                fee = st.number_input(f"Quoted fee ($) — schedule default for this service: "
                                      f"${fees.get(lead['assignment_type'], 0)}",
                                      min_value=0, value=int(lead.get("fee") or 0), step=25)
            with c2:
                street = st.text_input("Street address", value=lead["street"] or "")
                cc, cz = st.columns([2, 1])
                city = cc.text_input("City", value=lead["city"] or "")
                zip_ = cz.text_input("ZIP", value=lead["zip"] or "")
                county_opts = COUNTIES if lead.get("county") in COUNTIES or not lead.get("county") else COUNTIES + [lead["county"]]
                county = st.selectbox("County", county_opts,
                                      index=county_opts.index(lead["county"]) if lead.get("county") in county_opts else 0)
                status = st.selectbox("Status", STATUSES, index=STATUSES.index(lead["status"]))
                deadline = st.date_input("Target delivery date", value=parse_iso(lead["deadline"]) or date.today())
            source = st.selectbox("Lead source", SOURCES,
                                  index=SOURCES.index(lead["source"]) if lead.get("source") in SOURCES else 0)
            notes = st.text_area("Property notes & unique traits (manual appraiser entry)", value=lead["notes"] or "",
                                 height=120, help="Physical defects, unpermitted additions, custom features, access instructions.")
            saved = st.form_submit_button("💾 Save overview", type="primary")
        if saved:
            update_lead(lid, log="File details updated", client_name=name.strip(), phone=phone.strip(),
                        email=email.strip(), assignment_type=service, fee=int(fee), street=street.strip(),
                        city=city.strip(), zip=zip_.strip(), county=county, deadline=deadline.isoformat(),
                        source=source, notes=notes.strip())
            if status != lead["status"]:
                ok, msg = change_status(lid, status)
                flash("success" if ok else "error", msg if not ok else "Saved. " + msg)
            else:
                flash("success", "Saved.")
            st.rerun()
        if lead.get("raw_message"):
            with st.expander("Original message"):
                st.text(lead["raw_message"])
        st.markdown("**Activity**")
        for r in get_activity(lid, 20):
            st.caption(f"{r['ts'][:16]} — {r['message']}")

    # ---- Property records ----
    with t_rec:
        county = lead.get("county") or "Tarrant"
        cad_name, cad_url = CAD_LINKS.get(county, (f"{county} CAD", None))
        if cad_url:
            st.markdown(f"Look up public records: [{cad_name}]({cad_url})")
        default_src = f"{cad_name} Public Records"
        c1, c2 = st.columns(2)
        with c1:
            with st.container(border=True):
                st.markdown("#### County records vs. measurement")
                tad = st.number_input("County assessed GLA (SqFt)", min_value=0, step=10,
                                      value=int(lead.get("tad_gla") or 0), key=f"tad_{lid}")
                ansi = st.number_input("ANSI Z765 measured GLA (SqFt)", min_value=0, step=10,
                                       value=int(lead.get("ansi_gla") or 0), key=f"ansi_{lid}")
                lot = st.text_input("Site / lot size", value=lead.get("lot_size") or "", key=f"lot_{lid}")
                av = st.text_input("Assessed value", value=lead.get("assessed_value") or "", key=f"av_{lid}")
                ty = st.number_input("Tax year", min_value=2000, max_value=2100,
                                     value=int(lead.get("tax_year") or date.today().year), key=f"ty_{lid}")
                src = st.text_input("Data source", value=lead.get("records_source") or default_src, key=f"rsrc_{lid}")
                if tad and ansi:
                    diff = ansi - tad
                    if diff:
                        st.warning(f"⚠️ Measured GLA differs from county records by **{diff:+,} SqFt** "
                                   f"({diff / tad * 100:+.1f}%).")
                    else:
                        st.success("✅ Measured GLA matches county records.")
        with c2:
            with st.container(border=True):
                st.markdown("#### UAD 3.6 ratings (appraiser input)")
                cr = st.selectbox("Condition rating", CONDITION_RATINGS,
                                  index=CONDITION_RATINGS.index(lead["condition_rating"]) if lead.get("condition_rating") in CONDITION_RATINGS else 2,
                                  key=f"cr_{lid}")
                qr = st.selectbox("Quality rating", QUALITY_RATINGS,
                                  index=QUALITY_RATINGS.index(lead["quality_rating"]) if lead.get("quality_rating") in QUALITY_RATINGS else 2,
                                  key=f"qr_{lid}")
                tb = st.selectbox("Time adjustment basis", TIME_BASES,
                                  index=TIME_BASES.index(lead["time_basis"]) if lead.get("time_basis") in TIME_BASES else 0,
                                  key=f"tb_{lid}")
                st.caption("🛡️ Brokerage market data is kept separate from appraisal valuation opinions "
                           "under independence standards.")
            mlog = st.text_area("Verified sales data log", value=lead.get("market_log") or "", height=150,
                                key=f"mlog_{lid}", placeholder="Comp 1: address | Sold $ | Confirmed via NTREIS MLS on date")
        if st.button("💾 Save property records", key=f"save_rec_{lid}", type="primary"):
            update_lead(lid, log="Property records updated", tad_gla=int(tad), ansi_gla=int(ansi), lot_size=lot,
                        assessed_value=av, tax_year=int(ty), records_source=src, condition_rating=cr,
                        quality_rating=qr, time_basis=tb, market_log=mlog)
            flash("success", "Property records saved.")
            st.rerun()

    # ---- Comps & adjustments ----
    with t_comps:
        ver_key = f"compver_{lid}"
        ver = st.session_state.setdefault(ver_key, 0)

        with st.expander("📁 Import MLS / NTREIS comparable sheet (.csv)"):
            up = st.file_uploader("CSV export", type=["csv"], key=f"csv_{lid}_{ver}")
            if up is not None and st.button("Import into grid (replaces current comps)", key=f"imp_{lid}"):
                try:
                    imported = map_comp_csv(pd.read_csv(up))
                    update_lead(lid, log=f"Imported {len(imported)} comps from CSV",
                                comps_json=json.dumps(imported.to_dict("records")))
                    st.session_state[ver_key] = ver + 1
                    flash("success", f"Imported {len(imported)} comparable records. Review and set weights/adjustments.")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Could not read that CSV: {exc}")

        st.error("✏️ **APPRAISER RECONCILIATION & ADJUSTMENT ENGINE (UAD 3.6)** — enter dollar adjustments per comp. "
                 "Add rows with the + at the bottom of the grid.")
        edited = st.data_editor(
            load_comps(lead), num_rows="dynamic", key=f"comp_editor_{lid}_{ver}",
            column_config={
                "Address": st.column_config.TextColumn("Address", width="medium"),
                "Sale Price": st.column_config.NumberColumn("Sale Price", format="$%d", step=5000),
                "GLA": st.column_config.NumberColumn("GLA", format="%d"),
                "Weight": st.column_config.SelectboxColumn("UAD 3.6 Weight", options=WEIGHTS),
                **{c: st.column_config.NumberColumn(c, format="$%d", step=1000) for c in ADJ_COLS},
            },
        )
        calc = compute_comps(edited)
        if calc.empty:
            st.info("No comparables entered yet.")
        else:
            show = calc[["Address", "Weight", "Sale Price", "Net Adj", "Net %", "Gross Adj", "Gross %",
                         "Indicated Value", "Flags"]]
            st.dataframe(show, hide_index=True, column_config={
                "Sale Price": st.column_config.NumberColumn(format="$%d"),
                "Net Adj": st.column_config.NumberColumn(format="$%d"),
                "Gross Adj": st.column_config.NumberColumn(format="$%d"),
                "Net %": st.column_config.NumberColumn(format="%.1f%%"),
                "Gross %": st.column_config.NumberColumn(format="%.1f%%"),
                "Indicated Value": st.column_config.NumberColumn(format="$%d"),
            })
            for _, r in calc[calc["Flags"] != ""].iterrows():
                st.warning(f"⚠️ **Guideline alert — {r['Address'] or 'unnamed comp'}:** {r['Flags']}. "
                           "Verify comp selection or document the explanation.")
            used = calc[calc["Weight"] != WEIGHTS[2]]
            if not used.empty:
                lo, hi = used["Indicated Value"].min(), used["Indicated Value"].max()
                most = calc[calc["Weight"] == WEIGHTS[0]]
                cols = st.columns(3)
                cols[0].metric("Indicated range (weighted comps)", f"${lo:,.0f} – ${hi:,.0f}")
                cols[1].metric("Mean, 'Most Weight' comps", f"${most['Indicated Value'].mean():,.0f}" if not most.empty else "—")
                cols[2].metric("Comps used / set aside", f"{len(used)} / {len(calc) - len(used)}")
                st.caption("Reference statistics only — the final opinion of value is the appraiser's professional judgment.")
        recon = st.text_area("Reconciliation notes", value=lead.get("reconciliation_notes") or "", height=110,
                             key=f"recon_{lid}")
        if st.button("💾 Save comps & adjustments", key=f"save_comps_{lid}", type="primary"):
            keep = edited.copy()
            keep["Address"] = keep["Address"].fillna("")
            keep = keep[(keep["Address"].astype(str).str.strip() != "") |
                        (pd.to_numeric(keep["Sale Price"], errors="coerce").fillna(0) > 0)]
            keep = keep.fillna({"Sale Date": "", "Distance": "", "Weight": WEIGHTS[0]}).fillna(0)
            update_lead(lid, log=f"Comps & adjustments saved ({len(keep)} comps)",
                        comps_json=json.dumps(keep.to_dict("records"), default=float), reconciliation_notes=recon)
            flash("success", "Comps and adjustments saved.")
            st.rerun()
        st.caption("Edits in the grid are only stored when you press Save.")

    # ---- Export ----
    with t_exp:
        fresh = get_lead(lid)
        saved_calc = compute_comps(load_comps(fresh))
        md, js, vc = build_markdown(fresh, saved_calc), build_json(fresh, saved_calc), build_vcard(fresh)
        st.markdown("Exports use the **saved** version of this file. Save the other tabs first.")
        base = slug((fresh["client_name"] or "").lower())
        d1, d2, d3 = st.columns(3)
        d1.download_button("📄 Audit report (.md)", md, file_name=f"{base}_audit_summary.md", mime="text/markdown")
        d2.download_button("⚙️ UAD JSON payload", js, file_name=f"{base}_uad36_payload.json", mime="application/json")
        d3.download_button("📇 Contact vCard (.vcf)", vc, file_name=f"{base}_contact.vcf", mime="text/vcard")
        if st.button("💾 Save local backup folder", key=f"bk_{lid}"):
            folder = f"{BACKUP_ROOT}/{date.today().isoformat()}_{slug(fresh['client_name'])}_{slug(fresh['street'])}"
            os.makedirs(f"{folder}/photos_and_sketches", exist_ok=True)
            for fname, content in [(f"{base}_contact.vcf", vc), ("uad36_assignment_payload.json", js),
                                   (f"{base}_audit_summary.md", md)]:
                with open(f"{folder}/{fname}", "w", encoding="utf-8") as fh:
                    fh.write(content)
            log_activity(lid, f"Backup folder saved: {folder}")
            st.success(f"Saved to `{folder}/`")


# ----------------------------------------------------------------------
# FEE SCHEDULE PAGE
# ----------------------------------------------------------------------
def page_fees():
    st.title("💵 Fee Schedule")
    st.markdown("Baseline fees applied to new leads. Add, rename or remove services in the grid. "
                "Changing a fee here does not change quotes already on existing files.")
    fees = get_fees()
    df = pd.DataFrame({"Service": list(fees), "Fee ($)": list(fees.values())})
    edited = st.data_editor(df, num_rows="dynamic", hide_index=True, key="fee_editor",
                            column_config={"Fee ($)": st.column_config.NumberColumn(format="$%d", min_value=0, step=25)})
    if st.button("💾 Save fee schedule", type="primary"):
        clean = edited.dropna(subset=["Service"])
        clean = clean[clean["Service"].astype(str).str.strip() != ""]
        names = clean["Service"].astype(str).str.strip().tolist()
        if len(set(names)) != len(names):
            st.error("Service names must be unique.")
        elif not names:
            st.error("The fee schedule can't be empty.")
        else:
            save_fees({n: int(f) for n, f in zip(names, clean["Fee ($)"].fillna(0))})
            flash("success", "Fee schedule saved.")
            st.rerun()


# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------
def main():
    st.set_page_config(page_title="Appraisal Desk | Clearfork Appraisals", page_icon="🏠", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    init_db()

    if "_goto" in st.session_state:
        st.session_state["page"] = st.session_state.pop("_goto")
    st.session_state.setdefault("page", PAGE_PIPELINE)

    with st.sidebar:
        st.header("⚖️ Appraisal Desk")
        st.caption("Clearfork Appraisals")
        st.text(f"Appraiser: {APPRAISER['name']}")
        st.text(f"License: TX {APPRAISER['license']}")
        st.text("Coverage: Tarrant & surrounding")
        st.markdown("---")
        st.radio("Navigate", PAGES, key="page")
        st.markdown("---")
        leads = list_leads()
        if leads.empty:
            with st.expander("Getting started"):
                st.caption("Database is empty.")
                if st.button("Load sample leads"):
                    seed_demo()
                    st.rerun()
        else:
            st.caption(f"{int((~leads['status'].isin(TERMINAL)).sum())} open · {len(leads)} total files")
        if os.path.exists(DB_PATH):
            with open(DB_PATH, "rb") as fh:
                st.download_button("💾 Download database backup", fh.read(),
                                   file_name=f"appraisal_desk_backup_{date.today().isoformat()}.db")
        st.caption(f"Appraisal Desk {APP_VERSION}")

    show_flash()
    page = st.session_state["page"]
    if page != PAGE_NEW:
        st.session_state.pop("last_created", None)
    if page == PAGE_PIPELINE:
        page_pipeline()
    elif page == PAGE_NEW:
        page_new_lead()
    elif page == PAGE_WORKFILE:
        page_workfile()
    else:
        page_fees()


main()