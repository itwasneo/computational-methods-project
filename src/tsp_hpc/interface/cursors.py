"""Private signed cursor encoding; callers own context and pagination semantics."""

import base64
import hmac
import json


def encode_cursor(key: bytes, payload: list) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")
    return base64.urlsafe_b64encode(raw + hmac.digest(key, raw, "sha256")).decode("ascii")


def decode_cursor(key: bytes, cursor: str) -> list:
    if type(cursor) is not str or len(cursor) > 512:
        raise ValueError("invalid cursor")
    raw = base64.b64decode(cursor, altchars=b"-_", validate=True)
    payload, signature = raw[:-32], raw[-32:]
    if not hmac.compare_digest(signature, hmac.digest(key, payload, "sha256")):
        raise ValueError("invalid cursor")
    value = json.loads(payload)
    if type(value) is not list:
        raise ValueError("invalid cursor")
    return value
