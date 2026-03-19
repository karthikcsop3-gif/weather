from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file
import csv, os, hashlib, uuid, requests
from datetime import datetime
import zipfile

app = Flask(__name__)
app.secret_key = "weather_app_secret_key_2024_xK9#mP"

DATA_DIR       = "data"
BACKUP_DIR     = "backups"
USERS_FILE     = os.path.join(DATA_DIR, "users.csv")
LOGS_FILE      = os.path.join(DATA_DIR, "activity_logs.csv")
LOCATIONS_FILE = os.path.join(DATA_DIR, "location_logs.csv")

os.makedirs(DATA_DIR,   exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

# ── Helpers ───────────────────────────────────────────────

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def init_db():
    if not os.path.exists(USERS_FILE):
        with open(USERS_FILE, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["id", "username", "password", "role", "created_at", "last_login"])
            w.writerow([str(uuid.uuid4()), "admin", hash_password("admin123"),
                        "admin", datetime.now().isoformat(), ""])
    if not os.path.exists(LOGS_FILE):
        with open(LOGS_FILE, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["id", "timestamp", "username", "action", "ip", "details"])
    if not os.path.exists(LOCATIONS_FILE):
        with open(LOCATIONS_FILE, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["id", "timestamp", "username",
                        "latitude", "longitude", "accuracy", "gps_enabled"])

def read_csv(filepath):
    if not os.path.exists(filepath):
        return []
    with open(filepath, "r", newline="") as f:
        return list(csv.DictReader(f))

def write_csv(filepath, rows, fieldnames):
    with open(filepath, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

def append_csv(filepath, row):
    with open(filepath, "a", newline="") as f:
        csv.writer(f).writerow(row)

def log_activity(username, action, details=""):
    append_csv(LOGS_FILE, [
        str(uuid.uuid4()), datetime.now().isoformat(),
        username, action, request.remote_addr, details
    ])

def get_user(username):
    for u in read_csv(USERS_FILE):
        if u["username"] == username:
            return u
    return None

def update_last_login(username):
    users = read_csv(USERS_FILE)
    for u in users:
        if u["username"] == username:
            u["last_login"] = datetime.now().isoformat()
    write_csv(USERS_FILE, users,
              ["id", "username", "password", "role", "created_at", "last_login"])

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
    lat  = data.get("latitude")
    lon  = data.get("longitude")
    acc  = data.get("accuracy", "N/A")
    gps  = data.get("gps_enabled", False)
    append_csv(LOCATIONS_FILE, [
        str(uuid.uuid4()), datetime.now().isoformat(),
        session["username"], lat, lon, acc, str(gps)
    ])
    # Coordinates stored server-side only — never returned to the client
    log_activity(session["username"], "LOCATION_SAVED",
                 f"Source:GPS, Acc:{acc}m")
    return jsonify({"success": True})

# ── Location: Google Geolocation API fallback ─────────────
# Called only when browser GPS fails after all retries.
# The Google API key lives in the environment variable
# GOOGLE_MAPS_API_KEY — it is never sent to the frontend.

@app.route("/api/get_location_google", methods=["POST"])
def get_location_google():
    if "username" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    if not api_key:
        log_activity(session["username"], "GOOGLE_LOC_SKIPPED",
                     "GOOGLE_MAPS_API_KEY not set")
        return jsonify({"error": "Google API not configured"}), 503

    try:
        # Google Geolocation API — works via WiFi / cell towers / IP
        # No GPS required, no user permission popup needed
        resp = requests.post(
            f"https://www.googleapis.com/geolocation/v1/geolocate?key={api_key}",
            json={},      # empty body = let Google use all available signals
            timeout=10
        )
        resp.raise_for_status()
        data = resp.json()

        if "location" not in data:
            log_activity(session["username"], "GOOGLE_LOC_FAILED",
                         f"Response: {data}")
            return jsonify({"error": "No location in response"}), 502

        lat = round(data["location"]["lat"], 6)
        lon = round(data["location"]["lng"], 6)
        acc = round(data.get("accuracy", 0))

        append_csv(LOCATIONS_FILE, [
            str(uuid.uuid4()), datetime.now().isoformat(),
            session["username"], lat, lon, acc, "google"
        ])
        # API key safe — only lat/lon returned so the frontend
        # can fetch weather. They are never displayed to the user.
        log_activity(session["username"], "LOCATION_SAVED",
                     f"Source:Google, Acc:{acc}m")
        return jsonify({
            "success":   True,
            "latitude":  lat,
            "longitude": lon,
            "accuracy":  acc
        })

    except requests.exceptions.Timeout:
        log_activity(session["username"], "GOOGLE_LOC_TIMEOUT")
        return jsonify({"error": "Google API timed out"}), 504
    except Exception as e:
        log_activity(session["username"], "GOOGLE_LOC_ERROR", str(e))
        return jsonify({"error": "Google API error"}), 500

# ── GPS denied / failed log ───────────────────────────────

@app.route("/api/log_gps_denied", methods=["POST"])
def log_gps_denied():
    if "username" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    log_activity(session["username"], "GPS_DENIED",
                 "Browser GPS denied / failed")
    append_csv(LOCATIONS_FILE, [
        str(uuid.uuid4()), datetime.now().isoformat(),
        session["username"], "", "", "", "False"
    ])
    return jsonify({"success": True})

# ── Admin Dashboard ───────────────────────────────────────

@app.route("/admin")
def admin_dashboard():
    if session.get("role") != "admin":
        return redirect(url_for("login"))
    users     = [u for u in read_csv(USERS_FILE) if u["role"] != "admin"]
    logs      = read_csv(LOGS_FILE)[-50:][::-1]
    locations = read_csv(LOCATIONS_FILE)[-50:][::-1]
    all_logs  = read_csv(LOGS_FILE)
    all_locs  = read_csv(LOCATIONS_FILE)
    stats = {
        "total_users"    : len(users),
        "total_logins"   : sum(1 for l in all_logs if l["action"] == "LOGIN"),
        "gps_enabled"    : sum(1 for l in all_locs  if l["gps_enabled"] == "True"),
        "total_locations": len(all_locs)
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
    append_csv(USERS_FILE, [
        str(uuid.uuid4()), username, hash_password(password),
        "user", datetime.now().isoformat(), ""
    ])
    log_activity(session["username"], "CREATE_USER", f"Created: {username}")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/delete_user/<username>")
def delete_user(username):
    if session.get("role") != "admin":
        return redirect(url_for("login"))
    users = [u for u in read_csv(USERS_FILE) if u["username"] != username]
    write_csv(USERS_FILE, users,
              ["id", "username", "password", "role", "created_at", "last_login"])
    log_activity(session["username"], "DELETE_USER", f"Deleted: {username}")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/export_locations")
def export_locations():
    if session.get("role") != "admin":
        return redirect(url_for("login"))
    log_activity(session["username"], "EXPORT_LOCATIONS", "Exported location CSV")
    return send_file(LOCATIONS_FILE, as_attachment=True,
                     download_name="location_logs.csv")

@app.route("/admin/export_logs")
def export_logs():
    if session.get("role") != "admin":
        return redirect(url_for("login"))
    log_activity(session["username"], "EXPORT_LOGS", "Exported activity logs CSV")
    return send_file(LOGS_FILE, as_attachment=True,
                     download_name="activity_logs.csv")

@app.route("/admin/backup")
def create_backup():
    if session.get("role") != "admin":
        return redirect(url_for("login"))
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = os.path.join(BACKUP_DIR, f"backup_{ts}.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in os.listdir(DATA_DIR):
            zf.write(os.path.join(DATA_DIR, f), f)
    log_activity(session["username"], "BACKUP_CREATED",
                 f"File: backup_{ts}.zip")
    return send_file(zip_path, as_attachment=True,
                     download_name=f"backup_{ts}.zip")
