"""Stateless logical stream planning and explicitly owned PCG64 generators."""

from dataclasses import dataclass
import re

import numpy as np

from tsp_hpc.identity import stable_id

SEED_SCHEME = "sha256-uint64-v1"
BIT_GENERATOR = "numpy-pcg64"


def validate_seed(seed: int) -> None:
    """Require the shared solver contract's Python uint64-range integer seed."""
    if type(seed) is not int:
        raise TypeError("seed must be a Python integer, not a boolean or NumPy scalar")
    if not 0 <= seed < 2**64:
        raise ValueError("seed must lie in [0, 2**64)")


@dataclass(frozen=True)
class RandomStream:
    """A planned stream, not mutable RNG state. Obtain state with generator()."""

    stream_id: str
    seed: int

    def __post_init__(self) -> None:
        validate_seed(self.seed)
        if not isinstance(self.stream_id, str):
            raise TypeError("stream_id must be a string")
        if re.fullmatch(r"stream-v1-[0-9a-f]{64}", self.stream_id) is None:
            raise ValueError("invalid v1 stream_id")
        if int(self.stream_id.removeprefix("stream-v1-")[:16], 16) != self.seed:
            raise ValueError("seed does not match the stream ID")

    def generator(self) -> np.random.Generator:
        """Create fresh owned state at the beginning of this stream, never resume it."""
        return np.random.Generator(np.random.PCG64(self.seed))


def derive_stream(root_seed: int, role: str, *logical_ids: str | int) -> RandomStream:
    """Derive a stream from a framed role/ID path without consuming any RNG.

    ID tokens must be non-blank strings or non-negative Python integers. Worker,
    thread, rank and scheduling identifiers must never be used as logical IDs.
    Full SHA-256 stream IDs are retained; the first 64 digest bits seed PCG64.
    Hash-derived seeds do not prove non-overlapping or independent sequences.
    """
    validate_seed(root_seed)
    if not isinstance(role, str):
        raise TypeError("stream role must be a string")
    if not role.strip():
        raise ValueError("stream role must not be blank")
    for token in logical_ids:
        if type(token) not in (str, int):
            raise TypeError("logical IDs must be strings or Python integers, not booleans")
        if (isinstance(token, str) and not token.strip()) or (type(token) is int and token < 0):
            raise ValueError("logical IDs must be non-blank strings or non-negative integers")
    stream_id = stable_id("stream", {
        "seed_scheme": SEED_SCHEME,
        "root_seed": root_seed,
        "role": role,
        "logical_ids": list(logical_ids),
    })
    digest = stream_id.removeprefix("stream-v1-")
    return RandomStream(stream_id, int(digest[:16], 16))
