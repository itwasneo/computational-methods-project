"""Lossless bounded-interface projection, not a scientific value normalizer."""

import math

from tsp_hpc.interface.contracts import SAFE_INTEGER
from tsp_hpc.interface.errors import InterfaceError


def wire_value(value):
    """Missing observations become null; exact large integers become strings.

    Callers explicitly stringify ALL seeds, including small ones. Nonfinite
    infinities are errors, not an invented missing/zero observation.
    """
    import numpy as np
    import pandas as pd
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, np.generic):
        value = value.item()
    if type(value) is str:
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise InterfaceError("invalid_artifact", "Observation text is not valid Unicode.", 422) from None
        return value
    if type(value) is bool:
        return value
    if type(value) is int:
        return value if -SAFE_INTEGER <= value <= SAFE_INTEGER else str(value)
    if type(value) is float:
        if math.isnan(value):
            return None
        if not math.isfinite(value):
            raise InterfaceError("invalid_artifact", "Observation exceeds finite wire range.", 422)
        return value
    if type(value) in (list, tuple):
        return [wire_value(item) for item in value]
    if type(value) is dict and all(type(key) is str for key in value):
        return {wire_value(key): wire_value(item) for key, item in value.items()}
    raise InterfaceError("invalid_artifact", "Observation has an unsupported wire type.", 422)
