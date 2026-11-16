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

APP_VERSION = "v2.5"
DB_PATH = os.environ.get("APPRAISAL_DB", "appraisal_desk.db")
BACKUP_ROOT = "Appraisal_Files_Active"

APPRAISER = {
    "name": "Terrence Bilodeau",
    "designation": "Texas Certified Residential Real Estate Appraiser",
    "license": "1360232-CR",
    "broker_designation": "Texas Real Estate Broker",
    "broker_license": "0686157",          # as supplied by the user -- verify against TREC
    "firm": "Clearfork Appraisals",
    "address": "7725 Whisterwheel Way, Fort Worth, TX 76123",   # public business listing
    "phone": "(817) 903-4509",
    "website": "clearforkappraisals.com",
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
    "facts_json": "TEXT", "report_json": "TEXT",
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


def init_db():  # cheap and idempotent: runs on every load so code updates always migrate the DB
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
        c.execute("""CREATE TABLE IF NOT EXISTS narratives (
            id INTEGER PRIMARY KEY AUTOINCREMENT, section TEXT, title TEXT, body TEXT, created_at TEXT)""")
        if c.execute("SELECT COUNT(*) FROM fees").fetchone()[0] == 0:
            for i, (svc, fee) in enumerate(DEFAULT_FEES.items()):
                c.execute("INSERT INTO fees (service, fee, sort) VALUES (?,?,?)", (svc, fee, i))
    seed_narratives_once()
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
            return False, "Set a quoted fee before engaging this file (Working Files → Overview)."
        if not parse_iso(lead.get("deadline")):
            return False, "Set a firm delivery date before engaging this file (Working Files → Overview)."
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
*{APPRAISER['firm']} · {APPRAISER['address']} · {APPRAISER['phone']}*
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
**{APPRAISER['firm']}** · {APPRAISER['name']} · {APPRAISER['address']} · {APPRAISER['phone']}
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



# ======================================================================
# DASHBOARD DATA
# ======================================================================
# Approximate city-center coordinates (lat, lon) for every city the app recognises.
CITY_COORDS = {
    "fort worth": (32.7555, -97.3308), "arlington": (32.7357, -97.1081), "southlake": (32.9412, -97.1342),
    "keller": (32.9346, -97.2517), "colleyville": (32.8809, -97.1500), "grapevine": (32.9343, -97.0781),
    "north richland hills": (32.8343, -97.2289), "richland hills": (32.8123, -97.2286), "hurst": (32.8235, -97.1706),
    "euless": (32.8371, -97.0820), "bedford": (32.8441, -97.1431), "mansfield": (32.5632, -97.1417),
    "burleson": (32.5421, -97.3208), "haltom city": (32.7996, -97.2692), "watauga": (32.8579, -97.2547),
    "benbrook": (32.6732, -97.4606), "saginaw": (32.8601, -97.3636), "azle": (32.8946, -97.5428),
    "crowley": (32.5790, -97.3625), "haslet": (32.9640, -97.3392), "white settlement": (32.7596, -97.4589),
    "river oaks": (32.7773, -97.3950), "lake worth": (32.8068, -97.4339), "kennedale": (32.6468, -97.2250),
    "forest hill": (32.6724, -97.2670), "everman": (32.6307, -97.2897), "westworth village": (32.7568, -97.4039),
    "pantego": (32.7143, -97.1636), "dalworthington gardens": (32.7060, -97.1569), "blue mound": (32.8618, -97.3292),
    "weatherford": (32.7593, -97.7973), "aledo": (32.6962, -97.6022), "willow park": (32.7515, -97.6522),
    "hudson oaks": (32.7507, -97.7114), "springtown": (32.9693, -97.6842), "annetta": (32.7165, -97.6836),
    "cleburne": (32.3476, -97.3867), "joshua": (32.4615, -97.3892), "alvarado": (32.4068, -97.2120),
    "keene": (32.3965, -97.3225),
    "denton": (33.2148, -97.1331), "lewisville": (33.0462, -96.9942), "flower mound": (33.0146, -97.0970),
    "roanoke": (33.0040, -97.2259), "trophy club": (32.9987, -97.1836), "justin": (33.0848, -97.2967),
    "argyle": (33.1210, -97.1833), "northlake": (33.0968, -97.2614), "corinth": (33.1540, -97.0647),
    "highland village": (33.0912, -97.0467), "the colony": (33.0890, -96.8864), "frisco": (33.1507, -96.8236),
    "dallas": (32.7767, -96.7970), "irving": (32.8140, -96.9489), "grand prairie": (32.7460, -96.9978),
    "garland": (32.9126, -96.6389), "richardson": (32.9483, -96.7299), "duncanville": (32.6518, -96.9083),
    "desoto": (32.5899, -96.8570), "mesquite": (32.7668, -96.5992),
    "decatur": (33.2343, -97.5864), "bridgeport": (33.2101, -97.7536), "granbury": (32.4421, -97.7942),
    "waxahachie": (32.3865, -96.8483),
}
COUNTY_COORDS = {"Tarrant": (32.77, -97.29), "Parker": (32.78, -97.80), "Johnson": (32.38, -97.37),
                 "Denton": (33.21, -97.13), "Dallas": (32.77, -96.80), "Wise": (33.22, -97.65),
                 "Hood": (32.44, -97.79), "Ellis": (32.36, -96.80)}
PIPELINE_STATUSES = ["Inquiry", "Fee Quoted", "Engaged", "In Progress"]
MAP_VIEWS = {"In the pipeline": PIPELINE_STATUSES, "Delivered work": EARNED,
             "All (except lost)": PIPELINE_STATUSES + EARNED}
MAP_COLORS = {"In the pipeline": "#1f77b4", "Delivered work": "#2ca02c", "All (except lost)": "#9467bd"}


def lead_place(city, county):
    """(label, (lat, lon)) for a file; city centre if known, else the county centre, else None."""
    c = (city or "").strip().lower()
    if c in CITY_COORDS:
        return c.title(), CITY_COORDS[c]
    cy = (county or "").strip()
    if cy in COUNTY_COORDS:
        return f"{cy} County (no city match)", COUNTY_COORDS[cy]
    return None, None


def map_points(df, view="In the pipeline"):
    """One row per place with the number of files and fee value. Returns (points, n_unplaced)."""
    sel = df[df["status"].isin(MAP_VIEWS[view])]
    agg, unplaced = {}, 0
    for r in sel.itertuples():
        label, xy = lead_place(r.city, r.county)
        if not xy:
            unplaced += 1
            continue
        a = agg.setdefault(label, {"Place": label, "Files": 0, "Fees": 0, "lat": xy[0], "lon": xy[1]})
        a["Files"] += 1
        a["Fees"] += int(r.fee or 0)
    pts = pd.DataFrame(list(agg.values()), columns=["Place", "Files", "Fees", "lat", "lon"])
    if len(pts):
        pts["size"] = (1800 + 1400 * pts["Files"]).clip(upper=9000)
        pts["color"] = MAP_COLORS[view]
        pts = pts.sort_values("Files", ascending=False).reset_index(drop=True)
    return pts, unplaced


def due_soon(df, today, n=8):
    """Open pipeline files by target delivery date, soonest first (no-date files last)."""
    sel = df[df["status"].isin(PIPELINE_STATUSES)].copy()
    sel["_d"] = sel["deadline"].apply(parse_iso)
    sel["_k"] = sel["_d"].apply(lambda d: d if d else date(2999, 1, 1))
    sel = sel.sort_values(["_k", "id"]).head(n)
    return pd.DataFrame({
        "id": sel["id"].values, "Due": [due_label(d.isoformat() if d else None, st_) for d, st_ in zip(sel["_d"], sel["status"])],
        "Client": sel["client_name"].fillna("").values, "Property": sel["street"].fillna("").values,
        "Service": sel["assignment_type"].fillna("").values, "Status": sel["status"].values,
        "Fee": sel["fee"].fillna(0).astype(int).values})


def stage_counts(df):
    rows = []
    for i, stg in enumerate(PIPELINE_STATUSES, 1):
        sub = df[df["status"] == stg]
        rows.append({"Stage": f"{i} · {stg}", "Files": len(sub), "Fees": int(sub["fee"].fillna(0).sum())})
    return pd.DataFrame(rows)


def recent_activity(limit=8):
    with db() as c:
        rows = c.execute(
            "SELECT a.ts, a.message, a.lead_id, l.client_name, l.street FROM activity a "
            "LEFT JOIN leads l ON l.id = a.lead_id ORDER BY a.id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def _vs_last(cur, prev):
    d = cur - prev
    if not cur and not prev:
        return None
    return f"{'+' if d >= 0 else '-'}${abs(d):,.0f} vs last month"


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


# ======================================================================
# NARRATIVE LIBRARY, PROPERTY FACTS, REPORT MATERIALS
# ======================================================================
REPORT_SECTIONS = [
    "Neighborhood description", "Market conditions", "Site description", "Improvements description",
    "Condition and quality", "Functional utility", "Highest and best use", "Sales comparison methodology",
    "Reconciliation", "Scope of work", "Extraordinary assumptions & hypothetical conditions",
    "Limiting conditions & disclosures",
]
FACT_FIELDS = ["Property type", "Year built", "Stories", "Bedrooms", "Bathrooms", "Garage", "Basement", "Pool",
               "Foundation / construction", "Parcel / account #", "Legal description", "Notable features"]
FACT_SOURCES = ["", "MLS", "County records", "Client-provided", "Appraiser-observed", "Appraiser-verified"]
VERIFIED_SOURCES = {"County records", "Appraiser-observed", "Appraiser-verified"}
KEY_FACTS = ["Property type", "Year built", "Bedrooms", "Bathrooms", "Parcel / account #"]
FACT_TOKENS = {"Property type": "PROPERTY_TYPE", "Year built": "YEAR_BUILT", "Stories": "STORIES",
               "Bedrooms": "BEDROOMS", "Bathrooms": "BATHROOMS", "Garage": "GARAGE", "Basement": "BASEMENT",
               "Pool": "POOL", "Foundation / construction": "CONSTRUCTION", "Parcel / account #": "PARCEL",
               "Legal description": "LEGAL_DESCRIPTION", "Notable features": "NOTABLE_FEATURES"}
DOC_ITEMS = ["Engagement letter", "Inspection notes / sketch", "Subject photographs", "MLS / comparable data",
             "Market analysis support", "Public records (county)"]
INTENDED_USE = {
    "Date of Death / Probate": "estimating the market value of the property as of the decedent's date of death for estate administration",
    "Divorce or Marital Property Division": "estimating market value to assist in the division of marital property",
    "Property Tax Protest (ARB)": "supporting a property tax protest before the appraisal review board",
    "Pre-Listing Appraisal": "informing the owner's listing-price planning",
    "PMI / MIP Cancellation": "supporting a request to cancel mortgage insurance",
    "Gift Tax (Form 709)": "estimating market value for federal gift tax reporting",
    "IRS Appraisals (Gift/Estate Tax)": "estimating market value for federal gift or estate tax reporting",
    "Trust Funding & Administration": "trust funding and administration",
    "Bankruptcy Schedule A/B": "reporting the property on bankruptcy schedules",
    "Expert Witness & Litigation Support": "supporting expert analysis in litigation",
    "Pre-Purchase for Buyers": "informing a prospective buyer's purchase decision",
}
TOKEN_RE = re.compile(r"\[([A-Z][A-Z0-9_]{2,})\]")
TOKEN_HELP = {
    "CLIENT": "Client name", "PROPERTY_ADDRESS": "Street address", "CITY": "City", "COUNTY": "County (name only)",
    "ZIP": "ZIP code", "SERVICE": "Assignment type", "INTENDED_USE": "Intended use (filled from the service type — confirm)",
    "EFFECTIVE_DATE": "Effective date", "INSPECTION_DATE": "Inspection date", "INSPECTION_TYPE": "Inspection type",
    "REPORT_DATE": "Today's date", "APPRAISER": "Appraiser name", "FIRM": "Firm name", "LICENSE": "Appraiser license #",
    "GLA": "Gross living area (measured, else county)", "COUNTY_GLA": "County-record GLA", "LOT_SIZE": "Site size",
    "ASSESSED_VALUE": "Assessed value", "RECORDS_SOURCE": "Public-records source", "CONDITION_RATING": "C1–C6",
    "QUALITY_RATING": "Q1–Q6", "TIME_BASIS": "Time-adjustment basis", "COMP_COUNT": "Number of comps used",
    "SALE_RANGE": "Comp sale-price range", "ADJ_RANGE": "Adjusted-price range", "DISCLOSURE_STATEMENT": "Disclosure note",
    **{v: f"Property facts → {k}" for k, v in FACT_TOKENS.items()},
}

NARRATIVE_SEEDS = [
    ("Neighborhood description", "Sample — Established neighborhood",
     "The subject is located in [NEIGHBORHOOD], an established residential area of [CITY], [COUNTY] County, Texas. Land use in the "
     "immediate area is predominantly single-family residential, with schools, retail and services nearby. Access to major "
     "roadways is [ACCESS_DESCRIPTION]. No adverse influences were noted beyond those typical of the area, other than "
     "[ADVERSE_INFLUENCES]."),
    ("Neighborhood description", "Sample — Newer subdivision",
     "The subject is located in [SUBDIVISION], a newer subdivision in [CITY], [COUNTY] County. Homes in the subdivision were built "
     "primarily between [BUILD_YEARS] on lots that are typically [TYPICAL_LOT_DESCRIPTION]. The subdivision is served by "
     "[SCHOOL_DISTRICT]."),
    ("Market conditions", "Sample — Current market",
     "Market conditions in [CITY] and the surrounding area as of [EFFECTIVE_DATE] were reviewed using MLS data and public records. "
     "[MARKET_TREND] Typical marketing time was approximately [TYPICAL_DOM] days, and typical sale-to-list price ratios were "
     "[SALE_TO_LIST]. Supporting data is retained in the workfile."),
    ("Market conditions", "Sample — Retrospective effective date",
     "Market conditions were analyzed as of the retrospective effective date of [EFFECTIVE_DATE]. [RETROSPECTIVE_DATA_NOTE] "
     "[MARKET_TREND]"),
    ("Site description", "Sample — Residential site",
     "The site contains approximately [LOT_SIZE] and is [SITE_SHAPE_TOPOGRAPHY]. Public utilities available include [UTILITIES]. "
     "County records identify the property as parcel [PARCEL]. Easements and encroachments: [EASEMENTS_ENCROACHMENTS]."),
    ("Improvements description", "Sample — Single-family detached",
     "The subject is a [STORIES]-story [PROPERTY_TYPE] built in [YEAR_BUILT] with [GLA] of gross living area, [BEDROOMS] bedrooms "
     "and [BATHROOMS] bathrooms. Garage: [GARAGE]. Construction: [CONSTRUCTION]. Notable features include [NOTABLE_FEATURES]."),
    ("Improvements description", "Sample — Measured vs. county GLA",
     "Gross living area was measured by the appraiser in accordance with ANSI Z765 and totals [GLA]. County records report "
     "[COUNTY_GLA]. The appraiser's measurement was used in this analysis."),
    ("Condition and quality", "Sample — Condition",
     "As of the inspection on [INSPECTION_DATE], the improvements were observed to be in [CONDITION_RATING] condition overall. "
     "Renovations and updates: [RENOVATIONS_UPDATES]. Deferred maintenance observed: [DEFERRED_MAINTENANCE]."),
    ("Condition and quality", "Sample — Quality",
     "The quality of construction is rated [QUALITY_RATING], based on [QUALITY_BASIS]."),
    ("Functional utility", "Sample — Layout",
     "The floor plan is [LAYOUT_DESCRIPTION] and is considered [FUNCTIONAL_OPINION] for the market area. Functional issues "
     "observed: [FUNCTIONAL_ISSUES]."),
    ("Highest and best use", "Sample — As vacant and as improved",
     "Highest and best use as vacant: [HBU_AS_VACANT]. Highest and best use as improved: [HBU_AS_IMPROVED]. The legally "
     "permissible, physically possible, financially feasible and maximally productive tests were considered; the analysis is "
     "retained in the workfile."),
    ("Sales comparison methodology", "Sample — Sales comparison approach",
     "The sales comparison approach was applied using [COMP_COUNT] comparable sales with unadjusted prices from [SALE_RANGE]. "
     "After adjustment, the comparables indicated [ADJ_RANGE]. Adjustments were developed using [ADJUSTMENT_SUPPORT_METHOD]. "
     "Time adjustments follow the [TIME_BASIS] basis."),
    ("Sales comparison methodology", "Sample — Cost and income approaches",
     "The cost approach and the income approach were [CONSIDERED_OR_NOT_DEVELOPED] because [COST_INCOME_REASON]."),
    ("Reconciliation", "Sample — Reconciliation",
     "The indications from the comparable sales were reconciled with the most weight placed on [MOST_WEIGHT_COMPARABLES] "
     "because [RECONCILIATION_REASONING]."),
    ("Scope of work", "Sample — Scope of work",
     "The scope of work for this [SERVICE] assignment for [CLIENT] included an inspection of the subject property ([INSPECTION_TYPE]) "
     "on [INSPECTION_DATE], research of public records from [RECORDS_SOURCE], verification of comparable sales data from "
     "[DATA_SOURCES], and analysis using the sales comparison approach. The intended use is [INTENDED_USE]. The effective date is "
     "[EFFECTIVE_DATE] and the report date is [REPORT_DATE]."),
    ("Extraordinary assumptions & hypothetical conditions", "Sample — None",
     "No extraordinary assumptions or hypothetical conditions were used in this assignment."),
    ("Extraordinary assumptions & hypothetical conditions", "Sample — Extraordinary assumption",
     "This analysis relies on the extraordinary assumption that [EXTRAORDINARY_ASSUMPTION]. If this assumption is found to be "
     "false, the results of the analysis could be affected."),
    ("Extraordinary assumptions & hypothetical conditions", "Sample — Hypothetical condition",
     "This analysis is based on the hypothetical condition that [HYPOTHETICAL_CONDITION], which is contrary to what exists as of "
     "the effective date. Use of this hypothetical condition may affect the assignment results."),
    ("Limiting conditions & disclosures", "Sample — Use and reliance",
     "This report was prepared for [CLIENT] for the intended use of [INTENDED_USE] and may not be relied upon by others or for "
     "other purposes. [ADDITIONAL_LIMITING_CONDITIONS]"),
    ("Limiting conditions & disclosures", "Sample — Prior services and interest",
     "Disclosure regarding interest in the property and prior services: [DISCLOSURE_STATEMENT]"),
]


# ---------- narrative library (data layer) ----------
def seed_narratives_once():
    if get_setting("narratives_seeded"):
        return
    with db() as c:
        if c.execute("SELECT COUNT(*) FROM narratives").fetchone()[0] == 0:
            for sec, title, body in NARRATIVE_SEEDS:
                c.execute("INSERT INTO narratives (section, title, body, created_at) VALUES (?,?,?,?)",
                          (sec, title, body, now_str()))
    set_setting("narratives_seeded", True)


def list_narratives(section=None):
    q, args = "SELECT id, section, title, body FROM narratives", []
    if section:
        q += " WHERE section=?"
        args.append(section)
    with db() as c:
        rows = c.execute(q + " ORDER BY section, title COLLATE NOCASE", args).fetchall()
    return [dict(r) for r in rows]


def add_narrative(section, title, body):
    with db() as c:
        return c.execute("INSERT INTO narratives (section, title, body, created_at) VALUES (?,?,?,?)",
                         (section, title.strip() or "Untitled", body.strip(), now_str())).lastrowid


def update_narrative(nid, title, body):
    with db() as c:
        c.execute("UPDATE narratives SET title=?, body=? WHERE id=?", (title.strip() or "Untitled", body.strip(), nid))


def delete_narrative(nid):
    with db() as c:
        c.execute("DELETE FROM narratives WHERE id=?", (nid,))


def export_narratives_json():
    return json.dumps([{k: r[k] for k in ("section", "title", "body")} for r in list_narratives()], indent=1)


def import_narratives_json(text):
    """Merge a library export; entries with the same section + title are skipped. Returns (added, skipped)."""
    recs = json.loads(text)
    have = {(r["section"], r["title"]) for r in list_narratives()}
    added = skipped = 0
    for r in recs:
        if not isinstance(r, dict) or r.get("section") not in REPORT_SECTIONS or not str(r.get("body", "")).strip():
            skipped += 1
            continue
        if (r["section"], r.get("title", "")) in have:
            skipped += 1
            continue
        add_narrative(r["section"], r.get("title", "Imported"), r["body"])
        added += 1
    return added, skipped


# ---------- property facts (each with a source label) ----------
def load_facts(lead):
    try:
        raw = json.loads(lead.get("facts_json") or "{}")
    except json.JSONDecodeError:
        raw = {}
    return {f: {"v": str((raw.get(f) or {}).get("v", "") or ""), "src": (raw.get(f) or {}).get("src", "") or ""}
            for f in FACT_FIELDS}


def facts_frame(lead):
    f = load_facts(lead)
    return pd.DataFrame({"Field": FACT_FIELDS, "Value": [f[k]["v"] for k in FACT_FIELDS],
                         "Source": [f[k]["src"] or None for k in FACT_FIELDS]})


def facts_to_json(df):
    out = {}
    for r in df.to_dict("records"):
        v = "" if _isblank(r.get("Value")) else str(r["Value"]).strip()
        s = "" if _isblank(r.get("Source")) else str(r["Source"])
        out[r["Field"]] = {"v": v, "src": s}
    return json.dumps(out)


def load_report(lead):
    try:
        r = json.loads(lead.get("report_json") or "{}")
    except json.JSONDecodeError:
        r = {}
    return r if isinstance(r, dict) else {}


# ---------- placeholders ----------
def _long_date(d):
    return f"{d:%B} {d.day}, {d.year}" if d else None


def _money0(v):
    return f"${v:,.0f}"


def report_context(lead, facts, calc, today):
    """token -> text for everything we already know. Unknown tokens stay in the text for the appraiser to finish."""
    used = used_comps(calc) if len(calc) else calc
    ctx = {
        "CLIENT": lead.get("client_name"), "PROPERTY_ADDRESS": lead.get("street"), "CITY": lead.get("city"),
        "COUNTY": lead.get("county"), "ZIP": lead.get("zip"), "SERVICE": lead.get("assignment_type"),
        "INTENDED_USE": INTENDED_USE.get(lead.get("assignment_type") or ""),
        "EFFECTIVE_DATE": _long_date(parse_iso(lead.get("effective_date"))),
        "INSPECTION_DATE": _long_date(parse_iso(lead.get("inspection_date"))),
        "INSPECTION_TYPE": (lead.get("inspection_type") or "").lower() or None,
        "REPORT_DATE": _long_date(today), "APPRAISER": APPRAISER["name"], "FIRM": APPRAISER["firm"],
        "LICENSE": f"TX {APPRAISER['license']}", "LOT_SIZE": lead.get("lot_size"),
        "ASSESSED_VALUE": lead.get("assessed_value"), "RECORDS_SOURCE": lead.get("records_source"),
        "CONDITION_RATING": rating_code(lead.get("condition_rating"), "C"),
        "QUALITY_RATING": rating_code(lead.get("quality_rating"), "Q"), "TIME_BASIS": lead.get("time_basis"),
        "DISCLOSURE_STATEMENT": (lead.get("disclosure_notes") or "").strip() or None,
    }
    gla = int(lead.get("ansi_gla") or 0) or int(lead.get("tad_gla") or 0)
    if gla:
        ctx["GLA"] = f"{gla:,} square feet"
    if int(lead.get("tad_gla") or 0):
        ctx["COUNTY_GLA"] = f"{int(lead['tad_gla']):,} square feet"
    for f, tok in FACT_TOKENS.items():
        if facts.get(f, {}).get("v"):
            ctx[tok] = facts[f]["v"]
    if len(used):
        ctx["COMP_COUNT"] = str(len(used))
        ctx["SALE_RANGE"] = f"{_money0(used['Sale Price'].min())} to {_money0(used['Sale Price'].max())}"
        ctx["ADJ_RANGE"] = f"{_money0(used['Adjusted Price'].min())} to {_money0(used['Adjusted Price'].max())}"
    return {k: str(v) for k, v in ctx.items() if v not in (None, "")}


def fill_placeholders(text, ctx):
    """Fill known [TOKENS]. Returns (filled text, list of tokens still open, in order of appearance)."""
    def sub(m):
        return ctx.get(m.group(1), m.group(0))
    out = TOKEN_RE.sub(sub, text or "")
    left = []
    for m in TOKEN_RE.finditer(out):
        if m.group(1) not in left:
            left.append(m.group(1))
    return out, left


# ---------- completeness & consistency checker ----------
SEV_ORDER = {"fix": 0, "missing": 1, "review": 2}
SEV_LABEL = {"fix": "🔴 Needs fixing", "missing": "🟡 Missing", "review": "🔵 Needs your professional review"}


def report_checks(lead, calc, facts, report, today):
    """[{sev: fix|missing|review, area, msg}] plus a count of passed checks."""
    out, passed = [], 0

    def issue(sev, area, msg):
        out.append({"sev": sev, "area": area, "msg": msg})

    def ok():
        nonlocal passed
        passed += 1

    # Subject data
    miss = [f for f in KEY_FACTS if not facts[f]["v"]]
    if miss:
        issue("missing", "Subject data", "Property facts not entered: " + ", ".join(miss) + ".")
    else:
        ok()
    unver = [f for f in FACT_FIELDS if facts[f]["v"] and facts[f]["src"] not in VERIFIED_SOURCES]
    if unver:
        issue("review", "Subject data", "Not yet verified (source is MLS, client-provided or unlabeled): " + ", ".join(unver) + ".")
    else:
        ok()
    tad, ansi = int(lead.get("tad_gla") or 0), int(lead.get("ansi_gla") or 0)
    if not (tad or ansi):
        issue("missing", "Subject data", "No gross living area entered (county or measured).")
    elif tad and ansi and abs(ansi - tad) / tad > 0.05:
        issue("review", "Subject data", f"Measured GLA differs from county records by {ansi - tad:+,} SF ({(ansi - tad) / tad * 100:+.1f}%).")
    else:
        ok()
    # Dates
    eff, insp = parse_iso(lead.get("effective_date")), parse_iso(lead.get("inspection_date"))
    if not eff:
        issue("missing", "Dates", "Effective date is not set.")
    else:
        ok()
    if not insp and (lead.get("inspection_type") or "") != "Desktop":
        issue("missing", "Dates", "Inspection date is not set.")
    elif insp and insp > today:
        issue("fix", "Dates", f"Inspection date {fmt_d(insp)} is in the future.")
    else:
        ok()
    if eff and eff > today:
        issue("review", "Dates", f"Effective date {fmt_d(eff)} is after today — confirm this is a prospective appraisal.")
    # Comparables
    used = used_comps(calc) if len(calc) else calc
    if not len(calc):
        issue("missing", "Comparables", "No comparables entered.")
    else:
        bad = [r["Address"] or f"row {i + 1}" for i, r in calc.iterrows()
               if r["Sale Price"] <= 0 or not r["Sale Date"] or not r["MLS #"]]
        if bad:
            issue("missing", "Comparables", "Missing price, sale date or MLS # on: " + "; ".join(bad) + ".")
        else:
            ok()
        if not len(used):
            issue("missing", "Comparables", "No comparable carries Most or Less weight.")
        flagged = [f"{r['Address']} ({r['Flags']})" for _, r in calc.iterrows() if r["Flags"]]
        if flagged:
            issue("review", "Adjustments", "Large adjustments — document support: " + "; ".join(flagged) + ".")
        else:
            ok()
        drift = [r["Address"] for _, r in calc.iterrows()
                 if abs(r["Adjusted Price"] - (r["Sale Price"] + r[ADJ_COLS].sum())) > 0.5]
        if drift:
            issue("fix", "Adjustments", "Arithmetic does not reconcile on: " + "; ".join(drift) + ".")
        else:
            ok()
    ov = int(lead.get("opinion_value") or 0)
    if len(used) and ov:
        lo, hi = used["Adjusted Price"].min(), used["Adjusted Price"].max()
        if ov < lo or ov > hi:
            issue("fix", "Reconciliation",
                  f"Opinion of value {_money0(ov)} is outside the adjusted range of the comps used ({_money0(lo)}–{_money0(hi)}).")
        else:
            ok()
    # Reconciliation
    if not (lead.get("reconciliation_notes") or "").strip():
        issue("missing", "Reconciliation", "No appraiser-entered reconciliation explanation.")
    else:
        ok()
    if not ov:
        issue("missing", "Reconciliation", "No opinion of value entered (this tool never supplies one).")
    # Narratives
    texts = {s: (report.get(s) or "").strip() for s in REPORT_SECTIONS}
    blank = [s for s, t in texts.items() if not t]
    if blank:
        issue("missing", "Narratives", f"{len(blank)} section(s) blank: " + ", ".join(blank) + ".")
    else:
        ok()
    ctx = report_context(lead, facts, calc, today)
    open_toks = {}
    for s, t in texts.items():
        for tok in fill_placeholders(t, ctx)[1]:
            open_toks.setdefault(s, []).append(tok)
    if open_toks:
        n = sum(len(v) for v in open_toks.values())
        issue("review", "Narratives", f"{n} placeholder(s) still open in: " + ", ".join(open_toks) + ".")
    else:
        ok()
    iu = INTENDED_USE.get(lead.get("assignment_type") or "")
    if iu and any(iu in t for t in texts.values()):
        issue("review", "Narratives", "Intended use was auto-filled from the service type — confirm it matches the engagement letter.")
    # Consistency: narrative text vs. file data
    gla = ansi or tad
    year = facts["Year built"]["v"]
    beds = facts["Bedrooms"]["v"]
    for s, t in texts.items():
        for m in re.finditer(r"([\d,]{3,6})\s*(?:square feet|sq\.? ?ft\.?|SF)\b", t, re.I):
            n = int(m.group(1).replace(",", "") or 0)
            if gla and n and n not in (ansi, tad) and abs(n - gla) > 0:
                issue("fix", "Consistency", f"“{s}” cites {n:,} SF but the file shows {gla:,} SF"
                      + (f" (county {tad:,})" if tad and tad != gla else "") + ".")
        for m in re.finditer(r"built in (\d{4})", t, re.I):
            if year and m.group(1) != year.strip():
                issue("fix", "Consistency", f"“{s}” says built in {m.group(1)} but the facts sheet says {year}.")
        for m in re.finditer(r"(\d+)\s*bedrooms?", t, re.I):
            if beds and m.group(1) != beds.strip().split(".")[0]:
                issue("fix", "Consistency", f"“{s}” says {m.group(1)} bedrooms but the facts sheet says {beds}.")
    # Disclosures
    disc = (bool(lead.get("no_interest")) and bool(lead.get("no_prior_services"))) or bool((lead.get("disclosure_notes") or "").strip())
    if not disc:
        issue("missing", "Disclosures", "Interest / prior-services disclosure not addressed (Subject & Scope tab).")
    else:
        ok()
    # Workfile documents
    docs = set(report.get("_docs") or [])
    nodoc = [d for d in DOC_ITEMS if d not in docs]
    if nodoc:
        issue("missing", "Workfile", "Not marked as on file: " + ", ".join(nodoc) + ".")
    else:
        ok()
    out.sort(key=lambda x: SEV_ORDER[x["sev"]])
    return out, passed


# ---------- Word draft ----------
def build_report_docx(lead, calc, unused, facts, report, checks, today):
    """Report-preparation package as .docx bytes. Open [TOKENS] and blank sections are highlighted for the appraiser."""
    from docx import Document
    from docx.enum.text import WD_COLOR_INDEX
    from docx.shared import Pt, Inches
    import io

    ctx = report_context(lead, facts, calc, today)
    doc = Document()
    for s in doc.sections:
        s.left_margin = s.right_margin = Inches(0.8)
        s.top_margin = s.bottom_margin = Inches(0.8)
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(10.5)

    def runs(par, text, bold=False, italic=False, size=None):
        for part in re.split(r"(\[[A-Z][A-Z0-9_]{2,}\])", text):
            if not part:
                continue
            r = par.add_run(part)
            r.bold, r.italic = bold, italic
            if size:
                r.font.size = Pt(size)
            if TOKEN_RE.fullmatch(part):
                r.font.highlight_color = WD_COLOR_INDEX.YELLOW

    def todo(par, text):
        r = par.add_run(text)
        r.font.highlight_color = WD_COLOR_INDEX.YELLOW
        r.bold = True

    def table(header, rows, widths=None, size=8.5):
        t = doc.add_table(rows=1, cols=len(header))
        t.style = "Table Grid"
        for i, h in enumerate(header):
            c = t.rows[0].cells[i]
            c.text = ""
            runs(c.paragraphs[0], str(h), bold=True, size=size)
        for row in rows:
            cells = t.add_row().cells
            for i, v in enumerate(row):
                cells[i].text = ""
                runs(cells[i].paragraphs[0], str(v), size=size)
        if widths:
            t.autofit = False
            for row in t.rows:
                for i, w in enumerate(widths):
                    row.cells[i].width = Inches(w)
        doc.add_paragraph()
        return t

    doc.add_heading(f"{APPRAISER['firm']} — Report Preparation Package", 0)
    p = doc.add_paragraph()
    runs(p, "DRAFT FOR APPRAISER COMPLETION. This is not a finished appraisal report. It contains no certification and "
            "states no opinion of value unless the appraiser entered one. Yellow highlights mark text the appraiser must "
            "complete or confirm.", italic=True)
    doc.add_paragraph(f"{lead.get('client_name') or ''} · {full_address(lead)} · prepared {_long_date(today)}")

    doc.add_heading("1. Assignment information", 1)
    table(["Item", "Detail"], [
        ["Client", lead.get("client_name") or "[CLIENT]"], ["Service", lead.get("assignment_type") or "[SERVICE]"],
        ["Intended use", ctx.get("INTENDED_USE", "[INTENDED_USE]")],
        ["Effective date", ctx.get("EFFECTIVE_DATE", "[EFFECTIVE_DATE]")],
        ["Inspection", f"{lead.get('inspection_type') or '[INSPECTION_TYPE]'} · {ctx.get('INSPECTION_DATE', '[INSPECTION_DATE]')}"],
        ["Report date", ctx["REPORT_DATE"]],
        ["Appraiser", f"{APPRAISER['name']}, {APPRAISER['designation']} (TALCB #{APPRAISER['license']})"],
    ], [1.6, 5.2], 9.5)

    doc.add_heading("2. Subject property", 1)
    gla_txt = ctx.get("GLA", "[GLA]")
    table(["Item", "Detail"], [
        ["Address", full_address(lead)], ["County", lead.get("county") or "[COUNTY]"],
        ["Gross living area", gla_txt + (f" (county records: {ctx['COUNTY_GLA']})" if "COUNTY_GLA" in ctx and ctx["COUNTY_GLA"] != gla_txt else "")],
        ["Site size", lead.get("lot_size") or "[LOT_SIZE]"], ["Assessed value", lead.get("assessed_value") or "[ASSESSED_VALUE]"],
        ["Records source", lead.get("records_source") or "[RECORDS_SOURCE]"],
        ["Condition / quality", f"{ctx.get('CONDITION_RATING', '[CONDITION_RATING]')} / {ctx.get('QUALITY_RATING', '[QUALITY_RATING]')}"],
    ], [1.6, 5.2], 9.5)
    rows = []
    for f in FACT_FIELDS:
        v, s = facts[f]["v"], facts[f]["src"]
        status = "Verified" if s in VERIFIED_SOURCES and v else ("UNVERIFIED — confirm" if v else "Not entered")
        rows.append([f, v or f"[{FACT_TOKENS[f]}]", s or "—", status])
    doc.add_paragraph("Property data sheet (each fact with its source)").runs[0].bold = True
    table(["Field", "Value", "Source", "Status"], rows, [1.7, 2.6, 1.3, 1.2])

    doc.add_heading("3. Narrative sections", 1)
    for s in REPORT_SECTIONS:
        doc.add_heading(s, 2)
        txt = (report.get(s) or "").strip()
        if not txt:
            todo(doc.add_paragraph(), "[APPRAISER TO COMPLETE — no narrative entered]")
            continue
        filled, _ = fill_placeholders(txt, ctx)
        for chunk in filled.split("\n\n"):
            runs(doc.add_paragraph(), chunk.strip())

    doc.add_heading("4. Sales comparison data", 1)
    if not len(calc):
        todo(doc.add_paragraph(), "[APPRAISER TO COMPLETE — no comparables entered]")
    else:
        table(["#", "Address", "MLS #", "Status", "Sale price", "Sale date", "GLA", "Weight"],
              [[i + 1, r["Address"], r["MLS #"], r["Status"] or "", _money0(r["Sale Price"]), fmt_d(r["Sale Date"]) if r["Sale Date"] else "—",
                f"{r['GLA']:,.0f}" if r["GLA"] else "—", r["Weight"] or ""] for i, r in calc.iterrows()],
              [0.3, 1.9, 0.8, 0.9, 0.9, 0.8, 0.5, 0.9])
        doc.add_paragraph("Adjustments (entered by the appraiser; this tool only performs the arithmetic)").runs[0].bold = True
        table(["#", "Time", "Cond/Qual", "GLA", "Site", "Other", "Net adj", "Gross %", "Adjusted price"],
              [[i + 1] + [f"{r[c]:+,.0f}" for c in ADJ_COLS] + [f"{r['Net Adj']:+,.0f}", f"{r['Gross %']:.1f}%", _money0(r["Adjusted Price"])]
               for i, r in calc.iterrows()], [0.3, 0.7, 0.8, 0.7, 0.7, 0.7, 0.8, 0.7, 1.1])
        used = used_comps(calc)
        if len(used):
            doc.add_paragraph(f"Adjusted-price range of comparables with Most or Less weight: "
                              f"{_money0(used['Adjusted Price'].min())} – {_money0(used['Adjusted Price'].max())}.")
        if any(str(r["Weight Rationale"]).strip() for _, r in calc.iterrows()):
            doc.add_paragraph("Weight rationale (appraiser's)").runs[0].bold = True
            for i, r in calc.iterrows():
                if str(r["Weight Rationale"]).strip():
                    doc.add_paragraph(f"Comp {i + 1} ({r['Weight']}): {r['Weight Rationale']}", style="List Bullet")
    if len(unused):
        doc.add_paragraph("Analyzed but not used").runs[0].bold = True
        table(list(UNUSED_COLS), [[r[c] for c in UNUSED_COLS] for _, r in unused.iterrows()], [1.9, 1.0, 2.0, 1.9])

    doc.add_heading("5. Reconciliation", 1)
    recon = (lead.get("reconciliation_notes") or "").strip()
    if recon:
        runs(doc.add_paragraph(), recon)
    else:
        todo(doc.add_paragraph(), "[APPRAISER TO COMPLETE — reconciliation explanation]")
    ov = int(lead.get("opinion_value") or 0)
    p = doc.add_paragraph()
    p.add_run("Opinion of value (as entered by the appraiser): ").bold = True
    if ov:
        p.add_run(_money0(ov))
    else:
        todo(p, "[APPRAISER TO COMPLETE]")

    doc.add_heading("6. Items to resolve before finalizing", 1)
    if not checks:
        doc.add_paragraph("No open items found by the completeness check.")
    for sev in ("fix", "missing", "review"):
        items = [c for c in checks if c["sev"] == sev]
        if items:
            doc.add_paragraph(SEV_LABEL[sev].split(" ", 1)[1]).runs[0].bold = True
            for c in items:
                doc.add_paragraph(f"{c['area']}: {c['msg']}", style="List Bullet")

    doc.add_heading("7. Workfile documents", 1)
    have = set(report.get("_docs") or [])
    for d in DOC_ITEMS:
        doc.add_paragraph(("☑ " if d in have else "☐ ") + d)
    p = doc.add_paragraph()
    runs(p, "Certification, signature and any required lender or client forms are added by the appraiser from his own templates.", italic=True)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ======================================================================
# FULL DEMO DATA (fictional) -- one complete file + six months of history
# ======================================================================
SHOWCASE_NAME = "DEMO — Reyes Residence"


def _ts(days_ago):
    return (date.today() - timedelta(days=days_ago)).strftime("%Y-%m-%d") + " 10:00:00"


def _set_history(lid, steps, paid=None, hours=None, closed=False):
    """steps = [(status, days_ago)] oldest first; rewrites the activity log and the date columns."""
    steps = sorted(steps, key=lambda s: -s[1])
    first, last = steps[0][1], steps[-1][1]
    with db() as c:
        c.execute("DELETE FROM activity WHERE lead_id=?", (lid,))
        c.execute("INSERT INTO activity (lead_id, ts, message) VALUES (?,?,?)", (lid, _ts(first), "Lead created (demo)"))
        prev = "Inquiry"
        for st_, ago in steps:
            if st_ == "Inquiry":
                continue
            c.execute("INSERT INTO activity (lead_id, ts, message) VALUES (?,?,?)", (lid, _ts(ago), f"Status: {prev} → {st_}"))
            prev = st_
        c.execute("UPDATE leads SET created_at=?, updated_at=?, closed_at=?, paid_date=?, paid_amount=?, hours=? WHERE id=?",
                  (_ts(first), _ts(last), _ts(last) if closed else None,
                   (date.today() - timedelta(days=paid[0])).isoformat() if paid else None,
                   paid[1] if paid else None, hours, lid))


def seed_showcase():
    """Adds one fully worked fictional file (comps, adjustments, facts, narratives) and history so every page has data."""
    with db() as c:
        if c.execute("SELECT 1 FROM leads WHERE client_name=?", (SHOWCASE_NAME,)).fetchone():
            return False
    today = date.today()
    fees = get_fees()
    ids = list(get_setting("demo_lead_ids", []) or [])
    iso = lambda n: (today - timedelta(days=n)).isoformat()
    svc = "Date of Death / Probate"
    eff_ago = 150

    lid = create_lead({
        "client_name": SHOWCASE_NAME, "phone": "817-555-0161", "email": "reyes.demo@example.com",
        "street": "4108 Cedar Ridge Ct", "city": "Fort Worth", "zip": "76109", "county": "Tarrant",
        "assignment_type": svc, "status": "In Progress", "fee": fees.get(svc, 600), "deadline": (today + timedelta(days=4)).isoformat(),
        "source": "Referral", "notes": "Fictional demo file: retrospective value for estate inventory. Access via the executor.",
    })
    ids.append(lid)

    def comp(addr, mls, price, c_off, s_off, dom, prox, dr, gla, cond, qual, weight, why, t, cq, g, site, oth=0, fin="Conventional"):
        eff = today - timedelta(days=eff_ago)
        return {"Address": addr, "MLS #": mls, "Status": "Settled Sale", "Sale Price": price,
                "Contract Date": (eff - timedelta(days=c_off)).isoformat(), "Sale Date": (eff - timedelta(days=s_off)).isoformat(),
                "DOM": dom, "Proximity (mi)": prox, "Direction": dr, "GLA": gla, "Concessions ($)": 0, "Financing": fin,
                "Cond": cond, "Qual": qual, "Weight": weight, "Weight Rationale": why, "Time Adj": t, "Cond/Qual Adj": cq,
                "GLA Adj": g, "Site Adj": site, "Other Adj": oth}

    comps = [
        comp("4120 Cedar Ridge Ct", "20871001", 438000, 55, 20, 21, 0.05, "N", 2180, "C3", "Q3", "Most Weight",
             "Same street; closest match in age, size and condition.", 4400, 0, 1700, 0),
        comp("4033 Ridgeway Dr", "20866417", 449500, 70, 35, 18, 0.4, "E", 2290, "C3", "Q4", "Most Weight",
             "Same subdivision; slightly larger with better finishes.", 3600, 0, -3800, -2000),
        comp("3921 Cedar Hollow Ln", "20859230", 421000, 85, 48, 33, 0.7, "SW", 2105, "C4", "Q3", "Less Weight",
             "Inferior condition required larger adjustments.", 5900, 8000, 4400, 0),
        comp("4207 Oakmont Trl", "20851184", 455000, 98, 62, 27, 0.9, "NE", 2350, "C2", "Q3", "Less Weight",
             "Superior condition and size; adjustments are larger.", 6100, -9000, -6800, -3000),
        comp("5012 Brookside Cir", "20844720", 468000, 110, 75, 40, 1.6, "NW", 2480, "C2", "Q4", "No Weight",
             "Larger and superior in condition and quality; set aside.", 7000, -5000, -13000, -4000, -6000),
    ]
    unused = [
        {"Address": "2807 Larkspur Ln", "Status": "Settled Sale", "Reason Not Used": "Size differs significantly",
         "Comment": "Roughly 900 SF smaller than the subject; no closer sales were needed."},
        {"Address": "4455 Highgate Ave", "Status": "Settled Sale", "Reason Not Used": "Atypical / non-arm's-length sale",
         "Comment": "Sold between related parties per MLS remarks."},
    ]
    facts = {
        "Property type": ("Single-family detached", "County records"), "Year built": ("1998", "County records"),
        "Stories": ("2", "Appraiser-observed"), "Bedrooms": ("4", "MLS"), "Bathrooms": ("2.5", "Appraiser-observed"),
        "Garage": ("2-car attached", "Appraiser-observed"), "Basement": ("None", "Appraiser-observed"),
        "Pool": ("No", "Appraiser-observed"), "Foundation / construction": ("Slab; brick and fiber-cement exterior", "Appraiser-observed"),
        "Parcel / account #": ("00000000 (demo)", "County records"), "Legal description": ("", ""),
        "Notable features": ("Updated kitchen (2021); covered patio", "Client-provided"),
    }
    ctx_demo = {"CITY": "Fort Worth", "COUNTY": "Tarrant", "EFFECTIVE_DATE": _long_date(today - timedelta(days=eff_ago)),
                "INSPECTION_DATE": _long_date(today - timedelta(days=3)), "CLIENT": SHOWCASE_NAME, "SERVICE": svc,
                "INTENDED_USE": INTENDED_USE[svc], "INSPECTION_TYPE": "full (interior & exterior)", "REPORT_DATE": _long_date(today),
                "RECORDS_SOURCE": "Tarrant Appraisal District (TAD) Public Records", "GLA": "2,214 square feet", "COUNTY_GLA": "2,150 square feet",
                "YEAR_BUILT": "1998", "STORIES": "2", "PROPERTY_TYPE": "Single-family detached", "BEDROOMS": "4", "BATHROOMS": "2.5",
                "GARAGE": "2-car attached", "CONSTRUCTION": "Slab; brick and fiber-cement exterior",
                "NOTABLE_FEATURES": "updated kitchen (2021); covered patio", "CONDITION_RATING": "C3", "QUALITY_RATING": "Q3"}
    seeds = {(s, t): b for s, t, b in NARRATIVE_SEEDS}
    report = {}
    for sec, title in [("Scope of work", "Sample — Scope of work"), ("Improvements description", "Sample — Single-family detached"),
                       ("Neighborhood description", "Sample — Established neighborhood"),
                       ("Extraordinary assumptions & hypothetical conditions", "Sample — None")]:
        report[sec] = fill_placeholders(seeds[(sec, title)], ctx_demo)[0]
    report["Neighborhood description"] = report["Neighborhood description"].replace("[NEIGHBORHOOD]", "Ridgeway Estates")
    report["_docs"] = ["Engagement letter", "MLS / comparable data"]

    update_lead(
        lid, effective_date=iso(eff_ago), inspection_type="Full (interior & exterior)", inspection_date=iso(3),
        tad_gla=2150, ansi_gla=2214, lot_size="0.21 acres", assessed_value="$402,000", tax_year=today.year,
        records_source="Tarrant Appraisal District (TAD) Public Records", condition_rating="C3", quality_rating="Q3",
        time_basis="Contract date (UAD 3.6)", no_interest=1, no_prior_services=1,
        market_log="Comps 1–5 confirmed via MLS on the inspection date.", comps_json=json.dumps(comps_to_records(pd.DataFrame(comps))),
        unused_json=json.dumps(unused), facts_json=json.dumps({k: {"v": v, "src": s} for k, (v, s) in facts.items()}),
        report_json=json.dumps(report), opinion_value=443000,
        reconciliation_notes="Most weight on the two comparables on or near the subject street, which required the smallest "
                             "and best-supported adjustments. Comparables 3 and 4 bracket the range and were given less weight.",
    )
    _set_history(lid, [("Fee Quoted", 11), ("Engaged", 9), ("In Progress", 8)], hours=6.5)

    # ---- six months of history: (name, service, street, city, zip, status, created, steps, paid, source, hours)
    hist = [
        ("Alvarez Trust", "Trust Funding & Administration", "112 Mockingbird Ln", "Fort Worth", "76107", "Closed", 178,
         [("Fee Quoted", 177), ("Engaged", 174), ("Delivered", 168), ("Closed", 150)], (150, "fee"), "Referral", 7.0),
        ("Brooks Family", "Property Tax Protest (ARB)", "905 Pecan Hollow Dr", "Arlington", "76012", "Closed", 150,
         [("Fee Quoted", 149), ("Engaged", 146), ("Delivered", 140), ("Closed", 120)], (128, "fee"), "Website form", 4.5),
        ("Chen Estate", "Date of Death / Probate", "3320 Willow Bend Ct", "Southlake", "76092", "Closed", 128,
         [("Fee Quoted", 127), ("Engaged", 124), ("Delivered", 115), ("Closed", 100)], (106, "fee"), "Referral", 8.0),
        ("Dawson Holdings", "Pre-Listing Appraisal", "77 County Road 1200", "Weatherford", "76088", "Closed", 108,
         [("Fee Quoted", 107), ("Engaged", 104), ("Delivered", 95), ("Closed", 85)], (88, "fee"), "Phone", 5.0),
        ("Ellison & Park", "Divorce or Marital Property Division", "1801 Meadowcrest Dr", "Keller", "76248", "Closed", 84,
         [("Fee Quoted", 83), ("Engaged", 80), ("Delivered", 70), ("Closed", 55)], (60, "fee"), "Referral", 7.5),
        ("Foster Remodel", "PMI / MIP Cancellation", "42 Briar Gate", "Cleburne", "76033", "Delivered", 66,
         [("Fee Quoted", 65), ("Engaged", 62), ("Delivered", 50)], None, "Text / SMS", 3.5),
        ("Garza Family", "Property Tax Protest (ARB)", "615 Lakeview Trl", "Granbury", "76048", "Closed", 52,
         [("Fee Quoted", 51), ("Engaged", 48), ("Delivered", 34), ("Closed", 28)], (30, "fee"), "Repeat client", 4.0),
        ("Hughes Estate", "Bankruptcy Schedule A/B", "2209 Elm Valley Dr", "Denton", "76208", "Delivered", 38,
         [("Fee Quoted", 37), ("Engaged", 34), ("Delivered", 20)], None, "Referral", 6.0),
        ("Iverson Partners", "Expert Witness & Litigation Support", "950 Commerce Park Blvd", "Grand Prairie", "75050", "Delivered", 26,
         [("Fee Quoted", 25), ("Engaged", 22), ("Delivered", 9)], None, "Referral", 12.0),
        ("Jensen Family", "Pre-Purchase for Buyers", "18 Sunrise Cir", "Mansfield", "76063", "Lost", 40,
         [("Fee Quoted", 39), ("Lost", 33)], None, "Website form", None),
        ("Kline Trust", "Gift Tax (Form 709)", "7 Stonegate Way", "Burleson", "76028", "Lost", 21,
         [("Fee Quoted", 20), ("Lost", 14)], None, "Phone", None),
    ]
    surnames = ["Morales", "Whitaker", "Okafor", "Lindqvist", "Delgado", "Prescott", "Nakamura", "Bellamy", "Castillo",
                "Hargrove", "Sullivan", "Tran", "Montgomery", "Reeves", "Abbott", "Navarro"]
    svc_cycle = ["Property Tax Protest (ARB)", "Date of Death / Probate", "Pre-Listing Appraisal",
                 "Divorce or Marital Property Division", "PMI / MIP Cancellation", "Trust Funding & Administration",
                 "Pre-Purchase for Buyers", "Expert Witness & Litigation Support"]
    city_cycle = [("Fort Worth", "76116"), ("Arlington", "76017"), ("Keller", "76244"), ("Southlake", "76092"),
                  ("Weatherford", "76086"), ("Burleson", "76028"), ("Mansfield", "76063"), ("Fort Worth", "76133"),
                  ("Granbury", "76049"), ("Cleburne", "76031"), ("Denton", "76205"), ("Grand Prairie", "75052")]
    src_cycle = ["Referral", "Phone", "Website form", "Repeat client", "Text / SMS", "Email"]
    roads = ["Oak Hollow Dr", "Maple Run", "Cedar Bend Ln", "Prairie View Ct", "Stonegate Blvd", "Willow Creek Rd"]
    for i, nm in enumerate(surnames):
        created = 176 - 10 * i
        deliv = created - 9
        steps = [("Fee Quoted", created - 1), ("Engaged", created - 3), ("Delivered", deliv)]
        last = i == len(surnames) - 1                       # the newest one is still unpaid
        if not last:
            steps.append(("Closed", deliv - 12 if deliv - 12 >= 0 else 0))
        city, zp = city_cycle[i % len(city_cycle)]
        hist.append((f"{nm} Family", svc_cycle[i % len(svc_cycle)], f"{100 + i * 37} {roads[i % len(roads)]}", city, zp,
                     "Delivered" if last else "Closed", created, steps,
                     None if last else (max(deliv - 10, 0), "fee"), src_cycle[i % len(src_cycle)], 3.5 + (i * 5) % 8))
    for name, sv, street, city, zp, status, created, steps, paid, src, hrs in hist:
        fee = fees.get(sv, 500)
        hid = create_lead({
            "client_name": f"{name} (demo)", "phone": "817-555-0100", "street": street, "city": city, "zip": zp,
            "county": CITY_COUNTY.get(city.lower(), ""), "assignment_type": sv, "status": status, "fee": fee,
            "source": src, "deadline": iso(steps[-1][1] - 2), "no_interest": 1, "no_prior_services": 1,
        })
        ids.append(hid)
        _set_history(hid, [("Inquiry", created)] + steps, paid=((paid[0], fee) if paid else None), hours=hrs,
                     closed=status in TERMINAL)
        if hrs and status in EARNED:                     # a little driving for each delivered file
            miles = round(14 + (hid * 7) % 22, 1)
            d0 = today - timedelta(days=steps[-2][1] if len(steps) > 1 else 5)
            add_trip(d0, miles, default_mileage_rate(d0), "Office (demo)", f"{street}, {city}", "Property inspection", hid)

    # ---- overhead + a few file-linked costs (marked "(demo)" so they can be removed)
    for m in range(6):
        d0 = add_months(today.replace(day=1), -m)
        add_expense(d0, "MLS & data subscriptions", "MLS", "Monthly MLS access (demo)", 135.0, None, True)
        add_expense(d0, "Software & technology", "Software", "Appraisal software (demo)", 69.0, None, True)
    add_expense(today - timedelta(days=140), "E&O / liability insurance", "Carrier", "Annual E&O premium (demo)", 1850.0, None, True)
    add_expense(today - timedelta(days=95), "Licenses, dues & continuing education", "Provider", "CE course (demo)", 295.0, None, True)
    add_expense(today - timedelta(days=60), "Marketing & advertising", "Print shop", "Business cards (demo)", 84.5, None, False)
    add_expense(today - timedelta(days=8), "Vehicle (non-mileage)", "Parking", "Courthouse parking (demo)", 18.0, ids[-3], False)
    add_trip(today - timedelta(days=3), 18.4, default_mileage_rate(today), "Office (demo)", "4108 Cedar Ridge Ct, Fort Worth", "Property inspection", lid)
    add_trip(today - timedelta(days=2), 9.2, default_mileage_rate(today), "Office (demo)", "Comparable drive-by, Cedar Ridge area", "Comparable drive-by", lid)

    set_setting("demo_lead_ids", ids)
    return True


def seed_full_demo():
    """Sample pipeline + the full showcase. Only the leads added here are tracked as demo data, so real files are never touched."""
    with db() as c:
        before = {r["id"] for r in c.execute("SELECT id FROM leads")}
    seed_demo()
    with db() as c:
        after = {r["id"] for r in c.execute("SELECT id FROM leads")}
    set_setting("demo_lead_ids", sorted(set(get_setting("demo_lead_ids", []) or []) | (after - before)))
    seed_showcase()


def delete_demo():
    """Remove everything loaded by the full demo (leads, their activity, and costs marked (demo))."""
    ids = list(get_setting("demo_lead_ids", []) or [])
    with db() as c:
        for i in ids:
            c.execute("DELETE FROM activity WHERE lead_id=?", (i,))
            c.execute("DELETE FROM leads WHERE id=?", (i,))
        c.execute("DELETE FROM expenses WHERE description LIKE '%(demo)'")
        c.execute("DELETE FROM mileage WHERE origin LIKE '%(demo)'")
    set_setting("demo_lead_ids", [])
    return len(ids)


# ==== UI ====
# ======================================================================
PAGE_DASH = "🏠 Dashboard"
PAGE_PIPELINE = "📊 Pipeline"
PAGE_NEW = "➕ New Lead"
PAGE_WORKFILE = "📋 Working Files"
PAGE_REPORTS = "📈 Run Reports"
PAGE_FEES = "⚙️ Settings & Libraries"
PAGE_BOOKS = "🧾 Expenses & Mileage"
PAGES = [PAGE_DASH, PAGE_NEW, PAGE_PIPELINE, PAGE_WORKFILE, PAGE_REPORTS, PAGE_BOOKS, PAGE_FEES]

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
        if st.button("📋 Open working file", key=f"open_{lid}", type="primary"):
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


# ----------------------------------------------------------------------
# DASHBOARD PAGE (landing)
# ----------------------------------------------------------------------
def page_dashboard():
    today = date.today()
    h1, h2 = st.columns([4, 1], vertical_alignment="center")
    h1.title("🏠 Dashboard")
    h1.caption(f"{APPRAISER['firm']} · {today:%A, %B %d, %Y} · Welcome back, {APPRAISER['name'].split()[0]}.")
    if h2.button("➕ New lead", type="primary", width="stretch", key="dash_new"):
        goto(PAGE_NEW)
    with st.expander("🧭 5-minute tour"):
        st.markdown(
            "1. **➕ New Lead** — paste a text or email and it pulls out the name, phone, address, service and deadline.\n"
            "2. **📊 Pipeline** — every lead lives here; open a file to move it forward.\n"
            "3. **📋 Working Files** → open *DEMO — Reyes Residence*: Subject & Scope, Comps & Adjustments (import an MLS CSV), "
            "then **📄 Report Materials** → *Prepare report materials (Word draft)*.\n"
            "4. **📈 Run Reports** → profit & loss, receivables, compliance.\n"
            "5. **⚙️ Settings & Libraries** → the narrative library: replace the sample language with your own.\n\n"
            "Notice anything slow or missing? Jot it down — that's what gets built next.")
    df = list_leads()
    if df.empty:
        st.info("No files yet — add your first lead, or load the full demo to see everything filled in.")
        if st.button("✨ Load full demo", type="primary", key="dash_demo"):
            seed_full_demo()
            st.rerun()
        return

    rf = report_frame(df)
    exp, mil = load_expenses(), load_mileage()
    snap = pipeline_snapshot(rf, today)
    due = df[df["status"].isin(PIPELINE_STATUSES)].copy()
    due["_d"] = due["deadline"].apply(parse_iso)
    n_over = int(due["_d"].apply(lambda d: d is not None and d < today).sum())
    n_soon = int(due["_d"].apply(lambda d: d is not None and 0 <= (d - today).days <= 3).sum())
    fu = followup_list(rf, today)

    if n_over:
        st.error(f"🔴 {n_over} file(s) are past their target delivery date — see Due soon below.")

    # ---- needs attention ----
    a = st.columns(4)
    a[0].metric("Past due", n_over, help="Open files whose target delivery date has passed.")
    a[1].metric("Due in 3 days", n_soon)
    a[2].metric("Follow-ups waiting", len(fu), help="Inquiries and quotes untouched for 3+ days.")
    a[3].metric("Delivered, unpaid", f"{snap['ar_n']} · {_money(snap['ar_v'])}")

    # ---- due soon + pipeline ----
    left, right = st.columns([3, 2])
    with left:
        with st.container(border=True):
            st.markdown("**📅 Due soon**")
            ds = due_soon(df, today)
            if ds.empty:
                st.caption("No open files with a delivery date.")
            else:
                ev = st.dataframe(ds.drop(columns="id"), hide_index=True, on_select="rerun", selection_mode="single-row",
                                  key="dash_due", column_config={"Fee": st.column_config.NumberColumn(format="$%d")})
                rows = ev.selection.rows if ev and ev.selection else []
                if st.button("📋 Open selected file", disabled=not rows, key="dash_open"):
                    goto(PAGE_WORKFILE, int(ds.iloc[rows[0]]["id"]))
                st.caption("Click a row, then open it.")
    with right:
        with st.container(border=True):
            st.markdown("**🧭 Pipeline**")
            sc = stage_counts(df)
            if sc["Files"].sum():
                st.bar_chart(sc.set_index("Stage")["Files"], horizontal=True, height=190)
            m = st.columns(2)
            m[0].metric("Quotes out", f"{snap['quoted_n']} · {_money(snap['quoted_v'])}")
            m[1].metric("Booked", f"{snap['booked_n']} · {_money(snap['booked_v'])}")
            if st.button("Open full pipeline →", key="dash_pipe"):
                goto(PAGE_PIPELINE)

    # ---- this month ----
    ms, me = period_bounds("This month", today)
    ps, pe = period_bounds("Last month", today)
    cur, prev = pnl(rf, exp, mil, ms, me, "cash"), pnl(rf, exp, mil, ps, pe, "cash")
    earned = business_kpis(rf, ms, me)["earned"]
    with st.container(border=True):
        st.markdown(f"**💰 {today:%B}** (cash basis)")
        k = st.columns(4)
        k[0].metric("Collected", _money(cur["revenue"]), delta=_vs_last(cur["revenue"], prev["revenue"]))
        k[1].metric("Earned (delivered)", _money(earned))
        k[2].metric("Expenses + mileage", _money(cur["expenses"] + cur["mileage"]))
        k[3].metric("Net", _money(cur["net"]), delta=_vs_last(cur["net"], prev["net"]))
        tr = monthly_pnl(rf, exp, mil, add_months(today.replace(day=1), -5), today, "cash")
        if tr[["Revenue", "Costs"]].sum().sum() > 0:
            st.bar_chart(tr[["Revenue", "Costs"]], stack=False, height=210)
        else:
            st.caption("Revenue and costs for the last six months will chart here once payments and expenses are recorded.")

    # ---- map + activity ----
    mcol, acol = st.columns([3, 2])
    with mcol:
        with st.container(border=True):
            st.markdown("**🗺️ Where the work is**")
            view = st.radio("Show", list(MAP_VIEWS), horizontal=True, key="dash_map_view", label_visibility="collapsed")
            pts, unplaced = map_points(df, view)
            if pts.empty:
                st.caption("No files to plot for this view yet.")
            else:
                st.map(pts, latitude="lat", longitude="lon", size="size", color="color", zoom=8, height=330)
                st.dataframe(pts[["Place", "Files", "Fees"]], hide_index=True, height=150,
                             column_config={"Fees": st.column_config.NumberColumn(format="$%d")})
            st.caption("Each dot is a city centre sized by file count — not the property's street address."
                       + (f" {unplaced} file(s) have no recognised city or county." if unplaced else ""))
    with acol:
        with st.container(border=True):
            st.markdown("**🕒 Recent activity**")
            acts = recent_activity(8)
            if not acts:
                st.caption("Nothing yet.")
            for r in acts:
                who = r["client_name"] or "—"
                st.markdown(f"**{who}** — {r['message']}  \n<span style='color:gray;font-size:0.8em'>{(r['ts'] or '')[:16]}</span>",
                            unsafe_allow_html=True)


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
            if b2.button("📋 Open working file"):
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
    st.title("📋 Working Files")
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

    t_over, t_subj, t_comps, t_rm, t_ready = st.tabs(
        ["📌 Overview", "🏠 Subject & Scope", "📁 Comps & Adjustments", "📄 Report Materials", "✅ Readiness & Export"])
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
    with t_rm:
        render_report_materials(lid, get_lead(lid))

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
    st.title("📈 Run Reports")
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
# ----------------------------------------------------------------------
# REPORT MATERIALS (per-file tab) + SETTINGS / LIBRARIES PAGE
# ----------------------------------------------------------------------
def _save_report(lid, updates):
    rep = load_report(get_lead(lid))
    rep.update(updates)
    update_lead(lid, report_json=json.dumps(rep))


def _rm_insert(key, pick_key, ctx):
    nid = st.session_state.get(pick_key)
    row = next((n for n in list_narratives() if n["id"] == nid), None)
    if row:
        st.session_state[key] = fill_placeholders(row["body"], ctx)[0]


def _rm_refill(key, ctx):
    st.session_state[key] = fill_placeholders(st.session_state.get(key, ""), ctx)[0]


def render_report_materials(lid, lead):
    today = date.today()
    facts = load_facts(lead)
    saved = load_report(lead)
    calc = compute_comps(load_comps(lead))
    unused = normalize_unused(load_unused(lead))
    ctx = report_context(lead, facts, calc, today)
    cur = {s: st.session_state.get(f"rm_{lid}_{i}", saved.get(s, "")) for i, s in enumerate(REPORT_SECTIONS)}
    cur["_docs"] = st.session_state.get(f"rm_docs_{lid}", saved.get("_docs", []))
    checks, passed = report_checks(lead, calc, facts, cur, today)
    n_written = sum(1 for s in REPORT_SECTIONS if (cur.get(s) or "").strip())
    cnt = {k: sum(1 for c in checks if c["sev"] == k) for k in SEV_ORDER}

    st.caption("Everything you enter here is reused in the Word draft — nothing is written by AI, and no value is ever "
               "suggested. Comps and adjustments come from the saved Comps & Adjustments tab.")
    m = st.columns(5)
    m[0].metric("Narratives", f"{n_written} / {len(REPORT_SECTIONS)}")
    m[1].metric("🔴 Needs fixing", cnt["fix"])
    m[2].metric("🟡 Missing", cnt["missing"])
    m[3].metric("🔵 To review", cnt["review"])
    m[4].metric("✅ Checks passed", passed)
    try:
        docx_bytes = build_report_docx(lead, calc, unused, facts, cur, checks, today)
        st.download_button("📄 Prepare report materials (Word draft)", docx_bytes, type="primary", key=f"rm_dl_{lid}",
                           file_name=f"report_materials_{slug((lead['client_name'] or 'file').lower())}_{today.isoformat()}.docx",
                           mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        st.caption("Yellow highlights in the Word file mark text you still need to complete or confirm. Unsaved edits below are included.")
    except ImportError:
        st.error("The Word export needs the python-docx package (add `python-docx` to requirements.txt).")

    t_nar, t_facts, t_chk, t_docs = st.tabs(["📝 Narratives", "🏠 Property data sheet", "✅ Completeness check", "🗂️ Workfile documents"])

    with t_nar:
        lib = list_narratives()
        st.caption("Pick language from your library for each section, adjust it, and save. Known details (client, dates, "
                   "address, GLA, comps…) fill in automatically; anything in [BRACKETS] is yours to complete.")
        for i, sec in enumerate(REPORT_SECTIONS):
            key = f"rm_{lid}_{i}"
            if key not in st.session_state:
                st.session_state[key] = saved.get(sec, "")
            done = bool((st.session_state[key] or "").strip())
            with st.expander(("✅ " if done else "⬜ ") + sec, expanded=False):
                opts = [n for n in lib if n["section"] == sec]
                by_id = {n["id"]: n["title"] for n in opts}
                c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
                pk = f"pick_{lid}_{i}"
                pick = c1.selectbox("Insert from library", [n["id"] for n in opts], index=None,
                                    format_func=lambda x, b=by_id: b[x], placeholder="Choose saved language…", key=pk)
                c2.button("Use this language", key=f"use_{lid}_{i}", disabled=pick is None, on_click=_rm_insert,
                          args=(key, pk, ctx), width="stretch")
                st.text_area(sec, key=key, height=170, label_visibility="collapsed")
                text = st.session_state[key] or ""
                filled, left = fill_placeholders(text, ctx)
                if filled != text:
                    st.button("Fill known details", key=f"refill_{lid}_{i}", on_click=_rm_refill, args=(key, ctx))
                if left:
                    st.caption("To complete: " + ", ".join(f"`[{t}]`" for t in left))
                if text.strip():
                    with st.popover("➕ Save to my library"):
                        st.caption("Replace client-specific details with [PLACEHOLDERS] first so it can be reused.")
                        ttl = st.text_input("Title", key=f"ttl_{lid}_{i}")
                        if st.button("Save to library", key=f"sv_{lid}_{i}"):
                            add_narrative(sec, ttl or f"{sec} — saved from file #{lid}", text)
                            flash("success", "Added to your narrative library.")
                            st.rerun()
        if st.button("💾 Save narratives", type="primary", key=f"save_nar_{lid}"):
            _save_report(lid, {s: st.session_state.get(f"rm_{lid}_{i}", "") for i, s in enumerate(REPORT_SECTIONS)})
            log_activity(lid, "Report narratives saved")
            flash("success", "Narratives saved.")
            st.rerun()

    with t_facts:
        st.caption("Enter each fact once and tag where it came from. MLS and client-provided facts are flagged as unverified "
                   "until you confirm them — so an unchecked MLS field never quietly becomes a reported fact.")
        edited = st.data_editor(
            facts_frame(lead), hide_index=True, key=f"facts_{lid}", disabled=["Field"], width="stretch",
            column_config={"Source": st.column_config.SelectboxColumn("Source", options=FACT_SOURCES[1:], help="Where did this come from?")})
        nv = sum(1 for r in edited.to_dict("records") if not _isblank(r["Value"]) and r["Source"] in VERIFIED_SOURCES)
        nu = sum(1 for r in edited.to_dict("records") if not _isblank(r["Value"]) and r["Source"] not in VERIFIED_SOURCES)
        st.caption(f"{nv} verified · {nu} unverified or unlabeled")
        if st.button("💾 Save property data sheet", key=f"save_facts_{lid}", type="primary"):
            update_lead(lid, facts_json=facts_to_json(edited), log="Property data sheet updated")
            flash("success", "Property data sheet saved.")
            st.rerun()

    with t_chk:
        if not checks:
            st.success("No open items — the completeness check found nothing to resolve.")
        for sev in ("fix", "missing", "review"):
            items = [c for c in checks if c["sev"] == sev]
            if items:
                st.markdown(f"**{SEV_LABEL[sev]}** ({len(items)})")
                for c in items:
                    st.markdown(f"- **{c['area']}** — {c['msg']}")
        st.caption("🔴 is a mathematical or factual conflict, 🟡 is information or documentation not yet entered, and 🔵 is "
                   "something only your professional judgment can settle. This is a prompt, not a substitute for your own review.")

    with t_docs:
        st.caption("Tick what is already in the workfile folder. Missing items appear in the completeness check and the Word draft.")
        sel = st.multiselect("On file", DOC_ITEMS, default=[d for d in saved.get("_docs", []) if d in DOC_ITEMS], key=f"rm_docs_{lid}")
        if st.button("💾 Save", key=f"save_docs_{lid}"):
            _save_report(lid, {"_docs": sel})
            flash("success", "Workfile documents saved.")
            st.rerun()
        st.caption("USPAP's Record Keeping Rule governs what the workfile must contain — confirm against the current edition.")


def _fees_tab():
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


def _library_tab():
    st.markdown("Your own approved language, organized by report section. Starter paragraphs are marked **Sample** — edit them into "
                "your voice or replace them. Nothing here is AI-generated.")
    with st.expander("Placeholders that fill in automatically"):
        st.markdown(", ".join(f"`[{k}]`" for k in sorted(TOKEN_HELP)))
        st.caption("Any other [CAPITALIZED_TOKEN] you write simply stays highlighted for you to complete. "
                   "Example: `[MARKET_TREND]`, `[NEIGHBORHOOD]`.")
    sec = st.selectbox("Section", ["All sections"] + REPORT_SECTIONS, key="lib_sec")
    rows = list_narratives(None if sec == "All sections" else sec)
    st.caption(f"{len(rows)} saved passage(s)")
    with st.expander("➕ Add new language"):
        with st.form("lib_add", clear_on_submit=True):
            ns = st.selectbox("Section", REPORT_SECTIONS, key="lib_new_sec")
            nt = st.text_input("Title")
            nb = st.text_area("Language", height=140, help="Use [PLACEHOLDERS] for anything that changes per assignment.")
            if st.form_submit_button("Add to library", type="primary"):
                if not nb.strip():
                    st.error("Enter the language to save.")
                else:
                    add_narrative(ns, nt, nb)
                    flash("success", "Added.")
                    st.rerun()
    for r in rows:
        with st.expander(f"{r['section']} — {r['title']}" if sec == "All sections" else r["title"]):
            t = st.text_input("Title", value=r["title"], key=f"lt_{r['id']}")
            b = st.text_area("Language", value=r["body"], height=150, key=f"lb_{r['id']}")
            c1, c2, _ = st.columns([1, 1, 4])
            if c1.button("Save", key=f"ls_{r['id']}", type="primary"):
                update_narrative(r["id"], t, b)
                flash("success", "Saved.")
                st.rerun()
            if c2.button("Delete", key=f"ld_{r['id']}"):
                delete_narrative(r["id"])
                flash("success", "Deleted.")
                st.rerun()
    st.markdown("---")
    st.markdown("**Back up or move your library**")
    d1, d2 = st.columns(2)
    d1.download_button("⬇️ Export library (.json)", export_narratives_json(), file_name="narrative_library.json",
                       mime="application/json")
    up = d2.file_uploader("Import a library file", type="json", key="lib_up", label_visibility="collapsed")
    if up is not None and st.button("Merge imported library"):
        try:
            a, s_ = import_narratives_json(up.getvalue().decode("utf-8"))
            flash("success", f"Imported {a} passage(s); skipped {s_} duplicate or invalid.")
            st.rerun()
        except (ValueError, UnicodeDecodeError):
            st.error("That file isn't a valid library export.")


def page_settings():
    st.title("⚙️ Settings & Libraries")
    t_fee, t_lib = st.tabs(["💵 Fee Schedule", "📚 Narrative Library"])
    with t_fee:
        _fees_tab()
    with t_lib:
        _library_tab()


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
    st.session_state.setdefault("page", PAGE_DASH)

    with st.sidebar:
        st.header("⚖️ Appraisal Desk")
        st.caption("Clearfork Appraisals")
        st.text(f"{APPRAISER['firm']}")
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
                if st.button("✨ Load full demo (best for a walkthrough)", type="primary"):
                    seed_full_demo()
                    st.rerun()
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
        if not leads.empty:
            if get_setting("demo_lead_ids"):
                with st.expander("Full demo data"):
                    sure_d = st.checkbox("Remove all demo files, costs and trips", key="demo_del_chk")
                    if st.button("🧹 Remove demo data", disabled=not sure_d):
                        flash("success", f"Removed {delete_demo()} demo file(s) and their costs.")
                        st.rerun()
            else:
                with st.expander("Demo tools"):
                    st.caption("Adds fictional files, costs and a fully worked example. Fully removable.")
                    if st.button("✨ Add full demo data"):
                        seed_full_demo()
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
    if page == PAGE_DASH:
        page_dashboard()
    elif page == PAGE_PIPELINE:
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
        page_settings()


main()
