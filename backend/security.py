import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import HTTPException
from sqlalchemy import select

from backend.models import ApiKey, LoginSession, User, now

password_hasher = PasswordHasher()


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def random_token() -> str:
    return secrets.token_urlsafe(32)


def check_password(encoded: str, password: str) -> bool:
    try:
        return password_hasher.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


@dataclass(frozen=True)
class Principal:
    id: str
    email: str
    name: str
    role: str
    session_id: str | None = None
    key_id: str | None = None


def csrf(secret: str, session_id: str) -> str:
    return hmac.new(secret.encode(), ("csrf:" + session_id).encode(), hashlib.sha256).hexdigest()


def delegate(secret: str, session_id: str) -> str:
    payload = (
        base64.urlsafe_b64encode(
            json.dumps(
                {"sid": session_id, "exp": int(time.time()) + 300, "aud": "chat-gateway"},
                separators=(",", ":"),
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"chat.{payload}.{signature}"


def delegation_session(secret: str, token: str) -> str:
    try:
        _, payload, signature = token.split(".")
        expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError()
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if data["aud"] != "chat-gateway" or data["exp"] <= time.time():
            raise ValueError()
        return data["sid"]
    except (ValueError, KeyError, TypeError):
        raise HTTPException(401, "Invalid or expired chat credential") from None


def authenticate(db, settings, *, cookie=None, bearer=None) -> Principal:
    with db.session() as session:
        login = None
        key = None
        if bearer:
            if bearer.startswith("chat."):
                login = session.get(
                    LoginSession, delegation_session(settings.session_secret, bearer)
                )
            else:
                key = session.scalar(select(ApiKey).where(ApiKey.token_hash == digest(bearer)))
                if not key or key.revoked_at:
                    raise HTTPException(401, "Invalid or revoked API key")
        elif cookie:
            login = session.scalar(
                select(LoginSession).where(LoginSession.token_hash == digest(cookie))
            )
        else:
            raise HTTPException(401, "Sign in or supply a gateway API key")
        if not key and (not login or login.revoked_at or login.expires_at <= now()):
            raise HTTPException(401, "Session expired or revoked")
        user = session.get(User, key.user_id if key else login.user_id)
        if not user or not user.active:
            raise HTTPException(401, "Account is inactive")
        if key:
            key.last_used_at = now()
        return Principal(
            user.id,
            user.email,
            user.name,
            user.role,
            login.id if login else None,
            key.id if key else None,
        )


def refresh_principal(db, principal):
    """Recheck queued calls immediately before execution, after any revocation or role edit."""
    with db.session() as session:
        if principal.key_id:
            key = session.get(ApiKey, principal.key_id)
            if not key or key.user_id != principal.id or key.revoked_at:
                raise HTTPException(401, "API key was revoked while the call was waiting")
        elif principal.session_id:
            login = session.get(LoginSession, principal.session_id)
            if (
                not login
                or login.user_id != principal.id
                or login.revoked_at
                or login.expires_at <= now()
            ):
                raise HTTPException(
                    401, "Session expired or was revoked while the call was waiting"
                )
        else:
            raise HTTPException(401, "An authenticated session or API key is required")
        user = session.get(User, principal.id)
        if not user or not user.active:
            raise HTTPException(401, "Account is inactive")
        return Principal(
            user.id, user.email, user.name, user.role, principal.session_id, principal.key_id
        )


class RateLimiter:
    """Bounded single-replica abuse guard, intentionally not a distributed quota system."""

    def __init__(self):
        self.buckets = defaultdict(deque)

    def check(self, key: str, limit: int, window: int = 60):
        instant = time.monotonic()
        if len(self.buckets) > 4096:
            self.buckets = defaultdict(
                deque, {k: q for k, q in self.buckets.items() if q and q[-1] > instant - window}
            )
        bucket = self.buckets[key]
        while bucket and bucket[0] <= instant - window:
            bucket.popleft()
        if len(bucket) >= limit:
            raise HTTPException(429, "Too many requests. Retry shortly.")
        bucket.append(instant)
