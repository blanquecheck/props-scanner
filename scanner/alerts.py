"""Telegram alerts with dedupe. Uses only the Bot API sendMessage method (free)."""
from __future__ import annotations

import html
import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone

import httpx

from . import odds
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


SPORT_ICON = {"nba": "🏀", "nfl": "🏈"}
STAT_TITLE = {"threes": "3-pointers made"}  # how Polymarket words it; Kalshi says "threes"


def c(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.1f}¢"


def cents(x: float) -> str:
    """45¢, or 45.5¢ when it isn't a whole cent."""
    v = round(x * 100, 1)
    return f"{v:g}¢"


def et_time(iso: str | None) -> str:
    if not iso:
        return ""
    from zoneinfo import ZoneInfo
    t = datetime.fromisoformat(iso).astimezone(ZoneInfo("America/New_York"))
    return t.strftime("%-I:%M %p ET")


def _friendly_warning(w: str) -> str | None:
    m = re.match(r"THIN BOOK: only ([\d.]+) at ask", w)
    if m:
        return f"Thin market: only {float(m.group(1)):g} contracts at this price"
    m = re.match(r"wide (ref )?spread (\d+)c on (\w+)", w)
    if m:
        return f"{m.group(3).title()} prices are {m.group(2)}¢ apart (bid to ask), so fills may be worse"
    if w == "depth unknown":
        return "Size at this price is unknown"
    return w


def what_to_buy(a: dict) -> str:
    """Exact instruction in each venue's own wording, e.g. Kalshi YES on '20+ points'."""
    n = int(a["line"] + 0.5)
    stat = STAT_LABEL.get(a["stat"], a["stat"])
    price = f"<b>{cents(a['ask'])}</b> ({odds.fmt_p(a['ask'])})" if 0 < a["ask"] < 1 else f"<b>{cents(a['ask'])}</b>"
    if a["buy_venue"] == "kalshi":
        return f"Kalshi: buy <b>{'YES' if a['side'] == 'yes' else 'NO'}</b> on “{n}+ {stat}” at {price}"
    poly_stat = STAT_TITLE.get(a["stat"], stat).capitalize()
    return (f"Polymarket: buy <b>{'Yes' if a['side'] == 'yes' else 'No'}</b> on “{poly_stat} O/U {a['line']:g}” "
            f"at {price}")


def sportsbook_lines(a: dict) -> list[str]:
    """For FanDuel / Fanatics / other books: the same bet in sportsbook terms and the fair odds.
    Books won't offer the Kalshi/Polymarket price, but if a book pays more than fair, it's a good price."""
    fair = a["ref_price"]
    if not 0.05 <= fair <= 0.95:
        return []  # extreme longshots/favorites: books rarely post these, and the odds get silly
    stat = STAT_LABEL.get(a["stat"], a["stat"])
    side = "Over" if a["side"] == "yes" else "Under"
    f = odds.american(fair)
    return [
        "📱 <b>FanDuel / Fanatics / other books</b>",
        f"Bet: <b>{side} {a['line']:g} {stat}</b> (same line only)",
        f"Fair odds ≈ <b>{odds.fmt(f)}</b>. Good price if your book pays {odds.fmt(odds.next_better(f))} or better.",
    ]


def format_alert(a: dict, audience: str = "owner") -> str:
    """Readable alert card. audience='owner' adds the /took line; 'channel' adds a short disclaimer.

    a: player, sport, stat, line, side, buy_venue, ask, ask_size, fee_per, ref_price, net_edge,
       arb_net, warnings, kalshi_url, poly_url, start_time, game (e.g. 'Wizards vs. Pacers'), opp_id.
    """
    stat = STAT_LABEL.get(a["stat"], a["stat"])
    direction = "OVER" if a["side"] == "yes" else "UNDER"
    ref_venue = "Polymarket" if a["buy_venue"] == "kalshi" else "Kalshi"
    pct = a["net_edge"] / (a["ask"] + a["fee_per"]) if a["ask"] else 0
    header = " · ".join(x for x in (f"{SPORT_ICON.get(a['sport'], '')} <b>{a['sport'].upper()}</b>",
                                    html.escape(a.get("game") or ""), et_time(a.get("start_time"))) if x.strip())
    lines = [
        header,
        "",
        f"<b>{html.escape(a['player'])} · {direction} {a['line']:g} {stat}</b>",
        f"👉 {what_to_buy(a)}",
        "",
        f"✅ Edge: <b>+{c(a['net_edge'])}</b> per contract after fees (≈{pct * 100:.0f}% on cost)",
        f"📊 {ref_venue} prices this side at {cents(a['ref_price'])}",
    ]
    if a.get("ask_size") is not None:
        lines.append(f"💧 {a['ask_size']:g} contracts available at {cents(a['ask'])}")
    warns = [fw for w in (a.get("warnings") or []) if (fw := _friendly_warning(w))]
    if warns:
        lines.append("")
        lines += [f"⚠️ {html.escape(w)}" for w in warns]
    book = sportsbook_lines(a)
    if book:
        lines.append("")
        lines += book
    lines.append("")
    lines.append("ℹ️ Check he's playing. If he sits, Polymarket pays No but Kalshi settles at a fair price.")
    if audience == "owner":
        if a.get("arb_net") is not None and a["arb_net"] > 0:
            lines.append(f"🔁 Buying the other side on {ref_venue} too locks {c(a['arb_net'])}/contract if he plays.")
        if a.get("opp_id"):
            lines.append(f"📝 Took it? Reply <code>/took {a['opp_id']} {round(a['ask'] * 100)} &lt;qty&gt;</code>")
    else:
        lines.append("<i>Info only, not betting advice. Prices move fast, so check before you buy.</i>")
    return "\n".join(lines)


def alert_buttons(a: dict) -> list[list[dict]]:
    first = ("kalshi_url", "Open on Kalshi") if a["buy_venue"] == "kalshi" else ("poly_url", "Open on Polymarket")
    second = ("poly_url", "Compare on Polymarket") if a["buy_venue"] == "kalshi" else ("kalshi_url", "Compare on Kalshi")
    return [[{"text": first[1], "url": a[first[0]]}, {"text": second[1], "url": a[second[0]]}]]


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

    @property
    def channel_configured(self) -> bool:
        return bool(self.s.telegram_token and self.s.telegram_channel_id)

    def send(self, text: str, buttons: list[list[dict]] | None = None, chat_id: str | None = None) -> bool:
        if not self.configured:
            log.warning("Telegram not configured; alert printed instead:\n%s", text)
            print(text, "\n")
            return False
        url = f"https://api.telegram.org/bot{self.s.telegram_token}/sendMessage"
        data = {"chat_id": chat_id or self.s.telegram_chat_id, "text": text,
                "parse_mode": "HTML", "disable_web_page_preview": "true"}
        if buttons:
            import json
            data["reply_markup"] = json.dumps({"inline_keyboard": buttons})
        try:
            r = self.client.post(url, data=data)
            if r.status_code != 200:
                log.error("Telegram send failed: %s %s", r.status_code, r.text[:200])
                return False
            return True
        except httpx.HTTPError as e:
            log.error("Telegram send failed: %s", e)
            return False

    def send_channel(self, text: str, buttons: list[list[dict]] | None = None) -> bool:
        if not self.channel_configured:
            return False
        return self.send(text, buttons, chat_id=self.s.telegram_channel_id)

    def pin(self, chat_id: str, message_id: int) -> bool:
        r = self.client.post(f"https://api.telegram.org/bot{self.s.telegram_token}/pinChatMessage",
                             data={"chat_id": chat_id, "message_id": message_id, "disable_notification": "true"})
        return r.status_code == 200
