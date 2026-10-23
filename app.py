"""PAWSE - smart pet IoT + social network (Flask)."""
import hmac
import io
import math
import random
import re
import secrets
import smtplib
import string
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from functools import wraps

import qrcode
import requests
from flask import (Flask, abort, flash, g, jsonify, redirect, render_template, request,
                   send_file, session, url_for)
from flask_sqlalchemy import SQLAlchemy
from PIL import Image, ImageOps
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

import ai
import assets
from config import Config

app = Flask(__name__)
app.config.from_object(Config)
db = SQLAlchemy(app)


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(dt):
    return dt.isoformat() + "Z" if dt else None


def hash_pw(pw):
    return generate_password_hash(pw, method="pbkdf2:sha256:200000")


# =============================================================== MODELS
class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(160), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(300), nullable=False)
    display_name = db.Column(db.String(80), nullable=False, default="Pet parent")
    is_admin = db.Column(db.Boolean, default=False)
    is_demo = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=utcnow)
    last_login = db.Column(db.DateTime)
    dogs = db.relationship("Dog", backref="owner", lazy=True, order_by="Dog.id")


class Collar(db.Model):
    __tablename__ = "collars"
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.String(40), unique=True, nullable=False, index=True)
    device_key = db.Column(db.String(80), nullable=False)
    status = db.Column(db.String(20), default="available")      # available | assigned
    created_at = db.Column(db.DateTime, default=utcnow)
    last_seen = db.Column(db.DateTime)
    last_lat = db.Column(db.Float)
    last_lon = db.Column(db.Float)
    battery = db.Column(db.Float)
    dog = db.relationship("Dog", back_populates="collar", uselist=False)


class Dog(db.Model):
    __tablename__ = "dogs"
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    name = db.Column(db.String(60), nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    paw_id = db.Column(db.String(24), unique=True, nullable=False, index=True)
    breed = db.Column(db.String(80))
    age = db.Column(db.String(30))
    sex = db.Column(db.String(12))
    color = db.Column(db.String(40))
    bio = db.Column(db.Text)
    photo = db.Column(db.String(200), default="images/avatar_0.png")
    collar_id = db.Column(db.Integer, db.ForeignKey("collars.id"), unique=True)
    demo_stream = db.Column(db.Boolean, default=False)   # presentation telemetry generator
    pin_home = db.Column(db.Boolean, default=False)      # map stays on configured location
    home_lat = db.Column(db.Float)
    home_lon = db.Column(db.Float)
    geofence_radius = db.Column(db.Float, default=150.0)  # metres, 0 = marker only
    created_at = db.Column(db.DateTime, default=utcnow)
    collar = db.relationship("Collar", back_populates="dog")


class Telemetry(db.Model):
    __tablename__ = "telemetry"
    id = db.Column(db.Integer, primary_key=True)
    dog_id = db.Column(db.Integer, db.ForeignKey("dogs.id"), index=True)
    device_id = db.Column(db.String(40), index=True)
    timestamp = db.Column(db.DateTime, default=utcnow, index=True)
    lat = db.Column(db.Float)
    lon = db.Column(db.Float)
    gps_valid = db.Column(db.Boolean, default=False)
    satellites = db.Column(db.Integer)
    speed = db.Column(db.Float)            # km/h
    ax = db.Column(db.Float); ay = db.Column(db.Float); az = db.Column(db.Float)
    gx = db.Column(db.Float); gy = db.Column(db.Float); gz = db.Column(db.Float)
    accel_mag = db.Column(db.Float)
    activity = db.Column(db.String(20))
    anomaly = db.Column(db.Boolean, default=False)
    anomaly_score = db.Column(db.Float, default=0.0)
    is_demo = db.Column(db.Boolean, default=False)   # True = seeded / presentation telemetry, NOT from a real collar
    __table_args__ = (db.Index("ix_tel_dog_ts", "dog_id", "timestamp"),)


class Alert(db.Model):
    __tablename__ = "alerts"
    id = db.Column(db.Integer, primary_key=True)
    dog_id = db.Column(db.Integer, db.ForeignKey("dogs.id"), index=True)
    kind = db.Column(db.String(20))        # ANOMALY | GEOFENCE | OFFLINE
    message = db.Column(db.String(240))
    severity = db.Column(db.String(12), default="warning")
    timestamp = db.Column(db.DateTime, default=utcnow, index=True)
    dog = db.relationship("Dog")


class Post(db.Model):
    __tablename__ = "posts"
    id = db.Column(db.Integer, primary_key=True)
    dog_id = db.Column(db.Integer, db.ForeignKey("dogs.id"), nullable=False, index=True)
    caption = db.Column(db.String(500))
    image = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, index=True)
    dog = db.relationship("Dog")
    likes = db.relationship("Like", backref="post", cascade="all, delete-orphan")


class Like(db.Model):
    __tablename__ = "likes"
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey("posts.id"), nullable=False, index=True)
    dog_id = db.Column(db.Integer, db.ForeignKey("dogs.id"), nullable=False)
    __table_args__ = (db.UniqueConstraint("post_id", "dog_id"),)


class FriendRequest(db.Model):
    __tablename__ = "friend_requests"
    id = db.Column(db.Integer, primary_key=True)
    from_dog_id = db.Column(db.Integer, db.ForeignKey("dogs.id"), nullable=False, index=True)
    to_dog_id = db.Column(db.Integer, db.ForeignKey("dogs.id"), nullable=False, index=True)
    status = db.Column(db.String(12), default="pending")
    created_at = db.Column(db.DateTime, default=utcnow)
    from_dog = db.relationship("Dog", foreign_keys=[from_dog_id])
    to_dog = db.relationship("Dog", foreign_keys=[to_dog_id])


class Friendship(db.Model):
    __tablename__ = "friendships"
    id = db.Column(db.Integer, primary_key=True)
    dog_a_id = db.Column(db.Integer, db.ForeignKey("dogs.id"), nullable=False, index=True)   # always the smaller id
    dog_b_id = db.Column(db.Integer, db.ForeignKey("dogs.id"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    dog_a = db.relationship("Dog", foreign_keys=[dog_a_id])
    dog_b = db.relationship("Dog", foreign_keys=[dog_b_id])
    __table_args__ = (db.UniqueConstraint("dog_a_id", "dog_b_id"),)


class OwnerAction(db.Model):
    __tablename__ = "owner_actions"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    action = db.Column(db.String(60))
    detail = db.Column(db.String(300))
    ip = db.Column(db.String(60))
    timestamp = db.Column(db.DateTime, default=utcnow, index=True)
    user = db.relationship("User")


# =============================================================== HELPERS
def log_action(user_id, action, detail=""):
    db.session.add(OwnerAction(user_id=user_id, action=action, detail=detail[:300], ip=request.remote_addr if request else None))


def new_paw_id():
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    while True:
        pid = f"PWG-{datetime.now().year}-" + "".join(secrets.choice(alphabet) for _ in range(6))
        if not Dog.query.filter_by(paw_id=pid).first():
            return pid


def make_username(name):
    base = re.sub(r"[^a-z0-9]+", "", name.lower())[:20] or "pet"
    cand = f"{base}.paws"
    n = 1
    while Dog.query.filter_by(username=cand).first():
        n += 1
        cand = f"{base}{n}.paws"
    return cand


def make_collar_key(device_id):
    return Config.SENSI69_KEY if device_id == "sensi69" else secrets.token_urlsafe(16)


def friend_ids(dog_id):
    rows = Friendship.query.filter(or_(Friendship.dog_a_id == dog_id, Friendship.dog_b_id == dog_id)).all()
    return {r.dog_b_id if r.dog_a_id == dog_id else r.dog_a_id for r in rows}


def friend_dogs(dog_id):
    ids = friend_ids(dog_id)
    return Dog.query.filter(Dog.id.in_(ids)).order_by(Dog.name).all() if ids else []


def make_friends(a, b):
    a, b = (a, b) if a < b else (b, a)
    if a != b and not Friendship.query.filter_by(dog_a_id=a, dog_b_id=b).first():
        db.session.add(Friendship(dog_a_id=a, dog_b_id=b))
        return True
    return False


ALLOWED_EXT = {"jpg", "jpeg", "png", "webp"}
ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}


def save_image(f, sub, max_side=900):
    """Validate extension, MIME, size and real image content; re-encode to a safe random filename."""
    if not f or not f.filename:
        return None
    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in ALLOWED_EXT:
        raise ValueError("Only JPG, JPEG, PNG or WEBP images are allowed.")
    if (f.mimetype or "").lower() not in ALLOWED_MIME:
        raise ValueError("Unsupported image type.")
    data = f.read(Config.MAX_IMAGE_BYTES + 1)
    if len(data) > Config.MAX_IMAGE_BYTES:
        raise ValueError("Image is too large (max 4 MB).")
    try:
        Image.open(io.BytesIO(data)).verify()
        img = Image.open(io.BytesIO(data))
        if img.format not in ("JPEG", "PNG", "WEBP"):
            raise ValueError("bad format")
        img = ImageOps.exif_transpose(img).convert("RGB")
    except Exception:
        raise ValueError("That file is not a valid image.")
    img.thumbnail((max_side, max_side))
    name = uuid.uuid4().hex + ".jpg"                 # never trust the original filename
    rel = f"uploads/{sub}/{name}"
    img.save(f"{Config.STATIC_DIR}/{rel}", "JPEG", quality=86)
    return rel


def send_login_email(user_email, dog_name, n_pets, ip, when):
    """Gmail SMTP login notification (credentials only from environment variables)."""
    if not (Config.SMTP_USER and Config.SMTP_PASSWORD):
        app.logger.info("Login email skipped: PAWSE_SMTP_USER / PAWSE_SMTP_PASSWORD not set.")
        return

    def _send():
        try:
            m = EmailMessage()
            m["Subject"] = f"PAWSE login: {user_email}"
            m["From"] = Config.SMTP_USER
            m["To"] = Config.LOGIN_NOTIFY_EMAIL
            m.set_content(f"Owner email: {user_email}\nLogin time (UTC): {when:%Y-%m-%d %H:%M:%S}\n"
                          f"IP address: {ip}\nPet selected: {dog_name}\nNumber of pets: {n_pets}\n")
            with smtplib.SMTP(Config.SMTP_HOST, Config.SMTP_PORT, timeout=15) as s:
                s.starttls()
                s.login(Config.SMTP_USER, Config.SMTP_PASSWORD)
                s.send_message(m)
        except Exception as e:  # never break login because of email
            app.logger.warning("Login email failed: %s", e)
    threading.Thread(target=_send, daemon=True).start()


def maybe_notify(user, dog):
    if user.is_admin or user.is_demo:
        return
    send_login_email(user.email, dog.name if dog else "(none yet)", len(user.dogs), request.remote_addr, utcnow())


def ago(dt):
    if not dt:
        return "never"
    s = (utcnow() - dt).total_seconds()
    if s < 60: return "just now"
    if s < 3600: return f"{int(s // 60)} min ago"
    if s < 86400: return f"{int(s // 3600)} h ago"
    return f"{int(s // 86400)} d ago"


def ist(dt, fmt="%d %b %H:%M"):
    return (dt + timedelta(hours=5, minutes=30)).strftime(fmt) + " IST" if dt else "-"


app.jinja_env.filters["ago"] = ago
app.jinja_env.filters["ist"] = ist


# =============================================================== AUTH / SESSION
@app.before_request
def load_session():
    g.user, g.dog = None, None
    uid = session.get("uid")
    if uid:
        g.user = db.session.get(User, uid)
        if g.user is None:
            session.clear()
        elif not g.user.is_admin:
            did = session.get("dog_id")
            dog = db.session.get(Dog, did) if did else None
            if dog and dog.owner_id == g.user.id:
                g.dog = dog
            elif len(g.user.dogs) == 1:
                g.dog = g.user.dogs[0]
                session["dog_id"] = g.dog.id
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    if request.method == "POST" and request.endpoint != "api_telemetry":
        sent = request.form.get("csrf") or request.headers.get("X-CSRF", "")
        if not hmac.compare_digest(sent, session["csrf"]):
            abort(400, "Bad CSRF token")


@app.context_processor
def inject():
    reqs = 0
    if g.get("dog"):
        reqs = FriendRequest.query.filter_by(to_dog_id=g.dog.id, status="pending").count()
    return dict(csrf=session.get("csrf", ""), me=g.get("user"), cur=g.get("dog"), req_count=reqs,
                photo=lambda p: url_for("static", filename=p or "images/avatar_0.png"),
                admin_page=request.path.startswith("/admin"))


def owner_required(f):
    @wraps(f)
    def w(*a, **kw):
        if not g.user:
            return redirect(url_for("login", next=request.path))
        if g.user.is_admin:
            return redirect(url_for("admin_dashboard"))
        if not g.dog:
            return redirect(url_for("add_pet") if not g.user.dogs else url_for("choose_pet"))
        return f(*a, **kw)
    return w


def admin_required(f):
    @wraps(f)
    def w(*a, **kw):
        if not (g.user and g.user.is_admin):
            return redirect(url_for("admin_login"))
        return f(*a, **kw)
    return w


def after_login(user):
    session.clear()
    session["uid"] = user.id
    session["csrf"] = secrets.token_hex(16)
    user.last_login = utcnow()
    log_action(user.id, "login", user.email)
    db.session.commit()
    if user.is_admin:
        return redirect(url_for("admin_dashboard"))
    dogs = user.dogs
    if not dogs:
        return redirect(url_for("add_pet"))
    if len(dogs) == 1:
        session["dog_id"] = dogs[0].id
        maybe_notify(user, dogs[0])
        return redirect(url_for("dashboard"))
    session["notify_pending"] = True
    return redirect(url_for("choose_pet"))


@app.route("/")
def index():
    return redirect(url_for("dashboard") if g.user and not g.user.is_admin else url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = User.query.filter_by(email=request.form.get("email", "").strip().lower()).first()
        if u and check_password_hash(u.password_hash, request.form.get("password", "")):
            return after_login(u)
        flash("Wrong email or password.", "error")
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        pw = request.form.get("password", "")
        name = request.form.get("display_name", "").strip()[:80] or "Pet parent"
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            flash("Enter a valid email.", "error")
        elif len(pw) < 8:
            flash("Password must be at least 8 characters.", "error")
        elif User.query.filter_by(email=email).first():
            flash("That email is already registered.", "error")
        else:
            u = User(email=email, password_hash=hash_pw(pw), display_name=name)
            db.session.add(u)
            db.session.commit()
            return after_login(u)
    return render_template("register.html")


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/choose-pet", methods=["GET", "POST"])
def choose_pet():
    if not g.user or g.user.is_admin:
        return redirect(url_for("login"))
    if request.method == "POST":
        dog = db.session.get(Dog, int(request.form.get("dog_id", 0) or 0))
        if not dog or dog.owner_id != g.user.id:
            abort(403)
        session["dog_id"] = dog.id
        if session.pop("notify_pending", False):
            maybe_notify(g.user, dog)
        return redirect(url_for("dashboard"))
    return render_template("choose_pet.html", dogs=g.user.dogs)


@app.post("/switch-pet")
def switch_pet():
    if not g.user:
        abort(403)
    dog = db.session.get(Dog, int(request.form.get("dog_id", 0) or 0))
    if not dog or dog.owner_id != g.user.id:
        abort(403)
    session["dog_id"] = dog.id
    return redirect(request.referrer or url_for("dashboard"))


# =============================================================== PETS
def assign_collar(dog, collar):
    if collar.status != "available" or collar.dog is not None:
        raise ValueError(f"Collar {collar.device_id} is already in use.")
    if dog.collar_id:
        raise ValueError(f"{dog.name} already has a collar. Unassign it first.")
    dog.collar_id = collar.id
    collar.status = "assigned"


def unassign_collar(dog):
    if dog.collar:
        dog.collar.status = "available"
    dog.collar_id = None


@app.route("/add-pet", methods=["GET", "POST"])
def add_pet():
    if not g.user or g.user.is_admin:
        return redirect(url_for("login"))
    available = Collar.query.filter_by(status="available").order_by(Collar.device_id).all()
    if request.method == "POST":
        name = request.form.get("name", "").strip()[:60]
        if not name:
            flash("Pet name is required.", "error")
            return render_template("add_pet.html", collars=available)
        try:
            photo = save_image(request.files.get("photo"), "pets") or f"images/avatar_{random.randint(0, 7)}.png"
        except ValueError as e:
            flash(str(e), "error")
            return render_template("add_pet.html", collars=available)
        dog = Dog(owner_id=g.user.id, name=name, username=make_username(name), paw_id=new_paw_id(),
                  breed=request.form.get("breed", "")[:80], age=request.form.get("age", "")[:30],
                  sex=request.form.get("sex", "")[:12], color=request.form.get("color", "")[:40],
                  bio=request.form.get("bio", "")[:400], photo=photo,
                  home_lat=Config.COLLEGE_LAT, home_lon=Config.COLLEGE_LON)
        db.session.add(dog)
        db.session.flush()
        cid = request.form.get("collar_id")
        try:
            if cid:
                c = db.session.get(Collar, int(cid))
                if not c:
                    raise ValueError("Collar not found.")
                assign_collar(dog, c)
            log_action(g.user.id, "add_pet", f"{dog.username} collar={cid or 'none'}")
            db.session.commit()
        except (ValueError, IntegrityError) as e:
            db.session.rollback()
            flash(str(e) if isinstance(e, ValueError) else "That collar was just taken. Pick another.", "error")
            return render_template("add_pet.html", collars=Collar.query.filter_by(status="available").all())
        session["dog_id"] = dog.id
        flash(f"Welcome {dog.name}! Your Paw ID is {dog.paw_id}", "ok")
        return redirect(url_for("dashboard"))
    return render_template("add_pet.html", collars=available)


@app.post("/pet/<username>/collar")
def pet_collar(username):
    dog = Dog.query.filter_by(username=username).first_or_404()
    if not g.user or dog.owner_id != g.user.id:
        abort(403)
    try:
        if request.form.get("action") == "unassign":
            unassign_collar(dog)
        else:
            c = db.session.get(Collar, int(request.form.get("collar_id", 0) or 0))
            if not c:
                raise ValueError("Pick a collar.")
            assign_collar(dog, c)
        log_action(g.user.id, "collar_" + request.form.get("action", "assign"), dog.username)
        db.session.commit()
    except (ValueError, IntegrityError) as e:
        db.session.rollback()
        flash(str(e) if isinstance(e, ValueError) else "Collar already in use.", "error")
    return redirect(url_for("pet_profile", username=username))


@app.route("/pet/<username>")
def pet_profile(username):
    dog = Dog.query.filter(func.lower(Dog.username) == username.lower().lstrip("@")).first_or_404()
    me = g.dog
    is_owner = bool(g.user and (dog.owner_id == g.user.id))
    fids = friend_ids(dog.id)
    status = "anon"
    if me:
        if me.id == dog.id: status = "self"
        elif dog.id in friend_ids(me.id): status = "friends"
        elif FriendRequest.query.filter_by(from_dog_id=me.id, to_dog_id=dog.id, status="pending").first(): status = "pending_out"
        elif FriendRequest.query.filter_by(from_dog_id=dog.id, to_dog_id=me.id, status="pending").first(): status = "pending_in"
        else: status = "none"
    show_private = is_owner or bool(g.user and g.user.is_admin)
    can_see_owner = show_private or status == "friends"
    posts = Post.query.filter_by(dog_id=dog.id).order_by(Post.created_at.desc()).all()
    avail = Collar.query.filter_by(status="available").order_by(Collar.device_id).all() if is_owner and not dog.collar else []
    last = Telemetry.query.filter_by(dog_id=dog.id).order_by(Telemetry.timestamp.desc()).first()
    safety = "Safe zone" if dog.geofence_radius == 0 else "Geofence active"
    return render_template("pet_profile.html", p=dog, status=status, is_owner=is_owner, show_private=show_private,
                           can_see_owner=can_see_owner, posts=posts, friend_count=len(fids), avail=avail, last=last, safety=safety,
                           friends=friend_dogs(dog.id)[:8] if status in ("friends", "self") or show_private else [])


@app.route("/qr/<username>.png")
def qr_png(username):
    dog = Dog.query.filter_by(username=username).first_or_404()
    base = Config.PUBLIC_URL.rstrip("/") or request.host_url.rstrip("/")
    img = qrcode.make(f"{base}/pet/{dog.username}", box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


# =============================================================== SOCIAL
@app.route("/feed")
@owner_required
def feed():
    tab = request.args.get("tab", "home")
    q = Post.query
    if tab != "discover":
        ids = friend_ids(g.dog.id) | {g.dog.id}
        q = q.filter(Post.dog_id.in_(ids))
        if q.count() < 3:
            q, tab = Post.query, "discover"
    posts = q.order_by(Post.created_at.desc()).limit(40).all()
    liked = {l.post_id for l in Like.query.filter_by(dog_id=g.dog.id).all()}
    return render_template("feed.html", posts=posts, liked=liked, tab=tab)


@app.post("/post")
@owner_required
def create_post():
    try:
        img = save_image(request.files.get("image"), "posts", 1200)
        if not img:
            raise ValueError("Please choose a photo.")
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("feed"))
    db.session.add(Post(dog_id=g.dog.id, caption=request.form.get("caption", "")[:500], image=img))
    log_action(g.user.id, "post", g.dog.username)
    db.session.commit()
    flash("Posted!", "ok")
    return redirect(url_for("feed"))


@app.post("/api/like/<int:post_id>")
@owner_required
def api_like(post_id):
    p = db.get_or_404(Post, post_id)
    l = Like.query.filter_by(post_id=p.id, dog_id=g.dog.id).first()
    if l:
        db.session.delete(l)
    else:
        db.session.add(Like(post_id=p.id, dog_id=g.dog.id))
    db.session.commit()
    return jsonify(liked=l is None, count=Like.query.filter_by(post_id=p.id).count())


@app.post("/post/<int:post_id>/delete")
@owner_required
def delete_post(post_id):
    p = db.get_or_404(Post, post_id)
    if p.dog.owner_id != g.user.id:
        abort(403)
    db.session.delete(p)
    db.session.commit()
    return redirect(request.referrer or url_for("feed"))


@app.route("/search")
@owner_required
def search():
    q = request.args.get("q", "").strip().lstrip("@")
    res = []
    if q:
        like = f"%{q}%"
        res = Dog.query.filter(or_(Dog.username.ilike(like), Dog.name.ilike(like), Dog.paw_id.ilike(like))).limit(40).all()
    return render_template("search.html", q=q, results=res, fids=friend_ids(g.dog.id))


@app.route("/friends")
@owner_required
def friends():
    return render_template("friends.html", friends=friend_dogs(g.dog.id))


@app.route("/friend-requests")
@owner_required
def friend_requests():
    inc = FriendRequest.query.filter_by(to_dog_id=g.dog.id, status="pending").order_by(FriendRequest.created_at.desc()).all()
    out = FriendRequest.query.filter_by(from_dog_id=g.dog.id, status="pending").order_by(FriendRequest.created_at.desc()).all()
    return render_template("friend_requests.html", incoming=inc, outgoing=out)


@app.post("/friend/request/<int:dog_id>")
@owner_required
def friend_request(dog_id):
    other = db.get_or_404(Dog, dog_id)
    if other.id == g.dog.id:
        abort(400)
    if other.id in friend_ids(g.dog.id):
        flash("Already friends.", "ok")
    else:
        rev = FriendRequest.query.filter_by(from_dog_id=other.id, to_dog_id=g.dog.id, status="pending").first()
        if rev:
            rev.status = "accepted"
            make_friends(g.dog.id, other.id)
            flash(f"You and {other.name} are now friends!", "ok")
        elif not FriendRequest.query.filter_by(from_dog_id=g.dog.id, to_dog_id=other.id, status="pending").first():
            db.session.add(FriendRequest(from_dog_id=g.dog.id, to_dog_id=other.id))
            flash("Friend request sent.", "ok")
        log_action(g.user.id, "friend_request", f"{g.dog.username}->{other.username}")
        db.session.commit()
    return redirect(request.referrer or url_for("pet_profile", username=other.username))


@app.post("/friend/respond/<int:req_id>/<decision>")
@owner_required
def friend_respond(req_id, decision):
    fr = db.get_or_404(FriendRequest, req_id)
    if fr.to_dog_id != g.dog.id or fr.status != "pending" or decision not in ("accept", "reject"):
        abort(400)
    fr.status = "accepted" if decision == "accept" else "rejected"
    if decision == "accept":
        make_friends(fr.from_dog_id, fr.to_dog_id)
    log_action(g.user.id, "friend_" + decision, f"{fr.from_dog.username}->{g.dog.username}")
    db.session.commit()
    return redirect(url_for("friend_requests"))


@app.post("/friend/remove/<int:dog_id>")
@owner_required
def friend_remove(dog_id):
    a, b = sorted((g.dog.id, dog_id))
    Friendship.query.filter_by(dog_a_id=a, dog_b_id=b).delete()
    db.session.commit()
    return redirect(request.referrer or url_for("friends"))


# =============================================================== TELEMETRY CORE
def ingest(dog, collar, d, is_demo=False):
    """Store one telemetry sample: activity, AI anomaly score, geofence events, alerts."""
    f = lambda k, default=0.0: float(d.get(k) if d.get(k) is not None else default)
    ax, ay, az = f("ax"), f("ay"), f("az", 9.81)
    mag = ai.accel_mag(ax, ay, az)
    act = d.get("activity") if d.get("activity") in ai.ACT_CODE else ai.classify_activity(mag)
    lat, lon = d.get("lat", d.get("latitude")), d.get("lon", d.get("longitude"))
    valid = bool(d.get("gps_valid")) and lat is not None and lon is not None
    if valid:
        lat, lon = float(lat), float(lon)
        valid = -90 <= lat <= 90 and -180 <= lon <= 180 and not (lat == 0 and lon == 0)
    if not valid:
        lat = lon = None
    speed = f("speed_kmh", d.get("speed", 0) or 0)
    prev_mags = [r[0] for r in db.session.query(Telemetry.accel_mag).filter_by(dog_id=dog.id).order_by(Telemetry.timestamp.desc()).limit(5)]
    home = (dog.home_lat, dog.home_lon) if dog.home_lat is not None else None
    dev = ai.haversine_m(home[0], home[1], lat, lon) if (home and valid) else 0
    x = ai.feat_vec(speed, mag, act, [m or 9.81 for m in reversed(prev_mags)] + [mag], dev)
    n_rows = Telemetry.query.filter_by(dog_id=dog.id).count()

    def loader():
        rows = Telemetry.query.filter_by(dog_id=dog.id, anomaly=False).order_by(Telemetry.timestamp.desc()).limit(500).all()
        return [dict(speed=r.speed or 0, mag=r.accel_mag or 9.81, activity=r.activity, lat=r.lat, lon=r.lon, gps_valid=r.gps_valid) for r in reversed(rows)]
    is_anom, score = ai.engine.evaluate(dog.id, n_rows, loader, x, home)

    prev_gps = Telemetry.query.filter_by(dog_id=dog.id, gps_valid=True).order_by(Telemetry.timestamp.desc()).first()
    t = Telemetry(dog_id=dog.id, device_id=collar.device_id if collar else None, timestamp=utcnow(), lat=lat, lon=lon,
                  gps_valid=valid, satellites=int(f("satellites")), speed=speed, ax=ax, ay=ay, az=az,
                  gx=f("gx"), gy=f("gy"), gz=f("gz"), accel_mag=mag, activity=act, anomaly=is_anom,
                  anomaly_score=score, is_demo=is_demo)
    db.session.add(t)
    if collar:
        collar.last_seen = t.timestamp
        if d.get("battery") is not None:
            collar.battery = float(d["battery"])
        if valid:
            collar.last_lat, collar.last_lon = lat, lon
    if is_anom:
        recent = Alert.query.filter_by(dog_id=dog.id, kind="ANOMALY").order_by(Alert.timestamp.desc()).first()
        if not recent or (t.timestamp - recent.timestamp).total_seconds() > 45:
            db.session.add(Alert(dog_id=dog.id, kind="ANOMALY", severity="warning", timestamp=t.timestamp,
                                 message="Unusual movement pattern detected."))
    if valid and dog.geofence_radius and dog.geofence_radius > 0 and dog.home_lat is not None:
        inside = ai.haversine_m(dog.home_lat, dog.home_lon, lat, lon) <= dog.geofence_radius
        was_inside = True
        if prev_gps:
            was_inside = ai.haversine_m(dog.home_lat, dog.home_lon, prev_gps.lat, prev_gps.lon) <= dog.geofence_radius
        if inside != was_inside:
            db.session.add(Alert(dog_id=dog.id, kind="GEOFENCE", severity="info" if inside else "warning", timestamp=t.timestamp,
                                 message=f"{dog.name} re-entered the geofence." if inside else f"{dog.name} left the geofence ({int(dog.geofence_radius)} m)."))
    db.session.commit()
    return t


@app.post("/api/telemetry")
def api_telemetry():
    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        return jsonify(error="invalid json"), 400
    did = str(d.get("device_id", "")).strip()
    key = str(request.headers.get("X-Device-Key") or d.get("device_key") or "")
    collar = Collar.query.filter_by(device_id=did).first()
    if not collar or not key or not hmac.compare_digest(key, collar.device_key):
        return jsonify(error="unauthorized"), 401
    collar.last_seen = utcnow()
    if not collar.dog:
        db.session.commit()
        return jsonify(ok=True, stored=False, note="collar is not assigned to a pet"), 202
    try:
        t = ingest(collar.dog, collar, d, is_demo=False)
    except (ValueError, TypeError):
        db.session.rollback()
        return jsonify(error="bad field values"), 400
    return jsonify(ok=True, stored=True, dog=collar.dog.username, activity=t.activity, gps_valid=t.gps_valid,
                   anomaly=t.anomaly, anomaly_score=t.anomaly_score)


# ---- presentation / demo telemetry (clearly flagged is_demo=True)
DEMO_LOOP = [(0, 0), (160, 40), (300, 160), (320, 330), (190, 430), (20, 380), (-110, 260), (-90, 100)]
DEMO_PHASES = [("RESTING", 0.4, 0.15), ("WALKING", 4.5, 1.6), ("WALKING", 5.8, 1.9), ("RUNNING", 11.0, 4.2),
               ("WALKING", 4.0, 1.5), ("HIGH ACTIVITY", 17.0, 7.5), ("RESTING", 0.3, 0.12), ("RUNNING", 9.0, 3.8)]
_demo_state = {}


def demo_tick(dog):
    now = utcnow()
    last = Telemetry.query.filter_by(dog_id=dog.id).order_by(Telemetry.timestamp.desc()).first()
    if last:
        age = (now - last.timestamp).total_seconds()
        if (not last.is_demo and age <= Config.ONLINE_SECONDS) or age < 3:
            return
    st = _demo_state.setdefault(dog.id, {"i": Telemetry.query.filter_by(dog_id=dog.id, is_demo=True).count(), "prog": 0.0, "t": time.time()})
    st["i"] += 1
    i = st["i"]
    rnd = random.Random(i * 7919 + dog.id)
    name, spd, amp = DEMO_PHASES[(i // 9) % len(DEMO_PHASES)]
    speed = max(0.0, spd + rnd.uniform(-0.25, 0.25) * max(spd, 1))
    mag_amp = amp * rnd.uniform(0.8, 1.2)
    spike = i % 31 == 30
    if spike:                                  # occasional presentation anomaly
        name, speed, mag_amp = "HIGH ACTIVITY", rnd.uniform(26, 33), 17
    ph = i * 0.9
    ax, ay = mag_amp * math.cos(ph) * 0.7, mag_amp * math.sin(ph * 1.3) * 0.6
    az = 9.81 + mag_amp * math.sin(ph) * 0.8
    dt = min(max(time.time() - st["t"], 1), 6)
    st["t"] = time.time()
    st["prog"] += speed / 3.6 * dt
    lat, lon = ai.loop_position((dog.home_lat, dog.home_lon), DEMO_LOOP, st["prog"])
    d = dict(gps_valid=True, lat=lat, lon=lon, satellites=rnd.randint(7, 11), speed_kmh=round(speed, 2),
             ax=round(ax, 3), ay=round(ay, 3), az=round(az, 3), gx=round(rnd.uniform(-20, 20) * mag_amp / 5, 2),
             gy=round(rnd.uniform(-20, 20) * mag_amp / 5, 2), gz=round(rnd.uniform(-10, 10), 2), activity=name)
    ingest(dog, dog.collar, d, is_demo=True)


# ---- weather (Open-Meteo, ambient only)
_wcache, _wfail = {}, {"t": 0}
WCODES = {0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Fog", 51: "Light drizzle", 53: "Drizzle",
          55: "Heavy drizzle", 61: "Light rain", 63: "Rain", 65: "Heavy rain", 80: "Rain showers", 81: "Rain showers", 82: "Violent showers",
          95: "Thunderstorm", 96: "Thunderstorm", 99: "Thunderstorm"}


def get_weather(lat, lon):
    key = (round(lat, 2), round(lon, 2))
    c = _wcache.get(key)
    if c and time.time() - c["t"] < 600:
        return c["w"]
    if time.time() - _wfail["t"] < 120:
        return c["w"] if c else {"available": False}
    try:
        r = requests.get("https://api.open-meteo.com/v1/forecast", timeout=3, params={
            "latitude": lat, "longitude": lon, "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code"})
        cur = r.json()["current"]
        w = {"available": True, "temp": cur["temperature_2m"], "humidity": cur["relative_humidity_2m"],
             "wind": cur["wind_speed_10m"], "condition": WCODES.get(cur["weather_code"], "Weather code %s" % cur["weather_code"])}
        _wcache[key] = {"t": time.time(), "w": w}
        return w
    except Exception:
        _wfail["t"] = time.time()
        return c["w"] if c else {"available": False}


def tdict(t):
    return {"id": t.id, "t": iso(t.timestamp), "speed": round(t.speed or 0, 2), "mag": round(t.accel_mag or 0, 2),
            "activity": t.activity, "act": ai.ACT_CODE.get(t.activity, 0), "anomaly": bool(t.anomaly),
            "score": round(t.anomaly_score or 0, 3), "gps_valid": bool(t.gps_valid), "lat": t.lat, "lon": t.lon,
            "satellites": t.satellites or 0}


def distance_km(dog_id, hours=24):
    rows = Telemetry.query.filter(Telemetry.dog_id == dog_id, Telemetry.gps_valid == True,
                                  Telemetry.timestamp > utcnow() - timedelta(hours=hours)).order_by(Telemetry.timestamp).limit(1500).all()
    tot = 0.0
    for a, b in zip(rows, rows[1:]):
        if (b.timestamp - a.timestamp).total_seconds() < 120:
            tot += ai.haversine_m(a.lat, a.lon, b.lat, b.lon)
    return round(tot / 1000, 2)


def live_payload(dog):
    if dog.demo_stream:
        demo_tick(dog)
    now = utcnow()
    rows = Telemetry.query.filter_by(dog_id=dog.id).order_by(Telemetry.timestamp.desc()).limit(40).all()[::-1]
    last = rows[-1] if rows else None
    age = (now - last.timestamp).total_seconds() if last else None
    online = age is not None and age <= Config.ONLINE_SECONDS
    if online and last.is_demo:
        mode, label = "demo", "DEMO STREAM ONLINE"
    elif online:
        mode, label = "real", "Collar Online"
    else:
        mode, label = "offline", "Collar Offline"
    lg = Telemetry.query.filter_by(dog_id=dog.id, gps_valid=True).order_by(Telemetry.timestamp.desc()).first()
    gps_live = bool(online and last.gps_valid)
    ref = (lg.lat, lg.lon) if lg else (dog.home_lat, dog.home_lon)
    alerts = Alert.query.filter_by(dog_id=dog.id).order_by(Alert.id.desc()).limit(8).all()
    return {
        "server_time": iso(now), "online": online, "mode": mode, "status_label": label,
        "age_s": None if age is None else int(age), "latest": tdict(last) if last else None,
        "gps": {"live": gps_live, "label": "PET GPS LIVE" if gps_live else "PET GPS OFFLINE",
                "lat": last.lat if gps_live else None, "lon": last.lon if gps_live else None,
                "satellites": last.satellites if last else 0, "speed": last.speed if last else 0},
        "last_known": {"lat": lg.lat, "lon": lg.lon, "t": iso(lg.timestamp)} if lg else None,
        "series": [tdict(r) for r in rows],
        "trail": [[r.lat, r.lon] for r in rows if r.gps_valid][-30:],
        "alerts": [{"id": a.id, "kind": a.kind, "message": a.message, "severity": a.severity, "t": iso(a.timestamp)} for a in reversed(alerts)],
        "weather": get_weather(*ref) if ref[0] is not None else {"available": False},
        "geofence": {"lat": dog.home_lat, "lon": dog.home_lon, "radius": dog.geofence_radius or 0},
        "pinned": bool(dog.pin_home), "distance_km": distance_km(dog.id),
        "sim_note": "Presentation telemetry - not from the physical collar." if mode == "demo" else "",
    }


def api_dog(dog_id):
    dog = db.get_or_404(Dog, dog_id)
    if not g.user or not (g.user.is_admin or dog.owner_id == g.user.id):
        abort(403)
    return dog


@app.get("/api/live/<int:dog_id>")
def api_live(dog_id):
    return jsonify(live_payload(api_dog(dog_id)))


@app.get("/api/insights/<int:dog_id>")
def api_insights(dog_id):
    dog = api_dog(dog_id)
    since = utcnow() - timedelta(hours=24)
    rows = Telemetry.query.filter(Telemetry.dog_id == dog.id, Telemetry.timestamp > since).all()
    counts = {k: 0 for k in ai.ACT_CODE}
    hours = [0.0] * 24
    hn = [0] * 24
    for r in rows:
        counts[r.activity or "RESTING"] = counts.get(r.activity or "RESTING", 0) + 1
        h = int(((r.timestamp + timedelta(hours=5.5)).hour))
        hours[h] += r.speed or 0
        hn[h] += 1
    avg = [round(hours[i] / hn[i], 2) if hn[i] else 0 for i in range(24)]
    week = Alert.query.filter(Alert.dog_id == dog.id, Alert.kind == "ANOMALY", Alert.timestamp > utcnow() - timedelta(days=7)).count()
    return jsonify(distance_km=distance_km(dog.id), samples=len(rows), activity=counts, hourly_speed=avg,
                   anomalies_7d=week, community=community_hotspots())


_route_cache = {"t": 0, "v": []}


def route_clusters(days=14):
    if time.time() - _route_cache["t"] < 300:
        return _route_cache["v"]
    rows = db.session.query(Telemetry.lat, Telemetry.lon, Telemetry.dog_id, Telemetry.timestamp).filter(
        Telemetry.gps_valid == True, Telemetry.timestamp > utcnow() - timedelta(days=days)).all()
    v = ai.cluster_routes([tuple(r) for r in rows])
    _route_cache.update(t=time.time(), v=v)
    return v


def community_hotspots():
    """Anonymous aggregate only: rounded centre + number of pets, never individual routes."""
    return [{"name": r["name"].replace("Common Route", "Pet-friendly zone"), "lat": round(r["lat"], 3), "lon": round(r["lon"], 3),
             "pets": r["pets"], "peak": r["peak"]} for r in route_clusters()[:4]]


@app.get("/api/missing/<int:dog_id>")
def api_missing(dog_id):
    dog = api_dog(dog_id)
    rows = Telemetry.query.filter_by(dog_id=dog.id, gps_valid=True).order_by(Telemetry.timestamp.desc()).limit(12).all()[::-1]
    if not rows:
        return jsonify(error="No valid GPS fix has ever been received for this pet."), 404
    pts = [dict(lat=r.lat, lon=r.lon, speed_kmh=r.speed) for r in rows]
    elapsed = (utcnow() - rows[-1].timestamp).total_seconds()
    est = ai.estimate_search_area(pts, elapsed)
    est["last_fix_at"] = iso(rows[-1].timestamp)
    est["history"] = [[p["lat"], p["lon"]] for p in pts]
    est["source_demo"] = bool(rows[-1].is_demo)
    log_action(g.user.id, "missing_mode", dog.username)
    db.session.commit()
    return jsonify(est)


@app.post("/geofence")
@owner_required
def set_geofence():
    try:
        r = max(0.0, min(float(request.form.get("radius", 150)), 5000.0))
    except ValueError:
        abort(400)
    g.dog.geofence_radius = r
    src = request.form.get("center")
    try:
        if src == "here":
            lat, lon = float(request.form["lat"]), float(request.form["lon"])
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                g.dog.home_lat, g.dog.home_lon = lat, lon
    except (KeyError, ValueError):
        pass
    log_action(g.user.id, "geofence", f"{g.dog.username} r={r}")
    db.session.commit()
    flash("Geofence updated.", "ok")
    return redirect(url_for("dashboard"))


# =============================================================== OWNER PAGES
@app.route("/dashboard")
@owner_required
def dashboard():
    dog = g.dog
    boot = live_payload(dog)
    posts = Post.query.filter_by(dog_id=dog.id).order_by(Post.created_at.desc()).limit(6).all()
    return render_template("dashboard.html", p=dog, boot=boot, posts=posts, friends=friend_dogs(dog.id)[:10],
                           friend_count=len(friend_ids(dog.id)))


@app.route("/insights")
@owner_required
def insights():
    return render_template("insights.html", p=g.dog)


@app.route("/missing-pet")
@owner_required
def missing():
    return render_template("missing.html", p=g.dog, home=(g.dog.home_lat, g.dog.home_lon))


# =============================================================== ADMIN
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        u = User.query.filter_by(email=request.form.get("email", "").strip().lower(), is_admin=True).first()
        if u and check_password_hash(u.password_hash, request.form.get("password", "")):
            return after_login(u)
        flash("Invalid admin credentials.", "error")
    return render_template("admin/login.html")


@app.get("/admin")
@admin_required
def admin_dashboard():
    now = utcnow()
    online = Collar.query.filter(Collar.last_seen > now - timedelta(seconds=Config.ONLINE_SECONDS)).count()
    days = []
    for i in range(6, -1, -1):
        d0 = (now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
        n = Alert.query.filter(Alert.kind == "ANOMALY", Alert.timestamp >= d0, Alert.timestamp < d0 + timedelta(days=1)).count()
        days.append({"d": d0.strftime("%d %b"), "n": n})
    stats = dict(users=User.query.filter_by(is_admin=False).count(), pets=Dog.query.count(), collars=Collar.query.count(),
                 assigned=Collar.query.filter_by(status="assigned").count(), online=online, posts=Post.query.count(),
                 telemetry=Telemetry.query.count(), alerts=Alert.query.count(), friendships=Friendship.query.count(),
                 anomalies=Telemetry.query.filter_by(anomaly=True).count())
    return render_template("admin/dashboard.html", s=stats, days=days,
                           actions=OwnerAction.query.order_by(OwnerAction.id.desc()).limit(15).all(),
                           friendships=Friendship.query.order_by(Friendship.id.desc()).limit(10).all(),
                           routes=route_clusters()[:3])


@app.get("/admin/users")
@admin_required
def admin_users():
    q = request.args.get("q", "").strip()
    qs = User.query.filter_by(is_admin=False)
    if q:
        qs = qs.filter(or_(User.email.ilike(f"%{q}%"), User.display_name.ilike(f"%{q}%")))
    return render_template("admin/users.html", users=qs.order_by(User.id).limit(300).all(), q=q)


@app.get("/admin/pets")
@admin_required
def admin_pets():
    q = request.args.get("q", "").strip().lstrip("@")
    qs = Dog.query
    if q:
        qs = qs.filter(or_(Dog.username.ilike(f"%{q}%"), Dog.paw_id.ilike(f"%{q}%"), Dog.name.ilike(f"%{q}%")))
    return render_template("admin/pets.html", pets=qs.order_by(Dog.id).limit(300).all(), q=q)


@app.route("/admin/collars", methods=["GET", "POST"])
@admin_required
def admin_collars():
    if request.method == "POST":
        act = request.form.get("action")
        try:
            if act == "create":
                did = request.form.get("device_id", "").strip()
                if not re.match(r"^[A-Za-z0-9_-]{3,32}$", did):
                    raise ValueError("Device ID: 3-32 letters, digits, - or _.")
                if Collar.query.filter_by(device_id=did).first():
                    raise ValueError(f"{did} already exists.")
                db.session.add(Collar(device_id=did, device_key=make_collar_key(did)))
                flash(f"Collar {did} created (Available).", "ok")
            elif act == "bulk":
                n = 0
                for i in range(1, 70):
                    did = f"sensi{i:02d}"
                    if not Collar.query.filter_by(device_id=did).first():
                        db.session.add(Collar(device_id=did, device_key=make_collar_key(did)))
                        n += 1
                flash(f"Created {n} collars (sensi01 - sensi69).", "ok")
            elif act == "assign":
                assign_collar(db.get_or_404(Dog, int(request.form["dog_id"])), db.get_or_404(Collar, int(request.form["collar_id"])))
                flash("Collar assigned.", "ok")
            elif act == "unassign":
                unassign_collar(db.get_or_404(Dog, int(request.form["dog_id"])))
                flash("Collar unassigned.", "ok")
            db.session.commit()
        except (ValueError, KeyError, IntegrityError) as e:
            db.session.rollback()
            flash(str(e) if isinstance(e, ValueError) else "Could not complete that action (collar already in use?).", "error")
        return redirect(url_for("admin_collars"))
    collars = Collar.query.order_by(Collar.device_id).all()
    unassigned = Dog.query.filter(Dog.collar_id.is_(None)).order_by(Dog.name).all()
    return render_template("admin/collars.html", collars=collars, unassigned=unassigned, now=utcnow(), online_s=Config.ONLINE_SECONDS)


@app.get("/admin/routes")
@admin_required
def admin_routes():
    _route_cache["t"] = 0
    return render_template("admin/routes.html", routes=route_clusters())


@app.get("/admin/telemetry")
@admin_required
def admin_telemetry():
    q = Telemetry.query
    dev = request.args.get("device", "").strip()
    if dev:
        q = q.filter(Telemetry.device_id.ilike(f"%{dev}%"))
    if request.args.get("anomalies"):
        q = q.filter_by(anomaly=True)
    rows = q.order_by(Telemetry.id.desc()).limit(200).all()
    names = {d.id: d.name for d in Dog.query.all()}
    return render_template("admin/telemetry.html", rows=rows, names=names, dev=dev)


@app.get("/admin/posts")
@admin_required
def admin_posts():
    return render_template("admin/posts.html", posts=Post.query.order_by(Post.id.desc()).limit(100).all())


@app.post("/admin/posts/<int:pid>/delete")
@admin_required
def admin_post_delete(pid):
    db.session.delete(db.get_or_404(Post, pid))
    db.session.commit()
    return redirect(url_for("admin_posts"))


@app.get("/admin/alerts")
@admin_required
def admin_alerts():
    return render_template("admin/alerts.html", alerts=Alert.query.order_by(Alert.id.desc()).limit(200).all())


# =============================================================== INIT
def ensure_admin():
    u = User.query.filter_by(email=Config.ADMIN_EMAIL.lower()).first()
    if not u:
        db.session.add(User(email=Config.ADMIN_EMAIL.lower(), display_name="PAWSE Admin", is_admin=True,
                            password_hash=hash_pw(Config.ADMIN_PASSWORD or Config.ADMIN_DEFAULT_PASSWORD)))
    elif Config.ADMIN_PASSWORD and not check_password_hash(u.password_hash, Config.ADMIN_PASSWORD):
        u.password_hash = hash_pw(Config.ADMIN_PASSWORD)
    db.session.commit()


def init_db():
    with app.app_context():
        db.create_all()
        assets.ensure_default_avatars(Config.STATIC_DIR)
        ensure_admin()


init_db()

if __name__ == "__main__":
    if "--init" in sys.argv:
        print("Database initialised.")
    else:
        app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
