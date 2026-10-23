# PAWSE 🐾 - smart-pet IoT + pet social network

Smart collar (ESP8266 + NEO-6M GPS + MPU6050) → Flask/SQLite server → live dashboard, AI anomaly detection, geofence, weather, QR pet profiles, pet feed, admin console, aggregate route intelligence, missing-pet search-area estimate.

## 1. Overview & 2. Architecture
```
 NodeMCU ESP8266 (sensi69) --WiFi/HTTP JSON every 5 s--> Flask  /api/telemetry  --> SQLite pawse.db
   GPS (NEO-6M) + IMU (MPU6050)                              |-- IsolationForest anomaly + activity + geofence
 Browser dashboard  <--- /api/live/<pet> (polled every 3 s) -|-- Open-Meteo weather (ambient only)
 Demo mode: server generates presentation telemetry (flagged is_demo) for Atlas/Luna when no real collar is sending.
```
Files: `app.py` (models + routes), `ai.py` (IsolationForest, DBSCAN, missing-pet), `assets.py` (bundled avatars/post images), `seed_demo.py`, `verify_project.py`, `templates/`, `static/`, `firmware/`.

## 3-5. Components, wiring, pins
NodeMCU ESP8266 (ESP-12E), NEO-6M GPS, MPU6050, LED + 220 Ω. USB powered during development.

| Module pin | NodeMCU | GPIO |
|---|---|---|
| MPU6050 VCC | 3V3 | - |
| MPU6050 GND | GND | - |
| MPU6050 SDA | D2 | GPIO4 |
| MPU6050 SCL | D1 | GPIO5 |
| NEO-6M GND | GND | - |
| NEO-6M TX | D5 | GPIO14 (ESP receives) |
| NEO-6M RX | D6 | GPIO12 (ESP transmits) |
| NEO-6M VCC | **3V3** (recommended) | - |
| LED anode (via 220 Ω) | D7 | GPIO13 |
| LED cathode | GND | - |

```
NodeMCU 3V3 ──┬── MPU6050 VCC        D2 ─── MPU6050 SDA      D5 ◄── NEO-6M TX
              └── NEO-6M VCC         D1 ─── MPU6050 SCL      D6 ──► NEO-6M RX
NodeMCU GND ──┬── MPU6050 GND        D7 ──[220Ω]──►|── GND  (LED)
              ├── NEO-6M GND
              └── LED cathode
```
**NEO-6M supply voltage:** check your breakout. Typical blue *GY-NEO6MV2* boards carry a 3.3 V regulator and accept 3.3-5 V on VCC, but a bare NEO-6M chip/module without regulator is **3.3 V only**. Powering from 3V3 is safe for both and is what this project uses. The module draws ~50 mA; the NodeMCU 3V3 rail handles it. Its TX is 3.3 V logic so it is safe for the ESP8266 input.

## 6-10. Arduino IDE, board, libraries, upload, WiFi
1. Install Arduino IDE 2.x. File → Preferences → Additional Boards Manager URLs: `https://arduino.esp8266.com/stable/package_esp8266com_index.json`.
2. Tools → Board → Boards Manager → install **esp8266**; choose **NodeMCU 1.0 (ESP-12E Module)**. If the port is missing install the CH340 or CP2102 USB driver.
3. Library Manager: **TinyGPSPlus** (Mikal Hart), **MPU6050_tockn** (tockn). (ESP8266WiFi/HTTPClient/SoftwareSerial/Wire come with the board package.)
4. Open `firmware/pawse_esp8266/pawse_esp8266.ino`, edit `WIFI_SSID`, `WIFI_PASS`, `SERVER_URL` (e.g. `http://192.168.1.50:5000/api/telemetry` - your PC's LAN IP from `ipconfig` / `ip a`), `DEVICE_KEY` (must equal `PAWSE_SENSI69_KEY`, default `sensi69-change-this-device-key`).
5. Upload, then Serial Monitor at 115200. The collar and PC must be on the same WiFi (2.4 GHz). Allow Python through the PC firewall on port 5000.

LED: fast blink = no WiFi · slow heartbeat = WiFi + GPS fix · double blink = WiFi but GPS unavailable · short flash = sent OK · three rapid flashes = server/HTTP error.

## 11-14. Server, Python, database, demo accounts
```bash
# Python 3.10+  (Windows: python.org installer, tick "Add to PATH"; Linux: sudo apt install python3 python3-venv)
cd PAWSE
python -m venv venv
venv\Scripts\activate            # Linux/macOS: source venv/bin/activate
pip install -r requirements.txt
copy .env.example .env           # Linux/macOS: cp .env.example .env   (then edit)
python seed_demo.py              # optional - the shipped pawse.db is already seeded; safe to re-run, never duplicates
python verify_project.py         # expect: PAWSE VERIFY: PASS
python app.py                    # http://localhost:5000  (also http://<PC-IP>:5000 on the LAN)
```
SQLite file `pawse.db` is created automatically (`python app.py --init` just initialises). Delete it and run `python seed_demo.py` to reset.

| Login | Email | Password |
|---|---|---|
| Presentation (Atlas + Luna) | demo@pawse.local | pawseDemo2026 |
| Pillu on **sensi69** | soham69@gmail.com | armaanDD |
| Admin (`/admin/login`) | mi.jesko.x@gmail.com | armaanDD (override with `PAWSE_ADMIN_PASSWORD`) |
| 67 more demo owners | owner02@demo.pawse.local … owner68@… | `Paws@02Demo` … `Paws@68Demo` |

All in `DEMO_ACCOUNTS.md`. Change the admin password and `PAWSE_SECRET_KEY` before any public deployment.

## 15. Collar assignment (sensi69)
Admin → **Collars** → *Create collar* `sensi69` (or *Generate sensi01…sensi69*) → status **Available** → owner registers/opens a pet → **Assign collar** → choose `sensi69` → confirm. The DB links Dog→Collar (one collar per pet, enforced in code and by a UNIQUE column), and the ESP8266 sends `device_id=sensi69` so the server finds the pet automatically. Admin can also assign/unassign from the Collars page. `sensi69` is pre-assigned to Pillu in the seed data.

## 16-18. Real vs demo telemetry, browser fallback
* **Real:** the collar POSTs to `/api/telemetry` with header `X-Device-Key`. Rows are stored with `is_demo=0`; the dashboard says **🟢 Collar Online** when a sample is < 15 s old, otherwise **🔴 Collar Offline**.
* **Demo:** pets with `demo_stream=1` (Atlas, Luna) get a new *presentation* sample every ≥3 s when the dashboard is polled and no real collar data is arriving; stored with `is_demo=1`, shown as **🟢 DEMO STREAM ONLINE** with a "Presentation telemetry" note. About every 31st sample is a spike that triggers the AI anomaly popup. Real data always takes priority; nothing demo is ever labelled as the physical collar.
* **Switching:** to present with hardware, log in as Pillu (sensi69 is a real collar, no simulation). To make another pet simulate, set `demo_stream=1` for it; to stop simulating Atlas, set it to 0 (SQLite: `UPDATE dogs SET demo_stream=0 WHERE username='atlas.thegolden';`).
* **GPS fallback:** when pet GPS is invalid the page shows 🔴 PET GPS OFFLINE and, using `navigator.geolocation.watchPosition`, a separate 📍 DEVICE LOCATION marker. The pet's last known GPS stays as a faded marker; the two are never merged. Browsers only allow geolocation on `localhost` or HTTPS.
* Pillu's map is pinned to SJCEM Palghar (19.706259, 72.783614); geofence radius 0 m = marker only (breach alerts disabled).

## 19. Email (Gmail login notifications)
Google Account → Security → turn on 2-Step Verification → App passwords → create one. In `.env`:
```
PAWSE_SMTP_USER=yourgmail@gmail.com
PAWSE_SMTP_PASSWORD=<16-char app password>
PAWSE_LOGIN_NOTIFY_EMAIL=mi.jesko.x@gmail.com
```
When a **real** owner (not demo, not admin) logs in (multi-pet owners: after choosing a pet) a mail with owner email, time, IP, pet and pet count is sent in a background thread. Missing credentials or SMTP errors never block login (see server log). `soham69@gmail.com` counts as a real owner so you can test it.

## 20-24. QR, social, AI
* **QR:** every pet page has a PNG QR (`/qr/<username>.png`) → `/pet/<username>`. Set `PAWSE_PUBLIC_URL=http://<PC-IP>:5000` so phones scan a reachable address. Public view shows pet name, DP, bio, breed, friend button - no owner details, no device ID.
* **Social:** feed (friends + you, Discover tab), photo posts (upload only; JPG/PNG/WEBP ≤4 MB, content verified, re-encoded, random filename), likes, search by @username/name/Paw ID, friend requests, friends list.
* **Anomaly detection:** per-pet IsolationForest on speed, acceleration magnitude, activity, short-term acceleration variability, distance from home (trained from the pet's own history; rule-based fallback and hard limits). Popup "⚠️ AI ANOMALY DETECTED" slides in for 7 s and stays in the alert list. Activity (RESTING/WALKING/RUNNING/HIGH ACTIVITY) is behavioural, not medical.
* **Route intelligence (admin):** DBSCAN (haversine, ε≈120 m) over all GPS fixes → Common Route #n with area, pets, visits, peak time (IST). Only clusters used by ≥3 pets are shown, centroids rounded - aggregate only.
* **Missing pet:** last valid fix + recent bearing + speed + elapsed time → *estimated search area* circle/sector with confidence, never an exact location.
* **Weather:** Open-Meteo ambient weather at the pet's location, labelled "Local Weather".

## 25. Troubleshooting
| Symptom | Fix |
|---|---|
| Serial: `HTTP -1` | wrong `SERVER_URL`/firewall/different WiFi; open `http://PC-IP:5000` from a phone to test |
| `401 unauthorized` | `DEVICE_KEY` ≠ server key; collar missing - create `sensi69` in Admin → Collars |
| `202 not assigned` | assign the collar to a pet |
| `IMU not found` | swap SDA/SCL, check 3V3/GND, I²C address 0x68 (AD0 low) |
| `no NMEA data` | NEO-6M TX → D5, common GND, power; first fix needs open sky (cold start up to several minutes) |
| Dashboard shows Offline for Pillu | expected until the real collar sends; Atlas shows the demo stream |
| Map tiles blank | map tiles need internet (Leaflet/Chart.js themselves are bundled offline) |
| Weather unavailable | no internet / Open-Meteo unreachable |
| No email | check `.env`, App Password, server log line "Login email failed" |
| Port in use | edit `app.run(port=...)` in `app.py` |

## 26-27. Security & deployment
Passwords: PBKDF2-SHA256 hashes (Werkzeug). CSRF token on all browser POSTs. Device API needs per-collar key (constant-time compare). Uploads validated by extension, MIME, size, real image decode, re-encoded with random names. Secrets only via env/.env (never commit `.env`). Telemetry key travels in plain HTTP on the LAN - for internet use put Flask behind HTTPS (nginx/Caddy + gunicorn) and switch the firmware to `WiFiClientSecure`. SQLite is for the prototype; use PostgreSQL for production. Run with `debug=False` (default).
