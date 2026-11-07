"""Compiled two-coordinate adaptation of CPython 3.12.3's vector_norm.

Adapted from Modules/mathmodule.c (vector_norm, dl_mul, dl_fast_sum).
Copyright (c) 2001-2023 Python Software Foundation; All Rights Reserved.
See licenses/CPython.txt for the retained license and copyright notice.

Changes: Python/Numba implementation for two finite scalar inputs; subnormal
recursion replaced by one explicit rescaling step; C fma expressed as an LLVM
intrinsic. No Python callbacks, coordinate vectors, or dynamic allocations.
The compensated arithmetic order is retained; fastmath must remain disabled.
"""

import math

from numba import njit, types
from numba.extending import intrinsic


@intrinsic
def _fma(typing_context, x, y, z):
    """An explicitly fused float64 multiply-add, not blanket fastmath."""
    if (x, y, z) != (types.float64, types.float64, types.float64):
        raise TypeError("compensated norm requires float64 operands")
    signature = types.float64(x, y, z)

    def codegen(context, builder, sig, args):
        function = builder.module.declare_intrinsic(
            "llvm.fma", [context.get_value_type(sig.return_type)] * 3
        )
        return builder.call(function, args)

    return signature, codegen


@njit(cache=True, fastmath=False)
def hypot_2d(x, y):
    """CPython-style compensated norm for finite float64 components."""
    x = abs(x)
    y = abs(y)
    maximum = max(x, y)
    if maximum == 0.0:
        return 0.0
    _, exponent = math.frexp(maximum)
    minimum_normal = 2.2250738585072014e-308
    subnormal = exponent < -1023
    if subnormal:
        x /= minimum_normal
        y /= minimum_normal
        _, exponent = math.frexp(maximum / minimum_normal)
    scale = math.ldexp(1.0, -exponent)
    total = 1.0
    product_errors = 0.0
    sum_errors = 0.0
    for component in (x, y):
        scaled = component * scale
        square = scaled * scaled
        product_error = _fma(scaled, scaled, -square)
        combined = total + square
        sum_error = (total - combined) + square
        total = combined
        product_errors += product_error
        sum_errors += sum_error
    root = math.sqrt(total - 1.0 + (product_errors + sum_errors))
    negative_square = -root * root
    product_error = _fma(-root, root, -negative_square)
    combined = total + negative_square
    sum_error = (total - combined) + negative_square
    total = combined
    product_errors += product_error
    sum_errors += sum_error
    residual = total - 1.0 + (product_errors + sum_errors)
    root += residual / (2.0 * root)
    result = root / scale
    return minimum_normal * result if subnormal else result
