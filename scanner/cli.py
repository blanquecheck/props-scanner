"""Command line entry points.

    python -m scanner scan [--sports nba,nfl]   one scan pass (cron this in pregame windows)
    python -m scanner closes                    snapshot closes for tracked props near start
    python -m scanner setup-telegram            guided bot setup (writes .env)
    python -m scanner test-alert                send a Telegram test message
    python -m scanner ui                        localhost UI
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from .alerts import Telegram
from .config import LOCAL_HOSTS, Settings
from .db import connect


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="scanner")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sc = sub.add_parser("scan")
    sc.add_argument("--sports", default="nba,nfl")
    sub.add_parser("closes")
    sub.add_parser("test-alert")
    sub.add_parser("setup-telegram")
    sub.add_parser("ui")
    args = ap.parse_args(argv)

    if args.cmd == "setup-telegram":
        from .telegram_setup import run
        return run()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
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
    if args.cmd == "scan":
        from .pipeline import run_scan
        sports = [x.strip() for x in args.sports.split(",") if x.strip()]
        summary = run_scan(s, conn, kalshi, poly, Telegram(s), sports)
    else:
        from .clv import snapshot_closes
        summary = snapshot_closes(conn, kalshi, poly, s.close_lead_min)
    print(json.dumps(summary, indent=2, default=str))
    return 0
