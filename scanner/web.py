"""Tiny private UI. Run with `python -m scanner ui` (binds 127.0.0.1 only).

Pages: opportunities (+ mark a trade you placed yourself), match review list, CLV scoreboard.
Nothing here can place an order. "Mark taken" only writes to the local log.
"""
from __future__ import annotations

import html
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from .alerts import STAT_LABEL
from .clv import flag_rows, scoreboard, trade_rows
from .config import LOCAL_HOSTS, Settings
from .db import connect
from .store import log_trade, set_match_status

app = FastAPI(title="Props Scanner", docs_url=None, redoc_url=None, openapi_url=None)
S = Settings()


def db():
    return connect(S.db_file)


@app.middleware("http")
async def local_only(request: Request, call_next):
    host = request.client.host if request.client else ""
    if host not in LOCAL_HOSTS:
        return PlainTextResponse("localhost only", status_code=403)
    return await call_next(request)


CSS = """
:root{--bg:#fbfbfa;--fg:#1d1d1b;--muted:#6b6b66;--line:#e4e3df;--warn:#9a5b00;--good:#1f7a3a;--bad:#a22;}
@media (prefers-color-scheme: dark){:root{--bg:#161615;--fg:#ecebe7;--muted:#9c9b95;--line:#2c2b29;--warn:#e0a84a;--good:#5cc47a;--bad:#f07070;}}
body{background:var(--bg);color:var(--fg);font:14px/1.45 -apple-system,system-ui,sans-serif;margin:0;padding:16px;max-width:1200px}
nav a{margin-right:14px;color:var(--fg);font-weight:600}
h1{font-size:18px;margin:4px 0 12px} h2{font-size:15px;margin:22px 0 8px}
table{border-collapse:collapse;width:100%;display:block;overflow-x:auto}
th,td{border-bottom:1px solid var(--line);padding:5px 8px;text-align:left;white-space:nowrap;vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:12px}
.risk{border:1px solid var(--warn);color:var(--warn);padding:8px 10px;border-radius:6px;margin:10px 0 14px}
.warn{color:var(--warn)} .good{color:var(--good)} .bad{color:var(--bad)} .muted{color:var(--muted)}
input{width:64px;background:transparent;color:var(--fg);border:1px solid var(--line);padding:2px 4px}
button{cursor:pointer}
"""

RISK = ("<div class='risk'><b>Risk note.</b> Read-only scanner: it never places orders. Kalshi prop books are "
        "often thin, so check size at the ask before trusting an edge. Taker fees (Kalshi ~7%·p·(1−p), "
        "Polymarket sports 5%·p·(1−p)) are already subtracted, but they still drag small edges to nothing. "
        "<b>DNP rules differ:</b> Polymarket settles NO if the player sits; Kalshi settles at a fair price. "
        "Big gaps usually mean lineup news, not free money.</div>")


def page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>
<style>{CSS}</style></head><body>
<nav><a href="/">Opportunities</a><a href="/review">Review matches</a><a href="/clv">CLV scoreboard</a></nav>
<h1>{html.escape(title)}</h1>{RISK}{body}</body></html>""")


def cents(x) -> str:
    return "—" if x is None else f"{x * 100:.1f}¢"


def e(x) -> str:
    return html.escape("" if x is None else str(x))


def prop_label(player, line, stat) -> str:
    return f"{e(player)} {int(line + 0.5)}+ {e(STAT_LABEL.get(stat, stat))}"


@app.get("/", response_class=HTMLResponse)
def opportunities(hours: int = 24):
    conn = db()
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(microsecond=0).isoformat()
    rows = conn.execute("""
        SELECT o.*, k.player_raw, k.stat, k.line, k.sport, k.url kurl, p.url purl, p.start_time
        FROM opportunities o
        JOIN (SELECT MAX(id) id FROM opportunities WHERE ts>=? GROUP BY match_id, buy_venue, side) l ON l.id=o.id
        JOIN matches m ON m.id=o.match_id JOIN prop_contracts k ON k.id=m.kalshi_id
        JOIN prop_contracts p ON p.id=m.poly_id ORDER BY o.suppressed IS NOT NULL, o.net_edge DESC""",
                        (since,)).fetchall()
    trs = []
    for r in rows:
        side = "OVER" if r["side"] == "yes" else "UNDER"
        status = (f"<span class='warn'>withheld: {e(r['suppressed'])}</span>" if r["suppressed"]
                  else ("alerted" if r["alerted"] else "flagged"))
        trs.append(f"""<tr>
<td>{e(r['ts'][5:16])}</td><td>{e(r['sport']).upper()}</td><td>{prop_label(r['player_raw'], r['line'], r['stat'])}</td>
<td><b>{side}</b> on {e(r['buy_venue'])}</td><td>{cents(r['ask'])}</td><td>{e(r['ask_size'])}</td>
<td>{cents(r['fee_per'])}</td><td>{cents(r['ref_price'])}</td><td class='good'><b>{cents(r['net_edge'])}</b></td>
<td>{e(r['paper_contracts'])} / ${e(r['paper_cost'])}</td>
<td class='warn'>{e(r['warnings'])}</td><td>{status}</td>
<td><a href="{e(r['kurl'])}">Kalshi</a> · <a href="{e(r['purl'])}">Poly</a></td>
<td><form method="post" action="/mark-taken">
<input type="hidden" name="opportunity_id" value="{r['id']}">
<input name="price" placeholder="price" value="{r['ask']:.2f}"><input name="contracts" placeholder="qty">
<button>Mark taken</button></form></td></tr>""")
    body = (f"<p class='muted'>Latest flag per market/side in the last {hours}h. "
            f"Threshold {cents(S.min_net_edge)} net. 'Paper' = would have bet ${S.paper_stake_usd:g} (capped by depth).</p>"
            "<table><tr><th>time (UTC)</th><th>sport</th><th>prop</th><th>buy</th><th>ask</th><th>size</th>"
            "<th>fee/ct</th><th>ref mid</th><th>net edge</th><th>paper qty / cost</th><th>warnings</th>"
            "<th>status</th><th>links</th><th>log a trade you placed</th></tr>"
            + ("".join(trs) or "<tr><td colspan=14 class='muted'>No flags yet.</td></tr>") + "</table>")
    return page("Opportunities", body)


@app.post("/mark-taken")
def mark_taken(opportunity_id: int = Form(...), price: float = Form(...), contracts: float = Form(...),
               note: str = Form("")):
    """Log a trade you placed by hand. Local bookkeeping only."""
    if not log_trade(db(), opportunity_id, price, contracts, note):
        return PlainTextResponse("bad input", status_code=400)
    return RedirectResponse("/clv", status_code=303)


@app.get("/review", response_class=HTMLResponse)
def review():
    conn = db()
    rows = conn.execute("""
        SELECT m.*, k.player_raw kp, p.player_raw pp, k.stat, k.line, k.game_key, k.title kt, p.title pt
        FROM matches m JOIN prop_contracts k ON k.id=m.kalshi_id JOIN prop_contracts p ON p.id=m.poly_id
        WHERE m.status='review' ORDER BY m.confidence DESC LIMIT 500""").fetchall()
    trs = "".join(f"""<tr><td>{e(r['game_key'])}</td><td>{e(r['kt'])}</td><td>{e(r['pt'])}</td>
<td>{r['confidence']:.2f}</td><td>{e(r['method'])}</td>
<td><form method="post" action="/review/{r['id']}" style="display:inline">
<button name="decision" value="approved">Same player</button>
<button name="decision" value="rejected">Different</button></form></td></tr>""" for r in rows)
    body = ("<p class='muted'>Low-confidence name matches never alert. Approving one also saves the name pair as an "
            "alias so future games match automatically.</p><table><tr><th>game</th><th>Kalshi</th><th>Polymarket</th>"
            "<th>conf</th><th>method</th><th></th></tr>"
            + (trs or "<tr><td colspan=6 class='muted'>Nothing to review.</td></tr>") + "</table>")
    return page("Review matches", body)


@app.post("/review/{match_id}")
def review_decide(match_id: int, decision: str = Form(...)):
    if decision not in ("approved", "rejected"):
        return PlainTextResponse("bad decision", status_code=400)
    set_match_status(db(), match_id, decision)
    return RedirectResponse("/review", status_code=303)


def _board(rows, venue_key) -> str:
    b = scoreboard(rows, venue_key)
    if not b:
        return "<p class='muted'>No closes yet.</p>"
    trs = "".join(f"<tr><td>{e(x['dim'])}</td><td>{e(x['value'])}</td><td>{x['n']}</td>"
                  f"<td class='{'good' if x['avg_clv_net'] > 0 else 'bad'}'>{cents(x['avg_clv'])}</td>"
                  f"<td class='{'good' if x['avg_clv_net'] > 0 else 'bad'}'><b>{cents(x['avg_clv_net'])}</b></td>"
                  f"<td>{x['pct_positive'] * 100:.0f}%</td></tr>" for x in b)
    return ("<table><tr><th>by</th><th>value</th><th>n</th><th>avg CLV</th><th>avg CLV net of fee</th>"
            f"<th>% beat close</th></tr>{trs}</table>")


@app.get("/clv", response_class=HTMLResponse)
def clv_page():
    conn = db()
    trades = trade_rows(conn)
    flags = [f for f in flag_rows(conn) if not f["suppressed"]]
    n_closed = sum(t["clv"] is not None for t in trades)
    tr = "".join(f"<tr><td>{e(t['ts'][5:16])}</td><td>{prop_label(t['player_raw'], t['line'], t['stat'])}</td>"
                 f"<td>{'OVER' if t['side'] == 'yes' else 'UNDER'} on {e(t['venue'])}</td><td>{cents(t['price'])}</td>"
                 f"<td>{t['contracts']:g}</td><td>${t['fee']:.2f}</td><td>{cents(t['close_mid'])}</td>"
                 f"<td>{cents(t['clv'])}</td><td><b>{cents(t['clv_net'])}</b></td></tr>" for t in trades)
    paper = sum(f["paper_pnl_at_close"] or 0 for f in flags if f["paper_pnl_at_close"] is not None)
    body = (f"<p>Go/no-go needs <b>≥30 taken samples</b> with positive CLV after fees. Taken with a close: "
            f"<b>{n_closed}</b> / 30.</p>"
            "<h2>Trades you marked taken</h2>" + _board(trades, "venue") +
            "<h2>Trade log</h2><table><tr><th>time</th><th>prop</th><th>side</th><th>price</th><th>qty</th>"
            "<th>fee</th><th>close mid</th><th>CLV</th><th>CLV net</th></tr>"
            + (tr or "<tr><td colspan=9 class='muted'>None yet.</td></tr>") + "</table>"
            f"<h2>All flags (paper, first flag per market/side)</h2><p class='muted'>Paper P&amp;L at close, "
            f"${S.paper_stake_usd:g} per flag capped by depth: <b>${paper:,.2f}</b> across "
            f"{sum(f['clv'] is not None for f in flags)} closed flags. Withheld (DNP-suspect) flags excluded.</p>"
            + _board(flags, "buy_venue"))
    return page("CLV scoreboard", body)
