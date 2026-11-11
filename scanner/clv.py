"""Close snapshots and the personal CLV scoreboard.

CLV for a position bought at `price` on side S = close_mid(S, same venue) - price.
clv_net also subtracts the entry taker fee per contract. Positive = you beat the close.
"""
from __future__ import annotations

import logging
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .db import now_iso
from .venues import Kalshi, Polymarket, _num

log = logging.getLogger(__name__)
MISS_AFTER_MIN = 10


def side_mid(yes_bid: float | None, yes_ask: float | None, side: str) -> float | None:
    if yes_bid is None or yes_ask is None:
        return None
    m = (yes_bid + yes_ask) / 2
    return m if side == "yes" else 1 - m


def clv(entry_price: float, close_mid: float | None, fee_per: float = 0.0) -> tuple[float | None, float | None]:
    if close_mid is None:
        return None, None
    g = round(close_mid - entry_price, 6)
    return g, round(g - fee_per, 6)


def _tracked_matches(conn: sqlite3.Connection):
    return conn.execute("""
        SELECT m.id, k.market_id kticker, p.token_yes, p.start_time, k.id kid, p.id pid
        FROM matches m JOIN prop_contracts k ON k.id=m.kalshi_id JOIN prop_contracts p ON p.id=m.poly_id
        WHERE (EXISTS (SELECT 1 FROM opportunities o WHERE o.match_id=m.id)
               OR EXISTS (SELECT 1 FROM trades_taken t WHERE t.match_id=m.id))
          AND NOT EXISTS (SELECT 1 FROM closes c WHERE c.match_id=m.id)
          AND p.start_time IS NOT NULL""").fetchall()


def _last_quote_before(conn, contract_id: int, before_iso: str):
    return conn.execute("SELECT yes_bid, yes_ask FROM quotes WHERE contract_id=? AND ts<=? ORDER BY ts DESC LIMIT 1",
                        (contract_id, before_iso)).fetchone()


def snapshot_closes(conn: sqlite3.Connection, kalshi: Kalshi, poly: Polymarket, lead_min: float,
                    now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    done = missed = 0
    for m in _tracked_matches(conn):
        start = datetime.fromisoformat(m["start_time"])
        if now < start - timedelta(minutes=lead_min):
            continue
        if now <= start + timedelta(minutes=MISS_AFTER_MIN):
            try:
                km = kalshi.market(m["kticker"])
                kb, ka = _num(km.get("yes_bid_dollars")), _num(km.get("yes_ask_dollars"))
                pb = poly.book(m["token_yes"])
                conn.execute("INSERT OR IGNORE INTO closes (match_id, ts, kalshi_yes_bid, kalshi_yes_ask, "
                             "poly_yes_bid, poly_yes_ask, status) VALUES (?,?,?,?,?,?, 'ok')",
                             (m["id"], now_iso(), kb or None, ka if ka and ka < 1 else None,
                              pb["yes_bid"], pb["yes_ask"]))
                done += 1
            except Exception as e:
                log.warning("close snapshot failed for match %s: %s", m["id"], e)
        else:
            # Too late for a live close: fall back to the last stored quotes before start.
            kq = _last_quote_before(conn, m["kid"], m["start_time"])
            pq = _last_quote_before(conn, m["pid"], m["start_time"])
            conn.execute("INSERT OR IGNORE INTO closes (match_id, ts, kalshi_yes_bid, kalshi_yes_ask, "
                         "poly_yes_bid, poly_yes_ask, status) VALUES (?,?,?,?,?,?, 'last_quote')",
                         (m["id"], now_iso(), kq["yes_bid"] if kq else None, kq["yes_ask"] if kq else None,
                          pq["yes_bid"] if pq else None, pq["yes_ask"] if pq else None))
            missed += 1
    conn.commit()
    return {"closes": done, "fallback": missed}


def _close_mid(row, venue: str, side: str):
    if venue == "kalshi":
        return side_mid(row["kalshi_yes_bid"], row["kalshi_yes_ask"], side)
    return side_mid(row["poly_yes_bid"], row["poly_yes_ask"], side)


def trade_rows(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("""
        SELECT t.*, k.sport, k.stat, k.player_raw, k.line, c.kalshi_yes_bid, c.kalshi_yes_ask,
               c.poly_yes_bid, c.poly_yes_ask, c.status close_status
        FROM trades_taken t JOIN matches m ON m.id=t.match_id JOIN prop_contracts k ON k.id=m.kalshi_id
        LEFT JOIN closes c ON c.match_id=t.match_id ORDER BY t.ts DESC""").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        cm = _close_mid(r, r["venue"], r["side"]) if r["close_status"] else None
        fee_per = (r["fee"] / r["contracts"]) if r["contracts"] else 0
        d["close_mid"] = cm
        d["clv"], d["clv_net"] = clv(r["price"], cm, fee_per)
        out.append(d)
    return out


def flag_rows(conn: sqlite3.Connection) -> list[dict]:
    """First flag per (match, venue, side), with paper 'would have bet' P&L at the close."""
    rows = conn.execute("""
        SELECT o.*, k.sport, k.stat, k.player_raw, k.line, c.kalshi_yes_bid, c.kalshi_yes_ask,
               c.poly_yes_bid, c.poly_yes_ask, c.status close_status
        FROM opportunities o
        JOIN (SELECT MIN(id) id FROM opportunities GROUP BY match_id, buy_venue, side) f ON f.id=o.id
        JOIN matches m ON m.id=o.match_id JOIN prop_contracts k ON k.id=m.kalshi_id
        LEFT JOIN closes c ON c.match_id=o.match_id ORDER BY o.ts DESC""").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        cm = _close_mid(r, r["buy_venue"], r["side"]) if r["close_status"] else None
        d["close_mid"] = cm
        d["clv"], d["clv_net"] = clv(r["ask"], cm, r["fee_per"])
        d["paper_pnl_at_close"] = None if d["clv_net"] is None else round(d["clv_net"] * (r["paper_contracts"] or 0), 2)
        out.append(d)
    return out


def scoreboard(rows: list[dict], venue_key: str = "venue") -> list[dict]:
    """Aggregate CLV by sport / stat / venue. Rows without a close are excluded."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("clv") is None:
            continue
        for dim, val in (("sport", r["sport"]), ("stat", r["stat"]), ("venue", r[venue_key]), ("all", "all")):
            groups[(dim, val)].append(r)
    out = []
    for (dim, val), rs in sorted(groups.items()):
        n = len(rs)
        out.append({"dim": dim, "value": val, "n": n,
                    "avg_clv": round(sum(r["clv"] for r in rs) / n, 6),
                    "avg_clv_net": round(sum(r["clv_net"] for r in rs) / n, 6),
                    "pct_positive": round(sum(r["clv_net"] > 0 for r in rs) / n, 4)})
    return out
