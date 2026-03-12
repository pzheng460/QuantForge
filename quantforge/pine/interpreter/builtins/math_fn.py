"""math.* built-in functions for Pine Script."""

from __future__ import annotations

import math


def pine_abs(x):
    if x is None:
        return None
    return abs(x)


def pine_max(a, b):
    if a is None or b is None:
        return None
    return max(a, b)


def pine_min(a, b):
    if a is None or b is None:
        return None
    return min(a, b)


def pine_round(x, precision=0):
    if x is None:
        return None
    if precision == 0:
        return round(x)
    return round(x, precision)


def pine_log(x):
    if x is None or x <= 0:
        return None
    return math.log(x)


def pine_sqrt(x):
    if x is None or x < 0:
        return None
    return math.sqrt(x)


def pine_pow(base, exp):
    if base is None or exp is None:
        return None
    return base**exp


def pine_ceil(x):
    if x is None:
        return None
    return math.ceil(x)


def pine_floor(x):
    if x is None:
        return None
    return math.floor(x)


def pine_sign(x):
    if x is None:
        return None
    if x > 0:
        return 1
    if x < 0:
        return -1
    return 0


MATH_FUNCTIONS = {
    "abs": pine_abs,
    "max": pine_max,
    "min": pine_min,
    "round": pine_round,
    "log": pine_log,
    "sqrt": pine_sqrt,
    "pow": pine_pow,
    "ceil": pine_ceil,
    "floor": pine_floor,
    "sign": pine_sign,
}
