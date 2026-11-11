"""Price <-> American odds, for readers who bet on sportsbooks (FanDuel, Fanatics, ...).

A prediction-market price in dollars is an implied probability: 45c = 45%.
American odds for that probability (no vig):
    p >= 0.5  ->  -(100 * p / (1 - p))     e.g. 0.60 -> -150
    p <  0.5  ->  +(100 * (1 - p) / p)     e.g. 0.45 -> +122
"""
from __future__ import annotations


def american(p: float) -> int:
    if not 0 < p < 1:
        raise ValueError("probability must be between 0 and 1")
    if p >= 0.5:
        return -round(100 * p / (1 - p))
    return round(100 * (1 - p) / p)


def fmt(odds: int) -> str:
    """-108 -> '−108', 122 -> '+122', -100 -> '+100' (even money)."""
    if odds in (-100, 100):
        return "+100"
    return f"+{odds}" if odds > 0 else f"−{-odds}"


def fmt_p(p: float) -> str:
    return fmt(american(p))


def next_better(odds: int) -> int:
    """The next 'round' price (multiple of 5) that pays MORE than `odds`.
    -108 -> -105, -105 -> +100, -100/+100 -> +105, +122 -> +125, +125 -> +130."""
    if odds in (-100, 100):
        return 105
    if odds < 0:
        v = (-odds - 1) // 5 * 5
        return 100 if v <= 100 else -v
    return (odds // 5 + 1) * 5
