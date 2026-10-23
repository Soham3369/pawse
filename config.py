"""PAWSE configuration. Secrets come from environment variables / .env (never hard-code mail passwords)."""
import os
import secrets
from pathlib import Path

BASE = Path(__file__).resolve().parent


def _load_env():
    p = BASE / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()


def _secret():
    env = os.environ.get("PAWSE_SECRET_KEY")
    if env:
        return env
    p = BASE / ".secret_key"
    if p.exists():
        return p.read_text().strip()
    k = secrets.token_hex(32)
    p.write_text(k)
    return k


class Config:
    SECRET_KEY = _secret()
    SQLALCHEMY_DATABASE_URI = os.environ.get("PAWSE_DATABASE_URI", "sqlite:///" + str(BASE / "pawse.db"))
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_CONTENT_LENGTH = 8 * 1024 * 1024          # hard request cap
    MAX_IMAGE_BYTES = 4 * 1024 * 1024             # per uploaded image
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    STATIC_DIR = str(BASE / "static")

    ADMIN_EMAIL = os.environ.get("PAWSE_ADMIN_EMAIL", "mi.jesko.x@gmail.com")
    ADMIN_PASSWORD = os.environ.get("PAWSE_ADMIN_PASSWORD")        # None -> default used on first creation only
    ADMIN_DEFAULT_PASSWORD = "armaanDD"
    SENSI69_KEY = os.environ.get("PAWSE_SENSI69_KEY", "sensi69-change-this-device-key")
    PUBLIC_URL = os.environ.get("PAWSE_PUBLIC_URL", "")

    ONLINE_SECONDS = 15                            # telemetry newer than this => collar ONLINE
    COLLEGE_LAT, COLLEGE_LON = 19.706259, 72.783614  # St. John College of Engineering & Management, Palghar

    SMTP_HOST = os.environ.get("PAWSE_SMTP_HOST", "smtp.gmail.com")
    SMTP_PORT = int(os.environ.get("PAWSE_SMTP_PORT", "587"))
    SMTP_USER = os.environ.get("PAWSE_SMTP_USER")
    SMTP_PASSWORD = os.environ.get("PAWSE_SMTP_PASSWORD")
    LOGIN_NOTIFY_EMAIL = os.environ.get("PAWSE_LOGIN_NOTIFY_EMAIL", "mi.jesko.x@gmail.com")
