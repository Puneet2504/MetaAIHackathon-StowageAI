"""Accounts: email + password, signed tokens, and the demo account.

Passwords are hashed with scrypt and a random salt; they are never stored, logged or returned
in plain text. Tokens are signed with HMAC, so they survive a backend restart.
Each account's ships and plans live in data/users/<account id>/.
"""
import base64
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from datetime import datetime, timezone

from fastapi import Header, HTTPException

from . import config, vessels

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 8
MAX_FAILURES, LOCK_SECONDS = 5, 60

_lock = threading.Lock()
_failures: dict[str, tuple[int, float]] = {}  # email -> (count, first failure time)


class AuthError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


# ---------- storage ----------

def _accounts_path():
    return config.USER_DIR / "accounts.json"


def _load() -> dict:
    path = _accounts_path()
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _save(accounts: dict) -> None:
    vessels.write_json(_accounts_path(), accounts)


def normalise_email(email: str) -> str:
    e = email.strip().lower()
    if len(e) > 254 or not EMAIL_RE.match(e):
        raise AuthError(422, "Enter a valid email address")
    return e


def _hash(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32).hex()


def email_of(user_id: str) -> str | None:
    return next((a["email"] for a in _load().values() if a["id"] == user_id), None)


# ---------- sign in or register ----------

def _check_lock(email: str) -> None:
    count, since = _failures.get(email, (0, 0.0))
    if count >= MAX_FAILURES and time.time() - since < LOCK_SECONDS:
        raise AuthError(429, f"Too many wrong passwords. Try again in {LOCK_SECONDS} seconds.")
    if count >= MAX_FAILURES:
        _failures.pop(email, None)


def _fail(email: str) -> None:
    count, since = _failures.get(email, (0, time.time()))
    _failures[email] = (count + 1, since)


def register(email: str, password: str) -> dict:
    email = normalise_email(email)
    if len(password) < MIN_PASSWORD:
        raise AuthError(422, f"Use a password of at least {MIN_PASSWORD} characters")
    with _lock:
        accounts = _load()
        if email in accounts:
            raise AuthError(409, "An account with this email already exists")
        salt = secrets.token_bytes(16)
        account = {"id": secrets.token_hex(16), "email": email, "salt": salt.hex(),
                   "password_hash": _hash(password, salt),
                   "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        accounts[email] = account
        _save(accounts)
    return account


def sign_in(email: str, password: str, create: bool = False) -> dict:
    """Return {token, user, created}, or {status: "new_account"} for an unknown email without create."""
    email = normalise_email(email)
    with _lock:
        _check_lock(email)
        account = _load().get(email)
        if account is None:
            if not create:
                return {"status": "new_account", "email": email}
        elif not hmac.compare_digest(account["password_hash"], _hash(password, bytes.fromhex(account["salt"]))):
            _fail(email)
            raise AuthError(401, "Wrong email or password")
        else:
            _failures.pop(email, None)
    created = account is None
    if created:
        account = register(email, password)
    return {"token": make_token(account["id"]), "user": {"email": email}, "created": created}


# ---------- tokens ----------

def _secret() -> bytes:
    if config.SECRET_KEY:
        return config.SECRET_KEY.encode()
    path = config.USER_DIR / ".secret"
    with _lock:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(secrets.token_hex(32), encoding="utf-8")
        return path.read_text(encoding="utf-8").strip().encode()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_token(user_id: str, lifetime_s: float | None = None) -> str:
    expiry = int(time.time() + (lifetime_s if lifetime_s is not None else config.TOKEN_DAYS * 86400))
    body = _b64(f"{user_id}.{expiry}".encode())
    sig = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_token(token: str) -> str | None:
    try:
        body, sig = token.split(".")
        good = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            return None
        user_id, expiry = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode().split(".")
        if int(expiry) < time.time() or not re.fullmatch(r"[0-9a-f]{32}", user_id):
            return None
        return user_id
    except (ValueError, UnicodeDecodeError):
        return None


def current_user(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Sign in first")
    user_id = verify_token(authorization[7:].strip())
    if user_id is None:
        raise HTTPException(401, "Your session has ended. Sign in again.")
    return user_id


# ---------- demo account ----------

def ensure_demo_user() -> str | None:
    """Create the demo account once (never overwrite it), and seed its workspace when empty."""
    if not (config.DEMO_USER_EMAIL and config.DEMO_USER_PASSWORD):
        return None
    email = normalise_email(config.DEMO_USER_EMAIL)
    account = _load().get(email) or register(email, config.DEMO_USER_PASSWORD)
    from .demo_seed import seed_workspace  # late import: the seed uses the solver and builder
    seed_workspace(account["id"])
    return account["id"]
