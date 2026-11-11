"""SQLite persistence helpers."""
from __future__ import annotations

import sqlite3

from .db import now_iso
from .venues import Contract


def upsert_contract(conn: sqlite3.Connection, c: Contract) -> int:
    ts = now_iso()
    conn.execute(
        """INSERT INTO prop_contracts (venue, market_id, event_id, sport, game_key, player_raw, player_norm,
               player_ext_id, stat, line, title, url, start_time, token_yes, token_no, fee_rate, fee_exponent,
               fee_mult, first_seen, last_seen)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(venue, market_id) DO UPDATE SET
               title=excluded.title, url=excluded.url,
               start_time=COALESCE(excluded.start_time, prop_contracts.start_time),
               fee_rate=excluded.fee_rate, fee_exponent=excluded.fee_exponent, fee_mult=excluded.fee_mult,
               last_seen=excluded.last_seen""",
        (c.venue, c.market_id, c.event_id, c.sport, c.game_key, c.player_raw, c.player_norm, c.player_ext_id,
         c.stat, c.line, c.title, c.url, c.start_time, c.token_yes, c.token_no, c.fee_rate, c.fee_exponent,
         c.fee_mult, ts, ts),
    )
    return conn.execute("SELECT id FROM prop_contracts WHERE venue=? AND market_id=?",
                        (c.venue, c.market_id)).fetchone()["id"]


def insert_quote(conn: sqlite3.Connection, contract_id: int, bid, ask, bid_size, ask_size, source: str) -> bool:
    """Store a quote only if it differs from the latest one for this contract (keeps the DB small)."""
    last = conn.execute("SELECT yes_bid, yes_ask, yes_bid_size, yes_ask_size FROM quotes WHERE contract_id=? "
                        "ORDER BY id DESC LIMIT 1", (contract_id,)).fetchone()
    if last and tuple(last) == (bid, ask, bid_size, ask_size):
        return False
    conn.execute("INSERT INTO quotes (contract_id, ts, yes_bid, yes_ask, yes_bid_size, yes_ask_size, source) "
                 "VALUES (?,?,?,?,?,?,?)", (contract_id, now_iso(), bid, ask, bid_size, ask_size, source))
    return True


def prune(conn: sqlite3.Connection, keep_days: float = 3) -> dict:
    """Drop data for games that ended > keep_days ago, unless it backs a flag or a trade.
    Keeps the SQLite file small enough to round-trip through git on GitHub Actions."""
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).replace(microsecond=0).isoformat()
    tracked = """SELECT kalshi_id FROM matches WHERE id IN (SELECT match_id FROM opportunities UNION SELECT match_id FROM trades_taken)
                 UNION SELECT poly_id FROM matches WHERE id IN (SELECT match_id FROM opportunities UNION SELECT match_id FROM trades_taken)"""
    q = conn.execute(f"DELETE FROM quotes WHERE ts < ? AND contract_id NOT IN ({tracked})", (cutoff,)).rowcount
    m = conn.execute("""DELETE FROM matches WHERE updated_at < ? AND status IN ('auto','review')
                        AND id NOT IN (SELECT match_id FROM opportunities UNION SELECT match_id FROM trades_taken)""",
                     (cutoff,)).rowcount
    c = conn.execute("""DELETE FROM prop_contracts WHERE last_seen < ?
                        AND id NOT IN (SELECT kalshi_id FROM matches UNION SELECT poly_id FROM matches)
                        AND id NOT IN (SELECT contract_id FROM quotes)""", (cutoff,)).rowcount
    conn.commit()
    if q or m or c:
        conn.execute("VACUUM")
    return {"quotes": q, "matches": m, "contracts": c}


def log_trade(conn: sqlite3.Connection, opportunity_id: int, price: float, contracts: float,
              note: str = "") -> sqlite3.Row | None:
    """Record a trade you placed by hand against a flagged opportunity. Returns the opportunity row."""
    from .fees import kalshi_taker_fee, polymarket_taker_fee
    o = conn.execute("""SELECT o.*, k.fee_mult, p.fee_rate, p.fee_exponent, k.player_raw, k.stat, k.line
                        FROM opportunities o JOIN matches m ON m.id=o.match_id
                        JOIN prop_contracts k ON k.id=m.kalshi_id JOIN prop_contracts p ON p.id=m.poly_id
                        WHERE o.id=?""", (opportunity_id,)).fetchone()
    if not o or not (0 < price < 1) or contracts <= 0:
        return None
    fee = (kalshi_taker_fee(price, contracts, o["fee_mult"] or 1.0) if o["buy_venue"] == "kalshi"
           else polymarket_taker_fee(price, contracts, o["fee_rate"] or 0.0, o["fee_exponent"] or 1.0))
    conn.execute("INSERT INTO trades_taken (opportunity_id, match_id, venue, side, price, contracts, fee, ts, note) "
                 "VALUES (?,?,?,?,?,?,?,?,?)", (o["id"], o["match_id"], o["buy_venue"], o["side"], price,
                                                 contracts, fee, now_iso(), note))
    conn.commit()
    return o


def upsert_match(conn: sqlite3.Connection, kalshi_id: int, poly_id: int, confidence: float,
                 method: str, status: str) -> sqlite3.Row:
    """Insert or refresh a match. Your decisions (approved / rejected) are never overwritten."""
    ts = now_iso()
    row = conn.execute("SELECT * FROM matches WHERE kalshi_id=? AND poly_id=?", (kalshi_id, poly_id)).fetchone()
    if row is None:
        conn.execute("INSERT INTO matches (kalshi_id, poly_id, confidence, method, status, created_at, updated_at) "
                     "VALUES (?,?,?,?,?,?,?)", (kalshi_id, poly_id, confidence, method, status, ts, ts))
    elif row["status"] not in ("approved", "rejected"):
        conn.execute("UPDATE matches SET confidence=?, method=?, status=?, updated_at=? WHERE id=?",
                     (confidence, method, status, ts, row["id"]))
    return conn.execute("SELECT * FROM matches WHERE kalshi_id=? AND poly_id=?", (kalshi_id, poly_id)).fetchone()


def rejected_pairs(conn: sqlite3.Connection) -> set[tuple[str, str]]:
    rows = conn.execute("""SELECT k.market_id km, p.market_id pm FROM matches m
                           JOIN prop_contracts k ON k.id=m.kalshi_id JOIN prop_contracts p ON p.id=m.poly_id
                           WHERE m.status='rejected'""").fetchall()
    return {(r["km"], r["pm"]) for r in rows}


def db_aliases(conn: sqlite3.Connection) -> set[tuple[str, str, str]]:
    return {(r["sport"], r["kalshi_norm"], r["poly_norm"]) for r in conn.execute("SELECT * FROM aliases")}


def set_match_status(conn: sqlite3.Connection, match_id: int, status: str) -> None:
    assert status in ("approved", "rejected")
    m = conn.execute("""SELECT m.id, k.sport, k.player_norm kn, p.player_norm pn FROM matches m
                        JOIN prop_contracts k ON k.id=m.kalshi_id JOIN prop_contracts p ON p.id=m.poly_id
                        WHERE m.id=?""", (match_id,)).fetchone()
    if not m:
        return
    conf = 1.0 if status == "approved" else 0.0
    conn.execute("UPDATE matches SET status=?, confidence=?, method=CASE WHEN ?='approved' THEN 'approved' "
                 "ELSE method END, updated_at=? WHERE id=?", (status, conf, status, now_iso(), match_id))
    if status == "approved" and m["kn"] != m["pn"]:
        conn.execute("INSERT OR IGNORE INTO aliases (sport, kalshi_norm, poly_norm) VALUES (?,?,?)",
                     (m["sport"], m["kn"], m["pn"]))
    conn.commit()
