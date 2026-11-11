"""Telegram alerts with dedupe. Uses only the Bot API sendMessage method (free)."""
from __future__ import annotations

import html
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

import httpx

from .config import Settings
from .db import now_iso

log = logging.getLogger(__name__)

STAT_LABEL = {
    "points": "points", "rebounds": "rebounds", "assists": "assists", "threes": "threes",
    "steals": "steals", "blocks": "blocks", "pts_reb_ast": "pts+reb+ast", "pts_reb": "pts+reb",
    "pts_ast": "pts+ast", "reb_ast": "reb+ast", "passing_yards": "pass yds",
    "rushing_yards": "rush yds", "receiving_yards": "rec yds", "receptions": "receptions",
    "passing_tds": "pass TDs", "rushing_attempts": "rush att", "passing_attempts": "pass att",
    "rush_rec_yards": "rush+rec yds",
}


def c(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.1f}¢"


def format_alert(a: dict) -> str:
    """a: dict with player, stat, line, side, buy_venue, ask, ask_size, fee_per, ref_price,
    net_edge, arb_net, kalshi_yes_bid/ask, poly_yes_bid/ask, warnings, kalshi_url, poly_url, start_time."""
    threshold = int(a["line"] + 0.5)
    side = f"OVER ({threshold}+)" if a["side"] == "yes" else f"UNDER ({threshold}+ = NO)"
    lines = [
        f"<b>{html.escape(a['player'])} {threshold}+ {STAT_LABEL.get(a['stat'], a['stat'])}</b> [{a['sport'].upper()}]",
        f"Buy <b>{side}</b> on <b>{a['buy_venue'].title()}</b> @ {c(a['ask'])} (size {a['ask_size'] if a['ask_size'] is not None else '?'})",
        f"Net edge <b>{c(a['net_edge'])}</b> after {c(a['fee_per'])} fee vs ref {c(a['ref_price'])}",
        f"Kalshi YES {c(a['kalshi_yes_bid'])} / {c(a['kalshi_yes_ask'])}  ·  Poly YES {c(a['poly_yes_bid'])} / {c(a['poly_yes_ask'])}",
    ]
    if a.get("arb_net") is not None and a["arb_net"] > 0:
        lines.append(f"Both sides (only locked if he plays): {c(a['arb_net'])}/contract after both fees")
    for w in a.get("warnings") or []:
        lines.append(f"⚠️ {html.escape(w)}")
    lines.append("⚠️ DNP rules differ: Polymarket settles NO if he sits; Kalshi settles at a fair price. Check status.")
    if a.get("start_time"):
        lines.append(f"Start: {a['start_time']}")
    lines.append(f"<a href=\"{html.escape(a['kalshi_url'])}\">Kalshi market</a> · "
                 f"<a href=\"{html.escape(a['poly_url'])}\">Polymarket</a>")
    lines.append("Read-only scanner. Place any trade yourself.")
    return "\n".join(lines)


def should_alert(conn: sqlite3.Connection, key: str, edge: float, s: Settings,
                 now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    row = conn.execute("SELECT last_edge, last_ts FROM alert_state WHERE key=?", (key,)).fetchone()
    if not row:
        return True
    last_ts = datetime.fromisoformat(row["last_ts"])
    if edge >= row["last_edge"] + s.alert_edge_bump - 1e-9:
        return True
    return now - last_ts >= timedelta(minutes=s.alert_cooldown_min)


def record_alert(conn: sqlite3.Connection, key: str, edge: float, ts: str | None = None) -> None:
    conn.execute("INSERT INTO alert_state(key, last_edge, last_ts) VALUES(?,?,?) "
                 "ON CONFLICT(key) DO UPDATE SET last_edge=excluded.last_edge, last_ts=excluded.last_ts",
                 (key, edge, ts or now_iso()))


class Telegram:
    def __init__(self, s: Settings, client: httpx.Client | None = None):
        self.s = s
        self.client = client or httpx.Client(timeout=15)

    @property
    def configured(self) -> bool:
        return bool(self.s.telegram_token and self.s.telegram_chat_id)

    def send(self, text: str) -> bool:
        if not self.configured:
            log.warning("Telegram not configured; alert printed instead:\n%s", text)
            print(text, "\n")
            return False
        url = f"https://api.telegram.org/bot{self.s.telegram_token}/sendMessage"
        try:
            r = self.client.post(url, data={"chat_id": self.s.telegram_chat_id, "text": text,
                                            "parse_mode": "HTML", "disable_web_page_preview": "true"})
            if r.status_code != 200:
                log.error("Telegram send failed: %s %s", r.status_code, r.text[:200])
                return False
            return True
        except httpx.HTTPError as e:
            log.error("Telegram send failed: %s", e)
            return False
