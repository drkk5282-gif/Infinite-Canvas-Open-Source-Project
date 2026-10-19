"""Short-lived signed tokens for /uploads/ (MyApi server_mode public fetch)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from urllib.parse import urlparse

from backend.config import DATA_DIR

_SIGN_SECRET = hashlib.sha256(f"{DATA_DIR}:doubao-media-sign-v1".encode()).digest()
_DEFAULT_TTL = 7200


def _canonical_upload_path(url: str) -> str:
    """Normalize any uploads reference to /uploads/... for sign + verify."""
    raw = (url or "").strip().split("?", 1)[0].split("#", 1)[0]
    if not raw:
        return ""
    if raw.startswith("/uploads/"):
        return raw
    if raw.startswith("uploads/"):
        return f"/{raw}"
    if raw.startswith("http://") or raw.startswith("https://"):
        path = urlparse(raw).path or ""
        if path.startswith("/uploads/"):
            return path
    return raw


def sign_upload_access(url: str, *, ttl_sec: int = _DEFAULT_TTL) -> str:
    """Return url with ?mt= token for unauthenticated fetch (external MyApi relay)."""
    raw = (url or "").strip()
    if not raw:
        return url
    bare = raw.split("?", 1)[0].split("#", 1)[0]
    canon = _canonical_upload_path(bare)
    if not canon.startswith("/uploads/"):
        return url
    exp = int(time.time()) + max(60, int(ttl_sec))
    msg = f"{canon}|{exp}".encode()
    sig = hmac.new(_SIGN_SECRET, msg, hashlib.sha256).hexdigest()
    payload = base64.urlsafe_b64encode(f"{exp}:{sig}".encode()).decode().rstrip("=")
    sep = "&" if "?" in bare else "?"
    return f"{bare}{sep}mt={payload}"


def _verify_token_for_key(sign_key: str, tok: str) -> bool:
    if not sign_key or not tok:
        return False
    try:
        pad = "=" * (-len(tok) % 4)
        decoded = base64.urlsafe_b64decode(tok + pad).decode("ascii")
        exp_s, sig = decoded.split(":", 1)
        exp = int(exp_s)
    except Exception:
        return False
    if exp < int(time.time()):
        return False
    msg = f"{sign_key}|{exp}".encode()
    expected = hmac.new(_SIGN_SECRET, msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


def verify_upload_access_token(
    url: str,
    token: str | None,
    *,
    public_absolute: str | None = None,
) -> bool:
    """Accept tokens signed with /uploads/... or legacy full https://... keys."""
    canon = _canonical_upload_path(url)
    tok = (token or "").strip()
    if not tok:
        return False
    keys: list[str] = []
    if canon.startswith("/uploads/"):
        keys.append(canon)
    if public_absolute:
        bare = public_absolute.strip().split("?", 1)[0].split("#", 1)[0]
        if bare and bare not in keys:
            keys.append(bare)
    return any(_verify_token_for_key(k, tok) for k in keys)
