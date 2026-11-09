import hashlib
import re
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

PASSWORD_CONTEXT = CryptContext(schemes=["bcrypt"], deprecated="auto")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 12
DEVICE_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,128}$")


def hash_password(password: str) -> str:
    if len(password.encode("utf-8")) > 72:
        raise ValueError("Password must be at most 72 UTF-8 bytes.")
    return PASSWORD_CONTEXT.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    if len(password.encode("utf-8")) > 72:
        return False
    return PASSWORD_CONTEXT.verify(password, hashed_password)


def hash_device_code(device_code: str) -> str:
    if not DEVICE_CODE_PATTERN.fullmatch(device_code):
        raise ValueError("Device code must be 32-128 URL-safe characters.")
    return hashlib.sha256(device_code.encode("ascii")).hexdigest()


def create_access_token(subject: str, secret_key: str) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": subject, "iat": now, "exp": now + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)},
        secret_key,
        algorithm=JWT_ALGORITHM,
    )


def decode_access_token(token: str, secret_key: str) -> str | None:
    try:
        subject = jwt.decode(token, secret_key, algorithms=[JWT_ALGORITHM]).get("sub")
    except JWTError:
        return None
    return subject if isinstance(subject, str) and subject.isdecimal() else None
