import csv
import json
from pathlib import Path

import pytest

from scanner.normalize import parse_poly_event
from scanner.venues import parse_kalshi_market, parse_poly_market

HERE = Path(__file__).parent


def kalshi_contract(sport, stat, event, title, floor, yes_bid=0.50, yes_ask=0.52, bid_size=200, ask_size=200,
                    strike_type="greater", ticker=None):
    m = {"ticker": ticker or f"{event}-{abs(hash((title, floor))) % 10**8}", "event_ticker": event,
         "title": title, "floor_strike": float(floor), "strike_type": strike_type,
         "yes_bid_dollars": f"{yes_bid:.4f}", "yes_ask_dollars": f"{yes_ask:.4f}",
         "yes_bid_size_fp": str(bid_size), "yes_ask_size_fp": str(ask_size), "custom_strike": {}}
    return parse_kalshi_market(m, sport, stat, 1.0, event.split("-")[0], "x")


def poly_contract(sport, stat_type, event_slug, question, line, yes_bid=0.50, yes_ask=0.52, start="2099-01-01T00:00:00Z"):
    m = {"question": question, "line": float(line), "sportsMarketType": stat_type, "active": True, "closed": False,
         "acceptingOrders": True, "outcomes": json.dumps(["Yes", "No"]), "clobTokenIds": json.dumps(["1", "2"]),
         "slug": f"{event_slug}-{abs(hash((question, line))) % 10**8}", "bestBid": yes_bid, "bestAsk": yes_ask,
         "feesEnabled": True, "feeSchedule": {"rate": 0.05, "exponent": 1}, "gameStartTime": None}
    e = {"slug": event_slug}
    from scanner.venues import _parse_ts
    return parse_poly_market(m, e, sport, parse_poly_event(event_slug), _parse_ts(start))


@pytest.fixture
def real_pairs():
    """22 real Kalshi/Polymarket pairs captured live on 2026-11-11."""
    with open(HERE / "fixtures_pairs.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        k = kalshi_contract(r["sport"], r["stat"], r["kalshi_event"], r["kalshi_title"], r["kalshi_floor"])
        poly_type = "threes" if r["stat"] == "threes" else r["stat"]
        p = poly_contract(r["sport"], poly_type, r["poly_event"], r["poly_question"], r["poly_line"])
        out.append((k, p))
    return out
