"""Telegram alerts with dedupe. Uses only the Bot API sendMessage method (free)."""
from __future__ import annotations

import html
import logging
import re
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
    if a.get("opp_id"):
        lines.append(f"Took it? Reply <code>/took {a['opp_id']} &lt;price&gt; &lt;qty&gt;</code>, "
                     f"e.g. /took {a['opp_id']} {round(a['ask'] * 100)} 10")
    return "\n".join(lines)


TOOK_RE = re.compile(r"^/took(?:@\w+)?\s+(\d+)\s+([0-9.]+)\s+([0-9.]+)\s*(.*)$", re.I)
HELP = ("Commands:\n/took &lt;id&gt; &lt;price&gt; &lt;qty&gt; [note]: log a trade you placed by hand on flag #id. "
        "Price as 45 or 0.45.\n/help: this message")


def parse_took(text: str) -> tuple[int, float, float, str] | None:
    m = TOOK_RE.match(text.strip())
    if not m:
        return None
    price = float(m.group(2))
    if price >= 1:
        price = price / 100  # typed in cents
    return int(m.group(1)), price, float(m.group(3)), m.group(4).strip()


def process_commands(conn: sqlite3.Connection, s: Settings, tg: "Telegram") -> int:
    """Read new Telegram messages and handle /took. Only messages from TELEGRAM_CHAT_ID count."""
    from .store import log_trade
    if not tg.configured:
        return 0
    row = conn.execute("SELECT value FROM kv WHERE key='tg_offset'").fetchone()
    offset = int(row["value"]) if row else None
    updates = tg.get_updates(offset)
    handled = 0
    for u in updates:
        offset = u["update_id"] + 1
        msg = u.get("message") or {}
        if str((msg.get("chat") or {}).get("id")) != str(s.telegram_chat_id):
            continue  # ignore anyone else who finds the bot
        text = (msg.get("text") or "").strip()
        if text.lower().startswith("/took"):
            parsed = parse_took(text)
            o = log_trade(conn, *parsed) if parsed else None
            if o:
                opp_id, price, qty, _ = parsed
                side = "OVER" if o["side"] == "yes" else "UNDER"
                tg.send(f"Logged #{opp_id}: {html.escape(o['player_raw'])} {int(o['line'] + 0.5)}+ "
                        f"{STAT_LABEL.get(o['stat'], o['stat'])} {side} on {o['buy_venue']}, "
                        f"{qty:g} @ {price * 100:.0f}¢. CLV shows after the close.")
            else:
                tg.send("Couldn't log that. Use /took &lt;id&gt; &lt;price&gt; &lt;qty&gt; with the id from the alert.")
            handled += 1
        elif text.lower().startswith(("/help", "/start")):
            tg.send(HELP)
            handled += 1
    if offset is not None:
        conn.execute("INSERT INTO kv(key, value) VALUES('tg_offset', ?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(offset),))
        conn.commit()
    return handled


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

    def get_updates(self, offset: int | None) -> list[dict]:
        if not self.configured:
            return []
        params = {"timeout": 0, "allowed_updates": '["message"]'}
        if offset is not None:
            params["offset"] = offset
        try:
            r = self.client.get(f"https://api.telegram.org/bot{self.s.telegram_token}/getUpdates", params=params)
            return r.json().get("result", []) if r.status_code == 200 else []
        except (httpx.HTTPError, ValueError) as e:
            log.error("Telegram getUpdates failed: %s", e)
            return []

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
