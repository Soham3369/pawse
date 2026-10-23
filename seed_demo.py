"""Idempotent demo seeder:  python seed_demo.py
Creates collars sensi01..sensi69 + PG-ATLAS-01, 68 demo owners (+ Atlas/Luna on the demo account), the special
Soham/Pillu account, pets, posts, likes, friendships, telemetry history, alerts. Safe to run twice."""
import math
import os
import random
from datetime import timedelta

import ai
import assets
from app import (Alert, Collar, Config, Dog, Friendship, FriendRequest, Like, Post, Telemetry, User, app, db,
                 ensure_admin, hash_pw, make_collar_key, make_friends, new_paw_id, utcnow)

RNG = random.Random(2026)
STATIC = Config.STATIC_DIR

CORRIDORS = [  # fictional-use walking corridors (lat, lon) waypoints - shared by many pets => aggregate route clusters
    ("SJCEM campus loop", [(19.7063, 72.7836), (19.7072, 72.7852), (19.7081, 72.7871), (19.7090, 72.7890), (19.7083, 72.7908)]),
    ("Palghar station road", [(19.6960, 72.7690), (19.6975, 72.7706), (19.6990, 72.7724), (19.7004, 72.7741)]),
    ("Borivali national-park edge", [(19.2150, 72.8700), (19.2166, 72.8712), (19.2183, 72.8722), (19.2199, 72.8735)]),
    ("IC Colony garden walk", [(19.2403, 72.8456), (19.2412, 72.8470), (19.2421, 72.8486), (19.2430, 72.8499)]),
    ("Juhu beach strip", [(19.0990, 72.8260), (19.1003, 72.8266), (19.1016, 72.8272), (19.1030, 72.8278)]),
]
NAMES = """Atlas Luna Pillu Bruno Coco Max Bella Charlie Daisy Rocky Milo Ruby Simba Lily Oscar Nala Zeus Mocha Buddy Maple
Tiger Pepper Ginger Rex Choco Sunny Leo Misty Bolt Peanut Hazel Jack Cleo Toby Poppy Duke Kiwi Biscuit Oreo Teddy Pixie
Rusty Sandy Shadow Ziggy Marley Finn Olive Gizmo Waffles Cookie Sparky Bailey Nova Archie Honey Loki Willow Chester Pumpkin
Scout Mango Domino Tofu Pancake Rio Bean Truffle Echo Cinnamon Dobby Jelly Nugget Sprout""".split()
HUMAN = """Aarav Mehta,Diya Nair,Kabir Shah,Ananya Iyer,Vihaan Patil,Isha Kulkarni,Reyansh Gupta,Myra Desai,Arjun Rao,Saanvi Joshi,
Ishaan Verma,Anika Bose,Advait Menon,Kiara Sethi,Dhruv Malhotra,Riya Chopra,Yash Pawar,Tara Bhatt,Rohan Dubey,Navya Reddy""".replace("\n", "").split(",")
BREEDS = ["Golden Retriever", "Labrador", "Beagle", "Indie (Desi)", "Pomeranian", "Pug", "Shih Tzu", "Husky", "German Shepherd",
          "Indian Spitz", "Cocker Spaniel", "Dachshund", "Corgi", "Dalmatian", "Rottweiler"]
COLORS = ["Golden", "Black", "Brown", "Cream", "White", "Tan", "Grey", "Spotted"]
BIOS = ["Professional zoomie athlete 🐾", "Treat negotiator & sunbeam hunter ☀️", "Park patrol, 7 days a week",
        "Fetch is my love language 🎾", "Nap expert. Belly rubs accepted.", "Tail wags guaranteed 🐕", "Sniffing out the best walks in town",
        "Certified good boy/girl. Ask my human.", "Squirrel watcher (level 99)"]
CAPTIONS = ["Morning zoomies complete 🐕💨", "Found the best sunny spot today ☀️🐾", "Park day!", "Treat o'clock 🦴", "Muddy paws, happy heart 💙",
            "Who else loves rainy-day walks? 🌧️", "New stick. Best day ever.", "Nap time after a big walk 😴", "Beach vibes 🌊🐾",
            "Made a new friend at the park today 🐶💕", "Caught my own tail again. 10/10.", "Training session: sit, stay, snack ✅",
            "Sunset stroll with my human 🌅", "Wearing my PAWSE collar like a pro 😎", "Weekend hike done! So tired, so happy.",
            "Zoomies at 6 AM again 🏃", "Bath time (not my favourite) 🛁", "Fetch champion of the lane 🎾"]
PILLU_CAPS = ["Found the best sunny spot today ☀️🐾", "Guarding the campus like a pro 🎓🐕", "Project demo day with my human! 💙",
              "Nap break between lectures 😴", "Spotted a squirrel near the library 🐿️"]
ATLAS_CAPS = ["Morning zoomies complete 🐕💨", "Golden hour at the garden walk ✨", "Fetch champion of IC Colony 🎾", "Treat o'clock 🦴", "Wearing my PAWSE collar like a pro 😎"]
LUNA_CAPS = ["Park day!", "Squirrel watching, level 99 🐿️", "Rainy day cuddles 🌧️", "New bandana, who dis? 💖"]


def session_points(rng, corridor, n, end, jitter=12, speed_bias=1.0, center=None, spread=None):
    pts = []
    t0 = end - timedelta(seconds=30 * n)
    act_i = 1
    for k in range(n):
        if rng.random() < 0.18:
            act_i = rng.choices([0, 1, 2, 3], [3, 6, 3, 0.6])[0]
        name = ai.ACT_CODE and list(ai.ACT_CODE)[act_i]
        spd = {0: 0.3, 1: 4.5, 2: 10.5, 3: 16.0}[act_i] * speed_bias + rng.uniform(-0.4, 0.4)
        amp = {0: 0.15, 1: 1.6, 2: 4.2, 3: 7.5}[act_i] * rng.uniform(0.8, 1.2)
        anomaly = rng.random() < 0.025
        if anomaly:
            name, spd, amp = "HIGH ACTIVITY", rng.uniform(26, 33), 17
        valid = rng.random() > 0.04
        if center:
            lat, lon = ai.offset_latlon(center[0], center[1], rng.gauss(0, spread), rng.gauss(0, spread))
        else:
            f = abs(((k / max(n - 1, 1)) * 2) % 2 - 1)
            f = 1 - f
            seg = f * (len(corridor) - 1)
            i0 = min(int(seg), len(corridor) - 2)
            fr = seg - i0
            lat = corridor[i0][0] + (corridor[i0 + 1][0] - corridor[i0][0]) * fr
            lon = corridor[i0][1] + (corridor[i0 + 1][1] - corridor[i0][1]) * fr
            lat, lon = ai.offset_latlon(lat, lon, rng.gauss(0, jitter), rng.gauss(0, jitter))
        ph = k * 0.9
        ax, ay, az = amp * math.cos(ph) * .7, amp * math.sin(ph * 1.3) * .6, 9.81 + amp * math.sin(ph) * .8
        pts.append(dict(timestamp=t0 + timedelta(seconds=30 * k), lat=lat if valid else None, lon=lon if valid else None,
                        gps_valid=valid, satellites=rng.randint(6, 11) if valid else rng.randint(0, 2), speed=max(0.0, round(spd, 2)),
                        ax=round(ax, 3), ay=round(ay, 3), az=round(az, 3), gx=round(rng.uniform(-15, 15), 2), gy=round(rng.uniform(-15, 15), 2),
                        gz=round(rng.uniform(-8, 8), 2), accel_mag=round(math.sqrt(ax*ax + ay*ay + az*az), 3), activity=name,
                        anomaly=anomaly, anomaly_score=round(rng.uniform(.72, .96), 3) if anomaly else round(rng.uniform(.05, .45), 3), is_demo=True))
    return pts


def create_dog(owner, name, username, device, idx, corridor_i, caps, home, rng, demo_stream=False, pin=False, radius=150.0, college=False):
    bio = rng.choice(BIOS)
    av_rel = f"demo/avatars/{username.replace('.', '_')}.png"
    assets.make_avatar(os.path.join(STATIC, av_rel), idx)
    collar = Collar.query.filter_by(device_id=device).first()
    dog = Dog(owner_id=owner.id, name=name, username=username, paw_id=new_paw_id(), breed=rng.choice(BREEDS), age=f"{rng.randint(1, 11)} yrs",
              sex=rng.choice(["Male", "Female"]), color=rng.choice(COLORS), bio=bio, photo=av_rel, demo_stream=demo_stream,
              pin_home=pin, home_lat=home[0], home_lon=home[1], geofence_radius=radius)
    db.session.add(dog)
    db.session.flush()
    dog.collar_id = collar.id
    collar.status = "assigned"
    now = utcnow()
    # posts
    for k, cap in enumerate(caps):
        rel = f"demo/posts/{username.replace('.', '_')}_{k}.jpg"
        assets.make_post_image(os.path.join(STATIC, rel), seed=idx * 13 + k, avatar_idx=idx)
        db.session.add(Post(dog_id=dog.id, caption=cap, image=rel, created_at=now - timedelta(hours=rng.randint(2, 24 * 9), minutes=rng.randint(0, 59))))
    # telemetry history: one very recent session + two older ones
    rows = []
    if college:
        rows += session_points(rng, None, 70, now - timedelta(minutes=5), center=home, spread=18, speed_bias=.5)
        rows += session_points(rng, None, 50, now - timedelta(hours=20), center=home, spread=25, speed_bias=.5)
    else:
        name_c, wp = CORRIDORS[corridor_i % len(CORRIDORS)]
        rows += session_points(rng, wp, 40, now - timedelta(minutes=rng.randint(3, 90)))
        for dback, hr in ((1, rng.choice([7, 8, 18])), (rng.randint(2, 5), rng.choice([7, 18, 19]))):
            end = (now - timedelta(days=dback)).replace(hour=hr, minute=rng.randint(0, 40)) - timedelta(hours=5, minutes=30)
            rows += session_points(rng, wp, 80, end)
    for r in rows:
        r.update(dog_id=dog.id, device_id=device)
    db.session.bulk_insert_mappings(Telemetry, rows)
    last = [r for r in rows if r["gps_valid"]]
    collar.last_seen = max(r["timestamp"] for r in rows)
    if last:
        collar.last_lat, collar.last_lon = last[-1]["lat"], last[-1]["lon"]
    collar.battery = round(rng.uniform(55, 98), 0)
    for r in [x for x in rows if x["anomaly"]][:4]:
        db.session.add(Alert(dog_id=dog.id, kind="ANOMALY", message="Unusual movement pattern detected.", severity="warning", timestamp=r["timestamp"]))
    if radius:
        db.session.add(Alert(dog_id=dog.id, kind="GEOFENCE", message=f"{name} left the geofence ({int(radius)} m).", severity="warning", timestamp=now - timedelta(hours=rng.randint(3, 40))))
        db.session.add(Alert(dog_id=dog.id, kind="GEOFENCE", message=f"{name} re-entered the geofence.", severity="info", timestamp=now - timedelta(hours=rng.randint(1, 3))))
    return dog


def main():
    with app.app_context():
        db.create_all()
        assets.ensure_default_avatars(STATIC)
        ensure_admin()
        for i in range(1, 70):
            did = f"sensi{i:02d}"
            if not Collar.query.filter_by(device_id=did).first():
                db.session.add(Collar(device_id=did, device_key=make_collar_key(did)))
        if not Collar.query.filter_by(device_id="PG-ATLAS-01").first():
            db.session.add(Collar(device_id="PG-ATLAS-01", device_key=make_collar_key("PG-ATLAS-01")))
        db.session.commit()

        accounts = []                      # for DEMO_ACCOUNTS.md
        names = list(NAMES)
        ATLAS_HOME = (19.2403, 72.8456)
        COLLEGE = (Config.COLLEGE_LAT, Config.COLLEGE_LON)

        def get_owner(email, pw, name, demo):
            u = User.query.filter_by(email=email).first()
            if u:
                return u, False
            u = User(email=email, password_hash=hash_pw(pw), display_name=name, is_demo=demo)
            db.session.add(u)
            db.session.flush()
            return u, True

        # 1) presentation account: Atlas + Luna
        u, new = get_owner("demo@pawse.local", "pawseDemo2026", "Demo Owner", True)
        accounts.append(("demo@pawse.local", "pawseDemo2026", "Atlas (@atlas.thegolden) PG-ATLAS-01, Luna (@luna.paws) sensi01", "presentation account, live demo stream"))
        if new:
            rng = random.Random(1)
            create_dog(u, "Atlas", "atlas.thegolden", "PG-ATLAS-01", 0, 3, ATLAS_CAPS, ATLAS_HOME, rng, demo_stream=True, pin=False, radius=250)
            create_dog(u, "Luna", "luna.paws", "sensi01", 4, 0, LUNA_CAPS, CORRIDORS[0][1][0], rng, demo_stream=True, radius=200)
            db.session.commit()

        # 2) special account: Soham / Pillu on the physical collar sensi69
        u, new = get_owner("soham69@gmail.com", "armaanDD", "Soham", False)
        accounts.append(("soham69@gmail.com", "armaanDD", "Pillu (@pillu.paws) sensi69", "physical prototype collar, college location, geofence 0 m"))
        if new:
            create_dog(u, "Pillu", "pillu.paws", "sensi69", 2, 0, PILLU_CAPS, COLLEGE, random.Random(69), pin=True, radius=0.0, college=True)
            db.session.commit()

        # 3) 67 more demo owners -> sensi02..sensi68  (total demo owners incl. demo@pawse.local = 68)
        pool = [n for n in names if n not in ("Atlas", "Luna", "Pillu")]
        for n in range(2, 69):
            email, pw = f"owner{n:02d}@demo.pawse.local", f"Paws@{n:02d}Demo"
            pet = pool[(n - 2) % len(pool)]
            owner_name = HUMAN[(n - 2) % len(HUMAN)]
            accounts.append((email, pw, f"{pet} (@{pet.lower()}.paws) sensi{n:02d}", f"demo owner {n}"))
            u, new = get_owner(email, pw, owner_name, True)
            if new:
                rng = random.Random(n * 31)
                ci = n % len(CORRIDORS)
                create_dog(u, pet, f"{pet.lower()}.paws", f"sensi{n:02d}", n, ci, rng.sample(CAPTIONS, rng.randint(3, 5)),
                           CORRIDORS[ci][1][0], rng, radius=rng.choice([120.0, 200.0, 300.0]))
            if n % 10 == 0:
                db.session.commit()
        db.session.commit()

        # 4) social graph (deterministic => idempotent)
        dogs = Dog.query.order_by(Dog.id).all()
        by = {d.username: d for d in dogs}
        rng = random.Random(77)
        for d in dogs:
            for o in rng.sample(dogs, 6):
                if o.id != d.id:
                    make_friends(d.id, o.id)
        for special in ("atlas.thegolden", "pillu.paws", "luna.paws"):
            for o in rng.sample(dogs, 14):
                if o.id != by[special].id:
                    make_friends(by[special].id, o.id)
        make_friends(by["atlas.thegolden"].id, by["pillu.paws"].id)
        make_friends(by["luna.paws"].id, by["pillu.paws"].id)
        db.session.commit()
        for tgt, k in (("atlas.thegolden", 3), ("pillu.paws", 2), ("luna.paws", 2)):
            t = by[tgt]
            fr = friend_set = {x for x in [f.dog_b_id if f.dog_a_id == t.id else f.dog_a_id for f in Friendship.query.filter((Friendship.dog_a_id == t.id) | (Friendship.dog_b_id == t.id))]}
            cands = [d for d in dogs if d.id != t.id and d.id not in fr][:k]
            for c in cands:
                if not FriendRequest.query.filter_by(from_dog_id=c.id, to_dog_id=t.id).first():
                    db.session.add(FriendRequest(from_dog_id=c.id, to_dog_id=t.id))
        rng = random.Random(5)
        existing = {(l.post_id, l.dog_id) for l in Like.query.all()}
        for p in Post.query.all():
            for d in rng.sample(dogs, rng.randint(8, 58)):
                if (p.id, d.id) not in existing:
                    db.session.add(Like(post_id=p.id, dog_id=d.id))
        db.session.commit()

        # 5) credentials file
        lines = ["# PAWSE demo accounts", "", "All data is fictional. Generated by `python seed_demo.py`.", "",
                 "## Admin", "", "| Email | Password | URL |", "|---|---|---|",
                 f"| {Config.ADMIN_EMAIL} | armaanDD (default, change via PAWSE_ADMIN_PASSWORD) | /admin/login |", "",
                 "## Accounts", "", "| Email | Password | Pets / devices | Notes |", "|---|---|---|---|"]
        lines += [f"| {a} | {b} | {c} | {d} |" for a, b, c, d in accounts]
        with open("DEMO_ACCOUNTS.md", "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print(f"Seed OK: {User.query.filter_by(is_admin=False).count()} owners, {Dog.query.count()} pets, {Collar.query.count()} collars, "
              f"{Post.query.count()} posts, {Telemetry.query.count()} telemetry rows, {Friendship.query.count()} friendships, {Alert.query.count()} alerts")


if __name__ == "__main__":
    main()
