"""Local file storage on the VPS, replacing app/services/s3.py (AWS S3).

Keeps the exact same presigned-URL contract the frontend already expects
(POST /uploads/presign -> {upload_url, object_key, public_url}, then the
browser PUTs the file bytes straight to upload_url) so no frontend change
was needed — only what upload_url actually points at changed.

Security property of a real S3 presigned URL that this preserves: the URL
is time-limited and tamper-proof without needing any request auth/session.
Implemented here as an HMAC-SHA256 signature over (object_key, expiry),
verified in app/routers/uploads.py's PUT handler — same idea AWS's own
presigned URLs use internally, just homegrown (found MinIO's free binary
distribution has been discontinued — dl.min.io returns 410 Gone, and their
GitHub releases ship with no downloadable assets either — so self-hosting
a real S3-compatible server wasn't actually available without a much
bigger, riskier build; this is the contained alternative).

Files are saved under STORAGE_ROOT and served back out as static files by
Apache directly (see the kiosk-api.reeyalifestyle.com vhost config, added
alongside the reverse-proxy rule), not by this Python process — same
division of labor a real object store would have: a dedicated, fast
static-file path, separate from the API.
"""

import hashlib
import hmac
import os
import re
import time

import requests

from app.config import get_settings

# .../backend/storage — three levels up from this file (app/services/storage.py).
STORAGE_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "storage")

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]")


def _sanitize_filename(filename: str) -> str:
    """Strips path separators and anything else that isn't a plain filename
    character — filename comes from the client and becomes part of a real
    filesystem path, so this is the one thing standing between here and a
    path-traversal bug (e.g. a filename of "../../etc/passwd")."""
    base = os.path.basename(filename)
    cleaned = _SAFE_FILENAME_RE.sub("_", base)
    return cleaned or "upload"


def build_object_key(user_id: str, filename: str) -> str:
    """Namespaces uploads by user so they can't collide or overwrite."""
    return f"uploads/{user_id}/{_sanitize_filename(filename)}"


def _sign(object_key: str, expires_at: int) -> str:
    settings = get_settings()
    message = f"{object_key}:{expires_at}".encode()
    return hmac.new(settings.upload_signing_secret.encode(), message, hashlib.sha256).hexdigest()


def verify_signature(object_key: str, expires_at: int, signature: str) -> bool:
    if time.time() > expires_at:
        return False
    expected = _sign(object_key, expires_at)
    return hmac.compare_digest(expected, signature)


def generate_presigned_put_url(user_id: str, filename: str, content_type: str | None = None) -> tuple[str, str]:
    """Returns (presigned_put_url, object_key) for a direct client -> storage upload.

    content_type isn't used here (unlike the old S3 version) — Apache infers
    the Content-Type it serves back from the file extension when the object
    is later read via build_public_url's URL, same as it does for any other
    static file.
    """
    settings = get_settings()
    key = build_object_key(user_id, filename)
    expires_at = int(time.time()) + settings.presign_expiry_seconds
    signature = _sign(key, expires_at)
    url = f"{settings.public_base_url}/uploads/put/{key}?expires={expires_at}&sig={signature}"
    return url, key


def save_upload(object_key: str, data: bytes) -> None:
    """Writes uploaded bytes to disk at STORAGE_ROOT/{object_key}. Caller
    (the PUT route) must have already verified the signature before calling
    this — this function trusts object_key completely, so it must never be
    passed anything that didn't already go through build_object_key or a
    verified signature."""
    path = os.path.join(STORAGE_ROOT, object_key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def build_public_url(object_key: str) -> str:
    """Constructs the public/readable URL for an object after upload completes."""
    settings = get_settings()
    return f"{settings.public_base_url}/storage/{object_key}"


def download_image_bytes(url: str) -> bytes:
    """Downloads image bytes from a public URL for embedding generation."""
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.content
