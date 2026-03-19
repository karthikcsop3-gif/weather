from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file
import os, hashlib, uuid, requests, csv, io, zipfile
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)
app.secret_key = "weather_app_secret_key_2024_xK9#mP"

# ── Database connection ───────────────────────────────────
# Render automatically sets DATABASE_URL when you attach a PostgreSQL instance.

def get_db():
    return psycopg2.connect(os.environ["DATABASE_URL"], sslmode="require")

def init_db():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id          TEXT PRIMARY KEY,
                    username    TEXT UNIQUE NOT NULL,
                    password    TEXT NOT NULL,
                    role        TEXT NOT NULL DEFAULT 'user',
                    created_at  TIMESTAMP DEFAULT NOW(),
                    last_login  TIMESTAMP
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS activity_logs (
                    id         TEXT PRIMARY KEY,
                    timestamp  TIMESTAMP DEFAULT NOW(),
                    username   TEXT,
                    action     TEXT,
                    ip         TEXT,
                    details    TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS location_logs (
                    id         TEXT PRIMARY KEY,
                    timestamp  TIMESTAMP DEFAULT NOW(),
                    username   TEXT,
                    latitude   TEXT,
                    longitude  TEXT,
                    accuracy   TEXT,
                    gps_enabled TEXT
                )
            """)
            # Create default admin if not exists
            cur.execute("SELECT 1 FROM users WHERE username = 'admin'")
            if not cur.fetchone():
                cur.execute(
                    "INSERT INTO users (id, username, password, role) VALUES (%s, %s, %s, 'admin')",
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
                    "INSERT INTO activity_logs (id, username, action, ip, details) VALUES (%s,%s,%s,%s,%s)",
                    (str(uuid.uuid4()), username, action, request.remote_addr, details)
                )
            conn.commit()
    except Exception:
        pass

def get_user(username):
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE username = %s", (username,))
            return cur.fetchone()

def update_last_login(username):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET last_login = NOW() WHERE username = %s", (username,)
            )
        conn.commit()

init_db()

# ── Auth ──────────────────────────────────────────────────

@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        user = get_user(username)
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

# ── Location: browser GPS result ─────────────────────────

@app.route("/api/save_location", methods=["POST"])
def save_location():
    if "username" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    data = request.json or {}
    lat  = str(data.get("latitude", ""))
    lon  = str(data.get("longitude", ""))
    acc  = str(data.get("accuracy", "N/A"))
    gps  = str(data.get("gps_enabled", False))
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO location_logs (id,username,latitude,longitude,accuracy,gps_enabled) VALUES (%s,%s,%s,%s,%s,%s)",
                (str(uuid.uuid4()), session["username"], lat, lon, acc, gps)
            )
        conn.commit()
    log_activity(session["username"], "LOCATION_SAVED", f"Source:GPS, Acc:{acc}m")
    return jsonify({"success": True})

# ── Location: Google Geolocation API fallback ─────────────

@app.route("/api/get_location_google", methods=["POST"])
def get_location_google():
    if "username" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    if not api_key:
        log_activity(session["username"], "GOOGLE_LOC_SKIPPED", "GOOGLE_MAPS_API_KEY not set")
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
        log_activity(session["username"], "GOOGLE_LOC_TIMEOUT")
        return jsonify({"error": "Google API timed out"}), 504
    except Exception as e:
        log_activity(session["username"], "GOOGLE_LOC_ERROR", str(e))
        return jsonify({"error": "Google API error"}), 500

# ── GPS denied log ────────────────────────────────────────

@app.route("/api/log_gps_denied", methods=["POST"])
def log_gps_denied():
    if "username" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    log_activity(session["username"], "GPS_DENIED", "Browser GPS denied / failed")
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO location_logs (id,username,latitude,longitude,accuracy,gps_enabled) VALUES (%s,%s,%s,%s,%s,%s)",
                (str(uuid.uuid4()), session["username"], "", "", "", "False")
            )
        conn.commit()
    return jsonify({"success": True})

# ── Admin Dashboard ───────────────────────────────────────

@app.route("/admin")
def admin_dashboard():
    if session.get("role") != "admin":
        return redirect(url_for("login"))
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
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (id, username, password, role) VALUES (%s, %s, %s, 'user')",
                (str(uuid.uuid4()), username, hash_password(password))
            )
        conn.commit()
    log_activity(session["username"], "CREATE_USER", f"Created: {username}")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/delete_user/<username>")
def delete_user(username):
    if session.get("role") != "admin":
        return redirect(url_for("login"))
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE username = %s AND role != 'admin'", (username,))
        conn.commit()
    log_activity(session["username"], "DELETE_USER", f"Deleted: {username}")
    return redirect(url_for("admin_dashboard"))

# ── Admin Exports ─────────────────────────────────────────

@app.route("/admin/export_locations")
def export_locations():
    if session.get("role") != "admin":
        return redirect(url_for("login"))
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM location_logs ORDER BY timestamp DESC")
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(cols)
    w.writerows(rows)
    output.seek(0)
    log_activity(session["username"], "EXPORT_LOCATIONS", "Exported location CSV")
    return send_file(
        io.BytesIO(output.getvalue().encode()),
        as_attachment=True, download_name="location_logs.csv",
        mimetype="text/csv"
    )

@app.route("/admin/export_logs")
def export_logs():
    if session.get("role") != "admin":
        return redirect(url_for("login"))
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM activity_logs ORDER BY timestamp DESC")
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(cols)
    w.writerows(rows)
    output.seek(0)
    log_activity(session["username"], "EXPORT_LOGS", "Exported activity logs CSV")
    return send_file(
        io.BytesIO(output.getvalue().encode()),
        as_attachment=True, download_name="activity_logs.csv",
        mimetype="text/csv"
    )

@app.route("/admin/backup")
def create_backup():
    if session.get("role") != "admin":
        return redirect(url_for("login"))
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
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
    zip_buffer.seek(0)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_activity(session["username"], "BACKUP_CREATED", f"backup_{ts}.zip")
    return send_file(
        zip_buffer, as_attachment=True,
        download_name=f"backup_{ts}.zip",
        mimetype="application/zip"
    )
