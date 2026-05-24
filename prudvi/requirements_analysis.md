# BloodFlow — Software & Hardware Requirements

## Project Summary
BloodFlow is a web-based blood bank management system built on Flask + SQLite. It connects donors, hospitals, blood banks, volunteers, and admins via a proximity-aware logistics platform with real-time email notifications.

---

## 1. Software Requirements

### 1.1 Server-Side (Backend)

| Component | Version / Detail |
|-----------|-----------------|
| **Python** | 3.10 or higher (3.14 used in dev) |
| **Flask** | 3.0.3 — Web framework, routing, templating |
| **Werkzeug** | 3.0.3 — Password hashing, WSGI utilities |
| **SQLite3** | Built-in Python stdlib — primary database |
| **smtplib** | Built-in Python stdlib — Gmail OTP email delivery |
| **math** | Built-in — Haversine distance calculation |
| **os, uuid, random, datetime** | Built-in stdlib modules |

**Install command:**
```bash
pip install Flask==3.0.3 Werkzeug==3.0.3
```

### 1.2 Frontend (Client-Side CDN Libraries)

| Library | Version | Purpose |
|---------|---------|---------|
| **Leaflet.js** | 1.9.4 | Interactive maps, GPS tracking, volunteer markers |
| **Chart.js** | latest (CDN) | Dashboard analytics charts |
| **Google Fonts — Inter** | 300–800 | Primary UI typography |
| **Google Fonts — Plus Jakarta Sans** | 500–800 | Brand / navbar typography |

> [!NOTE]
> All CDN libraries require an internet connection at runtime. No npm/node build step is needed.

### 1.3 Static Assets (Served Locally)

| File | Purpose |
|------|---------|
| [static/css/style.css](file:///c:/prudvi/static/css/style.css) | Complete UI theme (600+ lines, CSS-only animations) |
| [static/js/main.js](file:///c:/prudvi/static/js/main.js) | Animation engine: scroll-reveal, ripple, count-up, tilt, tab system |

### 1.4 Database

| Detail | Value |
|--------|-------|
| **Engine** | SQLite 3 (file-based, no installation required) |
| **File** | [bloodbank.db](file:///c:/prudvi/bloodbank.db) (auto-created on first run) |
| **Tables** | [users](file:///c:/prudvi/app.py#1050-1055), `blood_requests`, `donations`, [inventory](file:///c:/prudvi/app.py#1119-1124), [bb_inventory](file:///c:/prudvi/app.py#1385-1399), `blood_units`, [notifications](file:///c:/prudvi/app.py#1111-1118), `otps`, `request_audit`, `volunteer_tasks`, `gps_logs` |
| **ORM** | None — raw SQL via `sqlite3` module |

### 1.5 External Services

| Service | Purpose | Required? |
|---------|---------|-----------|
| **Gmail SMTP** (`smtp.gmail.com:587`) | OTP verification emails | Optional (app works without it; OTP shown in flash) |
| **Google Fonts CDN** | Font loading | Optional (fallback: system sans-serif) |
| **unpkg CDN** (Leaflet) | Map rendering | Required for map features |
| **jsDelivr CDN** (Chart.js) | Dashboard charts | Required for chart features |

### 1.6 Environment Variables

| Variable | Purpose |
|----------|---------|
| `GMAIL_USER` | Gmail address for sending OTPs |
| `GMAIL_PASS` | Gmail app password (16-char) |

### 1.7 Operating System Compatibility

| OS | Status |
|----|--------|
| Windows 10/11 | ✅ Fully tested |
| Ubuntu / Debian Linux | ✅ Compatible |
| macOS 12+ | ✅ Compatible |

---

## 2. Hardware Requirements

### 2.1 Minimum (Development / Single User)

| Component | Minimum |
|-----------|---------|
| **CPU** | Dual-core 1.5 GHz (x86-64) |
| **RAM** | 512 MB free |
| **Storage** | 200 MB free (app + DB) |
| **Network** | Internet for CDN (Google Fonts, Leaflet, Chart.js) |
| **Display** | 1024 × 768 resolution |

### 2.2 Recommended (Small Deployment ≤ 50 concurrent users)

| Component | Recommended |
|-----------|-------------|
| **CPU** | Quad-core 2.5 GHz (e.g. Intel i5 / Ryzen 5) |
| **RAM** | 2 GB free |
| **Storage** | 2 GB SSD (for DB growth, logs) |
| **Network** | 10 Mbps broadband |
| **Display** | 1280 × 720 or higher |

### 2.3 Production Server (100+ concurrent users)

| Component | Specification |
|-----------|--------------|
| **CPU** | 4–8 vCPUs (2.8 GHz+) |
| **RAM** | 4–8 GB |
| **Storage** | 20 GB SSD (DB + logs) |
| **OS** | Ubuntu 22.04 LTS |
| **WSGI Server** | Gunicorn (`pip install gunicorn`) |
| **Reverse Proxy** | Nginx or Apache |
| **DB Upgrade** | PostgreSQL recommended for >10k records |

> [!IMPORTANT]
> For production, replace SQLite with **PostgreSQL** and use **Gunicorn** instead of Flask's built-in development server.

---

## 3. Browser Requirements (Client)

| Browser | Version | Status |
|---------|---------|--------|
| Google Chrome | 110+ | ✅ Fully supported |
| Microsoft Edge | 110+ | ✅ Fully supported |
| Firefox | 110+ | ✅ Fully supported |
| Safari | 15+ | ✅ Supported |
| Internet Explorer | Any | ❌ Not supported |

> Requires JavaScript enabled. CSS custom properties (variables) and CSS animations must be supported.

---

## 4. Python Package Summary

```
Flask==3.0.3
Werkzeug==3.0.3
# All other dependencies are Python standard library
```

**Optional for production:**
```
gunicorn>=21.0
psycopg2-binary>=2.9   # if migrating to PostgreSQL
python-dotenv>=1.0      # for .env file management
```

---

## 5. Quick Setup

```bash
# 1. Install Python 3.10+
# 2. Install dependencies
pip install Flask==3.0.3 Werkzeug==3.0.3

# 3. Set env vars (optional, for email OTP)
set GMAIL_USER=your@gmail.com
set GMAIL_PASS=your_app_password

# 4. Run
python app.py
# → http://127.0.0.1:5000
```
