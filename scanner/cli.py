"""Command line entry points.

    python -m scanner scan [--sports nba,nfl]   one scan pass (cron this in pregame windows)
    python -m scanner closes                    snapshot closes for tracked props near start
    python -m scanner auto [--force SPORTS]     scan whatever is in its US-Eastern window, then closes
                                                and Telegram /took commands (used by GitHub Actions)
    python -m scanner setup-telegram            guided bot setup (writes .env)
    python -m scanner test-alert                send a Telegram test message
    python -m scanner ui                        localhost UI
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from .alerts import Telegram
from .config import LOCAL_HOSTS, Settings
from .db import connect

ET = ZoneInfo("America/New_York")


def active_sports(now_et: datetime) -> list[str]:
    """Pregame windows in US Eastern. NBA nightly 4pm-midnight; NFL Sun 10am-9pm, Mon/Thu 6pm-9pm."""
    h, wd = now_et.hour, now_et.weekday()  # Mon=0 .. Sun=6
    out = []
    if 16 <= h <= 23:
        out.append("nba")
    if (wd == 6 and 10 <= h < 21) or (wd in (0, 3) and 18 <= h < 21):
        out.append("nfl")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="scanner")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sc = sub.add_parser("scan")
    sc.add_argument("--sports", default="nba,nfl")
    sub.add_parser("closes")
    au = sub.add_parser("auto")
    au.add_argument("--force", default="", help="comma list of sports to scan regardless of the time window")
    sub.add_parser("test-alert")
    sub.add_parser("setup-telegram")
    sub.add_parser("setup-channel")
    sub.add_parser("channel-intro")
    sub.add_parser("preview-alert")
    sub.add_parser("ui")
    args = ap.parse_args(argv)

    if args.cmd == "setup-telegram":
        from .telegram_setup import run
        return run()
    if args.cmd == "setup-channel":
        from .telegram_setup import run_channel
        return run_channel()
    if args.cmd in ("channel-intro", "preview-alert"):
        import httpx
        from .alerts import alert_buttons, format_alert
        from .telegram_setup import post_intro
        s = Settings()
        if args.cmd == "channel-intro":
            if not s.telegram_channel_id:
                print("No TELEGRAM_CHANNEL_ID in .env; run setup-channel first.")
                return 1
            return 0 if post_intro(httpx.Client(timeout=15), s.telegram_token, s.telegram_channel_id) else 1
        # preview-alert: send a sample of both versions to YOUR chat only
        sample = {"player": "AJ Dybantsa", "sport": "nba", "stat": "points", "line": 19.5, "side": "yes",
                  "buy_venue": "kalshi", "ask": 0.45, "ask_size": 400, "fee_per": 0.0174, "ref_price": 0.52,
                  "net_edge": 0.0526, "arb_net": 0.021, "warnings": [], "game": "Wizards vs. Pacers",
                  "start_time": "2026-11-12T00:00:00+00:00", "opp_id": 42,
                  "kalshi_url": "https://kalshi.com/markets/kxnbapts/nba-player-points/kxnbapts-26nov11wasind",
                  "poly_url": "https://polymarket.com/event/nba-was-ind-2026-11-11"}
        tg = Telegram(s)
        ok = tg.send("👀 <b>SAMPLE: what you get (DM)</b>\n\n" + format_alert(sample, "owner"), alert_buttons(sample))
        thin = dict(sample, side="no", ask=0.55, ask_size=12, warnings=["THIN BOOK: only 12 at ask"])
        ok &= tg.send("👀 <b>SAMPLE: what members get (channel)</b>\n\n" + format_alert(sample, "channel"),
                      alert_buttons(sample))
        ok &= tg.send("👀 <b>SAMPLE: thin-market UNDER (DM only; members skip thin markets)</b>\n\n"
                      + format_alert(thin, "owner"), alert_buttons(thin))
        print("sent 3 samples to your chat" if ok else "not sent (check .env)")
        return 0 if ok else 1

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    s = Settings()

    if args.cmd == "ui":
        if s.ui_host not in LOCAL_HOSTS:
            print(f"Refusing to bind UI to {s.ui_host!r}; localhost only.", file=sys.stderr)
            return 2
        import uvicorn
        uvicorn.run("scanner.web:app", host=s.ui_host, port=s.ui_port, log_level="info")
        return 0

    if args.cmd == "test-alert":
        tg = Telegram(s)
        ok = tg.send("✅ Props scanner test alert. Read-only; no orders are ever placed.")
        print("sent" if ok else "NOT sent (check TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID in .env)")
        return 0 if ok else 1

    from .venues import Kalshi, Polymarket
    conn = connect(s.db_file)
    kalshi, poly = Kalshi(), Polymarket()
    tg = Telegram(s)
    try:
        if args.cmd == "scan":
            from .pipeline import run_scan
            sports = [x.strip() for x in args.sports.split(",") if x.strip()]
            summary = run_scan(s, conn, kalshi, poly, tg, sports)
        elif args.cmd == "closes":
            from .clv import snapshot_closes
            summary = snapshot_closes(conn, kalshi, poly, s.close_lead_min)
        else:  # auto
            from .alerts import process_commands
            from .clv import snapshot_closes
            from .pipeline import run_scan
            now_et = datetime.now(ET)
            sports = [x.strip() for x in args.force.split(",") if x.strip()] or active_sports(now_et)
            summary = {"now_et": now_et.isoformat(timespec="minutes"), "sports": sports,
                       "commands": process_commands(conn, s, tg)}
            if sports:
                summary["scan"] = run_scan(s, conn, kalshi, poly, tg, sports)
            summary["closes"] = snapshot_closes(conn, kalshi, poly, s.close_lead_min)
            summary["commands"] += process_commands(conn, s, tg)
            from .store import prune
            summary["pruned"] = prune(conn)
        print(json.dumps(summary, indent=2, default=str))
        return 0
    finally:
        # Fold the WAL back into props.db so a plain file copy (GitHub Actions) has everything.
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
