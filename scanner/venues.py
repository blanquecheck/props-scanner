"""Read-only venue adapters. Only public market-data GET endpoints are used:

Kalshi:      GET /series/{ticker}, GET /markets
Polymarket:  GET gamma /events, GET clob /book
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .config import KALSHI_BASE, KALSHI_SERIES, POLY_CLOB_BASE, POLY_GAMMA_BASE, POLY_SERIES, POLY_STAT_TYPES
from .http import ReadOnlyClient
from .normalize import norm_name, parse_kalshi_event, parse_kalshi_title, parse_poly_event, parse_poly_question

log = logging.getLogger(__name__)


@dataclass
class Contract:
    venue: str
    market_id: str
    event_id: str
    sport: str
    game_key: str
    player_raw: str
    player_ext_id: str | None
    stat: str
    line: float
    title: str
    url: str
    start_time: str | None
    yes_bid: float | None
    yes_ask: float | None
    yes_bid_size: float | None
    yes_ask_size: float | None
    token_yes: str | None = None
    token_no: str | None = None
    fee_rate: float | None = None
    fee_exponent: float | None = None
    fee_mult: float | None = None
    event_title: str | None = None   # e.g. "Wizards vs. Pacers" (Polymarket)

    @property
    def player_norm(self) -> str:
        return norm_name(self.player_raw)


def _num(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


# ---------------------------------------------------------------- Kalshi

class Kalshi:
    def __init__(self, client: ReadOnlyClient | None = None):
        self.c = client or ReadOnlyClient(KALSHI_BASE, min_interval=0.1)
        self._series: dict[str, dict] = {}

    def series(self, ticker: str) -> dict:
        if ticker not in self._series:
            self._series[ticker] = self.c.get(f"/series/{ticker}").get("series", {})
        return self._series[ticker]

    def open_markets(self, series_ticker: str):
        cursor = None
        while True:
            params = {"series_ticker": series_ticker, "status": "open", "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            d = self.c.get("/markets", params)
            yield from d.get("markets", [])
            cursor = d.get("cursor")
            if not cursor:
                break

    def market(self, ticker: str) -> dict:
        return self.c.get(f"/markets/{ticker}").get("market", {})

    def contracts(self, series_filter: set[str] | None = None) -> tuple[list[Contract], dict]:
        out: list[Contract] = []
        stats = {"seen": 0, "parsed": 0, "skipped": 0}
        for st, (sport, stat) in KALSHI_SERIES.items():
            if series_filter and sport not in series_filter:
                continue
            try:
                meta = self.series(st)
            except Exception as e:  # missing series etc.
                log.warning("kalshi series %s unavailable: %s", st, e)
                continue
            mult = _num(meta.get("fee_multiplier")) or 1.0
            if meta.get("fee_type") not in (None, "quadratic", "quadratic_with_maker_fees"):
                log.warning("kalshi series %s has fee_type=%s; assuming quadratic", st, meta.get("fee_type"))
            title_slug = _slug(meta.get("title") or st)
            for m in self.open_markets(st):
                stats["seen"] += 1
                c = parse_kalshi_market(m, sport, stat, mult, st, title_slug)
                if c:
                    out.append(c)
                    stats["parsed"] += 1
                else:
                    stats["skipped"] += 1
        return out, stats


def kalshi_url(series_ticker: str, title_slug: str, event_ticker: str) -> str:
    # Format used by kalshi.com market pages (not verifiable from here; Kalshi's site rate-limits bots).
    return f"https://kalshi.com/markets/{series_ticker.lower()}/{title_slug}/{event_ticker.lower()}"


def parse_kalshi_market(m: dict, sport: str, stat: str, fee_mult: float = 1.0,
                        series_ticker: str = "", title_slug: str = "") -> Contract | None:
    parsed = parse_kalshi_title(m.get("title", ""), m.get("floor_strike"), m.get("strike_type"))
    if not parsed:
        return None
    gk = parse_kalshi_event(sport, m.get("event_ticker", ""))
    if not gk:
        return None
    name, line = parsed
    cs = m.get("custom_strike") or {}
    ext = cs.get("basketball_player") or cs.get("football_player")
    yes_bid = _num(m.get("yes_bid_dollars"))
    yes_ask = _num(m.get("yes_ask_dollars"))
    # Kalshi quotes 0 when a side is empty; treat as missing.
    yes_bid = yes_bid if yes_bid and yes_bid > 0 else None
    yes_ask = yes_ask if yes_ask and yes_ask < 1 else None
    return Contract(
        venue="kalshi", market_id=m["ticker"], event_id=m["event_ticker"], sport=sport, game_key=gk,
        player_raw=name, player_ext_id=ext, stat=stat, line=line, title=m.get("title", ""),
        url=kalshi_url(series_ticker or m["event_ticker"].split("-")[0], title_slug or "market", m["event_ticker"]),
        start_time=None,  # Kalshi doesn't expose tip time on the market; taken from the matched Poly game
        yes_bid=yes_bid, yes_ask=yes_ask,
        yes_bid_size=_num(m.get("yes_bid_size_fp")), yes_ask_size=_num(m.get("yes_ask_size_fp")),
        fee_mult=fee_mult,
    )


# ---------------------------------------------------------------- Polymarket

class Polymarket:
    def __init__(self, gamma: ReadOnlyClient | None = None, clob: ReadOnlyClient | None = None):
        self.gamma = gamma or ReadOnlyClient(POLY_GAMMA_BASE, min_interval=0.1)
        self.clob = clob or ReadOnlyClient(POLY_CLOB_BASE, min_interval=0.05)

    def game_events(self, sport: str):
        offset = 0
        while True:
            d = self.gamma.get("/events", {"series_id": POLY_SERIES[sport], "active": "true",
                                           "closed": "false", "limit": 100, "offset": offset})
            if not d:
                break
            yield from d
            if len(d) < 100:
                break
            offset += 100

    def book(self, token_id: str) -> dict:
        """Best bid/ask and sizes from the public CLOB book for one outcome token."""
        b = self.clob.get("/book", {"token_id": token_id})
        bids = [(float(x["price"]), float(x["size"])) for x in b.get("bids", [])]
        asks = [(float(x["price"]), float(x["size"])) for x in b.get("asks", [])]
        bb = max(bids, default=None)
        ba = min(asks, default=None)
        return {"yes_bid": bb[0] if bb else None, "yes_bid_size": bb[1] if bb else None,
                "yes_ask": ba[0] if ba else None, "yes_ask_size": ba[1] if ba else None}

    def contracts(self, sports: list[str], hours_ahead: float) -> tuple[list[Contract], dict]:
        out: list[Contract] = []
        stats = {"events": 0, "seen": 0, "parsed": 0, "skipped": 0, "unknown_types": {}}
        now = datetime.now(timezone.utc)
        horizon = now + timedelta(hours=hours_ahead)
        for sport in sports:
            for e in self.game_events(sport):
                gk = parse_poly_event(e.get("slug", ""))
                start = _parse_ts(e.get("startTime"))
                if not gk or not start or start <= now or start > horizon:
                    continue
                stats["events"] += 1
                for m in e.get("markets", []):
                    smt = m.get("sportsMarketType")
                    if smt in ("moneyline", "spreads", "totals") or not smt:
                        continue
                    stats["seen"] += 1
                    if smt not in POLY_STAT_TYPES:
                        stats["unknown_types"][smt] = stats["unknown_types"].get(smt, 0) + 1
                        stats["skipped"] += 1
                        continue
                    c = parse_poly_market(m, e, sport, gk, start)
                    if c:
                        out.append(c)
                        stats["parsed"] += 1
                    else:
                        stats["skipped"] += 1
        return out, stats


def _parse_ts(s) -> datetime | None:
    if not s:
        return None
    try:
        s = str(s).replace(" ", "T")
        if s.endswith("+00"):
            s += ":00"
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_poly_market(m: dict, e: dict, sport: str, gk: str, start: datetime | None) -> Contract | None:
    stat = POLY_STAT_TYPES.get(m.get("sportsMarketType"))
    if not stat or not m.get("active", True) or m.get("closed") or not m.get("acceptingOrders", True):
        return None
    try:
        outcomes = json.loads(m.get("outcomes") or "[]")
        tokens = json.loads(m.get("clobTokenIds") or "[]")
    except json.JSONDecodeError:
        return None
    # First outcome must be the Over side ("Yes" on player props).
    if len(outcomes) != 2 or len(tokens) != 2 or outcomes[0].lower() not in ("yes", "over"):
        return None
    parsed = parse_poly_question(m.get("question", ""), m.get("line"))
    if not parsed:
        return None
    name, line = parsed
    fs = m.get("feeSchedule") or {}
    fee_rate = float(fs.get("rate", 0)) if m.get("feesEnabled") else 0.0
    fee_exp = float(fs.get("exponent", 1)) if m.get("feesEnabled") else 1.0
    gst = _parse_ts(m.get("gameStartTime")) or start
    return Contract(
        venue="polymarket", market_id=m.get("slug") or m.get("conditionId"), event_id=e.get("slug", ""),
        sport=sport, game_key=gk, player_raw=name, player_ext_id=None, stat=stat, line=line,
        title=m.get("question", ""), url=f"https://polymarket.com/event/{e.get('slug', '')}",
        start_time=gst.isoformat() if gst else None,
        yes_bid=_num(m.get("bestBid")), yes_ask=_num(m.get("bestAsk")),
        yes_bid_size=None, yes_ask_size=None,
        token_yes=tokens[0], token_no=tokens[1], fee_rate=fee_rate, fee_exponent=fee_exp,
        event_title=e.get("title"),
    )
