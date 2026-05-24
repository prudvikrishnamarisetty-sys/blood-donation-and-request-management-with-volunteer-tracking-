import os
import sqlite3
import random
import uuid
import math
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, date, timedelta
from functools import wraps
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    g,
    jsonify,
)
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "bloodbank.db")

app = Flask(__name__)
app.secret_key = "bloodflow-secret-2024"

# Gmail SMTP config — set via environment variables for security
GMAIL_USER = os.environ.get("GMAIL_USER", "bloodflowapp2024@gmail.com")
GMAIL_PASS = os.environ.get("GMAIL_PASS", "")  # App password


def send_otp_email(to_email, otp_code, user_name=""):
    """Send OTP verification email via Gmail SMTP."""
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "🩸 BloodFlow - Email Verification OTP"
        msg["From"] = GMAIL_USER
        msg["To"] = to_email
        html = f"""
        <div style="font-family:Arial,sans-serif;max-width:480px;margin:auto;padding:24px;background:#1a1a2e;color:#fff;border-radius:12px">
          <h2 style="color:#ef4444">🩸 BloodFlow Verification</h2>
          <p>Hello {user_name or 'there'},</p>
          <p>Your OTP for email verification is:</p>
          <div style="font-size:2.5rem;font-weight:bold;letter-spacing:12px;text-align:center;padding:20px;
            background:#2d2d44;border-radius:10px;color:#ef4444;margin:20px 0">{otp_code}</div>
          <p style="color:#aaa">This OTP expires in <b>10 minutes</b>. Do not share it with anyone.</p>
          <p style="color:#aaa;font-size:0.85rem">If you did not register on BloodFlow, ignore this email.</p>
        </div>"""
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_PASS)
            server.sendmail(GMAIL_USER, to_email, msg.as_string())
        return True
    except Exception as e:
        app.logger.error(f"OTP email failed: {e}")
        return False


BLOOD_GROUPS = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]
RARE_GROUPS = ["AB-", "O-", "B-", "A-"]
ROLES = ["donor", "volunteer", "hospital", "admin", "blood_bank"]
MAX_DONOR_RANGE_KM = 50  # Maximum km radius for notifying nearby donors
GENDERS = ["Male", "Female", "Other"]
PRIORITIES = ["Low", "Medium", "High", "Critical"]
REQUEST_STATUSES = ["Pending", "Approved", "Allocated", "Fulfilled", "Closed"]
COMPATIBLE = {
    "A+": ["A+", "A-", "O+", "O-"],
    "A-": ["A-", "O-"],
    "B+": ["B+", "B-", "O+", "O-"],
    "B-": ["B-", "O-"],
    "AB+": ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"],
    "AB-": ["A-", "B-", "AB-", "O-"],
    "O+": ["O+", "O-"],
    "O-": ["O-"],
}


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(e=None):
    db = g.pop("db", None)
    if db:
        db.close()


def query_db(q, args=(), one=False):
    cur = get_db().execute(q, args)
    rv = cur.fetchall()
    cur.close()
    return (rv[0] if rv else None) if one else rv


def execute_db(q, args=()):
    db = get_db()
    cur = db.execute(q, args)
    db.commit()
    return cur.lastrowid


def create_notification(user_id, title, message, ntype="info", blood_group=None):
    execute_db(
        "INSERT INTO notifications (user_id,title,message,ntype,blood_group) VALUES (?,?,?,?,?)",
        (user_id, title, message, ntype, blood_group),
    )


def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(a))


def get_city(address):
    """Extract a normalized city keyword from an address string."""
    if not address:
        return ""
    # Use last meaningful word or first comma-separated segment as city
    parts = [p.strip().lower() for p in address.split(",")]
    return parts[-1] if parts else address.strip().lower()


def same_city(addr1, addr2):
    """Return True if two addresses share the same city keyword."""
    c1, c2 = get_city(addr1), get_city(addr2)
    if not c1 or not c2:
        return True  # unknown location — allow notification
    return c1 == c2 or c1 in c2 or c2 in c1


def is_nearby(
    user_lat, user_lng, target_lat, target_lng, addr1=None, addr2=None, radius_km=50
):
    """Return True if target is within radius_km, or same city when coords unavailable."""
    if user_lat and user_lng and target_lat and target_lng:
        return haversine(user_lat, user_lng, target_lat, target_lng) <= radius_km
    # Fallback: city name match
    return same_city(addr1, addr2)


def match_donors(blood_group, hospital_lat=None, hospital_lng=None, limit=10):
    compatible = COMPATIBLE.get(blood_group, [blood_group])
    cutoff = (date.today() - timedelta(days=90)).isoformat()
    donors = query_db(
        """SELECT u.*,
        COALESCE((SELECT MAX(donation_date) FROM donations WHERE donor_id=u.id AND donation_date IS NOT NULL),'') as last_donation
        FROM users u WHERE u.role='donor' AND u.is_available=1 AND u.is_verified=1
        AND u.blood_group IN ({})""".format(",".join("?" * len(compatible))),
        compatible,
    )
    results = []
    for d in donors:
        if d["last_donation"] and d["last_donation"] >= cutoff:
            continue
        score = 100
        if d["blood_group"] in RARE_GROUPS:
            score += 30
        if hospital_lat and hospital_lng and d["latitude"] and d["longitude"]:
            dist = haversine(hospital_lat, hospital_lng, d["latitude"], d["longitude"])
            score += max(0, 50 - dist * 2)
        if not d["last_donation"]:
            score += 10
        results.append({"donor": dict(d), "score": score})
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:limit]


def notify_low_inventory():
    low = query_db("SELECT * FROM inventory WHERE units <= threshold_units")
    for item in low:
        donors = query_db(
            'SELECT id FROM users WHERE role="donor" AND blood_group=? AND is_verified=1 AND is_available=1',
            (item["blood_group"],),
        )
        for d in donors:
            existing = query_db(
                'SELECT id FROM notifications WHERE user_id=? AND title=? AND DATE(created_at)=DATE("now")',
                (d["id"], f"Urgent: {item['blood_group']} needed"),
                one=True,
            )
            if not existing:
                create_notification(
                    d["id"], f"Urgent: {
                        item['blood_group']} needed", f"Blood inventory for {
                        item['blood_group']} is critically low ({
                        item['units']} units). Please consider donating.", "urgent", item["blood_group"], )


def log_audit(request_id, actor_id, action, old_status, new_status, note=""):
    execute_db(
        "INSERT INTO request_audit (request_id,actor_id,action,old_status,new_status,note) VALUES (?,?,?,?,?,?)",
        (request_id,
         actor_id,
         action,
         old_status,
         new_status,
         note),
    )


def init_db():
    db = sqlite3.connect(DB_PATH)
    c = db.cursor()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        full_name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, phone TEXT,
        password_hash TEXT NOT NULL, role TEXT NOT NULL,
        blood_group TEXT, gender TEXT, age INTEGER, address TEXT,
        latitude REAL, longitude REAL,
        hospital_type TEXT, org_reg_no TEXT,
        is_available INTEGER DEFAULT 1, is_verified INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS otps (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        otp_code TEXT NOT NULL, purpose TEXT NOT NULL, expires_at TEXT NOT NULL,
        is_used INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS blood_units (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        unit_uid TEXT UNIQUE NOT NULL,
        blood_group TEXT NOT NULL, volume_ml INTEGER DEFAULT 450,
        source TEXT DEFAULT 'donation',
        donor_id INTEGER, blood_bank_id INTEGER,
        collection_date TEXT NOT NULL,
        expiry_date TEXT NOT NULL,
        status TEXT DEFAULT 'Available',
        allocated_to INTEGER, used_by_request INTEGER,
        discard_reason TEXT,
        notes TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (donor_id) REFERENCES users(id),
        FOREIGN KEY (blood_bank_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS bb_inventory (
        blood_bank_id INTEGER NOT NULL,
        blood_group TEXT NOT NULL,
        units INTEGER DEFAULT 0,
        threshold_units INTEGER DEFAULT 10,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (blood_bank_id, blood_group)
    );
    CREATE TABLE IF NOT EXISTS incident_reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reporter_id INTEGER NOT NULL,
        report_type TEXT NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        severity TEXT DEFAULT 'Low',
        status TEXT DEFAULT 'Open',
        admin_note TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        resolved_at TEXT,
        FOREIGN KEY (reporter_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS inventory (
        blood_group TEXT PRIMARY KEY,
        units INTEGER NOT NULL DEFAULT 0,
        threshold_units INTEGER NOT NULL DEFAULT 20,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS bb_donation_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        donor_id INTEGER NOT NULL,
        blood_bank_id INTEGER NOT NULL,
        blood_group TEXT NOT NULL,
        units INTEGER DEFAULT 1,
        note TEXT,
        status TEXT DEFAULT 'Pending',
        volunteer_id INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (donor_id) REFERENCES users(id),
        FOREIGN KEY (blood_bank_id) REFERENCES users(id),
        FOREIGN KEY (volunteer_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS blood_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        hospital_id INTEGER NOT NULL, volunteer_id INTEGER, donor_id INTEGER,
        patient_name TEXT, blood_group TEXT NOT NULL, units INTEGER NOT NULL,
        priority TEXT DEFAULT 'Medium', location TEXT, note TEXT,
        status TEXT DEFAULT 'Pending', donor_status TEXT DEFAULT 'Pending',
        allocated_units TEXT,
        feedback TEXT, feedback_rating INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (hospital_id) REFERENCES users(id),
        FOREIGN KEY (volunteer_id) REFERENCES users(id),
        FOREIGN KEY (donor_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS request_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id INTEGER NOT NULL, actor_id INTEGER,
        action TEXT NOT NULL, old_status TEXT, new_status TEXT, note TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (request_id) REFERENCES blood_requests(id)
    );
    CREATE TABLE IF NOT EXISTS donations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        donor_id INTEGER NOT NULL, hospital_id INTEGER, volunteer_id INTEGER,
        blood_group TEXT NOT NULL, units INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'requested',
        requested_by TEXT DEFAULT 'donor', request_note TEXT,
        last_donated_date TEXT, donation_date TEXT, handover_date TEXT, completed_at TEXT,
        chk_age_weight INTEGER DEFAULT 0, chk_tattoos INTEGER DEFAULT 0,
        chk_symptoms INTEGER DEFAULT 0, chk_surgery INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (donor_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS volunteer_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id INTEGER NOT NULL, volunteer_id INTEGER NOT NULL,
        task_type TEXT DEFAULT 'delivery',
        status TEXT DEFAULT 'Assigned',
        pickup_location TEXT, delivery_location TEXT,
        assigned_at TEXT DEFAULT CURRENT_TIMESTAMP,
        collected_at TEXT, delivered_at TEXT, notes TEXT,
        FOREIGN KEY (request_id) REFERENCES blood_requests(id),
        FOREIGN KEY (volunteer_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, title TEXT NOT NULL, message TEXT NOT NULL,
        ntype TEXT DEFAULT 'info', blood_group TEXT,
        is_read INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS gps_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        volunteer_id INTEGER NOT NULL, latitude REAL NOT NULL, longitude REAL NOT NULL,
        label TEXT DEFAULT 'Available', task_id INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (volunteer_id) REFERENCES users(id)
    );
    """)
    # Migrate: add blood_bank_id column if not exists
    try:
        c.execute("ALTER TABLE blood_units ADD COLUMN blood_bank_id INTEGER")
    except BaseException:
        pass
    try:
        c.execute("ALTER TABLE blood_units ADD COLUMN discard_reason TEXT")
    except BaseException:
        pass
    try:
        c.execute("ALTER TABLE blood_requests ADD COLUMN blood_bank_id INTEGER")
    except BaseException:
        pass
    for bg in BLOOD_GROUPS:
        c.execute(
            "INSERT OR IGNORE INTO inventory (blood_group,units,threshold_units) VALUES (?,?,?)",
            (bg, 35, 20),
        )
    if c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        seed_users = [
            (
                "Admin User",
                "admin@bloodflow.com",
                "9999990001",
                "admin",
                None,
                None,
                None,
                "Head Office",
                None,
                None,
                None,
                None,
            ),
            # ── 20 Donors ───────────────────────────────────────────────────────────
            (
                "Asha Mehta",
                "donor1@bloodflow.com",
                "9876543201",
                "donor",
                "B-",
                "Female",
                27,
                "Vijayawada, Andhra Pradesh",
                16.5062,
                80.6480,
                None,
                None,
            ),
            (
                "Ravi Kumar",
                "donor2@bloodflow.com",
                "9876543202",
                "donor",
                "O+",
                "Male",
                31,
                "Guntur, Andhra Pradesh",
                16.3067,
                80.4365,
                None,
                None,
            ),
            (
                "Priya Sharma",
                "donor3@bloodflow.com",
                "9876543203",
                "donor",
                "AB-",
                "Female",
                24,
                "Hyderabad, Telangana",
                17.3850,
                78.4867,
                None,
                None,
            ),
            (
                "Suresh Reddy",
                "donor4@bloodflow.com",
                "9876543204",
                "donor",
                "A+",
                "Male",
                35,
                "Vijayawada, Andhra Pradesh",
                16.5120,
                80.6390,
                None,
                None,
            ),
            (
                "Kavitha Nair",
                "donor5@bloodflow.com",
                "9876543205",
                "donor",
                "B+",
                "Female",
                29,
                "Anakapalli, Andhra Pradesh",
                17.6974,
                83.0069,
                None,
                None,
            ),
            (
                "Mahesh Babu",
                "donor6@bloodflow.com",
                "9876543206",
                "donor",
                "O-",
                "Male",
                40,
                "Guntur, Andhra Pradesh",
                16.3100,
                80.4300,
                None,
                None,
            ),
            (
                "Anitha Rao",
                "donor7@bloodflow.com",
                "9876543207",
                "donor",
                "A-",
                "Female",
                22,
                "Vijayawada, Andhra Pradesh",
                16.5080,
                80.6510,
                None,
                None,
            ),
            (
                "Kiran Patel",
                "donor8@bloodflow.com",
                "9876543208",
                "donor",
                "AB+",
                "Male",
                33,
                "Hyderabad, Telangana",
                17.3900,
                78.4800,
                None,
                None,
            ),
            (
                "Deepa Krishnan",
                "donor9@bloodflow.com",
                "9876543209",
                "donor",
                "O+",
                "Female",
                26,
                "Guntur, Andhra Pradesh",
                16.3020,
                80.4400,
                None,
                None,
            ),
            (
                "Venkat Rao",
                "donor10@bloodflow.com",
                "9876543210",
                "donor",
                "B+",
                "Male",
                45,
                "Anakapalli, Andhra Pradesh",
                17.7010,
                83.0120,
                None,
                None,
            ),
            (
                "Lakshmi Devi",
                "donor11@bloodflow.com",
                "9876543211",
                "donor",
                "A+",
                "Female",
                30,
                "Guntur, Andhra Pradesh",
                16.3150,
                80.4350,
                None,
                None,
            ),
            (
                "Rajesh Kumar",
                "donor12@bloodflow.com",
                "9876543212",
                "donor",
                "B-",
                "Male",
                28,
                "Vijayawada, Andhra Pradesh",
                16.4980,
                80.6560,
                None,
                None,
            ),
            (
                "Sunita Mishra",
                "donor13@bloodflow.com",
                "9876543213",
                "donor",
                "O+",
                "Female",
                23,
                "Hyderabad, Telangana",
                17.3820,
                78.4900,
                None,
                None,
            ),
            (
                "Arun Pillai",
                "donor14@bloodflow.com",
                "9876543214",
                "donor",
                "AB-",
                "Male",
                37,
                "Anakapalli, Andhra Pradesh",
                17.6940,
                83.0030,
                None,
                None,
            ),
            (
                "Meena Gupta",
                "donor15@bloodflow.com",
                "9876543215",
                "donor",
                "A+",
                "Female",
                31,
                "Vijayawada, Andhra Pradesh",
                16.5140,
                80.6420,
                None,
                None,
            ),
            (
                "Srinivasa Raju",
                "donor16@bloodflow.com",
                "9876543216",
                "donor",
                "O-",
                "Male",
                42,
                "Guntur, Andhra Pradesh",
                16.3080,
                80.4380,
                None,
                None,
            ),
            (
                "Pooja Verma",
                "donor17@bloodflow.com",
                "9876543217",
                "donor",
                "B+",
                "Female",
                25,
                "Hyderabad, Telangana",
                17.3870,
                78.4840,
                None,
                None,
            ),
            (
                "Naresh Babu",
                "donor18@bloodflow.com",
                "9876543218",
                "donor",
                "A-",
                "Male",
                38,
                "Vijayawada, Andhra Pradesh",
                16.5090,
                80.6490,
                None,
                None,
            ),
            (
                "Geeta Sinha",
                "donor19@bloodflow.com",
                "9876543219",
                "donor",
                "AB+",
                "Female",
                27,
                "Anakapalli, Andhra Pradesh",
                17.6985,
                83.0085,
                None,
                None,
            ),
            (
                "Prasad Murthy",
                "donor20@bloodflow.com",
                "9876543220",
                "donor",
                "O+",
                "Male",
                50,
                "Guntur, Andhra Pradesh",
                16.3010,
                80.4440,
                None,
                None,
            ),
            # ── 10 Volunteers ───────────────────────────────────────────────────────
            (
                "Meera Volunteer",
                "volunteer1@bloodflow.com",
                "9876543241",
                "volunteer",
                None,
                "Female",
                25,
                "Vijayawada, Andhra Pradesh",
                16.5062,
                80.6480,
                None,
                None,
            ),
            (
                "Arjun Volunteer",
                "volunteer2@bloodflow.com",
                "9876543242",
                "volunteer",
                None,
                "Male",
                29,
                "Guntur, Andhra Pradesh",
                16.3067,
                80.4365,
                None,
                None,
            ),
            (
                "Divya Nath",
                "volunteer3@bloodflow.com",
                "9876543243",
                "volunteer",
                None,
                "Female",
                27,
                "Hyderabad, Telangana",
                17.3840,
                78.4880,
                None,
                None,
            ),
            (
                "Suraj Menon",
                "volunteer4@bloodflow.com",
                "9876543244",
                "volunteer",
                None,
                "Male",
                32,
                "Anakapalli, Andhra Pradesh",
                17.6974,
                83.0069,
                None,
                None,
            ),
            (
                "Rekha Pandey",
                "volunteer5@bloodflow.com",
                "9876543245",
                "volunteer",
                None,
                "Female",
                26,
                "Vijayawada, Andhra Pradesh",
                16.5020,
                80.6510,
                None,
                None,
            ),
            (
                "Anand Kumar",
                "volunteer6@bloodflow.com",
                "9876543246",
                "volunteer",
                None,
                "Male",
                35,
                "Guntur, Andhra Pradesh",
                16.3100,
                80.4370,
                None,
                None,
            ),
            (
                "Nisha Srivastava",
                "volunteer7@bloodflow.com",
                "9876543247",
                "volunteer",
                None,
                "Female",
                28,
                "Hyderabad, Telangana",
                17.3870,
                78.4830,
                None,
                None,
            ),
            (
                "Vinod Gupta",
                "volunteer8@bloodflow.com",
                "9876543248",
                "volunteer",
                None,
                "Male",
                33,
                "Anakapalli, Andhra Pradesh",
                17.7010,
                83.0120,
                None,
                None,
            ),
            (
                "Archana Sharma",
                "volunteer9@bloodflow.com",
                "9876543249",
                "volunteer",
                None,
                "Female",
                30,
                "Vijayawada, Andhra Pradesh",
                16.5105,
                80.6445,
                None,
                None,
            ),
            (
                "Prakash Varma",
                "volunteer10@bloodflow.com",
                "9876543250",
                "volunteer",
                None,
                "Male",
                38,
                "Guntur, Andhra Pradesh",
                16.3000,
                80.4440,
                None,
                None,
            ),
            # ── 10 Hospitals ────────────────────────────────────────────────────────
            (
                "City Care Hospital",
                "hospital1@bloodflow.com",
                "9900000001",
                "hospital",
                None,
                None,
                None,
                "Vijayawada, Andhra Pradesh",
                16.5062,
                80.6480,
                "General",
                "HOSP001",
            ),
            (
                "Apollo Medics Vijayawada",
                "hospital2@bloodflow.com",
                "9900000002",
                "hospital",
                None,
                None,
                None,
                "Vijayawada, Andhra Pradesh",
                16.5150,
                80.6390,
                "Specialty",
                "HOSP002",
            ),
            (
                "Guntur Government Hospital",
                "hospital3@bloodflow.com",
                "9900000003",
                "hospital",
                None,
                None,
                None,
                "Guntur, Andhra Pradesh",
                16.3067,
                80.4365,
                "Government",
                "HOSP003",
            ),
            (
                "Care Foundation Guntur",
                "hospital4@bloodflow.com",
                "9900000004",
                "hospital",
                None,
                None,
                None,
                "Guntur, Andhra Pradesh",
                16.3100,
                80.4300,
                "Specialty",
                "HOSP004",
            ),
            (
                "KIMS Hyderabad",
                "hospital5@bloodflow.com",
                "9900000005",
                "hospital",
                None,
                None,
                None,
                "Hyderabad, Telangana",
                17.4239,
                78.4738,
                "Corporate",
                "HOSP005",
            ),
            (
                "Yashoda Hospitals Hyderabad",
                "hospital6@bloodflow.com",
                "9900000006",
                "hospital",
                None,
                None,
                None,
                "Hyderabad, Telangana",
                17.3850,
                78.4867,
                "Specialty",
                "HOSP006",
            ),
            (
                "Anakapalli District Hospital",
                "hospital7@bloodflow.com",
                "9900000007",
                "hospital",
                None,
                None,
                None,
                "Anakapalli, Andhra Pradesh",
                17.6974,
                83.0069,
                "Government",
                "HOSP007",
            ),
            (
                "Surya Multi-Specialty Anakapalli",
                "hospital8@bloodflow.com",
                "9900000008",
                "hospital",
                None,
                None,
                None,
                "Anakapalli, Andhra Pradesh",
                17.7010,
                83.0120,
                "Specialty",
                "HOSP008",
            ),
            (
                "Sri Ramachandra Hospital Vijayawada",
                "hospital9@bloodflow.com",
                "9900000009",
                "hospital",
                None,
                None,
                None,
                "Vijayawada, Andhra Pradesh",
                16.4980,
                80.6560,
                "Multi-Specialty",
                "HOSP009",
            ),
            (
                "Medicover Hospitals Hyderabad",
                "hospital10@bloodflow.com",
                "9900000010",
                "hospital",
                None,
                None,
                None,
                "Hyderabad, Telangana",
                17.4460,
                78.3615,
                "Corporate",
                "HOSP010",
            ),
            # ── 10 Blood Banks ──────────────────────────────────────────────────────
            (
                "Vijayawada Central Blood Bank",
                "bb1@bloodflow.com",
                "9800000001",
                "blood_bank",
                None,
                None,
                None,
                "Vijayawada, Andhra Pradesh",
                16.5062,
                80.6480,
                "Licensed",
                "BB001",
            ),
            (
                "LifeLine Blood Bank Vijayawada",
                "bb2@bloodflow.com",
                "9800000002",
                "blood_bank",
                None,
                None,
                None,
                "Vijayawada, Andhra Pradesh",
                16.5140,
                80.6420,
                "Licensed",
                "BB002",
            ),
            (
                "Guntur District Blood Bank",
                "bb3@bloodflow.com",
                "9800000003",
                "blood_bank",
                None,
                None,
                None,
                "Guntur, Andhra Pradesh",
                16.3067,
                80.4365,
                "Government",
                "BB003",
            ),
            (
                "Rotary Blood Bank Guntur",
                "bb4@bloodflow.com",
                "9800000004",
                "blood_bank",
                None,
                None,
                None,
                "Guntur, Andhra Pradesh",
                16.3100,
                80.4300,
                "Charity",
                "BB004",
            ),
            (
                "Red Cross Blood Bank Hyderabad",
                "bb5@bloodflow.com",
                "9800000005",
                "blood_bank",
                None,
                None,
                None,
                "Hyderabad, Telangana",
                17.3850,
                78.4867,
                "NGO",
                "BB005",
            ),
            (
                "Apollo Blood Bank Hyderabad",
                "bb6@bloodflow.com",
                "9800000006",
                "blood_bank",
                None,
                None,
                None,
                "Hyderabad, Telangana",
                17.4239,
                78.4738,
                "Corporate",
                "BB006",
            ),
            (
                "Anakapalli Blood Centre",
                "bb7@bloodflow.com",
                "9800000007",
                "blood_bank",
                None,
                None,
                None,
                "Anakapalli, Andhra Pradesh",
                17.6974,
                83.0069,
                "Licensed",
                "BB007",
            ),
            (
                "Sanjeevani Blood Bank Anakapalli",
                "bb8@bloodflow.com",
                "9800000008",
                "blood_bank",
                None,
                None,
                None,
                "Anakapalli, Andhra Pradesh",
                17.7010,
                83.0120,
                "Licensed",
                "BB008",
            ),
            (
                "Sukruth Blood Bank Vijayawada",
                "bb9@bloodflow.com",
                "9800000009",
                "blood_bank",
                None,
                None,
                None,
                "Vijayawada, Andhra Pradesh",
                16.4980,
                80.6560,
                "Licensed",
                "BB009",
            ),
            (
                "Jeevan Blood Bank Hyderabad",
                "bb10@bloodflow.com",
                "9800000010",
                "blood_bank",
                None,
                None,
                None,
                "Hyderabad, Telangana",
                17.4460,
                78.3615,
                "Licensed",
                "BB010",
            ),
        ]
        for (
            name,
            email,
            phone,
            role,
            bg,
            gender,
            age,
            addr,
            lat,
            lng,
            htype,
            regno,
        ) in seed_users:
            c.execute(
                """INSERT INTO users (full_name,email,phone,password_hash,role,blood_group,gender,age,address,latitude,longitude,hospital_type,org_reg_no,is_available,is_verified)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1,1)""",
                (name,
                 email,
                 phone,
                 generate_password_hash("password123"),
                 role,
                 bg,
                 gender,
                 age,
                 addr,
                 lat,
                 lng,
                 htype,
                 regno,
                 ),
            )
        donor1 = c.execute(
            "SELECT id FROM users WHERE email='donor1@bloodflow.com'"
        ).fetchone()[0]
        donor2 = c.execute(
            "SELECT id FROM users WHERE email='donor2@bloodflow.com'"
        ).fetchone()[0]
        donor3 = c.execute(
            "SELECT id FROM users WHERE email='donor3@bloodflow.com'"
        ).fetchone()[0]
        vol1 = c.execute(
            "SELECT id FROM users WHERE email='volunteer1@bloodflow.com'"
        ).fetchone()[0]
        vol2 = c.execute(
            "SELECT id FROM users WHERE email='volunteer2@bloodflow.com'"
        ).fetchone()[0]
        hosp1 = c.execute(
            "SELECT id FROM users WHERE email='hospital1@bloodflow.com'"
        ).fetchone()[0]
        hosp2 = c.execute(
            "SELECT id FROM users WHERE email='hospital3@bloodflow.com'"
        ).fetchone()[0]
        bb1 = c.execute(
            "SELECT id FROM users WHERE email='bb1@bloodflow.com'"
        ).fetchone()[0]
        for bg, units, dt in [
            ("B-", 18, "2026-01-10"),
            ("O+", 22, "2026-01-15"),
            ("AB-", 8, "2026-02-01"),
            ("A+", 30, "2026-01-20"),
        ]:
            c.execute("UPDATE inventory SET units=? WHERE blood_group=?", (units, bg))
            uid = str(uuid.uuid4().hex)[:12].upper()
            exp = (datetime.strptime(dt, "%Y-%m-%d") + timedelta(days=42)).strftime(
                "%Y-%m-%d"
            )
            c.execute(
                "INSERT INTO blood_units (unit_uid,blood_group,source,collection_date,expiry_date,status) VALUES (?,?,?,?,?,?)",
                (uid, bg, "donation", dt, exp, "Available"),
            )
        c.execute(
            """INSERT INTO donations (donor_id,hospital_id,volunteer_id,blood_group,units,status,requested_by,request_note,last_donated_date,donation_date,handover_date,completed_at,chk_age_weight,chk_tattoos,chk_symptoms,chk_surgery)
            VALUES (?,?,?,?,?,'completed','donor','Emergency support',?,?,?,?,1,1,1,1)""",
            (
                donor1,
                hosp1,
                vol1,
                "B-",
                1,
                str(date.today() - timedelta(days=130)),
                str(date.today() - timedelta(days=120)),
                str(date.today() - timedelta(days=119)),
                str(date.today() - timedelta(days=119)),
            ),
        )
        c.execute(
            """INSERT INTO blood_requests (hospital_id,blood_group,units,priority,patient_name,location,note,status,blood_bank_id)
            VALUES (?,'B-',3,'High','Rahul Verma','ICU Wing','Urgent requirement','Approved',?)""", (hosp1, bb1), )
        req1 = c.lastrowid
        c.execute(
            """INSERT INTO blood_requests (hospital_id,blood_group,units,priority,patient_name,location,note,status)
            VALUES (?,'O+',2,'Medium','Suma Reddy','Ward 4','Routine surgery','Pending')""", (hosp1,), )
        c.lastrowid
        c.execute(
            """INSERT INTO blood_requests (hospital_id,blood_group,units,priority,patient_name,location,note,status)
            VALUES (?,'AB-',1,'Critical','Arjun Singh','Emergency','Rare blood needed','Pending')""", (hosp2,), )
        c.execute(
            "INSERT INTO request_audit (request_id,actor_id,action,old_status,new_status,note) VALUES (?,?,?,?,?,?)",
            (req1,
             1,
             "Admin approved request",
             "Pending",
             "Approved",
             "Sufficient inventory available",
             ),
        )
        c.execute(
            "INSERT INTO volunteer_tasks (request_id,volunteer_id,task_type,status,pickup_location,delivery_location) VALUES (?,?,?,?,?,?)",
            (req1,
             vol1,
             "delivery",
             "Assigned",
             "Vijayawada Central Blood Bank",
             "ICU Wing, City Care Hospital",
             ),
        )
        c.execute(
            "INSERT INTO gps_logs (volunteer_id,latitude,longitude,label) VALUES (?,?,?,?)",
            (vol1, 16.5062, 80.6480, "En Route"),
        )
    db.commit()
    db.close()


init_db()


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return query_db("SELECT * FROM users WHERE id=?", (uid,), one=True)


def login_required(role=None):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = current_user()
            if not user:
                flash("Please sign in first.", "warning")
                return redirect(url_for("login"))
            if role and user["role"] != role:
                flash("Unauthorized access.", "danger")
                return redirect(url_for("dashboard"))
            return fn(*args, **kwargs)

        return wrapper

    return decorator


@app.context_processor
def inject_globals():
    user = current_user()
    nc = 0
    unread = []
    if user:
        unread = query_db(
            "SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 5",
            (user["id"],),
        )
        notif_count_row = query_db(
            "SELECT COUNT(*) c FROM notifications WHERE user_id=? AND is_read=0",
            (user["id"],),
            one=True,
        )
        nc = notif_count_row["c"] if notif_count_row else 0
    return dict(
        current_user=user,
        notification_count=nc,
        top_notifications=unread,
        now=datetime.now(),
    )


# ── Routes ────────────────────────────────────────────────────────────────────


@app.route("/")
def index():
    recent_requests = query_db(
        """SELECT br.*,u.full_name AS hospital_name FROM blood_requests br
        JOIN users u ON br.hospital_id=u.id ORDER BY br.created_at DESC LIMIT 6"""
    )
    blood_banks_count = query_db(
        'SELECT COUNT(*) c FROM users WHERE role="blood_bank" AND is_verified=1',
        one=True,
    )["c"]
    stats = {
        "donors": query_db('SELECT COUNT(*) c FROM users WHERE role="donor"', one=True)[
            "c"
        ],
        "volunteers": query_db(
            'SELECT COUNT(*) c FROM users WHERE role="volunteer"', one=True
        )["c"],
        "hospitals": query_db(
            'SELECT COUNT(*) c FROM users WHERE role="hospital"', one=True
        )["c"],
        "completed": query_db(
            'SELECT COUNT(*) c FROM donations WHERE status="completed"', one=True
        )["c"],
        "blood_banks": blood_banks_count,
    }
    return render_template("index.html", recent_requests=recent_requests, stats=stats)


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["full_name"].strip()
        email = request.form["email"].strip().lower()
        phone = request.form.get("phone", "").strip()
        password = request.form["password"]
        role = request.form["role"]
        bg = request.form.get("blood_group") or None
        gender = request.form.get("gender") or None
        age = request.form.get("age") or None
        address = request.form.get("address", "").strip()
        htype = request.form.get("hospital_type") or None
        regno = request.form.get("org_reg_no") or None
        if role not in ["donor", "volunteer", "hospital", "blood_bank"]:
            flash("Invalid role.", "danger")
            return redirect(url_for("register"))
        if role == "blood_bank" and not regno:
            flash("Blood Banks must provide a registration number.", "danger")
            return redirect(url_for("register"))
        if query_db("SELECT id FROM users WHERE email=?", (email,), one=True):
            flash("Email already registered.", "danger")
            return redirect(url_for("register"))
        if role == "donor" and (not bg or not age):
            flash("Donors must provide blood group and age.", "danger")
            return redirect(url_for("register"))
        uid = execute_db(
            """INSERT INTO users (full_name,email,phone,password_hash,role,blood_group,gender,age,address,hospital_type,org_reg_no,is_available,is_verified)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,1,0)""",
            (name,
             email,
             phone,
             generate_password_hash(password),
             role,
             bg,
             gender,
             int(age) if age else None,
                address,
                htype,
                regno,
             ),
        )
        otp = f"{random.randint(100000, 999999)}"
        expires = (datetime.now() + timedelta(minutes=10)).isoformat()
        execute_db(
            "INSERT INTO otps (user_id,otp_code,purpose,expires_at) VALUES (?,?,?,?)",
            (uid, otp, "registration", expires),
        )
        session["otp_user_id"] = uid
        session["otp_code"] = otp  # store for flash display
        flash(f"Registration successful! Your OTP is: {otp}", "otp")
        return redirect(url_for("verify_otp"))
    return render_template("register.html", blood_groups=BLOOD_GROUPS, genders=GENDERS)


@app.route("/verify-otp", methods=["GET", "POST"])
def verify_otp():
    uid = session.get("otp_user_id")
    if not uid:
        flash("No pending OTP verification.", "warning")
        return redirect(url_for("register"))
    user = query_db("SELECT * FROM users WHERE id=?", (uid,), one=True)
    if request.method == "POST":
        code = request.form["otp"].strip()
        row = query_db(
            """SELECT * FROM otps WHERE user_id=? AND otp_code=? AND purpose='registration' AND is_used=0
            ORDER BY id DESC LIMIT 1""", (uid, code), one=True, )
        if row and datetime.fromisoformat(row["expires_at"]) >= datetime.now():
            execute_db("UPDATE otps SET is_used=1 WHERE id=?", (row["id"],))
            execute_db("UPDATE users SET is_verified=1 WHERE id=?", (uid,))
            session.pop("otp_user_id", None)
            session.pop("otp_code", None)
            create_notification(
                uid,
                "Welcome to BloodFlow!",
                "Your account has been verified. Start saving lives today.",
                "success",
            )
            flash("OTP verified! You can now sign in.", "success")
            return redirect(url_for("login"))
        flash("Invalid or expired OTP.", "danger")
    return render_template("verify_otp.html", user=user)


@app.route("/resend-otp", methods=["POST"])
def resend_otp():
    uid = session.get("otp_user_id")
    if not uid:
        flash("No pending verification session.", "warning")
        return redirect(url_for("register"))
    # Invalidate old OTPs
    execute_db(
        "UPDATE otps SET is_used=1 WHERE user_id=? AND purpose='registration' AND is_used=0",
        (uid,),
    )
    otp = f"{random.randint(100000, 999999)}"
    expires = (datetime.now() + timedelta(minutes=10)).isoformat()
    execute_db(
        "INSERT INTO otps (user_id,otp_code,purpose,expires_at) VALUES (?,?,?,?)",
        (uid, otp, "registration", expires),
    )
    session["otp_code"] = otp
    flash(f"New OTP generated: {otp}", "otp")
    return redirect(url_for("verify_otp"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        user = query_db("SELECT * FROM users WHERE email=?", (email,), one=True)
        if user and check_password_hash(user["password_hash"], password):
            if not user["is_verified"]:
                session["otp_user_id"] = user["id"]
                flash("Please verify your OTP first.", "warning")
                return redirect(url_for("verify_otp"))
            session["user_id"] = user["id"]
            flash(f'Welcome back, {user["full_name"]}!', "success")
            return redirect(url_for("dashboard"))
        flash("Invalid email or password.", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out successfully.", "info")
    return redirect(url_for("index"))


@app.route("/dashboard")
@login_required()
def dashboard():
    user = current_user()
    role = user["role"]
    if role == "admin":
        data = {
            "users": query_db("SELECT COUNT(*) c FROM users", one=True)["c"],
            "donors": query_db(
                'SELECT COUNT(*) c FROM users WHERE role="donor"', one=True
            )["c"],
            "volunteers": query_db(
                'SELECT COUNT(*) c FROM users WHERE role="volunteer"', one=True
            )["c"],
            "hospitals": query_db(
                'SELECT COUNT(*) c FROM users WHERE role="hospital"', one=True
            )["c"],
            "blood_banks": query_db(
                'SELECT COUNT(*) c FROM users WHERE role="blood_bank"', one=True
            )["c"],
            "pending_requests": query_db(
                'SELECT COUNT(*) c FROM blood_requests WHERE status="Pending"', one=True
            )["c"],
            "fulfilled": query_db(
                'SELECT COUNT(*) c FROM blood_requests WHERE status="Fulfilled"',
                one=True,
            )["c"],
            "total_donations": query_db(
                'SELECT COUNT(*) c FROM donations WHERE status="completed"', one=True
            )["c"],
        }
        pending_reqs = query_db(
            """SELECT br.*,u.full_name AS hospital_name FROM blood_requests br
            JOIN users u ON br.hospital_id=u.id WHERE br.status="Pending" ORDER BY
            CASE br.priority WHEN 'Critical' THEN 0 WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 ELSE 3 END, br.created_at"""
        )
        approved_reqs = query_db(
            """SELECT br.*,u.full_name AS hospital_name,v.full_name AS volunteer_name
            FROM blood_requests br JOIN users u ON br.hospital_id=u.id
            LEFT JOIN users v ON br.volunteer_id=v.id WHERE br.status IN ('Approved','Allocated')
            ORDER BY br.created_at DESC"""
        )
        volunteers = query_db(
            'SELECT * FROM users WHERE role="volunteer" ORDER BY is_available DESC'
        )
        recent_users = query_db("SELECT * FROM users ORDER BY created_at DESC LIMIT 10")
        blood_units = query_db(
            "SELECT * FROM blood_units ORDER BY expiry_date ASC LIMIT 20"
        )
        blood_banks = query_db(
            'SELECT * FROM users WHERE role="blood_bank" ORDER BY full_name'
        )
        # Per-bank inventory for detail view
        bb_inventories = {}
        for bb in blood_banks:
            inv = query_db(
                "SELECT * FROM bb_inventory WHERE blood_bank_id=? ORDER BY blood_group",
                (bb["id"],),
            )
            bb_inventories[bb["id"]] = [dict(i) for i in inv]
        return render_template(
            "dashboard_admin.html",
            data=data,
            pending_reqs=pending_reqs,
            approved_reqs=approved_reqs,
            volunteers=volunteers,
            recent_users=recent_users,
            blood_units=blood_units,
            blood_groups=BLOOD_GROUPS,
            priorities=PRIORITIES,
            blood_banks=blood_banks,
            bb_inventories=bb_inventories,
        )
    if role == "donor":
        history = query_db(
            """SELECT d.*,h.full_name AS hospital_name,v.full_name AS volunteer_name
            FROM donations d LEFT JOIN users h ON d.hospital_id=h.id LEFT JOIN users v ON d.volunteer_id=v.id
            WHERE d.donor_id=? ORDER BY d.created_at DESC""",
            (user["id"],),
        )
        last_donated = query_db(
            "SELECT donation_date FROM donations WHERE donor_id=? AND donation_date IS NOT NULL ORDER BY donation_date DESC LIMIT 1",
            (user["id"],),
            one=True,
        )
        next_eligible = None
        if last_donated and last_donated["donation_date"]:
            ld = datetime.strptime(last_donated["donation_date"], "%Y-%m-%d").date()
            next_eligible = ld + timedelta(days=90)
        open_requests = query_db(
            """SELECT br.*,u.full_name AS hospital_name FROM blood_requests br
            JOIN users u ON br.hospital_id=u.id WHERE br.blood_group IN ({}) AND br.status IN ('Pending','Approved')
            ORDER BY CASE br.priority WHEN 'Critical' THEN 0 WHEN 'High' THEN 1 ELSE 2 END, br.created_at DESC
            LIMIT 10""".format(
                ",".join(
                    "?"
                    * len(
                        COMPATIBLE.get(user["blood_group"], [user["blood_group"]])
                        + ["NA"]
                    )
                )
            ),
            COMPATIBLE.get(user["blood_group"], [user["blood_group"]]) + ["NA"],
        )
        my_requests = query_db(
            """SELECT br.*,u.full_name AS hospital_name FROM blood_requests br
            JOIN users u ON br.hospital_id=u.id WHERE br.donor_id=? ORDER BY br.updated_at DESC""",
            (user["id"],),
        )
        hospitals = query_db('SELECT id,full_name FROM users WHERE role="hospital"')
        blood_banks = query_db(
            'SELECT id,full_name,address FROM users WHERE role="blood_bank" AND is_verified=1 ORDER BY full_name'
        )
        is_eligible = not next_eligible or date.today() >= next_eligible
        return render_template(
            "dashboard_donor.html",
            history=history,
            last_donated=last_donated,
            next_eligible=next_eligible,
            open_requests=open_requests,
            my_requests=my_requests,
            hospitals=hospitals,
            blood_banks=blood_banks,
            is_eligible=is_eligible,
            blood_groups=BLOOD_GROUPS,
        )
    if role == "volunteer":
        open_reqs = query_db(
            """SELECT br.*,h.full_name AS hospital_name FROM blood_requests br
            JOIN users h ON br.hospital_id=h.id WHERE br.status='Approved' AND br.volunteer_id IS NULL
            ORDER BY CASE br.priority WHEN 'Critical' THEN 0 WHEN 'High' THEN 1 ELSE 2 END, br.created_at"""
        )
        my_tasks = query_db(
            """SELECT br.*,h.full_name AS hospital_name,vt.status AS task_status,
            vt.id AS task_id, vt.pickup_location, vt.delivery_location, vt.collected_at, vt.delivered_at
            FROM volunteer_tasks vt JOIN blood_requests br ON vt.request_id=br.id
            JOIN users h ON br.hospital_id=h.id
            WHERE vt.volunteer_id=? ORDER BY vt.assigned_at DESC""",
            (user["id"],),
        )
        donors_raw = query_db(
            'SELECT id,full_name,blood_group,phone,address,is_available,latitude,longitude FROM users WHERE role="donor" ORDER BY is_available DESC,blood_group'
        )
        donors = [dict(d) for d in donors_raw]
        stats = {
            "completed": query_db(
                'SELECT COUNT(*) c FROM volunteer_tasks WHERE volunteer_id=? AND status="Delivered"',
                (user["id"],),
                one=True,
            )["c"],
            "active": query_db(
                'SELECT COUNT(*) c FROM volunteer_tasks WHERE volunteer_id=? AND status IN ("Assigned","Collected")',
                (user["id"],),
                one=True,
            )["c"],
            "total": query_db(
                "SELECT COUNT(*) c FROM volunteer_tasks WHERE volunteer_id=?",
                (user["id"],),
                one=True,
            )["c"],
        }
        # Blood bank donation requests pending volunteer action
        bb_donation_reqs = query_db(
            """SELECT bdr.*, u.full_name AS donor_name, u.blood_group AS donor_blood_group,
            u.phone AS donor_phone, bb.full_name AS blood_bank_name, bb.address AS bb_address
            FROM bb_donation_requests bdr
            JOIN users u ON bdr.donor_id=u.id
            JOIN users bb ON bdr.blood_bank_id=bb.id
            WHERE bdr.status="Pending"
            ORDER BY bdr.created_at DESC""")
        # Hospital-approved donor donations needing volunteer pickup assistance
        donor_assist_reqs = query_db(
            """SELECT d.*, u.full_name AS donor_name, u.phone AS donor_phone,
            u.address AS donor_address, u.blood_group AS donor_bg,
            h.full_name AS hospital_name, h.address AS hospital_address, h.phone AS hospital_phone
            FROM donations d
            JOIN users u ON d.donor_id=u.id
            LEFT JOIN users h ON d.hospital_id=h.id
            WHERE d.status="approved" AND d.volunteer_id IS NULL
            ORDER BY d.created_at DESC"""
        )
        # In-progress donor assists assigned to THIS volunteer (volunteer
        # accepted, awaiting donation)
        in_progress_donor_assists = query_db(
            """SELECT d.*, u.full_name AS donor_name, u.phone AS donor_phone,
            u.address AS donor_address, u.blood_group AS donor_bg,
            h.full_name AS hospital_name, h.address AS hospital_address
            FROM donations d
            JOIN users u ON d.donor_id=u.id
            LEFT JOIN users h ON d.hospital_id=h.id
            WHERE d.status="in_progress" AND d.volunteer_id=?
            ORDER BY d.created_at DESC""",
            (user["id"],),
        )
        return render_template(
            "dashboard_volunteer.html",
            open_reqs=open_reqs,
            my_tasks=my_tasks,
            donors=donors,
            stats=stats,
            bb_donation_reqs=bb_donation_reqs,
            donor_assist_reqs=donor_assist_reqs,
            in_progress_donor_assists=in_progress_donor_assists,
        )
    if role == "hospital":
        my_requests = query_db(
            """SELECT br.*,v.full_name AS volunteer_name,d.full_name AS donor_name,
            v.phone AS volunteer_phone, v.latitude AS vol_lat, v.longitude AS vol_lng
            FROM blood_requests br LEFT JOIN users v ON br.volunteer_id=v.id LEFT JOIN users d ON br.donor_id=d.id
            WHERE br.hospital_id=? ORDER BY CASE br.status WHEN 'Pending' THEN 0 WHEN 'Approved' THEN 1
            WHEN 'Allocated' THEN 2 WHEN 'Fulfilled' THEN 3 ELSE 4 END, br.created_at DESC""",
            (user["id"],),
        )
        donors = query_db(
            'SELECT id,full_name,blood_group,age,gender,is_available FROM users WHERE role="donor" AND is_verified=1 ORDER BY blood_group,full_name'
        )
        matched_donors = {}
        for bg in BLOOD_GROUPS:
            md = match_donors(bg, user["latitude"], user["longitude"], limit=3)
            if md:
                matched_donors[bg] = md
        # Blood banks — show ONLY nearby ones (within 50km or same city)
        all_bbs = query_db(
            'SELECT * FROM users WHERE role="blood_bank" AND is_verified=1 ORDER BY full_name'
        )
        nearby_blood_banks = []
        for bb in all_bbs:
            if not is_nearby(
                user["latitude"],
                user["longitude"],
                bb["latitude"],
                bb["longitude"],
                user["address"],
                bb["address"],
                radius_km=MAX_DONOR_RANGE_KM,
            ):
                continue  # Skip blood banks outside the radius
            dist = None
            if (
                user["latitude"]
                and user["longitude"]
                and bb["latitude"]
                and bb["longitude"]
            ):
                dist = round(
                    haversine(
                        user["latitude"],
                        user["longitude"],
                        bb["latitude"],
                        bb["longitude"],
                    ),
                    1,
                )
            bb_inv = query_db(
                "SELECT * FROM bb_inventory WHERE blood_bank_id=? ORDER BY blood_group",
                (bb["id"],),
            )
            nearby_blood_banks.append(
                {
                    "bank": dict(bb),
                    "distance_km": dist,
                    "inventory": [dict(i) for i in bb_inv],
                }
            )
        nearby_blood_banks.sort(
            key=lambda x: x["distance_km"] if x["distance_km"] is not None else 9999
        )
        # Pending donor donation requests sent to this hospital (or without a
        # hospital yet)
        pending_donor_donations = query_db(
            """SELECT d.*, u.full_name AS donor_name, u.phone AS donor_phone,
            u.address AS donor_address, u.blood_group AS donor_bg
            FROM donations d JOIN users u ON d.donor_id=u.id
            WHERE (d.hospital_id=? OR d.hospital_id IS NULL) AND d.status="requested" AND d.requested_by="donor"
            ORDER BY d.created_at DESC LIMIT 20""",
            (user["id"],),
        )
        return render_template(
            "dashboard_hospital.html",
            my_requests=my_requests,
            donors=donors,
            matched_donors=matched_donors,
            blood_groups=BLOOD_GROUPS,
            priorities=PRIORITIES,
            nearby_blood_banks=nearby_blood_banks,
            pending_donor_donations=pending_donor_donations,
        )
    if role == "blood_bank":
        today_str = str(date.today())
        warning_date = str(date.today() + timedelta(days=7))
        # Ensure bb_inventory rows exist for this blood bank
        for bg in BLOOD_GROUPS:
            execute_db(
                "INSERT OR IGNORE INTO bb_inventory (blood_bank_id,blood_group,units,threshold_units) VALUES (?,?,0,10)",
                (user["id"], bg),
            )
        bb_inv = query_db(
            "SELECT * FROM bb_inventory WHERE blood_bank_id=? ORDER BY blood_group",
            (user["id"],),
        )
        bb_units = query_db(
            """SELECT bu.*, u.full_name AS donor_name
            FROM blood_units bu LEFT JOIN users u ON bu.donor_id=u.id
            WHERE bu.blood_bank_id=? ORDER BY bu.expiry_date ASC""",
            (user["id"],),
        )
        # Recalculate bb_inventory from actual units
        for bg in BLOOD_GROUPS:
            cnt = query_db(
                """SELECT COUNT(*) c FROM blood_units
                WHERE blood_bank_id=? AND blood_group=? AND status="Available"
                AND expiry_date >= ?""",
                (user["id"], bg, today_str),
                one=True,
            )["c"]
            execute_db(
                "UPDATE bb_inventory SET units=?,updated_at=CURRENT_TIMESTAMP WHERE blood_bank_id=? AND blood_group=?",
                (cnt, user["id"], bg),
            )
        bb_inv = query_db(
            "SELECT * FROM bb_inventory WHERE blood_bank_id=? ORDER BY blood_group",
            (user["id"],),
        )
        expired_units = [u for u in bb_units if u["expiry_date"] < today_str]
        expiring_soon = [
            u
            for u in bb_units
            if today_str <= u["expiry_date"] <= warning_date
            and u["status"] == "Available"
        ]
        available_units = [
            u
            for u in bb_units
            if u["status"] == "Available" and u["expiry_date"] >= today_str
        ]
        # Anonymized donor list — no name/contact
        donors_anon = query_db("""SELECT blood_group,
            CASE WHEN INSTR(address,',')>0
                 THEN TRIM(SUBSTR(address,INSTR(address,',')+1))
                 ELSE address END AS city_area,
            is_available,
            CASE WHEN blood_group IN ("AB-","O-","B-","A-") THEN 1 ELSE 0 END AS is_rare
            FROM users WHERE role="donor" AND is_verified=1
            ORDER BY is_available DESC, blood_group""")
        # Pending hospital requests — only show requests from NEARBY hospitals
        all_pending_raw = (
            query_db(
                """SELECT br.*, u.full_name AS hospital_name,
            u.latitude AS hosp_lat, u.longitude AS hosp_lng, u.address AS hosp_address
            FROM blood_requests br JOIN users u ON br.hospital_id=u.id
            WHERE br.status="Pending"
            ORDER BY CASE br.priority WHEN "Critical" THEN 0 WHEN "High" THEN 1 WHEN "Medium" THEN 2 ELSE 3 END, br.created_at"""
            )
            or []
        )
        pending_requests = [
            r
            for r in all_pending_raw
            if is_nearby(
                user["latitude"],
                user["longitude"],
                r["hosp_lat"],
                r["hosp_lng"],
                user["address"],
                r["hosp_address"],
            )
        ]
        # Active/in-progress requests — only THIS blood bank’s approved requests
        active_requests = query_db(
            """SELECT br.*, h.full_name AS hospital_name
            FROM blood_requests br JOIN users h ON br.hospital_id=h.id
            WHERE br.status IN ("Approved","Allocated") AND br.blood_bank_id=?
            ORDER BY CASE br.priority WHEN "Critical" THEN 0 WHEN "High" THEN 1 ELSE 2 END, br.created_at
            LIMIT 10""",
            (user["id"],),
        )
        my_incidents = query_db(
            "SELECT * FROM incident_reports WHERE reporter_id=? ORDER BY created_at DESC",
            (user["id"],),
        )
        bb_stats = {
            "total": sum(i["units"] for i in bb_inv),
            "available": len(available_units),
            "expiring": len(expiring_soon),
            "expired": len(expired_units),
            "incidents_open": query_db(
                'SELECT COUNT(*) c FROM incident_reports WHERE reporter_id=? AND status="Open"',
                (user["id"],),
                one=True,
            )["c"],
            "pending_requests": len(pending_requests),
        }
        return render_template(
            "dashboard_bloodbank.html",
            bb_inv=bb_inv,
            bb_units=bb_units,
            bb_stats=bb_stats,
            expired_units=expired_units,
            expiring_soon=expiring_soon,
            available_units=available_units,
            donors_anon=donors_anon,
            active_requests=active_requests,
            pending_requests=pending_requests,
            my_incidents=my_incidents,
            blood_groups=BLOOD_GROUPS,
            today=today_str,
            warning_date=warning_date,
            bb_donation_reqs=query_db(
                """SELECT bdr.*, u.full_name AS donor_name, u.phone AS donor_phone,
                u.address AS donor_address
                FROM bb_donation_requests bdr JOIN users u ON bdr.donor_id=u.id
                WHERE bdr.blood_bank_id=? AND bdr.status="Pending"
                ORDER BY bdr.created_at DESC""",
                (user["id"],),
            ),
        )
    return redirect(url_for("index"))


@app.route("/donation-request", methods=["POST"])
@login_required("donor")
def donation_request():
    user = current_user()
    if not (
        request.form.get("chk_age_weight")
        and request.form.get("chk_tattoos")
        and request.form.get("chk_symptoms")
        and request.form.get("chk_surgery")
    ):
        flash("All medical compliance checks are required.", "danger")
        return redirect(url_for("dashboard"))
    hospital_id = request.form.get("hospital_id") or None
    units = max(1, int(request.form.get("units", 1)))
    note = request.form.get("request_note", "").strip()
    last_donated = request.form.get("last_donated_date") or None
    execute_db(
        """INSERT INTO donations (donor_id,hospital_id,blood_group,units,status,requested_by,request_note,last_donated_date,chk_age_weight,chk_tattoos,chk_symptoms,chk_surgery)
        VALUES (?,?,?,?,'requested','donor',?,?,1,1,1,1)""",
        (user["id"],
         hospital_id,
         user["blood_group"],
         units,
         note,
         last_donated),
    )
    # ── Notify ONLY the hospital (specific, or nearby ones if no hospital chosen) ──
    # Blood banks and volunteers are NOT notified here.
    # Volunteers get notified AFTER the hospital accepts the donation.
    if hospital_id:
        # Donor chose a specific hospital — notify only that hospital
        create_notification(
            int(hospital_id), "🩸 Donor Ready to Donate", f'{
                user["full_name"]} ({
                user["blood_group"]}) wants to donate {units} unit(s) to your hospital. ' f'Note: {
                note or "None"}. Please review on your dashboard.', "info", user["blood_group"], )
        flash(
            "Donation request submitted! The selected hospital has been notified.",
            "success",
        )
    else:
        # No specific hospital — notify nearby hospitals only
        all_hospitals = (
            query_db('SELECT * FROM users WHERE role="hospital" AND is_verified=1')
            or []
        )
        notified = 0
        for h in all_hospitals:
            if is_nearby(
                user["latitude"],
                user["longitude"],
                h["latitude"],
                h["longitude"],
                user["address"],
                h["address"],
            ):
                create_notification(
                    h["id"],
                    "🩸 Donor Ready to Donate",
                    f'{user["full_name"]} ({user["blood_group"]}, {user["address"] or "nearby"}) can donate {units} unit(s). '
                    f"Accept on your dashboard to assign a volunteer.",
                    "info",
                    user["blood_group"],
                )
                notified += 1
        if notified == 0:  # fallback — notify all hospitals
            for h in all_hospitals:
                create_notification(
                    h["id"],
                    "🩸 Donor Ready to Donate",
                    f'{user["full_name"]} ({user["blood_group"]}) can donate {units} unit(s).',
                    "info",
                    user["blood_group"],
                )
        flash(
            f"Donation request submitted! {
                notified or len(all_hospitals)} hospital(s) notified.",
            "success",
        )
    return redirect(url_for("dashboard"))


@app.route("/blood-request", methods=["POST"])
@login_required("hospital")
def blood_request():
    user = current_user()
    bg = request.form["blood_group"]
    units = int(request.form["units"])
    priority = request.form["priority"]
    patient = request.form["patient_name"]
    location = request.form["location"]
    note = request.form.get("note", "")
    rid = execute_db(
        """INSERT INTO blood_requests (hospital_id,blood_group,units,priority,patient_name,location,note,status)
        VALUES (?,?,?,?,?,?,?,'Pending')""",
        (user["id"], bg, units, priority, patient, location, note),
    )
    log_audit(
        rid,
        user["id"],
        "Request created",
        None,
        "Pending",
        f"Hospital submitted request for {units} units of {bg}",
    )
    # ── Notify NEARBY blood banks only ──────────────────────────────────────────
    all_blood_banks = (
        query_db('SELECT * FROM users WHERE role="blood_bank" AND is_verified=1') or []
    )
    notified_bb = 0
    for bb in all_blood_banks:
        if is_nearby(
            user["latitude"],
            user["longitude"],
            bb["latitude"],
            bb["longitude"],
            user["address"],
            bb["address"],
        ):
            create_notification(
                bb["id"], "🏥 New Hospital Blood Request", f'{
                    user["full_name"]} ({
                    user["address"] or "nearby"}) needs {units} unit(s) of {bg}. Priority: {priority}. Patient: {patient}.', "urgent", bg, )
            notified_bb += 1
    # ── Notify NEARBY volunteers ─────────────────────────────────────────────────
    all_vols = (
        query_db(
            'SELECT * FROM users WHERE role="volunteer" AND is_available=1 AND is_verified=1'
        )
        or []
    )
    notified_vols = 0
    for v in all_vols:
        if is_nearby(
            user["latitude"],
            user["longitude"],
            v["latitude"],
            v["longitude"],
            user["address"],
            v["address"],
        ):
            create_notification(
                v["id"], "🚑 Hospital Needs Blood Delivery", f'{
                    user["full_name"]} ({
                    user["address"] or "nearby"}) needs {units} unit(s) of {bg}. Priority: {priority}. Stand by for assignment.', "info", bg, )
            notified_vols += 1
    # ── Notify NEARBY hospitals (situational awareness) ──────────────────────────
    all_hospitals = (
        query_db(
            'SELECT * FROM users WHERE role="hospital" AND is_verified=1 AND id!=?',
            (user["id"],),
        )
        or []
    )
    for h in all_hospitals:
        if is_nearby(
            user["latitude"],
            user["longitude"],
            h["latitude"],
            h["longitude"],
            user["address"],
            h["address"],
        ):
            create_notification(
                h["id"],
                "ℹ️ Nearby Blood Request",
                f'{user["full_name"]} (nearby hospital) submitted a {priority} priority request for {units} unit(s) of {bg}.',
                "info",
                bg,
            )
    # ── Notify NEARBY compatible donors ─────────────────────────────────────────
    compatible_groups = COMPATIBLE.get(bg, [bg])
    eligible_donors = (
        query_db(
            'SELECT * FROM users WHERE role="donor" AND blood_group IN ({}) AND is_available=1 AND is_verified=1'.format(
                ",".join(
                    "?" *
                    len(compatible_groups))),
            compatible_groups,
        ) or [])
    notified_count = 0
    first_nearby_donor = None
    for d in eligible_donors:
        if is_nearby(
            user["latitude"],
            user["longitude"],
            d["latitude"],
            d["longitude"],
            user["address"],
            d["address"],
        ):
            if first_nearby_donor is None:
                first_nearby_donor = d["id"]
                execute_db(
                    'UPDATE blood_requests SET donor_id=?,donor_status="Pending" WHERE id=?',
                    (d["id"], rid),
                )
            create_notification(
                d["id"],
                "🆘 Blood Request Near You!",
                f'{user["full_name"]} urgently needs {units} unit(s) of {bg}. Priority: {priority}. '
                f"Location: {location}. Please respond from your dashboard.",
                "urgent",
                bg,
            )
            notified_count += 1
    flash(
        f"Blood request submitted! {notified_bb} nearby blood bank(s) and {notified_vols} nearby volunteer(s) notified. {notified_count} matching donor(s) alerted.",
        "success",
    )
    return redirect(url_for("dashboard"))


@app.route("/admin/approve-request/<int:req_id>", methods=["POST"])
@login_required("admin")
def admin_approve_request(req_id):
    # Approval is now handled by blood banks — admin no longer approves
    # hospital requests
    flash(
        "Hospital request approvals are now handled by registered blood banks.", "info"
    )
    return redirect(url_for("dashboard"))


@app.route("/bb/approve-request/<int:req_id>", methods=["POST"])
@login_required("blood_bank")
def bb_approve_request(req_id):
    user = current_user()
    action = request.form.get("action")
    note = request.form.get("note", "")
    req = query_db("SELECT * FROM blood_requests WHERE id=?", (req_id,), one=True)
    if not req:
        flash("Request not found.", "danger")
        return redirect(url_for("dashboard"))
    hosp = query_db("SELECT * FROM users WHERE id=?", (req["hospital_id"],), one=True)
    old_status = req["status"]
    if action == "approve":
        # Record which blood bank approved this request, and update status
        execute_db(
            'UPDATE blood_requests SET status="Approved",blood_bank_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',
            (user["id"], req_id),
        )
        log_audit(
            req_id,
            user["id"],
            f'Blood bank {user["full_name"]} approved',
            old_status,
            "Approved",
            note or "Approved by blood bank",
        )
        create_notification(
            req["hospital_id"],
            "Request Approved!",
            f'Your request for {
                req["units"]} unit(s) of {
                req["blood_group"]} has been approved by {
                user["full_name"]}.',
            "success",
            req["blood_group"],
        )
        # Notify ONLY NEARBY volunteers (relative to hospital) — strictly no fallback
        all_vols = (
            query_db(
                'SELECT * FROM users WHERE role="volunteer" AND is_available=1 AND is_verified=1'
            )
            or []
        )
        notified_vols = 0
        hlat = hosp["latitude"] if hosp else None
        hlng = hosp["longitude"] if hosp else None
        haddr = hosp["address"] if hosp else None
        for v in all_vols:
            if is_nearby(
                hlat, hlng, v["latitude"], v["longitude"], haddr, v["address"]
            ):
                create_notification(
                    v["id"],
                    "🚚 Blood Delivery Needed Near You",
                    f'Approved by {
                        user["full_name"]}: {
                        req["units"]} unit(s) of {
                        req["blood_group"]} to {
                        req["location"] or "Hospital"} ({
                        haddr or "nearby"}). Priority: {
                            req["priority"]}.',
                    "info",
                    req["blood_group"],
                )
                notified_vols += 1
        flash(
            f"Request #{req_id} approved. {notified_vols} nearby volunteer(s) notified.",
            "success",
        )
    elif action == "reject":
        execute_db(
            'UPDATE blood_requests SET status="Closed",updated_at=CURRENT_TIMESTAMP WHERE id=?',
            (req_id,),
        )
        log_audit(
            req_id,
            user["id"],
            f'Blood bank {user["full_name"]} rejected',
            old_status,
            "Closed",
            note or "Rejected by blood bank",
        )
        create_notification(
            req["hospital_id"],
            "Request Rejected",
            f'Your request for {
                req["blood_group"]} was rejected by {
                user["full_name"]}. Reason: {note}',
            "danger",
            req["blood_group"],
        )
        flash(f"Request #{req_id} rejected.", "info")
    return redirect(url_for("dashboard"))


@app.route("/admin/assign-volunteer/<int:req_id>", methods=["POST"])
@login_required("admin")
def admin_assign_volunteer(req_id):
    user = current_user()
    vol_id = request.form.get("volunteer_id")
    req = query_db("SELECT * FROM blood_requests WHERE id=?", (req_id,), one=True)
    if not req or not vol_id:
        flash("Invalid assignment data.", "danger")
        return redirect(url_for("dashboard"))
    execute_db(
        'UPDATE blood_requests SET volunteer_id=?,status="Allocated",updated_at=CURRENT_TIMESTAMP WHERE id=?',
        (vol_id, req_id),
    )
    log_audit(
        req_id,
        user["id"],
        "Volunteer assigned",
        req["status"],
        "Allocated",
        f"Volunteer ID {vol_id} assigned",
    )
    execute_db(
        """INSERT INTO volunteer_tasks (request_id,volunteer_id,task_type,status,pickup_location,delivery_location)
        VALUES (?,?,'delivery','Assigned','Blood Bank',?)""",
        (req_id, vol_id, req["location"]),
    )
    vol = query_db("SELECT * FROM users WHERE id=?", (vol_id,), one=True)
    create_notification(
        int(vol_id),
        "New Task Assigned",
        f'You have been assigned to deliver {
            req["units"]} unit(s) of {
            req["blood_group"]} to hospital.',
        "info",
        req["blood_group"],
    )
    create_notification(
        req["hospital_id"],
        "Volunteer Assigned",
        f'{vol["full_name"]} will deliver your blood request shortly.',
        "success",
        req["blood_group"],
    )
    flash(f"Volunteer assigned to request #{req_id}.", "success")
    return redirect(url_for("dashboard"))


@app.route("/volunteer/accept-request/<int:req_id>", methods=["POST"])
@login_required("volunteer")
def volunteer_accept(req_id):
    user = current_user()
    req = query_db(
        'SELECT * FROM blood_requests WHERE id=? AND status="Approved" AND volunteer_id IS NULL',
        (req_id,),
        one=True,
    )
    if not req:
        flash("Request not available.", "danger")
        return redirect(url_for("dashboard"))
    execute_db(
        'UPDATE blood_requests SET volunteer_id=?,status="Allocated",updated_at=CURRENT_TIMESTAMP WHERE id=?',
        (user["id"], req_id),
    )
    log_audit(req_id, user["id"], "Volunteer self-assigned", req["status"], "Allocated")
    execute_db(
        """INSERT INTO volunteer_tasks (request_id,volunteer_id,task_type,status,pickup_location,delivery_location)
        VALUES (?,?,'delivery','Assigned','Blood Bank',?)""",
        (req_id, user["id"], req["location"]),
    )
    create_notification(
        req["hospital_id"],
        "Volunteer Accepted",
        f'{user["full_name"]} accepted your blood request and is preparing delivery.',
        "success",
        req["blood_group"],
    )
    flash("Task accepted successfully!", "success")
    return redirect(url_for("dashboard"))


@app.route("/volunteer/reject-request/<int:req_id>", methods=["POST"])
@login_required("volunteer")
def volunteer_reject(req_id):
    """Volunteer declines a blood delivery request — returns it to the pool."""
    user = current_user()
    req = query_db(
        'SELECT * FROM blood_requests WHERE id=? AND status="Approved" AND volunteer_id IS NULL',
        (req_id,),
        one=True,
    )
    if not req:
        flash("Request not available to reject.", "danger")
        return redirect(url_for("dashboard"))
    log_audit(
        req_id,
        user["id"],
        "Volunteer rejected request",
        req["status"],
        req["status"],
        f'{user["full_name"]} declined this delivery request',
    )
    create_notification(
        req["hospital_id"],
        "Volunteer Declined",
        f'Volunteer {
            user["full_name"]} declined your request #{req_id}. It remains open for other volunteers.',
        "warning",
        req["blood_group"],
    )
    flash(
        "You declined the request. It remains available for other volunteers.", "info"
    )
    return redirect(url_for("dashboard"))


@app.route("/volunteer/update-task/<int:task_id>", methods=["POST"])
@login_required("volunteer")
def update_task(task_id):
    user = current_user()
    task = query_db(
        "SELECT * FROM volunteer_tasks WHERE id=? AND volunteer_id=?",
        (task_id, user["id"]),
        one=True,
    )
    if not task:
        flash("Task not found.", "danger")
        return redirect(url_for("dashboard"))
    action = request.form.get("action")
    req = query_db(
        "SELECT * FROM blood_requests WHERE id=?", (task["request_id"],), one=True
    )
    if action == "collected":
        execute_db(
            'UPDATE volunteer_tasks SET status="Collected",collected_at=CURRENT_TIMESTAMP WHERE id=?',
            (task_id,),
        )
        execute_db(
            'UPDATE blood_requests SET status="Allocated",updated_at=CURRENT_TIMESTAMP WHERE id=?',
            (task["request_id"],),
        )
        log_audit(
            task["request_id"],
            user["id"],
            "Blood collected",
            "Allocated",
            "Allocated",
            "Volunteer collected blood units",
        )
        create_notification(
            req["hospital_id"],
            "Blood Units Collected",
            f'Volunteer {
                user["full_name"]} has collected the blood units and is on the way.',
            "info",
            req["blood_group"],
        )
        flash("Marked as collected!", "success")
    elif action == "delivered":
        execute_db(
            'UPDATE volunteer_tasks SET status="Delivered",delivered_at=CURRENT_TIMESTAMP WHERE id=?',
            (task_id,),
        )
        execute_db(
            'UPDATE blood_requests SET status="Fulfilled",updated_at=CURRENT_TIMESTAMP WHERE id=?',
            (task["request_id"],),
        )
        log_audit(
            task["request_id"],
            user["id"],
            "Blood delivered",
            "Allocated",
            "Fulfilled",
            "Volunteer delivered to hospital",
        )
        inv = query_db(
            "SELECT * FROM inventory WHERE blood_group=?",
            (req["blood_group"],),
            one=True,
        )
        if inv:
            new_units = max(0, inv["units"] - req["units"])
            execute_db(
                "UPDATE inventory SET units=?,updated_at=CURRENT_TIMESTAMP WHERE blood_group=?",
                (new_units, req["blood_group"]),
            )
            notify_low_inventory()
        if req["donor_id"]:
            execute_db(
                """INSERT INTO donations (donor_id,hospital_id,volunteer_id,blood_group,units,status,requested_by,donation_date,handover_date,completed_at)
                VALUES (?,?,?,?,?,'completed','hospital',?,?,?)""",
                (
                    req["donor_id"],
                    req["hospital_id"],
                    user["id"],
                    req["blood_group"],
                    req["units"],
                    str(date.today()),
                    str(date.today()),
                    str(date.today()),
                ),
            )
            create_notification(
                req["donor_id"],
                "Donation Complete!",
                "Your blood donation was delivered. Thank you for saving a life!",
                "success",
                req["blood_group"],
            )
        create_notification(
            req["hospital_id"], "Blood Delivered!", f'{
                req["units"]} unit(s) of {
                req["blood_group"]} delivered by {
                user["full_name"]}. Please confirm receipt.', "success", req["blood_group"], )
        flash("Marked as delivered! Great work!", "success")
    return redirect(url_for("dashboard"))


@app.route("/hospital/close-request/<int:req_id>", methods=["POST"])
@login_required("hospital")
def close_request(req_id):
    user = current_user()
    feedback = request.form.get("feedback", "").strip()
    rating = request.form.get("rating")
    req = query_db(
        "SELECT * FROM blood_requests WHERE id=? AND hospital_id=?",
        (req_id, user["id"]),
        one=True,
    )
    if not req:
        flash("Request not found.", "danger")
        return redirect(url_for("dashboard"))
    execute_db(
        'UPDATE blood_requests SET status="Closed",feedback=?,feedback_rating=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',
        (feedback, rating, req_id),
    )
    log_audit(
        req_id, user["id"], "Hospital closed request", req["status"], "Closed", feedback
    )
    if req["volunteer_id"]:
        create_notification(
            req["volunteer_id"],
            "Request Closed",
            f'Hospital confirmed receipt. Feedback: {feedback or "No feedback"}',
            "info",
        )
    flash("Request closed. Thank you for your feedback!", "success")
    return redirect(url_for("dashboard"))


@app.route("/donor-response/<int:req_id>/<response>", methods=["POST"])
@login_required("donor")
def donor_response(req_id, response):
    user = current_user()
    req = query_db(
        "SELECT * FROM blood_requests WHERE id=? AND donor_id=?",
        (req_id, user["id"]),
        one=True,
    )
    if not req:
        flash("Request not found.", "danger")
        return redirect(url_for("dashboard"))
    if response == "accept":
        execute_db(
            'UPDATE blood_requests SET donor_status="Accepted",updated_at=CURRENT_TIMESTAMP WHERE id=?',
            (req_id,),
        )
        if req["volunteer_id"]:
            create_notification(
                req["volunteer_id"],
                "Donor Accepted",
                f'{user["full_name"]} accepted. Proceed to collection.',
                "success",
                req["blood_group"],
            )
        create_notification(
            req["hospital_id"],
            "Donor Confirmed",
            f'A donor has confirmed donation for {req["blood_group"]}.',
            "success",
            req["blood_group"],
        )
        flash(
            "You accepted the donation request. The volunteer will coordinate with you.",
            "success",
        )
    elif response == "reject":
        execute_db(
            'UPDATE blood_requests SET donor_id=NULL,donor_status="Rejected",updated_at=CURRENT_TIMESTAMP WHERE id=?',
            (req_id,),
        )
        if req["volunteer_id"]:
            create_notification(
                req["volunteer_id"],
                "Donor Declined",
                f'{user["full_name"]} declined. Please find another donor.',
                "warning",
                req["blood_group"],
            )
        flash("You declined the request.", "info")
    return redirect(url_for("dashboard"))


@app.route("/update-gps", methods=["POST"])
@login_required("volunteer")
def update_gps():
    user = current_user()
    lat = request.form.get("latitude")
    lng = request.form.get("longitude")
    label = request.form.get("label", "Available")
    if lat and lng:
        execute_db(
            "UPDATE users SET latitude=?,longitude=? WHERE id=?", (lat, lng, user["id"])
        )
        execute_db(
            "INSERT INTO gps_logs (volunteer_id,latitude,longitude,label) VALUES (?,?,?,?)",
            (user["id"], lat, lng, label),
        )
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Missing coordinates"}), 400


@app.route("/admin/adjust-inventory", methods=["POST"])
@login_required("admin")
def admin_adjust_inventory():
    bg = request.form.get("blood_group")
    units = request.form.get("units")
    if bg not in BLOOD_GROUPS or not units:
        flash("Invalid data.", "danger")
        return redirect(url_for("dashboard"))
    execute_db(
        "UPDATE inventory SET units=?,updated_at=CURRENT_TIMESTAMP WHERE blood_group=?",
        (int(units), bg),
    )
    notify_low_inventory()
    flash(f"{bg} inventory updated to {units} units.", "success")
    return redirect(url_for("dashboard"))


@app.route("/admin/add-blood-unit", methods=["POST"])
@login_required("admin")
def admin_add_blood_unit():
    bg = request.form.get("blood_group")
    vol = request.form.get("volume_ml", 450)
    src = request.form.get("source", "donation")
    notes = request.form.get("notes", "")
    cdate = request.form.get("collection_date", str(date.today()))
    exp = (datetime.strptime(cdate, "%Y-%m-%d") + timedelta(days=42)).strftime(
        "%Y-%m-%d"
    )
    uid_str = str(uuid.uuid4())[:12].upper()
    execute_db(
        'INSERT INTO blood_units (unit_uid,blood_group,volume_ml,source,collection_date,expiry_date,status,notes) VALUES (?,?,?,?,?,?,"Available",?)',
        (uid_str,
         bg,
         vol,
         src,
         cdate,
         exp,
         notes),
    )
    execute_db(
        "UPDATE inventory SET units=units+1,updated_at=CURRENT_TIMESTAMP WHERE blood_group=?",
        (bg,),
    )
    flash(f"Blood unit {uid_str} ({bg}) added. Expires {exp}.", "success")
    return redirect(url_for("dashboard"))


@app.route("/admin/broadcast", methods=["POST"])
@login_required("admin")
def admin_broadcast():
    title = request.form.get("title", "").strip()
    message = request.form.get("message", "").strip()
    target_role = request.form.get("target_role", "all")
    ntype = request.form.get("ntype", "info")
    if not title or not message:
        flash("Title and message required.", "danger")
        return redirect(url_for("dashboard"))
    recipients = (
        query_db("SELECT id FROM users")
        if target_role == "all"
        else query_db("SELECT id FROM users WHERE role=?", (target_role,))
    )
    for r in recipients or []:
        create_notification(r["id"], title, message, ntype)
    flash(f"Broadcast sent to {len(recipients)} users.", "success")
    return redirect(url_for("dashboard"))


@app.route("/admin/users")
@login_required("admin")
def admin_users():
    users_list = query_db("SELECT * FROM users ORDER BY role,created_at DESC")
    return render_template(
        "admin_users.html",
        users_list=users_list,
        blood_groups=BLOOD_GROUPS,
        roles=ROLES,
        genders=GENDERS,
    )


@app.route("/admin/edit-user/<int:uid>", methods=["POST"])
@login_required("admin")
def admin_edit_user(uid):
    fn = request.form.get("full_name", "").strip()
    role = request.form.get("role", "")
    bg = request.form.get("blood_group") or None
    gender = request.form.get("gender") or None
    age = request.form.get("age") or None
    address = request.form.get("address", "").strip()
    is_verified = 1 if request.form.get("is_verified") else 0
    is_available = 1 if request.form.get("is_available") else 0
    execute_db(
        "UPDATE users SET full_name=?,role=?,blood_group=?,gender=?,age=?,address=?,is_verified=?,is_available=? WHERE id=?",
        (fn,
         role,
         bg,
         gender,
         int(age) if age else None,
         address,
         is_verified,
         is_available,
         uid,
         ),
    )
    flash(f"User #{uid} updated.", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/delete-user/<int:uid>", methods=["POST"])
@login_required("admin")
def admin_delete_user(uid):
    u = query_db("SELECT * FROM users WHERE id=?", (uid,), one=True)
    if not u or u["role"] == "admin":
        flash("Cannot delete this user.", "danger")
        return redirect(url_for("admin_users"))
    execute_db("DELETE FROM users WHERE id=?", (uid,))
    flash(f'User "{u["full_name"]}" deleted.', "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/toggle-verify/<int:uid>", methods=["POST"])
@login_required("admin")
def admin_toggle_verify(uid):
    u = query_db("SELECT * FROM users WHERE id=?", (uid,), one=True)
    if u:
        execute_db(
            "UPDATE users SET is_verified=? WHERE id=?",
            (0 if u["is_verified"] else 1, uid),
        )
        flash("Verification status toggled.", "success")
    return redirect(url_for("admin_users"))


@app.route("/edit-profile", methods=["GET", "POST"])
@login_required()
def edit_profile():
    user = current_user()
    if request.method == "POST":
        fn = request.form.get("full_name", "").strip()
        phone = request.form.get("phone", "").strip()
        address = request.form.get("address", "").strip()
        age = request.form.get("age") or None
        gender = request.form.get("gender") or None
        bg = request.form.get("blood_group") or None
        new_pw = request.form.get("new_password", "").strip()
        if not fn:
            flash("Name required.", "danger")
            return redirect(url_for("edit_profile"))
        execute_db(
            "UPDATE users SET full_name=?,phone=?,address=?,age=?,gender=?,blood_group=? WHERE id=?",
            (fn,
             phone,
             address,
             int(age) if age else None,
                gender,
                bg if user["role"] in (
                 "donor",
                 "volunteer") else user["blood_group"],
                user["id"],
             ),
        )
        if new_pw:
            execute_db(
                "UPDATE users SET password_hash=? WHERE id=?",
                (generate_password_hash(new_pw), user["id"]),
            )
        flash("Profile updated!", "success")
        return redirect(url_for("dashboard"))
    return render_template(
        "edit_profile.html", blood_groups=BLOOD_GROUPS, genders=GENDERS
    )


@app.route("/notifications")
@login_required()
def notifications():
    user = current_user()
    notes = query_db(
        "SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC",
        (user["id"],),
    )
    execute_db("UPDATE notifications SET is_read=1 WHERE user_id=?", (user["id"],))
    return render_template("notifications.html", notes=notes)


@app.route("/inventory")
@login_required()
def inventory_page():
    # Redirect to dashboard — global inventory removed; blood bank inventories
    # are in dashboards
    return redirect(url_for("dashboard"))


@app.route("/toggle-availability", methods=["POST"])
@login_required()
def toggle_availability():
    user = current_user()
    if user["role"] == "volunteer":
        new_val = 0 if user["is_available"] else 1
        execute_db("UPDATE users SET is_available=? WHERE id=?", (new_val, user["id"]))
        flash(
            f'Availability set to {"Available" if new_val else "Unavailable"}.',
            "success",
        )
    return redirect(url_for("dashboard"))


@app.route("/api/live-locations")
@login_required("hospital")
def live_locations():
    user = current_user()
    vols = query_db(
        """SELECT DISTINCT v.id,v.full_name,v.phone,v.latitude,v.longitude,br.blood_group,br.status,br.id as req_id
        FROM blood_requests br JOIN users v ON br.volunteer_id=v.id
        WHERE br.hospital_id=? AND br.status IN ('Allocated','Fulfilled') AND v.latitude IS NOT NULL""",
        (user["id"],),
    )
    return jsonify(
        [
            {
                "id": v["id"],
                "name": v["full_name"],
                "phone": v["phone"],
                "lat": v["latitude"],
                "lng": v["longitude"],
                "task": f"{v['status']}: {v['blood_group']}",
            }
            for v in vols
        ]
    )


@app.route("/api/hospitals-locations")
@login_required("volunteer")
def hospitals_locations():
    """API endpoint to return hospital locations for volunteer map."""
    hospitals = query_db(
        'SELECT id, full_name, address, latitude, longitude FROM users WHERE role="hospital" AND is_verified=1 AND latitude IS NOT NULL'
    )
    return jsonify(
        [
            {
                "id": h["id"],
                "name": h["full_name"],
                "address": h["address"] or "",
                "lat": h["latitude"],
                "lng": h["longitude"],
            }
            for h in (hospitals or [])
        ]
    )


@app.route("/api/bloodbanks-locations")
@login_required("volunteer")
def bloodbanks_locations():
    """API endpoint to return blood bank locations for volunteer map."""
    bbs = query_db(
        'SELECT id, full_name, address, latitude, longitude FROM users WHERE role="blood_bank" AND is_verified=1 AND latitude IS NOT NULL'
    )
    return jsonify(
        [
            {
                "id": b["id"],
                "name": b["full_name"],
                "address": b["address"] or "",
                "lat": b["latitude"],
                "lng": b["longitude"],
            }
            for b in (bbs or [])
        ]
    )


@app.route("/volunteer/reject-bb-donation/<int:req_id>", methods=["POST"])
@login_required("volunteer")
def volunteer_reject_bb_donation(req_id):
    """Allow a volunteer to reject a blood bank donation request."""
    req = query_db(
        'SELECT * FROM bb_donation_requests WHERE id=? AND status="Pending"',
        (req_id,),
        one=True,
    )
    if not req:
        flash("Request not found or already processed.", "danger")
        return redirect(url_for("dashboard"))
    execute_db(
        'UPDATE bb_donation_requests SET status="Rejected" WHERE id=?', (req_id,)
    )
    create_notification(
        req["donor_id"],
        "Donation Request Declined",
        "A volunteer was unable to coordinate your blood donation. Please try submitting again or contact the blood bank directly.",
        "warning",
        req["blood_group"],
    )
    flash("You rejected the blood bank donation request.", "info")
    return redirect(url_for("dashboard"))


@app.route("/admin/user/<int:uid>")
@login_required("admin")
def admin_user_profile(uid):
    """Detailed profile page for a specific user."""
    u = query_db("SELECT * FROM users WHERE id=?", (uid,), one=True)
    if not u:
        flash("User not found.", "danger")
        return redirect(url_for("admin_users"))
    donations = (
        query_db(
            "SELECT * FROM donations WHERE donor_id=? ORDER BY created_at DESC LIMIT 10",
            (uid,),
        )
        if u["role"] == "donor"
        else []
    )
    tasks = (
        query_db(
            "SELECT * FROM volunteer_tasks WHERE volunteer_id=? ORDER BY assigned_at DESC LIMIT 10",
            (uid,),
        )
        if u["role"] == "volunteer"
        else []
    )
    requests = (
        query_db(
            "SELECT * FROM blood_requests WHERE hospital_id=? ORDER BY created_at DESC LIMIT 10",
            (uid,),
        )
        if u["role"] == "hospital"
        else []
    )
    return render_template(
        "admin_user_profile.html",
        u=u,
        donations=donations,
        tasks=tasks,
        requests=requests,
    )


@app.route("/admin/users-by-role/<role>")
@login_required("admin")
def admin_users_by_role(role):
    """List all users in a specific category/role."""
    valid_roles = ["donor", "volunteer", "hospital", "blood_bank", "admin"]
    if role not in valid_roles:
        flash("Invalid role.", "danger")
        return redirect(url_for("dashboard"))
    users_list = query_db(
        "SELECT * FROM users WHERE role=? ORDER BY created_at DESC", (role,)
    )
    return render_template("admin_users_by_role.html", users_list=users_list, role=role)


@app.route("/api/matching-donors")
@login_required()
def api_matching_donors():
    bg = request.args.get("blood_group", "O+")
    lat = request.args.get("lat", type=float)
    lng = request.args.get("lng", type=float)
    results = match_donors(bg, lat, lng)
    return jsonify(
        [
            {
                "id": r["donor"]["id"],
                "name": r["donor"]["full_name"],
                "blood_group": r["donor"]["blood_group"],
                "score": r["score"],
                "address": r["donor"]["address"],
            }
            for r in results
        ]
    )


@app.route("/api/inventory-chart")
def inventory_chart():
    # Aggregate bb_inventory across all registered blood banks
    inv = query_db("""SELECT blood_group, COALESCE(SUM(units),0) AS units,
        COALESCE(SUM(threshold_units),0) AS threshold_units
        FROM bb_inventory GROUP BY blood_group ORDER BY blood_group""")
    # Fill missing blood groups with zeros
    inv_map = {i["blood_group"]: i for i in inv}
    labels = BLOOD_GROUPS
    units = [inv_map.get(bg, {}).get("units", 0) for bg in labels]
    thresholds = [inv_map.get(bg, {}).get("threshold_units", 0) for bg in labels]
    return jsonify({"labels": labels, "units": units, "thresholds": thresholds})


@app.route("/mark-donation/<int:donation_id>/<status>")
@login_required("hospital")
def mark_donation(donation_id, status):
    if status not in ["approved", "rejected"]:
        flash("Invalid status.", "danger")
        return redirect(url_for("dashboard"))
    execute_db("UPDATE donations SET status=? WHERE id=?", (status, donation_id))
    d = query_db("SELECT * FROM donations WHERE id=?", (donation_id,), one=True)
    create_notification(
        d["donor_id"],
        f"Donation {status}",
        f"Your donation request was {status}.",
        "success" if status == "approved" else "danger",
        d["blood_group"],
    )
    flash("Donation updated.", "success")
    return redirect(url_for("dashboard"))


@app.route("/admin/blood-bank/<int:bb_id>")
@login_required("admin")
def admin_blood_bank_detail(bb_id):
    bb = query_db(
        'SELECT * FROM users WHERE id=? AND role="blood_bank"', (bb_id,), one=True
    )
    if not bb:
        flash("Blood bank not found.", "danger")
        return redirect(url_for("dashboard"))
    today_str = str(date.today())
    warning_date = str(date.today() + timedelta(days=7))
    bb_inv = query_db(
        "SELECT * FROM bb_inventory WHERE blood_bank_id=? ORDER BY blood_group",
        (bb_id,),
    )
    bb_units = query_db(
        """SELECT bu.*, u.full_name AS donor_name
        FROM blood_units bu LEFT JOIN users u ON bu.donor_id=u.id
        WHERE bu.blood_bank_id=? ORDER BY bu.expiry_date ASC""",
        (bb_id,),
    )
    expiring_soon = [
        u
        for u in bb_units
        if today_str <= u["expiry_date"] <= warning_date and u["status"] == "Available"
    ]
    expired = [u for u in bb_units if u["expiry_date"] < today_str]
    return render_template(
        "admin_blood_bank_detail.html",
        bb=bb,
        bb_inv=bb_inv,
        bb_units=bb_units,
        expiring_soon=expiring_soon,
        expired=expired,
        today=today_str,
        warning_date=warning_date,
        blood_groups=BLOOD_GROUPS,
    )


@app.route("/hospital/blood-bank/<int:bb_id>")
@login_required("hospital")
def hospital_blood_bank_detail(bb_id):
    bb = query_db(
        'SELECT * FROM users WHERE id=? AND role="blood_bank"', (bb_id,), one=True
    )
    if not bb:
        flash("Blood bank not found.", "danger")
        return redirect(url_for("dashboard"))
    today_str = str(date.today())
    warning_date = str(date.today() + timedelta(days=7))
    bb_inv = query_db(
        "SELECT * FROM bb_inventory WHERE blood_bank_id=? ORDER BY blood_group",
        (bb_id,),
    )
    bb_units = query_db(
        """SELECT bu.*, u.full_name AS donor_name
        FROM blood_units bu LEFT JOIN users u ON bu.donor_id=u.id
        WHERE bu.blood_bank_id=? AND bu.status="Available" AND bu.expiry_date >= ?
        ORDER BY bu.expiry_date ASC""",
        (bb_id, today_str),
    )
    expiring_soon = [u for u in bb_units if u["expiry_date"] <= warning_date]
    return render_template(
        "hospital_blood_bank_detail.html",
        bb=bb,
        bb_inv=bb_inv,
        bb_units=bb_units,
        expiring_soon=expiring_soon,
        today=today_str,
        warning_date=warning_date,
        blood_groups=BLOOD_GROUPS,
    )


# /blood-bank hub page removed — individual blood bank dashboards are at /dashboard (blood_bank role)
# and admin/hospital can view specific banks at /admin/blood-bank/<id> and
# /hospital/blood-bank/<id>


@app.route("/api/bb-reports")
@login_required()
def api_bb_reports():
    inventory = query_db("SELECT * FROM inventory ORDER BY blood_group")
    status_counts = query_db(
        "SELECT status, COUNT(*) cnt FROM blood_requests GROUP BY status"
    )
    monthly = query_db(
        """SELECT SUBSTR(donation_date,1,7) AS month, COALESCE(SUM(units),0) total_units
        FROM donations WHERE status="completed" AND donation_date IS NOT NULL
        GROUP BY month ORDER BY month ASC LIMIT 6"""
    )
    return jsonify(
        {
            "inventory": {
                "labels": [i["blood_group"] for i in (inventory or [])],
                "units": [i["units"] for i in (inventory or [])],
                "thresholds": [i["threshold_units"] for i in (inventory or [])],
            },
            "statuses": {
                "labels": [r["status"] for r in (status_counts or [])],
                "counts": [r["cnt"] for r in (status_counts or [])],
            },
            "monthly": {
                "labels": [r["month"] for r in (monthly or [])],
                "units": [r["total_units"] for r in (monthly or [])],
            },
        }
    )


# ── Blood Bank Institution Routes ─────────────────────────────────────────────


@app.route("/bb/edit-inventory", methods=["POST"])
@login_required("blood_bank")
def bb_edit_inventory():
    """Allow blood bank to manually set units and threshold for a blood group."""
    user = current_user()
    blood_group = request.form.get("blood_group")
    units = request.form.get("units", "0")
    threshold = request.form.get("threshold_units", "10")
    if blood_group not in BLOOD_GROUPS:
        flash("Invalid blood group.", "danger")
        return redirect(url_for("dashboard"))
    try:
        units_val = max(0, int(units))
        threshold_val = max(1, int(threshold))
    except ValueError:
        flash("Units and threshold must be whole numbers.", "danger")
        return redirect(url_for("dashboard"))
    execute_db(
        "INSERT OR IGNORE INTO bb_inventory (blood_bank_id,blood_group,units,threshold_units) VALUES (?,?,0,10)",
        (user["id"], blood_group),
    )
    execute_db(
        "UPDATE bb_inventory SET units=?,threshold_units=?,updated_at=CURRENT_TIMESTAMP WHERE blood_bank_id=? AND blood_group=?",
        (units_val, threshold_val, user["id"], blood_group),
    )
    flash(
        f"{blood_group} inventory updated: {units_val} units (threshold: {threshold_val}).",
        "success",
    )
    return redirect(url_for("dashboard") + "#tab-inventory")


@app.route("/bb/add-unit", methods=["POST"])
@login_required("blood_bank")
def bb_add_unit():
    user = current_user()
    bg = request.form.get("blood_group")
    vol = int(request.form.get("volume_ml", 450))
    src = request.form.get("source", "donation")
    cdate = request.form.get("collection_date", str(date.today()))
    notes = request.form.get("notes", "").strip()
    if bg not in BLOOD_GROUPS:
        flash("Invalid blood group.", "danger")
        return redirect(url_for("dashboard"))
    exp = (datetime.strptime(cdate, "%Y-%m-%d") + timedelta(days=42)).strftime(
        "%Y-%m-%d"
    )
    uid_str = str(uuid.uuid4())[:12].upper()
    execute_db(
        """INSERT INTO blood_units (unit_uid,blood_group,volume_ml,source,blood_bank_id,collection_date,expiry_date,status,notes)
        VALUES (?,?,?,?,?,?,?,"Available",?)""",
        (uid_str, bg, vol, src, user["id"], cdate, exp, notes),
    )
    # Update bb_inventory count
    execute_db(
        """INSERT OR IGNORE INTO bb_inventory (blood_bank_id,blood_group,units,threshold_units) VALUES (?,?,0,10)""",
        (user["id"], bg),
    )
    execute_db(
        """UPDATE bb_inventory SET units=units+1,updated_at=CURRENT_TIMESTAMP
        WHERE blood_bank_id=? AND blood_group=?""",
        (user["id"], bg),
    )
    # Alert admin
    admin = query_db('SELECT id FROM users WHERE role="admin" LIMIT 1', one=True)
    if admin:
        create_notification(
            admin["id"],
            "New Blood Unit Added",
            f'{user["full_name"]} added unit {uid_str} ({bg}, {vol}ml) to their inventory.',
            "info",
            bg,
        )
    flash(f"Unit {uid_str} ({bg}) added. Expires {exp}.", "success")
    return redirect(url_for("dashboard"))


@app.route("/bb/update-unit-status/<int:unit_id>", methods=["POST"])
@login_required("blood_bank")
def bb_update_unit(unit_id):
    user = current_user()
    unit = query_db(
        "SELECT * FROM blood_units WHERE id=? AND blood_bank_id=?",
        (unit_id, user["id"]),
        one=True,
    )
    if not unit:
        flash("Unit not found.", "danger")
        return redirect(url_for("dashboard"))
    new_status = request.form.get("status")
    reason = request.form.get("discard_reason", "").strip()
    if new_status not in ["Available", "Used", "Discarded", "Expired"]:
        flash("Invalid status.", "danger")
        return redirect(url_for("dashboard"))
    execute_db(
        "UPDATE blood_units SET status=?,discard_reason=? WHERE id=?",
        (new_status, reason or None, unit_id),
    )
    # Decrement inventory if no longer available
    if unit["status"] == "Available" and new_status != "Available":
        execute_db(
            """UPDATE bb_inventory SET units=MAX(0,units-1),updated_at=CURRENT_TIMESTAMP
            WHERE blood_bank_id=? AND blood_group=?""",
            (user["id"], unit["blood_group"]),
        )
    # Alert admin on discard/expired
    if new_status in ("Discarded", "Expired"):
        admin = query_db('SELECT id FROM users WHERE role="admin" LIMIT 1', one=True)
        if admin:
            create_notification(
                admin["id"], f"Unit {new_status}", f'{
                    user["full_name"]}: unit {
                    unit["unit_uid"]} ({
                    unit["blood_group"]}) marked {new_status}. Reason: {
                    reason or "N/A"}', "warning", unit["blood_group"], )
    flash(f'Unit {unit["unit_uid"]} marked as {new_status}.', "success")
    return redirect(url_for("dashboard"))


@app.route("/bb/incident-report", methods=["POST"])
@login_required("blood_bank")
def bb_incident_report():
    user = current_user()
    rtype = request.form.get("report_type", "Incident")
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    severity = request.form.get("severity", "Low")
    if not title or not description:
        flash("Title and description are required.", "danger")
        return redirect(url_for("dashboard"))
    execute_db(
        """INSERT INTO incident_reports (reporter_id,report_type,title,description,severity)
        VALUES (?,?,?,?,?)""",
        (user["id"], rtype, title, description, severity),
    )
    admin = query_db('SELECT id FROM users WHERE role="admin" LIMIT 1', one=True)
    if admin:
        create_notification(
            admin["id"],
            f"Incident Report: {title}",
            f'{user["full_name"]} filed a {severity} severity {rtype}: {description[:100]}',
            "urgent",
        )
    flash("Report submitted successfully. Admin has been notified.", "success")
    return redirect(url_for("dashboard"))


@app.route("/api/bb-inventory")
@login_required("blood_bank")
def api_bb_inventory():
    user = current_user()
    bb_inv = query_db(
        "SELECT * FROM bb_inventory WHERE blood_bank_id=? ORDER BY blood_group",
        (user["id"],),
    )
    status_counts = query_db(
        """SELECT status, COUNT(*) cnt FROM blood_units
        WHERE blood_bank_id=? GROUP BY status""",
        (user["id"],),
    )
    return jsonify(
        {
            "labels": [i["blood_group"] for i in (bb_inv or [])],
            "units": [i["units"] for i in (bb_inv or [])],
            "thresholds": [i["threshold_units"] for i in (bb_inv or [])],
            "statuses": {
                "labels": [r["status"] for r in (status_counts or [])],
                "counts": [r["cnt"] for r in (status_counts or [])],
            },
        }
    )


@app.route("/donor/donate-to-bloodbank", methods=["POST"])
@login_required("donor")
def donor_donate_to_bloodbank():
    user = current_user()
    bb_id = request.form.get("blood_bank_id")
    units = max(1, int(request.form.get("units", 1)))
    note = request.form.get("note", "").strip()
    if not bb_id:
        flash("Please select a blood bank.", "danger")
        return redirect(url_for("dashboard"))
    bb = query_db(
        'SELECT * FROM users WHERE id=? AND role="blood_bank"', (bb_id,), one=True
    )
    if not bb:
        flash("Blood bank not found.", "danger")
        return redirect(url_for("dashboard"))
    if not user["blood_group"]:
        flash("Your blood group is not set. Please update your profile.", "danger")
        return redirect(url_for("dashboard"))
    execute_db(
        """INSERT INTO bb_donation_requests (donor_id,blood_bank_id,blood_group,units,note,status)
        VALUES (?,?,?,?,?,"Pending")""",
        (user["id"], int(bb_id), user["blood_group"], units, note),
    )
    # ── Notify ONLY the blood bank — volunteers are NOT notified at this stage ──
    # The blood bank will coordinate with the donor directly.
    # Volunteers can be notified later if the blood bank or admin decides to
    # involve them.
    create_notification(
        int(bb_id), "🩸 Donor Wants to Donate", f'{
            user["full_name"]} ({
            user["blood_group"]}, {
                user["address"] or "nearby"}) wants to donate {units} unit(s) to your blood bank. ' f'Note: {
                    note or "None"}. Please review and coordinate with the donor directly.', "info", user["blood_group"], )
    flash(
        f'Your donation request to {
            bb["full_name"]} has been submitted. The blood bank has been notified!',
        "success",
    )
    return redirect(url_for("dashboard"))


# ── Hospital accepts donor's donation request → notify nearby volunteer ────────
@app.route("/hospital/accept-donor-donation/<int:donation_id>", methods=["POST"])
@login_required("hospital")
def hospital_accept_donor_donation(donation_id):
    user = current_user()
    donation = query_db(
        "SELECT * FROM donations WHERE id=? AND hospital_id=?",
        (donation_id, user["id"]),
        one=True,
    )
    if not donation:
        # Try without hospital filter (donor chose 'any hospital')
        donation = query_db(
            'SELECT * FROM donations WHERE id=? AND status="requested"',
            (donation_id,),
            one=True,
        )
        if not donation:
            flash("Donation request not found.", "danger")
            return redirect(url_for("dashboard"))
    execute_db(
        'UPDATE donations SET status="approved",hospital_id=? WHERE id=?',
        (user["id"], donation_id),
    )
    donor = query_db(
        "SELECT * FROM users WHERE id=?", (donation["donor_id"],), one=True
    )
    # Notify donor that hospital accepted
    if donor:
        create_notification(
            donor["id"],
            "✅ Hospital Accepted Your Donation!",
            f'{user["full_name"]} has accepted your donation request. A nearby volunteer will be assigned to assist you.',
            "success",
            donation["blood_group"],
        )
    # Notify NEARBY volunteers to assist donor pickup
    all_vols = (
        query_db(
            'SELECT * FROM users WHERE role="volunteer" AND is_available=1 AND is_verified=1'
        )
        or []
    )
    notified = 0
    for v in all_vols:
        if is_nearby(
            user["latitude"],
            user["longitude"],
            v["latitude"],
            v["longitude"],
            user["address"],
            v["address"],
        ):
            create_notification(
                v["id"],
                "🤝 Donor Needs Pickup Assistance",
                f'Hospital {user["full_name"]} accepted a donation from {donor["full_name"] if donor else "a donor"} '
                f'({donation["blood_group"]}). Please assist with donor pickup. '
                f'Donor: {donor["phone"] if donor else "N/A"}, Address: {donor["address"] if donor else "N/A"}.',
                "info",
                donation["blood_group"],
            )
            notified += 1
    if notified == 0:
        for v in all_vols:
            create_notification(
                v["id"],
                "🤝 Donor Needs Pickup Assistance",
                f'Hospital {
                    user["full_name"]} accepted a donation. Donor: {
                    donor["full_name"] if donor else "N/A"} ({
                    donation["blood_group"]}). Please assist.',
                "info",
                donation["blood_group"],
            )
    flash(
        f"Donor donation accepted. {notified} nearby volunteer(s) notified to assist with pickup.",
        "success",
    )
    return redirect(url_for("dashboard"))


# ── Volunteer accepts/rejects a donor-assist task from hospital donation ────────
@app.route("/volunteer/accept-donor-assist/<int:donation_id>", methods=["POST"])
@login_required("volunteer")
def volunteer_accept_donor_assist(donation_id):
    user = current_user()
    donation = query_db(
        'SELECT * FROM donations WHERE id=? AND status="approved"',
        (donation_id,),
        one=True,
    )
    if not donation:
        flash("Donation request not available.", "danger")
        return redirect(url_for("dashboard"))
    # Link volunteer to this donation
    execute_db(
        'UPDATE donations SET volunteer_id=?,status="in_progress" WHERE id=?',
        (user["id"], donation_id),
    )
    donor = query_db(
        "SELECT * FROM users WHERE id=?", (donation["donor_id"],), one=True
    )
    hospital = query_db(
        "SELECT * FROM users WHERE id=?", (donation["hospital_id"],), one=True
    )
    # Notify donor — include volunteer contact details
    if donor:
        create_notification(
            donor["id"],
            "🤝 Volunteer Assigned to Assist You!",
            f'Volunteer {user["full_name"]} will assist with your donation pickup. '
            f'Contact: {user["phone"] or "N/A"} | Location: {user["address"] or "N/A"}.',
            "success",
            donation["blood_group"],
        )
    if hospital:
        create_notification(
            hospital["id"],
            "🚗 Volunteer Accepted Donor Pickup",
            f'Volunteer {
                user["full_name"]} is coordinating pickup from donor {
                donor["full_name"] if donor else "N/A"}.',
            "info",
            donation["blood_group"],
        )
    flash(
        f'You accepted donor assist task. Donor {
            donor["full_name"] if donor else ""} has been notified with your contact details.',
        "success",
    )
    return redirect(url_for("dashboard"))


@app.route("/volunteer/reject-donor-assist/<int:donation_id>", methods=["POST"])
@login_required("volunteer")
def volunteer_reject_donor_assist(donation_id):
    user = current_user()
    donation = query_db(
        'SELECT * FROM donations WHERE id=? AND status="approved"',
        (donation_id,),
        one=True,
    )
    if not donation:
        flash("Donation request not available.", "danger")
        return redirect(url_for("dashboard"))
    hospital = query_db(
        "SELECT * FROM users WHERE id=?", (donation["hospital_id"],), one=True
    )
    donor = query_db(
        "SELECT * FROM users WHERE id=?", (donation["donor_id"],), one=True
    )
    # Find another nearby volunteer to notify (exclude current volunteer)
    all_vols = (
        query_db(
            'SELECT * FROM users WHERE role="volunteer" AND is_available=1 AND is_verified=1 AND id!=?',
            (user["id"],),
        )
        or []
    )
    notified_next = 0
    for v in all_vols:
        hlat = hospital["latitude"] if hospital else None
        hlng = hospital["longitude"] if hospital else None
        haddr = hospital["address"] if hospital else None
        if is_nearby(hlat, hlng, v["latitude"], v["longitude"], haddr, v["address"]):
            create_notification(
                v["id"], "🤝 Donor Needs Pickup Assistance", f'Volunteer {
                    user["full_name"]} declined. Hospital {
                    hospital["full_name"] if hospital else ""} still needs a volunteer to assist donor ' f'{
                    donor["full_name"] if donor else "N/A"} ({
                    donation["blood_group"]}).', "info", donation["blood_group"], )
            notified_next += 1
            if notified_next >= 3:  # ping up to 3 next volunteers
                break
    flash(
        f"You declined the donor assist task. {notified_next} other nearby volunteer(s) have been notified.",
        "info",
    )
    return redirect(url_for("dashboard"))


@app.route("/volunteer/handle-bb-donation/<int:req_id>/<action>", methods=["POST"])
@login_required("volunteer")
def volunteer_handle_bb_donation(req_id, action):
    user = current_user()
    req = query_db("SELECT * FROM bb_donation_requests WHERE id=?", (req_id,), one=True)
    if not req:
        flash("Request not found.", "danger")
        return redirect(url_for("dashboard"))
    if action == "accept":
        execute_db(
            'UPDATE bb_donation_requests SET status="Accepted",volunteer_id=? WHERE id=?',
            (user["id"], req_id),
        )
        create_notification(
            req["donor_id"],
            "Donation Pickup Confirmed",
            f'Volunteer {
                user["full_name"]} will coordinate your blood donation to the blood bank. They will contact you.',
            "success",
            req["blood_group"],
        )
        flash(
            "You accepted the blood bank donation request. Please coordinate with the donor.",
            "success",
        )
    elif action == "complete":
        execute_db(
            'UPDATE bb_donation_requests SET status="Completed" WHERE id=?', (req_id,)
        )
        # Add blood unit to blood bank inventory
        bb_id = req["blood_bank_id"]
        bg = req["blood_group"]
        cdate = str(date.today())
        exp = (date.today() + timedelta(days=42)).strftime("%Y-%m-%d")
        uid_str = str(uuid.uuid4())[:12].upper()
        execute_db(
            """INSERT INTO blood_units (unit_uid,blood_group,source,donor_id,blood_bank_id,collection_date,expiry_date,status)
            VALUES (?,?,"donation",?,?,?,?,"Available")""",
            (uid_str, bg, req["donor_id"], bb_id, cdate, exp),
        )
        execute_db(
            """INSERT OR IGNORE INTO bb_inventory (blood_bank_id,blood_group,units,threshold_units) VALUES (?,?,0,10)""",
            (bb_id, bg),
        )
        execute_db(
            """UPDATE bb_inventory SET units=units+1,updated_at=CURRENT_TIMESTAMP WHERE blood_bank_id=? AND blood_group=?""",
            (bb_id, bg),
        )
        create_notification(
            req["donor_id"],
            "🎉 Donation Complete!",
            f"Your blood donation has been delivered to the blood bank. Thank you for saving lives!",
            "success",
            bg,
        )
        create_notification(
            req["blood_bank_id"],
            "New Blood Unit Added via Donor",
            f'Volunteer {
                user["full_name"]} delivered 1 unit of {bg} from donor. Unit {uid_str} added to inventory.',
            "success",
            bg,
        )
        flash(
            "Donation marked complete. Blood unit added to blood bank inventory.",
            "success",
        )
    return redirect(url_for("dashboard"))


# ── Hospital edits a Pending blood request ───────────────────────────────────
@app.route("/hospital/edit-request/<int:req_id>", methods=["POST"])
@login_required("hospital")
def hospital_edit_request(req_id):
    user = current_user()
    req = query_db(
        'SELECT * FROM blood_requests WHERE id=? AND hospital_id=? AND status="Pending"',
        (req_id, user["id"]),
        one=True,
    )
    if not req:
        flash(
            "Request not found or cannot be edited (only Pending requests can be edited).",
            "danger",
        )
        return redirect(url_for("dashboard"))
    blood_group = request.form.get("blood_group", req["blood_group"])
    units = request.form.get("units", req["units"])
    priority = request.form.get("priority", req["priority"])
    patient_name = request.form.get("patient_name", req["patient_name"])
    note = request.form.get("note", req["note"])
    execute_db(
        """UPDATE blood_requests SET blood_group=?,units=?,priority=?,patient_name=?,note=?,updated_at=CURRENT_TIMESTAMP
        WHERE id=?""", (blood_group, units, priority, patient_name, note, req_id), )
    flash(f"Request #{req_id} updated successfully.", "success")
    return redirect(url_for("dashboard"))


# ── Volunteer marks that donor has donated blood → notifies hospital ──────────
@app.route("/volunteer/donor-donated/<int:donation_id>", methods=["POST"])
@login_required("volunteer")
def volunteer_donor_donated(donation_id):
    user = current_user()
    donation = query_db(
        'SELECT * FROM donations WHERE id=? AND volunteer_id=? AND status="in_progress"',
        (donation_id, user["id"]),
        one=True,
    )
    if not donation:
        flash("Donation record not found or not in progress.", "danger")
        return redirect(url_for("dashboard"))
    # Mark as completed
    execute_db(
        'UPDATE donations SET status="completed",donation_date=? WHERE id=?',
        (str(date.today()), donation_id),
    )
    donor = query_db(
        "SELECT * FROM users WHERE id=?", (donation["donor_id"],), one=True
    )
    hospital = (
        query_db("SELECT * FROM users WHERE id=?", (donation["hospital_id"],), one=True)
        if donation["hospital_id"]
        else None
    )
    # Notify the hospital
    if hospital:
        create_notification(
            hospital["id"],
            "🩸 Donor Donated Blood — Collection Complete",
            f'Volunteer {user["full_name"]} confirms that donor {donor["full_name"] if donor else "N/A"} '
            f'({donation["blood_group"]}) has donated blood. Please confirm receipt at your facility.',
            "success",
            donation["blood_group"],
        )
    # Notify the donor
    if donor:
        create_notification(
            donor["id"],
            "🎉 Blood Donation Recorded!",
            f'Thank you! Volunteer {user["full_name"]} has confirmed your blood donation. '
            f"The hospital has been notified.",
            "success",
            donation["blood_group"],
        )
    # Auto-add blood unit to the nearest blood bank inventory when no specific hospital blood bank is linked
    # (for hospital-directed donations the hospital manages the blood directly)
    bg = donation["blood_group"]
    str(date.today())
    exp = (date.today() + timedelta(days=42)).strftime("%Y-%m-%d")
    uid_str = str(uuid.uuid4())[:12].upper()
    if donation["hospital_id"]:
        # Log the unit against the hospital as a collected unit (for auditing)
        execute_db(
            """INSERT OR IGNORE INTO blood_requests (hospital_id,blood_group,units,priority,status,donor_id,updated_at)
            VALUES (?,?,1,"Normal","Fulfilled",?,CURRENT_TIMESTAMP)""",
            (donation["hospital_id"], bg, donation["donor_id"]),
        )
    flash(
        f'Donation marked complete. Hospital {
            hospital["full_name"] if hospital else ""} has been notified.',
        "success",
    )
    return redirect(url_for("dashboard"))


# ── Hospital rejects a donor's donation offer ────────────────────────────────
@app.route("/hospital/reject-donor-donation/<int:donation_id>", methods=["POST"])
@login_required("hospital")
def hospital_reject_donor_donation(donation_id):
    user = current_user()
    donation = query_db(
        'SELECT * FROM donations WHERE id=? AND status="requested"',
        (donation_id,),
        one=True,
    )
    if not donation:
        flash("Donation request not found or already processed.", "danger")
        return redirect(url_for("dashboard"))
    execute_db('UPDATE donations SET status="rejected" WHERE id=?', (donation_id,))
    donor = query_db(
        "SELECT * FROM users WHERE id=?", (donation["donor_id"],), one=True
    )
    if donor:
        create_notification(
            donor["id"],
            "❌ Donation Request Declined",
            f'Your donation offer to {
                user["full_name"]} was declined. You can offer to another hospital.',
            "info",
            donation["blood_group"],
        )
    flash("Donor donation offer rejected.", "info")
    return redirect(url_for("dashboard"))


# ── Blood bank accepts a donor's direct donation request ─────────────────────
@app.route("/bb/accept-donor-donation/<int:req_id>", methods=["POST"])
@login_required("blood_bank")
def bb_accept_donor_donation(req_id):
    user = current_user()
    req = query_db(
        'SELECT * FROM bb_donation_requests WHERE id=? AND status="Pending"',
        (req_id,),
        one=True,
    )
    if not req:
        flash("Request not found or already processed.", "danger")
        return redirect(url_for("dashboard"))
    execute_db(
        'UPDATE bb_donation_requests SET status="Accepted" WHERE id=?', (req_id,)
    )
    donor = query_db("SELECT * FROM users WHERE id=?", (req["donor_id"],), one=True)
    if donor:
        create_notification(
            donor["id"], "✅ Blood Bank Accepted Your Donation!", f'{
                user["full_name"]} accepted your donation of {
                req["units"]} unit(s) of {
                req["blood_group"]}. ' f'Please visit or coordinate directly: {
                    user["phone"] or "contact blood bank"}, {
                        user["address"] or "see your dashboard"}.', "success", req["blood_group"], )
    flash(
        f'Donor donation request accepted. {
            donor["full_name"] if donor else "Donor"} has been notified with your contact.',
        "success",
    )
    return redirect(url_for("dashboard"))


# ── Blood bank rejects a donor's direct donation request ─────────────────────
@app.route("/bb/reject-donor-donation/<int:req_id>", methods=["POST"])
@login_required("blood_bank")
def bb_reject_donor_donation(req_id):
    user = current_user()
    req = query_db(
        'SELECT * FROM bb_donation_requests WHERE id=? AND status="Pending"',
        (req_id,),
        one=True,
    )
    if not req:
        flash("Request not found or already processed.", "danger")
        return redirect(url_for("dashboard"))
    execute_db(
        'UPDATE bb_donation_requests SET status="Rejected" WHERE id=?', (req_id,)
    )
    donor = query_db("SELECT * FROM users WHERE id=?", (req["donor_id"],), one=True)
    if donor:
        create_notification(
            donor["id"],
            "❌ Blood Bank Declined Your Donation",
            f'{user["full_name"]} could not accept your donation at this time. You may try another blood bank.',
            "info",
            req["blood_group"],
        )
    flash("Donor donation request rejected. Donor has been notified.", "info")
    return redirect(url_for("dashboard"))


if __name__ == "__main__":
    app.run(debug=True)
