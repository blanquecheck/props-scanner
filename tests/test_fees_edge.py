"""QC #4 (fee math: displayed edge is net of fees) and QC #5 (thin-book warning)."""
import pytest

from scanner.edge import Book, FeeInfo, compute_edges
from scanner.fees import kalshi_taker_fee, polymarket_taker_fee


def test_kalshi_fee_matches_published_formula():
    # 0.07 * 100 * 0.5 * 0.5 = 1.75 -> $1.75
    assert kalshi_taker_fee(0.50, 100) == 1.75
    # 0.07 * 1 * 0.5 * 0.5 = 0.0175 -> rounds UP to $0.02
    assert kalshi_taker_fee(0.50, 1) == 0.02
    # 0.07 * 10 * 0.3 * 0.7 = 0.147 -> $0.15
    assert kalshi_taker_fee(0.30, 10) == 0.15
    # exact cent stays exact (no float creep): 0.07*100*0.2*0.8 = 1.12
    assert kalshi_taker_fee(0.20, 100) == 1.12
    assert kalshi_taker_fee(0.50, 0) == 0


def test_polymarket_fee_matches_docs_examples():
    # docs.polymarket.com: 100 shares @ $0.50, sports rate 0.05 -> $1.25; @ $0.30 -> $1.05
    assert polymarket_taker_fee(0.50, 100, 0.05) == 1.25
    assert polymarket_taker_fee(0.30, 100, 0.05) == 1.05
    assert polymarket_taker_fee(0.30, 100, 0.0) == 0


def test_displayed_edge_is_net_of_fees():
    kalshi = Book(yes_bid=0.44, yes_ask=0.45, yes_bid_size=500, yes_ask_size=500)
    poly = Book(yes_bid=0.51, yes_ask=0.53)           # mid 0.52
    edges = compute_edges(kalshi, poly, FeeInfo(poly_rate=0.05), contracts=100)
    e = next(x for x in edges if x.buy_venue == "kalshi" and x.side == "yes")
    fee_per = kalshi_taker_fee(0.45, 100) / 100       # 1.74 / 100 = 0.0174 (0.07*100*.45*.55=1.7325 -> 1.74)
    assert e.fee_per == pytest.approx(fee_per)
    assert e.net_edge == pytest.approx(0.52 - 0.45 - fee_per)
    assert e.net_edge < 0.52 - 0.45                   # strictly less than the gross gap


def test_fee_can_erase_small_gap():
    kalshi = Book(0.49, 0.50, 500, 500)
    poly = Book(0.51, 0.52)                           # mid .515, gross 1.5c, fee ~1.75c
    e = next(x for x in compute_edges(kalshi, poly, FeeInfo()) if x.buy_venue == "kalshi" and x.side == "yes")
    assert e.net_edge < 0


def test_no_side_uses_complement_prices_and_yes_bid_depth():
    kalshi = Book(yes_bid=0.60, yes_ask=0.62, yes_bid_size=7, yes_ask_size=900)
    poly = Book(0.48, 0.50)
    e = next(x for x in compute_edges(kalshi, poly, FeeInfo()) if x.buy_venue == "kalshi" and x.side == "no")
    assert e.ask == pytest.approx(0.40)               # NO ask = 1 - YES bid
    assert e.ask_size == 7                            # depth = size resting on the YES bid
    assert e.ref_price == pytest.approx(0.51)         # Poly NO mid = 1 - 0.49


def test_thin_book_warning():
    kalshi = Book(0.40, 0.42, yes_bid_size=300, yes_ask_size=8)
    poly = Book(0.49, 0.51)
    e = next(x for x in compute_edges(kalshi, poly, FeeInfo(), thin_depth=50)
             if x.buy_venue == "kalshi" and x.side == "yes")
    assert any("THIN BOOK" in w for w in e.warnings)
    deep = next(x for x in compute_edges(Book(0.40, 0.42, 300, 800), poly, FeeInfo(), thin_depth=50)
                if x.buy_venue == "kalshi" and x.side == "yes")
    assert not any("THIN BOOK" in w for w in deep.warnings)


def test_thin_book_fee_rounding_uses_available_size():
    # only 3 contracts available: fee rounding is per order, so per-contract fee is higher
    kalshi = Book(0.40, 0.42, 300, 3)
    e = next(x for x in compute_edges(kalshi, Book(0.49, 0.51), FeeInfo()) if x.buy_venue == "kalshi" and x.side == "yes")
    assert e.fee_per == pytest.approx(kalshi_taker_fee(0.42, 3) / 3)


def test_wide_reference_book_is_not_trusted():
    kalshi = Book(0.40, 0.42, 300, 300)
    poly = Book(0.10, 0.60)                           # 50c spread: mid is meaningless
    assert not [x for x in compute_edges(kalshi, poly, FeeInfo(), max_ref_spread=0.05) if x.buy_venue == "kalshi"]


def test_arb_net_subtracts_both_fees():
    kalshi = Book(0.40, 0.42, 300, 300)
    poly = Book(0.50, 0.52)  # Poly NO ask = 0.50
    e = next(x for x in compute_edges(kalshi, poly, FeeInfo(poly_rate=0.05)) if x.buy_venue == "kalshi" and x.side == "yes")
    pf = polymarket_taker_fee(0.50, 100, 0.05) / 100
    assert e.arb_net == pytest.approx(1 - 0.42 - e.fee_per - 0.50 - pf)
