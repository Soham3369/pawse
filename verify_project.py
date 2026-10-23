"""PAWSE project verifier.  python verify_project.py
Runs on a throw-away temp database: syntax, files, templates, models, routes, seed, auth flows, telemetry API,
uploads, QR, AI anomaly, online/offline logic, route intelligence, firmware sanity. Prints PAWSE VERIFY: PASS / FAIL."""
import io
import os
import py_compile
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
errors, passed = [], 0


def check(cond, msg):
    global passed
    if cond:
        passed += 1
    else:
        errors.append(msg)
        print("  FAIL:", msg)


REQUIRED = """app.py config.py seed_demo.py verify_project.py requirements.txt README.md DEMO_ACCOUNTS.md .env.example ai.py assets.py
templates/base.html templates/login.html templates/register.html templates/choose_pet.html templates/add_pet.html templates/dashboard.html
templates/feed.html templates/search.html templates/pet_profile.html templates/friends.html templates/friend_requests.html templates/insights.html
templates/missing.html templates/_post.html templates/admin/login.html templates/admin/dashboard.html templates/admin/users.html templates/admin/pets.html
templates/admin/collars.html templates/admin/routes.html templates/admin/telemetry.html templates/admin/posts.html templates/admin/alerts.html
static/css/pawse.css static/js/dashboard.js static/js/feed.js static/js/admin.js static/vendor/chart.umd.js static/vendor/leaflet/leaflet.js
firmware/pawse_esp8266/pawse_esp8266.ino firmware/pawse_esp8266/README.md""".split()

print("1. files + syntax")
for f in REQUIRED:
    check((ROOT / f).exists(), f"missing file {f}")
for p in ROOT.glob("*.py"):
    try:
        py_compile.compile(str(p), doraise=True)
    except py_compile.PyCompileError as e:
        check(False, f"syntax error {p.name}: {e}")

print("2. firmware")
ino = (ROOT / "firmware/pawse_esp8266/pawse_esp8266.ino")
if ino.exists():
    t = ino.read_text()
    for needle in ["sensi69", "TinyGPSPlus", "MPU6050_tockn", "SoftwareSerial", "ESP8266HTTPClient", "void setup()", "void loop()", "gps_valid", "/api/telemetry"]:
        check(needle in t, f"firmware missing '{needle}'")
    check(t.count("{") == t.count("}"), "firmware braces unbalanced")
    check(t.count("(") == t.count(")"), "firmware parentheses unbalanced")

print("3. temp database + seed")
tmp = tempfile.mkdtemp()
os.environ["PAWSE_DATABASE_URI"] = "sqlite:///" + os.path.join(tmp, "verify.db")
os.environ.pop("PAWSE_SMTP_USER", None)
import app as A  # noqa: E402
import seed_demo  # noqa: E402
seed_demo.main()
seed_demo.main()                       # second run must not duplicate
from app import (Alert, Collar, Dog, Friendship, Like, Post, Telemetry, User, db)  # noqa: E402

with A.app.app_context():
    check(User.query.filter_by(is_demo=True).count() == 68, "expected 68 demo owners")
    check(Dog.query.count() == 70, f"expected 70 pets, got {Dog.query.count()} (idempotency?)")
    check(Collar.query.count() == 70, "expected 70 collars (sensi01-69 + PG-ATLAS-01)")
    names = [d.username for d in Dog.query.all()]
    check(len(names) == len(set(names)), "usernames not unique")
    check(len({d.paw_id for d in Dog.query.all()}) == 70, "paw ids not unique")
    check(len({d.collar_id for d in Dog.query.all()}) == 70, "collars not unique per pet")
    pillu = Dog.query.filter_by(username="pillu.paws").first()
    atlas = Dog.query.filter_by(username="atlas.thegolden").first()
    check(pillu and pillu.collar.device_id == "sensi69", "Pillu is not on sensi69")
    check(pillu and abs(pillu.home_lat - 19.706259) < 1e-6 and pillu.geofence_radius == 0 and pillu.pin_home, "Pillu location/geofence wrong")
    check(atlas and atlas.collar.device_id == "PG-ATLAS-01" and atlas.demo_stream, "Atlas demo stream wrong")
    check(all(Post.query.filter_by(dog_id=d.id).count() >= 3 for d in Dog.query.all()), "every pet needs >=3 posts")
    check(Telemetry.query.count() > 5000 and Alert.query.count() > 100 and Friendship.query.count() > 100 and Like.query.count() > 1000, "seed volume too low")
    check(User.query.filter_by(email="mi.jesko.x@gmail.com", is_admin=True).count() == 1, "admin missing")
    check(all(os.path.exists(os.path.join(A.Config.STATIC_DIR, d.photo)) for d in Dog.query.all()), "missing pet DP file")
    check(all(not u.password_hash.startswith(("armaanDD", "pawseDemo")) for u in User.query.all()), "plaintext password!")

print("4. templates")
for f in (ROOT / "templates").rglob("*.html"):
    try:
        A.app.jinja_env.get_template(str(f.relative_to(ROOT / "templates")).replace("\\", "/"))
    except Exception as e:
        check(False, f"template {f.name}: {e}")

print("5. routes")
rules = {r.rule for r in A.app.url_map.iter_rules()}
for need in ["/", "/login", "/register", "/choose-pet", "/dashboard", "/feed", "/search", "/pet/<username>", "/friends", "/friend-requests", "/add-pet",
             "/insights", "/missing-pet", "/api/telemetry", "/admin/login", "/admin", "/admin/users", "/admin/pets", "/admin/collars", "/admin/routes",
             "/admin/telemetry", "/admin/posts", "/admin/alerts", "/qr/<username>.png"]:
    check(need in rules, f"route {need} missing")

print("6. flows")


def tok(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def login(email, pw, path="/login"):
    c = A.app.test_client()
    r = c.post(path, data=dict(email=email, password=pw, csrf=tok(c.get(path).get_data(as_text=True))))
    return c, r


def post(c, url, page, **data):
    return c.post(url, data=dict(csrf=tok(c.get(page).get_data(as_text=True)), **data))


c, r = login("demo@pawse.local", "pawseDemo2026")
check(r.headers["Location"].endswith("/choose-pet"), "multi-pet owner should see choose-pet")
check("Who&#39;s joining PAWSE today?" in c.get("/choose-pet").get_data(as_text=True) or "Who's joining PAWSE today?" in c.get("/choose-pet").get_data(as_text=True), "choose-pet heading")
with A.app.app_context():
    atlas_id = Dog.query.filter_by(username="atlas.thegolden").first().id
post(c, "/choose-pet", "/choose-pet", dog_id=atlas_id)
for p in ["/dashboard", "/feed", "/feed?tab=discover", "/search?q=pillu", "/friends", "/friend-requests", "/insights", "/missing-pet", "/pet/pillu.paws", "/add-pet"]:
    check(c.get(p).status_code == 200, f"GET {p}")
html = c.get("/dashboard").get_data(as_text=True)
check("PAWSE_BOOT" in html and "DEMO STREAM ONLINE" in html and "SENSE" not in html.upper().replace("SENSI", ""), "dashboard boot/demo status/old name")
check("PAWSENSE" not in "".join(p.read_text(errors="ignore") for p in (ROOT / "templates").rglob("*.html")).upper(), "old name PAWSENSE found")
live = c.get(f"/api/live/{atlas_id}").get_json()
check(live["mode"] == "demo" and live["online"] and live["latest"] and len(live["series"]) >= 10, "Atlas demo stream not live")
check(live["sim_note"].startswith("Presentation"), "demo not labelled")
png = c.get("/qr/pillu.paws.png")
check(png.status_code == 200 and png.data[:8] == b"\x89PNG\r\n\x1a\n", "QR not PNG")
check(c.get("/api/live/1").status_code in (200, 403), "live api")
check(A.app.test_client().get(f"/api/live/{atlas_id}").status_code == 403, "live API must need login")
check(A.app.test_client().post("/login", data={}).status_code == 400, "CSRF must be enforced")

# image upload
from PIL import Image  # noqa: E402
buf = io.BytesIO(); Image.new("RGB", (300, 200), "pink").save(buf, "PNG"); good = buf.getvalue()
r = post(c, "/post", "/feed", caption="test post", image=(io.BytesIO(good), "x.png", "image/png"))
with A.app.app_context():
    newp = Post.query.filter_by(caption="test post").first()
    check(newp is not None and newp.image.startswith("uploads/posts/") and len(newp.image) > 20, "valid upload failed")
    if newp:
        check(os.path.exists(os.path.join(A.Config.STATIC_DIR, newp.image)), "uploaded file missing")
        db.session.delete(newp); db.session.commit()
        try: os.remove(os.path.join(A.Config.STATIC_DIR, newp.image))
        except OSError: pass
post(c, "/post", "/feed", caption="evil", image=(io.BytesIO(b"<?php evil ?>"), "x.php", "image/png"))
post(c, "/post", "/feed", caption="evil2", image=(io.BytesIO(b"not an image"), "x.png", "image/png"))
with A.app.app_context():
    check(Post.query.filter(Post.caption.in_(["evil", "evil2"])).count() == 0, "bad upload accepted")

# soham / pillu / real telemetry on sensi69
c2, r = login("soham69@gmail.com", "armaanDD")
check(r.headers["Location"].endswith("/dashboard"), "single-pet owner should go straight to dashboard")
with A.app.app_context():
    pid = Dog.query.filter_by(username="pillu.paws").first().id
j = c2.get(f"/api/live/{pid}").get_json()
check(j["mode"] == "offline" and j["status_label"] == "Collar Offline", "Pillu should be offline without real collar")
check(abs(j["geofence"]["lat"] - 19.706259) < 1e-6 and j["pinned"], "Pillu map not pinned to college")
tc = A.app.test_client()
body = dict(device_id="sensi69", gps_valid=True, lat=19.706259, lon=72.783614, speed_kmh=4.8, satellites=8, ax=0.2, ay=0.4, az=9.7, gx=.1, gy=.2, gz=.3, activity="WALKING")
check(tc.post("/api/telemetry", json=body).status_code == 401, "telemetry without key must be 401")
check(tc.post("/api/telemetry", json=body, headers={"X-Device-Key": "wrong"}).status_code == 401, "wrong key must be 401")
key = A.Config.SENSI69_KEY
r = tc.post("/api/telemetry", json=body, headers={"X-Device-Key": key})
check(r.status_code == 200 and r.get_json()["stored"] and r.get_json()["dog"] == "pillu.paws", f"real telemetry rejected {r.data}")
j = c2.get(f"/api/live/{pid}").get_json()
check(j["mode"] == "real" and j["status_label"] == "Collar Online" and j["gps"]["live"] and j["gps"]["label"] == "PET GPS LIVE", "Pillu should be online with PET GPS LIVE")
nogps = dict(body, gps_valid=False, lat=None, lon=None)
tc.post("/api/telemetry", json=nogps, headers={"X-Device-Key": key})
j = c2.get(f"/api/live/{pid}").get_json()
check(not j["gps"]["live"] and j["gps"]["label"] == "PET GPS OFFLINE" and j["online"] and j["last_known"], "GPS-unavailable handling")
spike = dict(body, ax=22, ay=5, az=14, speed_kmh=31, activity="HIGH ACTIVITY")
r = tc.post("/api/telemetry", json=spike, headers={"X-Device-Key": key})
check(r.get_json()["anomaly"] is True, "anomaly not detected on spike")
j = c2.get(f"/api/live/{pid}").get_json()
check(any(a["kind"] == "ANOMALY" for a in j["alerts"]), "anomaly alert missing")
check(tc.post("/api/telemetry", json=dict(body, device_id="nope"), headers={"X-Device-Key": key}).status_code == 401, "unknown device")
m = c2.get(f"/api/missing/{pid}").get_json()
check(m and "radius_m" in m and "Estimated search area" in m["note"], "missing-pet analysis")

# collar uniqueness + admin
with A.app.app_context():
    a_collar = Collar.query.filter_by(device_id="sensi69").first().id
    luna = Dog.query.filter_by(username="luna.paws").first().id
ca, r = login("mi.jesko.x@gmail.com", "armaanDD", "/admin/login")
check(r.headers["Location"].endswith("/admin"), "admin login")
for p in ["/admin", "/admin/users", "/admin/pets", "/admin/pets?q=PWG", "/admin/collars", "/admin/routes", "/admin/telemetry", "/admin/posts", "/admin/alerts"]:
    check(ca.get(p).status_code == 200, f"admin GET {p}")
check(c2.get("/admin").status_code == 302, "owner must not see /admin")
with A.app.app_context():
    atlas_c = Dog.query.filter_by(username="atlas.thegolden").first()
post(ca, "/admin/collars", "/admin/collars", action="create", device_id="sensi70")
post(ca, "/admin/collars", "/admin/collars", action="unassign", dog_id=atlas_id)
r = post(ca, "/admin/collars", "/admin/collars", action="assign", dog_id=atlas_id, collar_id=a_collar)   # sensi69 already assigned
with A.app.app_context():
    check(db.session.get(Dog, atlas_id).collar_id is None, "double assignment of sensi69 was allowed!")
    check(Collar.query.filter_by(device_id="sensi70").first().status == "available", "sensi70 should be available")
    check(len(A.route_clusters()) >= 1, "route intelligence found no clusters")
    check(all(r_["pets"] >= 3 for r_ in A.route_clusters()), "k-anonymity violated")

# friends
with A.app.app_context():
    busy = {x.from_dog_id for x in A.FriendRequest.query.all()} | {x.to_dog_id for x in A.FriendRequest.query.all()}
    other = Dog.query.filter(Dog.id != pid).filter(~Dog.id.in_(list(A.friend_ids(pid)) + list(busy))).first().id
r = post(c2, f"/friend/request/{other}", "/search?q=a")
with A.app.app_context():
    check(A.FriendRequest.query.filter_by(from_dog_id=pid, to_dog_id=other).count() == 1, "friend request not created")

# email: must not raise without credentials
try:
    A.send_login_email("x@y.z", "Pillu", 1, "127.0.0.1", A.utcnow()); check(True, "")
except Exception as e:
    check(False, f"email function raised {e}")
check(A.Config.LOGIN_NOTIFY_EMAIL == "mi.jesko.x@gmail.com", "notify email default")

print("7. front-end static checks")
js = (ROOT / "static/js/dashboard.js").read_text()
for needle in ["destroy()", "maintainAspectRatio: false", "watchPosition", "AI ANOMALY DETECTED", "PET GPS LIVE", "DEVICE LOCATION"]:
    check(needle in js, f"dashboard.js missing {needle}")

print(f"\n{passed} checks passed, {len(errors)} failed")
print("PAWSE VERIFY: PASS" if not errors else "PAWSE VERIFY: FAIL\n  " + "\n  ".join(errors))
sys.exit(1 if errors else 0)
