from base64 import b64decode, b64encode
from collections import deque
from functools import cache
from hashlib import scrypt, sha256
from hmac import compare_digest
from secrets import token_bytes, token_urlsafe
from threading import Lock
from time import monotonic

SCRYPT_N: int = 2 ** 17
SCRYPT_R: int = 8
SCRYPT_P: int = 1
SCRYPT_MAXMEM: int = 2 ** 28
MIN_PASSWORD_LENGTH: int = 8
MAX_PASSWORD_LENGTH: int = 256
USERNAME_PATTERN: str = r"^[a-z0-9][a-z0-9_.-]{1,31}$"
EMAIL_PATTERN: str = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=SCRYPT_MAXMEM, dklen=32)


def hash_password(password: str) -> str:
    salt = token_bytes(16)
    digest = _derive(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${b64encode(salt).decode()}${b64encode(digest).decode()}"


@cache
def _decoy_hash() -> str:
    return hash_password(token_urlsafe(16))


def verify_password(password: str, encoded: str | None) -> bool:
    try:
        scheme, n, r, p, salt, digest = (encoded or _decoy_hash()).split("$")
        if scheme != "scrypt":
            return False
        valid = compare_digest(_derive(password, b64decode(salt), int(n), int(r), int(p)), b64decode(digest))
    except ValueError:
        return False
    return valid and encoded is not None


def new_token() -> str:
    return token_urlsafe(32)


def token_digest(token: str) -> str:
    return sha256(token.encode()).hexdigest()


class LoginThrottle(object):
    def __init__(self, *, max_per_address: int = 50, max_per_pair: int = 10, window_seconds: float = 900) -> None:
        self.max_per_address: int = max_per_address
        self.max_per_pair: int = max_per_pair
        self.window_seconds: float = window_seconds
        self._attempts: dict[str, deque[float]] = {}
        self._lock: Lock = Lock()

    def _count(self, key: str, now: float) -> int:
        attempts = self._attempts.get(key)
        if attempts is None:
            return 0
        while attempts and attempts[0] <= now - self.window_seconds:
            attempts.popleft()
        if not attempts:
            del self._attempts[key]
            return 0
        return len(attempts)

    def attempt(self, address_key: str, pair_key: str) -> bool:
        now = monotonic()
        with self._lock:
            if self._count(address_key, now) >= self.max_per_address or self._count(pair_key, now) >= self.max_per_pair:
                return False
            for key in (address_key, pair_key):
                self._attempts.setdefault(key, deque()).append(now)
            return True

    def reset(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)
