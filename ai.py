"""PAWSE AI helpers: activity classification, IsolationForest anomaly detection,
DBSCAN aggregate route intelligence, missing-pet search-area estimation.
Pure functions - no database access (app.py supplies the data)."""
import math
import threading
import time

import numpy as np
from sklearn.cluster import DBSCAN
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

ACT_CODE = {"RESTING": 0, "WALKING": 1, "RUNNING": 2, "HIGH ACTIVITY": 3}
G = 9.80665
R_EARTH = 6371000.0


# ---------------------------------------------------------------- geometry
def haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R_EARTH * math.asin(min(1, math.sqrt(a)))


def bearing_deg(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def destination(lat, lon, bearing, dist_m):
    b, d = math.radians(bearing), dist_m / R_EARTH
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540) % 360 - 180


def offset_latlon(lat, lon, east_m, north_m):
    return (lat + north_m / 111320.0, lon + east_m / (111320.0 * math.cos(math.radians(lat))))


def loop_position(origin, offsets, progress_m):
    """Point on a closed polyline of metre offsets around origin after travelling progress_m."""
    pts = [offset_latlon(origin[0], origin[1], e, n) for e, n in offsets]
    pts.append(pts[0])
    segs = [haversine_m(*pts[i], *pts[i + 1]) for i in range(len(pts) - 1)]
    total = sum(segs) or 1.0
    d = progress_m % total
    for i, s in enumerate(segs):
        if d <= s:
            f = d / s if s else 0
            return pts[i][0] + (pts[i + 1][0] - pts[i][0]) * f, pts[i][1] + (pts[i + 1][1] - pts[i][1]) * f
        d -= s
    return pts[0]


# ---------------------------------------------------------------- activity
def accel_mag(ax, ay, az):
    return math.sqrt(ax * ax + ay * ay + az * az)


def classify_activity(mag):
    """Behavioural classification from acceleration magnitude (m/s^2). NOT a medical diagnosis."""
    dyn = abs(mag - 9.81)
    if dyn < 0.6:
        return "RESTING"
    if dyn < 2.5:
        return "WALKING"
    if dyn < 6.0:
        return "RUNNING"
    return "HIGH ACTIVITY"


# ---------------------------------------------------------------- anomaly detection
def feat_vec(speed, mag, act, recent_mags, dev_m):
    std = float(np.std(recent_mags)) if len(recent_mags) > 1 else 0.0
    return [float(speed or 0), float(mag or 9.81), float(ACT_CODE.get(act, 0)), std, math.log1p(max(dev_m or 0, 0))]


def build_features(rows, home=None):
    """rows: dicts(speed, mag, activity, lat, lon, gps_valid) in time order."""
    if home is None:
        vs = [(r["lat"], r["lon"]) for r in rows if r.get("gps_valid") and r.get("lat") is not None]
        home = (float(np.median([v[0] for v in vs])), float(np.median([v[1] for v in vs]))) if vs else None
    X = []
    for i, r in enumerate(rows):
        dev = 0.0
        if home and r.get("gps_valid") and r.get("lat") is not None:
            dev = haversine_m(home[0], home[1], r["lat"], r["lon"])
        recent = [x["mag"] for x in rows[max(0, i - 5): i + 1]]
        X.append(feat_vec(r["speed"], r["mag"], r["activity"], recent, dev))
    return np.array(X, dtype=float)


class AnomalyEngine:
    """Per-pet IsolationForest, lazily trained from that pet's own history, with rule-based safety net."""

    def __init__(self):
        self.cache = {}
        self.lock = threading.Lock()

    def _model(self, key, n_rows, loader, home):
        c = self.cache.get(key)
        if c and n_rows - c["n"] < 50 and time.time() - c["t"] < 600:
            return c["m"]
        if n_rows < 40:
            return None
        rows = loader()
        if len(rows) < 40:
            return None
        X = build_features(rows, home)
        sc = StandardScaler().fit(X)
        mdl = IsolationForest(n_estimators=80, contamination=0.04, random_state=7).fit(sc.transform(X))
        self.cache[key] = {"n": n_rows, "t": time.time(), "m": (mdl, sc)}
        return (mdl, sc)

    def evaluate(self, key, n_rows, loader, x, home=None):
        """Returns (is_anomaly, score 0..1)."""
        hard = x[1] > 26 or x[0] > 25
        with self.lock:
            m = self._model(key, n_rows, loader, home)
        if m is None:
            score = min(1.0, max(abs(x[1] - 9.81) / 20.0, x[0] / 30.0))
            return bool(hard or x[1] > 22 or x[0] > 20), round(score, 3)
        mdl, sc = m
        z = sc.transform([x])
        s = -float(mdl.score_samples(z)[0])
        pred = int(mdl.predict(z)[0]) == -1
        score = min(1.0, max(0.0, (s - 0.35) / 0.4))
        if hard:
            score = max(score, 0.9)
        return bool(pred or hard), round(score, 3)


engine = AnomalyEngine()


# ---------------------------------------------------------------- route intelligence
def cluster_routes(points, eps_m=120, min_samples=12, min_pets=3, max_points=20000, top=8):
    """Aggregate, anonymised route clustering with DBSCAN (haversine).
    points: (lat, lon, dog_id, datetime_utc). Clusters used by fewer than min_pets pets are dropped
    (k-anonymity) and only rounded centroids + counts are returned - never individual routes."""
    if len(points) < min_samples:
        return []
    if len(points) > max_points:
        idx = np.random.default_rng(1).choice(len(points), max_points, replace=False)
        points = [points[i] for i in idx]
    arr = np.radians(np.array([[p[0], p[1]] for p in points]))
    labels = DBSCAN(eps=eps_m / R_EARTH, min_samples=min_samples, metric="haversine", algorithm="ball_tree").fit_predict(arr)
    out = []
    for lab in sorted(set(labels)):
        if lab == -1:
            continue
        pts = [points[i] for i in np.where(labels == lab)[0]]
        pets = {p[2] for p in pts}
        if len(pets) < min_pets:
            continue
        lat = float(np.mean([p[0] for p in pts]))
        lon = float(np.mean([p[1] for p in pts]))
        dists = sorted(haversine_m(lat, lon, p[0], p[1]) for p in pts)
        extent = dists[int(len(dists) * 0.9)]
        hist = [0] * 24
        for p in pts:
            hist[int((p[3].hour + p[3].minute / 60 + 5.5) % 24)] += 1   # UTC -> IST
        peak = max(range(24), key=hist.__getitem__)
        visits = {(p[2], p[3].date()) for p in pts}
        out.append({
            "lat": round(lat, 4), "lon": round(lon, 4), "radius_m": int(max(80, min(extent, 1500))),
            "pets": len(pets), "points": len(pts), "visits": len(visits),
            "peak": f"{peak:02d}:00-{(peak + 1) % 24:02d}:00 IST", "hist": hist,
            "kind": "Corridor" if extent > 350 else "Hotspot",
        })
    out.sort(key=lambda r: (r["pets"], r["visits"]), reverse=True)
    for i, r in enumerate(out[:top], 1):
        r["name"] = f"Common Route #{i}"
    return out[:top]


# ---------------------------------------------------------------- missing pet
def estimate_search_area(pts, elapsed_s):
    """pts: oldest->newest list of dicts(lat, lon, speed_kmh) of VALID gps fixes. Returns an *estimated search area*."""
    if not pts:
        return None
    last = pts[-1]
    recent = pts[-8:]
    heading, spread = None, 1.0
    if len(recent) >= 2:
        bs = [bearing_deg(a["lat"], a["lon"], b["lat"], b["lon"]) for a, b in zip(recent, recent[1:])
              if haversine_m(a["lat"], a["lon"], b["lat"], b["lon"]) > 2]
        if bs:
            x = sum(math.cos(math.radians(b)) for b in bs) / len(bs)
            y = sum(math.sin(math.radians(b)) for b in bs) / len(bs)
            heading = (math.degrees(math.atan2(y, x)) + 360) % 360
            spread = 1 - math.hypot(x, y)            # 0 = very consistent heading, 1 = random
    speeds = [p.get("speed_kmh") or 0 for p in recent]
    avg_mps = max(sum(speeds) / len(speeds) / 3.6, 0.4)
    travel = min(elapsed_s, 1800) * avg_mps * 0.6
    cl, cn = last["lat"], last["lon"]
    if heading is not None:
        cl, cn = destination(last["lat"], last["lon"], heading, travel * 0.5)
    radius = max(120.0, min(3000.0, travel * 0.7 + 90))
    conf = 0.85 - min(elapsed_s / 7200.0, 0.5) - 0.2 * spread - (0.1 if len(pts) < 5 else 0)
    conf = max(0.1, min(0.95, conf))
    sector = []
    if heading is not None:
        sector = [[last["lat"], last["lon"]]]
        for k in range(-35, 36, 10):
            sector.append(list(destination(last["lat"], last["lon"], heading + k, radius + travel * 0.5)))
    return {
        "last_known": {"lat": last["lat"], "lon": last["lon"]},
        "center": {"lat": cl, "lon": cn}, "radius_m": int(radius),
        "heading_deg": None if heading is None else int(heading), "avg_speed_kmh": round(avg_mps * 3.6, 1),
        "confidence": round(conf, 2), "confidence_label": "High" if conf > 0.65 else "Medium" if conf > 0.4 else "Low",
        "sector": sector, "elapsed_min": int(elapsed_s // 60),
        "note": "Estimated search area - not an exact predicted location.",
    }
