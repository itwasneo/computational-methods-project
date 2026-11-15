"""Bounded callers' strict UTF-8 JSON parsing; no scientific normalization."""

import json
import math


def decode_json(raw, *, max_depth=64):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate JSON field")
            value[key] = item
        return value

    def invalid(_):
        raise ValueError("nonfinite JSON number")

    def finite(text):
        value = float(text)
        if not math.isfinite(value):
            invalid(text)
        return value

    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="strict")
    value = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid, parse_float=finite)
    pending = [(value, 1)]
    while pending:
        item, depth = pending.pop()
        if depth > max_depth:
            raise ValueError("JSON nesting exceeds its bound")
        if type(item) is str:
            item.encode("utf-8", errors="strict")  # Reject escaped unpaired surrogates too.
        elif type(item) is dict:
            pending.extend((key, depth + 1) for key in item)
            pending.extend((child, depth + 1) for child in item.values())
        elif type(item) is list:
            pending.extend((child, depth + 1) for child in item)
    return value
