"""Edge after fees for one matched Kalshi/Polymarket pair.

For each side (yes = over, no = under) and each buy venue:
    net_edge = ref_mid(other venue, same side) - ask(buy venue) - taker_fee_per_contract(buy venue)
The reference mid is only trusted when the other venue's spread is <= max_ref_spread.

arb_net is the locked profit per contract from buying this side on the buy venue and the
opposite side on the other venue, after both taker fees (positive = true arb).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .fees import fee_per_contract


@dataclass
class Book:
    """Quote for the YES (over) outcome on one venue."""
    yes_bid: float | None
    yes_ask: float | None
    yes_bid_size: float | None = None
    yes_ask_size: float | None = None

    def side(self, side: str) -> tuple[float | None, float | None, float | None]:
        """(bid, ask, ask_size) for side 'yes' or 'no'. NO ask = 1 - YES bid, with YES-bid size."""
        if side == "yes":
            return self.yes_bid, self.yes_ask, self.yes_ask_size
        bid = None if self.yes_ask is None else round(1 - self.yes_ask, 6)
        ask = None if self.yes_bid is None else round(1 - self.yes_bid, 6)
        return bid, ask, self.yes_bid_size

    def mid(self, side: str) -> float | None:
        bid, ask, _ = self.side(side)
        if bid is None or ask is None:
            return None
        return (bid + ask) / 2

    def spread(self) -> float | None:
        if self.yes_bid is None or self.yes_ask is None:
            return None
        return round(self.yes_ask - self.yes_bid, 6)


@dataclass
class FeeInfo:
    kalshi_mult: float = 1.0
    poly_rate: float = 0.0
    poly_exp: float = 1.0


@dataclass
class Edge:
    buy_venue: str
    side: str
    ask: float
    ask_size: float | None
    fee_per: float
    ref_price: float
    ref_spread: float | None
    buy_spread: float | None
    net_edge: float
    arb_net: float | None
    warnings: list[str] = field(default_factory=list)


def _fee(venue: str, price: float, n: float, f: FeeInfo) -> float:
    return fee_per_contract(venue, price, n, kalshi_mult=f.kalshi_mult, poly_rate=f.poly_rate, poly_exp=f.poly_exp)


def compute_edges(kalshi: Book, poly: Book, fees: FeeInfo, *, contracts: int = 100,
                  max_ref_spread: float = 0.05, thin_depth: float = 50,
                  wide_spread: float = 0.05) -> list[Edge]:
    books = {"kalshi": kalshi, "polymarket": poly}
    out: list[Edge] = []
    for buy_venue, ref_venue in (("kalshi", "polymarket"), ("polymarket", "kalshi")):
        buy, ref = books[buy_venue], books[ref_venue]
        ref_spread = ref.spread()
        if ref_spread is None or ref_spread > max_ref_spread:
            continue  # no trustworthy fair price on the other venue
        for side in ("yes", "no"):
            _, ask, ask_size = buy.side(side)
            ref_mid = ref.mid(side)
            if ask is None or ref_mid is None or not (0 < ask < 1):
                continue
            n = min(contracts, ask_size) if ask_size else contracts
            fee = _fee(buy_venue, ask, n, fees)
            net = round(ref_mid - ask - fee, 6)

            # locked arb: this side here + the other side on the other venue
            opp = "no" if side == "yes" else "yes"
            _, opp_ask, opp_size = ref.side(opp)
            arb = None
            if opp_ask is not None and 0 < opp_ask < 1:
                n2 = min(n, opp_size) if opp_size else n
                arb = round(1 - ask - fee - opp_ask - _fee(ref_venue, opp_ask, n2, fees), 6)

            warns: list[str] = []
            if ask_size is None:
                warns.append("depth unknown")
            elif ask_size < thin_depth:
                warns.append(f"THIN BOOK: only {ask_size:g} at ask")
            bs = buy.spread()
            if bs is not None and bs > wide_spread:
                warns.append(f"wide spread {bs * 100:.0f}c on {buy_venue}")
            if ref_spread > wide_spread:
                warns.append(f"wide ref spread {ref_spread * 100:.0f}c on {ref_venue}")
            out.append(Edge(buy_venue, side, ask, ask_size, round(fee, 6), round(ref_mid, 6),
                            ref_spread, bs, net, arb, warns))
    return sorted(out, key=lambda e: e.net_edge, reverse=True)
