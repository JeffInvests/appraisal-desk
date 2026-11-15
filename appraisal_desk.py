"""
Appraisal Desk Control Deck | Clearfork Appraisals
Internal back-office pipeline + workfile tool.

Run:   streamlit run appraisal_desk.py
Needs: streamlit >= 1.35, pandas
Data:  appraisal_desk.db (SQLite, created next to this file; set APPRAISAL_DB to relocate)
"""
import hmac
import io
import json
import os
import random
import re
import sqlite3
import zlib
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from urllib.parse import quote_plus

import pandas as pd
import streamlit as st

APP_VERSION = "v2.3"
DB_PATH = os.environ.get("APPRAISAL_DB", "appraisal_desk.db")
BACKUP_ROOT = "Appraisal_Files_Active"

APPRAISER = {
    "name": "Terrence Bilodeau",
    "designation": "Texas Certified Residential Real Estate Appraiser",
    "license": "1360232-CR",
    "broker_designation": "Texas Real Estate Broker",
    "broker_license": "0686157",          # as supplied by the user -- verify against TREC
}

STATUSES = ["Inquiry", "Fee Quoted", "Engaged", "In Progress", "Delivered", "Closed", "Lost"]
TERMINAL = ["Closed", "Lost"]
SOURCES = ["Phone", "Text / SMS", "Email", "Referral", "Website form", "Repeat client", "Other", "Simulated (demo)"]

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

RATING_C = ["C1", "C2", "C3", "C4", "C5", "C6"]
RATING_Q = ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6"]
TIME_BASES = ["Contract date (UAD 3.6)", "Sale / closing date (legacy)"]
WEIGHTS = ["Most Weight", "Less Weight", "No Weight"]
LIST_STATUSES = ["Settled Sale", "Pending", "Active", "Off Market"]
DIRECTIONS = ["", "N", "NE", "E", "SE", "S", "SW", "W", "NW"]
FINANCING = ["", "Cash", "Conventional", "FHA", "VA", "USDA", "Private", "Other"]
INSPECTION_TYPES = ["Full (interior & exterior)", "Exterior-only", "Desktop", "Hybrid"]
UNUSED_REASONS = ["Location differs significantly", "Property type / style differs", "Size differs significantly",
                  "Condition or quality differs significantly", "Sale too old", "Atypical / non-arm's-length sale",
                  "Data unverified or insufficient", "No sale price (active / pending)", "Other (explain in comment)"]

COMP_COLS = ["Address", "MLS #", "Status", "Sale Price", "Contract Date", "Sale Date", "DOM", "Proximity (mi)",
             "Direction", "GLA", "Concessions ($)", "Financing", "Cond", "Qual", "Weight", "Weight Rationale",
             "Time Adj", "Cond/Qual Adj", "GLA Adj", "Site Adj", "Other Adj"]
ADJ_COLS = ["Time Adj", "Cond/Qual Adj", "GLA Adj", "Site Adj", "Other Adj"]
NUM_ZERO = ["Sale Price", "GLA", "Concessions ($)"] + ADJ_COLS
NUM_NULLABLE = ["DOM", "Proximity (mi)"]
DATE_COLS = ["Contract Date", "Sale Date"]
SELECT_OPTIONS = {"Status": LIST_STATUSES, "Direction": DIRECTIONS[1:], "Financing": FINANCING[1:],
                  "Cond": RATING_C, "Qual": RATING_Q, "Weight": WEIGHTS}
SELECT_COLS = list(SELECT_OPTIONS)
TEXT_COLS = ["Address", "MLS #", "Weight Rationale"] + SELECT_COLS
UNUSED_COLS = ["Address", "Status", "Reason Not Used", "Comment"]

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
    "effective_date": "TEXT", "inspection_type": "TEXT", "inspection_date": "TEXT", "gse_required": "INTEGER",
    "time_rate": "REAL", "time_support": "TEXT", "gla_rate": "REAL", "opinion_value": "INTEGER",
    "unused_json": "TEXT",
    "paid_date": "TEXT", "paid_amount": "INTEGER",
    "no_interest": "INTEGER", "no_prior_services": "INTEGER", "disclosure_notes": "TEXT",
    "hours": "REAL",
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
        c.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        c.execute("""CREATE TABLE IF NOT EXISTS fees (
            service TEXT PRIMARY KEY, fee INTEGER, sort INTEGER)""")
        c.execute("""CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, category TEXT, vendor TEXT, description TEXT,
            amount REAL, lead_id INTEGER, receipt INTEGER, created_at TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS mileage (
            id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, miles REAL, rate REAL, origin TEXT,
            destination TEXT, purpose TEXT, lead_id INTEGER, created_at TEXT)""")
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


def get_setting(key, default=None):
    with db() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    try:
        return json.loads(r["value"]) if r else default
    except json.JSONDecodeError:
        return default


def set_setting(key, value):
    with db() as c:
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, json.dumps(value)))


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
    rf"\b\d{{1,6}}\s+(?:[NSEW]\.?\s+)?(?:[A-Za-z0-9'\-]+\.?\s+){{0,4}}?{STREET_SUFFIXES}\b\.?(?:\s+{STREET_SUFFIXES}\b\.?)?"
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
    ("Gift Tax (Form 709)", r"gift tax|\b709\b"),
    ("IRS Appraisals (Gift/Estate Tax)", r"\birs\b|estate tax"),
    ("Date of Death / Probate", r"probate|(?<!real )estate\b|date of death|passed away|deceased|inherit|late (mother|father|mom|dad)"),
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
        draft["email"] = m.group(0).rstrip(".-")
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
# SIMULATED CUSTOMER (demo / proof-of-concept data -- entirely fictional)
# ======================================================================
SIM_SOURCE = "Simulated (demo)"
SIM_FIRST = ["Alex", "Morgan", "Taylor", "Casey", "Riley", "Jamie", "Avery", "Quinn", "Parker", "Reese", "Dana",
             "Skyler", "Rowan", "Emerson", "Harper", "Logan", "Dakota", "Sawyer", "Blake", "Cameron"]
SIM_LAST = ["Brooks", "Whitfield", "Alvarez", "Nguyen", "Patel", "Sullivan", "Hayes", "Okafor", "Lindqvist",
            "Delgado", "Mercer", "Castillo", "Fairchild", "Navarro", "Thornton", "Ellison", "Marlowe", "Quintero",
            "Ashford", "Whitaker"]
SIM_STREETS = ["Cedar Hollow", "Mockingbird Ridge", "Quail Run", "Sage Meadow", "Copper Creek", "Silver Oak",
               "Prairie Wind", "Foxtail", "Bluebonnet Springs", "Lantern Ridge", "Heron Landing", "Juniper Crest",
               "Wild Plum", "Stonegate Bend", "Pecan Orchard", "Larkspur"]
SIM_SUFFIXES = ["Dr", "Ln", "Ct", "Trl", "Way", "Rd", "Cir"]
SIM_CITY_ZIPS = {
    "Fort Worth": ["76107", "76109", "76116", "76132", "76244"], "Arlington": ["76013", "76016", "76018"],
    "Southlake": ["76092"], "Keller": ["76248"], "Weatherford": ["76086", "76087"], "Mansfield": ["76063"],
    "Burleson": ["76028"], "Denton": ["76205", "76210"], "Grapevine": ["76051"], "Colleyville": ["76034"],
    "Benbrook": ["76126"], "Granbury": ["76048"],
}
SIM_CITY_WEIGHTS = {"Fort Worth": 8, "Arlington": 3, "Southlake": 2, "Keller": 2, "Weatherford": 2, "Mansfield": 1,
                    "Burleson": 1, "Denton": 1, "Grapevine": 1, "Colleyville": 1, "Benbrook": 1, "Granbury": 1}
SIM_AREA_CODES = ["817", "682", "214", "972"]
SIM_CHANNELS = ["Text message", "Email", "Voicemail transcript"]

# service -> opening requests (each contains the keywords the parser looks for)
SIM_ASKS = {
    "Date of Death / Probate": ["need a probate appraisal for my late father's house",
                                "I need a date of death valuation for my mom's home, she passed away in the spring"],
    "Divorce or Marital Property Division": ["need an appraisal of our home for the divorce",
                                             "my attorney says we need a neutral valuation for the marital property in our divorce"],
    "Property Tax Protest (ARB)": ["I want to protest my appraisal district value, the notice of appraised value is way too high",
                                   "need help with a property tax protest, I think the house is over-assessed"],
    "Pre-Listing Appraisal": ["we're about to list and want a pre-listing appraisal first",
                              "need a pre-listing appraisal before we list the house"],
    "PMI / MIP Cancellation": ["I need a value for PMI removal on my loan",
                               "my lender said I need an appraisal to cancel PMI"],
    "Bail Bond & Surety Collateral": ["need a fast valuation of a house being pledged as collateral for a bail bond",
                                      "I need a property valuation for a bond, it's for surety collateral"],
    "Trust Funding & Administration": ["need a valuation for funding a family trust with this house",
                                       "our attorney wants an appraisal for trust administration"],
    "Gift Tax (Form 709)": ["need an appraisal for a gift tax return, Form 709, we're gifting the house to our son",
                            "I'm filing a gift tax return and need a qualified appraisal of the home"],
    "IRS Appraisals (Gift/Estate Tax)": ["our CPA needs an IRS-ready appraisal of the house",
                                         "need an appraisal for the estate tax filing, my accountant asked for it"],
    "Bankruptcy Schedule A/B": ["need a home value for my bankruptcy schedule A/B",
                                "my bankruptcy attorney asked me to get an appraisal of the house"],
    "Pre-Purchase for Buyers": ["I'm buying this house and want an independent pre-purchase appraisal",
                                "we're buying a home and would like a pre-purchase valuation"],
    "Measurement & Floor Plans (ANSI Z765)": ["need a floor plan and ANSI measurement of the house",
                                              "I only need the floor plan and square footage measured"],
    "Partial Interest / Partition Suit": ["need a valuation for a partition suit, my sibling and I co-own the house",
                                          "I need an appraisal of a partial interest in a home we co-own"],
    "Immigration Asset Documentation": ["need an appraisal of my house for immigration paperwork",
                                        "a lawyer asked for property valuation documentation for a USCIS filing"],
    "Expert Witness & Litigation Support": ["our attorney needs an expert witness appraiser for a litigation matter",
                                            "looking for an appraiser who can testify in a dispute over the house"],
    "Portfolio & Multi-Property": ["I have a few rental properties and need a portfolio appraisal, starting with this one",
                                   "need valuations on multiple properties, this is the first"],
    "Partial Release": ["need an appraisal for a partial release of a lien on part of the land",
                        "my lender needs a partial release valuation"],
}
SIM_FORMATS = ["{ask} at {street} in {city}.", "{ask}. The property is {street}, {city} TX {zip}.",
               "{ask} - it's {street} in {city} {zip}."]


def _sim_phone(rng):
    return f"{rng.choice(SIM_AREA_CODES)}-555-01{rng.randint(0, 99):02d}"      # 555-0100..0199 is reserved fiction


def _sim_when(rng, today):
    kind = rng.choice(["nextweekday", "slash", "monthname", "asap", "eom", "nextweek", "none"])
    d = today + timedelta(days=rng.randint(6, 28))
    if kind == "nextweekday":
        return rng.choice(["Need it by next Friday.", "Hoping to have it next Wednesday.", "Deadline is next Monday."]), True
    if kind == "slash":
        return f"Need it by {d.month}/{d.day}.", True
    if kind == "monthname":
        return f"Need it by {d.strftime('%b')} {d.day}.", True
    if kind == "asap":
        return "It's kind of urgent, ASAP if possible.", True
    if kind == "eom":
        return "Need it by the end of the month.", True
    if kind == "nextweek":
        return "Can you get it done next week?", True
    return "", False


def generate_sim_customer(fees, service=None, channel=None, rng=None, today=None):
    """Return a fictional inbound inquiry. `expected` holds the ground truth for testing the parser."""
    rng = rng or random.Random()
    today = today or date.today()
    usable = [s for s in fees if s in SIM_ASKS]
    service = service if service in usable else rng.choice(usable)
    channel = channel if channel in SIM_CHANNELS else rng.choice(SIM_CHANNELS)
    first, last = rng.choice(SIM_FIRST), rng.choice(SIM_LAST)
    city = rng.choices(list(SIM_CITY_WEIGHTS), weights=list(SIM_CITY_WEIGHTS.values()))[0]
    street = f"{rng.randint(100, 9899)} {rng.choice(SIM_STREETS)} {rng.choice(SIM_SUFFIXES)}"
    zip_ = rng.choice(SIM_CITY_ZIPS[city])
    phone = _sim_phone(rng)
    email = f"{first.lower()}.{last.lower()}@example.com"
    ask = rng.choice(SIM_ASKS[service])
    body = rng.choice(SIM_FORMATS).format(ask=ask, street=street, city=city, zip=zip_)
    when, has_deadline = _sim_when(rng, today)
    shown_phone = rng.choice([phone, f"({phone[:3]}) {phone[4:]}", phone.replace("-", ".")])

    if channel == "Text message":
        text = f"Hey Terrence, {body} {when} {shown_phone} - {first} {last}".replace("  ", " ")
    elif channel == "Email":
        text = (f"Subject: Appraisal request\n\nHi Terrence,\n\nMy name is {first} {last}. {body[0].upper() + body[1:]} "
                f"{when}\n\nYou can reach me at {shown_phone} or {email}.\n\nThanks!").replace("  ", " ")
    else:
        text = (f"Voicemail transcript: hi Terrence this is {first} {last} calling about an appraisal, {body} "
                f"{when} my number is {shown_phone}. thanks, bye.").replace("  ", " ")
    return {
        "message": text, "channel": channel, "service": service,
        "expected": {"client_name": f"{first} {last}", "phone": phone, "street": street, "city": city,
                     "zip": zip_, "county": CITY_COUNTY[city.lower()], "assignment_type": service,
                     "has_deadline": has_deadline,
                     "email": email if channel == "Email" else None},
    }


def add_simulated_leads(n, fees, rng=None):
    """Generate n fictional inquiries, run each through the real parser, save them to the pipeline."""
    rng = rng or random.Random()
    added = []
    for _ in range(n):
        for _attempt in range(8):
            cust = generate_sim_customer(fees, rng=rng)
            d = parse_message(cust["message"], fees)
            if d.get("street") and d.get("client_name") and not find_open_duplicates(d["street"]):
                break
        else:
            continue
        svc = d.get("assignment_type") or cust["service"]
        lid = create_lead({
            "client_name": d["client_name"], "phone": d.get("phone", ""), "email": d.get("email", ""),
            "street": d["street"], "city": d.get("city", ""), "zip": d.get("zip", ""),
            "county": d.get("county", ""), "assignment_type": svc, "status": "Inquiry",
            "deadline": (d.get("deadline") or date.today() + timedelta(days=5)).isoformat(),
            "fee": fees.get(svc, 0), "source": SIM_SOURCE, "notes": "", "raw_message": cust["message"],
        })
        added.append(lid)
    return added


def delete_simulated():
    with db() as c:
        ids = [r["id"] for r in c.execute("SELECT id FROM leads WHERE source=?", (SIM_SOURCE,))]
        for lid in ids:
            c.execute("DELETE FROM activity WHERE lead_id=?", (lid,))
        c.execute("DELETE FROM leads WHERE source=?", (SIM_SOURCE,))
    return len(ids)


# ======================================================================
# COMPS / ADJUSTMENT MATH + UAD 3.6 READINESS
# ======================================================================
def _isblank(v):
    if v is None:
        return True
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def to_num(x):
    if _isblank(x):
        return 0.0
    s = re.sub(r"[^\d.\-]", "", str(x))
    try:
        return float(s) if s not in ("", "-", ".") else 0.0
    except ValueError:
        return 0.0


def to_nullable_num(x):
    if _isblank(x):
        return None
    s = re.sub(r"[^\d.\-]", "", str(x))
    if s in ("", "-", "."):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def to_date(x):
    if _isblank(x):
        return None
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    s = str(x).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(s[:10] if fmt == "%Y-%m-%d" else s, fmt).date()
        except ValueError:
            continue
    t = pd.to_datetime(s, errors="coerce")
    return None if pd.isna(t) else t.date()


def fmt_d(d):
    return d.strftime("%m/%d/%Y") if d else ""


def rating_code(v, letter):
    m = re.match(rf"^{letter}[1-6]", str(v or ""))
    return m.group(0) if m else ""


def blank_comp():
    r = {}
    for c in COMP_COLS:
        if c in NUM_ZERO:
            r[c] = 0.0
        elif c in NUM_NULLABLE or c in DATE_COLS or c in SELECT_COLS:
            r[c] = None
        else:
            r[c] = ""
    r["Status"] = LIST_STATUSES[0]
    return r


def _to_editor_df(recs):
    df = pd.DataFrame(recs, columns=COMP_COLS)
    for c in DATE_COLS:
        df[c] = pd.to_datetime(df[c].apply(to_date), errors="coerce")
    for c in NUM_NULLABLE:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in NUM_ZERO:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    for c in SELECT_COLS:
        df[c] = df[c].apply(lambda v, c=c: v if v in SELECT_OPTIONS[c] else None)
    for c in ("Address", "MLS #", "Weight Rationale"):
        df[c] = df[c].apply(lambda v: "" if _isblank(v) else str(v))
    return df


def empty_comps(n=3):
    return _to_editor_df([blank_comp() for _ in range(n)])


def load_comps(lead):
    try:
        recs = json.loads(lead.get("comps_json") or "[]")
    except json.JSONDecodeError:
        recs = []
    if not recs:
        return empty_comps()
    fixed = []
    for r in recs:
        r = dict(r)
        if "GLA/Site Adj" in r and "GLA Adj" not in r:
            r["GLA Adj"] = r.pop("GLA/Site Adj")
        if "Distance" in r and "Proximity (mi)" not in r:
            r["Proximity (mi)"] = to_nullable_num(r.pop("Distance"))
        if r.get("Weight") == "No Weight (Set Aside)":
            r["Weight"] = "No Weight"
        fixed.append({c: r.get(c) for c in COMP_COLS})
    return _to_editor_df(fixed)


def normalize_comps(df):
    d = df.copy()
    for c in COMP_COLS:
        if c not in d:
            d[c] = None
    for c in NUM_ZERO:
        d[c] = pd.to_numeric(d[c], errors="coerce").fillna(0.0)
    for c in NUM_NULLABLE:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    for c in TEXT_COLS:
        d[c] = d[c].apply(lambda v: "" if _isblank(v) else str(v).strip())
    for c in DATE_COLS:
        d[c] = d[c].apply(to_date).astype(object)
    d = d[(d["Address"] != "") | (d["Sale Price"] > 0)].copy()
    return d.reset_index(drop=True)[COMP_COLS]


def compute_comps(df):
    d = normalize_comps(df)
    d["Net Adj"] = d[ADJ_COLS].sum(axis=1)
    d["Gross Adj"] = d[ADJ_COLS].abs().sum(axis=1)
    price = d["Sale Price"].where(d["Sale Price"] > 0)
    d["Net %"] = (d["Net Adj"] / price * 100).fillna(0.0)
    d["Gross %"] = (d["Gross Adj"] / price * 100).fillna(0.0)
    d["Adjusted Price"] = d["Sale Price"] + d["Net Adj"]

    # Traditional review-flag thresholds -- a prompt to document, not a UAD 3.6 rule.
    def flag(r):
        out = []
        if r["Gross %"] > 25:
            out.append("Gross > 25%")
        if abs(r["Net %"]) > 15:
            out.append("Net > 15%")
        return ", ".join(out)

    d["Flags"] = [flag(r) for _, r in d.iterrows()]
    return d


def comps_to_records(df):
    out = []
    for _, r in normalize_comps(df).iterrows():
        rec = {}
        for c in COMP_COLS:
            v = r[c]
            if c in DATE_COLS:
                rec[c] = v.isoformat() if v else ""
            elif c in NUM_NULLABLE:
                rec[c] = None if _isblank(v) else float(v)
            elif c in NUM_ZERO:
                rec[c] = float(v)
            else:
                rec[c] = v
        out.append(rec)
    return out


def used_comps(calc):
    return calc[calc["Weight"].isin(WEIGHTS[:2])]


# ---------- unused ("analyzed, not used") table ----------
def empty_unused():
    return pd.DataFrame({c: pd.Series(dtype="object") for c in UNUSED_COLS})


def load_unused(lead):
    try:
        recs = json.loads(lead.get("unused_json") or "[]")
    except json.JSONDecodeError:
        recs = []
    if not recs:
        return empty_unused()
    df = pd.DataFrame(recs)
    for c in UNUSED_COLS:
        if c not in df:
            df[c] = ""
    return df[UNUSED_COLS].astype(object)


def normalize_unused(df):
    d = df.copy()
    for c in UNUSED_COLS:
        if c not in d:
            d[c] = ""
        d[c] = d[c].apply(lambda v: "" if _isblank(v) else str(v).strip())
    d = d[d["Address"] != ""]
    return d.reset_index(drop=True)[UNUSED_COLS]


# ---------- MLS CSV import: auto column matching ----------
def _norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


CSV_TARGETS = ["Address", "MLS #", "Status", "Sale Price", "Contract Date", "Sale Date", "DOM",
               "Proximity (mi)", "GLA", "Concessions ($)", "Financing"]
CSV_SYNONYMS = {
    "Address": ["address", "fulladdress", "propertyaddress", "streetaddress", "listingaddress"],
    "MLS #": ["mlsnumber", "mls", "mlsnum", "mlsid", "listingid", "listingnumber", "listno", "mlsno"],
    "Status": ["status", "liststatus", "listingstatus", "mlsstatus", "standardstatus"],
    "Sale Price": ["closeprice", "soldprice", "saleprice", "closedprice", "sp", "closeprc", "soldpr"],
    "Contract Date": ["contractdate", "purchasecontractdate", "undercontractdate", "pendingdate", "contractdt"],
    "Sale Date": ["closedate", "saledate", "solddate", "closingdate", "settleddate", "closedt"],
    "DOM": ["dom", "daysonmarket", "daysonmkt", "cdom", "cumulativedaysonmarket"],
    "Proximity (mi)": ["distance", "dist", "proximity", "milesfromsubject", "distancefromsubject"],
    "GLA": ["gla", "sqft", "squarefeet", "livingarea", "totalsqft", "buildingsqft", "sf", "heatedsqft",
            "sqftliving", "abovegradefinishedarea", "livingareasqft"],
    "Concessions ($)": ["sellerconcessions", "concessions", "sellerpaid", "sellercontribution",
                        "concessionsamount", "sellerconcessionsamount"],
    "Financing": ["financing", "financingtype", "loantype", "buyerfinancing", "financingconcessions"],
}
ADDR_PART_SYNS = {
    "num": ["streetnumber", "streetno", "housenumber", "addressnumber"],
    "dir": ["streetdirprefix", "streetdirection", "streetdir", "streetpredirectional"],
    "name": ["streetname"],
    "suffix": ["streetsuffix", "streettype"],
    "city": ["city"],
}
PARTS_OPT = "(build from street number / name columns)"


def detect_address_parts(columns):
    nm = {_norm(c): c for c in columns}
    return {k: next((nm[s] for s in syns if s in nm), None) for k, syns in ADDR_PART_SYNS.items()}


def guess_mapping(columns, saved=None):
    saved = saved or {}
    nm = {_norm(c): c for c in columns}
    parts = detect_address_parts(columns)
    out = {}
    for target in CSV_TARGETS:
        s = saved.get(target)
        if s is not None and (s in columns or s in ("", "__parts__")):
            if s == "__parts__" and not parts["name"]:
                s = ""
            out[target] = s
            continue
        out[target] = next((nm[x] for x in CSV_SYNONYMS[target] if x in nm), "")
        if target == "Address" and not out[target] and parts["name"]:
            out[target] = "__parts__"
    return out


def norm_status(v, has_price):
    s = str(v or "").strip().lower()
    if s in ("closed", "sold", "settled", "settled sale", "c"):
        return "Settled Sale"
    if "pend" in s or "under contract" in s or "contingent" in s or "option" in s:
        return "Pending"
    if any(k in s for k in ("expire", "withdraw", "cancel", "off market", "hold", "delete")):
        return "Off Market"
    if "active" in s:
        return "Active"
    return "Settled Sale" if has_price else "Active"


def norm_financing(v):
    s = str(v or "").strip().lower()
    if not s or s in ("nan", "none"):
        return None
    for key, label in (("cash", "Cash"), ("conv", "Conventional"), ("fha", "FHA"), ("va", "VA"),
                       ("usda", "USDA"), ("private", "Private")):
        if key in s:
            return label
    return "Other"


def read_csv_upload(data):
    last = None
    for enc in ("utf-8-sig", "latin-1"):
        try:
            return pd.read_csv(io.BytesIO(data), encoding=enc, dtype=str)
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise last


def build_comps_from_csv(raw, mapping):
    parts = detect_address_parts(list(raw.columns))
    recs = []
    for _, row in raw.iterrows():
        def pick(target):
            col = mapping.get(target)
            return row[col] if col and col != "__parts__" and col in raw.columns else None

        rec = blank_comp()
        if mapping.get("Address") == "__parts__":
            bits = [row[parts[k]] for k in ("num", "dir", "name", "suffix") if parts.get(k)]
            addr = " ".join(str(b).strip() for b in bits if not _isblank(b))
            if parts.get("city") and not _isblank(row[parts["city"]]):
                addr += f", {str(row[parts['city']]).strip()}"
        else:
            a = pick("Address")
            addr = "" if _isblank(a) else str(a).strip()
        rec["Address"] = addr
        m = pick("MLS #")
        rec["MLS #"] = "" if _isblank(m) else str(m).strip()
        price = to_num(pick("Sale Price"))
        rec["Sale Price"] = price
        rec["Status"] = norm_status(pick("Status"), price > 0)
        rec["Contract Date"] = to_date(pick("Contract Date"))
        rec["Sale Date"] = to_date(pick("Sale Date"))
        rec["DOM"] = to_nullable_num(pick("DOM"))
        rec["Proximity (mi)"] = to_nullable_num(pick("Proximity (mi)"))
        rec["GLA"] = to_num(pick("GLA"))
        rec["Concessions ($)"] = to_num(pick("Concessions ($)"))
        rec["Financing"] = norm_financing(pick("Financing"))
        if rec["Address"] or price > 0:
            recs.append(rec)
    return _to_editor_df(recs)


def addr_key(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


# ---------- adjustment helpers (suggestions only) ----------
def suggest_time_adjustments(df, effective, rate_pct_per_month, use_contract=True):
    """Time adj = price x monthly rate x months between the comp's basis date and the effective date.
    Rounded to $100. Rows without the needed date/price are skipped."""
    d = normalize_comps(df)
    done = skipped = 0
    for i, r in d.iterrows():
        basis = r["Contract Date"] if use_contract else r["Sale Date"]
        if not basis or r["Sale Price"] <= 0:
            skipped += 1
            continue
        months = (effective - basis).days / 30.4375
        d.at[i, "Time Adj"] = float(round(r["Sale Price"] * rate_pct_per_month / 100 * months / 100) * 100)
        done += 1
    return d, done, skipped


def suggest_gla_adjustments(df, subject_gla, rate_per_sf):
    d = normalize_comps(df)
    done = skipped = 0
    for i, r in d.iterrows():
        if r["GLA"] <= 0:
            skipped += 1
            continue
        d.at[i, "GLA Adj"] = float(round((subject_gla - r["GLA"]) * rate_per_sf / 100) * 100)
        done += 1
    return d, done, skipped


# ---------- UAD 3.6 readiness ----------
def uad_readiness(lead):
    """List of checks: {group, label, ok (True / False / None = informational), detail}."""
    items = []

    def add(group, label, ok, detail=""):
        items.append({"group": group, "label": label, "ok": None if ok is None else bool(ok), "detail": detail})

    comps = compute_comps(load_comps(lead))
    unused = normalize_unused(load_unused(lead))

    add("Assignment", "Quoted fee and firm delivery date",
        bool(lead.get("fee")) and bool(parse_iso(lead.get("deadline"))), "Overview tab.")
    add("Assignment", "Effective date of appraisal", bool(parse_iso(lead.get("effective_date"))),
        "Subject & Scope tab.")
    add("Assignment", "Inspection type (as specified in the engagement)", bool(lead.get("inspection_type")),
        "Subject & Scope tab.")
    disc_ok = (bool(lead.get("no_interest")) and bool(lead.get("no_prior_services"))) or \
        bool((lead.get("disclosure_notes") or "").strip())
    add("Assignment", "Interest and prior-services (3-year) disclosure addressed", disc_ok,
        "Subject & Scope tab. Prior services in any capacity includes brokerage work.")

    ansi = int(lead.get("ansi_gla") or 0)
    tad = int(lead.get("tad_gla") or 0)
    add("Subject", "ANSI Z765 measured GLA", ansi > 0, "Subject & Scope tab.")
    if ansi and tad and ansi != tad:
        add("Subject", "County vs. measured GLA", None, f"Differs by {ansi - tad:+,} SqFt — be ready to explain it.")
    add("Subject", "Condition rating (C1–C6)", bool(rating_code(lead.get("condition_rating"), "C")),
        "Definitions were rewritten in UAD 3.6 — apply the current ones.")
    add("Subject", "Quality rating (Q1–Q6)", bool(rating_code(lead.get("quality_rating"), "Q")), "")

    n = len(comps)
    add("Comparables", "At least 3 comparables entered", n >= 3,
        f"{n} entered (common practice — confirm what this assignment needs).")
    if n:
        settled = comps["Status"] == "Settled Sale"
        sold_or_pend = comps["Status"].isin(["Settled Sale", "Pending"])
        cd_missing = comps["Contract Date"].apply(lambda d: d is None)
        sd_missing = comps["Sale Date"].apply(lambda d: d is None)
        prox = comps["Proximity (mi)"]

        checks = [
            ("Listing status", comps["Status"] == ""),
            ("Sale price (settled sales)", settled & (comps["Sale Price"] <= 0)),
            ("Contract date (settled / pending)", sold_or_pend & cd_missing),
            ("Sale date (settled sales)", settled & sd_missing),
            ("Days on market", comps["DOM"].isna()),
            ("Proximity to subject", prox.isna()),
            ("Direction from subject (when proximity > 0)", (prox.fillna(0) > 0) & (comps["Direction"] == "")),
            ("MLS # / data source identifier", comps["MLS #"] == ""),
            ("Living area (GLA)", comps["GLA"] <= 0),
            ("Condition and quality ratings", (comps["Cond"] == "") | (comps["Qual"] == "")),
            ("Weight (Most / Less / No)", comps["Weight"] == ""),
            ("Weight rationale", comps["Weight Rationale"] == ""),
        ]
        for label, mask in checks:
            bad = [a or "(no address)" for a in comps.loc[mask, "Address"]]
            add("Comparables", f"{label} on every comp", not bad, ("Missing: " + "; ".join(bad)) if bad else "")
        flagged = [f"{r['Address'] or '(no address)'} ({r['Flags']})" for _, r in comps.iterrows() if r["Flags"]]
        if flagged:
            add("Comparables", "Adjustment review flags", None, "Document: " + "; ".join(flagged))

    ov = int(lead.get("opinion_value") or 0)
    used = used_comps(comps)
    add("Reconciliation", "Opinion of value entered", ov > 0, "Appraiser's conclusion — Comps tab.")
    if ov > 0 and not used.empty:
        lo, hi = used["Adjusted Price"].min(), used["Adjusted Price"].max()
        add("Reconciliation", "Opinion falls within range of adjusted prices (weighted comps)", lo <= ov <= hi,
            f"Range ${lo:,.0f} – ${hi:,.0f}.")
    elif ov > 0:
        add("Reconciliation", "Opinion falls within range of adjusted prices (weighted comps)", False,
            "No comps carry Most / Less weight.")
    add("Reconciliation", "Time-adjustment support summarized (data source, tool, technique)",
        bool((lead.get("time_support") or "").strip()), "Comps tab.")
    add("Reconciliation", "Reconciliation narrative", bool((lead.get("reconciliation_notes") or "").strip()),
        "Comps tab.")
    if len(unused):
        bad = [r["Address"] for _, r in unused.iterrows() if not r["Reason Not Used"] or not r["Comment"]]
        add("Reconciliation", "Properties analyzed but not used: reason and comment", not bad,
            ("Incomplete: " + "; ".join(bad)) if bad else "")
    else:
        add("Reconciliation", "Properties analyzed but not used", None, "None listed — add any you analyzed and set aside.")
    return items


def readiness_counts(rec):
    lead = {k: (None if _isblank(v) else v) for k, v in dict(rec).items()}
    scored = [i for i in uad_readiness(lead) if i["ok"] is not None]
    return sum(1 for i in scored if i["ok"]), len(scored)


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


def comps_csv(calc):
    out = calc.copy()
    for c in DATE_COLS:
        out[c] = out[c].apply(fmt_d)
    return out.to_csv(index=False)


def build_json(lead, calc, unused, items):
    comps = []
    for _, r in calc.iterrows():
        comps.append({
            "address": r["Address"], "mls_number": r["MLS #"], "listing_status": r["Status"],
            "sale_price": r["Sale Price"], "contract_date": fmt_d(r["Contract Date"]),
            "sale_date": fmt_d(r["Sale Date"]),
            "days_on_market": None if _isblank(r["DOM"]) else float(r["DOM"]),
            "proximity_mi": None if _isblank(r["Proximity (mi)"]) else float(r["Proximity (mi)"]),
            "direction": r["Direction"], "gla": r["GLA"], "sales_concessions": r["Concessions ($)"],
            "financing": r["Financing"], "condition": r["Cond"], "quality": r["Qual"],
            "weight": r["Weight"], "weight_rationale": r["Weight Rationale"],
            "adjustments": {"time": r["Time Adj"], "condition_quality": r["Cond/Qual Adj"],
                            "gla": r["GLA Adj"], "site": r["Site Adj"], "other": r["Other Adj"]},
            "net_adjustment": r["Net Adj"], "gross_adjustment": r["Gross Adj"], "adjusted_price": r["Adjusted Price"],
        })
    pkg = {
        "format_note": ("Working data sheet for keying into appraisal software. This is NOT a UCDP / MISMO XML "
                        "submission and does not include every UAD 3.6 field."),
        "appraiser": {"name": APPRAISER["name"], "designation": APPRAISER["designation"],
                      "talcb_license_number": APPRAISER["license"],
                      "broker_designation": APPRAISER["broker_designation"],
                      "broker_license_number": APPRAISER["broker_license"]},
        "assignment": {
            "client_name": lead.get("client_name"), "intended_use": lead.get("assignment_type"),
            "quoted_fee": lead.get("fee"), "delivery_deadline": lead.get("deadline"),
            "effective_date": lead.get("effective_date"), "inspection_type": lead.get("inspection_type"),
            "inspection_date": lead.get("inspection_date"), "gse_delivered": bool(lead.get("gse_required")),
            "no_present_or_prospective_interest": bool(lead.get("no_interest")),
            "no_services_prior_3_years": bool(lead.get("no_prior_services")),
            "disclosure_notes": lead.get("disclosure_notes"),
        },
        "subject": {
            "address": full_address(lead), "county": lead.get("county"),
            "county_assessed_gla": lead.get("tad_gla"), "ansi_z765_gla": lead.get("ansi_gla"),
            "lot_size": lead.get("lot_size"), "assessed_value": lead.get("assessed_value"),
            "tax_year": lead.get("tax_year"), "records_source": lead.get("records_source"),
            "condition_rating": rating_code(lead.get("condition_rating"), "C"),
            "quality_rating": rating_code(lead.get("quality_rating"), "Q"), "notes": lead.get("notes"),
        },
        "sales_comparison": {
            "time_adjustment_basis": lead.get("time_basis"),
            "time_adjustment_support": lead.get("time_support"),
            "comparables": comps,
            "analyzed_not_used": unused.to_dict("records"),
        },
        "reconciliation": {"opinion_of_value": lead.get("opinion_value") or None,
                           "narrative": lead.get("reconciliation_notes")},
        "readiness": [{"group": i["group"], "check": i["label"], "ok": i["ok"], "detail": i["detail"]} for i in items],
    }
    return json.dumps(pkg, indent=4, default=float)


def build_markdown(lead, calc, unused, items):
    md = f"""# Appraisal Assignment Audit Summary
**Clearfork Appraisals**
*{APPRAISER['name']}, {APPRAISER['designation']} (TALCB #{APPRAISER['license']}) · {APPRAISER['broker_designation']} (#{APPRAISER['broker_license']})*
*Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*

---

## 1. Assignment
* **Client:** {lead.get('client_name')} · {lead.get('phone') or '—'} · {lead.get('email') or '—'}
* **Subject Address:** {full_address(lead)} ({lead.get('county') or '—'} County)
* **Intended Use / Assignment:** {lead.get('assignment_type')}
* **Quoted Fee:** ${lead.get('fee') or 0} · **Delivery Deadline:** {lead.get('deadline') or '—'}
* **Effective Date:** {fmt_d(parse_iso(lead.get('effective_date'))) or '—'}
* **Inspection:** {lead.get('inspection_type') or '—'} ({fmt_d(parse_iso(lead.get('inspection_date'))) or 'date n/a'})

## 2. Subject & Public Records
* **County Assessed GLA:** {lead.get('tad_gla') or 0} SqFt · **ANSI Z765 Measured GLA:** {lead.get('ansi_gla') or 0} SqFt
* **Site / Lot Size:** {lead.get('lot_size') or '—'}
* **Assessed Value ({lead.get('tax_year') or '—'}):** {lead.get('assessed_value') or '—'}
* **Data Provenance:** {lead.get('records_source') or '—'}
* **Condition:** {rating_code(lead.get('condition_rating'), 'C') or '—'} · **Quality:** {rating_code(lead.get('quality_rating'), 'Q') or '—'}
* **Notes:** {lead.get('notes') or '—'}

---

## 3. Sales Comparison
**Time-adjustment basis:** {lead.get('time_basis') or '—'}
**Time-adjustment support:** {lead.get('time_support') or '—'}

"""
    if calc.empty:
        md += "_No comparables entered._\n"
    for i, r in calc.iterrows():
        dom = "n/a" if _isblank(r["DOM"]) else f"{r['DOM']:.0f}"
        prox = "n/a" if _isblank(r["Proximity (mi)"]) else f"{r['Proximity (mi)']:.2f} mi {r['Direction']}".strip()
        md += f"""### Comparable #{i + 1}: {r['Address']}
* **MLS #:** {r['MLS #'] or '—'} · **Status:** {r['Status'] or '—'} · **Proximity:** {prox} · **DOM:** {dom}
* **Contract Date:** {fmt_d(r['Contract Date']) or '—'} · **Sale Date:** {fmt_d(r['Sale Date']) or '—'} · **Sale Price:** ${r['Sale Price']:,.0f}
* **GLA:** {r['GLA']:,.0f} · **Condition/Quality:** {r['Cond'] or '—'}/{r['Qual'] or '—'} · **Concessions:** ${r['Concessions ($)']:,.0f} · **Financing:** {r['Financing'] or '—'}
* **Weight:** {r['Weight'] or '—'} — {r['Weight Rationale'] or '(no rationale entered)'}
* **Adjustments:** Time ${r['Time Adj']:,.0f} · Cond/Qual ${r['Cond/Qual Adj']:,.0f} · GLA ${r['GLA Adj']:,.0f} · Site ${r['Site Adj']:,.0f} · Other ${r['Other Adj']:,.0f}
* **Net:** ${r['Net Adj']:,.0f} ({r['Net %']:.1f}%) · **Gross:** ${r['Gross Adj']:,.0f} ({r['Gross %']:.1f}%) · **Adjusted Price: ${r['Adjusted Price']:,.0f}**{('  ⚠ ' + r['Flags']) if r['Flags'] else ''}

"""
    if len(unused):
        md += "### Properties Analyzed, Not Used\n"
        for _, r in unused.iterrows():
            md += f"* **{r['Address']}** ({r['Status'] or 'status n/a'}) — {r['Reason Not Used'] or '—'}: {r['Comment'] or '—'}\n"
        md += "\n"
    md += "---\n\n## 4. Reconciliation\n"
    ov = int(lead.get("opinion_value") or 0)
    md += f"* **Opinion of Value:** {'$' + format(ov, ',') if ov else '—'}\n\n{lead.get('reconciliation_notes') or '_No narrative entered._'}\n\n"
    open_items = [i for i in items if i["ok"] is False]
    md += "---\n\n## 5. Open Readiness Items\n"
    md += "\n".join(f"* {i['label']}" + (f" — {i['detail']}" if i["detail"] else "") for i in open_items) if open_items \
        else "All readiness checks complete."
    md += "\n"
    return md


# ======================================================================
# REPORTING (business performance, receivables, compliance records)
# ======================================================================
WON = ["Engaged", "In Progress", "Delivered", "Closed"]
EARNED = ["Delivered", "Closed"]
LITIGATION_SERVICES = {"Expert Witness & Litigation Support", "Divorce or Marital Property Division",
                       "Partial Interest / Partition Suit", "Bankruptcy Schedule A/B"}
PERIODS = ["This month", "Last month", "This quarter", "Year to date", "Last 12 months", "All time", "Custom"]
STATUS_RE = re.compile(r"^Status: (.+) → (.+)$")
RF_COLS = ["id", "client", "phone", "street", "city", "county", "service", "status", "source", "fee", "created",
           "updated", "quote", "engaged", "delivered", "deadline", "paid", "paid_amount", "won", "lost", "earned",
           "no_interest", "no_prior", "disc_note", "hours"]


def status_history():
    """{lead_id: {status: timestamp it was first entered}} from the activity log."""
    out = {}
    with db() as c:
        for r in c.execute("SELECT lead_id, ts, message FROM activity ORDER BY id"):
            m = STATUS_RE.match(r["message"] or "")
            if m:
                out.setdefault(r["lead_id"], {}).setdefault(m.group(2), r["ts"])
    return out


def add_years(d, n):
    try:
        return d.replace(year=d.year + n)
    except ValueError:                      # Feb 29
        return d.replace(year=d.year + n, day=28)


def period_bounds(choice, today, custom=None):
    if choice == "This month":
        return today.replace(day=1), today
    if choice == "Last month":
        end = today.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end
    if choice == "This quarter":
        return today.replace(month=3 * ((today.month - 1) // 3) + 1, day=1), today
    if choice == "Year to date":
        return today.replace(month=1, day=1), today
    if choice == "Last 12 months":
        return today - timedelta(days=364), today
    if choice == "Custom" and custom:
        s, e = custom
        return (s, e) if s <= e else (e, s)
    return date(1900, 1, 1), today          # All time


def report_frame(df):
    """One row per file with the dates the reports need.
    Dates come from the activity log where the status change was recorded there; otherwise from
    created / closed / updated timestamps (approximate)."""
    hist = status_history()
    rows = []
    for rec in df.to_dict("records"):
        rec = {k: (None if _isblank(v) else v) for k, v in rec.items()}
        h = hist.get(rec["id"], {})
        status = rec.get("status") or "Inquiry"
        created = parse_iso(rec.get("created_at"))

        def first(*names):
            ts = [h[n] for n in names if n in h]
            return parse_iso(min(ts)) if ts else None

        delivered = None
        if status in EARNED:
            delivered = (first("Delivered", "Closed") or parse_iso(rec.get("closed_at"))
                         or parse_iso(rec.get("updated_at")) or created)
        fee = int(rec.get("fee") or 0)
        rows.append({
            "id": rec["id"], "client": rec.get("client_name") or "", "phone": rec.get("phone") or "",
            "street": rec.get("street") or "", "city": rec.get("city") or "", "county": rec.get("county") or "",
            "service": rec.get("assignment_type") or "", "status": status, "source": rec.get("source") or "",
            "fee": fee, "created": created, "updated": parse_iso(rec.get("updated_at")),
            "quote": first("Fee Quoted"),
            "engaged": first("Engaged", "In Progress") or (created if status in WON else None),
            "delivered": delivered, "deadline": parse_iso(rec.get("deadline")),
            "paid": parse_iso(rec.get("paid_date")),
            "paid_amount": int(rec.get("paid_amount") or 0) or fee,
            "won": status in WON, "lost": status == "Lost", "earned": status in EARNED,
            "no_interest": bool(rec.get("no_interest")), "no_prior": bool(rec.get("no_prior_services")),
            "disc_note": (rec.get("disclosure_notes") or "").strip(),
            "hours": float(rec.get("hours") or 0),
        })
    return pd.DataFrame(rows, columns=RF_COLS)


def _in(d, s, e):
    return d is not None and s <= d <= e


def _mask(series, s, e):
    """Boolean mask for dates inside [s, e] -- always dtype bool, even for an empty frame."""
    return pd.Series([_in(d, s, e) for d in series], index=series.index, dtype=bool)


def business_kpis(rf, s, e):
    deliv = rf[_mask(rf["delivered"], s, e)]
    new = rf[_mask(rf["created"], s, e)]
    coll = rf[_mask(rf["paid"], s, e)]
    won, lost = int(new["won"].sum()), int(new["lost"].sum())
    turn = [(r.delivered - r.engaged).days for r in deliv.itertuples() if r.engaged and r.delivered]
    due = [r for r in deliv.itertuples() if r.deadline]
    qd = [(r.quote - r.created).days for r in new.itertuples() if r.quote and r.created]
    return {
        "earned": int(deliv["fee"].sum()), "delivered_n": len(deliv),
        "avg_fee": float(deliv["fee"].mean()) if len(deliv) else 0.0,
        "collected": int(coll["paid_amount"].sum()),
        "new_leads": len(new), "won": won, "lost": lost,
        "win_rate": (won / (won + lost)) if (won + lost) else None,
        "avg_turnaround": (sum(turn) / len(turn)) if turn else None,
        "on_time": (sum(1 for r in due if r.delivered <= r.deadline) / len(due)) if due else None,
        "avg_days_to_quote": (sum(qd) / len(qd)) if qd else None,
    }


def breakdown(rf, s, e, col, label):
    new = rf[_mask(rf["created"], s, e)]
    deliv = rf[_mask(rf["delivered"], s, e)]
    keys = sorted({(k or "—") for k in new[col]} | {(k or "—") for k in deliv[col]})
    rows = []
    for k in keys:
        n = new[new[col].replace("", "—") == k]
        d = deliv[deliv[col].replace("", "—") == k]
        w, l = int(n["won"].sum()), int(n["lost"].sum())
        rows.append({label: k, "New leads": len(n), "Won": w, "Lost": l,
                     "Win rate": (w / (w + l) * 100) if (w + l) else None,
                     "Delivered": len(d), "Earned": int(d["fee"].sum()),
                     "Avg fee": float(d["fee"].mean()) if len(d) else None})
    out = pd.DataFrame(rows, columns=[label, "New leads", "Won", "Lost", "Win rate", "Delivered", "Earned", "Avg fee"])
    return out.sort_values(["Earned", "New leads"], ascending=False).reset_index(drop=True)


def month_range(s, e):
    out, (y, m) = [], (s.year, s.month)
    while (y, m) <= (e.year, e.month):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def monthly_trend(rf, s, e):
    if s.year < 1990:
        ds = [d for col in ("created", "delivered", "paid") for d in rf[col] if d]
        s = min(ds) if ds else e
    s = max(s, add_years(e, -2))            # cap the chart at 24 months
    months = month_range(s, e)
    earned, coll = dict.fromkeys(months, 0), dict.fromkeys(months, 0)
    for r in rf.itertuples():
        if _in(r.delivered, s, e):
            earned[r.delivered.strftime("%Y-%m")] += r.fee
        if _in(r.paid, s, e):
            coll[r.paid.strftime("%Y-%m")] += r.paid_amount
    return pd.DataFrame({"Earned": list(earned.values()), "Collected": list(coll.values())}, index=months)


def quarterly_collected(rf, year):
    q = {f"Q{i}": 0 for i in range(1, 5)}
    for r in rf.itertuples():
        if r.paid and r.paid.year == year:
            q[f"Q{(r.paid.month - 1) // 3 + 1}"] += r.paid_amount
    return pd.DataFrame({"Quarter": list(q), "Collected": list(q.values())})


def receivables(rf, today):
    ar = rf[rf["earned"] & rf["paid"].isna()].copy() if len(rf) else rf.copy()
    ar["days"] = [(today - d).days if d else 0 for d in ar["delivered"]]
    ar["bucket"] = ar["days"].apply(lambda n: "0–30" if n <= 30 else "31–60" if n <= 60 else "61–90" if n <= 90 else "90+")
    return ar.sort_values("days", ascending=False).reset_index(drop=True)


def aging_table(ar):
    rows = []
    for b in ["0–30", "31–60", "61–90", "90+"]:
        sub = ar[ar["bucket"] == b]
        rows.append({"Days outstanding": b, "Invoices": len(sub), "Amount": int(sub["fee"].sum())})
    return pd.DataFrame(rows)


def pipeline_snapshot(rf, today):
    q = rf[rf["status"] == "Fee Quoted"]
    b = rf[rf["status"].isin(["Engaged", "In Progress"])]
    ar = receivables(rf, today)
    return {"quoted_n": len(q), "quoted_v": int(q["fee"].sum()), "booked_n": len(b), "booked_v": int(b["fee"].sum()),
            "ar_n": len(ar), "ar_v": int(ar["fee"].sum())}


def followup_list(rf, today, min_idle=3):
    f = rf[rf["status"].isin(["Inquiry", "Fee Quoted"])].copy()
    f["idle"] = [(today - d).days if d else 0 for d in f["updated"]]
    f = f[f["idle"] >= min_idle].sort_values("idle", ascending=False)
    return f[["id", "client", "phone", "service", "status", "fee", "idle"]].reset_index(drop=True)


def retention_table(rf, today):
    rows = []
    for r in rf[rf["earned"]].itertuples():
        until = add_years(r.delivered, 5) if r.delivered else None
        note = ("If testimony was given, keep until 2 yrs after final disposition of the proceeding if that is later."
                if r.service in LITIGATION_SERVICES else "")
        status = "—" if not until else ("Past 5-yr minimum" if until < today else f"{(until - today).days} days left")
        rows.append({"File #": r.id, "Client": r.client, "Property": r.street, "Service": r.service,
                     "Delivered": fmt_d(r.delivered), "Retain until (5 yrs)": fmt_d(until), "Status": status,
                     "Note": note, "_until": until})
    out = pd.DataFrame(rows, columns=["File #", "Client", "Property", "Service", "Delivered", "Retain until (5 yrs)",
                                      "Status", "Note", "_until"])
    return out.sort_values("_until").drop(columns="_until").reset_index(drop=True) if len(out) else out.drop(columns="_until")


def disclosure_table(rf):
    rows = []
    for r in rf[rf["won"]].itertuples():
        attested = r.no_interest and r.no_prior
        rows.append({"File #": r.id, "Client": r.client, "Property": r.street, "Status": r.status,
                     "No interest": "✅" if r.no_interest else "—", "No prior services (3 yr)": "✅" if r.no_prior else "—",
                     "Disclosure note": r.disc_note, "Needs attention": not (attested or r.disc_note)})
    return pd.DataFrame(rows, columns=["File #", "Client", "Property", "Status", "No interest",
                                       "No prior services (3 yr)", "Disclosure note", "Needs attention"])


def accountant_rows(rf, s, e):
    sel = rf[_mask(rf["delivered"], s, e) | _mask(rf["paid"], s, e)]
    return pd.DataFrame({
        "File #": sel["id"], "Client": sel["client"], "Property": sel["street"], "City": sel["city"],
        "County": sel["county"], "Service": sel["service"], "Source": sel["source"], "Fee": sel["fee"],
        "Delivered": [fmt_d(d) for d in sel["delivered"]], "Paid date": [fmt_d(d) for d in sel["paid"]],
        "Amount received": pd.array([int(a) if p else pd.NA for a, p in zip(sel["paid_amount"], sel["paid"])], dtype="Int64"),
    }).reset_index(drop=True)


def _money(x):
    return f"-${abs(x):,.0f}" if round(x) < 0 else f"${x:,.0f}"


def _pct(x):
    return "—" if x is None else f"{x * 100:.0f}%"


def _days(x):
    return "—" if x is None else f"{x:.1f} days"


def df_to_md(df):
    if df.empty:
        return "_None._\n"
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join("" if _isblank(r[c]) else str(r[c]) for c in cols) + " |")
    return "\n".join(lines) + "\n"


def report_markdown(rf, s, e, label, today, exp=None, mil=None, basis="cash"):
    k = business_kpis(rf, s, e)
    snap = pipeline_snapshot(rf, today)
    md = f"""# Business Report — {label}
**Clearfork Appraisals** · {APPRAISER['name']}
Period: {fmt_d(s) if s.year > 1990 else 'start'} – {fmt_d(e)} · Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}

## Performance
* Earned (delivered files): **{_money(k['earned'])}** across {k['delivered_n']} file(s), average fee {_money(k['avg_fee'])}
* Collected (payments received): **{_money(k['collected'])}**
* New leads: {k['new_leads']} · won {k['won']} · lost {k['lost']} · win rate {_pct(k['win_rate'])}
* Average turnaround (engaged to delivered): {_days(k['avg_turnaround'])} · on-time delivery: {_pct(k['on_time'])}
* Average time to first quote: {_days(k['avg_days_to_quote'])}

## Pipeline today
* Quotes awaiting decision: {snap['quoted_n']} ({_money(snap['quoted_v'])})
* Booked, in progress: {snap['booked_n']} ({_money(snap['booked_v'])})
* Delivered, unpaid: {snap['ar_n']} ({_money(snap['ar_v'])})

"""
    for col, lab in (("service", "Service"), ("county", "County"), ("source", "Lead source")):
        b = breakdown(rf, s, e, col, lab).copy()
        b["Win rate"] = b["Win rate"].apply(lambda x: "—" if _isblank(x) else f"{x:.0f}%")
        b["Earned"] = b["Earned"].apply(_money)
        b["Avg fee"] = b["Avg fee"].apply(lambda x: "—" if _isblank(x) else _money(x))
        md += f"## By {lab.lower()}\n{df_to_md(b)}\n"
    if exp is not None and mil is not None:
        md += "\n" + pnl_markdown(pnl(rf, exp, mil, s, e, basis), label, basis)
    return md


# ======================================================================
# ======================================================================
# EXPENSES, MILEAGE & PROFIT AND LOSS
# ======================================================================
EXPENSE_CATEGORIES = [
    "MLS & data subscriptions", "E&O / liability insurance", "Licenses, dues & continuing education",
    "Software & technology", "Office & supplies", "Marketing & advertising", "Phone & internet",
    "Vehicle (non-mileage)", "Travel & meals", "Equipment", "Professional services (legal / accounting)",
    "Other",
]
MILEAGE_PURPOSES = ["Property inspection", "Comparable drive-by", "Client meeting", "ARB hearing / courthouse",
                    "Closing / delivery", "Other"]
# IRS standard business mileage rate by year ($/mile). Each trip stores the rate it was logged with.
MILEAGE_RATES = {2025: 0.70, 2026: 0.725}
EXP_COLS = ["id", "date", "category", "vendor", "description", "amount", "lead_id", "receipt"]
MIL_COLS = ["id", "date", "miles", "rate", "origin", "destination", "purpose", "lead_id"]


def default_mileage_rate(d):
    yrs = sorted(MILEAGE_RATES)
    return MILEAGE_RATES.get(d.year, MILEAGE_RATES[yrs[-1]] if d.year > yrs[-1] else MILEAGE_RATES[yrs[0]])


def add_months(d, n):
    y, m = divmod(d.month - 1 + n, 12)
    y, m = d.year + y, m + 1
    last = [31, 29 if (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return date(y, m, min(d.day, last))


def add_expense(d, category, vendor, description, amount, lead_id=None, receipt=False):
    with db() as c:
        cur = c.execute("INSERT INTO expenses (date, category, vendor, description, amount, lead_id, receipt, created_at) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (d.isoformat(), category, vendor, description, round(float(amount), 2),
                         int(lead_id) if lead_id else None, int(bool(receipt)), now_str()))
        return cur.lastrowid


def add_trip(d, miles, rate, origin, destination, purpose, lead_id=None):
    with db() as c:
        cur = c.execute("INSERT INTO mileage (date, miles, rate, origin, destination, purpose, lead_id, created_at) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (d.isoformat(), round(float(miles), 1), float(rate), origin, destination, purpose,
                         int(lead_id) if lead_id else None, now_str()))
        return cur.lastrowid


def _read(table, cols):
    with db() as c:
        df = pd.read_sql_query(f"SELECT {', '.join(cols)} FROM {table} ORDER BY date DESC, id DESC", c)
    df["date"] = df["date"].apply(parse_iso)
    return df


def load_expenses():
    df = _read("expenses", EXP_COLS)
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
    df["receipt"] = df["receipt"].fillna(0).astype(int).astype(bool)
    return df


def load_mileage():
    df = _read("mileage", MIL_COLS)
    df["miles"] = pd.to_numeric(df["miles"], errors="coerce").fillna(0.0)
    df["rate"] = pd.to_numeric(df["rate"], errors="coerce").fillna(0.0)
    df["deduction"] = (df["miles"] * df["rate"]).round(2)
    return df


def save_book_edits(table, edited, fields):
    """Apply inline edits / deletes from a books data_editor. Returns (updated, deleted)."""
    upd = dele = 0
    with db() as c:
        for _, r in edited.iterrows():
            rid = int(r["id"])
            if r["Delete"]:
                c.execute(f"DELETE FROM {table} WHERE id=?", (rid,))
                dele += 1
                continue
            vals = [fields[k](r) for k in fields]
            c.execute(f"UPDATE {table} SET {', '.join(k + '=?' for k in fields)} WHERE id=?", [*vals, rid])
            upd += 1
    return upd, dele


def _dsel(df, s, e):
    return df[_mask(df["date"], s, e)] if len(df) else df


def pnl(rf, exp, mil, s, e, basis="cash"):
    """Profit and loss for [s, e]. basis 'cash' = payments received; 'accrual' = files delivered."""
    if basis == "cash":
        rev_rows = rf[_mask(rf["paid"], s, e)]
        revenue = float(rev_rows["paid_amount"].sum())
    else:
        rev_rows = rf[_mask(rf["delivered"], s, e)]
        revenue = float(rev_rows["fee"].sum())
    ex = _dsel(exp, s, e)
    mi = _dsel(mil, s, e)
    by_cat = ex.groupby("category")["amount"].sum().sort_values(ascending=False) if len(ex) else pd.Series(dtype=float)
    expenses = float(ex["amount"].sum()) if len(ex) else 0.0
    mileage = float(mi["deduction"].sum()) if len(mi) else 0.0
    net = revenue - expenses - mileage
    return {"revenue": revenue, "expenses": expenses, "mileage": mileage, "miles": float(mi["miles"].sum()) if len(mi) else 0.0,
            "net": net, "margin": (net / revenue) if revenue else None, "by_category": by_cat, "files": len(rev_rows)}


def monthly_pnl(rf, exp, mil, s, e, basis="cash"):
    if s.year < 1990:
        ds = [d for col in ("created", "delivered", "paid") for d in rf[col] if d] + list(exp["date"].dropna()) + list(mil["date"].dropna())
        s = min(ds) if ds else e
    s = max(s, add_years(e, -2))
    months = month_range(s, e)
    rev, cost = dict.fromkeys(months, 0.0), dict.fromkeys(months, 0.0)
    for r in rf.itertuples():
        d, amt = (r.paid, r.paid_amount) if basis == "cash" else (r.delivered, r.fee)
        if _in(d, s, e):
            rev[d.strftime("%Y-%m")] += amt
    for r in exp.itertuples():
        if _in(r.date, s, e):
            cost[r.date.strftime("%Y-%m")] += r.amount
    for r in mil.itertuples():
        if _in(r.date, s, e):
            cost[r.date.strftime("%Y-%m")] += r.deduction
    out = pd.DataFrame({"Revenue": list(rev.values()), "Costs": list(cost.values())}, index=months)
    out["Net"] = out["Revenue"] - out["Costs"]
    return out


def service_profitability(rf, exp, mil, s, e):
    """Per delivered file: fee less costs linked to that file; $/hour where hours were logged."""
    sel = rf[_mask(rf["delivered"], s, e)]
    rows = []
    for r in sel.itertuples():
        direct = float(exp.loc[exp["lead_id"] == r.id, "amount"].sum()) + float(mil.loc[mil["lead_id"] == r.id, "deduction"].sum())
        rows.append({"service": r.service, "fee": r.fee, "direct": direct, "hours": r.hours})
    d = pd.DataFrame(rows, columns=["service", "fee", "direct", "hours"])
    out = []
    for svc, grp in d.groupby("service"):
        h = grp[grp["hours"] > 0]
        out.append({"Service": svc, "Files": len(grp), "Revenue": grp["fee"].sum(), "Direct costs": grp["direct"].sum(),
                    "Net": (grp["fee"] - grp["direct"]).sum(), "Files with hours": len(h),
                    "Revenue / hour": (h["fee"].sum() / h["hours"].sum()) if len(h) else None,
                    "Net / hour": ((h["fee"] - h["direct"]).sum() / h["hours"].sum()) if len(h) else None})
    cols = ["Service", "Files", "Revenue", "Direct costs", "Net", "Files with hours", "Revenue / hour", "Net / hour"]
    return pd.DataFrame(out, columns=cols).sort_values("Net", ascending=False).reset_index(drop=True)


def pnl_markdown(p, label, basis):
    cats = "\n".join(f"* {k}: {_money(v)}" for k, v in p["by_category"].items()) or "* (none)"
    margin = "" if p["margin"] is None else f" ({p['margin'] * 100:.0f}% margin)"
    basis_txt = "cash basis: payments received" if basis == "cash" else "accrual basis: files delivered"
    return (f"## Profit & loss — {label} ({basis_txt})\n"
            f"* Revenue: **{_money(p['revenue'])}** ({p['files']} file(s))\n"
            f"* Expenses: {_money(p['expenses'])}\n"
            f"* Mileage deduction: {_money(p['mileage'])} ({p['miles']:,.1f} miles)\n"
            f"* **Net: {_money(p['net'])}**{margin}\n\n"
            f"Expenses by category:\n{cats}\n\n_Figures are bookkeeping summaries, not tax advice._\n")



# ==== UI ====
# ======================================================================
PAGE_PIPELINE = "📊 Pipeline"
PAGE_NEW = "➕ New Lead"
PAGE_WORKFILE = "📋 Workfile"
PAGE_REPORTS = "📈 Reports"
PAGE_FEES = "💵 Fee Schedule"
PAGE_BOOKS = "🧾 Expenses & Mileage"
PAGES = [PAGE_PIPELINE, PAGE_NEW, PAGE_WORKFILE, PAGE_REPORTS, PAGE_BOOKS, PAGE_FEES]

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
        "UAD ready": [("%d/%d" % readiness_counts(rec)) if rec["status"] in ("Engaged", "In Progress", "Delivered") else "—"
                      for rec in shown.to_dict("records")],
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
        data=df.drop(columns=["comps_json", "unused_json", "raw_message", "_due"], errors="ignore").to_csv(index=False),
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
    st.session_state.pop("sim", None)
    st.rerun()


def sim_tab():
    st.markdown("🤖 **Simulated customer** — generates a fictional inbound inquiry so you can demo the whole flow "
                "without real client data. Names, phones (555-01xx, a reserved fictional range) and emails "
                "(example.com) are invented, and street names are made up, so an address could only match a real "
                "one by coincidence. The message goes through the same parser and review form as a real one. "
                "It is a template generator, not an AI model.")
    fees = get_fees()
    usable = [s for s in fees if s in SIM_ASKS]
    c1, c2 = st.columns(2)
    svc = c1.selectbox("Service", ["Random"] + usable, key="sim_svc")
    chan = c2.selectbox("Channel", ["Random"] + SIM_CHANNELS, key="sim_chan")
    if st.button("🎲 Generate customer inquiry", type="primary"):
        st.session_state["sim"] = generate_sim_customer(fees, None if svc == "Random" else svc,
                                                        None if chan == "Random" else chan)
        st.session_state["sim_n"] = st.session_state.get("sim_n", 0) + 1

    sim = st.session_state.get("sim")
    if sim:
        st.markdown(f"**Incoming {sim['channel'].lower()}:**")
        st.code(sim["message"], language=None, wrap_lines=True)
        draft = parse_message(sim["message"], fees)
        found = draft.get("_found", {})
        labels = [("name", "Name"), ("phone", "Phone"), ("email", "Email"), ("address", "Address"),
                  ("city", "City/County"), ("service", "Service"), ("deadline", "Deadline")]
        st.markdown("**Parser detected:** " + " · ".join(("✅ " if found.get(k) else "❌ ") + v for k, v in labels))
        res = lead_form(f"s{st.session_state['sim_n']}", draft, SIM_SOURCE, "➕ Save to Pipeline")
        if res:
            save_new_lead(res, "sim_unused")

    st.markdown("---")
    st.markdown("**Fill the pipeline for a demo**")
    b1, b2 = st.columns([1, 3])
    n = b1.number_input("How many", min_value=1, max_value=15, value=5, key="sim_bulk_n")
    if b2.button("➕ Add simulated leads straight to the pipeline"):
        added = add_simulated_leads(int(n), fees)
        flash("success", f"Added {len(added)} simulated lead(s) with source “{SIM_SOURCE}”. "
                         "Remove them any time from the sidebar.")
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

    tab_manual, tab_text, tab_sim = st.tabs(["✍️ Manual Entry", "📨 Paste Text / Email", "🤖 Simulated Customer"])

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

    with tab_sim:
        sim_tab()


# ----------------------------------------------------------------------
# WORKFILE PAGE
# ----------------------------------------------------------------------
EFFECTIVE_DATE_HINTS = {
    "Date of Death / Probate": "Retrospective: the effective date is normally the decedent's date of death.",
    "Property Tax Protest (ARB)": "Normally January 1 of the tax year being protested.",
    "Divorce or Marital Property Division": "Confirm with the attorney whether it is the date of separation, filing, or current.",
    "Gift Tax (Form 709)": "Normally the date of the gift.",
    "IRS Appraisals (Gift/Estate Tax)": "Normally the date of the gift or of death.",
}

CC = st.column_config
COMP_COLCFG = {
    "Address": CC.TextColumn("Address", width="medium"),
    "MLS #": CC.TextColumn("MLS #", width="small"),
    "Status": CC.SelectboxColumn("Status", options=LIST_STATUSES),
    "Sale Price": CC.NumberColumn("Sale Price", format="$%d", step=5000),
    "Contract Date": CC.DateColumn("Contract Date", format="MM/DD/YYYY"),
    "Sale Date": CC.DateColumn("Sale Date", format="MM/DD/YYYY"),
    "DOM": CC.NumberColumn("DOM", format="%d", min_value=0),
    "Proximity (mi)": CC.NumberColumn("Proximity (mi)", format="%.2f", min_value=0),
    "Direction": CC.SelectboxColumn("Direction", options=DIRECTIONS[1:]),
    "GLA": CC.NumberColumn("GLA", format="%d"),
    "Concessions ($)": CC.NumberColumn("Concessions ($)", format="$%d"),
    "Financing": CC.SelectboxColumn("Financing", options=FINANCING[1:]),
    "Cond": CC.SelectboxColumn("Cond", options=RATING_C),
    "Qual": CC.SelectboxColumn("Qual", options=RATING_Q),
    "Weight": CC.SelectboxColumn("Weight", options=WEIGHTS),
    "Weight Rationale": CC.TextColumn("Weight Rationale", width="large"),
    **{c: CC.NumberColumn(c, format="$%d", step=1000) for c in ADJ_COLS},
}


def csv_import_ui(lid, ver_key, ver):
    up = st.file_uploader("MLS export (.csv)", type=["csv"], key=f"csv_{lid}_{ver}")
    if up is None:
        st.caption("Export your comps from the MLS as CSV — include contract date, close date, DOM and MLS #. "
                   "Columns are matched automatically; correct the match once and it is remembered for next time.")
        return
    try:
        raw = read_csv_upload(up.getvalue())
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not read that CSV: {exc}")
        return
    cols = list(raw.columns)
    guess = guess_mapping(cols, get_setting("csv_map", {}))
    parts_ok = bool(detect_address_parts(cols)["name"])
    sig = zlib.crc32("|".join(cols).encode())
    st.markdown(f"Found **{len(raw)}** rows. Confirm the column match:")
    chosen = {}
    grid = st.columns(3)
    for i, target in enumerate(CSV_TARGETS):
        opts = ["(none)"] + ([PARTS_OPT] if (target == "Address" and parts_ok) else []) + cols
        g = guess[target]
        default = "(none)" if g == "" else (PARTS_OPT if g == "__parts__" else g)
        sel = grid[i % 3].selectbox(target, opts, index=opts.index(default) if default in opts else 0,
                                    key=f"map_{lid}_{sig}_{target}")
        chosen[target] = "" if sel == "(none)" else ("__parts__" if sel == PARTS_OPT else sel)
    mode = st.radio("When importing", ["Add to existing comps", "Replace all comps"], horizontal=True,
                    key=f"impmode_{lid}")
    if st.button("Import comparables", key=f"imp_{lid}", type="primary"):
        try:
            new = build_comps_from_csv(raw, chosen)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Import failed: {exc}")
            return
        lead = get_lead(lid)
        if mode.startswith("Add"):
            have = normalize_comps(load_comps(lead))
            seen = {addr_key(a) for a in have["Address"]}
            new = new[~new["Address"].apply(addr_key).isin(seen)]
            combined = pd.concat([have, normalize_comps(new)], ignore_index=True)
        else:
            combined = normalize_comps(new)
        n_new = len(normalize_comps(new))
        norm_new = normalize_comps(new)
        miss_cd = int((norm_new["Status"].isin(["Settled Sale", "Pending"]) &
                       norm_new["Contract Date"].apply(lambda d: d is None)).sum())
        miss_mls = int((norm_new["MLS #"] == "").sum())
        set_setting("csv_map", chosen)
        update_lead(lid, log=f"Imported {n_new} comps from CSV ({mode.lower()})",
                    comps_json=json.dumps(comps_to_records(combined)))
        st.session_state[ver_key] = ver + 1
        note = f"Imported {n_new} comparable(s)."
        if miss_cd:
            note += f" {miss_cd} missing a contract date — check the export includes it."
        if miss_mls:
            note += f" {miss_mls} missing an MLS #."
        flash("success", note + " Set weights, ratings and adjustments next.")
        st.rerun()


def adjustment_helpers_ui(lid, lead, edited, ver_key, ver):
    st.caption("These fill the grid with suggestions from rates *you* enter. Review and edit every number — "
               "the support behind each adjustment is your professional judgment.")
    c1, c2 = st.columns(2)
    trate = c1.number_input("Market trend, % per month", value=float(lead.get("time_rate") or 0.0), step=0.1,
                            format="%.2f", key=f"trate_{lid}",
                            help="From your market analysis. Positive = appreciating market.")
    grate = c2.number_input("GLA adjustment rate, $ per SqFt", value=float(lead.get("gla_rate") or 0.0), step=5.0,
                            format="%.2f", key=f"grate_{lid}")
    eff = parse_iso(lead.get("effective_date"))
    use_contract = str(lead.get("time_basis") or TIME_BASES[0]).startswith("Contract")
    subj = int(lead.get("ansi_gla") or lead.get("tad_gla") or 0)
    b1, b2 = st.columns(2)
    if b1.button("Apply time adjustments", key=f"apptime_{lid}"):
        if not eff:
            st.error("Set the effective date on the Subject & Scope tab first.")
        else:
            new, done, skipped = suggest_time_adjustments(edited, eff, trate, use_contract)
            update_lead(lid, log=f"Time adjustments applied ({trate:.2f}%/mo)", time_rate=trate,
                        comps_json=json.dumps(comps_to_records(new)))
            st.session_state[ver_key] = ver + 1
            basis = "contract date" if use_contract else "sale date"
            flash("success", f"Time adjustments applied to {done} comp(s) from {basis} to {fmt_d(eff)}."
                  + (f" {skipped} skipped (no {basis} or price)." if skipped else ""))
            st.rerun()
    if b2.button("Apply GLA adjustments", key=f"appgla_{lid}"):
        if subj <= 0:
            st.error("Enter the subject's measured GLA on the Subject & Scope tab first.")
        else:
            new, done, skipped = suggest_gla_adjustments(edited, subj, grate)
            update_lead(lid, log=f"GLA adjustments applied (${grate:.2f}/SqFt)", gla_rate=grate,
                        comps_json=json.dumps(comps_to_records(new)))
            st.session_state[ver_key] = ver + 1
            flash("success", f"GLA adjustments applied to {done} comp(s) against {subj:,} SqFt."
                  + (f" {skipped} skipped (no GLA)." if skipped else ""))
            st.rerun()
    st.caption(f"Time basis: {'contract date' if use_contract else 'sale date'} · "
               f"Effective date: {fmt_d(eff) or 'not set'} · Subject GLA: {subj:,} SqFt")


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

    items = uad_readiness(lead)
    scored = [i for i in items if i["ok"] is not None]
    done = sum(1 for i in scored if i["ok"])
    st.progress(done / len(scored) if scored else 0.0,
                text=f"UAD 3.6 data readiness: {done} of {len(scored)} checks complete (details on the last tab)")

    t_over, t_subj, t_comps, t_ready = st.tabs(
        ["📌 Overview", "🏠 Subject & Scope", "📁 Comps & Adjustments", "✅ Readiness & Export"])
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
            p1, p2, p3 = st.columns(3)
            paid_on = p1.date_input("Payment received on (leave blank if unpaid)", value=parse_iso(lead.get("paid_date")),
                                    format="MM/DD/YYYY")
            paid_amt = p2.number_input("Amount received ($) — 0 uses the quoted fee", min_value=0,
                                       value=int(lead.get("paid_amount") or 0), step=25)
            hours = p3.number_input("Hours spent on this file", min_value=0.0, value=float(lead.get("hours") or 0),
                                    step=0.5, format="%.1f", help="Optional. Powers revenue-per-hour in Reports → Profit & Loss.")
            notes = st.text_area("Property notes & unique traits (manual appraiser entry)", value=lead["notes"] or "",
                                 height=120, help="Physical defects, unpermitted additions, custom features, access instructions.")
            saved = st.form_submit_button("💾 Save overview", type="primary")
        if saved:
            update_lead(lid, log="File details updated", client_name=name.strip(), phone=phone.strip(),
                        email=email.strip(), assignment_type=service, fee=int(fee), street=street.strip(),
                        city=city.strip(), zip=zip_.strip(), county=county, deadline=deadline.isoformat(),
                        source=source, notes=notes.strip(), hours=float(hours), paid_date=paid_on.isoformat() if paid_on else None,
                        paid_amount=((int(paid_amt) or int(fee)) if paid_on else None))
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

    # ---- Subject & scope ----
    with t_subj:
        county = lead.get("county") or "Tarrant"
        cad_name, cad_url = CAD_LINKS.get(county, (f"{county} CAD", None))
        if cad_url:
            st.markdown(f"Look up public records: [{cad_name}]({cad_url})")
        default_src = f"{cad_name} Public Records"
        c1, c2 = st.columns(2)
        with c1:
            with st.container(border=True):
                st.markdown("#### Assignment & inspection")
                eff = st.date_input("Effective date of appraisal", value=parse_iso(lead.get("effective_date")),
                                    key=f"eff_{lid}", format="MM/DD/YYYY")
                hint = EFFECTIVE_DATE_HINTS.get(lead["assignment_type"])
                if hint:
                    st.caption(hint)
                insp_cur = lead.get("inspection_type")
                insp = st.selectbox("Inspection type (per engagement)", INSPECTION_TYPES,
                                    index=INSPECTION_TYPES.index(insp_cur) if insp_cur in INSPECTION_TYPES else None,
                                    placeholder="Select…", key=f"insp_{lid}")
                insp_date = st.date_input("Inspection date", value=parse_iso(lead.get("inspection_date")),
                                          key=f"inspd_{lid}", format="MM/DD/YYYY")
                gse = st.checkbox("Lender / GSE-delivered assignment (UAD 3.6 mandatory)",
                                  value=bool(lead.get("gse_required")), key=f"gse_{lid}",
                                  help="UAD 3.6 is mandatory for appraisals delivered to Fannie Mae / Freddie Mac via UCDP "
                                       "from Nov 2, 2026. Non-lender work (probate, divorce, tax protest…) is not GSE-delivered, "
                                       "so unmet UAD checks show as warnings rather than errors.")
            with st.container(border=True):
                st.markdown("#### Disclosures (certification)")
                no_int = st.checkbox("No present or prospective interest in the property or personal interest in the parties",
                                     value=bool(lead.get("no_interest")), key=f"noint_{lid}")
                no_prior = st.checkbox("No services regarding this property in the prior 3 years, in any capacity "
                                       "(including brokerage)", value=bool(lead.get("no_prior_services")),
                                       key=f"noprior_{lid}")
                disc_notes = st.text_area("Disclosure note (required if either box is unchecked)",
                                          value=lead.get("disclosure_notes") or "", height=70, key=f"discn_{lid}")
                st.caption("As a licensed broker, check your brokerage history with this property and these parties. "
                           "Confirm the certification wording against your current USPAP edition.")
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
                st.markdown("#### Ratings (appraiser input)")
                cc_cur, qc_cur = rating_code(lead.get("condition_rating"), "C"), rating_code(lead.get("quality_rating"), "Q")
                cr = st.selectbox("Condition rating", RATING_C, index=RATING_C.index(cc_cur) if cc_cur else None,
                                  placeholder="Select…", key=f"cr_{lid}")
                qr = st.selectbox("Quality rating", RATING_Q, index=RATING_Q.index(qc_cur) if qc_cur else None,
                                  placeholder="Select…", key=f"qr_{lid}")
                st.caption("UAD 3.6 kept the C1–C6 / Q1–Q6 scales but rewrote the definitions, and adds separate "
                           "interior and exterior ratings. Apply the current definitions; interior / exterior "
                           "ratings are keyed in your appraisal software.")
                tb_cur = lead.get("time_basis")
                tb = st.selectbox("Time adjustment basis", TIME_BASES,
                                  index=TIME_BASES.index(tb_cur) if tb_cur in TIME_BASES else 0, key=f"tb_{lid}")
            mlog = st.text_area("Verified sales data log", value=lead.get("market_log") or "", height=150,
                                key=f"mlog_{lid}", placeholder="Comp 1: address | Sold $ | Confirmed via MLS on date")
            st.caption("🛡️ Brokerage market data is kept separate from appraisal valuation opinions.")
        if st.button("💾 Save subject & scope", key=f"save_subj_{lid}", type="primary"):
            update_lead(lid, log="Subject & scope updated", tad_gla=int(tad), ansi_gla=int(ansi), lot_size=lot,
                        assessed_value=av, tax_year=int(ty), records_source=src,
                        effective_date=eff.isoformat() if eff else None, inspection_type=insp,
                        inspection_date=insp_date.isoformat() if insp_date else None, gse_required=int(gse), no_interest=int(no_int),
                        no_prior_services=int(no_prior), disclosure_notes=disc_notes,
                        condition_rating=cr, quality_rating=qr, time_basis=tb, market_log=mlog)
            flash("success", "Subject & scope saved.")
            st.rerun()

    # ---- Comps & adjustments ----
    with t_comps:
        ver_key = f"compver_{lid}"
        ver = st.session_state.setdefault(ver_key, 0)
        have_comps = not normalize_comps(load_comps(lead)).empty

        with st.expander("📁 Import MLS comparable export (.csv)", expanded=not have_comps):
            csv_import_ui(lid, ver_key, ver)

        st.markdown("**Sales comparison grid** — one row per comparable (scroll right for adjustments). "
                    "Use the + at the bottom to add rows.")
        edited = st.data_editor(load_comps(lead), num_rows="dynamic", hide_index=True,
                                key=f"comp_editor_{lid}_{ver}", column_config=COMP_COLCFG)

        with st.expander("🧮 Adjustment helpers (suggestions only)"):
            adjustment_helpers_ui(lid, lead, edited, ver_key, ver)

        calc = compute_comps(edited)
        if calc.empty:
            st.info("No comparables entered yet.")
        else:
            show = calc[["Address", "Status", "Weight", "Sale Price", "Net Adj", "Net %", "Gross Adj", "Gross %",
                         "Adjusted Price", "Flags"]]
            st.dataframe(show, hide_index=True, column_config={
                "Sale Price": CC.NumberColumn(format="$%d"), "Net Adj": CC.NumberColumn(format="$%d"),
                "Gross Adj": CC.NumberColumn(format="$%d"), "Net %": CC.NumberColumn(format="%.1f%%"),
                "Gross %": CC.NumberColumn(format="%.1f%%"), "Adjusted Price": CC.NumberColumn(format="$%d"),
            })
            for _, r in calc[calc["Flags"] != ""].iterrows():
                st.warning(f"⚠️ **{r['Address'] or 'unnamed comp'}:** {r['Flags']}. Document the explanation.")
            st.caption("Gross > 25% / Net > 15% are traditional review flags, not a UAD 3.6 requirement as far as I "
                       "could confirm — use them as a prompt to document.")
            used = used_comps(calc)
            if not used.empty:
                lo, hi = used["Adjusted Price"].min(), used["Adjusted Price"].max()
                most = calc[calc["Weight"] == WEIGHTS[0]]
                m = st.columns(3)
                m[0].metric("Adjusted-price range (Most + Less weight)", f"${lo:,.0f} – ${hi:,.0f}")
                m[1].metric("Mean, Most Weight comps", f"${most['Adjusted Price'].mean():,.0f}" if not most.empty else "—")
                m[2].metric("Weighted / total comps", f"{len(used)} / {len(calc)}")
            else:
                st.caption("Set a weight (Most / Less) on at least one comp to see the adjusted-price range.")

        st.markdown("**Properties analyzed but not used**")
        unused_edit = st.data_editor(
            load_unused(lead), num_rows="dynamic", hide_index=True, key=f"unused_{lid}",
            column_config={
                "Address": CC.TextColumn("Address", width="medium"),
                "Status": CC.SelectboxColumn("Status", options=LIST_STATUSES),
                "Reason Not Used": CC.SelectboxColumn("Reason Not Used", options=UNUSED_REASONS, width="medium"),
                "Comment": CC.TextColumn("Comment (required)", width="large"),
            })
        st.caption("UAD 3.6 keeps these in their own table with a reason and a comment. The reason list here is a "
                   "short set of common reasons — match it to your software's list when you key it in.")

        st.markdown("**Reconciliation**")
        tsupport = st.text_area("Time-adjustment support (data source, tool, technique)",
                                value=lead.get("time_support") or "", height=80, key=f"tsup_{lid}",
                                placeholder="e.g., paired sales / HPI / regression on NTREIS closed sales, with the dates analyzed")
        recon = st.text_area("Reconciliation narrative (should match the weights above)",
                             value=lead.get("reconciliation_notes") or "", height=110, key=f"recon_{lid}")
        ov = st.number_input("Opinion of value ($) — the appraiser's conclusion; 0 = not yet set", min_value=0,
                             value=int(lead.get("opinion_value") or 0), step=1000, key=f"ov_{lid}")
        if st.button("💾 Save comps & reconciliation", key=f"save_comps_{lid}", type="primary"):
            n = len(normalize_comps(edited))
            update_lead(lid, log=f"Comps & reconciliation saved ({n} comps)",
                        comps_json=json.dumps(comps_to_records(edited)),
                        unused_json=json.dumps(normalize_unused(unused_edit).to_dict("records")),
                        time_support=tsupport, reconciliation_notes=recon, opinion_value=int(ov))
            flash("success", "Comps and reconciliation saved.")
            st.rerun()
        st.caption("Grid edits are only stored when you press Save (the helpers and CSV import save the grid for you).")

    # ---- Readiness & export ----
    with t_ready:
        fresh = get_lead(lid)
        items = uad_readiness(fresh)
        strict = bool(fresh.get("gse_required"))
        if strict:
            st.error("Flagged as lender / GSE-delivered — UAD 3.6 data is required for this file.")
        else:
            st.caption("Not flagged as lender / GSE-delivered, so open items show as ⚠️ warnings. "
                       "UAD 3.6 became mandatory for GSE (UCDP) submissions on Nov 2, 2026.")
        groups = []
        for it in items:
            if it["group"] not in groups:
                groups.append(it["group"])
        for g in groups:
            st.markdown(f"**{g}**")
            for it in [i for i in items if i["group"] == g]:
                icon = "✅" if it["ok"] else ("ℹ️" if it["ok"] is None else ("❌" if strict else "⚠️"))
                st.markdown(f"{icon} {it['label']}" + (f" — _{it['detail']}_" if it["detail"] and it["ok"] is not True else ""))
        st.caption("Not tracked here (key these in your appraisal software): transfer terms, site / view / influence, "
                   "property rights, interior / exterior ratings, highest & best use, market narrative, photos.")

        st.markdown("---")
        st.markdown("### Exports (use the **saved** version of this file)")
        saved_calc = compute_comps(load_comps(fresh))
        saved_unused = normalize_unused(load_unused(fresh))
        md = build_markdown(fresh, saved_calc, saved_unused, items)
        js = build_json(fresh, saved_calc, saved_unused, items)
        vc = build_vcard(fresh)
        cs = comps_csv(saved_calc)
        base = slug((fresh["client_name"] or "").lower())
        d1, d2, d3, d4 = st.columns(4)
        d1.download_button("📄 Audit report (.md)", md, file_name=f"{base}_audit_summary.md", mime="text/markdown")
        d2.download_button("⚙️ UAD data sheet (.json)", js, file_name=f"{base}_uad36_data_sheet.json", mime="application/json")
        d3.download_button("📊 Comp grid (.csv)", cs, file_name=f"{base}_comp_grid.csv", mime="text/csv")
        d4.download_button("📇 Contact vCard (.vcf)", vc, file_name=f"{base}_contact.vcf", mime="text/vcard")
        st.caption("The JSON is a working data sheet for keying into your appraisal software — it is not a UCDP / MISMO XML file.")
        if st.button("💾 Save local backup folder", key=f"bk_{lid}"):
            folder = f"{BACKUP_ROOT}/{date.today().isoformat()}_{slug(fresh['client_name'])}_{slug(fresh['street'])}"
            os.makedirs(f"{folder}/photos_and_sketches", exist_ok=True)
            for fname, content in [(f"{base}_contact.vcf", vc), (f"{base}_uad36_data_sheet.json", js),
                                   (f"{base}_audit_summary.md", md), (f"{base}_comp_grid.csv", cs)]:
                with open(f"{folder}/{fname}", "w", encoding="utf-8") as fh:
                    fh.write(content)
            log_activity(lid, f"Backup folder saved: {folder}")
            st.success(f"Saved to `{folder}/`")


# ----------------------------------------------------------------------
# REPORTS PAGE
# ----------------------------------------------------------------------
def page_reports():
    st.title("📈 Reports")
    df = list_leads()
    if df.empty:
        st.info("No files yet. Add leads (or load sample leads from the sidebar) to see reports.")
        return
    rf = report_frame(df)
    today = date.today()

    pc1, pc2 = st.columns([1, 2])
    period = pc1.selectbox("Period", PERIODS, index=PERIODS.index("Year to date"), key="rep_period")
    custom = None
    if period == "Custom":
        a, b = pc2.columns(2)
        custom = (a.date_input("From", value=today - timedelta(days=90), key="rep_from", format="MM/DD/YYYY"),
                  b.date_input("To", value=today, key="rep_to", format="MM/DD/YYYY"))
    s, e = period_bounds(period, today, custom)
    pc2.caption(f"Showing {fmt_d(s) if s.year > 1990 else 'all records'} → {fmt_d(e)}. “Earned” counts files by the date "
                "they were delivered; “Collected” counts payments by the date received.")

    t_biz, t_ar, t_pnl, t_comp, t_exp = st.tabs(["📊 Business", "💵 Receivables", "💼 Profit & Loss", "🛡️ Compliance & Records", "📤 Export"])

    # ---------------- Business ----------------
    with t_biz:
        k = business_kpis(rf, s, e)
        r1 = st.columns(4)
        r1[0].metric("Earned (delivered)", _money(k["earned"]))
        r1[1].metric("Collected", _money(k["collected"]))
        r1[2].metric("Files delivered", k["delivered_n"])
        r1[3].metric("Average fee", _money(k["avg_fee"]))
        r2 = st.columns(5)
        r2[0].metric("New leads", k["new_leads"])
        r2[1].metric("Win rate", _pct(k["win_rate"]), help="Won ÷ (won + lost) among leads created in the period. "
                                                          "Quotes still awaiting a decision are not counted.")
        r2[2].metric("Avg turnaround", _days(k["avg_turnaround"]), help="Engaged → delivered.")
        r2[3].metric("On-time delivery", _pct(k["on_time"]), help="Delivered on or before the target delivery date.")
        r2[4].metric("Avg time to quote", _days(k["avg_days_to_quote"]))

        snap = pipeline_snapshot(rf, today)
        st.markdown("**Pipeline today**")
        p = st.columns(3)
        p[0].metric("Quotes awaiting decision", f"{snap['quoted_n']} · {_money(snap['quoted_v'])}")
        p[1].metric("Booked / in progress", f"{snap['booked_n']} · {_money(snap['booked_v'])}")
        p[2].metric("Delivered, unpaid", f"{snap['ar_n']} · {_money(snap['ar_v'])}")

        trend = monthly_trend(rf, s, e)
        st.markdown("**Monthly trend**")
        if trend.sum().sum() > 0:
            st.bar_chart(trend, stack=False)
        else:
            st.caption("No earned or collected revenue in this period yet.")

        dim = st.radio("Break down by", ["Service", "County", "Lead source"], horizontal=True, key="rep_dim")
        col = {"Service": "service", "County": "county", "Lead source": "source"}[dim]
        bd = breakdown(rf, s, e, col, dim)
        st.dataframe(bd, hide_index=True, column_config={
            "Win rate": st.column_config.NumberColumn(format="%.0f%%"),
            "Earned": st.column_config.NumberColumn(format="$%d"),
            "Avg fee": st.column_config.NumberColumn(format="$%d")})
        st.caption("Lead-source win rates show which channels are worth your time — a source with many leads and few wins "
                   "is a candidate for less effort.")

        st.markdown("**Follow-ups: leads idle 3+ days**")
        fu = followup_list(rf, today)
        if fu.empty:
            st.success("Nothing waiting — every inquiry and quote has been touched in the last 3 days.")
        else:
            st.dataframe(fu.rename(columns={"id": "File #", "client": "Client", "phone": "Phone", "service": "Service",
                                            "status": "Status", "fee": "Quoted fee", "idle": "Idle days"}),
                         hide_index=True, column_config={"Quoted fee": st.column_config.NumberColumn(format="$%d")})

        st.markdown(f"**Collected by quarter, {today.year}** (for estimated-tax planning; this is not tax advice)")
        st.dataframe(quarterly_collected(rf, today.year), hide_index=True,
                     column_config={"Collected": st.column_config.NumberColumn(format="$%d")})

    # ---------------- Receivables ----------------
    with t_ar:
        ar = receivables(rf, today)
        a = st.columns(4)
        a[0].metric("Outstanding", _money(int(ar["fee"].sum())))
        a[1].metric("Unpaid invoices", len(ar))
        a[2].metric("Oldest", f"{int(ar['days'].max())} days" if len(ar) else "—")
        a[3].metric("Collected in period", _money(business_kpis(rf, s, e)["collected"]))
        if ar.empty:
            st.success("No outstanding invoices — everything delivered has been paid.")
        else:
            st.markdown("**Aging**")
            st.dataframe(aging_table(ar), hide_index=True,
                         column_config={"Amount": st.column_config.NumberColumn(format="$%d")})
            st.markdown("**Delivered, not yet paid** — check the files that have paid, then record them.")
            view = pd.DataFrame({
                "File #": ar["id"], "Client": ar["client"], "Property": ar["street"], "Service": ar["service"],
                "Fee": ar["fee"], "Delivered": [fmt_d(d) for d in ar["delivered"]], "Days": ar["days"],
                "Paid": False})
            edited = st.data_editor(view, hide_index=True, key="ar_editor",
                                    disabled=[c for c in view.columns if c != "Paid"],
                                    column_config={"Fee": st.column_config.NumberColumn(format="$%d"),
                                                   "Paid": st.column_config.CheckboxColumn("Paid ✓")})
            pay_date = st.date_input("Payment received on", value=today, key="ar_date", format="MM/DD/YYYY")
            if st.button("💾 Record payments for checked files", type="primary"):
                checked = edited[edited["Paid"]]
                for _, r in checked.iterrows():
                    update_lead(int(r["File #"]), paid_date=pay_date.isoformat(), paid_amount=int(r["Fee"]),
                                log=f"Payment received ${int(r['Fee']):,} on {fmt_d(pay_date)}")
                flash("success" if len(checked) else "warning",
                      f"Recorded {len(checked)} payment(s)." if len(checked) else "Nothing was checked.")
                st.rerun()
            st.caption("Different amount or a partial payment? Enter it on the file's Overview tab (Payment received).")

    # ---------------- Profit & loss ----------------
    with t_pnl:
        basis_lbl = st.radio("Revenue basis", ["Cash — payments received", "Accrual — files delivered"],
                             horizontal=True, key="pnl_basis")
        basis = "cash" if basis_lbl.startswith("Cash") else "accrual"
        exp, mil = load_expenses(), load_mileage()
        p = pnl(rf, exp, mil, s, e, basis)
        pm = st.columns(4)
        pm[0].metric("Revenue", _money(p["revenue"]), help=f"{p['files']} file(s) in the period.")
        pm[1].metric("Expenses", _money(p["expenses"]))
        pm[2].metric("Mileage deduction", _money(p["mileage"]), help=f"{p['miles']:,.1f} miles.")
        pm[3].metric("Net profit", _money(p["net"]), delta=None if p["margin"] is None else f"{p['margin'] * 100:.0f}% margin")
        if not len(exp) and not len(mil):
            st.info("No expenses or mileage logged yet — add them on the 🧾 Expenses & Mileage page and this becomes "
                    "a real profit-and-loss view.")
        trend = monthly_pnl(rf, exp, mil, s, e, basis)
        if trend[["Revenue", "Costs"]].sum().sum() > 0:
            st.markdown("**Revenue vs. costs by month**")
            st.bar_chart(trend[["Revenue", "Costs"]], stack=False)
        if len(p["by_category"]):
            st.markdown("**Expenses by category**")
            bc = p["by_category"].reset_index()
            bc.columns = ["Category", "Total"]
            st.dataframe(bc, hide_index=True, column_config={"Total": st.column_config.NumberColumn(format="$%.2f")})
        st.markdown("**Profitability by service** (delivered files in the period)")
        sp = service_profitability(rf, exp, mil, s, e)
        if sp.empty:
            st.caption("No delivered files in this period.")
        else:
            st.dataframe(sp, hide_index=True, column_config={
                "Revenue": st.column_config.NumberColumn(format="$%d"),
                "Direct costs": st.column_config.NumberColumn(format="$%.2f"),
                "Net": st.column_config.NumberColumn(format="$%.2f"),
                "Revenue / hour": st.column_config.NumberColumn(format="$%.0f"),
                "Net / hour": st.column_config.NumberColumn(format="$%.0f")})
            st.caption("Direct costs are expenses and mileage you linked to that file. Enter hours spent on each file's "
                       "Overview tab to see revenue and net per hour — the quickest way to learn which assignment types "
                       "actually pay. General overhead (insurance, dues, MLS) is not allocated to files.")
        st.caption("Bookkeeping summary, not tax advice. Cash basis counts payments when received; accrual counts files "
                   "when delivered — your accountant can tell you which your books should use.")

    # ---------------- Compliance & records ----------------
    with t_comp:
        st.markdown("#### Interest and prior-services disclosures")
        st.caption("For every engaged or delivered file: confirm you have no present or prospective interest in the property "
                   "or parties, and no services on the property in the prior 3 years in any capacity — which includes "
                   "brokerage work. If either is not true, record the disclosure note instead. Set these on each file's "
                   "Subject & Scope tab. Confirm the exact certification wording against your current USPAP edition.")
        dt = disclosure_table(rf)
        need = dt[dt["Needs attention"]] if len(dt) else dt
        if dt.empty:
            st.info("No engaged files yet.")
        elif need.empty:
            st.success("Every engaged or delivered file has its disclosures addressed.")
        else:
            st.warning(f"{len(need)} file(s) need a disclosure confirmation or note.")
            st.dataframe(need.drop(columns="Needs attention"), hide_index=True)

        st.markdown("#### Workfile retention")
        st.caption("USPAP's Record Keeping Rule: keep the workfile for the longer of 5 years after preparation, or 2 years "
                   "after final disposition of any judicial proceeding in which you gave testimony. State enforcement and "
                   "civil-liability periods can run longer, so treat this as a minimum and confirm against current USPAP and "
                   "TALCB rules. Nothing is deleted by this app.")
        rt = retention_table(rf, today)
        if rt.empty:
            st.info("No delivered files yet.")
        else:
            st.dataframe(rt, hide_index=True)

        st.markdown("#### Engaged files not yet UAD-ready")
        rows = []
        for rec in df.to_dict("records"):
            if rec["status"] in ("Engaged", "In Progress"):
                d, t = readiness_counts(rec)
                if d < t:
                    rows.append({"File #": rec["id"], "Client": rec["client_name"], "Property": rec["street"],
                                 "UAD checks": f"{d}/{t}", "Due": due_label(rec["deadline"], rec["status"])})
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True)
        else:
            st.success("No engaged files with open UAD checks.")

    # ---------------- Export ----------------
    with t_exp:
        label = period if period != "Custom" else f"{fmt_d(s)}–{fmt_d(e)}"
        slug_label = slug(label.lower())
        acct = accountant_rows(rf, s, e)
        st.markdown(f"**Accountant / bookkeeping export — {label}**  ·  {len(acct)} file(s) delivered or paid in the period")
        if len(acct):
            st.dataframe(acct, hide_index=True)
        c1, c2, c3 = st.columns(3)
        c1.download_button("📊 Files & payments (.csv)", acct.to_csv(index=False),
                           file_name=f"files_and_payments_{slug_label}.csv", mime="text/csv")
        c2.download_button("📈 Monthly summary (.csv)", monthly_trend(rf, s, e).to_csv(index_label="Month"),
                           file_name=f"monthly_summary_{slug_label}.csv", mime="text/csv")
        _exp, _mil = load_expenses(), load_mileage()
        c3.download_button("📄 Business report + P&L (.md)", report_markdown(rf, s, e, label, today, _exp, _mil),
                           file_name=f"business_report_{slug_label}.md", mime="text/markdown")
        _ex, _mi = _dsel(_exp, s, e), _dsel(_mil, s, e)
        d1, d2 = st.columns(2)
        d1.download_button("🧾 Expenses (.csv)", expenses_export(_ex).to_csv(index=False),
                           file_name=f"expenses_{slug_label}.csv", mime="text/csv")
        d2.download_button("🚗 Mileage log (.csv)", mileage_export(_mi).to_csv(index=False),
                           file_name=f"mileage_log_{slug_label}.csv", mime="text/csv")
        st.caption("Revenue is shown by delivery date and by payment date. Which one your books use depends on your "
                   "accounting method — ask your accountant.")


# ----------------------------------------------------------------------
# EXPENSES & MILEAGE PAGE
# ----------------------------------------------------------------------
def expenses_export(exp):
    return pd.DataFrame({
        "Date": [fmt_d(x) for x in exp["date"]], "Category": exp["category"], "Vendor": exp["vendor"],
        "Description": exp["description"], "Amount": exp["amount"],
        "File #": exp["lead_id"].astype("Int64"), "Receipt on file": ["Yes" if r else "No" for r in exp["receipt"]]})


def mileage_export(mil):
    return pd.DataFrame({
        "Date": [fmt_d(x) for x in mil["date"]], "Miles": mil["miles"], "Rate ($/mi)": mil["rate"],
        "Deduction ($)": mil["deduction"], "Origin": mil["origin"], "Destination": mil["destination"],
        "Business purpose": mil["purpose"], "File #": mil["lead_id"].astype("Int64")})


def _year_mask(df, yr):
    """Boolean mask for rows dated in `yr` (safe on an empty table)."""
    return pd.Series([d is not None and d.year == yr for d in df["date"]], index=df.index, dtype=bool)


def page_books():
    st.title("🧾 Expenses & Mileage")
    st.caption("Log business costs and drives here and they flow into Reports → Profit & Loss. "
               "Link a cost to a file to see what each assignment really nets.")
    leads = list_leads()
    leads = leads.sort_values("id", ascending=False) if len(leads) else leads
    labels = {0: "— none —"}
    for r in leads.itertuples():
        labels[int(r.id)] = f"#{r.id} · {r.client_name} · {r.street}"
    file_ids = list(labels)
    t_exp, t_mil = st.tabs(["🧾 Expenses", "🚗 Mileage log"])
    CC = st.column_config

    # ---------------- Expenses ----------------
    with t_exp:
        with st.form("exp_form", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            d = c1.date_input("Date", value=date.today(), format="MM/DD/YYYY")
            cat = c2.selectbox("Category", EXPENSE_CATEGORIES)
            amt = c3.number_input("Amount ($)", min_value=0.0, value=0.0, step=5.0, format="%.2f")
            c4, c5 = st.columns(2)
            vendor = c4.text_input("Vendor")
            desc = c5.text_input("Description")
            c6, c7, c8 = st.columns(3)
            fid = c6.selectbox("Link to a file (optional)", file_ids, format_func=lambda i: labels[i])
            repeat = c7.number_input("Repeat monthly for … months (1 = once)", min_value=1, max_value=24, value=1,
                                     help="For subscriptions and dues: creates one entry per month.")
            receipt = c8.checkbox("Receipt on file")
            ok = st.form_submit_button("➕ Add expense", type="primary")
        if ok:
            if amt <= 0:
                st.error("Enter an amount greater than zero.")
            else:
                for i in range(int(repeat)):
                    add_expense(add_months(d, i), cat, vendor.strip(), desc.strip(), amt, fid or None, receipt)
                flash("success", f"Added {int(repeat)} expense entr{'y' if repeat == 1 else 'ies'} ({_money(amt)} each).")
                st.rerun()

        exp = load_expenses()
        years = sorted({x.year for x in exp["date"] if x} | {date.today().year}, reverse=True)
        yr = st.selectbox("Year", years, key="exp_year")
        sub = exp[_year_mask(exp, yr)]
        m = st.columns(3)
        m[0].metric(f"Total {yr}", _money(float(sub["amount"].sum())))
        m[1].metric("Entries", len(sub))
        m[2].metric("Missing receipts", int((~sub["receipt"]).sum()) if len(sub) else 0)
        if sub.empty:
            st.info(f"No expenses logged for {yr} yet.")
        else:
            view = pd.DataFrame({
                "id": sub["id"].values, "Date": pd.to_datetime(sub["date"]).values, "Category": sub["category"].values,
                "Vendor": sub["vendor"].fillna("").values, "Description": sub["description"].fillna("").values,
                "Amount": sub["amount"].values, "File #": sub["lead_id"].astype("Int64").values,
                "Receipt": sub["receipt"].values, "Delete": False})
            edited = st.data_editor(
                view, hide_index=True, key=f"exp_ed_{yr}", disabled=["id", "File #"],
                column_config={"id": None, "Date": CC.DateColumn("Date", format="MM/DD/YYYY"),
                               "Category": CC.SelectboxColumn("Category", options=EXPENSE_CATEGORIES),
                               "Amount": CC.NumberColumn("Amount", format="$%.2f", min_value=0.0),
                               "Receipt": CC.CheckboxColumn("Receipt ✓"), "Delete": CC.CheckboxColumn("Delete")})
            if st.button("💾 Save changes (edits and deletes)", key="exp_save"):
                upd, dele = save_book_edits("expenses", edited, {
                    "date": lambda r: (to_date(r["Date"]) or date.today()).isoformat(),
                    "category": lambda r: r["Category"], "vendor": lambda r: r["Vendor"] or "",
                    "description": lambda r: r["Description"] or "",
                    "amount": lambda r: round(float(r["Amount"] or 0), 2), "receipt": lambda r: int(bool(r["Receipt"]))})
                flash("success", f"Saved. {dele} deleted.")
                st.rerun()
            cat_tbl = sub.groupby("category")["amount"].sum().sort_values(ascending=False).reset_index()
            cat_tbl.columns = ["Category", "Total"]
            st.markdown(f"**{yr} by category**")
            st.dataframe(cat_tbl, hide_index=True, column_config={"Total": CC.NumberColumn(format="$%.2f")})
        st.caption("Bookkeeping record only — ask your accountant what is deductible and how.")

    # ---------------- Mileage ----------------
    with t_mil:
        base_rate = default_mileage_rate(date.today())
        st.caption(f"Default rate: {base_rate * 100:.1f}¢/mile — the IRS standard business mileage rate for "
                   f"{date.today().year}. Each trip keeps the rate it was logged with. Confirm with your accountant "
                   "whether standard mileage or actual vehicle expenses is right for you.")
        with st.form("mil_form", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            md_ = c1.date_input("Date", value=date.today(), format="MM/DD/YYYY", key="mil_date")
            miles = c2.number_input("Miles (one way)", min_value=0.0, value=0.0, step=0.5, format="%.1f")
            rt = c3.checkbox("Round trip (doubles the miles)", value=True)
            c4, c5 = st.columns(2)
            origin = c4.text_input("From (optional)", placeholder="Office")
            dest = c5.text_input("To", placeholder="Leave blank to use the linked file's address")
            c6, c7, c8 = st.columns(3)
            purpose = c6.selectbox("Business purpose", MILEAGE_PURPOSES)
            mfid = c7.selectbox("Link to a file", file_ids, format_func=lambda i: labels[i], key="mil_fid")
            rate = c8.number_input("Rate ($/mile)", min_value=0.0, value=float(base_rate), step=0.005, format="%.3f")
            mok = st.form_submit_button("➕ Log trip", type="primary")
        if mok:
            if miles <= 0:
                st.error("Enter the miles driven.")
            else:
                total = miles * 2 if rt else miles
                use_rate = rate if abs(rate - base_rate) > 1e-9 else default_mileage_rate(md_)
                to_txt = dest.strip() or (full_address(get_lead(mfid)) if mfid else "")
                add_trip(md_, total, use_rate, origin.strip(), to_txt, purpose, mfid or None)
                flash("success", f"Logged {total:,.1f} miles — {_money(total * use_rate)} at {use_rate * 100:.1f}¢.")
                st.rerun()

        mil = load_mileage()
        myears = sorted({x.year for x in mil["date"] if x} | {date.today().year}, reverse=True)
        myr = st.selectbox("Year", myears, key="mil_year")
        msub = mil[_year_mask(mil, myr)]
        mm = st.columns(3)
        mm[0].metric(f"Miles {myr}", f"{float(msub['miles'].sum()):,.1f}")
        mm[1].metric("Deduction", _money(float(msub["deduction"].sum())))
        mm[2].metric("Trips", len(msub))
        if msub.empty:
            st.info(f"No trips logged for {myr} yet.")
        else:
            mview = pd.DataFrame({
                "id": msub["id"].values, "Date": pd.to_datetime(msub["date"]).values, "Miles": msub["miles"].values,
                "Rate": msub["rate"].values, "Deduction": msub["deduction"].values,
                "From": msub["origin"].fillna("").values, "To": msub["destination"].fillna("").values,
                "Purpose": msub["purpose"].fillna("").values, "File #": msub["lead_id"].astype("Int64").values,
                "Delete": False})
            medited = st.data_editor(
                mview, hide_index=True, key=f"mil_ed_{myr}", disabled=["id", "Deduction", "File #"],
                column_config={"id": None, "Date": CC.DateColumn("Date", format="MM/DD/YYYY"),
                               "Miles": CC.NumberColumn("Miles", format="%.1f", min_value=0.0),
                               "Rate": CC.NumberColumn("Rate", format="$%.3f", min_value=0.0),
                               "Deduction": CC.NumberColumn("Deduction", format="$%.2f"),
                               "Purpose": CC.SelectboxColumn("Purpose", options=MILEAGE_PURPOSES),
                               "Delete": CC.CheckboxColumn("Delete")})
            if st.button("💾 Save changes (edits and deletes)", key="mil_save"):
                upd, dele = save_book_edits("mileage", medited, {
                    "date": lambda r: (to_date(r["Date"]) or date.today()).isoformat(),
                    "miles": lambda r: round(float(r["Miles"] or 0), 1), "rate": lambda r: float(r["Rate"] or 0),
                    "origin": lambda r: r["From"] or "", "destination": lambda r: r["To"] or "",
                    "purpose": lambda r: r["Purpose"] or ""})
                flash("success", f"Saved. {dele} deleted.")
                st.rerun()
        st.caption("A mileage log generally needs the date, miles, destination and business purpose for each trip "
                   "(IRS recordkeeping) — this log captures all four. Deduction shown is miles × the rate.")


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
def require_password():
    """Optional login gate. Active only when an `app_password` secret is configured
    (Streamlit Cloud: App settings -> Secrets; locally: .streamlit/secrets.toml)."""
    try:
        pw = st.secrets.get("app_password")
    except Exception:  # noqa: BLE001  (no secrets file at all)
        pw = None
    if not pw or st.session_state.get("_authed"):
        return
    st.title("🔒 Appraisal Desk")
    with st.form("login"):
        entered = st.text_input("Password", type="password")
        ok = st.form_submit_button("Sign in", type="primary")
    if ok:
        if hmac.compare_digest(entered.encode(), str(pw).encode()):
            st.session_state["_authed"] = True
            st.rerun()
        st.error("Incorrect password.")
    st.stop()


def main():
    st.set_page_config(page_title="Appraisal Desk | Clearfork Appraisals", page_icon="🏠", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    require_password()
    init_db()

    if "_goto" in st.session_state:
        st.session_state["page"] = st.session_state.pop("_goto")
    st.session_state.setdefault("page", PAGE_PIPELINE)

    with st.sidebar:
        st.header("⚖️ Appraisal Desk")
        st.caption("Clearfork Appraisals")
        st.text(f"{APPRAISER['name']}")
        st.text(f"Appraiser: TX {APPRAISER['license']}")
        st.text(f"Broker: TX {APPRAISER['broker_license']}")
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
            n_sim = int((leads["source"] == SIM_SOURCE).sum())
            if n_sim:
                with st.expander(f"Demo data ({n_sim} simulated)"):
                    sure = st.checkbox("Delete all simulated leads", key="sim_del_chk")
                    if st.button("🧹 Remove simulated leads", disabled=not sure):
                        flash("success", f"Removed {delete_simulated()} simulated lead(s).")
                        st.rerun()
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
    elif page == PAGE_REPORTS:
        page_reports()
    elif page == PAGE_BOOKS:
        page_books()
    else:
        page_fees()


main()
