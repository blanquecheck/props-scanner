"""One scan pass: read both venues, match, price edges, alert."""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

from .alerts import Telegram, alert_buttons, format_alert, record_alert, should_alert
from .config import ROOT, Settings
from .db import now_iso
from .edge import Book, FeeInfo, compute_edges
from .matching import load_alias_csv, match_contracts
from .store import db_aliases, insert_quote, rejected_pairs, upsert_contract, upsert_match
from .venues import Contract, Kalshi, Polymarket

log = logging.getLogger(__name__)


def evaluate_pair(k: Contract, p: Contract, s: Settings) -> list:
    return compute_edges(
        Book(k.yes_bid, k.yes_ask, k.yes_bid_size, k.yes_ask_size),
        Book(p.yes_bid, p.yes_ask, p.yes_bid_size, p.yes_ask_size),
        FeeInfo(kalshi_mult=k.fee_mult or 1.0, poly_rate=p.fee_rate or 0.0, poly_exp=p.fee_exponent or 1.0),
        contracts=s.alert_contracts, max_ref_spread=s.max_ref_spread,
        thin_depth=s.thin_depth_contracts, wide_spread=s.wide_spread_warn,
    )


def player_gaps(results, max_spread: float = 0.10) -> dict[tuple[str, str], tuple[float, int]]:
    """Median (Kalshi mid - Polymarket mid) per (game, player) across all auto-matched lines
    where both books are reasonably tight. A large positive gap means Polymarket is pricing in
    a DNP (it settles NO if the player sits), so those contracts are not equivalent right now."""
    from statistics import median
    gaps: dict[tuple[str, str], list[float]] = {}
    for r in results:
        if r.status != "auto":
            continue
        k, p = r.kalshi, r.poly
        if None in (k.yes_bid, k.yes_ask, p.yes_bid, p.yes_ask):
            continue
        if k.yes_ask - k.yes_bid > max_spread or p.yes_ask - p.yes_bid > max_spread:
            continue
        g = (k.yes_bid + k.yes_ask) / 2 - (p.yes_bid + p.yes_ask) / 2
        gaps.setdefault((k.game_key, k.player_norm), []).append(g)
    return {key: (median(v), len(v)) for key, v in gaps.items()}


def suppression_reason(edge_net: float, gap: tuple[float, int] | None, s: Settings) -> str | None:
    """DNP signature is one-directional: Kalshi YES above Polymarket YES, because Polymarket
    settles NO when the player sits. Needs >=2 lines agreeing, or one line with 2x the gap."""
    if gap:
        g, n = gap
        if (n >= 2 and g >= s.dnp_gap) or (n == 1 and g >= 2 * s.dnp_gap):
            return (f"Kalshi above Polymarket on this player across {n} line(s) (median {g * 100:.0f}c): "
                    f"Polymarket is likely pricing in a DNP")
    if edge_net > s.max_sane_edge:
        return f"edge {edge_net * 100:.0f}c is too large to trust: check injury/lineup news"
    return None


def run_scan(s: Settings, conn: sqlite3.Connection, kalshi: Kalshi, poly: Polymarket, tg: Telegram,
             sports: list[str], injuries=None) -> dict:
    """injuries: an InjuryReport (or None to skip the injury check, e.g. in tests)."""
    from .injuries import check as injury_check
    summary: dict = {"started": now_iso(), "sports": sports}
    pcs, pstats = poly.contracts(sports, s.hours_ahead)
    summary["polymarket"] = pstats
    poly_games = {p.game_key for p in pcs}
    kcs, kstats = kalshi.contracts(set(sports))
    summary["kalshi"] = kstats
    kcs = [k for k in kcs if k.game_key in poly_games]  # nothing to compare otherwise
    summary["kalshi_in_shared_games"] = len(kcs)

    ids: dict[tuple[str, str], int] = {}
    for c in pcs + kcs:
        cid = upsert_contract(conn, c)
        ids[(c.venue, c.market_id)] = cid
        insert_quote(conn, cid, c.yes_bid, c.yes_ask, c.yes_bid_size, c.yes_ask_size, "list")
    conn.commit()

    aliases = load_alias_csv(ROOT / "data" / "player_aliases.csv") | db_aliases(conn)
    results = match_contracts(kcs, pcs, aliases, rejected_pairs(conn))
    summary["matches_auto"] = sum(r.status == "auto" for r in results)
    summary["matches_review"] = sum(r.status == "review" for r in results)

    gaps = player_gaps(results)
    now = datetime.now(timezone.utc)
    flagged = alerted = suppressed = 0
    for r in results:
        k, p = r.kalshi, r.poly
        kid, pid = ids[("kalshi", k.market_id)], ids[("polymarket", p.market_id)]
        row = upsert_match(conn, kid, pid, r.confidence, r.method, r.status)
        if p.start_time:  # Kalshi has no tip time; borrow Polymarket's
            conn.execute("UPDATE prop_contracts SET start_time=? WHERE id=?", (p.start_time, kid))
        if row["status"] not in ("auto", "approved") or row["confidence"] < s.min_alert_confidence:
            continue
        if p.start_time and datetime.fromisoformat(p.start_time) <= now:
            continue
        edges = evaluate_pair(k, p, s)
        if not edges or edges[0].net_edge < s.min_net_edge - 0.01:
            continue
        # Candidate: confirm Polymarket with the live CLOB book (sizes + fresh prices).
        try:
            b = poly.book(p.token_yes)
            p.yes_bid, p.yes_ask, p.yes_bid_size, p.yes_ask_size = (
                b["yes_bid"], b["yes_ask"], b["yes_bid_size"], b["yes_ask_size"])
            insert_quote(conn, pid, p.yes_bid, p.yes_ask, p.yes_bid_size, p.yes_ask_size, "book")
        except Exception as e:
            log.warning("poly book fetch failed for %s: %s", p.market_id, e)
            continue
        for e in evaluate_pair(k, p, s):
            if e.net_edge < s.min_net_edge:
                continue
            flagged += 1
            paper_n = s.paper_stake_usd / e.ask
            if e.ask_size is not None:
                paper_n = min(paper_n, e.ask_size)
            why = suppression_reason(e.net_edge, gaps.get((k.game_key, k.player_norm)), s)
            ic = injury_check(injuries, k.sport, k.player_norm, k.game_key)
            why = why or ic.withhold
            if ic.warning:
                e.warnings.append(ic.warning)
            cur = conn.execute(
                """INSERT INTO opportunities (match_id, ts, buy_venue, side, ask, ask_size, fee_per, ref_price,
                       ref_spread, buy_spread, net_edge, arb_net, warnings, suppressed, paper_contracts, paper_cost)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (row["id"], now_iso(), e.buy_venue, e.side, e.ask, e.ask_size, e.fee_per, e.ref_price,
                 e.ref_spread, e.buy_spread, e.net_edge, e.arb_net, "; ".join(e.warnings), why,
                 round(paper_n, 2), round(paper_n * (e.ask + e.fee_per), 2)))
            if why:
                suppressed += 1
                continue
            key = f"{row['id']}:{e.buy_venue}:{e.side}"
            if should_alert(conn, key, e.net_edge, s):
                a = {
                    "player": k.player_raw, "sport": k.sport, "stat": k.stat, "line": k.line, "side": e.side,
                    "buy_venue": e.buy_venue, "ask": e.ask, "ask_size": e.ask_size, "fee_per": e.fee_per,
                    "ref_price": e.ref_price, "net_edge": e.net_edge, "arb_net": e.arb_net,
                    "warnings": e.warnings, "kalshi_url": k.url, "poly_url": p.url,
                    "start_time": p.start_time, "game": p.event_title, "opp_id": cur.lastrowid,
                    "injury": ic.card_line,
                }
                buttons = alert_buttons(a)
                tg.send(format_alert(a, "owner"), buttons)
                # Members only see alerts with real size behind them and a clean injury check.
                if ic.channel_ok and e.ask_size is not None and e.ask_size >= s.channel_min_depth:
                    if tg.send_channel(format_alert(a, "channel"), buttons):
                        summary["channel_posts"] = summary.get("channel_posts", 0) + 1
                record_alert(conn, key, e.net_edge)
                conn.execute("UPDATE opportunities SET alerted=1 WHERE id=?", (cur.lastrowid,))
                alerted += 1
    conn.commit()
    summary.update(flagged=flagged, suppressed=suppressed, alerted=alerted, finished=now_iso())
    return summary
