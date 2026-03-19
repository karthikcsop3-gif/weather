# 🌤 WeatherSphere

A full-featured weather web application with role-based login, real-time GPS weather, admin panel, location logs, activity monitoring, and data backup.

---

## 📁 Project Structure

```
weather_app/
├── app.py                  # Main Flask application
├── requirements.txt        # Python dependencies
├── README.md
├── templates/
│   ├── login.html          # Login page
│   ├── user_dashboard.html # User weather dashboard
│   └── admin_dashboard.html# Admin control panel
├── data/                   # CSV-based database (auto-created)
│   ├── users.csv           # User accounts
│   ├── activity_logs.csv   # Login/action logs
│   └── location_logs.csv   # GPS location records
└── backups/                # Backup ZIP archives (auto-created)
```

---

## 🚀 Setup & Run

### 1. Install Python (3.8+)

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Run the app
```bash
python app.py
```

### 4. Open in browser
```
http://localhost:5000
```

---

## 🔑 Default Credentials

| Role  | Username | Password  |
|-------|----------|-----------|
| Admin | admin    | admin123  |

> ⚠️ Change the admin password in `data/users.csv` after first login (hash new password with SHA-256).

---

## 🌟 Features

### User Dashboard
- Requests browser GPS/location permission on load
- Shows a notification banner if location is denied
- Displays real-time weather: temperature, humidity, wind, pressure, visibility
- Shows a 5-day forecast
- All location data is saved securely to CSV

### Admin Panel
- **Users tab**: Create and delete user accounts
- **Location Logs**: View all GPS records (lat/lon, accuracy, GPS status) — export as CSV
- **Activity Logs**: Full audit trail (login, logout, GPS denied, exports, etc.) — export as CSV
- **Backup**: Download a ZIP archive of all data files

---

## 🔒 Security Notes

- Passwords are stored as SHA-256 hashes
- Session-based authentication with server-side role checks
- Location data is only accessible in the admin panel
- All user actions are logged with timestamp and IP

---

## 🌐 Weather Data

Weather is fetched from the free **Open-Meteo API** (no API key needed).
Location names are resolved via **Nominatim / OpenStreetMap**.

---

## 💾 Backup & Restore

- Click **"Create Backup"** in the admin sidebar to download a ZIP of all CSV data.
- To restore: replace the contents of the `data/` folder with files from the backup ZIP.
"# weather" 
