import hashlib
import hmac
from datetime import timedelta

import bcrypt
import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.errors import DomainError
from app.clock import utcnow
from app.models import User

bearer = HTTPBearer(auto_error=False)


def hash_password(password: str, rounds: int = 12) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=rounds)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def hash_device_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def device_key_matches(raw_key: str, key_hash: str) -> bool:
    if not raw_key:
        return False
    return hmac.compare_digest(hash_device_key(raw_key), key_hash)


def create_token(user: User) -> str:
    settings = get_settings()
    payload = {
        "sub": str(user.id),
        "role": user.role,
        "exp": utcnow() + timedelta(minutes=settings.jwt_ttl_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise DomainError("unauthorized", "Missing bearer token", 401)
    settings = get_settings()
    try:
        payload = jwt.decode(credentials.credentials, settings.jwt_secret, algorithms=["HS256"])
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise DomainError("unauthorized", "Invalid or expired token", 401) from None
    user = db.get(User, user_id)
    if user is None:
        raise DomainError("unauthorized", "User no longer exists", 401)
    return user


def require_roles(*roles: str):
    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise DomainError("forbidden", "Insufficient role", 403)
        return user

    return dependency


def user_by_email(db: Session, email: str) -> User | None:
    return db.scalars(select(User).where(User.email == email.lower())).one_or_none()
