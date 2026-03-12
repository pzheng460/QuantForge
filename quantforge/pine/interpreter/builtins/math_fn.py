"""Math builtins: math.abs, math.max, math.min, math.round, math.log, math.sqrt, math.pow."""

from __future__ import annotations

import math as _math

from quantforge.pine.interpreter.series import is_na


def math_abs(x):
    if is_na(x):
        return None
    return abs(x)


def math_max(*args):
    vals = [v for v in args if not is_na(v)]
    if not vals:
        return None
    return max(vals)


def math_min(*args):
    vals = [v for v in args if not is_na(v)]
    if not vals:
        return None
    return min(vals)


def math_round(x, precision=0):
    if is_na(x):
        return None
    return round(x, int(precision))


def math_log(x):
    if is_na(x) or x <= 0:
        return None
    return _math.log(x)


def math_sqrt(x):
    if is_na(x) or x < 0:
        return None
    return _math.sqrt(x)


def math_pow(base, exp):
    if is_na(base) or is_na(exp):
        return None
    return base**exp


def math_ceil(x):
    if is_na(x):
        return None
    return _math.ceil(x)


def math_floor(x):
    if is_na(x):
        return None
    return _math.floor(x)


def math_sign(x):
    if is_na(x):
        return None
    if x > 0:
        return 1
    elif x < 0:
        return -1
    return 0
