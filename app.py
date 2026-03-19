from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file
import os, hashlib, uuid, requests, csv, io, zipfile, traceback
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "weather_app_secret_key_2024_xK9#mP")

# ── DB connection ─────────────────────────────────────────

def get_db():
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        raise RuntimeError("DATABASE_URL environment variable is not set.")
    # Render sometimes gives 'postgres://' but psycopg2 needs 'postgresql://'
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    return psycopg2.connect(db_url, sslmode="require")

# ── DB init (called once on startup) ─────────────────────

def init_db():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id         TEXT PRIMARY KEY,
                    username   TEXT UNIQUE NOT NULL,
                    password   TEXT NOT NULL,
                    role       TEXT NOT NULL DEFAULT 'user',
                    created_at TIMESTAMP DEFAULT NOW(),
                    last_login TIMESTAMP
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS activity_logs (
                    id        TEXT PRIMARY KEY,
                    timestamp TIMESTAMP DEFAULT NOW(),
                    username  TEXT,
                    action    TEXT,
                    ip        TEXT,
                    details   TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS location_logs (
                    id          TEXT PRIMARY KEY,
                    timestamp   TIMESTAMP DEFAULT NOW(),
                    username    TEXT,
                    latitude    TEXT,
                    longitude   TEXT,
                    accuracy    TEXT,
                    gps_enabled TEXT
                )
            """)
            # Seed default admin only if not already present
            cur.execute("SELECT 1 FROM users WHERE username = 'admin'")
            if not cur.fetchone():
                cur.execute(
                    "INSERT INTO users (id, username, password, role) VALUES (%s,%s,%s,'admin')",
                    (str(uuid.uuid4()), "admin", hash_password("admin123"))
                )
        conn.commit()

# ── Helpers ───────────────────────────────────────────────

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def log_activity(username, action, details=""):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO activity_logs (id,username,action,ip,details) VALUES (%s,%s,%s,%s,%s)",
                    (str(uuid.uuid4()), username, action, request.remote_addr, details)
                )
            conn.commit()
    except Exception:
        pass  # never let logging crash the app

def get_user(username):
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE username = %s", (username,))
            return cur.fetchone()

def update_last_login(username):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET last_login = NOW() WHERE username = %s", (username,))
        conn.commit()

# ── Startup ───────────────────────────────────────────────
# Wrapped so a bad DB URL shows a clear error instead of a cryptic 500

try:
    init_db()
    print("✅ Database initialised successfully.")
except Exception as e:
    print(f"❌ Database init failed: {e}")
    traceback.print_exc()

# ── Health check (visit /health to diagnose issues) ───────

@app.route("/health")
def health():
    status = {}
    # 1. Check env var
    db_url = os.environ.get("DATABASE_URL", "")
    status["DATABASE_URL_set"] = bool(db_url)
    status["GOOGLE_API_KEY_set"] = bool(os.environ.get("GOOGLE_MAPS_API_KEY", ""))

    # 2. Try connecting
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM users")
                count = cur.fetchone()[0]
        status["db_connected"] = True
        status["user_count"]   = count
    except Exception as e:
        status["db_connected"] = False
        status["db_error"]     = str(e)

    ok = status.get("db_connected", False)
    return jsonify(status), 200 if ok else 500

# ── Auth ──────────────────────────────────────────────────

@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        try:
            user = get_user(username)
        except Exception as e:
            return render_template("login.html", error=f"Database error: {e}")
        if user and user["password"] == hash_password(password):
            session["username"] = username
            session["role"]     = user["role"]
            update_last_login(username)
            log_activity(username, "LOGIN", f"Role: {user['role']}")
            if user["role"] == "admin":
                return redirect(url_for("admin_dashboard"))
            return redirect(url_for("user_dashboard"))
        return render_template("login.html", error="Invalid credentials")
    return render_template("login.html")

@app.route("/logout")
def logout():
    if "username" in session:
        log_activity(session["username"], "LOGOUT")
    session.clear()
    return redirect(url_for("login"))

# ── User Dashboard ────────────────────────────────────────

@app.route("/dashboard")
def user_dashboard():
    if "username" not in session or session.get("role") != "user":
        return redirect(url_for("login"))
    return render_template("user_dashboard.html", username=session["username"])

# ── Location: browser GPS ─────────────────────────────────

@app.route("/api/save_location", methods=["POST"])
def save_location():
    if "username" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    data = request.json or {}
    lat  = str(data.get("latitude",  ""))
    lon  = str(data.get("longitude", ""))
    acc  = str(data.get("accuracy",  "N/A"))
    gps  = str(data.get("gps_enabled", False))
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO location_logs (id,username,latitude,longitude,accuracy,gps_enabled) VALUES (%s,%s,%s,%s,%s,%s)",
                    (str(uuid.uuid4()), session["username"], lat, lon, acc, gps)
                )
            conn.commit()
        log_activity(session["username"], "LOCATION_SAVED", f"Source:GPS, Acc:{acc}m")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── Location: Google fallback ─────────────────────────────

@app.route("/api/get_location_google", methods=["POST"])
def get_location_google():
    if "username" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    if not api_key:
        return jsonify({"error": "Google API not configured"}), 503
    try:
        resp = requests.post(
            f"https://www.googleapis.com/geolocation/v1/geolocate?key={api_key}",
            json={}, timeout=10
        )
        resp.raise_for_status()
        gdata = resp.json()
        if "location" not in gdata:
            return jsonify({"error": "No location in response"}), 502
        lat = round(gdata["location"]["lat"], 6)
        lon = round(gdata["location"]["lng"], 6)
        acc = round(gdata.get("accuracy", 0))
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO location_logs (id,username,latitude,longitude,accuracy,gps_enabled) VALUES (%s,%s,%s,%s,%s,%s)",
                    (str(uuid.uuid4()), session["username"], str(lat), str(lon), str(acc), "google")
                )
            conn.commit()
        log_activity(session["username"], "LOCATION_SAVED", f"Source:Google, Acc:{acc}m")
        return jsonify({"success": True, "latitude": lat, "longitude": lon, "accuracy": acc})
    except requests.exceptions.Timeout:
        return jsonify({"error": "Google API timed out"}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── GPS denied log ────────────────────────────────────────

@app.route("/api/log_gps_denied", methods=["POST"])
def log_gps_denied():
    if "username" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    try:
        log_activity(session["username"], "GPS_DENIED", "Browser GPS denied / failed")
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO location_logs (id,username,latitude,longitude,accuracy,gps_enabled) VALUES (%s,%s,%s,%s,%s,%s)",
                    (str(uuid.uuid4()), session["username"], "", "", "", "False")
                )
            conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── Admin Dashboard ───────────────────────────────────────

@app.route("/admin")
def admin_dashboard():
    if session.get("role") != "admin":
        return redirect(url_for("login"))
    try:
        with get_db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM users WHERE role != 'admin' ORDER BY created_at DESC")
                users = cur.fetchall()
                cur.execute("SELECT * FROM activity_logs ORDER BY timestamp DESC LIMIT 50")
                logs = cur.fetchall()
                cur.execute("SELECT * FROM location_logs ORDER BY timestamp DESC LIMIT 50")
                locations = cur.fetchall()
                cur.execute("SELECT COUNT(*) as c FROM activity_logs WHERE action='LOGIN'")
                total_logins = cur.fetchone()["c"]
                cur.execute("SELECT COUNT(*) as c FROM location_logs WHERE gps_enabled='True'")
                gps_enabled = cur.fetchone()["c"]
                cur.execute("SELECT COUNT(*) as c FROM location_logs")
                total_locations = cur.fetchone()["c"]
        stats = {
            "total_users"    : len(users),
            "total_logins"   : total_logins,
            "gps_enabled"    : gps_enabled,
            "total_locations": total_locations
        }
        return render_template("admin_dashboard.html",
                               users=users, logs=logs,
                               locations=locations, stats=stats)
    except Exception as e:
        return f"<h2>Admin dashboard error:</h2><pre>{traceback.format_exc()}</pre>", 500

@app.route("/admin/create_user", methods=["POST"])
def create_user():
    if session.get("role") != "admin":
        return redirect(url_for("login"))
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()
    if not username or not password:
        return redirect(url_for("admin_dashboard"))
    if get_user(username):
        return redirect(url_for("admin_dashboard") + "?error=User+already+exists")
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (id,username,password,role) VALUES (%s,%s,%s,'user')",
                    (str(uuid.uuid4()), username, hash_password(password))
                )
            conn.commit()
        log_activity(session["username"], "CREATE_USER", f"Created: {username}")
    except psycopg2.errors.UniqueViolation:
        return redirect(url_for("admin_dashboard") + "?error=User+already+exists")
    except Exception as e:
        return redirect(url_for("admin_dashboard") + f"?error={str(e)}")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/delete_user/<username>")
def delete_user(username):
    if session.get("role") != "admin":
        return redirect(url_for("login"))
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE username=%s AND role!='admin'", (username,))
        conn.commit()
    log_activity(session["username"], "DELETE_USER", f"Deleted: {username}")
    return redirect(url_for("admin_dashboard"))

# ── Exports ───────────────────────────────────────────────

@app.route("/admin/export_locations")
def export_locations():
    if session.get("role") != "admin":
        return redirect(url_for("login"))
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM location_logs ORDER BY timestamp DESC")
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(cols)
    w.writerows(rows)
    out.seek(0)
    log_activity(session["username"], "EXPORT_LOCATIONS", "Exported location CSV")
    return send_file(io.BytesIO(out.getvalue().encode()),
                     as_attachment=True, download_name="location_logs.csv",
                     mimetype="text/csv")

@app.route("/admin/export_logs")
def export_logs():
    if session.get("role") != "admin":
        return redirect(url_for("login"))
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM activity_logs ORDER BY timestamp DESC")
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(cols)
    w.writerows(rows)
    out.seek(0)
    log_activity(session["username"], "EXPORT_LOGS", "Exported activity logs CSV")
    return send_file(io.BytesIO(out.getvalue().encode()),
                     as_attachment=True, download_name="activity_logs.csv",
                     mimetype="text/csv")

@app.route("/admin/backup")
def create_backup():
    if session.get("role") != "admin":
        return redirect(url_for("login"))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        with get_db() as conn:
            for table in ["users", "activity_logs", "location_logs"]:
                with conn.cursor() as cur:
                    cur.execute(f"SELECT * FROM {table}")
                    rows = cur.fetchall()
                    cols = [d[0] for d in cur.description]
                out = io.StringIO()
                csv.writer(out).writerow(cols)
                csv.writer(out).writerows(rows)
                zf.writestr(f"{table}.csv", out.getvalue())
    buf.seek(0)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_activity(session["username"], "BACKUP_CREATED", f"backup_{ts}.zip")
    return send_file(buf, as_attachment=True,
                     download_name=f"backup_{ts}.zip",
                     mimetype="application/zip")
