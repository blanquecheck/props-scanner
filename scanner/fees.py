"""Taker fee math. All prices are dollars per contract (0-1)."""
from __future__ import annotations

import math


def kalshi_taker_fee(price: float, contracts: float, multiplier: float = 1.0, rate: float = 0.07) -> float:
    """Kalshi quadratic taker fee for one order, in dollars.

    fee = ceil_to_cent(rate * multiplier * C * P * (1 - P))
    Rounding is per order, so the per-contract fee is higher on small orders.
    """
    if contracts <= 0:
        return 0.0
    raw = rate * multiplier * contracts * price * (1 - price)
    return math.ceil(round(raw * 100, 9)) / 100


def polymarket_taker_fee(price: float, shares: float, rate: float, exponent: float = 1.0) -> float:
    """Polymarket taker fee in USDC: shares * rate * (p * (1 - p)) ** exponent, rounded to 5 dp.

    Sports markets seen at build time: rate=0.05, exponent=1 (feeSchedule on each market).
    """
    if shares <= 0 or not rate:
        return 0.0
    fee = shares * rate * (price * (1 - price)) ** exponent
    return round(fee, 5)


def fee_per_contract(venue: str, price: float, contracts: float, *, kalshi_mult: float = 1.0,
                     poly_rate: float = 0.0, poly_exp: float = 1.0) -> float:
    n = max(contracts, 1)
    if venue == "kalshi":
        return kalshi_taker_fee(price, n, kalshi_mult) / n
    return polymarket_taker_fee(price, n, poly_rate, poly_exp) / n
