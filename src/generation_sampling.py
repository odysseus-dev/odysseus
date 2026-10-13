"""Validation shared by direct native generation paths."""
import math


def validate_temperature(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('temperature must be a finite nonnegative number')
    return float(value)
